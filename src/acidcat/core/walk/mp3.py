"""MP3 structural walker: ID3v2/ID3v1 tags, the MPEG frame run, and the
Xing/LAME/VBRI first-frame headers. Frame and tag primitives live in
core/mp3.py; this module shapes them into the chunk model."""

import os
import struct

from acidcat.core import mp3 as mp3mod
from acidcat.core.walk.base import (
    _FRAME_LISTING_CAP, _ID3_READ_CAP, _bu16, _bu32, _f,
)

_ID3_TEXT_FRAMES = {
    "TIT2": "title", "TPE1": "artist", "TALB": "album", "TCON": "genre",
    "TBPM": "bpm", "TKEY": "initial key", "TYER": "year", "TDRC": "year",
    "TRCK": "track", "TSSE": "encoder settings", "TENC": "encoded by",
    "COMM": "comment", "TPE2": "album artist", "TPE3": "conductor",
    "TCOM": "composer", "TPOS": "disc", "TPUB": "publisher",
    "TCOP": "copyright", "TSRC": "ISRC", "TEXT": "lyricist",
    "TCMP": "compilation", "TIT1": "grouping", "TIT3": "subtitle",
}

# ID3v2.2 used 3-character frame ids. without this map a v2.2 tag lists frame
# sizes but never decodes its title/artist/etc.
_ID3V22_TEXT_FRAMES = {
    "TT2": "title", "TP1": "artist", "TAL": "album", "TCO": "genre",
    "TBP": "bpm", "TKE": "initial key", "TYE": "year", "TRK": "track",
    "TSS": "encoder settings", "TEN": "encoded by", "COM": "comment",
}

_VBR_METHODS = {
    0: "unknown", 1: "CBR", 2: "ABR", 3: "VBR (rh)", 4: "VBR (mtrh)",
    5: "VBR (rh2)", 6: "VBR (constrained)",
}

# ID3v1 genre index -> name: 0-79 the original spec, 80-191 the Winamp
# extensions. 255 (and anything past the table) is "none/unknown".
_ID3_GENRES = [
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge",
    "Hip-Hop", "Jazz", "Metal", "New Age", "Oldies", "Other", "Pop", "R&B",
    "Rap", "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska",
    "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient",
    "Trip-Hop", "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical",
    "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel", "Noise",
    "AlternRock", "Bass", "Soul", "Punk", "Space", "Meditative",
    "Instrumental Pop", "Instrumental Rock", "Ethnic", "Gothic", "Darkwave",
    "Techno-Industrial", "Electronic", "Pop-Folk", "Eurodance", "Dream",
    "Southern Rock", "Comedy", "Cult", "Gangsta", "Top 40", "Christian Rap",
    "Pop/Funk", "Jungle", "Native American", "Cabaret", "New Wave",
    "Psychadelic", "Rave", "Showtunes", "Trailer", "Lo-Fi", "Tribal",
    "Acid Punk", "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll",
    "Hard Rock", "Folk", "Folk-Rock", "National Folk", "Swing", "Fast Fusion",
    "Bebob", "Latin", "Revival", "Celtic", "Bluegrass", "Avantgarde",
    "Gothic Rock", "Progressive Rock", "Psychedelic Rock", "Symphonic Rock",
    "Slow Rock", "Big Band", "Chorus", "Easy Listening", "Acoustic", "Humour",
    "Speech", "Chanson", "Opera", "Chamber Music", "Sonata", "Symphony",
    "Booty Bass", "Primus", "Porn Groove", "Satire", "Slow Jam", "Club",
    "Tango", "Samba", "Folklore", "Ballad", "Power Ballad", "Rhythmic Soul",
    "Freestyle", "Duet", "Punk Rock", "Drum Solo", "A capella", "Euro-House",
    "Dance Hall", "Goa", "Drum & Bass", "Club-House", "Hardcore", "Terror",
    "Indie", "BritPop", "Negerpunk", "Polsk Punk", "Beat",
    "Christian Gangsta Rap", "Heavy Metal", "Black Metal", "Crossover",
    "Contemporary Christian", "Christian Rock", "Merengue", "Salsa",
    "Thrash Metal", "Anime", "Jpop", "Synthpop",
]


