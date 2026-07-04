"""acidcat explore: build a standalone interactive HTML byte-explorer for a file.

Runs `inspect --full` internally and renders it with the packaged explorer (the
same hex-grid-with-tinted-fields datasheet, plus the LSB entropy heat-map). Uses
the CLI's stable --full JSON contract rather than importing walker internals, so
it keeps working across inspect refactors.
"""

import json
import os
import subprocess
import sys

from acidcat import explorer


def register(subparsers):
    p = subparsers.add_parser(
        "explore",
        help="Build a standalone interactive HTML byte-explorer of a file.")
    p.add_argument("file")
    p.add_argument("-o", "--output",
                   help="Output HTML path (default: the input name with .html).")
    p.set_defaults(func=run)


def run(args):
    path = args.file
    if not os.path.isfile(path):
        print(f"acidcat explore: {path}: No such file", file=sys.stderr)
        return 1
    r = subprocess.run(
        [sys.executable, "-m", "acidcat", "inspect", "--full", path],
        capture_output=True)
    out_bytes, err = r.stdout, r.stderr
    if r.returncode != 0:
        sys.stderr.write(err.decode("utf-8", "replace"))
        return r.returncode or 1
    line = out_bytes.decode("utf-8", "replace").splitlines()
    if not line:
        print(f"acidcat explore: {path}: inspect produced no output",
              file=sys.stderr)
        return 1
    try:
        record = json.loads(line[0])
    except ValueError:
        print(f"acidcat explore: {path}: could not parse inspect --full output",
              file=sys.stderr)
        return 1
    html = explorer.build(record)
    out = args.output or (os.path.splitext(path)[0] + ".html")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print(f"wrote {out} ({len(html):,} bytes)")
    return 0
