"""DSF and DSDIFF walkers: the two containers for one-bit audio.

Both were written from their published specifications and then checked against
commercial SACD rips. Both carry an ID3v2 tag, which is why neither needed new
tag code -- `formats.mp3.id3v2_from_bytes` already serves the three containers
that embed one, and this makes five.

Neither file is ever read whole. A DSD track is 200 MB of one-bit stream and
every fact worth reporting lives in the first hundred bytes or the last few
hundred.
"""

import os
import struct

from acidcat.core.formats.dsd import (
    DSDIFF_CHANNEL_IDS, DSDIFF_COMPRESSION, DSF_BLOCK_SIZE,
    DSF_CHANNEL_LAYOUTS, DSF_CHANNEL_TYPES,
    dsd_duration, dsf_header, rate_name,
)
from acidcat.core.primitives.notes import coverage, is_coverage
from acidcat.core.walk.base import _f

# A DSDIFF is IFF and can nest. Real files are a handful of chunks; the bound
# is for a crafted one that claims thousands.
_MAX_CHUNKS = 256
# How much of an embedded tag to read. A DSD tag is a few hundred bytes.
_TAG_CAP = 1 << 20
# Listing bound for the channel table.
_CHANNEL_LIST_CAP = 32


def _id3_fields(payload):
    """An embedded ID3v2 tag, through the reader the other containers use."""
    from acidcat.core.formats import mp3 as mp3mod
    header, frames, warns = mp3mod.id3v2_from_bytes(payload)
    if header is None:
        return [], list(warns)
    fields = [_f(None, 0, "id3_version",
                 f"2.{header['major']}.{header['revision']}")]
    for fid, text in frames[:40]:
        fields.append(_f(None, 0, fid, str(text)[:160]))
    return fields, list(warns)


# ── Sony DSF ────────────────────────────────────────────────────────

