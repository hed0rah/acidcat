"""
acidcat survey -- count chunk IDs across a directory tree.
"""

import csv
import os
import sys
from collections import Counter, defaultdict

from acidcat.core.riff import iter_chunks
from acidcat.core.formats import output
from acidcat.util.csv_helpers import safe_basename_for_csv


def register(subparsers):
    p = subparsers.add_parser("survey", help="Count RIFF chunk types across a directory.")
    p.add_argument("target", help="Directory to scan.")
    p.add_argument("-n", "--num", type=int, default=1000000, help="Max files to scan.")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--has", help="Only count files containing these chunk IDs (comma-separated).")
    p.add_argument("--examples", type=int, default=1,
                   help="Example file paths to store per chunk ID.")
    p.add_argument("-f", "--format", default="table", choices=["table", "json", "csv"],
                   help="Output format (default: table).")
    p.add_argument("-o", "--output", help="Write output to file.")
    p.set_defaults(func=run)


def run(args):
    directory = args.target
    if not os.path.isdir(directory):
        print(f"acidcat survey: {directory}: Not a directory", file=sys.stderr)
        return 1

    wanted = None
    has_val = getattr(args, 'has', None)
    if has_val:
        wanted = set(w.strip().upper() for w in has_val.split(",") if w.strip())

    quiet = getattr(args, 'quiet', False)
    num = getattr(args, 'num', 1000000)
    max_examples = getattr(args, 'examples', 1)

    counts = Counter()
    examples = defaultdict(list)
    files_scanned = 0

    for root, _, files in os.walk(directory):
        for fn in files:
            if not fn.lower().endswith(".wav"):
                continue
            path = os.path.join(root, fn)
            ids = []
            try:
                for cid, _, _ in iter_chunks(path):
                    ids.append(cid)
            except Exception:
                continue

            if not ids:
                continue

            if wanted:
                u = {c.upper() for c in ids}
                if not (u & wanted):
                    continue

            seen_local = set()
            for c in ids:
                if c not in seen_local:
                    seen_local.add(c)
                    counts[c] += 1
                    if len(examples[c]) < max_examples:
                        examples[c].append(path)

            files_scanned += 1
            if not quiet and files_scanned % 200 == 0:
                print(f"  [survey] {files_scanned} files...", file=sys.stderr)
            if files_scanned >= num:
                break
        if files_scanned >= num:
            break

    # Format results
    rows = []
    for cid, cnt in counts.most_common():
        rows.append({
            "chunk_id": cid,
            "files": cnt,
            "example": examples[cid][0] if examples[cid] else "",
        })

    fmt_name = getattr(args, 'format', 'table')
    stream = sys.stdout
    out_path = getattr(args, 'output', None)
    if out_path:
        stream = open(out_path, 'w')

    if fmt_name == "table":
        stream.write(f"Chunk ID Survey -- {files_scanned} WAV files scanned\n\n")
        if files_scanned == 0:
            stream.write("  (no RIFF/WAV files found -- survey only processes .wav files)\n")
        for r in rows:
            stream.write(f"  {r['chunk_id']:6s} : {r['files']} files\n")
    else:
        output(rows, fmt=fmt_name, stream=stream)

    if stream is not sys.stdout:
        stream.close()
    elif not quiet:
        print(f"\n[INFO] Scanned {files_scanned} WAV file(s), {len(counts)} unique chunk ID(s).",
              file=sys.stderr)

    return 0
