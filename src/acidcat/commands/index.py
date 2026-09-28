"""
acidcat index -- build and manage per-library sample indexes.

A library is a directory you have registered for indexing. Each library
gets its own SQLite file (default: ~/.acidcat/libraries/<label>_<hash>.db,
or `<library>/.acidcat/index.db` with --in-tree). The global registry at
~/.acidcat/registry.db tracks every library so reads can fan out across
your whole collection without scanning disk.

Usage:
    acidcat index DIR [--label NAME] [--in-tree] [--features] [--deep]
                      [--rebuild] [--import-tags FILE] [--registry PATH]
    acidcat index --list   [--registry PATH]
    acidcat index --orphans [--registry PATH]
    acidcat index --stats LABEL_OR_PATH [--registry PATH]
    acidcat index --forget LABEL_OR_PATH [--registry PATH]
    acidcat index --remove LABEL_OR_PATH [--registry PATH]
"""

import json
import os
import sys
import time

from acidcat.core.catalogue import index as idx
from acidcat.core.catalogue import paths as acidpaths
from acidcat.core.catalogue import registry as reg
from acidcat.core.catalogue.indexing import (
    walk_and_upsert, _refuses_as_root, _count_audio_in_subtree,
    _discover_candidates, _resolve_unique_label,
)


def register(subparsers):
    p = subparsers.add_parser(
        "index",
        help="Build/update a per-library sample index.",
    )
    p.add_argument("target", nargs="?", help="Directory to index.")
    p.add_argument("--label",
                   help="Friendly label for the library "
                        "(default: basename of DIR).")
    p.add_argument("--in-tree", dest="in_tree", action="store_true",
                   help="Store the DB inside <DIR>/.acidcat/index.db instead "
                        "of ~/.acidcat/libraries/<label>_<hash>.db.")
    p.add_argument("--rebuild", action="store_true",
                   help="Delete the existing per-library DB before indexing.")
    p.add_argument("--force", action="store_true",
                   help="Re-extract metadata even for files whose mtime and "
                        "size are unchanged. Use after a parser upgrade; "
                        "preserves tags, descriptions, and features.")
    p.add_argument("--features", action="store_true",
                   help="Extract librosa audio features during indexing.")
    p.add_argument("--jobs", "-j", type=int, default=None,
                   help="Worker processes for feature extraction (default: "
                        "CPU count - 1; 1 = serial).")
    p.add_argument("--deep", action="store_true",
                   help="Use librosa for BPM/key when metadata is absent.")
    p.add_argument("--import-tags", dest="import_tags",
                   help="Import a legacy <name>_tags.json into the library.")
    p.add_argument("--registry",
                   help="Override registry DB path "
                        "(default: ~/.acidcat/registry.db).")
    # registry-management subcommands (target-less)
    p.add_argument("--list", dest="list_libs", action="store_true",
                   help="List all registered libraries.")
    p.add_argument("--orphans", action="store_true",
                   help="List registered libraries whose DB file is missing.")
    p.add_argument("--stats", dest="stats_target",
                   help="Print stats for one library (by label or path).")
    p.add_argument("--forget", dest="forget",
                   help="Remove a library from the registry. Does NOT delete "
                        "its DB file.")
    p.add_argument("--remove", dest="remove",
                   help="Forget a library AND delete its DB file.")
    p.add_argument("--refresh-stats", dest="refresh_stats",
                   action="store_true",
                   help="Read every registered library's DB and refresh the "
                        "registry's cached sample_count, feature_count, and "
                        "last_indexed_at. Useful migration step after upgrading "
                        "from an older version that did not auto-populate "
                        "these fields.")
    p.add_argument("--refresh-stats-target", dest="refresh_stats_target",
                   help="With --refresh-stats: scope to one library by label "
                        "or path. Default: refresh every registered library.")
    # discovery (walk a tree, register qualifying directories as libraries)
    p.add_argument("--discover", dest="discover_root",
                   help="Walk this directory and register every qualifying "
                        "subdirectory as its own library.")
    p.add_argument("--min-samples", dest="min_samples", type=int, default=20,
                   help="--discover threshold: minimum audio files in a "
                        "subtree for it to qualify as a library (default 20).")
    p.add_argument("--max-depth", dest="max_depth", type=int, default=3,
                   help="--discover walks this many levels into the tree "
                        "looking for non-qualifying parents whose children "
                        "qualify (default 3).")
    p.add_argument("--label-prefix", dest="label_prefix", default="",
                   help="--discover prefixes every auto-derived label with "
                        "this string. Useful for namespacing scattered "
                        "collections.")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="--discover prints what would be registered without "
                        "writing to the registry.")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Diagnostic lines on stderr.")
    p.set_defaults(func=run)


