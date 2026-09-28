"""Format conversion.

- Bitwig note clips (.bwclip) -> a Standard MIDI File: the clip's notes
  (pitch, position, duration, velocity) become a type-0 SMF.
- NI Compressed Wave (.ncw) -> a WAV: NCW is Kontakt's lossless codec (DPCM +
  bit-truncation + mid/side); decode reconstructs the PCM. Compression, not
  access control -- no key, nothing bypassed, the same class of work as
  decoding FLAC.
- IFF 8SVX (.8svx / .iff) -> a 16-bit WAV: the Amiga voice format, optionally
  Fibonacci-delta compressed (DPCM's grandfather). Same decode-not-bypass class
  as the NCW path.
- Sun/NeXT audio (.au / .snd) -> a 16-bit WAV: the self-describing header that
  predated RIFF. mu-law and A-law (G.711) decode through a fixed table; 8- and
  16-bit big-endian linear PCM are re-framed to little-endian. Same class again.
"""

import os
import struct
import sys

from acidcat.util import outpath
from acidcat.util.paths import under

from acidcat.core.codecs import adpcm
from acidcat.core.codecs import g711
from acidcat.core.formats import bitwig as bwmod
from acidcat.core.codecs import ncw as ncwmod
from acidcat.core.formats import sf2 as sf2mod
from acidcat.core.formats import svx as svxmod
from acidcat.core.walk import au as aumod
from acidcat.core.write.midi_write import notes_to_smf
from acidcat.core.primitives.wavio import pcm_wav


def _safe_name(name, idx, ext="wav"):
    """A filesystem-safe filename for a sample (names can carry / and other
    reserved chars, and can collide/repeat), prefixed with the index."""
    keep = "".join(c if c.isalnum() or c in " -_.()" else "_" for c in name)
    return f"{idx:04d}_{keep.strip() or 'sample'}.{ext}"


def register(subparsers):
    p = subparsers.add_parser(
        "convert",
        help="Bitwig clip -> MIDI, NCW/8SVX/AU -> WAV, or SF2 -> a folder of WAVs.",
    )
    p.add_argument("input", metavar="FILE", help="Input file (.bwclip / .ncw / .sf2 / .8svx / "
                                 ".au), or a directory to batch-convert every "
                                 ".ncw within.")
    p.add_argument("-o", "--output",
                   help="Output path (single file); ignored for a directory, "
                        "where each WAV is written beside its .ncw.")
    p.add_argument("--division", type=int, default=480,
                   help="MIDI ticks per beat for .bwclip output (default 480).")
    p.add_argument("--skip-existing", action="store_true",
                   help="Batch mode: skip an .ncw whose .wav already exists.")
    p.add_argument("--force", action="store_true",
                   help="Allow writing over an existing file at a path convert "
                        "derived itself (by swapping the extension). Without "
                        "this, such a write is refused rather than silently "
                        "destroying an unrelated file of the same name.")
    p.add_argument("--to-pcm", action="store_true",
                   help="Decode a compressed/ADPCM WAV to a plain 16-bit PCM WAV "
                        "that plays anywhere (IMA/DVI ADPCM 0x0011, Microsoft "
                        "ADPCM 0x0002).")
    p.add_argument("--codec", choices=("ima", "ms"),
                   help="Force a decoder for a mistagged WAV (ima = IMA/DVI, "
                        "ms = Microsoft ADPCM), overriding the header.")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Batch mode: suppress the per-file line, keep the summary.")
    p.set_defaults(func=run)


def _batch_ncw(directory, args):
    """Convert every .ncw under `directory` to a sibling .wav. Read-only on the
    inputs; one bad file is counted and skipped, never fatal."""
    done = skipped = failed = refused = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if not name.lower().endswith(".ncw"):
                continue
            src = under(directory, root, name)
            out = os.path.splitext(src)[0] + ".wav"
            if args.skip_existing and os.path.exists(out):
                skipped += 1
                continue
            clash = outpath.refuse_clobber("convert", out, force=args.force)
            if clash:
                # counted and reported, not fatal: one pre-existing sibling
                # should not abandon the rest of the directory
                refused += 1
                print(f"  [skip] {clash}", file=sys.stderr)
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
          + (f", refused {refused:,} that would overwrite an existing file"
             if refused else "")
          + (f", {failed:,} failed" if failed else ""))
    # a refusal is a file NOT converted, so it must not report success
    return 0 if done or not (failed or refused) else 1


def _run_ncw(path, data, args):
    try:
        hdr, chans = ncwmod.decode(data)
        wav = ncwmod.to_wav(hdr, chans)
    except ncwmod.NcwError as e:
        print(f"acidcat convert: {path}: {e}", file=sys.stderr)
        return 1
    out = args.output or (os.path.splitext(path)[0] + ".wav")
    err = outpath.refuse_self_overwrite("convert", path, out)
    if not err and not args.output:
        err = outpath.refuse_clobber("convert", out, force=args.force)
    if err:
        print(err, file=sys.stderr)
        return 2
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