def _lame_replaygain(word):
    """Decode a LAME 16-bit replay-gain word; None if unset (0x0000)."""
    if word == 0:
        return None
    name = (word >> 13) & 0x07
    sign = (word >> 9) & 0x01
    mag = word & 0x1FF
    db = (-1 if sign else 1) * mag / 10.0
    kind = {1: "radio", 2: "audiophile"}.get(name, "")
    return f"{db:+.1f} dB" + (f" ({kind})" if kind else "")


def _id3v1_fields(tag):
    """Decode a 128-byte ID3v1/v1.1 trailer into display fields; also
    return the title for the chunk summary."""
    def s(a, b):
        return tag[a:b].decode("latin-1", errors="replace").split("\x00")[0].rstrip("\x00 ")
    fields = [
        _f(0x03, 30, "title", s(3, 33)),
        _f(0x21, 30, "artist", s(33, 63)),
        _f(0x3F, 30, "album", s(63, 93)),
        _f(0x5D, 4, "year", s(93, 97)),
    ]
    # ID3v1.1: byte 125 is zero and byte 126 (track) is nonzero, so the
    # comment is only 28 bytes. Otherwise it is a full 30-byte v1.0 comment.
    if tag[125] == 0 and tag[126] != 0:
        fields.append(_f(0x61, 28, "comment", s(97, 125)))
        fields.append(_f(0x7E, 1, "track", tag[126]))
    else:
        fields.append(_f(0x61, 30, "comment", s(97, 127)))
    g = tag[127]
    gname = _ID3_GENRES[g] if g < len(_ID3_GENRES) else ("none" if g == 255
                                                          else f"unknown {g}")
    fields.append(_f(0x7F, 1, "genre", g, gname))
    return fields, s(3, 33)


def _decode_id3_text(raw):
    """Decode an ID3v2 text-frame payload (leading encoding byte)."""
    if not raw:
        return ""
    enc = raw[0]
    body = raw[1:]
    codecs = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}
    try:
        text = body.decode(codecs.get(enc, "latin-1"), errors="replace")
    except Exception:
        text = body.decode("latin-1", errors="replace")
    return text.replace("\x00", " ").strip()


def _decode_txxx(raw, fid):
    """Decode a user-defined TXXX/WXXX frame as 'description = value'. TXXX is
    [enc][description NUL][value]; WXXX's value is always latin-1 (a URL)."""
    if not raw:
        return ""
    enc = raw[0]
    body = raw[1:]
    codecs = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}
    codec = codecs.get(enc, "latin-1")
    sep = b"\x00\x00" if enc in (1, 2) else b"\x00"
    idx = body.find(sep)
    if idx < 0:
        desc, val = body, b""
    else:
        desc, val = body[:idx], body[idx + len(sep):]
    d = desc.decode(codec, "replace").strip()
    vcodec = "latin-1" if fid == "WXXX" else codec
    v = val.decode(vcodec, "replace").replace("\x00", " ").strip()
    return f"{d} = {v}" if d else v


