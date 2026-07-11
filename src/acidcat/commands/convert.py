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
from acidcat.core import sf2 as sf2mod
from acidcat.core.midi_write import notes_to_smf


def _safe_name(name, idx, ext="wav"):
    """A filesystem-safe filename for a sample (names can carry / and other
    reserved chars, and can collide/repeat), prefixed with the index."""
    keep = "".join(c if c.isalnum() or c in " -_.()" else "_" for c in name)
    return f"{idx:04d}_{keep.strip() or 'sample'}.{ext}"


def register(subparsers):
    p = subparsers.add_parser(
        "convert",
        help="Bitwig clip -> MIDI, NCW -> WAV, or SF2 -> a folder of WAV samples.",
    )
    p.add_argument("input", help="Input file (.bwclip / .ncw / .sf2), or a "
                                 "directory to batch-convert every .ncw within.")
    p.add_argument("-o", "--output",
                   help="Output path (single file); ignored for a directory, "
                        "where each WAV is written beside its .ncw.")
    p.add_argument("--division", type=int, default=480,
                   help="MIDI ticks per beat for .bwclip output (default 480).")
    p.add_argument("--skip-existing", action="store_true",
                   help="Batch mode: skip an .ncw whose .wav already exists.")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Batch mode: suppress the per-file line, keep the summary.")
    p.set_defaults(func=run)


def _batch_ncw(directory, args):
    """Convert every .ncw under `directory` to a sibling .wav. Read-only on the
    inputs; one bad file is counted and skipped, never fatal."""
    done = skipped = failed = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if not name.lower().endswith(".ncw"):
                continue
            src = os.path.join(root, name)
            out = os.path.splitext(src)[0] + ".wav"
            if args.skip_existing and os.path.exists(out):
                skipped += 1
                continue
            try:
                with open(src, "rb") as f:
                    data = f.read()
                hdr, chans = ncwmod.decode(data)
                with open(out, "wb") as f:
                    f.write(ncwmod.to_wav(hdr, chans))
                done += 1
                if not args.quiet:
                    print(f"  {os.path.relpath(src, directory)} -> "
                          f"{hdr['num_samples']:,} samples", file=sys.stderr)
            except (ncwmod.NcwError, OSError) as e:
                failed += 1
                print(f"  [skip] {os.path.relpath(src, directory)}: {e}",
                      file=sys.stderr)
    print(f"converted {done:,} .ncw -> .wav"
          + (f", skipped {skipped:,} existing" if skipped else "")
          + (f", {failed:,} failed" if failed else ""))
    return 0 if done or not failed else 1


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


def _run_sf2(path, data, args):
    try:
        info = sf2mod.parse_sf2(data)
    except sf2mod.Sf2Error as e:
        print(f"acidcat convert: {path}: {e}", file=sys.stderr)
        return 1
    samples = info["samples"]
    if not samples:
        print(f"acidcat convert: {path}: no extractable samples", file=sys.stderr)
        return 1
    outdir = args.output or (os.path.splitext(path)[0] + "_samples")
    os.makedirs(outdir, exist_ok=True)
    for i, s in enumerate(samples):
        if s.get("compressed"):
            # SF3 stores each sample as an Ogg Vorbis stream; extract it verbatim
            # (decoding Vorbis to PCM needs a codec acidcat does not bundle)
            blob, ext = sf2mod.sample_bytes(data, s), "ogg"
        else:
            blob, ext = sf2mod.sample_wav(data, info["smpl_offset"], s), "wav"
        with open(os.path.join(outdir, _safe_name(s["name"], i, ext)), "wb") as f:
            f.write(blob)
    kind = "Ogg Vorbis streams" if info.get("sf3") else "WAV samples"
    print(f"extracted {len(samples):,} {kind} -> {outdir}")
    return 0


def run(args):
    path = args.input
    if os.path.isdir(path):
        return _batch_ncw(path, args)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"acidcat convert: {path}: {e}", file=sys.stderr)
        return 1
    if data[:4] == ncwmod.MAGIC:
        return _run_ncw(path, data, args)
    if sf2mod.is_sf2(data):
        return _run_sf2(path, data, args)
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