def _vlog(args, msg):
    if getattr(args, "verbose", False) and not getattr(args, "quiet", False):
        print(msg, file=sys.stderr)


def _warn_legacy_db(args):
    """One-line stderr warning if v0.4 single-DB sits at ~/.acidcat/index.db."""
    legacy = acidpaths.legacy_global_db_path()
    if os.path.isfile(legacy) and not getattr(args, "quiet", False):
        print(f"[INFO] legacy v0.4 DB at {legacy} is ignored. "
              f"Remove with: rm {legacy}*", file=sys.stderr)


def run(args):
    """Entry point. Turns a designed refusal into a clean message.

    SchemaVersionError exists to be shown to a person: it means the DB was
    written by a newer acidcat and this build will not touch it. No call site
    caught it, so `index --stats lib` on such a DB printed a traceback followed
    by "internal error (this is a bug)" -- telling the user their correct,
    intentional refusal was a defect, and burying the one sentence that says
    what to do about it.
    """
    from acidcat.core.catalogue.index import SchemaVersionError
    try:
        return _run(args)
    except SchemaVersionError as e:
        print(f"acidcat lib: {e}", file=sys.stderr)
        return 2


def _run(args):
    quiet = getattr(args, "quiet", False)
    registry_path = getattr(args, "registry", None)

    # collision check: registry-management flags don't take a positional
    # target. Silently ignoring the target hides what the user actually
    # asked for. Surface it instead.
    mgmt_flag_set = {
        "--list":     args.list_libs,
        "--orphans":  args.orphans,
        "--stats":    bool(args.stats_target),
        "--forget":   bool(args.forget),
        "--remove":   bool(args.remove),
        "--discover": bool(getattr(args, "discover_root", None)),
        "--refresh-stats": bool(getattr(args, "refresh_stats", False)),
    }
    active_mgmt = [name for name, v in mgmt_flag_set.items() if v]
    if args.target and active_mgmt:
        print(
            f"acidcat lib: cannot combine {active_mgmt[0]} with a "
            f"target directory. Drop the target or remove the flag.",
            file=sys.stderr,
        )
        return 2
    if len(active_mgmt) > 1:
        print(
            f"acidcat lib: cannot combine {active_mgmt[0]} and "
            f"{active_mgmt[1]}; pick one.",
            file=sys.stderr,
        )
        return 2

    # registry-management modes (no target required)
    if args.list_libs:
        return _cmd_list(registry_path)
    if args.orphans:
        return _cmd_orphans(registry_path)
    if args.stats_target:
        return _cmd_stats(args.stats_target, registry_path)
    if args.forget:
        return _cmd_forget(args.forget, registry_path, quiet=quiet)
    if args.remove:
        return _cmd_remove(args.remove, registry_path, quiet=quiet)
    if getattr(args, "refresh_stats", False):
        return _cmd_refresh_stats(
            getattr(args, "refresh_stats_target", None),
            registry_path, quiet=quiet,
        )
    if getattr(args, "discover_root", None):
        return _cmd_discover(
            args.discover_root,
            registry_path=registry_path,
            min_samples=args.min_samples,
            max_depth=args.max_depth,
            label_prefix=args.label_prefix or "",
            dry_run=args.dry_run,
            do_features=args.features,
            do_deep=args.deep,
            quiet=quiet,
            verbose=getattr(args, "verbose", False),
        )

    # main index mode requires a target dir
    if not args.target:
        print("acidcat lib: missing target directory (or use "
              "--list/--orphans/--stats/--forget/--remove)", file=sys.stderr)
        return 2

    target = args.target
    if not os.path.isdir(target):
        print(f"acidcat lib: {target}: Not a directory", file=sys.stderr)
        return 2

    _warn_legacy_db(args)

    scan_root = acidpaths.normalize(target)
    label = args.label or os.path.basename(scan_root) or "library"
    in_tree = bool(args.in_tree)
    db_path = (
        acidpaths.in_tree_db_path_for(scan_root) if in_tree
        else acidpaths.central_db_path_for(scan_root, label)
    )

    # open registry first to validate non-overlap before we touch any disk state
    rconn = reg.open_registry(registry_path)
    try:
        try:
            # the registry returns the canonical db_path: on re-register
            # it reuses the stored one, which can differ from the path
            # computed above if the filename scheme changed since the
            # library was first registered.
            db_path = reg.register_library(
                rconn, scan_root, label=label, db_path=db_path,
                in_tree=in_tree, schema_version=idx.SCHEMA_VERSION,
            )
        except reg.OverlapError as e:
            print(f"acidcat lib: {e}", file=sys.stderr)
            return 1
    finally:
        rconn.close()

    if args.rebuild and os.path.isfile(db_path):
        try:
            os.remove(db_path)
            for ext in (".db-shm", ".db-wal"):
                sidecar = db_path[:-3] + ext if db_path.endswith(".db") else db_path + ext
                if os.path.isfile(sidecar):
                    os.remove(sidecar)
        except OSError as e:
            print(f"acidcat lib: --rebuild could not remove {db_path}: {e}",
                  file=sys.stderr)
            return 2
        _vlog(args, f"[index] removed existing DB at {db_path}")

    conn = idx.open_db(db_path)
    try:
        if not quiet:
            print(f"[INFO] indexing {scan_root} -> {db_path}", file=sys.stderr)

        counts = walk_and_upsert(
            conn, scan_root,
            do_features=args.features,
            do_deep=args.deep,
            quiet=quiet,
            force=getattr(args, "force", False),
            jobs=getattr(args, "jobs", None),
        )

        if args.import_tags:
            imported = _import_tags(conn, args.import_tags)
            counts["tags_imported"] = imported
            if not quiet:
                print(f"[INFO] imported tags for {imported} sample(s) "
                      f"from {args.import_tags}", file=sys.stderr)

        conn.commit()

        # post-walk: refresh registry stats
        sample_count = conn.execute(
            "SELECT COUNT(*) AS c FROM samples"
        ).fetchone()["c"]
        feature_count = conn.execute(
            "SELECT COUNT(*) AS c FROM features"
        ).fetchone()["c"]
    finally:
        conn.close()

    rconn = reg.open_registry(registry_path)
    try:
        reg.update_stats(
            rconn, scan_root,
            sample_count=sample_count,
            feature_count=feature_count,
            last_indexed_at=time.time(),
            schema_version=idx.SCHEMA_VERSION,
        )
    finally:
        rconn.close()

    if not quiet:
        # "unrecognised" is reported separately from "skipped". They are
        # different facts -- skipped means seen before and unchanged, which is
        # a file that IS in the index, while unrecognised means never opened at
        # all. Folding them together let a walk that passed over half a folder
        # print "0 skipped" and read as complete.
        extra = (f", {counts['unrecognised']} unrecognised extension"
                 if counts.get("unrecognised") else "")
        print(
            f"[INFO] [{label}] {counts['added']} added, {counts['updated']} updated, "
            f"{counts['skipped']} skipped, {counts['pruned']} pruned, "
            f"{counts['failed']} failed{extra}",
            file=sys.stderr,
        )
    return 0