def inspect_dsf(filepath, ctx=None):
    """Sony DSF: four blocks, little-endian, no nesting."""
    ctx = ctx if ctx is not None else {}
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        head = f.read(96)
    chunks, file_warns = [], []

    hdr = dsf_header(head)
    if hdr is None:
        return chunks, ["not a readable DSF header"]

    # ── the DSD block: where the file says it ends, and where its tag is ──
    # Field offsets are relative to payload_base, never to the file. The
    # 12-byte block header (magic + size) is not in the payload, so those two
    # are reported unpositioned rather than at a made-up offset.
    dsd_fields = [
        _f(None, 0, "magic", "DSD "),
        _f(None, 0, "chunk_size", 28),
        _f(0x00, 8, "total_size", f"{hdr['total_size']:,}", "bytes",
           enc="<Q", raw=hdr["total_size"]),
        _f(0x08, 8, "metadata_offset", f"{hdr['metadata_offset']:,}",
           "0 when the file carries no tag", enc="<Q",
           raw=hdr["metadata_offset"]),
    ]
    dsd_warns = []
    # the file states its own length, so it can be wrong about it -- and a DSF
    # that is wrong here is the same class of damage as a RIFF with a bad size
    if hdr["total_size"] != file_size:
        dsd_warns.append(
            f"total_size says {hdr['total_size']:,} bytes, file is "
            f"{file_size:,} ({hdr['total_size'] - file_size:+,})")
    if hdr["metadata_offset"] > file_size:
        dsd_warns.append(
            f"metadata_offset {hdr['metadata_offset']:,} is past the end of "
            f"the file ({file_size:,})")
    chunks.append({"id": "DSD ", "offset": 0, "size": 28,
                   "summary": f"{hdr['total_size']:,} bytes total",
                   "fields": dsd_fields, "warnings": dsd_warns,
                   "payload_base": 12, "payload_len": 16, "extent_len": 28})

    # ── the fmt block ────────────────────────────────────────────────
    rate = hdr["sample_rate"]
    ctype = hdr["channel_type"]
    chans = hdr["channels"]
    bits = hdr["bits_per_sample"]
    layout = DSF_CHANNEL_LAYOUTS.get(ctype)
    fmt_fields = [
        _f(None, 0, "magic", "fmt "),
        _f(None, 0, "chunk_size", hdr["fmt_size"]),
        _f(0x00, 4, "format_version", hdr["version"],
           "" if hdr["version"] == 1 else "only version 1 is defined"),
        _f(0x04, 4, "format_id", hdr["format_id"],
           "DSD raw" if hdr["format_id"] == 0 else "undefined"),
        _f(0x08, 4, "channel_type", ctype,
           DSF_CHANNEL_TYPES.get(ctype, "undefined")),
        _f(0x0C, 4, "channels", chans,
           ", ".join(layout) if layout else ""),
        _f(0x10, 4, "sample_rate", f"{rate:,}", rate_name(rate),
           enc="<I", raw=rate),
        _f(0x14, 4, "bits_per_sample", bits,
           "one bit per sample is the whole point of DSD"
           if bits == 1 else "DSD defines 1 and 8"),
        _f(0x18, 8, "sample_count", f"{hdr['sample_count']:,}",
           "per channel", enc="<Q", raw=hdr["sample_count"]),
        _f(0x20, 4, "block_size", hdr["block_size"],
           "bytes per channel; the last block is ZERO-PADDED, not short"),
        _f(0x24, 4, "reserved", hdr["reserved"]),
    ]
    fmt_warns = []
    if hdr["version"] != 1:
        fmt_warns.append(f"format_version is {hdr['version']}; only 1 is defined")
    if hdr["format_id"] != 0:
        fmt_warns.append(f"format_id is {hdr['format_id']}; only 0 (DSD raw) "
                         f"is defined")
    if bits not in (1, 8):
        fmt_warns.append(f"bits_per_sample is {bits}; DSD defines 1 and 8")
    if layout and len(layout) != chans:
        fmt_warns.append(
            f"channel_type {ctype} is {DSF_CHANNEL_TYPES.get(ctype, '?')} "
            f"({len(layout)} channels) but channels says {chans}")
    if rate and rate not in (2822400, 5644800, 11289600, 22579200, 45158400,
                             3072000, 6144000, 12288000):
        fmt_warns.append(f"sample_rate {rate:,} is not a defined DSD rate")
    if hdr["block_size"] != DSF_BLOCK_SIZE:
        fmt_warns.append(f"block_size is {hdr['block_size']}, not the "
                         f"{DSF_BLOCK_SIZE} the spec fixes")
    if hdr["reserved"]:
        fmt_warns.append("the reserved field is not zero")

    dur = dsd_duration(hdr["sample_count"], rate)
    if dur:
        fmt_fields.append(_f(None, 0, "duration", f"{dur:.3f} s"))
    summary = (f"{rate_name(rate) or f'{rate:,} Hz'} {bits}-bit "
               f"{chans}ch {DSF_CHANNEL_TYPES.get(ctype, '')}").strip()
    if dur:
        summary += f", {dur:.1f} s"
    chunks.append({"id": "fmt ", "offset": 28, "size": hdr["fmt_size"],
                   "summary": summary, "fields": fmt_fields,
                   "warnings": fmt_warns, "payload_base": 40,
                   "payload_len": max(0, hdr["fmt_size"] - 12),
                   "extent_len": hdr["fmt_size"]})
    ctx.update({"sample_rate": rate, "channels": chans, "bits": bits,
                "frames": hdr["sample_count"], "duration": dur})

    # ── the data block ───────────────────────────────────────────────
    # fmt_size is read FROM the file, so a damaged or crafted one can be any
    # 64-bit number, and an offset built from it goes straight into seek().
    # Python raises "cannot fit 'int' into an offset-sized integer" there,
    # which is a crash rather than a finding -- the fuzzer found it in one
    # pass. Anything past the end of the file is not an offset.
    data_off = 28 + hdr["fmt_size"]
    if not (0 < data_off <= file_size):
        file_warns.append(
            f"fmt chunk_size {hdr['fmt_size']:,} puts the data block at "
            f"0x{data_off:x}, outside a {file_size:,}-byte file")
        for entry in chunks:
            file_warns.extend(w for w in entry["warnings"] if is_coverage(w))
        return chunks, file_warns
    with open(filepath, "rb") as f:
        f.seek(data_off)
        dh = f.read(12)
    if len(dh) >= 12 and dh[:4] == b"data":
        dsize = struct.unpack_from("<Q", dh, 4)[0]
        dwarns = []
        # the payload is n + 12 by the spec's own arithmetic, so the audio is
        # dsize - 12 bytes, and one BIT per sample means the byte count is
        # eight times smaller than a PCM reader would assume
        audio = max(0, dsize - 12)
        expect = (hdr["sample_count"] // 8) * chans if chans else 0
        dfields = [
            _f(None, 0, "magic", "data"),
            _f(None, 0, "chunk_size", f"{dsize:,}",
               "counts the 12-byte header with the samples", enc="<Q",
               raw=dsize),
            _f(None, 0, "audio_bytes", f"{audio:,}",
               f"{audio * 8 // max(chans, 1):,} bits per channel"),
        ]
        if data_off + dsize > file_size:
            dwarns.append(
                f"data claims {dsize:,} bytes at 0x{data_off:x} but only "
                f"{file_size - data_off:,} remain (file is truncated)")
        elif expect and abs(audio - expect) > DSF_BLOCK_SIZE * chans:
            # one bit per sample: bytes = samples/8 per channel, rounded up to
            # the fixed block. A gap wider than one block on every channel
            # means the count and the bytes disagree about something real.
            dwarns.append(
                f"data holds {audio:,} bytes; {hdr['sample_count']:,} samples "
                f"at 1 bit across {chans} channel(s) is {expect:,}")
        chunks.append({"id": "data", "offset": data_off, "size": dsize,
                       "summary": f"{audio:,} bytes of one-bit stream",
                       "fields": dfields, "warnings": dwarns,
                       "payload_base": data_off + 12,
                       "payload_len": audio, "extent_len": dsize})
    else:
        file_warns.append(f"no data block at 0x{data_off:x}")

    # ── the tag, which the header points at ──────────────────────────
    meta = hdr["metadata_offset"]
    if meta and meta < file_size:
        with open(filepath, "rb") as f:
            f.seek(meta)
            tag = f.read(min(_TAG_CAP, file_size - meta))
        fields, warns = _id3_fields(tag)
        chunks.append({"id": "ID3 ", "offset": meta, "size": len(tag),
                       "summary": (f"ID3v2 tag, {len(tag):,} bytes"
                                   if fields else "not an ID3v2 tag"),
                       "fields": fields, "warnings": warns,
                       "payload_base": meta, "payload_len": len(tag),
                       "extent_len": len(tag)})

    for entry in chunks:
        file_warns.extend(w for w in entry["warnings"] if is_coverage(w))
    return chunks, file_warns


# ── Philips DSDIFF ──────────────────────────────────────────────────

def _dsdiff_prop(payload):
    """The PROP chunk's local chunks: rate, channels, compression, start."""
    fields, warns = [], []
    if len(payload) < 4:
        return "truncated", fields, ["PROP is under 4 bytes"]
    ptype = payload[:4]
    fields.append(_f(0x00, 4, "property_type", ptype.decode("latin-1"),
                     "sound properties" if ptype == b"SND " else "unknown"))
    bits = []
    pos, n = 4, 0
    while pos + 12 <= len(payload) and n < _MAX_CHUNKS:
        cid = payload[pos:pos + 4]
        size = struct.unpack_from(">Q", payload, pos + 4)[0]
        body = payload[pos + 12:pos + 12 + size]
        n += 1
        if cid == b"FS  " and len(body) >= 4:
            rate = struct.unpack_from(">I", body, 0)[0]
            # the field is the rate word itself, after the local chunk's
            # 12-byte header: spanning the whole local chunk, its >I enc
            # described 16 bytes and could never be verified or edited
            fields.append(_f(pos + 12, 4, "sample_rate", f"{rate:,}",
                             rate_name(rate), enc=">I", raw=rate))
            bits.append(rate_name(rate) or f"{rate:,} Hz")
        elif cid == b"CHNL" and len(body) >= 2:
            count = struct.unpack_from(">H", body, 0)[0]
            names = []
            for i in range(min(count, _CHANNEL_LIST_CAP)):
                off = 2 + i * 4
                if off + 4 > len(body):
                    break
                ident = body[off:off + 4]
                names.append(DSDIFF_CHANNEL_IDS.get(ident,
                                                    ident.decode("latin-1")))
            fields.append(_f(pos, 12 + size, "channels", count,
                             ", ".join(names)))
            if count > _CHANNEL_LIST_CAP:
                warns.append(coverage(f"listing the first {_CHANNEL_LIST_CAP} "
                                      f"of {count} channels"))
            bits.append(f"{count}ch")
        elif cid == b"CMPR" and len(body) >= 5:
            ident = body[:4]
            nlen = body[4]
            text = body[5:5 + nlen].decode("latin-1", "replace")
            fields.append(_f(pos, 12 + size, "compression",
                             ident.decode("latin-1"),
                             DSDIFF_COMPRESSION.get(ident, text)))
            bits.append(DSDIFF_COMPRESSION.get(ident, text))
        elif cid == b"ABSS" and len(body) >= 8:
            h, m, s, smp = struct.unpack_from(">HBBI", body, 0)
            fields.append(_f(pos, 12 + size, "absolute_start",
                             f"{h:02d}:{m:02d}:{s:02d} + {smp:,} samples",
                             "where this file sits in the original recording"))
        elif cid == b"LSCO" and len(body) >= 2:
            cfg = struct.unpack_from(">H", body, 0)[0]
            fields.append(_f(pos, 12 + size, "loudspeaker_config", cfg,
                             {0: "stereo", 3: "5 channels",
                              4: "5.1 channels"}.get(cfg, "")))
        elif cid == b"ID3 ":
            sub, subw = _id3_fields(body)
            fields.extend(sub)
            warns.extend(subw)
        else:
            fields.append(_f(pos, 12 + size, cid.decode("latin-1"),
                             f"{size:,} bytes"))
        pos += 12 + size + (size & 1)
    return ", ".join(bits) if bits else "sound properties", fields, warns


# Comment types, from the spec's own table. Type 3 is the one that matters in
# practice: a File History comment is where a ripper writes its own name.
_COMMENT_TYPES = {
    0: "general (album)", 1: "channel", 2: "sound source", 3: "file history",
}
# For a sound-source comment the reference is not a channel number.
_SOUND_SOURCE = {0: "DSD recording", 1: "analogue recording"}
# Marker types. TrackStart and TrackStop are how an edited master carves a
# continuous stream into tracks, which is what an SACD actually stores.
_MARK_TYPES = {
    0: "TrackStart", 1: "TrackStop", 2: "ProgramStart", 3: "obsolete",
    4: "Index",
}

# Listing bounds. A real file carries a handful of each.
_COMMENT_CAP = 32
_MARKER_CAP = 64


def _pstring(body, pos):
    """A DSDIFF counted string: a u32 length then that many bytes.

    Returns the text and the offset AFTER its pad byte. The pad is decided by
    the STRING LENGTH being odd, which is what the spec says and what
    MediaInfoLib does (`if (count%2)`). Deciding it from the absolute offset
    instead happens to agree while every record starts on an even boundary,
    and stops agreeing the moment one does not.
    """
    if pos + 4 > len(body):
        return "", pos
    n = struct.unpack_from(">I", body, pos)[0]
    text = body[pos + 4:pos + 4 + n].decode("latin-1", "replace")
    return text.rstrip("\x00"), pos + 4 + n + (n & 1)


def _dsdiff_comt(payload, chans):
    """The comments chunk: a count, then timestamped typed strings.

    This is where a ripper signs its work. A file-history comment carries the
    tool's name and version and the date it ran, which makes COMT a provenance
    record rather than a free-text field -- and the reason it is decoded rather
    than counted.
    """
    fields, warns = [], []
    if len(payload) < 2:
        return "truncated", fields, ["COMT is under 2 bytes"]
    count = struct.unpack_from(">H", payload, 0)[0]
    fields.append(_f(0x00, 2, "comments", count))
    pos, shown, bits = 2, 0, []
    while shown < min(count, _COMMENT_CAP) and pos + 14 <= len(payload):
        # year u16, month/day/hour/minute u8, cmtType u16, cmtRef u16 = TEN
        # bytes, and then the counted string. Reading the count two bytes late
        # sliced the first two characters off every comment and ran the text
        # into the next record: "terial ripped from SACD" for "Material...".
        year, month, day, hour, minute, ctype, cref = \
            struct.unpack_from(">HBBBBHH", payload, pos)
        text, end = _pstring(payload, pos + 10)
        kind = _COMMENT_TYPES.get(ctype, f"type {ctype}")
        if ctype == 2:
            kind += f" ({_SOUND_SOURCE.get(cref, cref)})"
        elif ctype == 1:
            kind += f" (channel {cref})" if cref else " (all channels)"
        stamp = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
        fields.append(_f(pos, end - pos, f"comment[{shown}]",
                         text[:200], f"{kind}, {stamp}"))
        if text:
            bits.append(text[:40])
        pos = end                      # _pstring stepped over the pad
        shown += 1
    # Two different facts, and the first draft reported only one of them: a
    # chunk declaring 30,840 comments in seven bytes got "listing the first 32
    # of 30,840", which claimed thirty-two listings that never happened. How
    # many were READ is the truncation finding; how many were LISTED is the
    # coverage note, and a damaged chunk earns both.
    if shown < min(count, _COMMENT_CAP):
        warns.append(f"declares {count} comments, {shown} fit in the chunk")
    if count > _COMMENT_CAP and shown >= _COMMENT_CAP:
        warns.append(coverage(f"listing the first {_COMMENT_CAP} of {count} "
                              f"comments"))
    return (" | ".join(bits) if bits else f"{count} comment(s)"), fields, warns


def _dsdiff_diin(payload):
    """Edited master information: the artist, the title, and the track marks.

    An SACD is authored as one continuous stream and carved into tracks by
    MARK chunks, so this is where an edited master says what it is. DIAR and
    DITI are the artist and title -- plain counted strings, and metadata that
    was being reported as a byte count.
    """
    fields, warns = [], []
    pos, n, marks, bits = 0, 0, 0, []
    while pos + 12 <= len(payload) and n < _MAX_CHUNKS:
        cid = payload[pos:pos + 4]
        size = struct.unpack_from(">Q", payload, pos + 4)[0]
        body = payload[pos + 12:pos + 12 + size]
        n += 1
        if cid == b"DIAR":
            text, _ = _pstring(body, 0)
            fields.append(_f(pos, 12 + size, "artist", text[:160]))
            bits.append(text[:40])
        elif cid == b"DITI":
            text, _ = _pstring(body, 0)
            fields.append(_f(pos, 12 + size, "title", text[:160]))
            bits.append(text[:40])
        elif cid == b"EMID":
            fields.append(_f(pos, 12 + size, "edited_master_id",
                             body.hex(" ")[:60]))
        elif cid == b"MARK" and len(body) >= 20:
            marks += 1
            if marks <= _MARKER_CAP:
                h, m, sec, smp, off, mtype, mchan, flags = \
                    struct.unpack_from(">HBBIiHHH", body, 0)
                text, _ = _pstring(body, 18)
                fields.append(_f(
                    pos, 12 + size, f"marker[{marks - 1}]",
                    f"{h:02d}:{m:02d}:{sec:02d} + {smp:,}",
                    f"{_MARK_TYPES.get(mtype, f'type {mtype}')}"
                    + (f", channel {mchan}" if mchan else ", all channels")
                    + (f", {text[:40]}" if text else "")))
        else:
            fields.append(_f(pos, 12 + size, cid.decode("latin-1"),
                             f"{size:,} bytes"))
        pos += 12 + size + (size & 1)
    if marks > _MARKER_CAP:
        warns.append(coverage(f"listing the first {_MARKER_CAP} of {marks} "
                              f"markers"))
    if marks:
        bits.append(f"{marks} marker(s)")
    return (", ".join(bits) if bits else f"{n} chunk(s)"), fields, warns


def _dst_sound(payload, size, ctx):
    """The DST sound chunk, which is a container rather than a blob.

    `FRTE` states how many DST frames there are and how many run per second,
    and those two numbers are the only place a DST file says how long it is:
    the sample count lives in the DSD chunk a compressed file does not have.
    Frames follow as `DSTF`, each optionally trailed by a `DSTC` checksum.

    The frames are not decoded. DST is a real codec with its own arithmetic
    coder, and naming it is not the same as reading it.
    """
    fields, warns = [], []
    frames = rate = None
    counted = 0
    pos, n = 0, 0
    while pos + 12 <= len(payload) and n < _MAX_CHUNKS:
        cid = payload[pos:pos + 4]
        csize = struct.unpack_from(">Q", payload, pos + 4)[0]
        body = payload[pos + 12:pos + 12 + csize]
        n += 1
        if cid == b"FRTE" and len(body) >= 6:
            frames, rate = struct.unpack_from(">IH", body, 0)
            fields.append(_f(pos, 12 + csize, "frames", f"{frames:,}"))
            fields.append(_f(pos + 12 + 4, 2, "frame_rate", rate,
                             "DST frames per second"))
            if rate:
                fields.append(_f(None, 0, "duration",
                                 f"{frames / rate:.3f} s",
                                 "frames over frame rate: a compressed file "
                                 "has no sample count to divide"))
        elif cid == b"DSTF":
            counted += 1
        pos += 12 + csize + (csize & 1)
        if csize == 0 and cid != b"DSTF":
            break
    if frames is None:
        warns.append("no FRTE chunk: a DST stream states its length there and "
                     "nowhere else")
    elif counted and counted != frames:
        # only trustworthy when the whole chunk was read; a capped read sees
        # fewer frames than the file holds and that is not a finding
        if len(payload) >= size:
            warns.append(f"FRTE declares {frames:,} frames, {counted:,} DSTF "
                         f"chunks follow")
    if n >= _MAX_CHUNKS:
        warns.append(coverage(f"stopped after {_MAX_CHUNKS} DST chunks"))
    if frames and rate and ctx is not None:
        ctx.setdefault("duration", frames / rate)
    summary = f"DST lossless, {size:,} bytes"
    if frames:
        summary += f", {frames:,} frames"
        if rate:
            summary += f" at {rate}/s"
    return summary, fields, warns


def inspect_dsdiff(filepath, ctx=None):
    """Philips DSDIFF: IFF with 64-bit sizes, big-endian throughout."""
    ctx = ctx if ctx is not None else {}
    file_size = os.path.getsize(filepath)
    chunks, file_warns = [], []
    with open(filepath, "rb") as f:
        head = f.read(16)
        if len(head) < 16:
            return chunks, ["file is shorter than a FRM8 header"]
        declared = struct.unpack_from(">Q", head, 4)[0]
        # FRM8 counts everything after its own 12-byte header, so the file is
        # declared + 12. Wave64 counts its header IN, which is exactly the
        # kind of disagreement that makes one reader wrong about the other.
        if declared + 12 != file_size:
            file_warns.append(
                f"FRM8 size says {declared:,} bytes ({declared + 12:,} with "
                f"its header), file is {file_size:,} "
                f"({declared + 12 - file_size:+,})")
        chunks.append({
            "id": "FRM8", "offset": 0, "size": declared,
            "summary": f"DSDIFF, {declared:,} bytes",
            # offsets from payload_base (12): the id and size are header
            # fields before it, the form type is the payload's first word
            "fields": [_f(-12, 4, "magic", "FRM8",
                          "IFF with 64-bit sizes"),
                       _f(-8, 8, "size", f"{declared:,}",
                          "counts everything after this 12-byte header",
                          enc=">Q", raw=declared),
                       _f(0, 4, "form_type",
                          head[12:16].decode("latin-1"))],
            "warnings": [], "payload_base": 12, "payload_len": declared,
            "extent_len": 12 + declared})

        pos, n = 16, 0
        while pos + 12 <= file_size and n < _MAX_CHUNKS:
            f.seek(pos)
            ch = f.read(12)
            if len(ch) < 12:
                break
            cid = ch[0:4]
            size = struct.unpack_from(">Q", ch, 4)[0]
            n += 1
            entry = {"id": cid.decode("latin-1", "replace"),
                     "offset": pos, "size": size, "summary": "",
                     "fields": [], "warnings": [],
                     "payload_base": pos + 12, "payload_len": size,
                     "extent_len": 12 + size}
            if pos + 12 + size > file_size:
                entry["warnings"].append(
                    f"claims {size:,} bytes but only "
                    f"{max(0, file_size - pos - 12):,} remain")
            read = min(size, _TAG_CAP)
            payload = f.read(read) if read else b""
            try:
                if cid == b"FVER" and len(payload) >= 4:
                    a, b, c, d = payload[:4]
                    entry["fields"] = [_f(0x00, 4, "version",
                                          f"{a}.{b}.{c}.{d}")]
                    entry["summary"] = f"DSDIFF version {a}.{b}"
                    if (a, b) != (1, 5):
                        entry["warnings"].append(
                            f"version {a}.{b}; the published spec is 1.5")
                elif cid == b"PROP":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _dsdiff_prop(payload)
                elif cid == b"ID3 ":
                    entry["fields"], entry["warnings"] = _id3_fields(payload)
                    entry["summary"] = f"ID3v2 tag, {size:,} bytes"
                elif cid == b"DSD ":
                    entry["summary"] = f"raw one-bit stream, {size:,} bytes"
                    entry["fields"] = [_f(None, 0, "audio_bytes", f"{size:,}")]
                elif cid == b"DST ":
                    # DST NESTS. The sound chunk is a container: an FRTE that
                    # counts the frames, then one DSTF per frame with an
                    # optional DSTC checksum beside it. Reporting only the
                    # outer size would say how big the compressed audio is and
                    # nothing about how much audio that is.
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _dst_sound(payload, size, ctx)
                elif cid == b"COMT":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _dsdiff_comt(payload, ctx.get("channels"))
                elif cid == b"DIIN":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _dsdiff_diin(payload)
                elif cid == b"DSTI":
                    # the DST index: one 12-byte entry per frame, giving its
                    # offset and length. Counted rather than listed -- the
                    # offsets are a seek table, and reading one back is the
                    # job of a player rather than of a structural walk.
                    entries = size // 12
                    entry["summary"] = f"DST index, {entries:,} entries"
                    entry["fields"] = [_f(None, 0, "entries", f"{entries:,}",
                                          "one per DST frame: offset and "
                                          "length, for seeking")]
                    if size % 12:
                        entry["warnings"].append(
                            f"{size:,} bytes is not a whole number of 12-byte "
                            f"index entries")
                elif cid == b"MANF":
                    entry["summary"] = (
                        f"manufacturer-specific, {size:,} bytes")
                    entry["fields"] = [
                        _f(0x00, 4, "manufacturer",
                           payload[:4].decode("latin-1", "replace")
                           if len(payload) >= 4 else ""),
                        _f(None, 0, "bytes", f"{size:,}")]
                else:
                    entry["summary"] = f"{size:,} bytes"
            except Exception as e:                      # noqa: BLE001
                entry["warnings"].append(
                    f"parse error: {e.__class__.__name__}: {e}")
            chunks.append(entry)
            step = 12 + size + (size & 1)               # IFF pad rule, kept
            if step <= 12:
                break
            pos += step

    if n >= _MAX_CHUNKS:
        file_warns.append(coverage(f"stopped after {_MAX_CHUNKS} chunks"))
    for entry in chunks:
        file_warns.extend(w for w in entry["warnings"] if is_coverage(w))
    # carry the sound properties out for the scan row
    for entry in chunks:
        if entry["id"] == "PROP":
            for fld in entry["fields"]:
                if fld["name"] == "sample_rate":
                    ctx["sample_rate"] = fld.get("raw")
                elif fld["name"] == "channels":
                    ctx["channels"] = fld["value"]
    return chunks, file_warns
