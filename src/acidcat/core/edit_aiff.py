"""Safe AIFF/AIFC rewriting for acidcat's write capability.

Edits the standard text chunks (NAME, AUTH, ANNO) while preserving the audio
(COMM/SSND) and every unknown chunk byte-for-byte. AIFF is BIG-endian (the trap
that separates it from WAV): FORM/chunk sizes are big-endian, otherwise the
alignment rules match RIFF (one uncounted 0x00 pad after any odd-sized chunk,
FORM size = file - 8). Malformed files are refused rather than guessed.
"""

import struct

from acidcat.core.edits import EditError

# field -> AIFF text chunk id (raw text, not null-terminated, not pascal)
_AIFF_TEXT = {
    "title": b"NAME", "name": b"NAME",
    "artist": b"AUTH", "author": b"AUTH", "creator": b"AUTH",
    "comment": b"ANNO", "annotation": b"ANNO", "description": b"ANNO",
}


def _iter_chunks(data):
    """Yield [chunk_id, payload] preserving order. Big-endian sizes. Raises
    EditError on anything unsafe to rewrite."""
    if data[:4] != b"FORM" or data[8:12] not in (b"AIFF", b"AIFC"):
        raise EditError("not an AIFF/AIFC file")
    n = len(data)
    pos = 12
    chunks = []
    seen_ssnd = False
    while pos + 8 <= n:
        cid = data[pos:pos + 4]
        size = struct.unpack_from(">I", data, pos + 4)[0]  # big-endian
        if pos + 8 + size > n:
            raise EditError(f"chunk {cid!r} overruns the file; refusing to rewrite")
        if not all(32 <= b < 127 for b in cid):
            raise EditError("non-printable chunk id; refusing to rewrite")
        chunks.append([cid, data[pos + 8:pos + 8 + size]])
        if cid == b"SSND":
            seen_ssnd = True
        pos += 8 + size + (size & 1)
    if not seen_ssnd:
        raise EditError("no SSND (sound data) chunk; refusing to rewrite")
    return chunks, data[pos:]


def edit_aiff(data, changes):
    unknown = [f for f in changes if f.lower() not in _AIFF_TEXT]
    if unknown:
        raise EditError(f"AIFF has no editable field(s): {', '.join(sorted(unknown))}")
    chunks, trailing = _iter_chunks(data)
    audio = next(c[1] for c in chunks if c[0] == b"SSND")
    applied = []

    for field, value in changes.items():
        cid = _AIFF_TEXT[field.lower()]
        existing = next((c for c in chunks if c[0] == cid), None)
        old = existing[1].decode("latin-1") if existing else None
        if value is None:
            if existing:
                chunks.remove(existing)
        else:
            payload = str(value).encode("utf-8")
            if existing:
                existing[1] = payload
            else:
                # text/metadata chunks go before SSND for maximum reader support
                idx = next(i for i, c in enumerate(chunks) if c[0] == b"SSND")
                chunks.insert(idx, [cid, payload])
        applied.append((field, old, value))

    out = bytearray(b"FORM\x00\x00\x00\x00" + data[8:12])
    for cid, payload in chunks:
        out += cid + struct.pack(">I", len(payload)) + payload
        if len(payload) & 1:
            out += b"\x00"
    form_size = len(out) - 8  # container covers header+chunks, NOT trailing junk
    out += trailing
    struct.pack_into(">I", out, 4, form_size)

    if next(c[1] for c in _iter_chunks(bytes(out))[0] if c[0] == b"SSND") != audio:
        raise EditError("internal: audio data changed during rewrite (aborted)")
    return bytes(out), applied