def _cmd_list(registry_path):
    rconn = reg.open_registry(registry_path)
    try:
        rows = reg.list_libraries(rconn)
        if not rows:
            print("(no libraries registered)")
            return 0
        for r in rows:
            existing = "  " if os.path.isfile(r["db_path"]) else "! "
            count = r["sample_count"] if r["sample_count"] is not None else "?"
            mode = "in-tree" if r["in_tree"] else "central"
            print(f"{existing}{r['label']:<24s} {count:>7}  [{mode}]  {r['root_path']}")
        return 0
    finally:
        rconn.close()


def _cmd_orphans(registry_path):
    rconn = reg.open_registry(registry_path)
    try:
        orphans = reg.find_orphans(rconn)
        if not orphans:
            print("(no orphans)")
            return 0
        for r in orphans:
            print(f"{r['label']:<24s}  {r['root_path']}  -> missing {r['db_path']}")
        return 0
    finally:
        rconn.close()


def _cmd_stats(target, registry_path):
    rconn = reg.open_registry(registry_path)
    try:
        row = reg.get_library(rconn, target)
        if row is None:
            print(f"acidcat lib: no library matches '{target}'", file=sys.stderr)
            return 1
    finally:
        rconn.close()

    if not os.path.isfile(row["db_path"]):
        print(f"Library:      {row['label']}")
        print(f"Root:         {row['root_path']}")
        print(f"DB:           {row['db_path']}  (MISSING)")
        return 1

    conn = idx.open_db(row["db_path"])
    try:
        stats = idx.index_stats(conn)
    finally:
        conn.close()

    print(f"Library:      {row['label']}")
    print(f"Root:         {row['root_path']}")
    print(f"DB:           {row['db_path']}")
    print(f"Mode:         {'in-tree' if row['in_tree'] else 'central'}")
    print(f"Total:        {stats['total_samples']}")
    print(f"With features:{stats['with_features']:>6}")
    print(f"Unique tags:  {stats['unique_tags']}")
    print(f"Descriptions: {stats['with_descriptions']}")
    if stats["by_format"]:
        print("By format:")
        for fmt in stats["by_format"]:
            print(f"  {fmt['format']:<10s} {fmt['count']}")
    return 0