def _id3v2_frames(filepath, hdr):
    """Enumerate ID3v2 frames into display fields. Decodes common text
    frames to their values; lists every frame id and size otherwise."""
    fields, warns = [], []
    major = hdr["major"]
    tag_size = hdr["size"]
    with open(filepath, "rb") as f:
        f.seek(10)
        body = f.read(min(tag_size, _ID3_READ_CAP))
    fields.append(_f(0x03, 1, "version", f"2.{major}.{hdr['revision']}"))
    flags = hdr["flags"]
    flag_bits = []
    if flags & 0x80:
        flag_bits.append("unsync")
    if flags & 0x40:
        flag_bits.append("extended header")
    if flags & 0x20:
        flag_bits.append("experimental")
    if flags & 0x10:
        flag_bits.append("footer")
    fields.append(_f(0x05, 1, "flags", f"0x{flags:02x}",
                     ", ".join(flag_bits) if flag_bits else "none",
                     enc="B", raw=flags))
    fields.append(_f(0x06, 4, "tag_size", f"{hdr['size']:,}", "synchsafe"))

    is_v22 = major == 2
    id_len = 3 if is_v22 else 4
    fhdr_len = 6 if is_v22 else 10

    # whole-tag unsynchronisation (flag bit 7) inserts a $00 after every $FF so
    # a frame body cannot masquerade as a frame sync. undo it before reading
    # sizes, or every size past the first $FF byte is wrong. this is a v2.2/v2.3
    # construct: in v2.4 unsync is per-frame and the frame size is the on-disk
    # length, so a global de-escape there would misalign every later frame.
    if flags & 0x80 and major != 4:
        body = body.replace(b"\xff\x00", b"\xff")
        warns.append("tag is unsynchronised; byte offsets shown are logical "
                     "(post-desync), not raw file positions")

    pos = 0
    # skip the extended header (flag bit 6) so it is not misread as a frame.
    if flags & 0x40 and not is_v22 and len(body) >= 4:
        if major == 4:
            ext_size = mp3mod.synchsafe(body[0:4])       # v2.4: size includes itself
        else:
            ext_size = struct.unpack(">I", body[0:4])[0] + 4  # v2.3: excludes the 4
        if 0 < ext_size <= len(body):
            fields.append(_f(10, ext_size, "extended_header",
                             f"{ext_size} bytes", "skipped"))
            pos = ext_size
    while pos + fhdr_len <= len(body):
        fid = body[pos:pos + id_len]
        if fid[0] == 0:  # padding
            break
        fid_s = fid.decode("ascii", errors="replace")
        if is_v22:
            fsize = struct.unpack(">I", b"\x00" + body[pos + 3:pos + 6])[0]
        elif major == 4:
            fsize = mp3mod.synchsafe(body[pos + 4:pos + 8])
        else:
            fsize = struct.unpack(">I", body[pos + 4:pos + 8])[0]
        data_start = pos + fhdr_len
        if data_start + fsize > tag_size:
            # the frame claims to run past the tag's own declared size:
            # a genuine structural error, compared against the true tag
            # size rather than however much we happened to read.
            warns.append(
                f"frame {fid_s!r} size {fsize} overruns the "
                f"{tag_size:,}-byte tag"
            )
            break
        if data_start + fsize > len(body):
            # fits inside the declared tag but past what we read (embedded
            # art beyond the read cap). record the frame and stop cleanly;
            # this is not a spec violation.
            note = "attached picture" if fid_s in ("APIC", "PIC") else ""
            fields.append(_f(10 + pos, fhdr_len + fsize, fid_s,
                             f"{fsize:,} bytes", note or "beyond read cap"))
            break
        raw = body[data_start:data_start + fsize]
        note = (_ID3V22_TEXT_FRAMES if is_v22 else _ID3_TEXT_FRAMES).get(fid_s, "")
        if fid_s in ("TXXX", "WXXX"):
            value = _decode_txxx(raw, fid_s)
            note = "user-defined text" if fid_s == "TXXX" else "user-defined URL"
        elif fid_s.startswith("T"):        # every T*** frame is text (id3 spec)
            value = _decode_id3_text(raw)
        elif fid_s == "APIC" or fid_s == "PIC":
            value = f"{fsize:,} bytes"
            note = "attached picture"
        else:
            value = f"{fsize:,} bytes"
        fields.append(_f(10 + pos, fhdr_len + fsize, fid_s, value, note))
        pos = data_start + fsize
    return fields, warns


