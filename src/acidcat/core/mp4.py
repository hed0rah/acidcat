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


# ── stsd sample-entry / codec-config decoding ──────────────────────

# ISO 14496-3 samplingFrequencyIndex table (index 15 = explicit 24-bit rate)
_ASC_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050,
              16000, 12000, 11025, 8000, 7350)

_AAC_OBJECT_TYPES = {
    1: "AAC Main", 2: "AAC LC", 3: "AAC SSR", 4: "AAC LTP",
    5: "SBR (HE-AAC)", 6: "AAC Scalable", 17: "ER AAC LC", 23: "ER AAC LD",
    29: "PS (HE-AACv2)", 39: "ER AAC ELD", 42: "USAC (xHE-AAC)",
}

# objectTypeIndication in the DecoderConfigDescriptor
_ESDS_OTI = {
    0x40: "MPEG-4 Audio", 0x66: "MPEG-2 AAC Main", 0x67: "MPEG-2 AAC LC",
    0x68: "MPEG-2 AAC SSR", 0x69: "MPEG-2 audio (Layer 1/2/3)",
    0x6B: "MPEG-1 audio (MP3)",
}


def _desc_len(data, pos, end):
    """MPEG-4 descriptor 'expandable' length: base-128, the high bit of each
    byte says another follows, at most 4 bytes. Returns (size, new_pos) or
    (None, pos) on truncation."""
    size = 0
    for _ in range(4):
        if pos >= end:
            return None, pos
        b = data[pos]
        pos += 1
        size = (size << 7) | (b & 0x7F)
        if not (b & 0x80):
            return size, pos
    return size, pos


def parse_esds(payload):
    """Decode an esds box payload (after its 4-byte version/flags): the
    ES_Descriptor -> DecoderConfigDescriptor -> DecoderSpecificInfo chain.
    Returns {es_id, object_type_indication, stream_type, buffer_size,
    max_bitrate, avg_bitrate, dsi} with whatever was present."""
    out = {}
    pos, end = 0, len(payload)
    while pos < end:
        tag = payload[pos]
        pos += 1
        size, pos = _desc_len(payload, pos, end)
        if size is None:
            break
        body_end = min(pos + size, end)
        if tag == 0x03:                     # ES_Descriptor: descend
            if pos + 3 > body_end:
                break
            out["es_id"] = int.from_bytes(payload[pos:pos + 2], "big")
            flags = payload[pos + 2]
            skip = 3
            if flags & 0x80:                # streamDependenceFlag
                skip += 2
            if flags & 0x40 and pos + skip < body_end:   # URL_Flag
                skip += 1 + payload[pos + skip]
            if flags & 0x20:                # OCRstreamFlag
                skip += 2
            pos += skip
        elif tag == 0x04:                   # DecoderConfigDescriptor: descend
            if pos + 13 > body_end:
                break
            out["object_type_indication"] = payload[pos]
            out["stream_type"] = payload[pos + 1] >> 2
            out["buffer_size"] = int.from_bytes(payload[pos + 2:pos + 5], "big")
            out["max_bitrate"] = int.from_bytes(payload[pos + 5:pos + 9], "big")
            out["avg_bitrate"] = int.from_bytes(payload[pos + 9:pos + 13], "big")
            pos += 13
        elif tag == 0x05:                   # DecoderSpecificInfo (leaf)
            out["dsi"] = bytes(payload[pos:body_end])
            pos = body_end
        else:                               # SLConfig (0x06) and friends
            pos = body_end
    return out or None


def parse_audio_specific_config(dsi):
    """Decode the leading bits of an AudioSpecificConfig (ISO 14496-3):
    audioObjectType (5 bits, escape 31 -> +6 bits), samplingFrequencyIndex
    (4 bits, 15 -> explicit 24-bit rate), channelConfiguration (4 bits),
    plus the extension rate when SBR/PS is signalled explicitly."""
    if len(dsi) < 2:
        return None
    v = int.from_bytes(dsi[:12].ljust(12, b"\0"), "big")
    nbits, pos = 96, 0

    def take(n):
        nonlocal pos
        pos += n
        return (v >> (nbits - pos)) & ((1 << n) - 1)

    aot = take(5)
    if aot == 31:
        aot = 32 + take(6)
    fi = take(4)
    rate = take(24) if fi == 15 else (
        _ASC_RATES[fi] if fi < len(_ASC_RATES) else None)
    out = {"object_type": aot, "sample_rate": rate, "channels": take(4)}
    if aot in (5, 29):                      # explicit SBR/PS: extension rate
        efi = take(4)
        out["ext_sample_rate"] = take(24) if efi == 15 else (
            _ASC_RATES[efi] if efi < len(_ASC_RATES) else None)
    return out


def parse_alac_cookie(payload):
    """Decode an alac box payload (after its 4-byte version/flags): the ALAC
    magic cookie, 24 big-endian bytes of codec parameters."""
    if len(payload) < 24:
        return None
    fl, cv, bits, pb, mb, kb, ch, maxrun = struct.unpack_from(
        ">IBBBBBBH", payload, 0)
    maxframe, avgbr, rate = struct.unpack_from(">III", payload, 12)
    return {"frame_length": fl, "compatible_version": cv, "bit_depth": bits,
            "pb": pb, "mb": mb, "kb": kb, "channels": ch, "max_run": maxrun,
            "max_frame_bytes": maxframe, "avg_bitrate": avgbr,
            "sample_rate": rate}


