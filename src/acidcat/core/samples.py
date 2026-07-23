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


def _s3m_frames(raw, bits16, stereo):
    """S3M PCM (unsigned; 8/16-bit; stereo stored as L-block then R-block) ->
    signed 16-bit little-endian, interleaved for stereo."""
    def to16(block):
        if bits16:
            n = len(block) // 2
            return [struct.unpack_from("<H", block, k * 2)[0] - 32768 for k in range(n)]
        return [(b - 128) * 256 for b in block]
    if stereo:
        half = len(raw) // 2
        left, right = to16(raw[:half]), to16(raw[half:])
        out = bytearray()
        for lo, ro in zip(left, right):
            out += struct.pack("<hh", lo, ro)
        return bytes(out)
    return b"".join(struct.pack("<h", v) for v in to16(raw))


def _s3m_samples(data):
    s3 = tkmod.parse_s3m(data)
    for i, s in enumerate(s3["samples"], 1):
        if not s.get("valid") or not s.get("is_pcm") or s.get("packing") == 1:
            continue                                     # header-only / adlib / ADPCM
        off, blen = s.get("pcm_off"), s.get("byte_len")
        if not off or not blen or off + blen > len(data):
            continue
        rate = s.get("c2spd") or _TRACKER_RATE
        yield {"name": s["name"] or s.get("dos_name") or f"sample{i:02d}",
               "wav": _wav(_s3m_frames(data[off:off + blen], s["bits16"], s["stereo"]),
                           rate, channels=2 if s["stereo"] else 1),
               "note": f"{blen:,} B {'16' if s['bits16'] else '8'}-bit "
                       f"{'stereo' if s['stereo'] else 'mono'} @ {rate} Hz"}


class _ITBits:
    """LSB-first bit reader over the IT sample bitstream."""
    def __init__(self, data):
        self.d, self.pos, self.buf, self.n = data, 0, 0, 0

    def read(self, k):
        while self.n < k:
            b = self.d[self.pos] if self.pos < len(self.d) else 0
            self.pos += 1
            self.buf |= b << self.n
            self.n += 8
        v = self.buf & ((1 << k) - 1)
        self.buf >>= k
        self.n -= k
        return v


def _it_decompress(data, off, count, bits16, it215):
    """Decompress an IT214/215 sample. Returns signed PCM (16-bit LE if bits16,
    else 8-bit signed). Mirrors Schism Tracker's itsex.c; verified against real
    IT modules (exact lengths, smooth output) for both delta variants."""
    out = bytearray()
    src = off
    top = 16 if bits16 else 8
    startw = 17 if bits16 else 9
    blockmax = 0x4000 if bits16 else 0x8000
    span = 16 if bits16 else 8
    done = 0
    while done < count:
        blocklen = min(blockmax, count - done)
        if src + 2 > len(data):
            break
        clen = struct.unpack_from("<H", data, src)[0]
        src += 2
        br = _ITBits(data[src:src + clen])
        src += clen
        width, d1, d2, pos = startw, 0, 0, 0
        while pos < blocklen:
            v = br.read(width)
            if width < 7:
                if v == (1 << (width - 1)):
                    nw = br.read(4 if bits16 else 3) + 1
                    width = nw if nw < width else nw + 1
                    continue
            elif width < startw:
                mask = 0xFFFF if bits16 else 0xFF
                border = (mask >> (startw - width)) - span // 2
                if border < v <= border + span:
                    v -= border
                    width = v if v < width else v + 1
                    continue
            elif width == startw:
                if v & (0x10000 if bits16 else 0x100):
                    width = (v + 1) & 0xFF
                    continue
            else:
                return bytes(out)                        # corrupt width
            # sign-extend v (width bits) to `top` bits
            if width < top:
                sh = top - width
                vv = (v << sh) & (0xFFFF if bits16 else 0xFF)
                sv = (vv - (1 << top) if vv >= (1 << (top - 1)) else vv) >> sh
            else:
                sv = v - (1 << top) if v >= (1 << (top - 1)) else v
            d1 += sv
            d2 += d1
            sample = d2 if it215 else d1
            if bits16:
                out += struct.pack("<h", ((sample + 32768) & 0xFFFF) - 32768)
            else:
                out.append(sample & 0xFF)
            pos += 1
        done += blocklen
    return bytes(out)


