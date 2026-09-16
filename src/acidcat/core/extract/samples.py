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

import os
import struct
import zipfile

from acidcat.core.codecs import ncw as ncwmod
# imported for their exception types only (the decoders themselves stay lazily
# imported at their call sites): a codec refusing malformed input is the same
# kind of failure as a parser refusing it, and must become SampleError, not a
# traceback out of `acidcat extract`
from acidcat.core.codecs.adx import AdxError
from acidcat.core.codecs.brstm import BrstmError
from acidcat.core.codecs.hps import HpsError
from acidcat.core.codecs.vag import VagError
from acidcat.core.formats import sf2 as sf2mod
from acidcat.core.formats import svx as svxmod
from acidcat.core.formats import tracker as tkmod
from acidcat.core.primitives.wavio import pcm_wav
from acidcat.core.infra.sniff import sniff
from acidcat.core.walk.base import Unsupported

_TRACKER_RATE = 8363             # the conventional Amiga C-3 rate; modules pitch by playback


class SampleError(Exception):
    """Raised when a format has no extractable samples."""


def _wav(frames, rate, channels=1, sampwidth=2):
    return pcm_wav(frames, rate or _TRACKER_RATE, channels, sampwidth)


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

def _bounded(data, off, want):
    """Slice `want` bytes at `off`, and say how many were not there.

    A sample header declaring more data than the file holds used to slice short
    and then be described by its DECLARED size: a MOD claiming a 4,096-byte
    sample starting at EOF listed as `4,096 B 8-bit`, counted toward "extracted
    4 sample(s)", exit 0 -- and landed on disk as a 44-byte WAV header with no
    audio in it. The bank's own truncation is a finding, not a silent success.

    Returns (raw, short_by).
    """
    if off is None or off < 0 or off >= len(data):
        return b"", want
    raw = data[off:off + want]
    return raw, want - len(raw)


def _truncated_note(name, want, have, total):
    """The informational record for a sample the file does not actually hold.
    wav=None keeps it out of the extracted count and routes it to `notes`."""
    if have:
        return None
    return {"name": name, "wav": None,
            "note": f"{name}: declared {want:,} B but the file holds none of "
                    f"it (file is {total:,} B) -- not extracted"}


def _mod_samples(data):
    m = tkmod.parse_mod(data)
    for i, s in enumerate(m["samples"], 1):
        if not s["length"] or s["offset"] is None:
            continue
        name = s["name"] or f"sample{i:02d}"
        raw, short = _bounded(data, s["offset"], s["length"])
        skip = _truncated_note(name, s["length"], len(raw), len(data))
        if skip:
            yield skip
            continue
        note = f"{len(raw):,} B 8-bit"
        if short:
            note += f" (declared {s['length']:,} B; {short:,} B past EOF)"
        yield {"name": name, "wav": _s8_to_wav(raw, _TRACKER_RATE), "note": note}


