"""
acidcat index -- walk a directory and upsert samples into the global
SQLite index at ~/.acidcat/index.db.
"""

import json
import os
import sys
import time

from acidcat.core import index as idx
from acidcat.core.riff import parse_riff, get_duration, get_fmt_info
from acidcat.core.aiff import is_aiff, parse_aiff
from acidcat.core.midi import is_midi, parse_midi
from acidcat.core.serum import is_serum_preset, parse_serum_preset
from acidcat.core.tagged import is_tagged_format


INDEXABLE_EXTENSIONS = {
    ".wav", ".aif", ".aiff",
    ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac",
    ".mid", ".midi",
    ".serumpreset",
}

# OS sidecar / metadata junk that shows up in copied libraries.
JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini"}


def _is_junk(name):
    """True for files we never want in the index (AppleDouble, OS metadata)."""
    if name.startswith("._"):
        return True
    return name.lower() in JUNK_FILES


def register(subparsers):
    p = subparsers.add_parser(
        "index",
        help="Build/update the global sample index at ~/.acidcat/index.db.",
    )
    p.add_argument("target", nargs="?", help="Directory to index.")
    p.add_argument("--db", help="Override DB path (default: ~/.acidcat/index.db).")
    p.add_argument("--features", action="store_true",
                   help="Extract librosa audio features during indexing.")
    p.add_argument("--deep", action="store_true",
                   help="Use librosa for BPM/key when metadata is absent.")
    p.add_argument("--import-tags", dest="import_tags",
                   help="Import a legacy <name>_tags.json into the index.")
    p.add_argument("--list-roots", action="store_true",
                   help="Print known scan roots and exit.")
    p.add_argument("--remove-root", dest="remove_root",
                   help="Delete all samples under this root and exit.")
    p.add_argument("--stats", action="store_true",
                   help="Print index summary and exit.")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=run)


def run(args):
    db_path = idx.resolve_db_path(getattr(args, "db", None))
    conn = idx.open_db(db_path)
    quiet = getattr(args, "quiet", False)

    try:
        if args.list_roots:
            _print_roots(conn, db_path)
            return 0

        if args.remove_root:
            root = idx.normalize_path(args.remove_root)
            removed = idx.remove_root(conn, root)
            conn.commit()
            if not quiet:
                print(f"[INFO] removed {removed} sample(s) under {root}",
                      file=sys.stderr)
            return 0

        if args.stats:
            _print_stats(conn, db_path)
            return 0

        if not args.target:
            print("acidcat index: missing target directory (or use "
                  "--list-roots/--remove-root/--stats)", file=sys.stderr)
            return 1

        target = args.target
        if not os.path.isdir(target):
            print(f"acidcat index: {target}: Not a directory", file=sys.stderr)
            return 1

        scan_root = idx.normalize_path(target)
        if not quiet:
            print(f"[INFO] indexing {scan_root} -> {db_path}", file=sys.stderr)

        counts = _walk_and_upsert(
            conn, scan_root,
            do_features=args.features,
            do_deep=args.deep,
            quiet=quiet,
        )

        if args.import_tags:
            imported = _import_tags(conn, args.import_tags)
            counts["tags_imported"] = imported
            if not quiet:
                print(f"[INFO] imported tags for {imported} sample(s) "
                      f"from {args.import_tags}", file=sys.stderr)

        conn.commit()

        if not quiet:
            print(
                f"[INFO] {counts['added']} added, {counts['updated']} updated, "
                f"{counts['skipped']} skipped, {counts['pruned']} pruned, "
                f"{counts['failed']} failed",
                file=sys.stderr,
            )
        return 0
    finally:
        conn.close()


