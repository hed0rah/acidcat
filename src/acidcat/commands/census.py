"""
acidcat census -- chunk-id histogram and open-question flags over a corpus.

A scaled-up, read-only survey of a RIFF-family tree: which FOURCCs actually
occur and how often, the container-variant and format-tag distributions, and
flags for the rare/undocumented chunks worth a closer look. Built to run over
millions of files without evicting the machine's working set; see
``acidcat.core.census`` for the traversal and read strategy.
"""

import json
import os
import sys
import time

from acidcat.core import census as _census


def register(subparsers):
    p = subparsers.add_parser(
        "census", help="Chunk-ID histogram + open-question flags over a corpus.")
    p.add_argument("target", nargs="+", help="Directory tree(s) to scan.")
    p.add_argument("-f", "--format", default="table", choices=["table", "json"],
                   help="Output format (default: table).")
    p.add_argument("-o", "--output", help="Write output to file.")
    p.add_argument("--limit", type=int, help="Stop after N files opened.")
    p.add_argument("--top", type=int, default=60,
                   help="Chunks to show in the table / json histogram (default 60).")
    p.add_argument("--jobs", default="auto",
                   help="Reader threads: N, or 'auto' (1 on HDD, CPU*4 on SSD).")
    p.add_argument("--io-hint", default="auto", choices=["auto", "ssd", "hdd"],
                   help="Storage kind for the default job count (default: auto).")
    p.add_argument("--follow-symlinks", action="store_true",
                   help="Follow directory symlinks (loop-safe; default off).")
    p.add_argument("--one-file-system", action="store_true",
                   help="Do not cross into other mounted filesystems.")
    p.add_argument("--noatime", action="store_true",
                   help="Open with O_NOATIME where permitted (Linux).")
    p.add_argument("--no-fadvise", action="store_true",
                   help="Do not hint the kernel to drop scanned pages from cache.")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress the progress line on stderr.")
    p.set_defaults(func=run)


def run(args):
    roots = []
    for t in args.target:
        if not os.path.isdir(t):
            print(f"acidcat census: {t}: Not a directory", file=sys.stderr)
            return 1
        roots.append(t)

    opts = _census.ScanOptions(
        follow_symlinks=args.follow_symlinks,
        one_file_system=args.one_file_system,
        fadvise=not args.no_fadvise,
        noatime=args.noatime,
    )
    jobs = args.jobs if args.jobs == "auto" else int(args.jobs)

    quiet = args.quiet
    t0 = time.time()

    def progress(files, riff, errors):
        if quiet:
            return
        riff_s = "" if riff < 0 else f", {riff} riff"
        print(f"  [census] {files} files{riff_s}, {round(time.time() - t0)}s",
              file=sys.stderr, flush=True)

    cx = _census.run_census(roots, opts=opts, jobs=jobs, io_hint=args.io_hint,
                            limit=args.limit, progress=progress)
    res = cx.result(top=args.top)
    res["elapsed_sec"] = round(time.time() - t0, 1)

    stream = sys.stdout
    out_path = args.output
    if out_path:
        stream = open(out_path, "w", encoding="utf-8")
    try:
        if args.format == "json":
            json.dump(res, stream, indent=2)
            stream.write("\n")
        else:
            _write_table(stream, res)
    finally:
        if stream is not sys.stdout:
            stream.close()

    if not quiet and args.format != "json":
        print(f"\n[census] {res['files_opened']} files, "
              f"{res['riff_family_files']} RIFF-family, "
              f"{res['distinct_chunks']} distinct chunks, "
              f"{res['errors']} errors in {res['elapsed_sec']}s", file=sys.stderr)
    return 0


def _write_table(w, res):
    w.write(f"Corpus census -- {res['files_opened']} files opened, "
            f"{res['riff_family_files']} RIFF-family\n\n")

    if res["containers"]:
        w.write("Containers\n")
        for k, n in res["containers"].items():
            w.write(f"  {k:14s} {n}\n")
        w.write("\n")

    hist = res["chunk_histogram"]
    if hist:
        w.write(f"Chunk histogram ({res['distinct_chunks']} distinct)\n")
        for cid, n in hist.items():
            w.write(f"  {cid:10s} {n}\n")
        w.write("\n")

    if res["format_tags"]:
        w.write("Format tags\n")
        for t, n in res["format_tags"].items():
            w.write(f"  {t:8s} {n}\n")
        w.write("\n")

    if res["bext_versions"]:
        w.write("bext versions\n")
        for v, n in sorted(res["bext_versions"].items()):
            w.write(f"  v{v:5s} {n}\n")
        w.write("\n")

    if res["flags"]:
        w.write("Flags (examples capped)\n")
        for name, paths in sorted(res["flags"].items()):
            w.write(f"  {name:22s} {len(paths)}  e.g. {paths[0] if paths else ''}\n")
        w.write("\n")

    if res["rare_chunks"]:
        w.write(f"Rare chunks (<=5 occurrences): "
                f"{len(res['rare_chunks'])}\n")
        for cid, n, ex in res["rare_chunks"][:40]:
            w.write(f"  {cid:10s} {n}  {ex}\n")
