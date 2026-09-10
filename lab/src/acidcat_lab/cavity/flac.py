"""Plant a payload in a FLAC metadata block.

A FLAC stream is "fLaC" then a chain of METADATA_BLOCKs, then audio frames.
Decoders play the audio whatever optional metadata is present, so a metadata
block is a container-internal cavity: the frames and every length field stay
honest and the file is conformant throughout.

Two vectors, both round-tripped here:

    app   an APPLICATION block (type 2). Body is a 4-byte application id plus
          free data. Decoders skip ids they do not know, so an unregistered one
          carries an arbitrary payload while the file stays valid.
    pad   a PADDING block (type 1). The spec says padding bytes are zero, so
          non-zero PADDING is the FLAC analogue of a non-zero RIFF JUNK chunk.

acidcat already detects both, at every size measured, via the
`application_block` and `cavity_content` rules -- which is why this module
arrives as a planter and not alongside a new detector. What it adds is
tests/test_flac_cavity_loop.py: existing capability turned into a permanent
regression test with a real adversary on the other end of it.
"""


import struct

APP_ID = b"ACFC"                    # unregistered application id (the tell)
MAGIC = b"ACFL"                     # marker so extract finds our block

_TYPE = {0: "STREAMINFO", 1: "PADDING", 2: "APPLICATION", 3: "SEEKTABLE",
         4: "VORBIS_COMMENT", 5: "CUESHEET", 6: "PICTURE"}


def _blocks(flac):
    """Yield (btype, is_last, body_bytes) for each metadata block."""
    if flac[:4] != b"fLaC":
        raise ValueError("not a FLAC stream")
    pos = 4
    while pos + 4 <= len(flac):
        hdr = flac[pos]
        is_last = bool(hdr & 0x80)
        btype = hdr & 0x7F
        size = struct.unpack(">I", b"\x00" + flac[pos + 1:pos + 4])[0]
        body = flac[pos + 4:pos + 4 + size]
        yield btype, is_last, body
        pos += 4 + size
        if is_last:
            break
    globals()["_frames_off"] = pos            # where audio frames start


def _emit(btype, body, is_last):
    hdr = (0x80 if is_last else 0) | (btype & 0x7F)
    return bytes([hdr]) + struct.pack(">I", len(body))[1:] + body


def _rebuild(flac, new_type, new_body):
    """Insert a metadata block after STREAMINFO, fix is-last flags, keep frames."""
    blocks = [(t, b) for t, _last, b in _blocks(flac)]
    frames = flac[_frames_off:]
    # STREAMINFO must stay first; splice ours in at index 1
    blocks.insert(1, (new_type, new_body))
    out = bytearray(b"fLaC")
    for i, (t, b) in enumerate(blocks):
        out += _emit(t, b, is_last=(i == len(blocks) - 1))
    out += frames
    return bytes(out)


def embed_app(flac, payload):
    return _rebuild(flac, 2, APP_ID + MAGIC + payload)


def embed_pad(flac, payload):
    return _rebuild(flac, 1, MAGIC + payload)


def extract(flac):
    for btype, _last, body in _blocks(flac):
        if btype == 2 and body[:4] == APP_ID and body[4:8] == MAGIC:
            return body[8:]
        if btype == 1 and body[:4] == MAGIC:
            return body[4:]
    return b""


def analyze(flac):
    out = []
    for btype, is_last, body in _blocks(flac):
        name = _TYPE.get(btype, f"RESERVED({btype})")
        note = ""
        if btype == 2:
            aid = body[:4]
            note = f" app id {aid!r}" + (" (unregistered/cavity)"
                                         if aid == APP_ID else "")
        elif btype == 1:
            note = " NON-ZERO (cavity)" if any(body) else " all zero (padding)"
        out.append(f"{name}: {len(body)} bytes{note}"
                   + (" [last]" if is_last else ""))
    return out
