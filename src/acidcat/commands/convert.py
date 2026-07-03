"""Convert a DAW clip's notes to a Standard MIDI File.

Currently supports Bitwig note clips (.bwclip). acidcat reads the clip's notes
(pitch, position, duration, velocity) and writes a type-0 SMF.
"""

import os
import sys

from acidcat.core import bitwig as bwmod
from acidcat.core.midi_write import notes_to_smf


def register(subparsers):
    p = subparsers.add_parser(
        "convert",
        help="Convert a DAW clip's notes to a Standard MIDI File (.mid).",
    )
    p.add_argument("input", help="Input clip (Bitwig .bwclip).")
    p.add_argument("-o", "--output",
                   help="Output .mid path (default: input name with .mid).")
    p.add_argument("--division", type=int, default=480,
                   help="MIDI ticks per beat (default 480).")
    p.set_defaults(func=run)


def run(args):
    path = args.input
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"acidcat convert: {path}: {e}", file=sys.stderr)
        return 1
    if data[:4] != bwmod.MAGIC:
        print(f"acidcat convert: {path}: not a Bitwig clip "
              f"(only .bwclip is supported so far)", file=sys.stderr)
        return 1
    notes = bwmod.parse_notes(data)
    if not notes:
        print(f"acidcat convert: {path}: no notes found in clip",
              file=sys.stderr)
        return 1
    bpm = bwmod.parse_numeric(data).get("bpm") or 120.0
    smf = notes_to_smf(notes, bpm=bpm, division=args.division)
    out = args.output or (os.path.splitext(path)[0] + ".mid")
    with open(out, "wb") as f:
        f.write(smf)
    print(f"wrote {out}: {len(notes)} notes, {bpm:g} bpm")
    return 0
