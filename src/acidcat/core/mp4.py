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

import math
import struct

from acidcat.core.mp3 import ID3V1_GENRES

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


def _box_header(data, pos, end, avail):
    """Decode a box header at pos. `end` is the logical bound (parent box end,
    or the real file size at the top level); `avail` is how many bytes were read
    into `data`. Returns (btype, hdr_len, box_size, beyond_cap) or None if the
    box overruns its logical bound (malformed). beyond_cap is True when the box
    is valid but its contents extend past the read window (e.g. a large mdat)."""
    if pos + 8 > avail:
        return None
    size = struct.unpack_from(">I", data, pos)[0]
    btype = data[pos + 4:pos + 8]
    hdr = 8
    if size == 1:
        if pos + 16 > avail:
            return None
        size = struct.unpack_from(">Q", data, pos + 8)[0]
        hdr = 16
    elif size == 0:
        size = end - pos
    if btype == b"uuid":
        hdr += 16
    if size < hdr or pos + size > end:
        return None
    return btype, hdr, size, pos + size > avail


def iter_boxes(data, start=0, end=None, depth=0, file_size=None):
    """Yield box dicts {type, offset, size, hdr, depth, truncated, beyond_cap}
    for the box tree in [start, end), recursing into containers. `file_size` (the
    real on-disk size) bounds top-level boxes so a large mdat read only in part
    is 'beyond_cap', not a false 'overruns'. Depth- and bounds-safe."""
    avail = len(data)
    if file_size is None:
        file_size = avail
    if end is None:
        end = file_size
    pos = start
    while pos + 8 <= end and pos + 8 <= avail:
        hd = _box_header(data, pos, end, avail)
        if hd is None:
            # a box header that overruns its logical parent: report and stop.
            raw = struct.unpack_from(">I", data, pos)[0] if pos + 4 <= avail else 0
            yield {"type": data[pos + 4:pos + 8], "offset": pos, "size": raw,
                   "hdr": 8, "depth": depth, "truncated": True, "beyond_cap": False}
            return
        btype, hdr, size, beyond_cap = hd
        yield {"type": btype, "offset": pos, "size": size, "hdr": hdr,
               "depth": depth, "truncated": False, "beyond_cap": beyond_cap}
        if not beyond_cap and btype in _CONTAINERS and depth < _MAX_DEPTH:
            yield from iter_boxes(data, pos + hdr, pos + size, depth + 1, file_size)
        elif not beyond_cap and btype == b"meta" and size >= hdr + 4 \
                and depth < _MAX_DEPTH:
            # FullBox container: 4-byte version/flags before the children
            yield from iter_boxes(data, pos + hdr + 4, pos + size, depth + 1, file_size)
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


def find_moov(filepath, file_size):
    """Locate the top-level moov box by reading only box headers (8-16 bytes
    each, no payload), so it is found regardless of file size or position.
    Non-faststart files (most Apple/ffmpeg output) put moov at EOF. Returns
    (offset, size) or (None, None)."""
    with open(filepath, "rb") as f:
        pos = 0
        while pos + 8 <= file_size:
            f.seek(pos)
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            size = struct.unpack(">I", hdr[:4])[0]
            btype = hdr[4:8]
            if size == 1:
                ext = f.read(8)
                if len(ext) < 8:
                    break
                size = struct.unpack(">Q", ext)[0]
            elif size == 0:
                size = file_size - pos
            if size < 8 or pos + size > file_size:
                break
            if btype == b"moov":
                return pos, size
            pos += size
    return None, None


