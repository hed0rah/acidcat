"""
acidcat features -- extract 50+ audio features for ML analysis.
"""

import csv
import os
import sys

from acidcat.util import targets
from acidcat.core.infra.render import output
from acidcat.commands._output import add_output_format_arg, out_stream


def register(subparsers):
    p = subparsers.add_parser("features", help="Extract ML audio features from WAV files.")
    p.add_argument("target", help="WAV file or directory.")
    p.add_argument("-n", "--num", type=int, default=500, help="Max files to scan.")
    p.add_argument("-q", "--quiet", action="store_true")
    add_output_format_arg(p, default="csv", only=("table", "json", "csv", "tsv"))
    p.add_argument("-o", "--output", help="Output file path.")
    p.set_defaults(func=run)


def run(args):
    from acidcat.util.deps import require
    if not require("librosa", "numpy", group="analysis"):
        return 1

    from acidcat.core.analysis.features import extract_audio_features

    target = args.target
    quiet = getattr(args, 'quiet', False)
    fmt_name = getattr(args, 'output_format', 'csv')

    # Single file
    if os.path.isfile(target):
        feats = extract_audio_features(target)
        if feats is None:
            print(f"acidcat analyze: Could not extract features from {target}", file=sys.stderr)
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
        print(f"acidcat analyze: {target}: No such file or directory", file=sys.stderr)
        return 2

    num = getattr(args, 'num', 500)
    rows = []
    # matched ".wav" only, so a directory of FLAC or AIFF produced "No features
    # extracted" -- indistinguishable from an empty directory, and wrong
    files, skipped = targets.expand([target])
    capped = len(files) > num
    for filepath in files[:num]:
        if not quiet:
            print(f"  [features] {os.path.basename(filepath)}...", file=sys.stderr)
        feats = extract_audio_features(filepath)
        if feats:
            feats["filename"] = filepath
            rows.append(feats)
    if not quiet:
        note = targets.skip_note(skipped)
        if note:
            print(f"  {note}", file=sys.stderr)
        if capped:
            print(f"  read {num:,} of {len(files):,} file(s) "
                  f"(raise with --num)", file=sys.stderr)

    if not rows:
        print("acidcat analyze: No features extracted.", file=sys.stderr)
        return 0

    # An explicitly requested rendering goes to stdout (or -o), like the
    # single-file path above already did. The directory path read
    # --output-format nowhere, so `features DIR --json` accepted the flag and
    # wrote a CSV file -- and nothing at all reached a pipe.
    if fmt_name in ("json", "table"):
        stream = sys.stdout
        if getattr(args, 'output', None):
            stream = open(args.output, 'w', encoding='utf-8', newline='')
        try:
            output(rows, fmt=fmt_name, stream=stream)
        finally:
            if stream is not sys.stdout:
                stream.close()
        return 0

    # CSV goes where json and table already go: stdout unless -o names a file.
    # It used to invent `<dirname>_features.csv` in the working directory, so
    # ONE command piped for one format and silently wrote a file for another,
    # overwriting anything of that name without asking.
    out_path = getattr(args, 'output', None)

    # union of keys across rows, first-row order preserved, extras appended
    fieldnames = list(rows[0].keys())
    known = set(fieldnames)
    for r in rows[1:]:
        for k in r:
            if k not in known:
                fieldnames.append(k)
                known.add(k)
    # lineterminator="\n": csv defaults to "\r\n", and stdout is a TEXT stream
    # that translates the "\n" again, so piping gave "\r\r\n" on every row.
    with out_stream(out_path) as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames,
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    if not quiet:
        cap_note = (f" of {len(files):,} (stopped at the -n {num} cap)"
                    if capped else "")
        where = f" to {out_path}" if out_path else ""
        print(f"\n[INFO] Wrote features for {len(rows)} file(s){cap_note}"
              f"{where}", file=sys.stderr)

    return 0
