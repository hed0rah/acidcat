"""
acidcat features -- extract 50+ audio features for ML analysis.
"""

import csv
import os
import sys

from acidcat.core.formats import output
from acidcat.util.csv_helpers import safe_basename_for_csv


def register(subparsers):
    p = subparsers.add_parser("features", help="Extract ML audio features from WAV files.")
    p.add_argument("target", help="WAV file or directory.")
    p.add_argument("-n", "--num", type=int, default=500, help="Max files to scan.")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-f", "--format", default="csv", choices=["table", "json", "csv"],
                   help="Output format (default: csv).")
    p.add_argument("-o", "--output", help="Output file path.")
    p.set_defaults(func=run)


def run(args):
    from acidcat.util.deps import require
    if not require("librosa", "numpy", group="analysis"):
        return 1

    from acidcat.core.features import extract_audio_features

    target = args.target
    quiet = getattr(args, 'quiet', False)
    fmt_name = getattr(args, 'format', 'csv')

    # Single file
    if os.path.isfile(target):
        feats = extract_audio_features(target)
        if feats is None:
            print(f"acidcat features: Could not extract features from {target}", file=sys.stderr)
            return 1
        feats["filename"] = os.path.basename(target)
        stream = sys.stdout
        if getattr(args, 'output', None):
            stream = open(args.output, 'w', encoding='utf-8')
        output(feats, fmt=fmt_name, stream=stream)
        if stream is not sys.stdout:
            stream.close()
        return 0

    # Directory
    if not os.path.isdir(target):
        print(f"acidcat features: {target}: No such file or directory", file=sys.stderr)
        return 1

    num = getattr(args, 'num', 500)
    rows = []
    count = 0

    for root, _, files in os.walk(target):
        for fn in files:
            if not fn.lower().endswith(".wav"):
                continue
            filepath = os.path.join(root, fn)
            if not quiet:
                print(f"  [features] {fn}...", file=sys.stderr)
            feats = extract_audio_features(filepath)
            if feats:
                feats["filename"] = filepath
                rows.append(feats)
            count += 1
            if count >= num:
                break
        if count >= num:
            break

    if not rows:
        print("acidcat features: No features extracted.", file=sys.stderr)
        return 0

    default_base = os.path.basename(os.path.normpath(target))
    out_path = getattr(args, 'output', None) or safe_basename_for_csv(
        default_base + "_features.csv"
    )

    # union of keys across rows, first-row order preserved, extras appended
    fieldnames = list(rows[0].keys())
    known = set(fieldnames)
    for r in rows[1:]:
        for k in r:
            if k not in known:
                fieldnames.append(k)
                known.add(k)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    if not quiet:
        print(f"\n[INFO] Wrote features for {len(rows)} files to {out_path}", file=sys.stderr)

    return 0
