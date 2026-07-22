"""Unified sample extraction -- pull the embedded audio out of any bank/module
acidcat can walk, as playable WAVs.

`inspect` shows you the samples are in there; this gets them out. One interface
over many formats: `iter_samples(path)` yields records with ready-to-write WAV
bytes, dispatching to per-format extraction. Reuses the existing decoders where
they exist (NCW's DPCM, 8SVX's Fibonacci-delta, SF2's PCM/Ogg) and adds raw and
delta PCM for tracker modules.

Verifiable-now coverage: MOD (raw 8-bit), XM (8/16-bit delta), IT (PCM samples),
8SVX (Fibonacci or raw), NCW (Kontakt DPCM), SF2 (PCM; SF3 = Ogg verbatim).
Formats acidcat walks but cannot yet extract -- Kurzweil KRZ, E-mu E4B/E5B, Akai,
Bitwig multisample, RX2, BFD .bfdlac -- need specimens and/or codec work; see the
`extract` command's roadmap. Read-only on the source.
"""

import io
import os
import struct
import wave
import zipfile

from acidcat.core import ncw as ncwmod
from acidcat.core import sf2 as sf2mod
from acidcat.core import svx as svxmod
from acidcat.core import tracker as tkmod
from acidcat.core.sniff import sniff
from acidcat.core.walk.base import Unsupported

_TRACKER_RATE = 8363             # the conventional Amiga C-3 rate; modules pitch by playback


class SampleError(Exception):
    """Raised when a format has no extractable samples."""


def _wav(frames, rate, channels=1, sampwidth=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate or _TRACKER_RATE)
        w.writeframes(frames)
    return buf.getvalue()


def _s8_to_wav(raw, rate):
    """Signed 8-bit PCM bytes -> 16-bit WAV (scaled up for universal playback)."""
    frames = b"".join(struct.pack("<h", (b - 256 if b > 127 else b) * 256) for b in raw)
    return _wav(frames, rate)


def _undelta8(raw):
    """XM 8-bit delta -> signed 8-bit PCM (a running accumulator)."""
    out = bytearray(len(raw))
    old = 0
    for i, b in enumerate(raw):
        old = (old + b) & 0xFF
        out[i] = old
    return bytes(out)                                    # unsigned bytes, but wrapped signed


def _undelta16(raw):
    """XM 16-bit delta -> signed 16-bit PCM (little-endian)."""
    n = len(raw) // 2
    out = bytearray(n * 2)
    old = 0
    for i in range(n):
        d = struct.unpack_from("<H", raw, i * 2)[0]
        old = (old + d) & 0xFFFF
        struct.pack_into("<H", out, i * 2, old)
    return bytes(out)


# ---- per-format extractors: each yields {name, wav, note} ------------------

def _mod_samples(data):
    m = tkmod.parse_mod(data)
    for i, s in enumerate(m["samples"], 1):
        if not s["length"] or s["offset"] is None:
            continue
        raw = data[s["offset"]:s["offset"] + s["length"]]
        yield {"name": s["name"] or f"sample{i:02d}",
               "wav": _s8_to_wav(raw, _TRACKER_RATE),
               "note": f"{s['length']:,} B 8-bit"}


def _xm_samples(data):
    x = tkmod.parse_xm(data)
    n = 0
    for inst in x["instruments"]:
        for s in inst["samples"]:
            if not s["length"] or s.get("offset") is None:
                continue
            n += 1
            raw = data[s["offset"]:s["offset"] + s["length"]]
            if s["bits16"]:
                pcm = _undelta16(raw)                     # -> signed 16-bit LE
                frames = pcm
            else:
                dec = _undelta8(raw)                      # -> wrapped signed 8-bit
                frames = b"".join(struct.pack("<h", (b - 256 if b > 127 else b) * 256)
                                  for b in dec)
            yield {"name": s["name"] or inst["name"] or f"sample{n:02d}",
                   "wav": _wav(frames, _TRACKER_RATE),
                   "note": f"{s['length']:,} B {'16' if s['bits16'] else '8'}-bit delta"}


def _it_samples(data):
    it = tkmod.parse_it(data)
    skipped = 0
    for i, s in enumerate(it["samples"], 1):
        if not s.get("valid") or not s.get("is_pcm", True):
            continue
        if s.get("compressed"):
            skipped += 1
            continue                                     # IT compression not decoded yet
        off, blen = s.get("pcm_off"), s.get("byte_len")
        if not off or not blen:
            continue
        raw = data[off:off + blen]
        if s["bits16"]:
            frames = raw                                 # signed 16-bit LE PCM
        else:
            frames = b"".join(struct.pack("<h", (b - 256 if b > 127 else b) * 256)
                              for b in raw)
        yield {"name": s["name"] or f"sample{i:02d}",
               "wav": _wav(frames, s.get("rate") or _TRACKER_RATE),
               "note": f"{blen:,} B {'16' if s['bits16'] else '8'}-bit PCM"}
    if skipped:
        yield {"name": None, "wav": None,
               "note": f"{skipped} IT-compressed sample(s) skipped (codec not decoded)"}


