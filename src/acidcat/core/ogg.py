"""Ogg container walker: page structure and the Vorbis/Opus comment header.

An Ogg stream is a sequence of pages, each 'OggS' + a 27-byte header + a segment
table + the segment data. Logical packets span segments (a packet continues
while segment lengths are 255). The metadata lives in the second packet: a
Vorbis comment header ('\\x03vorbis' + vendor + user comments) or Opus tags
('OpusTags' + the same comment layout). All lengths here are little-endian and
bounds-checked, since this parses untrusted files.
"""

import struct

MAGIC = b"OggS"


def is_ogg(data):
    return data[:4] == MAGIC


def iter_pages(data, cap=100000):
    """Yield page dicts for the Ogg stream. Stops on the first malformed page."""
    pos, n, count = 0, len(data), 0
    while pos + 27 <= n and data[pos:pos + 4] == MAGIC and count < cap:
        nseg = data[pos + 26]
        if pos + 27 + nseg > n:
            break
        seg_table = data[pos + 27:pos + 27 + nseg]
        data_len = sum(seg_table)
        data_off = pos + 27 + nseg
        if data_off + data_len > n:
            break
        yield {
            "serial": struct.unpack_from("<I", data, pos + 14)[0],
            "granule": struct.unpack_from("<q", data, pos + 6)[0],
            "seq": struct.unpack_from("<I", data, pos + 18)[0],
            "header_type": data[pos + 5],
            "seg_table": seg_table,
            "data_off": data_off,
            "data_len": data_len,
            "size": 27 + nseg + data_len,
        }
        pos = data_off + data_len
        count += 1


def _first_packets(data, max_pages=6):
    """Reconstruct the leading logical packets (the comment header is early)."""
    seg_lengths, blob = [], bytearray()
    for i, pg in enumerate(iter_pages(data)):
        if i >= max_pages:
            break
        seg_lengths.extend(pg["seg_table"])
        blob += data[pg["data_off"]:pg["data_off"] + pg["data_len"]]
    packets, cur, pos = [], bytearray(), 0
    for seglen in seg_lengths:
        cur += blob[pos:pos + seglen]
        pos += seglen
        if seglen < 255:               # a segment < 255 ends the packet
            packets.append(bytes(cur))
            cur = bytearray()
    return packets


def _decode_vorbis_comment(body):
    """(vendor, {TAG: value}) from a Vorbis-comment body."""
    if len(body) < 4:
        return None, {}
    vlen = struct.unpack_from("<I", body, 0)[0]
    if 4 + vlen + 4 > len(body):
        return None, {}
    vendor = body[4:4 + vlen].decode("utf-8", "replace")
    off = 4 + vlen
    count = struct.unpack_from("<I", body, off)[0]
    off += 4
    tags = {}
    for _ in range(min(count, 10000)):
        if off + 4 > len(body):
            break
        clen = struct.unpack_from("<I", body, off)[0]
        off += 4
        if clen > len(body) - off:
            break
        c = body[off:off + clen].decode("utf-8", "replace")
        off += clen
        if "=" in c:
            k, v = c.split("=", 1)
            tags.setdefault(k.upper(), v)
    return vendor, tags


def identification(data):
    """(codec, {sample_rate, channels}) from the Ogg identification header
    (the first packet), or None. Vorbis and Opus carry the audio params here."""
    packets = _first_packets(data)
    if not packets:
        return None
    p = packets[0]
    if p[:7] == b"\x01vorbis" and len(p) >= 16:
        return "Vorbis", {"channels": p[11],
                          "sample_rate": struct.unpack_from("<I", p, 12)[0]}
    if p[:8] == b"OpusHead" and len(p) >= 16:
        return "Opus", {"channels": p[9],
                        "sample_rate": struct.unpack_from("<I", p, 12)[0]}
    return None


def comment_header(data):
    """(codec, vendor, {TAG: value}) from the Ogg comment header, or None."""
    packets = _first_packets(data)
    if len(packets) < 2:
        return None
    ident, comment = packets[0], packets[1]
    if ident[:7] == b"\x01vorbis":
        if comment[:7] != b"\x03vorbis":
            return "Vorbis", None, {}
        vendor, tags = _decode_vorbis_comment(comment[7:])
        return "Vorbis", vendor, tags
    if ident[:8] == b"OpusHead":
        if comment[:8] != b"OpusTags":
            return "Opus", None, {}
        vendor, tags = _decode_vorbis_comment(comment[8:])
        return "Opus", vendor, tags
    if ident[:5] == b"\x7fFLAC":
        return "FLAC", None, {}
    return None