def _cmd_forget(target, registry_path, quiet=False):
    rconn = reg.open_registry(registry_path)
    try:
        n = reg.forget_library(rconn, target)
    finally:
        rconn.close()
    if n == 0:
        print(f"acidcat lib: no library matches '{target}'", file=sys.stderr)
        return 1
    if not quiet:
        print(f"[INFO] forgot library '{target}' "
              f"(DB file untouched)", file=sys.stderr)
    return 0


def _cmd_remove(target, registry_path, quiet=False):
    rconn = reg.open_registry(registry_path)
    try:
        row = reg.get_library(rconn, target)
        if row is None:
            print(f"acidcat lib: no library matches '{target}'",
                  file=sys.stderr)
            return 1
        db_path = row["db_path"]
        reg.forget_library(rconn, target)
    finally:
        rconn.close()

    removed_files = []
    for suffix in ("", "-shm", "-wal"):
        cand = db_path + suffix
        if os.path.isfile(cand):
            try:
                os.remove(cand)
                removed_files.append(cand)
            except OSError as e:
                print(f"[WARN] could not remove {cand}: {e}", file=sys.stderr)
    if not quiet:
        print(f"[INFO] removed library '{target}' "
              f"({len(removed_files)} file(s) deleted)", file=sys.stderr)
    return 0