def _walk_and_upsert(conn, scan_root, do_features=False, do_deep=False, quiet=False):
    walk_start = time.time()
    added = updated = skipped = failed = 0
    seen_paths = 0

    for root, _, files in os.walk(scan_root):
        for name in files:
            if _is_junk(name):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in INDEXABLE_EXTENSIONS:
                continue

            filepath = os.path.join(root, name)
            norm = idx.normalize_path(filepath)
            try:
                st = os.stat(filepath)
            except OSError:
                continue

            existing = idx.get_sample_stat(conn, norm)
            if existing is not None:
                old_mtime, old_size = existing
                if old_mtime == st.st_mtime and old_size == st.st_size:
                    idx.touch_last_seen(conn, norm, walk_start)
                    skipped += 1
                    seen_paths += 1
                    continue

            row = _extract_for_index(
                filepath, scan_root=scan_root,
                mtime=st.st_mtime, size=st.st_size,
                walk_start=walk_start,
                do_deep=do_deep,
            )
            if row is None:
                failed += 1
                if not quiet:
                    print(f"  [skip] {name}: parse failed", file=sys.stderr)
                continue

            row_tags = row.pop("_tags", None)

            if existing is None:
                added += 1
            else:
                updated += 1
            idx.upsert_sample(conn, row)

            if row_tags:
                idx.upsert_tags(conn, row["path"], row_tags, replace=True)

            if do_features:
                _extract_and_store_features(conn, filepath, row["path"], quiet=quiet)

            seen_paths += 1
            if not quiet:
                bpm = row.get("bpm") or "-"
                key = row.get("key") or "-"
                print(f"  {name:40s} bpm={bpm} key={key}", file=sys.stderr)

    pruned = idx.prune_missing(conn, scan_root, walk_start)
    idx.record_scan_root(conn, scan_root, seen_paths, walk_start)

    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "pruned": pruned,
    }


def _extract_for_index(filepath, scan_root, mtime, size, walk_start, do_deep=False):
    """Dispatch to the right parser and return a sample row dict.

    Returns None if the file could not be parsed at all.
    Includes a synthetic '_tags' list for formats that carry their own tags
    (e.g. Serum presets).
    """
    norm = idx.normalize_path(filepath)
    ext = os.path.splitext(filepath)[1].lower()

    row = {
        "path": norm,
        "scan_root": scan_root,
        "mtime": mtime,
        "size": size,
        "indexed_at": walk_start,
        "last_seen_at": walk_start,
    }

    try:
        if is_midi(filepath) or ext in (".mid", ".midi"):
            return _from_midi(filepath, row)
        if is_aiff(filepath) or ext in (".aif", ".aiff"):
            return _from_aiff(filepath, row)
        if is_serum_preset(filepath) or ext == ".serumpreset":
            return _from_serum(filepath, row)
        if is_tagged_format(filepath) and ext != ".wav":
            return _from_tagged(filepath, row, do_deep=do_deep)
        # default: WAV/RIFF
        return _from_wav(filepath, row, do_deep=do_deep)
    except Exception:
        return None


def _from_wav(filepath, row, do_deep=False):
    from acidcat.util.midi import midi_note_to_pitch_class
    from acidcat.core.detect import parse_key_from_path, parse_bpm_from_filename

    _, meta, seen = parse_riff(filepath, enumerate_all=False)
    duration = get_duration(filepath)
    fmt = get_fmt_info(filepath)

    row["format"] = "wav"
    row["duration"] = duration
    row["bpm"] = meta.get("bpm")

    # SMPL/ACID root_note = 0 (MIDI C-1) is the default "unset" value.
    # Treat it as missing so we can fall back to filename parsing.
    smpl = meta.get("smpl_root_key")
    acid = meta.get("acid_root_note")
    if not smpl:
        smpl = None
    if not acid:
        acid = None

    # key stores pitch class only (no octave); full MIDI int lives in root_note.
    row["key"] = midi_note_to_pitch_class(smpl) or midi_note_to_pitch_class(acid)
    row["acid_beats"] = meta.get("acid_beats")
    row["root_note"] = smpl or acid
    row["chunks"] = ",".join(
        c for c in seen if c not in ("RIFF", "WAVE", "fmt ", "data")
    ) or None

    if fmt:
        row["sample_rate"] = fmt.get("sample_rate")
        row["channels"] = fmt.get("channels")
        row["bits_per_sample"] = fmt.get("bits_per_sample")

    if row["key"] is None:
        row["key"] = parse_key_from_path(filepath)
    if row["bpm"] is None:
        fname_bpm = parse_bpm_from_filename(filepath)
        if fname_bpm is not None:
            row["bpm"] = float(fname_bpm)

    if do_deep and row["bpm"] is None:
        _fill_from_librosa(filepath, row)

    return row


def _from_aiff(filepath, row, do_deep=False):
    from acidcat.core.detect import parse_key_from_path, parse_bpm_from_filename

    _, meta, seen = parse_aiff(filepath, enumerate_all=False)
    row["format"] = "aiff"
    row["duration"] = meta.get("duration_sec")
    row["sample_rate"] = meta.get("sample_rate")
    row["channels"] = meta.get("channels")
    row["bits_per_sample"] = meta.get("bits_per_sample")
    row["title"] = meta.get("name")
    row["artist"] = meta.get("author")
    row["comment"] = meta.get("copyright")
    row["chunks"] = ",".join(seen) if seen else None

    # AIFF has no standard bpm/key chunks; fall back to filename/folder tokens.
    if row.get("key") is None:
        row["key"] = parse_key_from_path(filepath)
    if row.get("bpm") is None:
        fname_bpm = parse_bpm_from_filename(filepath)
        if fname_bpm is not None:
            row["bpm"] = float(fname_bpm)

    if do_deep and row.get("bpm") is None:
        _fill_from_librosa(filepath, row)
    return row