def _xm_samples(data):
    x = tkmod.parse_xm(data)
    n = 0
    for inst in x["instruments"]:
        for s in inst["samples"]:
            if not s["length"] or s.get("offset") is None:
                continue
            n += 1
            name = s["name"] or inst["name"] or f"sample{n:02d}"
            raw, short = _bounded(data, s["offset"], s["length"])
            skip = _truncated_note(name, s["length"], len(raw), len(data))
            if skip:
                yield skip
                continue
            if s["bits16"]:
                pcm = _undelta16(raw)                     # -> signed 16-bit LE
                frames = pcm
            else:
                dec = _undelta8(raw)                      # -> wrapped signed 8-bit
                frames = b"".join(struct.pack("<h", (b - 256 if b > 127 else b) * 256)
                                  for b in dec)
            note = f"{len(raw):,} B {'16' if s['bits16'] else '8'}-bit delta"
            if short:
                note += f" (declared {s['length']:,} B; {short:,} B past EOF)"
            yield {"name": name, "wav": _wav(frames, _TRACKER_RATE), "note": note}


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
        name = s["name"] or s.get("dos_name") or f"sample{i:02d}"
        want = length * (2 if bits16 else 1)
        short = 0
        if s.get("compressed"):
            pcm = _it_decompress(data, off, length, bits16, it215)
        else:
            pcm, short = _bounded(data, off, want)
        skip = _truncated_note(name, want, len(pcm), len(data))
        if skip:
            yield skip
            continue
        if bits16:
            frames = pcm[:(len(pcm) // 2) * 2]           # signed 16-bit LE already
        else:
            frames = b"".join(struct.pack("<h", (b - 256 if b > 127 else b) * 256)
                              for b in pcm)
        note = (f"{len(pcm):,} B {'16' if bits16 else '8'}-bit "
                f"{'IT-compressed' if s.get('compressed') else 'PCM'} @ {rate} Hz")
        if short:
            note += f" (declared {want:,} B; {short:,} B past EOF)"
        yield {"name": name, "wav": _wav(frames, rate), "note": note}


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


def _wav_note(ch, rate):
    return f"{'stereo' if ch == 2 else 'mono'} @ {rate} Hz"


def _cdxa_named(filepath):
    """ISO 9660-aware extraction: named XA tracks from .STR/.XA movies and SPU
    sound banks from .VB/.BD, standalone .VAG, or any file whose content is a
    strong SPU-ADPCM match. Returns True if it yielded anything."""
    from acidcat.core.containers import iso9660
    from acidcat.core.codecs import cdxa, vag
    got = False
    files = list(iso9660.walk(filepath))
    by_upper = {e["path"].upper(): e for e in files}     # sibling-header lookup
    for ent in files:                                    # named soundtrack (.STR/.XA)
        up = ent["path"].upper()
        if not (up.endswith(".STR") or up.endswith(".XA")):
            continue
        r = cdxa.decode_range(filepath, ent["lba"], (ent["size"] + 2047) // 2048)
        if not r:
            continue
        pcm, info = r
        base = ent["path"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        got = True
        yield {"name": base, "wav": _wav(pcm, info["rate"], channels=info["channels"]),
               "note": f"{info['frames'] / info['rate']:.0f}s "
                       f"{_wav_note(info['channels'], info['rate'])} XA"}
    for ent in files:                                    # SPU sound/instrument banks
        up = ent["path"].upper()
        base = ent["path"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if up.endswith((".STR", ".XA")):
            continue                                     # already emitted as a track
        if up.endswith(".VAG"):
            try:
                vinfo = vag.parse_vag(iso9660.read_file(filepath, ent))
            except vag.VagError:
                continue
            got = True
            yield {"name": vinfo["name"] or base,
                   "wav": _wav(vag.decode_spu(vinfo["data"]), vinfo["rate"]),
                   "note": f"SPU-ADPCM VAG @ {vinfo['rate']} Hz"}
            continue
        # .VB / .BD are SPU banks by name; anything else is taken only on a strong
        # content match, so a bank with an odd extension is still found and a data
        # file is not mistaken for one.
        bank = up.endswith((".VB", ".BD"))
        if not bank:
            head = iso9660.read_file(filepath, ent, limit=16 * 1024)
            bank = vag.looks_like_spu(head) >= 0.9
        if not bank:
            continue
        raw = iso9660.read_file(filepath, ent)
        sizes = _vab_sizes(filepath, ent, by_upper)     # from the sibling .VH/.HD
        if sizes:                                       # split into named samples
            for i, sdata in vag.split_vb(raw, sizes):
                pcm = vag.decode_spu(sdata)
                if len(pcm) < 2:
                    continue
                got = True
                yield {"name": f"{base}_{i:02d}", "wav": _wav(pcm, 22050),
                       "note": f"SPU sample {len(pcm) // 2:,} frames @ 22050 Hz (nominal)"}
        else:                                           # no header: one whole-bank WAV
            pcm = vag.decode_spu(raw, stop_on_end=False)
            if len(pcm) >= 2:
                got = True
                yield {"name": base + "_bank", "wav": _wav(pcm, 22050),
                       "note": f"SPU-ADPCM bank {len(pcm) // 2:,} frames @ 22050 Hz (nominal)"}
    return got


_VAB_HDR_EXT = {".VB": ".VH", ".BD": ".HD"}


def _vab_sizes(filepath, ent, by_upper):
    """The per-sample byte sizes from a bank's sibling VAB header (.VH/.HD), or
    None if there is no matching header or it does not parse."""
    from acidcat.core.containers import iso9660
    from acidcat.core.codecs import vag
    up = ent["path"].upper()
    dot = up.rfind(".")
    hext = _VAB_HDR_EXT.get(up[dot:]) if dot >= 0 else None
    if not hext:
        return None
    hdr = by_upper.get(up[:dot] + hext)
    if hdr is None:
        return None
    try:
        return vag.parse_vab(iso9660.read_file(filepath, hdr))["sizes"]
    except (vag.VagError, OSError):
        return None


def _cdxa_samples(filepath):
    """A raw CD sector image (PlayStation and CD-XA kin). With an ISO 9660
    filesystem, yields named .STR tracks and .VB/.VAG sound banks. Without one,
    falls back to decoding the raw XA channel and splitting it on silence.
    Reads via seeks, so a multi-hundred-MB disc is never slurped."""
    from acidcat.core.codecs import cdxa
    info0 = cdxa.detect_cd_image(filepath)
    if not info0 or not info0.get("xa"):
        return
    named = yield from _cdxa_named(filepath)
    if named:
        return
    streams = cdxa.xa_streams(filepath)
    for key in sorted(streams, key=lambda k: -len(streams[k]["sectors"])):
        cod = cdxa.coding_of(streams[key]["coding"])
        if cod["bits"] != 4:
            yield {"name": f"stream_f{key[0]}c{key[1]}", "wav": None,
                   "note": "8-bit XA-ADPCM not decoded yet"}
            continue
        pcm, info = cdxa.decode_stream(filepath, key)
        ch, rate = info["channels"], info["rate"]
        songs = cdxa.split_gaps(pcm, info)
        if len(songs) <= 1:
            yield {"name": f"soundtrack_f{key[0]}c{key[1]}",
                   "wav": _wav(pcm, rate, channels=ch),
                   "note": f"{info['frames'] / rate:.0f}s {_wav_note(ch, rate)} XA-ADPCM"}
            continue
        bpf = 2 * ch
        for i, (a, b) in enumerate(songs, 1):
            yield {"name": f"track_{i:02d}",
                   "wav": _wav(pcm[a * bpf:b * bpf], rate, channels=ch),
                   "note": f"{(b - a) / rate:.0f}s {_wav_note(ch, rate)}"}


def _cue_samples(cue_path):
    """A .cue sheet: extract each CD-DA (Red Book) audio track to a WAV -- raw
    16-bit little-endian stereo PCM at 44100 Hz, straight off the audio track."""
    from acidcat.core.containers import cue as cuemod
    for t in cuemod.audio_tracks(cue_path):
        with open(t["file"], "rb") as f:
            f.seek(t["start"])
            raw = f.read(t["size"])
        dur = len(raw) / (cuemod.CDDA_RATE * 4)
        yield {"name": f"track_{t['num']:02d}",
               "wav": _wav(raw, cuemod.CDDA_RATE, channels=2),
               "note": f"{dur:.0f}s CD-DA stereo @ 44100 Hz"}


def _hps_samples(data):
    """HAL PCM Stream (.hps): decode the DSP-ADPCM stream to one WAV."""
    from acidcat.core.codecs import hps
    pcm, info = hps.decode(data)
    yield {"name": "stream", "wav": _wav(pcm, info["rate"], channels=info["channels"]),
           "note": f"{info['frames'] / info['rate']:.0f}s "
                   f"{'stereo' if info['channels'] == 2 else 'mono'} @ {info['rate']} Hz "
                   f"DSP-ADPCM"}


def _adx_samples(data):
    """CRI ADX: decode to one WAV."""
    from acidcat.core.codecs import adx
    pcm, info = adx.decode(data)
    yield {"name": "stream", "wav": _wav(pcm, info["rate"], channels=info["channels"]),
           "note": f"{info['frames'] / info['rate']:.0f}s "
                   f"{'stereo' if info['channels'] == 2 else 'mono'} @ {info['rate']} Hz ADX"}


def _gcm_samples(filepath):
    """A GameCube disc image: walk the filesystem and decode each audio file --
    HAL .hps streams and CRI .adx. Reads via seeks, never slurps the disc."""
    from acidcat.core.containers import gcm
    from acidcat.core.codecs import hps, adx, dtk
    for ent in gcm.walk(filepath):
        p = ent["path"].lower()
        try:
            if p.endswith(".hps"):
                pcm, info = hps.decode(gcm.read_file(filepath, ent))
                kind = "DSP"
            elif p.endswith(".adx"):
                pcm, info = adx.decode(gcm.read_file(filepath, ent))
                kind = "ADX"
            elif p.endswith(".adp"):
                pcm, info = dtk.decode(gcm.read_file(filepath, ent))
                kind = "DTK"
            else:
                continue
        except Exception:
            continue
        base = ent["path"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        yield {"name": base,
               "wav": _wav(pcm, info["rate"], channels=info["channels"]),
               "note": f"{info['frames'] / info['rate']:.0f}s "
                       f"{'stereo' if info['channels'] == 2 else 'mono'} @ {info['rate']} Hz {kind}"}


def _brstm_samples(data):
    """BRSTM (RSTM): decode the DSP-ADPCM stream to one WAV."""
    from acidcat.core.codecs import brstm
    pcm, info = brstm.decode(data)
    yield {"name": "stream", "wav": _wav(pcm, info["rate"], channels=info["channels"]),
           "note": f"{info['frames'] / info['rate']:.0f}s "
                   f"{'stereo' if info['channels'] == 2 else 'mono'} @ {info['rate']} Hz "
                   f"DSP-ADPCM"}


def _wiidisc_samples(filepath):
    """A Wii disc image: decrypt the data partition, walk it, and decode each
    BRSTM stream. Needs the crypto extra (pip install acidcat[crypto]); decrypts
    clusters on demand, never slurping the disc."""
    from acidcat.core.containers import wiidisc
    from acidcat.core.codecs import brstm
    try:
        disc = wiidisc.WiiDisc(filepath)
    except wiidisc.WiiError as e:
        raise SampleError(str(e))
    try:
        for ent in disc.files():
            if not ent["path"].lower().endswith(".brstm"):
                continue
            try:
                pcm, info = brstm.decode(disc.read(ent))
            except Exception:
                continue
            base = ent["path"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
            yield {"name": base,
                   "wav": _wav(pcm, info["rate"], channels=info["channels"]),
                   "note": f"{info['frames'] / info['rate']:.0f}s "
                           f"{'stereo' if info['channels'] == 2 else 'mono'} "
                           f"@ {info['rate']} Hz DSP-ADPCM"}
    finally:
        disc.close()


def _n64rom_samples(filepath):
    """An N64 ROM: recover VADPCM samples container-agnostically (core/n64rip).
    N64 games each wrap VADPCM differently, so this finds the codebooks + audiotable
    by structure and pairs them by coherence rather than parsing a bank. Rate is
    approximate (22050; the real rate lives in the game-specific bank we skip)."""
    from acidcat.core.extract import n64rip
    with open(filepath, "rb") as f:
        data = f.read()
    for s in n64rip.recover(data):
        yield {"name": f"vadpcm_{s['offset']:07X}",
               "wav": _wav(s["pcm"], 22050, channels=1),
               "note": f"{s['frames'] / 22050:.2f}s VADPCM recovered @0x{s['offset']:X} "
                       f"(coherence {s['coherence']}, peak {s['peak']}; ~22050 Hz)"}


def _snesrom_samples(filepath):
    """A SNES ROM: recover BRR samples container-agnostically (core/snesrip). BRR
    carries no codebook, so an end-flag-terminated run of valid blocks that decodes
    to coherent audio is a sample -- found without parsing the game's sample table.
    Rate is the S-DSP native 32000 Hz (per-note pitch lives in the ARAM directory)."""
    from acidcat.core.extract import snesrip
    with open(filepath, "rb") as f:
        data = f.read()
    for s in snesrip.recover(data):
        yield {"name": f"brr_{s['offset']:06X}",
               "wav": _wav(s["pcm"], 32000, channels=1),
               "note": f"{len(s['pcm']) // 2 / 32000:.2f}s BRR recovered @0x{s['offset']:X} "
                       f"({s['blocks']} blocks, coherence {s['coherence']}, rms {s['rms']}, "
                       f"peak {s['peak']}; 32000 Hz)"}


def _vag_samples(data):
    """PS1 .VAG: a 48-byte header then SPU-ADPCM. One mono sample per file."""
    from acidcat.core.codecs import vag as vagmod
    info = vagmod.parse_vag(data)
    pcm = vagmod.decode_spu(info["data"])
    yield {"name": info["name"] or "sample", "wav": _wav(pcm, info["rate"]),
           "note": f"SPU-ADPCM {len(pcm) // 2:,} frames @ {info['rate']} Hz"}


# The X68000's MSM6258 is clocked for 15.6 kHz by default and an MDX song
# may ask the player for 3.9 to 15.6; the bank itself carries no rate, so
# the default is used and the note says so.
_PDX_RATE = 15625


def _pdx_samples(data):
    from acidcat.core.formats import pdx as pdxmod
    from acidcat.core.codecs.adpcm import decode_oki
    t = pdxmod.parse_table(data, len(data))
    if not t["ok"]:
        raise SampleError("pdx: " + t["why"])
    seen = {}
    for n, (off, ln) in enumerate(t["slots"]):
        if not ln:
            continue
        if (off, ln) in seen:
            # a bank aliases one sample to several numbers; the bytes are
            # extracted once and the alias is reported, not duplicated
            yield {"name": f"slot{n:03d}", "wav": None,
                   "note": f"slot {n} is the same bytes as slot {seen[(off, ln)]}"}
            continue
        seen[(off, ln)] = n
        pcm = decode_oki(data[off:off + ln])
        yield {"name": f"slot{n:03d}", "wav": _wav(pcm, _PDX_RATE),
               "note": f"OKI MSM6258 ADPCM {ln * 2:,} frames @ {_PDX_RATE} Hz "
                       "(the chip's default; the song may set another)"}


_EXTRACTORS = {
    "pdx": _pdx_samples,
    "mod": _mod_samples, "xm": _xm_samples, "it": _it_samples,
    "s3m": _s3m_samples, "gf1pat": _gf1pat_samples,
    "8svx": _svx_samples, "ncw": _ncw_samples, "sf2": _sf2_samples,
    "vag": _vag_samples, "hps": _hps_samples, "adx": _adx_samples,
    "brstm": _brstm_samples,
}
# formats whose extractor reads the path itself (walk/stream), not a bytes buffer
_PATH_EXTRACTORS = {"multisample": _multisample_samples, "krz": _krz_samples,
                    "e4b": _emu_samples, "e5b": _emu5_samples, "snd": _snd_samples,
                    "cdxa": _cdxa_samples, "gcm": _gcm_samples, "cue": _cue_samples,
                    "wii": _wiidisc_samples, "n64rom": _n64rom_samples,
                    "snesrom": _snesrom_samples}

EXTRACTABLE = frozenset(_EXTRACTORS) | frozenset(_PATH_EXTRACTORS)


# a malformed bank raises its own parser's error (Sf2Error, NcwError, SvxError
# are unrelated classes), and struct.error is the classic short-read signal.
# iter_samples promises SampleError, so they are converted at this boundary
# rather than leaking a parser's private exception type to the CLI as a
# traceback. Deliberately narrow: a KeyError or IndexError is a bug in us and
# must stay loud.
# cuemod.CueError: a .cue is hand-editable text, so a bad timestamp or a
# truncated TRACK line is ordinary malformed input rather than a bug in us.
# Imported lazily-but-eagerly here (the module is cheap and has no heavy deps)
# so the tuple stays a tuple.
from acidcat.core.containers import cue as _cuemod

_MALFORMED = (sf2mod.Sf2Error, ncwmod.NcwError, svxmod.SvxError, struct.error,
              _cuemod.CueError, AdxError, BrstmError, HpsError, VagError)


def iter_samples(filepath, fmt=None):
    """Yield {name, wav (bytes), note, ext?} for each embedded sample. Raises
    SampleError if the sniffed format has no extractor, or if the file claims
    that format but is too malformed to parse. Never modifies the file."""
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
    except _MALFORMED as e:
        raise SampleError(f"{fmt or 'file'}: {e}")
