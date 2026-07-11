"""FLAC structural walker: field decoding for every metadata block type
plus the audio-frame region. Block iteration lives in core/flac.py."""

import os
import struct

from acidcat.core import flac as flacmod
from acidcat.core.walk.base import _PAYLOAD_CAP, _bu16, _bu32, _f

def _flac_streaminfo(b):
    fields, warns = [], []
    if len(b) < 34:
        return "truncated", fields, [f"STREAMINFO is {len(b)} bytes, spec says 34"]
    min_block, max_block = _bu16(b, 0), _bu16(b, 2)
    min_frame = struct.unpack(">I", b"\x00" + b[4:7])[0]
    max_frame = struct.unpack(">I", b"\x00" + b[7:10])[0]
    packed = struct.unpack_from(">Q", b, 10)[0]
    rate = (packed >> 44) & 0xFFFFF
    channels = ((packed >> 41) & 0x07) + 1
    bits = ((packed >> 36) & 0x1F) + 1
    total = packed & 0xFFFFFFFFF
    md5 = b[18:34].hex()
    fields.append(_f(0x00, 2, "min_block_size", min_block, "samples"))
    fields.append(_f(0x02, 2, "max_block_size", max_block, "samples"))
    fields.append(_f(0x04, 3, "min_frame_size", min_frame, "bytes",
                     enc="u24be", raw=min_frame))
    fields.append(_f(0x07, 3, "max_frame_size", max_frame, "bytes",
                     enc="u24be", raw=max_frame))
    # sample_rate/channels/bits_per_sample/total_samples are bit-packed into the
    # 8-byte word at 0x0A; enc="bits:DELTA:CLEN:BITPOS:WIDTH:BIAS" edits each via a
    # read-modify-write on that word (DELTA back to 0x0A; BIAS -1 where stored -1).
    fields.append(_f(0x0A, 3, "sample_rate", rate, "Hz",
                     enc="bits:0:8:0:20:0", raw=rate))
    fields.append(_f(0x0C, 1, "channels", channels,
                     enc="bits:-2:8:20:3:-1", raw=channels))
    fields.append(_f(0x0D, 1, "bits_per_sample", bits,
                     enc="bits:-3:8:23:5:-1", raw=bits))
    dur = total / rate if rate else 0
    fields.append(_f(0x0D, 5, "total_samples", total,
                     f"{dur:.3f} s at {rate} Hz" if rate else "",
                     enc="bits:-3:8:28:36:0", raw=total))
    fields.append(_f(0x12, 16, "md5_signature",
                     md5 if md5 != "0" * 32 else "0 (unset)"))
    if rate == 0:
        warns.append("sample rate is 0")
    if min_block > max_block:
        warns.append(f"min_block_size {min_block} > max_block_size {max_block}")
    summary = f"{bits}-bit {channels}ch {rate} Hz, {total:,} samples, {dur:.3f} s"
    return summary, fields, warns


def _flac_vorbis_comment(b):
    fields, warns = [], []
    if len(b) < 8:
        return "truncated", fields, ["VORBIS_COMMENT under 8 bytes"]
    # vorbis comment lengths are little-endian, unlike the rest of FLAC
    vlen = struct.unpack_from("<I", b, 0)[0]
    pos = 4 + vlen
    if pos + 4 > len(b):
        return "truncated", fields, ["vendor string overruns block"]
    vendor = b[4:4 + vlen].decode("utf-8", errors="replace")
    fields.append(_f(0x00, vlen, "vendor", vendor[:80]))
    count = struct.unpack_from("<I", b, pos)[0]
    pos += 4
    shown = 0
    for i in range(count):
        if pos + 4 > len(b):
            warns.append(f"declares {count} comments but block ends at {i}")
            break
        clen = struct.unpack_from("<I", b, pos)[0]
        start = pos + 4
        if start + clen > len(b):
            warns.append(f"comment[{i}] overruns block")
            break
        text = b[start:start + clen].decode("utf-8", errors="replace")
        key, _, val = text.partition("=")
        fields.append(_f(pos, 4 + clen, key.upper()[:24], val[:80]))
        pos = start + clen
        shown += 1
    return f"{shown} comment(s), {vendor[:40]}", fields, warns