def _cmd_refresh_stats(target, registry_path, quiet=False):
    """Read each registered library's DB and push current sample/feature
    counts back into the registry. Migration helper for users whose
    libraries were registered before stats-on-attach landed (or who
    walked their DBs from outside acidcat).
    """
    rconn = reg.open_registry(registry_path)
    try:
        if target:
            row = reg.get_library(rconn, target)
            if row is None:
                print(f"acidcat lib: no library matches '{target}'",
                      file=sys.stderr)
                return 1
            libs = [row]
        else:
            libs = reg.list_libraries(rconn)
    finally:
        rconn.close()

    refreshed = 0
    skipped_missing = 0
    for lib in libs:
        if not os.path.isfile(lib["db_path"]):
            skipped_missing += 1
            if not quiet:
                print(f"[refresh-stats] skipped {lib['label']}: "
                      f"DB missing at {lib['db_path']}", file=sys.stderr)
            continue
        # Open through open_db first, on purpose: that is what applies a
        # pending schema migration, and a DB walked from outside acidcat is
        # exactly the case this command exists for. Then hand the reading to
        # the registry's own refresher rather than repeating its queries here.
        # The duplicate that used to live in this loop had drifted -- it never
        # touched schema_version, so `--refresh-stats` left the one field most
        # likely to be stale exactly as stale as it found it.
        try:
            idx.open_db(lib["db_path"]).close()
        except Exception as e:
            skipped_missing += 1
            if not quiet:
                print(f"[refresh-stats] skipped {lib['label']}: {e}",
                      file=sys.stderr)
            continue

        rconn = reg.open_registry(registry_path)
        try:
            stats = reg._refresh_stats_from_db(
                rconn, lib["root_path"], lib["db_path"])
        finally:
            rconn.close()

        if stats is None:
            skipped_missing += 1
            if not quiet:
                print(f"[refresh-stats] skipped {lib['label']}: "
                      f"not an acidcat sample DB", file=sys.stderr)
            continue

        refreshed += 1
        if not quiet:
            print(f"[refresh-stats] {lib['label']:<32s} "
                  f"samples={stats['sample_count']} "
                  f"features={stats['feature_count']}",
                  file=sys.stderr)

    if not quiet:
        # not "(missing DB)": a skip is also an unopenable file or a DB that
        # is not ours, and each one already printed its own reason above.
        print(f"[refresh-stats] {refreshed} refreshed, "
              f"{skipped_missing} skipped.", file=sys.stderr)
    return 0


