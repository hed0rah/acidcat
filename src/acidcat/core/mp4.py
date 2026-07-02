"""ISO-BMFF (MP4/M4A) box walker.

A box is [size u32][type 4cc][payload]. size == 1 means a 64-bit largesize
follows the type; size == 0 means the box runs to the end of the file. A
'uuid' box carries a 16-byte usertype after the header. Container boxes hold
child boxes; 'meta' is a FullBox container (a 4-byte version/flags prefix
precedes its children). All integers are big-endian.

This walks the tree (bounds- and depth-checked, the classic MP4 DoS guards)
and decodes the common metadata: ftyp brands, the movie duration from mvhd,
and the iTunes tags under udta > meta > ilst.
"""

import struct

_CONTAINERS = {
    b"moov", b"trak", b"edts", b"mdia", b"minf", b"dinf", b"stbl",
    b"udta", b"ilst", b"mvex", b"moof", b"traf", b"mfra",
}
_MAX_DEPTH = 16

# iTunes ilst tags -> label. the a9 (copyright sign) tags start with 0xA9.
_A9 = 0xA9
_ILST_TAGS = {
    b"\xa9nam": "title", b"\xa9ART": "artist", b"aART": "album_artist",
    b"\xa9alb": "album", b"\xa9day": "year", b"\xa9gen": "genre",
    b"\xa9wrt": "composer", b"\xa9too": "encoder", b"\xa9cmt": "comment",
    b"\xa9grp": "grouping", b"tmpo": "bpm", b"cpil": "compilation",
    b"trkn": "track", b"disk": "disc", b"covr": "cover_art", b"gnre": "genre",
}


def is_mp4(data):
    """A file whose first box (after the 4-byte size) is ftyp."""
    return len(data) >= 12 and data[4:8] == b"ftyp"


def _box_header(data, pos, end):
    """Decode a box header at pos. Return (btype, hdr_len, box_size) or None
    if it does not fit / is malformed."""
    if pos + 8 > end:
        return None
    size = struct.unpack_from(">I", data, pos)[0]
    btype = data[pos + 4:pos + 8]
    hdr = 8
    if size == 1:
        if pos + 16 > end:
            return None
        size = struct.unpack_from(">Q", data, pos + 8)[0]
        hdr = 16
    elif size == 0:
        size = end - pos
    if btype == b"uuid":
        hdr += 16
    if size < hdr or pos + size > end:
        return None
    return btype, hdr, size


def iter_boxes(data, start=0, end=None, depth=0):
    """Yield box dicts {type, offset, size, hdr, depth, truncated} for the box
    tree in [start, end), recursing into containers. Depth- and bounds-safe."""
    if end is None:
        end = len(data)
    pos = start
    while pos + 8 <= end:
        hd = _box_header(data, pos, end)
        if hd is None:
            # a box header that overruns its parent: report and stop this level
            raw = struct.unpack_from(">I", data, pos)[0] if pos + 4 <= end else 0
            yield {"type": data[pos + 4:pos + 8], "offset": pos,
                   "size": raw, "hdr": 8, "depth": depth, "truncated": True}
            return
        btype, hdr, size = hd
        yield {"type": btype, "offset": pos, "size": size, "hdr": hdr,
               "depth": depth, "truncated": False}
        if btype in _CONTAINERS and depth < _MAX_DEPTH:
            yield from iter_boxes(data, pos + hdr, pos + size, depth + 1)
        elif btype == b"meta" and depth < _MAX_DEPTH:
            # FullBox container: 4-byte version/flags before the children
            yield from iter_boxes(data, pos + hdr + 4, pos + size, depth + 1)
        pos += size


def movie_timescale_duration(data):
    """Return (timescale, duration) from mvhd, or (None, None)."""
    for b in iter_boxes(data):
        if b["type"] == b"mvhd" and not b["truncated"]:
            p = b["offset"] + b["hdr"]
            if p + 1 > len(data):
                return None, None
            version = data[p]
            try:
                if version == 1:
                    timescale = struct.unpack_from(">I", data, p + 20)[0]
                    duration = struct.unpack_from(">Q", data, p + 24)[0]
                else:
                    timescale = struct.unpack_from(">I", data, p + 12)[0]
                    duration = struct.unpack_from(">I", data, p + 16)[0]
            except struct.error:
                return None, None
            return timescale, duration
    return None, None


def _decode_data_box(data, start, end):
    """Decode a 'data' box payload (type indicator u32, locale u32, value)."""
    if end - start < 16:
        return None
    type_ind = struct.unpack_from(">I", data, start + 8)[0] & 0xFFFFFF
    val = data[start + 16:end]
    if type_ind == 1:  # utf-8 text
        return val.decode("utf-8", errors="replace")
    if type_ind in (13, 14):  # jpeg / png image
        return f"{len(val):,}-byte {'jpeg' if type_ind == 13 else 'png'} image"
    if type_ind == 21 and val:  # signed int (bpm, track counts, ...)
        return int.from_bytes(val, "big")
    return f"{len(val):,} bytes (type {type_ind})"


def parse_ilst(data):
    """Extract the iTunes metadata under udta > meta > ilst as {label: value}.
    Each ilst child's type is the tag; it holds a 'data' box with the value."""
    meta = {}
    for b in iter_boxes(data):
        if b["depth"] < 3 or b["truncated"]:
            continue
        label = _ILST_TAGS.get(b["type"])
        if not label:
            continue
        # find the 'data' child box inside this tag box
        inner_start = b["offset"] + b["hdr"]
        inner_end = b["offset"] + b["size"]
        for c in iter_boxes(data, inner_start, inner_end, depth=b["depth"] + 1):
            if c["type"] == b"data" and not c["truncated"]:
                v = _decode_data_box(data, c["offset"], c["offset"] + c["size"])
                if v is not None and label not in meta:
                    meta[label] = v
                break
    return meta