def _from_midi(filepath, row):
    meta = parse_midi(filepath)
    row["format"] = "midi"
    row["duration"] = meta.get("duration_sec")
    row["bpm"] = meta.get("tempo_bpm")
    row["key"] = meta.get("key_sig")
    if meta.get("track_names"):
        row["title"] = meta["track_names"][0]
    if meta.get("copyright"):
        row["comment"] = meta["copyright"]
    return row


def _from_serum(filepath, row):
    meta = parse_serum_preset(filepath)
    row["format"] = "serum"
    if meta.get("presetName"):
        row["title"] = meta["presetName"]
    if meta.get("presetAuthor"):
        row["artist"] = meta["presetAuthor"]
    if meta.get("presetDescription"):
        row["comment"] = meta["presetDescription"]
    tags = meta.get("tags")
    if isinstance(tags, list):
        row["_tags"] = tags
    elif isinstance(tags, str) and tags:
        row["_tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    return row


def _from_tagged(filepath, row, do_deep=False):
    from acidcat.core.tagged import parse_tagged
    from acidcat.core.detect import parse_key_from_path, parse_bpm_from_filename

    meta = parse_tagged(filepath)
    if meta is None:
        return None
    row["format"] = meta.get("format_type") or "tagged"
    row["duration"] = meta.get("duration")
    row["sample_rate"] = meta.get("sample_rate")
    row["channels"] = meta.get("channels")
    row["bits_per_sample"] = meta.get("bits_per_sample")
    row["title"] = meta.get("title")
    row["artist"] = meta.get("artist")
    row["album"] = meta.get("album")
    row["genre"] = meta.get("genre")
    row["comment"] = meta.get("comment")
    row["bpm"] = _coerce_bpm(meta.get("bpm"))
    row["key"] = meta.get("key")

    if row["key"] is None:
        row["key"] = parse_key_from_path(filepath)
    if row["bpm"] is None:
        fname_bpm = parse_bpm_from_filename(filepath)
        if fname_bpm is not None:
            row["bpm"] = float(fname_bpm)

    if do_deep and row["bpm"] is None:
        _fill_from_librosa(filepath, row)
    return row


def _coerce_bpm(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fill_from_librosa(filepath, row):
    try:
        from acidcat.core.detect import estimate_librosa_metadata
    except ImportError:
        return
    est = estimate_librosa_metadata(filepath) or {}
    if est.get("estimated_bpm") and row.get("bpm") is None:
        bpm = est["estimated_bpm"]
        if isinstance(bpm, (int, float)):
            row["bpm"] = float(bpm)
    if est.get("estimated_key") and row.get("key") is None:
        row["key"] = est["estimated_key"]
    if est.get("duration_sec") and row.get("duration") is None:
        row["duration"] = est["duration_sec"]


def _extract_and_store_features(conn, filepath, path_key, quiet=False):
    try:
        from acidcat.core.features import extract_audio_features
    except ImportError:
        if not quiet:
            print("  [features] librosa not installed; skipping", file=sys.stderr)
        return
    feats = extract_audio_features(filepath)
    if feats is None:
        return
    idx.upsert_features(conn, path_key, feats, version=1)


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
        like = "%/" + base
        rows = conn.execute(
            "SELECT path FROM samples WHERE path LIKE ?", (like,)
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


def _print_roots(conn, db_path):
    roots = idx.list_roots(conn)
    if not roots:
        print(f"(no scan roots in {db_path})")
        return
    for r in roots:
        print(f"{r['file_count']:>6}  {r['path']}")


def _print_stats(conn, db_path):
    stats = idx.index_stats(conn)
    print(f"DB: {db_path}")
    print(f"Total samples: {stats['total_samples']}")
    print(f"With features: {stats['with_features']}")
    print(f"With descriptions: {stats['with_descriptions']}")
    print(f"Unique tags: {stats['unique_tags']}")
    if stats["by_format"]:
        print("By format:")
        for row in stats["by_format"]:
            print(f"  {row['format']:<10s} {row['count']}")
    if stats["roots"]:
        print("Scan roots:")
        for r in stats["roots"]:
            print(f"  {r['file_count']:>6}  {r['path']}")
