"""Nintendo 64 libultra audio bank (.ctl / ALBankFile) walker.

The classic N64 audio bank: an ALBankFile header (revision 0x4231 'B1', a bank
count, and offsets to each ALBank), then a tree of ALBank -> ALInstrument ->
ALSound -> ALWaveTable, plus the ADPCM codebooks (ALADPCMBook) and loops. Every
reference is a big-endian u32 offset from the start of the .ctl. Sample waveform
bytes live in a companion .tbl file (VADPCM, decoded by core/vadpcm.py); an
ALWaveTable.base is an offset into that .tbl, not the .ctl.

This walks the .ctl structure for `inspect`. Many games store the .tbl at a
game-specific ROM location, so bulk sample extraction is a separate, best-effort
step; the structure here is self-contained.

Reference: libultra libaudio.h (AL_BANK_VERSION=0x4231); n64decomp/sm64.
"""

import struct

from acidcat.core.walk.base import _f, _open, _size

MAGIC = 0x4231
MAX_CTL_BYTES = 8 * 1024 * 1024


def _u8(d, o): return d[o]
def _s16(d, o): return struct.unpack_from(">h", d, o)[0]
def _u16(d, o): return struct.unpack_from(">H", d, o)[0]
def _s32(d, o): return struct.unpack_from(">i", d, o)[0]
def _u32(d, o): return struct.unpack_from(">I", d, o)[0]


def _ptr(d, o):
    """A u32 offset, or 0 when the POINTER ITSELF lies outside the file.

    Every offset table here is walked `count` times, and `count` is a number
    the file supplied. Each caller checked the bytes the pointer points AT and
    trusted that the four bytes holding the pointer were there to read -- true
    for a real bank, false for one whose declared counts outlive its length,
    and struct.error escaped the walk rather than a clean refusal.

    Zero is the right answer rather than an exception because zero is already
    what every caller reads as "absent"; a table entry that is not present in
    the file is exactly that.
    """
    return _u32(d, o) if 0 <= o and o + 4 <= len(d) else 0


def inspect_albank(filepath, deep=False):
    """Walk a .ctl ALBankFile. Returns (chunks, file_warnings)."""
    size = _size(filepath)
    with _open(filepath) as f:
        d = f.read(min(MAX_CTL_BYTES, size))
    warns = []
    if len(d) < 4 or _u16(d, 0) != MAGIC:
        warns.append("missing ALBankFile revision 0x4231")
    rev = _u16(d, 0) if len(d) >= 2 else 0
    bank_count = _s16(d, 2) if len(d) >= 4 else 0

    header = {"id": "ALBankFile", "offset": 0, "size": 4 + 4 * max(0, bank_count),
              "summary": f"revision 0x{rev:04X}, {bank_count} bank(s)", "payload_base": 0,
              "warnings": [], "fields": [
                  _f(0, 2, "revision", f"0x{rev:04X}", "'B1', AL_BANK_VERSION"),
                  _f(2, 2, "bankCount", bank_count)]}
    for i in range(max(0, min(bank_count, 64))):
        off = _ptr(d, 4 + 4 * i)
        header["fields"].append(_f(4 + 4 * i, 4, f"bank[{i}]", f"-> 0x{off:X}"))

    chunks = [header]
    for bi in range(max(0, min(bank_count, 64))):
        bo = _ptr(d, 4 + 4 * bi)
        if not bo or bo + 12 > len(d):
            continue
        chunks.append(_walk_bank(d, bo, bi, deep))
    return chunks, warns


def _walk_bank(d, bo, bi, deep):
    inst_count = _s16(d, bo)
    sample_rate = _s32(d, bo + 4)
    flags = _u8(d, bo + 2)
    chunk = {"id": f"ALBank[{bi}]", "offset": bo, "size": 0x0C + 4 * max(0, inst_count),
             "payload_base": bo, "warnings": [],
             "summary": f"{inst_count} instrument(s), {sample_rate} Hz",
             "fields": [_f(0, 2, "instCount", inst_count),
                        _f(2, 1, "flags", f"0x{flags:02X}"),
                        _f(4, 4, "sampleRate", f"{sample_rate} Hz")]}
    if not (8000 <= sample_rate <= 48000):
        chunk["warnings"].append("sampleRate outside 8000-48000 Hz (suspect bank)")

    # walk instruments -> sounds -> wavetables; summarize the waveforms + codebooks
    rows, adpcm, raw, seen = [], 0, 0, set()
    for i in range(max(0, min(inst_count, 256))):
        io = _ptr(d, bo + 0x0C + 4 * i)
        if not io or io + 0x10 > len(d):
            continue
        sound_count = _s16(d, io + 0x0E)
        for j in range(max(0, min(sound_count, 128))):
            so = _ptr(d, io + 0x10 + 4 * j)
            if not so or so in seen or so + 12 > len(d):
                continue
            seen.add(so)
            wto = _u32(d, so + 0x08)
            if not wto or wto + 0x14 > len(d):
                continue
            base = _u32(d, wto)
            wlen = _s32(d, wto + 4)
            wtype = _u8(d, wto + 8)
            book = _u32(d, wto + 0x10)
            order = npred = 0
            if wtype == 0 and book and book + 8 <= len(d):
                order, npred = _s32(d, book), _s32(d, book + 4)
                adpcm += 1
            elif wtype == 1:
                raw += 1
            if deep:
                rows.append({"tick": len(rows),
                             "event": "ADPCM" if wtype == 0 else "RAW16" if wtype == 1 else str(wtype),
                             "detail": f"tbl base 0x{base:X} len {wlen}"
                                       + (f" book order {order} npred {npred}" if wtype == 0 else "")})
    chunk["fields"].append(_f(None, 0, "waveforms", f"{adpcm + raw} ({adpcm} VADPCM, {raw} raw16)"))
    if deep:
        chunk["rows"] = rows
    return chunk