def _it_samples(data):
    it = tkmod.parse_it(data)
    it215 = (struct.unpack_from("<H", data, 0x2a)[0] >= 0x215) if len(data) > 0x2c else False
    for i, s in enumerate(it["samples"], 1):
        if not s.get("valid") or not s.get("has_sample") or not s.get("length"):
            continue
        bits16, rate = s["bits16"], s.get("c5_speed") or _TRACKER_RATE
        off, length = s.get("data_off"), s["length"]
        if not off:
            continue
        if s.get("compressed"):
            pcm = _it_decompress(data, off, length, bits16, it215)
        else:
            pcm = data[off:off + length * (2 if bits16 else 1)]
        if bits16:
            frames = pcm[:(len(pcm) // 2) * 2]           # signed 16-bit LE already
        else:
            frames = b"".join(struct.pack("<h", (b - 256 if b > 127 else b) * 256)
                              for b in pcm)
        yield {"name": s["name"] or s.get("dos_name") or f"sample{i:02d}",
               "wav": _wav(frames, rate),
               "note": f"{length:,} {'16' if bits16 else '8'}-bit "
                       f"{'IT-compressed' if s.get('compressed') else 'PCM'} @ {rate} Hz"}


def _gf1_frames(raw, bits16, unsigned):
    """GUS GF1 PCM (8/16-bit, signed or unsigned) -> signed 16-bit little-endian."""
    if bits16:
        n = len(raw) // 2
        if unsigned:
            return b"".join(struct.pack("<h", struct.unpack_from("<H", raw, k * 2)[0] - 32768)
                            for k in range(n))
        return raw[:n * 2]                               # already signed 16-bit LE
    if unsigned:
        return b"".join(struct.pack("<h", (b - 128) * 256) for b in raw)
    return b"".join(struct.pack("<h", (b - 256 if b > 127 else b) * 256) for b in raw)


def _gf1pat_samples(data):
    from acidcat.core.walk.gf1pat import parse_gf1
    info = parse_gf1(data)
    for i, s in enumerate(info["samples"], 1):
        off, sz = s["pcm_off"], s["data_size"]
        if not sz or off + sz > len(data):
            continue
        yield {"name": s["name"] or f"sample{i:02d}",
               "wav": _wav(_gf1_frames(data[off:off + sz], s["bits16"], s["unsigned"]),
                           s["rate"] or _TRACKER_RATE),
               "note": f"{sz:,} B {'16' if s['bits16'] else '8'}-bit @ {s['rate']} Hz"}


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


def _emu_samples(filepath):
    """E-mu Emulator 4 / EOS (.e4b): each E3S1 chunk is a 94-byte header then
    16-bit signed little-endian mono PCM. Reuse the walker to find the chunks
    (E5B/.exb keep their PCM in sibling .ebl files -- not handled here)."""
    from acidcat.core.walk.emu import inspect_emu, _SAMP_HDR
    chunks, _w = inspect_emu(filepath, False)
    with open(filepath, "rb") as f:
        data = f.read()
    n = 0
    for c in chunks:
        if not c["id"].startswith("E3S1"):
            continue
        fv = {fld.get("name"): fld for fld in c.get("fields", [])}
        rate = fv.get("sample_rate", {}).get("raw") or _TRACKER_RATE
        b0 = c["offset"] + 8 + _SAMP_HDR                 # skip IFF tag+size, then header
        b1 = c["offset"] + 8 + c["size"]
        if b1 - b0 < 2 or b1 > len(data):
            continue
        n += 1
        name = fv.get("name", {}).get("value")
        yield {"name": name if isinstance(name, str) and name else f"sample{n:02d}",
               "wav": _wav(data[b0:b1], rate),
               "note": f"{(b1 - b0) // 2:,} frames 16-bit @ {rate} Hz"}


def _snd_samples(filepath):
    """Akai MPC2000 .snd: a 38/42-byte header then 16-bit signed LE PCM at 44100
    Hz. Stereo is stored non-interleaved (L block then R block) and is interleaved
    on output. Reuse the walker to resolve the header size + geometry."""
    from acidcat.core.walk.mpc import inspect_snd
    chunks, _w = inspect_snd(filepath)
    pcm = next((c for c in chunks if c["id"] == "pcm"), None)
    if pcm is None:
        return
    snd = next((c for c in chunks if c["id"] == "SND"), None)
    fv = {fld.get("name"): fld for fld in (snd["fields"] if snd else [])}
    name = fv.get("name", {}).get("value") or "sound"
    channels = fv.get("channels", {}).get("value") or 1
    with open(filepath, "rb") as f:
        f.seek(pcm["offset"])
        raw = f.read(pcm["size"])
    if channels == 2:
        half = (len(raw) // 4) * 2                        # even byte split point
        left, right = raw[:half], raw[half:half * 2]
        frames = bytearray()
        for k in range(0, min(len(left), len(right)), 2):
            frames += left[k:k + 2] + right[k:k + 2]      # interleave L/R
        wav = _wav(bytes(frames), 44100, channels=2)
    else:
        wav = _wav(raw, 44100, channels=1)
    yield {"name": name, "wav": wav,
           "note": f"{pcm['size'] // 2 // channels:,} frames "
                   f"{'stereo' if channels == 2 else 'mono'} @ 44100 Hz"}


def _emu5_samples(filepath):
    """E-mu Emulator X / Proteus X (.ebl sample library, .exb bank): each E5S1
    chunk is a fixed 0xb8-byte header (inline UTF-16LE name, sample rate at +0x6a)
    then 16-bit signed little-endian mono PCM. .exb banks hold only presets +
    links, so extraction lands the samples from the .ebl libraries."""
    from acidcat.core.walk.emu import inspect_emu
    _E5_HDR, _RATE_OFF = 0xb8, 0x6a
    chunks, _w = inspect_emu(filepath, False)
    with open(filepath, "rb") as f:
        data = f.read()
    n = 0
    for c in chunks:
        if not c["id"].startswith("E5S1"):
            continue
        body = data[c["offset"] + 8:c["offset"] + 8 + c["size"]]
        if len(body) < _E5_HDR + 2:
            continue
        rate = struct.unpack_from("<I", body, _RATE_OFF)[0] or _TRACKER_RATE
        j = 6
        while j + 1 < min(len(body), 0x40) and body[j:j + 2] != b"\x00\x00":
            j += 2
        name = body[6:j].decode("utf-16-le", "replace").strip()
        n += 1
        yield {"name": name or f"sample{n:02d}",
               "wav": _wav(body[_E5_HDR:], rate),
               "note": f"{(len(body) - _E5_HDR) // 2:,} frames 16-bit @ {rate} Hz"}


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
    "s3m": _s3m_samples, "gf1pat": _gf1pat_samples,
    "8svx": _svx_samples, "ncw": _ncw_samples, "sf2": _sf2_samples,
}
# formats whose extractor reads the path itself (walk/stream), not a bytes buffer
_PATH_EXTRACTORS = {"multisample": _multisample_samples, "krz": _krz_samples,
                    "e4b": _emu_samples, "e5b": _emu5_samples, "snd": _snd_samples}

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
