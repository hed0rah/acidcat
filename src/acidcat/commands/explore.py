"""acidcat explore: build a standalone interactive HTML byte-explorer for a file.

Runs `inspect --full` internally and renders it with the packaged explorer (the
same hex-grid-with-tinted-fields datasheet, plus the LSB entropy heat-map). Uses
the CLI's stable --full JSON contract rather than importing walker internals, so
it keeps working across inspect refactors.
"""

import json
import os
import sys

from acidcat import explorer


def register(subparsers):
    p = subparsers.add_parser(
        "explore",
        help="Build a standalone interactive HTML byte-explorer of a file.")
    p.add_argument("file", metavar="FILE")
    p.add_argument("-o", "--output",
                   help="Output HTML path (default: the input name with .html).")
    p.set_defaults(func=run)


def run(args):
    path = args.file
    if not os.path.isfile(path):
        print(f"acidcat explore: {path}: No such file", file=sys.stderr)
        return 2
    # in process: the positioned dump is inspect's `full` record, which the
    # command line no longer spells (1.8's `inspect --full` is an alias for
    # --json). A subprocess running that spelling would get the wrong record.
    import contextlib
    import io
    from acidcat.commands import _legacy, inspect as inspectcmd
    ns = _legacy.parser_for(inspectcmd, "inspect").parse_args(["--json", path])
    ns.full = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = inspectcmd.run(ns)
    if rc:
        return rc
    line = buf.getvalue().splitlines()
    if not line:
        print(f"acidcat explore: {path}: inspect produced no output",
              file=sys.stderr)
        return 1
    try:
        record = json.loads(line[0])
    except ValueError:
        print(f"acidcat explore: {path}: could not parse inspect's record",
              file=sys.stderr)
        return 1
    html = explorer.build(record)
    out = args.output or (os.path.splitext(path)[0] + ".html")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print(f"wrote {out} ({len(html):,} bytes)")
    return 0