def _xing_offset(hdr):
    """Byte offset of the Xing/Info tag within the first frame, from the
    frame start: 4-byte header, an optional 2-byte CRC when the frame is
    protected, then the version/channel-dependent side info block."""
    mono = hdr["channel_mode"] == 0b11
    base = 4 + (2 if hdr.get("has_crc") else 0)
    if hdr["version_id"] == 0b11:        # MPEG 1
        return base + (17 if mono else 32)
    return base + (9 if mono else 17)    # MPEG 2 / 2.5


def _parse_vbri(buf, off):
    """Decode a Fraunhofer VBRI header. Returns the Xing-path 4-tuple
    (fields, warns, frame_count, tag). Offsets are frame-relative to match
    the frame0 chunk's payload_base. All fields are big-endian."""
    fields = []
    version = _bu16(buf, off + 4)
    nbytes = _bu32(buf, off + 10)
    frame_count = _bu32(buf, off + 14)
    fields.append(_f(off, 4, "vbr_tag", "VBRI", "VBR (Fraunhofer)"))
    fields.append(_f(off + 4, 2, "version", version))
    fields.append(_f(off + 10, 4, "byte_count", f"{nbytes:,}", enc=">I", raw=nbytes))
    fields.append(_f(off + 14, 4, "frame_count", f"{frame_count:,}",
                     enc=">I", raw=frame_count))
    return fields, [], frame_count, b"VBRI"


def _parse_xing_lame(filepath, frame_off, hdr):
    """Decode the Xing/Info VBR header and any LAME extension in the
    first frame. Returns (fields, warns, frame_count, tag) where tag is
    b"Xing" (VBR), b"Info" (CBR), or None if no tag is present."""
    fields, warns = [], []
    xoff = _xing_offset(hdr)
    with open(filepath, "rb") as f:
        f.seek(frame_off)
        buf = f.read(max(hdr["frame_length"], xoff + 200, 64))
    # VBRI (Fraunhofer) sits at a fixed offset, 32 bytes past the 4-byte frame
    # header, regardless of channel mode; Xing/Info sit at the side-info-
    # dependent xoff. a frame carries at most one of them.
    if len(buf) >= 36 + 18 and buf[36:40] == b"VBRI":
        return _parse_vbri(buf, 36)
    if xoff + 8 > len(buf):
        return None, [], None, None
    tag = buf[xoff:xoff + 4]
    if tag not in (b"Xing", b"Info"):
        return None, [], None, None
    kind = "VBR" if tag == b"Xing" else "CBR (LAME)"
    fields.append(_f(xoff, 4, "vbr_tag", tag.decode("ascii"), kind))
    flags = _bu32(buf, xoff + 4)
    pos = xoff + 8
    # each optional field is only present if its flag is set; the tag may be
    # truncated after any of them, so bound every read against the buffer.
    frame_count = None
    if flags & 0x01:
        if pos + 4 > len(buf):
            warns.append("Xing header truncated before frame_count")
            return fields, warns, frame_count, tag
        frame_count = _bu32(buf, pos)
        fields.append(_f(pos, 4, "frame_count", f"{frame_count:,}",
                         enc=">I", raw=frame_count))
        pos += 4
    if flags & 0x02:
        if pos + 4 > len(buf):
            warns.append("Xing header truncated before byte_count")
            return fields, warns, frame_count, tag
        nbytes = _bu32(buf, pos)
        fields.append(_f(pos, 4, "byte_count", f"{nbytes:,}", enc=">I", raw=nbytes))
        pos += 4
    if flags & 0x04:
        if pos + 100 > len(buf):
            warns.append("Xing header truncated before seek table")
            return fields, warns, frame_count, tag
        fields.append(_f(pos, 100, "toc", "100-entry seek table"))
        pos += 100
    if flags & 0x08:
        if pos + 4 > len(buf):
            warns.append("Xing header truncated before quality")
            return fields, warns, frame_count, tag
        quality = _bu32(buf, pos)
        fields.append(_f(pos, 4, "quality", quality, "0=best, 100=worst"))
        pos += 4

    # LAME extension: 9-byte encoder string then 27 bytes of detail
    if pos + 9 <= len(buf) and buf[pos:pos + 4] in (b"LAME", b"L3.9", b"GOGO"):
        version = buf[pos:pos + 9].decode("latin-1", errors="replace").strip()
        fields.append(_f(pos, 9, "encoder", version))
        if pos + 24 <= len(buf):
            vbr_method = buf[pos + 9] & 0x0F
            lowpass = buf[pos + 10] * 100
            fields.append(_f(pos + 9, 1, "vbr_method", vbr_method,
                             _VBR_METHODS.get(vbr_method, "")))
            if lowpass:
                fields.append(_f(pos + 10, 1, "lowpass", f"{lowpass} Hz"))
            rg = _lame_replaygain(_bu16(buf, pos + 15))
            if rg:
                fields.append(_f(pos + 15, 2, "replay_gain", rg))
            bitrate = buf[pos + 20]
            if bitrate:
                fields.append(_f(pos + 20, 1, "bitrate", f"{bitrate} kbps",
                                 "min for VBR, target for ABR"))
            delay = (buf[pos + 21] << 4) | (buf[pos + 22] >> 4)
            padding = ((buf[pos + 22] & 0x0F) << 8) | buf[pos + 23]
            fields.append(_f(pos + 21, 3, "gapless", f"delay {delay}, pad {padding}",
                             "encoder delay / padding samples"))
    return fields, warns, frame_count, tag


