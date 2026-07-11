"""Format conversion.

- Bitwig note clips (.bwclip) -> a Standard MIDI File: the clip's notes
  (pitch, position, duration, velocity) become a type-0 SMF.
- NI Compressed Wave (.ncw) -> a WAV: NCW is Kontakt's lossless codec (DPCM +
  bit-truncation + mid/side); decode reconstructs the PCM. Compression, not
  access control -- no key, nothing bypassed, the same class of work as
  decoding FLAC.
"""

import os
import sys

from acidcat.core import bitwig as bwmod
from acidcat.core import ncw as ncwmod
from acidcat.core.midi_write import notes_to_smf


def register(subparsers):
    p = subparsers.add_parser(
        "convert",
        help="Convert a Bitwig clip to MIDI, or an NCW sample to WAV.",
    )
    p.add_argument("input", help="Input file (.bwclip or .ncw).")
    p.add_argument("-o", "--output",
                   help="Output path (default: input name with .mid / .wav).")
    p.add_argument("--division", type=int, default=480,
                   help="MIDI ticks per beat for .bwclip output (default 480).")
    p.set_defaults(func=run)


def _run_ncw(path, data, args):
    try:
        hdr, chans = ncwmod.decode(data)
        wav = ncwmod.to_wav(hdr, chans)
    except ncwmod.NcwError as e:
        print(f"acidcat convert: {path}: {e}", file=sys.stderr)
        return 1
    out = args.output or (os.path.splitext(path)[0] + ".wav")
    with open(out, "wb") as f:
        f.write(wav)
    print(f"wrote {out}: {hdr['channels']}ch {hdr['bits']}-bit "
          f"{hdr['sample_rate']} Hz, {hdr['num_samples']:,} samples")
    return 0


def run(args):
    path = args.input
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"acidcat convert: {path}: {e}", file=sys.stderr)
        return 1
    if data[:4] == ncwmod.MAGIC:
        return _run_ncw(path, data, args)
    if data[:4] != bwmod.MAGIC:
        print(f"acidcat convert: {path}: unsupported input "
              f"(expected a Bitwig .bwclip or an NCW .ncw)", file=sys.stderr)
        return 1
    try:
        notes = bwmod.parse_notes(data)
    except Exception as e:
        print(f"acidcat convert: {path}: could not parse notes "
              f"({e.__class__.__name__})", file=sys.stderr)
        return 1
    if not notes:
        print(f"acidcat convert: {path}: no notes found in clip",
              file=sys.stderr)
        return 1
    bpm = bwmod.parse_numeric(data).get("bpm") or 120.0
    try:
        smf = notes_to_smf(notes, bpm=bpm, division=args.division)
    except Exception as e:
        print(f"acidcat convert: {path}: could not build MIDI "
              f"({e.__class__.__name__})", file=sys.stderr)
        return 1
    out = args.output or (os.path.splitext(path)[0] + ".mid")
    with open(out, "wb") as f:
        f.write(smf)
    print(f"wrote {out}: {len(notes)} notes, {bpm:g} bpm")
    return 0
