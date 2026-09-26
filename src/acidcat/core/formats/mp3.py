"""
MPEG audio (MP3) frame and ID3v2 primitives.

Unlike RIFF/FLAC, MP3 is not a chunked container: it is an optional
ID3v2 tag, a run of self-describing MPEG audio frames (each with its own
4-byte header), and an optional ID3v1 trailer. This module provides the
frame-header decoder, a frame walker, and the ID3v2 size reader. The
walker in core/walk/mp3.py shapes these into the chunk model and decodes the
Xing/LAME and ID3-frame detail for display.
"""

import struct

from acidcat.core.infra.source import open_input, input_size
from acidcat.core.infra.findings import defect

# bitrate (kbps) by (version, layer) -> 16-entry table. index 0 is the
# "free" format, index 15 is the reserved/invalid marker.
_BR_V1_L1 = (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, -1)
_BR_V1_L2 = (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, -1)
_BR_V1_L3 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, -1)
_BR_V2_L1 = (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, -1)
_BR_V2_L23 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, -1)

# version id (2 bits) -> label, sample-rate table
_VERSION = {0b00: "MPEG 2.5", 0b10: "MPEG 2", 0b11: "MPEG 1"}
_SAMPLE_RATES = {
    0b11: (44100, 48000, 32000, -1),   # MPEG 1
    0b10: (22050, 24000, 16000, -1),   # MPEG 2
    0b00: (11025, 12000, 8000, -1),    # MPEG 2.5
}

# layer (2 bits) -> label. 0b00 is reserved.
_LAYER = {0b01: "Layer III", 0b10: "Layer II", 0b11: "Layer I"}

_CHANNEL_MODES = {0b00: "stereo", 0b01: "joint stereo",
                  0b10: "dual channel", 0b11: "mono"}

_EMPHASIS = {0b00: "none", 0b01: "50/15 ms", 0b10: "reserved", 0b11: "CCITT J.17"}