def inspect_mp3(filepath, deep=False):
    """Walk an MP3: optional ID3v2 tag, the MPEG frame run (with the
    first frame fully decoded and any Xing/LAME header), and an optional
    ID3v1 trailer. With ``deep``, the frame run carries a per-frame
    listing (offset, bitrate, sample rate, channel mode, size)."""
    file_size = os.path.getsize(filepath)
    chunks = []
    file_warns = []

    audio_start = 0
    hdr = mp3mod.read_id3v2(filepath)
    if hdr:
        flds, warns = _id3v2_frames(filepath, hdr)
        ntext = sum(1 for fl in flds if fl["off"] is not None and fl["off"] >= 10)
        chunks.append({"id": "ID3v2", "offset": 0, "size": hdr["total"],
                       "summary": f"ID3v2.{hdr['major']} tag, {ntext} frame(s)",
                       "fields": flds, "warnings": warns,
                       "payload_base": 0})  # ID3 field offsets are absolute
        audio_start = hdr["total"]

    id3v1_off = mp3mod.find_id3v1(filepath)
    audio_end = id3v1_off if id3v1_off is not None else file_size

    # find the first valid frame at or after audio_start
    first = None
    for off, fh in mp3mod.iter_frames(filepath, audio_start, audio_end, max_frames=1):
        first = (off, fh)
        break
    if first is None:
        file_warns.append("no valid MPEG audio frame found")
        return chunks, file_warns

    frame_off, fh = first
    if frame_off > audio_start:
        file_warns.append(
            f"{frame_off - audio_start} bytes of junk between the tag and the "
            f"first frame sync"
        )
    fields = [
        _f(0x00, 4, "sync", "0x7ff", f"{fh['version']}, {fh['layer']}"),
        _f(None, 0, "bitrate", fh["bitrate"], "kbps (first frame)"),
        _f(None, 0, "sample_rate", fh["sample_rate"], "Hz"),
        _f(None, 0, "channel_mode", fh["channel_mode_name"]),
        _f(None, 0, "crc_protected", fh["has_crc"]),
        _f(None, 0, "samples_per_frame", fh["samples_per_frame"]),
    ]
    if fh["emphasis"] != "none":
        fields.append(_f(None, 0, "emphasis", fh["emphasis"]))

    try:
        xing_fields, xing_warns, vbr_frames, vbr_tag = \
            _parse_xing_lame(filepath, frame_off, fh)
    except Exception as e:
        xing_fields, xing_warns, vbr_frames, vbr_tag = None, \
            [f"VBR header parse error: {e.__class__.__name__}"], None, None
    # Xing and VBRI both declare VBR; an Info tag is the same structure as
    # Xing written by LAME for CBR streams and must not force the VBR label.
    is_vbr_header = vbr_tag in (b"Xing", b"VBRI")
    if xing_fields is not None:
        fields.extend(xing_fields)
    chunks.append({"id": "frame0", "offset": frame_off, "size": fh["frame_length"],
                   "summary": (f"{fh['version']} {fh['layer']}, {fh['bitrate']} kbps, "
                               f"{fh['sample_rate']} Hz, {fh['channel_mode_name']}"),
                   "fields": fields, "warnings": xing_warns,
                   "payload_base": frame_off})  # fields are frame-relative

    # LAME encoder delay + padding, so the reported duration is the gapless /
    # playable length (matching ffprobe/mutagen), not the raw frame count.
    gap_delay = gap_pad = 0
    for _fl in (xing_fields or []):
        if _fl.get("name") == "gapless":
            try:
                _t = str(_fl.get("value", "")).replace(",", "").split()
                gap_delay, gap_pad = int(_t[1]), int(_t[3])
            except (ValueError, IndexError):
                pass

    # count frames and derive duration. trust the Xing frame count when
    # present (accurate for VBR); otherwise walk the stream. with deep,
    # also record a per-frame row up to the listing cap.
    count = 0
    bitrates = set()
    rows = []
    truncated = False
    for off, f2 in mp3mod.iter_frames(filepath, frame_off, audio_end):
        count += 1
        bitrates.add(f2["bitrate"])
        if deep and len(rows) < _FRAME_LISTING_CAP:
            rows.append({
                "#": len(rows),
                "offset": f"0x{off:08x}",
                "kbps": f2["bitrate"],
                "Hz": f2["sample_rate"],
                "mode": f2["channel_mode_name"],
                "bytes": f2["frame_length"],
            })
        elif deep:
            truncated = True
    walked = count
    if vbr_frames:
        count = vbr_frames
    spf = fh["samples_per_frame"]
    duration = (max(0, count * spf - gap_delay - gap_pad) / fh["sample_rate"]
                if fh["sample_rate"] else 0)
    cbr = len(bitrates) == 1 and not is_vbr_header
    summary = (f"{count:,} frames, {duration:.3f} s, "
               f"{'CBR' if cbr else 'VBR'}")
    if len(bitrates) > 1:
        summary += f", {min(bitrates)}-{max(bitrates)} kbps"
    frames_entry = {"id": "frames", "offset": frame_off,
                    "size": audio_end - frame_off, "summary": summary,
                    "fields": [_f(None, 0, "frame_count", f"{count:,}"),
                               _f(None, 0, "duration", f"{duration:.3f} s"),
                               _f(None, 0, "vbr", not cbr)],
                    "warnings": [], "payload_base": frame_off}
    if vbr_frames and walked and abs(vbr_frames - walked) > max(2, walked // 20):
        frames_entry["warnings"].append(
            f"Xing/VBRI frame_count {vbr_frames:,} diverges from {walked:,} "
            f"frames walked; VBR duration may be wrong")
    if deep:
        frames_entry["rows"] = rows
        if truncated:
            frames_entry["warnings"].append(
                f"frame listing capped at {_FRAME_LISTING_CAP:,}; "
                f"{count:,} frames total"
            )
    chunks.append(frames_entry)

    if id3v1_off is not None:
        with open(filepath, "rb") as f:
            f.seek(id3v1_off)
            tag = f.read(128)
        v1_fields, title = _id3v1_fields(tag)
        chunks.append({"id": "ID3v1", "offset": id3v1_off, "size": 128,
                       "summary": f"ID3v1 trailer, {title or 'untitled'}",
                       "fields": v1_fields,
                       "warnings": [], "payload_base": id3v1_off})
    return chunks, file_warns