def _run_svx(path, data, args):
    try:
        info, samples = svxmod.decode(data)
    except svxmod.SvxError as e:
        print(f"acidcat convert: {path}: {e}", file=sys.stderr)
        return 1
    if not samples:
        print(f"acidcat convert: {path}: no sample data to render", file=sys.stderr)
        return 1
    wav = svxmod.to_wav(info, samples)
    out = args.output or (os.path.splitext(path)[0] + ".wav")
    err = outpath.refuse_self_overwrite("convert", path, out)
    if not err and not args.output:
        err = outpath.refuse_clobber("convert", out, force=args.force)
    if err:
        print(err, file=sys.stderr)
        return 2
    with open(out, "wb") as f:
        f.write(wav)
    rate = info["rate"]
    dur = info["num_samples"] / rate if rate else 0.0
    note = " [rate defaulted to 8000]" if info.get("rate_defaulted") else ""
    print(f"wrote {out}: mono {rate} Hz, {info['num_samples']:,} samples "
          f"({dur:.2f}s), {info['compression_name']}{note}")
    return 0


# encodings the convert path turns into a 16-bit PCM WAV: mu-law and A-law
# decode through g711, 8- and 16-bit linear are re-framed to 16-bit little-endian.
# The wider linear codes and the floats are named by the walker but not converted
# here yet, so convert refuses them cleanly rather than emitting noise.
_AU_TO_PCM = {1, 2, 3, 27}


def _au_pcm16(encoding, body):
    """Interleaved signed 16-bit little-endian PCM from an .au audio body, plus a
    codec label. Only the encodings in _AU_TO_PCM reach here."""
    if encoding == 1:
        return g711.decode_ulaw(body), "G.711 mu-law"
    if encoding == 27:
        return g711.decode_alaw(body), "G.711 A-law"
    if encoding == 2:                                    # 8-bit signed linear
        vals = struct.unpack(f">{len(body)}b", body)
        return struct.pack(f"<{len(body)}h", *[v << 8 for v in vals]), "8-bit linear PCM"
    # encoding 3: 16-bit linear, big-endian -> little-endian
    n = len(body) // 2
    vals = struct.unpack(f">{n}h", body[:n * 2])
    return struct.pack(f"<{n}h", *vals), "16-bit linear PCM"


def _run_au(path, data, args):
    """Sun/NeXT .au -> a 16-bit PCM WAV. mu-law/A-law decode through G.711; 8- and
    16-bit big-endian linear are re-framed to little-endian. Same decode-not-bypass
    class as the NCW and 8SVX paths."""
    hdr = aumod.parse_header(data)
    if hdr is None:
        print(f"acidcat convert: {path}: not a Sun/NeXT audio file", file=sys.stderr)
        return 1
    enc = hdr["encoding"]
    name = aumod._ENC.get(enc, (f"encoding {enc}", 0, False, False))[0]
    if enc not in _AU_TO_PCM:
        print(f"acidcat convert: {path}: {name} is not supported for conversion "
              f"yet (mu-law, A-law, 8- and 16-bit linear PCM are)", file=sys.stderr)
        return 1
    off = hdr["data_offset"]
    if off < 24 or off > len(data):
        print(f"acidcat convert: {path}: data offset {off} is outside the file",
              file=sys.stderr)
        return 1
    size = hdr["data_size"]
    body = data[off:] if size == aumod._UNKNOWN_SIZE else data[off:off + size]
    if not body:
        print(f"acidcat convert: {path}: no audio to convert", file=sys.stderr)
        return 1
    ch = hdr["channels"] or 1
    rate = hdr["sample_rate"] or 8000
    pcm, label = _au_pcm16(enc, body)
    out = args.output or (os.path.splitext(path)[0] + ".wav")
    err = outpath.refuse_self_overwrite("convert", path, out)
    if not err and not args.output:
        err = outpath.refuse_clobber("convert", out, force=args.force)
    if err:
        print(err, file=sys.stderr)
        return 2
    with open(out, "wb") as f:
        f.write(pcm_wav(pcm, rate, ch, sampwidth=2))
    frames = len(pcm) // 2 // max(ch, 1)
    dur = frames / rate if rate else 0.0
    print(f"wrote {out}: {ch}ch 16-bit {rate} Hz, {frames:,} frames "
          f"({dur:.2f}s) from {label}")
    return 0


def _pcm16_wav(frames, rate, channels):
    return pcm_wav(frames, rate or 44100, channels)