def parse_dops(payload):
    """Decode a dOps box payload (no version/flags prefix). NB: unlike the
    OpusHead packet in Ogg (little-endian), the ISO-BMFF dOps box is
    big-endian -- same fields, opposite byte order."""
    if len(payload) < 11:
        return None
    ver, ch = payload[0], payload[1]
    pre_skip = struct.unpack_from(">H", payload, 2)[0]
    in_rate = struct.unpack_from(">I", payload, 4)[0]
    gain = struct.unpack_from(">h", payload, 8)[0]
    family = payload[10]
    return {"version": ver, "channels": ch, "pre_skip": pre_skip,
            "input_sample_rate": in_rate, "output_gain_db": gain / 256.0,
            "mapping_family": family}


def sample_entries(data):
    """Enumerate the stsd box's sample entries. Yields dicts {codec, offset,
    size, hdr, depth, version, channels, sample_size, sample_rate, children}
    where children are the entry's own codec-config boxes (esds, alac, dOps,
    and the contents of a QuickTime 'wave' wrapper), each as (type, offset,
    hdr, size)."""
    for b in iter_boxes(data):
        if b["type"] != b"stsd" or b["truncated"]:
            continue
        send = b["offset"] + b["size"]
        if b["offset"] + b["hdr"] + 8 > len(data):
            return
        count = struct.unpack_from(">I", data, b["offset"] + b["hdr"] + 4)[0]
        pos = b["offset"] + b["hdr"] + 8
        for _ in range(min(count, 64)):
            eh = _box_header(data, pos, send, len(data))
            if eh is None:
                return
            codec, ehdr, esize, _ = eh
            entry = {"codec": codec, "offset": pos, "size": esize,
                     "hdr": ehdr, "depth": b["depth"] + 1, "children": []}
            ap = pos + ehdr
            child_start = None
            if ap + 28 <= min(len(data), pos + esize):
                version = struct.unpack_from(">H", data, ap + 8)[0]
                entry["version"] = version
                if version == 2 and ap + 64 <= len(data):
                    rate_f = struct.unpack_from(">d", data, ap + 32)[0]
                    entry["sample_rate"] = int(rate_f) if (
                        math.isfinite(rate_f) and 0 < rate_f < 1e7) else None
                    entry["channels"] = struct.unpack_from(">I", data, ap + 40)[0]
                    entry["sample_size"] = struct.unpack_from(">I", data, ap + 48)[0]
                    child_start = ap + 64
                else:
                    entry["channels"] = struct.unpack_from(">H", data, ap + 16)[0]
                    entry["sample_size"] = struct.unpack_from(">H", data, ap + 18)[0]
                    entry["sample_rate"] = struct.unpack_from(
                        ">I", data, ap + 24)[0] >> 16
                    # v1 appends four u32s (samples/packet etc.) after the rate
                    child_start = ap + 28 + (16 if version == 1 else 0)
            if child_start is not None:
                entry["children"] = _config_children(
                    data, child_start, pos + esize)
            yield entry
            pos += esize


def _config_children(data, start, end, depth=0):
    """The codec-config boxes inside a sample entry, flattening one level of
    QuickTime 'wave' wrapper (which holds frma/mp4a/esds/terminator)."""
    out = []
    pos = start
    while pos + 8 <= end and depth < 3:
        hd = _box_header(data, pos, end, len(data))
        if hd is None:
            break
        btype, hdr, size, _ = hd
        out.append((btype, pos, hdr, size))
        if btype == b"wave":
            out.extend(_config_children(data, pos + hdr, pos + size, depth + 1))
        pos += size
    return out


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


def _decode_freeform(data, tag):
    """Decode a '----' freeform tag box: children are 'mean' (a FullBox whose
    payload is a reverse-DNS namespace, e.g. com.apple.iTunes or
    com.serato.dj), 'name' (a FullBox with the key name), and 'data'. Returns
    (namespace:name, value); the com.apple.iTunes namespace is elided since
    it is the overwhelming default."""
    mean = name = value = None
    tstart = tag["offset"] + tag["hdr"]
    tend = tag["offset"] + tag["size"]
    for c in iter_boxes(data, tstart, tend, depth=tag["depth"] + 1):
        if c["depth"] != tag["depth"] + 1 or c["truncated"]:
            continue
        cs, ce = c["offset"], c["offset"] + c["size"]
        if c["type"] == b"mean" and ce - cs > 12:
            mean = data[cs + 12:ce].decode("utf-8", errors="replace")
        elif c["type"] == b"name" and ce - cs > 12:
            name = data[cs + 12:ce].decode("utf-8", errors="replace")
        elif c["type"] == b"data":
            value = _decode_data_box(data, cs, ce)
    if not name:
        return None, None
    prefix = "" if mean in (None, "com.apple.iTunes") else f"{mean}:"
    return f"{prefix}{name}", value


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
            if tag["type"] == b"----":
                # freeform atom: mean (reverse-DNS namespace) + name + data.
                # Serato, MusicBrainz, and iTunes normalization data live here.
                key, v = _decode_freeform(data, tag)
                if key and v is not None and key not in meta:
                    meta[key] = v
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
