"""Structural repair for FLAC: the metadata-block chain's derived fields.

A FLAC file is ``fLaC`` then a chain of metadata blocks -- each ``[last:1|type:7]
[length:24]`` header plus a body -- terminated by the block whose last-metadata
flag is set, after which the audio frames begin (a 14-bit ``11111111 111110``
sync, i.e. 0xFF 0xF8/0xF9). Two of those derived fields have an independent
witness, so they are repairable:

  * the **last-metadata-block flag** (OFFSET/boundary kind): exactly one block
    carries it, and the audio frames must begin immediately after that block. The
    witness is the frame sync -- walk the chain by block lengths (ignoring the
    stored flags) until the audio sync appears, and the block ending there is the
    true last one. A flag set too early (a decoder would read audio as metadata)
    or missing (metadata bleeds into the stream) is corrected. Length-preserving:
    it flips the 0x80 bit on a type byte.
  * **PADDING body all-zero** (ZERO kind): the spec fixes PADDING content at
    0x00, so non-zero bytes there are junk. Zeroed in place. This is the FLAC
    analog of the RIFF odd-chunk pad byte.

Everything from the first audio frame on is guarded and never touched. Fields that
would need the audio decoded to witness them (STREAMINFO's MD5 and total-samples,
the SEEKTABLE offsets) are out of scope here -- acidcat bundles no FLAC decoder.
"""

_MAGIC = b"fLaC"
_PADDING = 1


def is_flac(data):
    return len(data) >= 4 and data[:4] == _MAGIC


def _is_frame_sync(data, pos):
    """True when a FLAC audio frame header begins at pos (14-bit sync code)."""
    return (pos + 1 < len(data) and data[pos] == 0xFF
            and (data[pos + 1] & 0xFE) == 0xF8)


def walk(data):
    """Walk the metadata-block chain from offset 4, by block length and
    *ignoring* the stored last-flags, stopping at the audio frame sync. Returns
    (blocks, audio_start, ok) where each block is
    {pos, type, last, length, body} and ok is True when the walk reached a real
    frame sync (so the boundary is witnessed)."""
    blocks = []
    pos = 4
    while pos + 4 <= len(data):
        if _is_frame_sync(data, pos):
            return blocks, pos, True
        hdr = data[pos]
        btype = hdr & 0x7f
        if btype == 127:                       # reserved/invalid: not a block
            return blocks, pos, False
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        if pos + 4 + length > len(data):
            return blocks, pos, False          # overrun: chain not intact
        blocks.append({"pos": pos, "type": btype, "last": bool(hdr & 0x80),
                       "length": length, "body": pos + 4})
        pos += 4 + length
    return blocks, pos, False


def analyze(data):
    """Return a list of ``{path, field, old, new, kind, witness}`` for the
    repairable FLAC structural violations. Read-only."""
    out = []
    blocks, audio_start, ok = walk(data)

    # PADDING must be all zero (spec)
    for i, b in enumerate(blocks):
        if b["type"] == _PADDING:
            body = data[b["body"]:b["body"] + b["length"]]
            if any(body):
                nz = sum(1 for x in body if x)
                out.append({"path": f"PADDING[{i}]", "field": "padding",
                            "old": f"{nz} non-zero byte(s)", "new": "zeroed",
                            "kind": "zero", "witness": "spec (PADDING = 0x00)"})

    # exactly the last block (the one ending at the audio sync) carries the flag
    if ok and blocks:
        for i, b in enumerate(blocks):
            should = (i == len(blocks) - 1)
            if b["last"] != should:
                out.append({"path": f"block[{i}]", "field": "last_flag",
                            "old": b["last"], "new": should, "kind": "offset",
                            "witness": "audio frame sync"})
    return out


def repair_flac(data):
    """Return (new_bytes, changes) with the witnessed FLAC violations fixed.
    Length-preserving; the audio frames are never touched."""
    blocks, audio_start, ok = walk(data)
    changes = analyze(data)
    if not changes:
        return data, []
    out = bytearray(data)

    for b in blocks:
        if b["type"] == _PADDING:
            for j in range(b["body"], b["body"] + b["length"]):
                out[j] = 0
    if ok:
        for i, b in enumerate(blocks):
            should = (i == len(blocks) - 1)
            if b["last"] != should:
                if should:
                    out[b["pos"]] |= 0x80
                else:
                    out[b["pos"]] &= 0x7f
    return bytes(out), changes