def _flac_picture(b):
    fields, warns = [], []
    if len(b) < 32:
        return "truncated", fields, ["PICTURE under 32 bytes"]
    ptype = _bu32(b, 0)
    pos = 4
    # validate the declared string lengths before slicing: a forged
    # length would otherwise decode the rest of the block (up to the
    # payload cap) as a garbage mime/description string.
    mlen = _bu32(b, pos)
    if pos + 4 + mlen > len(b):
        return "truncated", fields, [
            f"mime_type length {mlen:,} overruns block"]
    mime = b[pos + 4:pos + 4 + mlen].decode("ascii", errors="replace")
    pos += 4 + mlen
    if pos + 4 > len(b):
        return "truncated", fields, ["PICTURE ends before description length"]
    dlen = _bu32(b, pos)
    if pos + 4 + dlen > len(b):
        return "truncated", fields, [
            f"description length {dlen:,} overruns block"]
    desc = b[pos + 4:pos + 4 + dlen].decode("utf-8", errors="replace")
    pos += 4 + dlen
    if pos + 20 > len(b):
        return "truncated", fields, ["PICTURE header overruns block"]
    width, height, depth, colors, datalen = struct.unpack_from(">IIIII", b, pos)
    types = {0: "other", 3: "front cover", 4: "back cover"}
    fields.append(_f(0x00, 4, "picture_type", ptype, types.get(ptype, "")))
    fields.append(_f(None, 0, "mime_type", mime))
    if desc:
        fields.append(_f(None, 0, "description", desc[:60]))
    fields.append(_f(None, 0, "dimensions", f"{width}x{height}", f"{depth}-bit"))
    fields.append(_f(None, 0, "data_length", f"{datalen:,}", "bytes"))
    return f"{types.get(ptype, 'image')}, {mime}, {width}x{height}", fields, warns


_SEEKPOINT_ROW_CAP = 64


def _flac_seektable(b, block_length=None):
    """SEEKTABLE: 18-byte points of (sample_number u64, byte offset from the
    first frame u64, samples in the target frame u16). sample_number
    0xFFFF... is a placeholder reserved-space point."""
    fields, warns = [], []
    # count from the declared block length, not the (possibly capped) payload
    n = (block_length if block_length is not None else len(b)) // 18
    avail = len(b) // 18
    placeholders = 0
    for i in range(avail):
        base = i * 18
        sample, offset = struct.unpack_from(">QQ", b, base)
        span = struct.unpack_from(">H", b, base + 16)[0]
        if sample == 0xFFFFFFFFFFFFFFFF:
            placeholders += 1
            continue
        if i < _SEEKPOINT_ROW_CAP:
            pf = _f(base, 18, f"point[{i}]",
                    f"sample {sample:,} @ +{offset:,}",
                    f"{span} samples in frame")
            # the byte offset is relative to the first audio frame, which is not
            # known until every metadata block is walked; inspect_flac resolves
            # this to an absolute `xref` (a followable, dangling-checkable pointer)
            pf["_xref_rel"] = offset
            fields.append(pf)
    if avail > _SEEKPOINT_ROW_CAP:
        fields.append(_f(None, 0, "...",
                         f"{avail - _SEEKPOINT_ROW_CAP} more points"))
    if n > avail:
        warns.append(f"table declares {n} points; listing the {avail} within "
                     "the read cap")
    note = f"{placeholders} placeholder" if placeholders else ""
    fields.insert(0, _f(None, 0, "num_points", n, note))
    return f"{n} seek point(s)", fields, warns


def _flac_application(b):
    if len(b) < 4:
        return "truncated", [], ["APPLICATION under 4 bytes"]
    app_id = b[:4].decode("ascii", errors="replace")
    return (f"app '{app_id}', {len(b) - 4:,} bytes",
            [_f(0x00, 4, "application_id", app_id),
             _f(0x04, len(b) - 4, "data", f"{len(b) - 4:,} bytes")], [])


def _flac_cuesheet(b):
    """FLAC CUESHEET (block type 5), RFC 9639 section 8.7. Big-endian.
    396-byte prefix, then per-track 36 bytes + 12 bytes per index point."""
    fields, warns = [], []
    if len(b) < 396:
        return "truncated", fields, [f"CUESHEET is {len(b)} bytes, needs 396"]
    catalog = b[0:128].split(b"\x00")[0].decode("ascii", errors="replace").strip()
    lead_in = struct.unpack_from(">Q", b, 128)[0]
    is_cd = bool(b[136] & 0x80)
    n_tracks = b[395]
    fields.append(_f(0x00, 128, "catalog_number", catalog or "(none)"))
    fields.append(_f(0x80, 8, "lead_in_samples", f"{lead_in:,}"))
    fields.append(_f(0x88, 1, "is_cd", is_cd))
    fields.append(_f(0x18B, 1, "num_tracks", n_tracks))
    pos = 396
    for i in range(n_tracks):
        if pos + 36 > len(b):
            warns.append(f"declares {n_tracks} tracks but payload ends at track {i}")
            break
        offset = struct.unpack_from(">Q", b, pos)[0]
        tnum = b[pos + 8]
        isrc = b[pos + 9:pos + 21].split(b"\x00")[0].decode("ascii", errors="replace").strip()
        ttype = "non-audio" if (b[pos + 21] & 0x80) else "audio"
        preemph = " +pre-emphasis" if (b[pos + 21] & 0x40) else ""
        n_idx = b[pos + 35]
        # the last track is the lead-out: 170 for CD-DA, 255 otherwise.
        lead_out = " (lead-out)" if tnum in (170, 255) else ""
        detail = f"#{tnum}{lead_out}, {ttype}{preemph}, {n_idx} index"
        if isrc:
            detail += f", ISRC {isrc}"
        fields.append(_f(pos, 36 + n_idx * 12, f"track[{i}]",
                         f"offset {offset:,}", detail))
        pos += 36 + n_idx * 12
    summary = f"cue sheet, {n_tracks} track(s)" + (", CD-DA" if is_cd else "")
    return summary, fields, warns