# ID3v1 genre index -> name: 0-79 the original spec, 80-191 the Winamp
# extensions. 255 (and anything past the table) is "none/unknown". Shared:
# the ID3v1 trailer, numeric TCON references, and the MP4 'gnre' atom
# (which stores this index + 1) all resolve against it.
ID3V1_GENRES = [
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


def is_mp3(filepath):
    """Check for an ID3v2 tag or an MPEG frame sync in the first bytes."""
    try:
        with open_input(filepath) as f:
            head = f.read(3)
            if head == b"ID3":
                return True
            return len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
    except Exception:
        return False


def synchsafe(b4):
    """Decode a 4-byte ID3v2 synchsafe integer (7 bits per byte).

    The mask is the point. Synchsafe exists so a length can never contain a
    0xFF byte that a decoder would mistake for a frame sync, which means the
    high bit of every byte is defined to be zero. A file with one set is
    malformed, and reading it unmasked turns four bytes into a quarter-gigabyte:
    a 414-byte file was reporting a 268 MB tag and skipping past the MPEG frame
    at offset 10 to look for audio that far away.

    `forensics/anomalies.py` masked from the start. This did not, so the two
    readers of the same field disagreed by 268 MB on the same bytes.
    """
    return (((b4[0] & 0x7F) << 21) | ((b4[1] & 0x7F) << 14)
            | ((b4[2] & 0x7F) << 7) | (b4[3] & 0x7F))


def id3v2_from_bytes(data):
    """Decode a complete ID3v2 tag held in memory.

    Returns (header, frames, warnings): header is
    {major, revision, flags, size, size_note}, frames is a list of
    (frame id, decoded text) for the frames that carry text, and warnings
    names anything the tag got wrong about itself.

    Bytes rather than a path, because a tag embedded in a RIFF or AIFF chunk is
    a slice of an already-open file. The alternative in use was writing it to a
    temp file to read it back, which costs a file per tag and fails wherever the
    filesystem is read-only.
    """
    warns = []
    if len(data) < 10 or data[:3] != b"ID3":
        return None, [], [defect("magic.mismatch", "not an ID3v2 tag")]
    major, revision, flags = data[3], data[4], data[5]
    size = synchsafe(data[6:10])
    size_note = "synchsafe"

    # A writer in the wild puts this field LITTLE-ENDIAN, which is not the
    # format: the size is synchsafe big-endian, and 0f 00 00 00 read that way
    # is 31,457,280 rather than 15. Measured in a real library on tags whose
    # whole chunk is 25 bytes -- where the same writer got the FRAME size
    # right, so the two halves of one header disagree about their byte order.
    #
    # Believed only when the spec reading does not fit the data and the
    # little-endian one fits exactly. Narrow on purpose: a guess that merely
    # looked plausible would silently re-interpret conformant tags.
    if 10 + size > len(data):
        le = int.from_bytes(data[6:10], "little")
        if 10 + le == len(data):
            warns.append(
                defect("value.invalid",
                       f"the tag size is written little-endian ({le}), not the "
                       f"synchsafe big-endian the format requires (which reads "
                       f"{size:,}). Using {le}, which matches the tag exactly."))
            size, size_note = le, "little-endian, non-conformant"
        else:
            warns.append(defect("size.overrun",
                                f"the tag declares {size:,} bytes but only "
                                f"{len(data) - 10:,} follow its header"))

    header = {"major": major, "revision": revision, "flags": flags,
              "size": size, "size_note": size_note}

    frames = []
    pos, end = 10, min(10 + size, len(data))
    idlen = 3 if major == 2 else 4
    while pos + idlen + (3 if major == 2 else 4) <= end:
        fid = data[pos:pos + idlen].decode("latin-1", "replace")
        if not fid.strip("\x00"):
            break                       # padding: the tag's frames are done
        if major == 2:
            fsize = int.from_bytes(data[pos + 3:pos + 6], "big")
            head = 6
        else:
            raw = data[pos + 4:pos + 8]
            # v2.4 made the frame size synchsafe; v2.3 left it a plain u32,
            # and reading one as the other shifts every frame after the first
            fsize = (synchsafe(raw) if major >= 4
                     else int.from_bytes(raw, "big"))
            head = 10
        if fsize <= 0 or pos + head + fsize > end:
            break
        text = _id3_frame_text(fid, data[pos + head:pos + head + fsize])
        if text:
            frames.append((fid, text))
        pos += head + fsize
    return header, frames, warns


def read_id3v2(filepath):
    """Read the ID3v2 header at offset 0, if present.

    Returns a dict with major, revision, flags, size (payload bytes
    after the 10-byte header), total (header + payload + footer), and
    has_footer, or None if no ID3v2 tag is present.
    """
    with open_input(filepath) as f:
        hdr = f.read(10)
    if len(hdr) < 10 or hdr[:3] != b"ID3":
        return None
    major, revision, flags = hdr[3], hdr[4], hdr[5]
    size = synchsafe(hdr[6:10])
    has_footer = bool(flags & 0x10)
    total = 10 + size + (10 if has_footer else 0)
    return {"major": major, "revision": revision, "flags": flags,
            "size": size, "total": total, "has_footer": has_footer}


_ID3_ENCODINGS = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}


def _id3_frame_text(fid, body):
    """Decoded text of a T*/W*/COMM/USLT ID3v2 frame body, or None for binary."""
    if not body:
        return None
    if fid.startswith("T"):
        codec = _ID3_ENCODINGS.get(body[0], "latin-1")
        try:
            return body[1:].decode(codec, "replace").strip("\x00")
        except Exception:
            return body[1:].decode("latin-1", "replace").strip("\x00")
    if fid.startswith("W"):
        return body.decode("latin-1", "replace").strip("\x00")
    if fid in ("COMM", "USLT", "COM", "ULT") and len(body) >= 4:
        # encoding byte, 3-byte language, then description\0text
        codec = _ID3_ENCODINGS.get(body[0], "latin-1")
        try:
            text = body[4:].decode(codec, "replace")
        except Exception:
            text = body[4:].decode("latin-1", "replace")
        return text.replace("\x00", " ").strip()
    return None