def audio_info(data):
    """From stsd's first audio SampleEntry: (codec_fourcc_str, channels,
    sample_rate). Returns None if not found. Codec is e.g. 'mp4a' (AAC),
    'alac' (Apple Lossless), 'Opus', 'fLaC'."""
    for b in iter_boxes(data):
        if b["type"] != b"stsd" or b["truncated"]:
            continue
        # FullBox: 4-byte version/flags, 4-byte entry_count, then the entry box
        ep = b["offset"] + b["hdr"] + 8
        eh = _box_header(data, ep, b["offset"] + b["size"], len(data))
        if eh is None:
            return None
        codec, ehdr, _, _ = eh
        ap = ep + ehdr  # AudioSampleEntry payload
        cstr = codec.decode("latin-1", errors="replace")
        # 6 reserved + 2 data_ref_index, then version(2) at +8. v0 and v1
        # share the layout up to samplerate: channelcount(2) at +16,
        # samplesize(2), 2+2, samplerate(4, 16.16 fixed) at +24 (v1 appends
        # four u32s after it). QuickTime v2 is a different struct: float64
        # rate at +32, u32 channel count at +40.
        if ap + 28 > len(data):
            return cstr, None, None
        version = struct.unpack_from(">H", data, ap + 8)[0]
        if version == 2:
            if ap + 44 > len(data):
                return cstr, None, None
            rate_f = struct.unpack_from(">d", data, ap + 32)[0]
            rate = int(rate_f) if math.isfinite(rate_f) and 0 < rate_f < 1e7 \
                else None
            channels = struct.unpack_from(">I", data, ap + 40)[0]
            return cstr, channels, rate
        channels = struct.unpack_from(">H", data, ap + 16)[0]
        rate = struct.unpack_from(">I", data, ap + 24)[0] >> 16
        return cstr, channels, rate
    return None


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
    if type_ind == 21 and 1 <= len(val) <= 8:  # int (bpm, track); real ones fit 8 bytes
        return int.from_bytes(val, "big")
    return f"{len(val):,} bytes (type {type_ind})"


def _decode_gnre(data, start, end):
    """Decode a 'gnre' data box: type indicator 0, a big-endian u16 holding
    the ID3v1 genre index + 1. Older iTunes wrote genre this way instead of
    the \\xa9gen text atom; without this it displays as raw bytes."""
    val = data[start + 16:end]
    if len(val) != 2:
        return None
    idx = struct.unpack_from(">H", val)[0] - 1
    if 0 <= idx < len(ID3V1_GENRES):
        return ID3V1_GENRES[idx]
    return None


def _decode_index_pair(data, start, end):
    """Decode a trkn/disk 'data' box (reserved u16, index u16, total u16, ...)
    to 'index/total', or 'index' when no total is set. None if too short."""
    val = data[start + 16:end]
    if len(val) < 6:
        return None
    idx = struct.unpack_from(">H", val, 2)[0]
    total = struct.unpack_from(">H", val, 4)[0]
    return f"{idx}/{total}" if total else str(idx)


def parse_ilst(data):
    """Extract the iTunes metadata under udta > meta > ilst as {label: value}.
    Only boxes that are direct children of an actual 'ilst' box are treated as
    tags (an ancestry check, not a depth heuristic, so a stray a9-prefixed box
    elsewhere in the tree cannot masquerade as a tag). Each tag box holds a
    'data' box with the value."""
    meta = {}
    for b in iter_boxes(data):
        if b["type"] != b"ilst" or b["truncated"]:
            continue
        istart, iend = b["offset"] + b["hdr"], b["offset"] + b["size"]
        for tag in iter_boxes(data, istart, iend, depth=b["depth"] + 1):
            if tag["depth"] != b["depth"] + 1:  # direct children of ilst only
                continue
            label = _ILST_TAGS.get(tag["type"])
            if not label or label in meta:
                continue
            tstart, tend = tag["offset"] + tag["hdr"], tag["offset"] + tag["size"]
            for c in iter_boxes(data, tstart, tend, depth=tag["depth"] + 1):
                if c["type"] == b"data" and not c["truncated"]:
                    cs, ce = c["offset"], c["offset"] + c["size"]
                    if label in ("track", "disc"):
                        v = _decode_index_pair(data, cs, ce) or _decode_data_box(data, cs, ce)
                    elif tag["type"] == b"gnre":
                        v = _decode_gnre(data, cs, ce) or _decode_data_box(data, cs, ce)
                    else:
                        v = _decode_data_box(data, cs, ce)
                    if v is not None:
                        meta[label] = v
                    break
    return meta
