"""
acidcat detect -- estimate BPM/key using librosa analysis.
"""

import csv
import os
import sys

from acidcat.core.detect import estimate_librosa_metadata
from acidcat.core.formats import output
from acidcat.util.csv_helpers import safe_basename_for_csv


def register(subparsers):
    p = subparsers.add_parser("detect", help="Estimate BPM and key using librosa.")
    p.add_argument("target", help="WAV file or directory.")
    p.add_argument("-n", "--num", type=int, default=500, help="Max files to scan (for dirs).")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-f", "--format", default="table", choices=["table", "json", "csv"],
                   help="Output format (default: table).")
    p.add_argument("-o", "--output", help="Write output to file.")
    p.set_defaults(func=run)


def _detect_single(filepath, quiet=False):
    """Run detection on a single file, return dict."""
    if not quiet:
        print(f"  [detect] {os.path.basename(filepath)}...", file=sys.stderr)

    result = estimate_librosa_metadata(filepath)
    return {
        "filename": os.path.basename(filepath),
        "bpm": result.get("estimated_bpm"),
        "key": result.get("estimated_key"),
        "duration_sec": result.get("duration_sec"),
        "bpm_source": result.get("bpm_source"),
        "key_source": result.get("key_source"),
        "filename_bpm": result.get("filename_bpm"),
        "filename_key": result.get("filename_key"),
        "detected_bpm": result.get("detected_bpm"),
        "detected_key": result.get("detected_key"),
    }


def run(args):
    target = args.target
    quiet = getattr(args, 'quiet', False)
    fmt_name = getattr(args, 'format', 'table')

    if os.path.isfile(target):
        rec = _detect_single(target, quiet)
        stream = sys.stdout
        if getattr(args, 'output', None):
            stream = open(args.output, 'w')
        output(rec, fmt=fmt_name, stream=stream)
        if stream is not sys.stdout:
            stream.close()
        return 0

    if os.path.isdir(target):
        num = getattr(args, 'num', 500)
        rows = []
        count = 0
        for root, _, files in os.walk(target):
            for fn in files:
                if not fn.lower().endswith(".wav"):
                    continue
                filepath = os.path.join(root, fn)
                rows.append(_detect_single(filepath, quiet))
                count += 1
                if count >= num:
                    break
            if count >= num:
                break

        stream = sys.stdout
        out_path = getattr(args, 'output', None)
        if out_path:
            stream = open(out_path, 'w')
        output(rows, fmt=fmt_name if fmt_name != "table" else "csv", stream=stream)
        if stream is not sys.stdout:
            stream.close()
        if not quiet:
            print(f"\n[INFO] Detected BPM/key for {len(rows)} files.", file=sys.stderr)
        return 0

    print(f"acidcat detect: {target}: No such file or directory", file=sys.stderr)
    return 1