def inspect_flac(filepath):
    """Walk a FLAC file: metadata blocks then the audio-frame region."""
    file_size = os.path.getsize(filepath)
    chunks = []
    file_warns = []
    seen = []
    last_end = 4

    chunks.append({"id": "fLaC", "offset": 0, "size": 4,
                   "summary": "FLAC signature",
                   "fields": [_f(0x00, 4, "magic", "fLaC")], "warnings": [],
                   "payload_base": 0})

    saw_last = False
    for btype, name, off, length, is_last in flacmod.iter_metadata_blocks(filepath):
        seen.append(name)
        last_end = off + 4 + length
        with open(filepath, "rb") as f:
            f.seek(off + 4)
            payload = f.read(min(length, _PAYLOAD_CAP))
        entry = {"id": name, "offset": off, "size": length,
                 "summary": "", "fields": [], "warnings": [],
                 "payload_base": off + 4}  # FLAC block header is 4 bytes
        try:
            if btype == 0:
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _flac_streaminfo(payload)
            elif btype == 4:
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _flac_vorbis_comment(payload)
            elif btype == 6:
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _flac_picture(payload)
            elif btype == 3:
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _flac_seektable(payload, block_length=length)
            elif btype == 2:
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _flac_application(payload)
            elif btype == 1:
                entry["summary"] = f"padding, {length:,} bytes"
            elif btype == 5:
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _flac_cuesheet(payload)
            else:
                entry["summary"] = f"reserved block type {btype}, {length:,} bytes"
        except Exception as e:
            entry["warnings"] = [f"parse error: {e.__class__.__name__}: {e}"]
        if last_end > file_size:
            entry["warnings"].append(
                f"declared length {length:,} overruns the file by "
                f"{last_end - file_size:,} bytes "
                f"(only {max(0, file_size - off - 4):,} present)")
        chunks.append(entry)
        if is_last:
            saw_last = True
            break

    if not saw_last and seen:
        file_warns.append("no block had the last-metadata-block flag set")
    if seen and seen[0] != "STREAMINFO":
        file_warns.append("first metadata block is not STREAMINFO, violating the FLAC spec")
    # data hidden past the terminator: real audio frames begin with the sync
    # code 0xFFF8, so a byte at last_end that instead parses as a metadata-block
    # header (known type, in-bounds length) is a block smuggled after the
    # last-metadata-block flag, which no conformant decoder reads.
    if saw_last and last_end + 4 <= file_size:
        with open(filepath, "rb") as f:
            f.seek(last_end)
            h = f.read(4)
        btype = h[0] & 0x7F
        blen = (h[1] << 16) | (h[2] << 8) | h[3]
        if h[0] != 0xFF and btype <= 6 and 0 < last_end + 4 + blen <= file_size:
            file_warns.append(
                f"a metadata-like block (type {btype}, {blen:,} bytes) follows "
                f"the last-metadata-block flag at 0x{last_end:08x}; conformant "
                f"decoders never read it (data hidden past the block table)")
    # resolve SEEKTABLE point offsets (relative to the first frame) into absolute
    # xref pointers now that last_end -- the first frame -- is known
    for c in chunks:
        if c["id"] != "SEEKTABLE":
            continue
        for fl in c["fields"]:
            if "_xref_rel" in fl:
                target = last_end + fl.pop("_xref_rel")
                fl["xref"] = target
                if not (0 <= target < file_size):
                    file_warns.append(
                        f"SEEKTABLE {fl['name']} points to 0x{target:08x}, "
                        f"outside the file (a dangling seek pointer)")
    audio_bytes = file_size - last_end
    if audio_bytes > 0:
        chunks.append({"id": "frames", "offset": last_end, "size": audio_bytes,
                       "summary": f"audio frames, {audio_bytes:,} bytes (opaque)",
                       "fields": [], "warnings": []})
    return chunks, file_warns
