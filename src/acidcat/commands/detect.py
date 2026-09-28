"""
acidcat detect -- estimate BPM/key using librosa analysis.
"""

import csv
import os
import sys
from acidcat.util import targets
from acidcat.util import deps
from acidcat.util.stdin import display_name

from acidcat.core.analysis.detect import estimate_librosa_metadata
from acidcat.commands._output import add_output_format_arg, out_stream
from acidcat.core.infra.render import output
from acidcat.util.csv_helpers import safe_basename_for_csv


def register(subparsers):
    p = subparsers.add_parser("detect", help="Estimate BPM and key using librosa.")
    p.add_argument("target", help="WAV file or directory, or '-' for stdin.")
    p.add_argument("-n", "--num", type=int, default=500, help="Max files to scan (for dirs).")
    p.add_argument("-q", "--quiet", action="store_true")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
    p.add_argument("-o", "--output", help="Write output to file.")
    p.set_defaults(func=run)


def _detect_single(filepath, quiet=False):
    """Run detection on a single file, return dict."""
    if not quiet:
        print(f"  [detect] {display_name(filepath)}...", file=sys.stderr)

    result = estimate_librosa_metadata(filepath)
    return {
        "path": display_name(filepath),
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
    from acidcat.util.stdin import resolved_input
    with resolved_input(args.target) as _p:
        if _p is None:
            print("acidcat analyze: no data on stdin", file=sys.stderr)
            return 1
        args.target = _p
        return _run(args)


def _run(args):
    target = args.target
    quiet = getattr(args, 'quiet', False)
    fmt_name = getattr(args, 'output_format', 'table')

    if os.path.isfile(target):
        rec = _detect_single(target, quiet)
        with out_stream(getattr(args, 'output', None)) as stream:
            output(rec, fmt=fmt_name, stream=stream)
        # a missing analysis stack that the filename could not cover means the
        # command could not do its job -- report failure so `detect f && ...`
        # does not proceed on an empty answer. with librosa present, an
        # undetectable file is a legitimate answer, not an error.
        if (rec["bpm"] is None and rec["key"] is None
                and not deps.available("librosa", "numpy")):
            return 1
        return 0

    if os.path.isdir(target):
        num = getattr(args, 'num', 500)
        rows = []
        # This walk matched ".wav" only, so a directory of FLACs produced an
        # empty result and said nothing amiss -- while `detect a.flac` on the
        # same file worked. The shared expander accepts everything acidcat can
        # parse and hands back what it passed over, so the skip is stated.
        files, skipped = targets.expand([target])
        capped = len(files) > num
        for filepath in files[:num]:
            rows.append(_detect_single(filepath, quiet))
        count = len(rows)
        if not quiet:
            note = targets.skip_note(skipped)
            if note:
                print(f"  {note}", file=sys.stderr)

        with out_stream(getattr(args, 'output', None)) as stream:
            output(rows, fmt=fmt_name if fmt_name != "table" else "csv",
                   stream=stream)
        if not quiet:
            # name the true total, not just that a cap was hit -- "1 of 2" is
            # actionable where "the cap was reached" leaves you guessing
            cap_note = (f" of {len(files):,} (stopped at the -n {num} cap)"
                        if capped else "")
            print(f"\n[INFO] Detected BPM/key for {len(rows)} file(s){cap_note}.",
                  file=sys.stderr)
        return 0

    print(f"acidcat analyze: {target}: No such file or directory", file=sys.stderr)
    return 2
