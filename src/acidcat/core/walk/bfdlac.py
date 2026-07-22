"""BFD `.bfdlac` (BFD Compressed) walker.

FXpansion's BFD drum engine stores each audio hit as a `.bfdlac` file: a
big-endian, IFF-style container (magic `BFDC`, an outer size = file length - 8)
holding a fixed chunk set:

    fmt    audio descriptor: bit depth, sample count, sample rate, channels
    BFDi   a pack/kit identifier string (e.g. "BFDHP-...")
    Indx   a block seek index: block size, frame count, then a u32 offset table
    data   the compressed audio -- a lossless ("lac") codec, ~7 bits/byte

Confirmed uniform across 181,696 BFD Horsepower files: every one is BFDC with
`fmt`/`BFDi`/`Indx`/`data`, 24-bit / 44100 Hz / stereo. The walker surfaces the
structure and the format metadata; it does not decode the compressed audio (the
bfdlac codec is undocumented). Chunk framing is IFF-standard big-endian: a 4-byte
id, a u32 size, the payload, no pad byte observed. The walk degrades on any
malformed input and never raises.
"""

import os

from acidcat.core.walk.base import _bu16, _bu32, _dtext, _f

_READ_CAP = 8 * 1024 * 1024
_CHUNK_CAP = 64

_BITS_OFF = 0x00                                    # fmt field offsets (big-endian u32)


def inspect_bfdlac(filepath):
    """Walk a BFD `.bfdlac` file, returning (chunks, file_warnings)."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        b = f.read(min(file_size, _READ_CAP))
    chunks, warns = [], []
    if len(b) < 12 or b[:4] != b"BFDC":
        return chunks, ["not a BFDC (.bfdlac) file"]

    outer = _bu32(b, 4)
    hdr = {"id": "BFDC", "offset": 0, "size": 8, "payload_base": 0,
           "summary": "BFD compressed audio (BFDC)",
           "fields": [
               _f(0x00, 4, "magic", "BFDC"),
               _f(0x04, 4, "outer_size", outer,
                  "declared body size (file length - 8)", enc=">I", raw=outer),
           ], "warnings": []}
    expected = file_size - 8
    if outer != expected:
        hdr["warnings"].append(
            f"outer_size {outer:,} != file length - 8 ({expected:,}); "
            f"off by {expected - outer}")
    chunks.append(hdr)

    fmt = None
    pos, n = 8, 0
    while pos + 8 <= len(b) and n < _CHUNK_CAP:
        cid = b[pos:pos + 4]
        size = _bu32(b, pos + 4)
        payload = pos + 8
        cid_s = cid.decode("latin1")
        avail = max(0, min(size, len(b) - payload))
        p = b[payload:payload + avail]
        n += 1
        try:
            chunk, fmt_out = _chunk(cid, cid_s, pos, size, p, avail, fmt, file_size)
        except Exception as e:                          # never raise on a bad chunk
            chunk, fmt_out = {
                "id": cid_s, "offset": pos, "size": size, "fields": [],
                "summary": "unparsed chunk",
                "warnings": [f"chunk decode error: {e.__class__.__name__}: {e}"]}, fmt
        fmt = fmt_out
        if avail < size:
            chunk.setdefault("warnings", []).append(
                f"chunk declares {size:,} bytes, only {avail:,} present (truncated)")
        chunks.append(chunk)
        if cid == b"data":                              # data is the final, huge chunk
            break
        step = 8 + size
        if step <= 8:
            warns.append(f"chunk at 0x{pos:08x} has size {size}; stopping the walk")
            break
        pos += step

    if n >= _CHUNK_CAP:
        warns.append(f"chunk walk stopped at the {_CHUNK_CAP}-chunk cap")

    # enrich the BFDC summary with the audio descriptor
    if fmt and fmt.get("rate"):
        dur = fmt["samples"] / fmt["rate"] if fmt["rate"] else 0
        chunks[0]["summary"] += (f" -- {fmt['bits']}-bit, {fmt['rate']} Hz, "
                                 f"{fmt['channels']}ch, {dur:.2f}s")
    return chunks, warns


def _chunk(cid, cid_s, pos, size, p, avail, fmt, file_size):
    fields, cwarns, summary = [], [], None

    if cid == b"fmt " and avail >= 20:
        bits = _bu32(p, 0)
        enc = _bu32(p, 4)                               # constant 10 across the corpus
        samples = _bu32(p, 8)
        rate = _bu32(p, 12)
        channels = _bu32(p, 16)
        fmt = {"bits": bits, "rate": rate, "channels": channels, "samples": samples}
        dur = samples / rate if rate else 0
        summary = f"audio format: {bits}-bit, {rate} Hz, {channels}ch, {dur:.3f}s"
        fields = [
            _f(0x00, 4, "bits_per_sample", bits, "sample bit depth"),
            _f(0x04, 4, "encoding", enc, "codec/encoding tag (constant 10 observed)"),
            _f(0x08, 4, "num_samples", samples, f"{samples:,} frames per channel"),
            _f(0x0C, 4, "sample_rate", rate, f"{rate} Hz"),
            _f(0x10, 4, "channels", channels, "channel count"),
        ]

    elif cid == b"BFDi":
        text = _dtext(p)
        summary = f"pack id: {text}" if text else "pack identifier"
        fields.append(_f(0x00, avail, "pack_id", text or "(empty)",
                         "BFD pack/kit identifier string"))

    elif cid == b"Indx":
        block = _bu32(p, 0) if avail >= 4 else 0
        frames = _bu32(p, 4) if avail >= 8 else 0
        entries = (avail - 8) // 4                       # u32 offset per frame
        summary = (f"block seek index: {frames} frame(s) of {block} samples, "
                   f"{entries} offset(s)")
        fields = [
            _f(0x00, 4, "block_size", block, "samples per compressed block"),
            _f(0x04, 4, "frame_count", frames, "number of blocks"),
            _f(0x08, avail - 8, "offsets", f"{entries} u32 entries",
               "byte offset of each block into the data chunk"),
        ]
        if fmt and block and fmt.get("samples"):
            approx = -(-fmt["samples"] // block)         # ceil(samples / block)
            if abs(approx - frames) > 1:
                cwarns.append(f"frame_count {frames} != ceil(samples/block) {approx}")

    elif cid == b"data":
        summary = f"compressed audio (bfdlac codec), {size:,} bytes"
        fields.append(_f(0x00, min(avail, 0), "payload", f"{size:,} bytes",
                         "lossless-compressed samples; codec undocumented"))

    else:
        summary = f"unrecognized chunk, {size:,} bytes"

    return {"id": cid_s, "offset": pos, "size": size,
            "summary": summary, "fields": fields, "warnings": cwarns}, fmt