def list_id3v2_frames(path, max_bytes=8 * 1024 * 1024):
    """List ID3v2 frame records at the head of the file, or [] if no tag. Each is
    a dict {id, version, offset, size, flags, encoding, text}: text is the decoded
    value for T*/W* frames else None; encoding is the text-encoding byte for T*
    frames else None; flags is b"" on v2.2. max_bytes caps the read (a hostile
    synchsafe size can claim 256 MB)."""
    tag = read_id3v2(path)
    if not tag:
        return []
    major = tag["major"]
    with open_input(path) as fh:
        data = fh.read(min(10 + tag["size"], max_bytes))
    pos, end = 10, min(len(data), 10 + tag["size"])
    idlen, hdrlen = (3, 6) if major == 2 else (4, 10)
    out = []
    while pos + hdrlen <= end and len(out) < 512:
        fid = data[pos:pos + idlen]
        if not fid.strip(b"\x00") or fid[0] < 0x30:       # padding / end of frames
            break
        szraw = data[pos + idlen:pos + idlen + (3 if major == 2 else 4)]
        size = synchsafe(szraw) if major == 4 else int.from_bytes(szraw, "big")
        flags = data[pos + idlen + (3 if major == 2 else 4):pos + hdrlen] if major != 2 else b""
        if size <= 0 or pos + hdrlen + size > end + 1:
            break
        body = data[pos + hdrlen:pos + hdrlen + size]
        fs = fid.decode("latin-1", "replace")
        out.append({"id": fs, "version": major, "offset": pos, "size": size,
                    "flags": flags,
                    "encoding": (body[0] if fs.startswith("T") and body else None),
                    "text": _id3_frame_text(fs, body)})
        pos += hdrlen + size
    return out