# directories acidcat refuses to discover under, regardless of audio count.
# These are user homes and roots: registering them as libraries would create
# nonsense library boundaries and pull in massive unrelated content.
def _cmd_discover(root, registry_path, min_samples, max_depth, label_prefix,
                   dry_run, do_features, do_deep, quiet, verbose):
    """Walk `root`, register every qualifying subdir as its own library."""
    if not os.path.isdir(root):
        print(f"acidcat lib: --discover ROOT must be a directory: {root}",
              file=sys.stderr)
        return 1
    if _refuses_as_root(root):
        print(f"acidcat lib: refusing to --discover at {root!r}; pick a "
              f"more specific samples directory.", file=sys.stderr)
        return 1

    norm_root = acidpaths.normalize(root)

    rconn = reg.open_registry(registry_path)
    try:
        registered_roots = {
            acidpaths.compare_path(r["root_path"])
            for r in reg.list_libraries(rconn)
        }
    finally:
        rconn.close()

    if not quiet:
        print(f"[discover] walking {norm_root}", file=sys.stderr)
        print(f"[discover] min-samples={min_samples} max-depth={max_depth}",
              file=sys.stderr)

    candidates = _discover_candidates(
        norm_root, registered_roots, min_samples, max_depth,
    )

    if not candidates:
        print(f"[discover] no qualifying subdirectories found", file=sys.stderr)
        return 0

    # report
    if not quiet:
        from acidcat.core.catalogue.indexing import _count_audio_deep
        truncated_any = False
        for c in candidates:
            count, truncated = _count_audio_deep(c, max_depth=max_depth)
            truncated_any = truncated_any or truncated
            print(f"[discover] candidate: {os.path.basename(c):<40s} "
                  f"({count}{'+' if truncated else ''} samples)",
                  file=sys.stderr)
        if truncated_any:
            print(f"[discover] counts marked + are floors: audio sits below "
                  f"--max-depth {max_depth} and was not counted",
                  file=sys.stderr)

    if dry_run:
        print(f"[discover] dry-run: {len(candidates)} libraries would be "
              f"registered. No changes made.", file=sys.stderr)
        return 0

    # register each candidate
    registered = 0
    failed = 0
    used_labels = set()
    rconn = reg.open_registry(registry_path)
    try:
        for cand in candidates:
            base = os.path.basename(cand) or "library"
            base_label = (label_prefix or "") + base
            parent = os.path.basename(os.path.dirname(cand))
            label = _resolve_unique_label(rconn, base_label, parent, used_labels,
                                          root=cand)
            db_path = acidpaths.central_db_path_for(cand, label)
            try:
                reg.register_library(
                    rconn, cand, label=label, db_path=db_path,
                    in_tree=False, schema_version=idx.SCHEMA_VERSION,
                )
                # pre-touch the DB so `acidcat index --list` does not show a
                # leading '!' for libraries that were registered but never
                # walked. Empty schema is created and immediately closed; a
                # later walk fills it with rows.
                _conn = idx.open_db(db_path)
                _conn.close()
                registered += 1
                if not quiet:
                    print(f"[discover] registered '{label}' -> {cand}",
                          file=sys.stderr)
            except reg.OverlapError as e:
                failed += 1
                if not quiet:
                    print(f"[discover] skipped {cand}: {e}", file=sys.stderr)
    finally:
        rconn.close()

    # optionally walk-and-upsert (full index pass) per registered library
    if do_features or do_deep or verbose:
        if not quiet:
            print(f"[discover] indexing {registered} new libraries...",
                  file=sys.stderr)
        for cand in candidates:
            base = os.path.basename(cand) or "library"
            base_label = (label_prefix or "") + base
            label = base_label  # may differ if collision; we re-resolve below
            rconn = reg.open_registry(registry_path)
            try:
                row = reg.get_library(rconn, cand)
                if row is None:
                    continue
                label = row["label"]
                db_path = row["db_path"]
            finally:
                rconn.close()
            conn = idx.open_db(db_path)
            try:
                walk_and_upsert(
                    conn, cand,
                    do_features=do_features,
                    do_deep=do_deep,
                    quiet=quiet,
                )
                sample_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM samples"
                ).fetchone()["c"]
                feature_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM features"
                ).fetchone()["c"]
            finally:
                conn.close()
            rconn = reg.open_registry(registry_path)
            try:
                reg.update_stats(
                    rconn, cand,
                    sample_count=sample_count,
                    feature_count=feature_count,
                    last_indexed_at=time.time(),
                    schema_version=idx.SCHEMA_VERSION,
                )
            finally:
                rconn.close()

    if not quiet:
        print(f"[discover] done: {registered} registered, {failed} skipped",
              file=sys.stderr)
    return 0


# commit every N processed files so a long --rebuild --features run can
# survive interruption (Ctrl-C, power loss, OS schedule task ending) with
# only the in-flight chunk lost rather than the entire walk. Tuned high
# enough that small libraries don't pay extra fsync cost, low enough that
# a 32k-file overnight job loses minutes not hours on a crash.


# shared with core; kept as a module name so existing call sites and
# tests keep working
_escape_like = idx.escape_like


def _import_tags(conn, import_file):
    """Pull a legacy <name>_tags.json into the index.

    Match by filename basename since old CSV paths may differ from current.
    """
    with open(import_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    imported = 0
    for old_path, entry in data.items():
        base = os.path.basename(old_path.replace("\\", "/"))
        if not base:
            continue
        like = "%/" + _escape_like(base)
        rows = conn.execute(
            "SELECT path FROM samples WHERE path LIKE ? ESCAPE '\\'",
            (like,),
        ).fetchall()
        if not rows:
            continue
        desc = entry.get("description") or ""
        tags = entry.get("tags") or []
        for r in rows:
            if desc:
                idx.upsert_description(conn, r["path"], desc)
            if tags:
                idx.upsert_tags(conn, r["path"], tags)
            imported += 1
    return imported


