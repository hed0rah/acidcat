"""
FLAC metadata-block iterator.

FLAC is a clean chunked container: the four-byte ``fLaC`` magic, then a
run of metadata blocks (each a 4-byte header + payload), then the audio
frames. This module just walks the block boundaries; per-block field
decoding lives in the core/walk/flac.py walker, like the other formats.
"""

import os
import struct

# block type -> name (FLAC spec 6.1)
BLOCK_TYPES = {
    0: "STREAMINFO",
    1: "PADDING",
    2: "APPLICATION",
    3: "SEEKTABLE",
    4: "VORBIS_COMMENT",
    5: "CUESHEET",
    6: "PICTURE",
}


def is_flac(filepath):
    """Check if file begins with the fLaC magic."""
    try:
        with open(filepath, "rb") as f:
            return f.read(4) == b"fLaC"
    except Exception:
        return False


def iter_metadata_blocks(filepath):
    """Yield (block_type, type_name, offset, length, is_last) for each
    metadata block in a FLAC file.

    ``offset`` points at the 4-byte block header; ``length`` is the
    payload size that follows it. Stops after the block whose last-block
    flag is set (the audio frames begin immediately after).
    """
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        if f.read(4) != b"fLaC":
            return
        pos = 4
        while pos + 4 <= size:
            f.seek(pos)
            header = f.read(4)
            if len(header) < 4:
                break
            first = header[0]
            is_last = bool(first & 0x80)
            block_type = first & 0x7F
            length = struct.unpack(">I", b"\x00" + header[1:4])[0]
            name = BLOCK_TYPES.get(block_type, f"RESERVED {block_type}")
            yield (block_type, name, pos, length, is_last)
            pos += 4 + length
            if is_last:
                break