def decode_frame_header(b4, allow_free=False):
    """Decode a 4-byte MPEG audio frame header.

    Returns a dict of decoded fields plus the computed frame_length and
    samples_per_frame, or None if the bytes are not a valid frame header
    (bad sync, reserved version/layer, invalid bitrate, reserved sample
    rate). Bitrate index 0 is the spec's "free format" (a constant bitrate
    outside the table, ISO 11172-3): rejected by default so the sniffing
    predicates stay strict, accepted with ``allow_free`` -- then
    ``free_format`` is True and ``frame_length`` is 0, because the length
    must be measured from the sync spacing (see iter_frames)."""
    if len(b4) < 4 or b4[0] != 0xFF or (b4[1] & 0xE0) != 0xE0:
        return None
    version_id = (b4[1] >> 3) & 0x03
    layer_id = (b4[1] >> 1) & 0x03
    if version_id == 0b01 or layer_id == 0b00:
        return None  # reserved version / reserved layer
    has_crc = not (b4[1] & 0x01)
    br_index = (b4[2] >> 4) & 0x0F
    sr_index = (b4[2] >> 2) & 0x03
    padding = (b4[2] >> 1) & 0x01
    private = b4[2] & 0x01
    channel_mode = (b4[3] >> 6) & 0x03
    mode_ext = (b4[3] >> 4) & 0x03
    copyright_bit = (b4[3] >> 3) & 0x01
    original = (b4[3] >> 2) & 0x01
    emphasis = b4[3] & 0x03

    layer = _LAYER[layer_id]
    is_v1 = version_id == 0b11
    if layer == "Layer I":
        table = _BR_V1_L1 if is_v1 else _BR_V2_L1
    elif layer == "Layer II":
        table = _BR_V1_L2 if is_v1 else _BR_V2_L23
    else:
        table = _BR_V1_L3 if is_v1 else _BR_V2_L23
    bitrate = table[br_index]            # kbps; 0 = free, -1 = invalid
    sample_rate = _SAMPLE_RATES[version_id][sr_index]
    if bitrate < 0 or sample_rate <= 0:
        return None
    if bitrate == 0 and not allow_free:
        return None

    if layer == "Layer I":
        samples = 384
    elif layer == "Layer III" and not is_v1:
        samples = 576
    else:
        samples = 1152

    if bitrate == 0:
        frame_length = 0                 # free format: measured, not derived
    else:
        bps = bitrate * 1000
        if layer == "Layer I":
            frame_length = (12 * bps // sample_rate + padding) * 4
        else:
            frame_length = (samples // 8) * bps // sample_rate + padding

    return {
        "free_format": bitrate == 0,
        "version_id": version_id,
        "version": _VERSION[version_id],
        "layer": layer,
        "has_crc": has_crc,
        "bitrate": bitrate,
        "sample_rate": sample_rate,
        "padding": padding,
        "private": private,
        "channel_mode": channel_mode,
        "channel_mode_name": _CHANNEL_MODES[channel_mode],
        "mode_ext": mode_ext,
        "copyright": bool(copyright_bit),
        "original": bool(original),
        "emphasis": _EMPHASIS[emphasis],
        "samples_per_frame": samples,
        "frame_length": frame_length,
    }


# how far past a free-format sync to look for its twin; generous versus the
# largest table frame (Layer I at 448 kbps / 32 kHz is ~672 bytes)
_FREE_SCAN_CAP = 65536


def _free_frame_length(f, pos, hdr, end):
    """The constant frame length of a free-format stream, measured as the
    distance from the sync at ``pos`` to the next header with the same
    version, layer, and sample rate (and free bitrate). None when no such
    twin exists, in which case the sync at ``pos`` is treated as false."""
    f.seek(pos + 4)
    window = f.read(min(_FREE_SCAN_CAP, max(0, end - pos - 4)))
    for i in range(len(window) - 3):
        if window[i] != 0xFF or (window[i + 1] & 0xE0) != 0xE0:
            continue
        h2 = decode_frame_header(window[i:i + 4], allow_free=True)
        if (h2 and h2["free_format"]
                and h2["version_id"] == hdr["version_id"]
                and h2["layer"] == hdr["layer"]
                and h2["sample_rate"] == hdr["sample_rate"]):
            return i + 4
    return None


def free_format_bitrate(hdr, frame_length):
    """Actual kbps of a measured free-format frame: the frame-length formula
    solved for the bitrate. None when the inputs don't allow it."""
    rate = hdr.get("sample_rate")
    if not rate or not frame_length:
        return None
    if hdr["layer"] == "Layer I":
        bps = (frame_length // 4 - hdr["padding"]) * rate / 12
    else:
        bps = (frame_length - hdr["padding"]) * rate / (hdr["samples_per_frame"] // 8)
    return round(bps / 1000, 1)


_RESYNC_WINDOW = 1 << 20        # bytes buffered per read while walking frames
_RESYNC_LIMIT = 1 << 20         # give up after this much contiguous non-frame data


def iter_frames(filepath, start, end, max_frames=None):
    """Walk MPEG audio frames from ``start`` up to ``end``.

    Yields (offset, header) for each decoded frame. Steps by the frame's own
    computed length; on a sync loss it scans forward for the next 0xFF sync
    candidate *in memory* over a buffered window (so a stray ID3/APE chunk
    mid-stream doesn't abort the walk, without a syscall per byte). If more than
    ``_RESYNC_LIMIT`` contiguous bytes carry no valid frame the stream is treated
    as lost and the walk stops -- a valid MPEG stream never has that gap, and it
    bounds a crafted run of garbage. Free-format frames (bitrate index 0) are
    supported. Stops after ``max_frames`` if given.
    """
    count = 0
    free_len = None
    lost = 0                    # contiguous non-frame bytes since the last frame
    with open_input(filepath) as f:
        pos = start
        buf = b""
        buf_start = start

        def _skip_to_next_sync(rel):
            # in-memory jump to the next 0xFF in the buffer (or its end)
            nxt = buf.find(b"\xff", rel + 1)
            return (buf_start + nxt, nxt - rel) if nxt != -1 \
                else (buf_start + len(buf), len(buf) - rel)

        while pos + 4 <= end:
            rel = pos - buf_start
            if rel < 0 or rel + 4 > len(buf):           # refill the window at pos
                f.seek(pos)
                buf = f.read(min(_RESYNC_WINDOW, end - pos))
                buf_start = pos
                rel = 0
                if len(buf) < 4:
                    break
            hdr = decode_frame_header(buf[rel:rel + 4], allow_free=True)
            if hdr is not None and hdr["free_format"]:
                if free_len is None:
                    free_len = _free_frame_length(f, pos, hdr, end)
                if free_len is None:
                    hdr = None                          # lone free sync: false
                else:
                    hdr["frame_length"] = free_len
            if hdr is None:
                pos, skipped = _skip_to_next_sync(rel)
                lost += skipped
                if lost > _RESYNC_LIMIT:
                    return
                continue
            lost = 0
            yield pos, hdr
            count += 1
            if max_frames is not None and count >= max_frames:
                return
            step = hdr["frame_length"]
            pos += step if step > 0 else 1


def find_id3v1(filepath):
    """Return the offset of a trailing 128-byte ID3v1 tag, or None."""
    size = input_size(filepath)
    if size < 128:
        return None
    with open_input(filepath) as f:
        f.seek(size - 128)
        if f.read(3) == b"TAG":
            return size - 128
    return None