def _looks_audio(pcm):
    """A quick sanity gate for a heuristic decode: the samples should be smooth,
    not full-scale noise (the wrong decoder yields garbage)."""
    n = min(len(pcm) // 2, 8000)
    if n < 8:
        return False
    v = struct.unpack_from(f"<{n}h", pcm, 0)
    md = sum(abs(v[i + 1] - v[i]) for i in range(n - 1)) / (n - 1)
    return md < 6000


def _ms_coefs(data, fmt_off):
    """The Microsoft ADPCM (0x0002) coefficient table from the fmt chunk, or None
    if the extended fields are absent (the decoder then uses the seven standard
    pairs). Layout after WAVEFORMATEX: cbSize, samplesPerBlock, numCoef, pairs."""
    base = fmt_off + 8 + 20                        # numCoef offset
    if base + 2 > len(data):
        return None
    ncoef = struct.unpack_from("<H", data, base)[0]
    if not 0 < ncoef <= 64 or base + 2 + ncoef * 4 > len(data):
        return None
    return [struct.unpack_from("<hh", data, base + 2 + n * 4) for n in range(ncoef)]


def _run_to_pcm(path, data, args):
    """Decode a compressed/ADPCM WAV to a plain 16-bit PCM WAV. IMA/DVI ADPCM
    (0x0011) and Microsoft ADPCM (0x0002) decode by header; a mistagged file uses
    --codec ima / ms to force one."""
    i = data.find(b"fmt ")
    di = data.find(b"data")
    if i < 0 or di < 0 or i + 24 > len(data):
        print(f"acidcat convert: {path}: not a parseable WAV", file=sys.stderr)
        return 1
    tag, channels, rate, _br, block_align, _bits = struct.unpack_from("<HHIIHH", data, i + 8)
    dsize = struct.unpack_from("<I", data, di + 4)[0]
    body = data[di + 8:di + 8 + dsize] if dsize else data[di + 8:]

    label = None
    if args.codec == "ima":
        pcm, channels, label = adpcm.decode_ima_continuous(body), 1, "IMA (forced, continuous)"
    elif args.codec == "ms":
        pcm = adpcm.decode_ms_adpcm(body, block_align, channels, _ms_coefs(data, i))
        label = "Microsoft ADPCM (forced)"
    elif tag == 0x0011:
        pcm = adpcm.decode_ima(body, block_align, channels)
        label = "IMA ADPCM"
    elif tag == 0x0002:
        pcm = adpcm.decode_ms_adpcm(body, block_align, channels, _ms_coefs(data, i))
        label = "Microsoft ADPCM"
    elif tag in (0x0001, 0xFFFE):
        print(f"acidcat convert: {path}: already PCM (nothing to decode)", file=sys.stderr)
        return 1
    else:
        # unknown/mistagged tag -- try continuous IMA and keep it only if sane
        trial = adpcm.decode_ima_continuous(body)
        if _looks_audio(trial):
            pcm, channels = trial, 1
            label = f"IMA (tag 0x{tag:04x} looked wrong; decoded as IMA)"
        else:
            print(f"acidcat convert: {path}: codec 0x{tag:04x} not supported for "
                  f"--to-pcm (try --codec ima)", file=sys.stderr)
            return 1

    out = args.output or (os.path.splitext(path)[0] + "_pcm.wav")
    err = outpath.refuse_self_overwrite("convert", path, out)
    if err:
        print(err, file=sys.stderr)
        return 2
    with open(out, "wb") as f:
        f.write(_pcm16_wav(pcm, rate, channels))
    frames = len(pcm) // 2 // max(channels, 1)
    print(f"wrote {out}: {channels}ch 16-bit {rate} Hz, {frames:,} frames "
          f"({frames / rate:.2f}s) from {label}")
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
        return 2
    if (args.to_pcm or args.codec) and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return _run_to_pcm(path, data, args)
    if data[:4] == ncwmod.MAGIC:
        return _run_ncw(path, data, args)
    if svxmod.is_8svx(data):
        return _run_svx(path, data, args)
    if sf2mod.is_sf2(data):
        return _run_sf2(path, data, args)
    if aumod.parse_header(data) is not None:
        return _run_au(path, data, args)
    if data[:4] != bwmod.MAGIC:
        # 2, like every other "this verb does not model that format".
        # The list also omitted --to-pcm, which is the documented path for a
        # WAV -- so someone holding exactly that was told convert could not help
        # while the feature they wanted went unmentioned.
        print(f"acidcat convert: {path}: unsupported input (expected a Bitwig "
              f".bwclip, NCW .ncw, SF2 .sf2, IFF 8SVX, or Sun/NeXT .au). For a "
              f"WAV, --to-pcm decodes ADPCM or a mistagged codec to plain PCM.",
              file=sys.stderr)
        return 2
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
    err = outpath.refuse_self_overwrite("convert", path, out)
    if err:
        print(err, file=sys.stderr)
        return 2
    with open(out, "wb") as f:
        f.write(smf)
    print(f"wrote {out}: {len(notes)} notes, {bpm:g} bpm")
    return 0
