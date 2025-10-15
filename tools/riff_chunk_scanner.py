#!/usr/bin/env python3
"""
riff_chunk_scanner.py

Quickly scan a directory tree of WAV files and report which RIFF chunk IDs
appear and how often. Also records example file paths per chunk.

Examples:
  python riff_chunk_scanner.py "D:\\Audio\\Loops" -n 2000
  python riff_chunk_scanner.py "D:\\Audio\\Loops" --has acid,smpl -n 5000
  python riff_chunk_scanner.py "D:\\Audio\\Loops" -o chunks_survey.csv
"""

import argparse
import os
import struct
import csv
from collections import defaultdict

def iter_chunks(filepath):
    """Yield (chunk_id_str, offset, size) for each chunk in a RIFF/WAVE file."""
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        header = f.read(12)
        if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            return
        pos = 12
        while pos + 8 <= size:
            f.seek(pos)
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid = hdr[0:4].decode("ascii", errors="ignore")
            try:
                csize = struct.unpack("<I", hdr[4:8])[0]
            except struct.error:
                break
            yield (cid, pos, csize)
            pos += 8 + csize
            if csize % 2 == 1:
                pos += 1

def main():
    ap = argparse.ArgumentParser(description="Scan WAV files and count RIFF chunk IDs.")
    ap.add_argument("directory", help="Root directory to scan")
    ap.add_argument("-n", "--num", type=int, default=1000000, help="Max WAV files to scan (default: 1,000,000)")
    ap.add_argument("-q", "--quiet", action="store_true", help="Reduce console output")
    ap.add_argument("--has", help="Only count files that contain ANY of these chunk IDs (comma-separated, e.g. 'acid,smpl')")
    ap.add_argument("--examples", type=int, default=1, help="How many example file paths to store per chunk id")
    ap.add_argument("--progress", type=int, default=200, help="Print progress every N files (if not quiet)")
    ap.add_argument("-o", "--output", help="Optional CSV to write survey results")
    args = ap.parse_args()

    wanted = None
    if args.has:
        wanted = set(w.strip().upper() for w in args.has.split(",") if w.strip())

    counts = defaultdict(int)
    examples = defaultdict(list)
    files_scanned = 0
    wav_seen = 0

    for root, _, files in os.walk(args.directory):
        for fn in files:
            if not fn.lower().endswith(".wav"):
                continue
            wav_seen += 1
            path = os.path.join(root, fn)
            # Collect all chunk ids in this file
            ids = []
            try:
                for cid, _, _ in iter_chunks(path):
                    ids.append(cid)
            except Exception:
                continue  # skip unreadable

            if not ids:
                continue

            # Filter by --has
            if wanted:
                u = {c.upper() for c in ids}
                if not (u & wanted):
                    continue

            # Count each chunk id once per file (like "files containing CID")
            unique_ids = []
            seen_local = set()
            for c in ids:
                if c not in seen_local:
                    seen_local.add(c)
                    unique_ids.append(c)

            for c in unique_ids:
                counts[c] += 1
                if len(examples[c]) < args.examples:
                    examples[c].append(path)

            files_scanned += 1
            if not args.quiet and files_scanned % args.progress == 0:
                print(f"[INFO] Scanned {files_scanned} files...")

            if files_scanned >= args.num:
                break
        if files_scanned >= args.num:
            break

    # Print summary
    if not args.quiet:
        print()
    print(f"[INFO] Scanned {files_scanned} file(s). Found chunk IDs:\n")
    for cid, cnt in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        eg = examples[cid][0] if examples[cid] else ""
        print(f"{cid:4s}: {cnt} files (e.g. {eg})")

    # Optional CSV
    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["chunk_id", "files_with_chunk", "example_paths"])
            for cid, cnt in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
                w.writerow([cid, cnt, " | ".join(examples[cid])])
        print(f"\n[INFO] Wrote survey to {args.output}")

if __name__ == "__main__":
    main()