def _svx_samples(data):
    info, samples = svxmod.decode(data)
    yield {"name": "voice", "wav": svxmod.to_wav(info, samples),
           "note": f"{info['num_samples']:,} samples {info['compression_name']}"}


def _ncw_samples(data):
    hdr, chans = ncwmod.decode(data)
    yield {"name": "wave", "wav": ncwmod.to_wav(hdr, chans),
           "note": f"{hdr['channels']}ch {hdr['bits']}-bit {hdr['sample_rate']} Hz"}


def _sf2_samples(data):
    info = sf2mod.parse_sf2(data)
    for i, s in enumerate(info["samples"]):
        if s.get("compressed"):
            blob = sf2mod.sample_bytes(data, s)          # SF3: Ogg Vorbis, verbatim
            yield {"name": s["name"], "wav": blob, "note": "Ogg (SF3)", "ext": "ogg"}
        else:
            blob = sf2mod.sample_wav(data, info["smpl_offset"], s)
            yield {"name": s["name"], "wav": blob, "note": "PCM"}


def _be16_to_wav(raw, rate):
    """Raw 16-bit big-endian PCM -> a little-endian 16-bit WAV."""
    import array
    a = array.array("h")
    a.frombytes(raw[:len(raw) & ~1])
    if array.array("h", b"\x01\x00")[0] == 1:            # host is little-endian
        a.byteswap()                                     # BE bytes -> correct LE values
    return _wav(a.tobytes(), rate)


def _krz_samples(filepath):
    """Kurzweil KRZ: each Sample object addresses a [start, end) word range in the
    one contiguous 16-bit big-endian PCM region (at pcm_offset). Reuse the walker
    to locate the region and the sample objects, then slice and byteswap."""
    from acidcat.core.walk.krz import inspect_krz
    chunks, _warns = inspect_krz(filepath)
    with open(filepath, "rb") as f:
        data = f.read()
    pcm_off = None
    for c in chunks:
        for fld in c.get("fields", []):
            if fld.get("name") == "pcm_offset":
                pcm_off = fld.get("raw", fld.get("value"))
    if pcm_off is None:
        return
    n = 0
    for c in chunks:
        fv = {fld.get("name"): fld for fld in c.get("fields", [])}
        if not ({"sample_start", "sample_end", "sample_period"} <= set(fv)):
            continue
        start = fv["sample_start"].get("raw", fv["sample_start"]["value"])
        end = fv["sample_end"].get("raw", fv["sample_end"]["value"])
        period = fv["sample_period"].get("raw", fv["sample_period"]["value"])
        rate = round(1e9 / period) if period else _TRACKER_RATE
        b0, b1 = pcm_off + start * 2, pcm_off + end * 2
        if not (0 <= b0 < b1 <= len(data)):
            continue
        n += 1
        name = fv["sample_start"] and None
        nm = next((f["value"] for f in c.get("fields", []) if f.get("name") == "name"), None)
        yield {"name": (nm if isinstance(nm, str) and nm != "(unnamed)" else None)
                       or f"sample{n:02d}",
               "wav": _be16_to_wav(data[b0:b1], rate),
               "note": f"{(b1 - b0) // 2:,} samples 16-bit @ {rate} Hz"}


def _multisample_samples(filepath):
    """A Bitwig .multisample is a zip of WAVs (+ multisample.xml). Stream each WAV
    member out verbatim -- read from the path so a multi-hundred-MB pack is not
    loaded into memory at once."""
    with zipfile.ZipFile(filepath) as z:
        for n in z.namelist():
            if n.lower().endswith(".wav"):
                yield {"name": os.path.splitext(os.path.basename(n))[0],
                       "wav": z.read(n), "note": f"{z.getinfo(n).file_size:,} B"}


_EXTRACTORS = {
    "mod": _mod_samples, "xm": _xm_samples, "it": _it_samples,
    "8svx": _svx_samples, "ncw": _ncw_samples, "sf2": _sf2_samples,
}
# formats whose extractor reads the path itself (walk/stream), not a bytes buffer
_PATH_EXTRACTORS = {"multisample": _multisample_samples, "krz": _krz_samples}

EXTRACTABLE = frozenset(_EXTRACTORS) | frozenset(_PATH_EXTRACTORS)


def iter_samples(filepath, fmt=None):
    """Yield {name, wav (bytes), note, ext?} for each embedded sample. Raises
    SampleError if the sniffed format has no extractor. Never modifies the file."""
    fmt = fmt or sniff(filepath)
    try:
        if fmt in _PATH_EXTRACTORS:
            yield from _PATH_EXTRACTORS[fmt](filepath)   # streams from the path
            return
        fn = _EXTRACTORS.get(fmt)
        if fn is None:
            raise SampleError(f"no sample extractor for {fmt or 'unrecognized'} "
                              f"(extractable: {', '.join(sorted(EXTRACTABLE))})")
        with open(filepath, "rb") as f:
            data = f.read()
        yield from fn(data)
    except Unsupported as e:
        raise SampleError(str(e))
