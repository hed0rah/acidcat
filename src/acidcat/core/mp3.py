"""
MPEG audio (MP3) frame and ID3v2 primitives.

Unlike RIFF/FLAC, MP3 is not a chunked container: it is an optional
ID3v2 tag, a run of self-describing MPEG audio frames (each with its own
4-byte header), and an optional ID3v1 trailer. This module provides the
frame-header decoder, a frame walker, and the ID3v2 size reader. The
walker in core/walk/mp3.py shapes these into the chunk model and decodes the
Xing/LAME and ID3-frame detail for display.
"""

import os
import struct

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
        with open(filepath, "rb") as f:
            head = f.read(3)
            if head == b"ID3":
                return True
            return len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
    except Exception:
        return False


def synchsafe(b4):
    """Decode a 4-byte ID3v2 synchsafe integer (7 bits per byte)."""
    return (b4[0] << 21) | (b4[1] << 14) | (b4[2] << 7) | b4[3]


def read_id3v2(filepath):
    """Read the ID3v2 header at offset 0, if present.

    Returns a dict with major, revision, flags, size (payload bytes
    after the 10-byte header), total (header + payload + footer), and
    has_footer, or None if no ID3v2 tag is present.
    """
    with open(filepath, "rb") as f:
        hdr = f.read(10)
    if len(hdr) < 10 or hdr[:3] != b"ID3":
        return None
    major, revision, flags = hdr[3], hdr[4], hdr[5]
    size = synchsafe(hdr[6:10])
    has_footer = bool(flags & 0x10)
    total = 10 + size + (10 if has_footer else 0)
    return {"major": major, "revision": revision, "flags": flags,
            "size": size, "total": total, "has_footer": has_footer}


def decode_frame_header(b4):
    """Decode a 4-byte MPEG audio frame header.

    Returns a dict of decoded fields plus the computed frame_length and
    samples_per_frame, or None if the bytes are not a valid frame header
    (bad sync, reserved version/layer, free/invalid bitrate, reserved
    sample rate).
    """
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
    if bitrate <= 0 or sample_rate <= 0:
        return None

    if layer == "Layer I":
        samples = 384
    elif layer == "Layer III" and not is_v1:
        samples = 576
    else:
        samples = 1152

    bps = bitrate * 1000
    if layer == "Layer I":
        frame_length = (12 * bps // sample_rate + padding) * 4
    else:
        frame_length = (samples // 8) * bps // sample_rate + padding

    return {
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


def iter_frames(filepath, start, end, max_frames=None):
    """Walk MPEG audio frames from ``start`` up to ``end``.

    Yields (offset, header) for each decoded frame. Steps by the frame's
    own computed length; on a sync loss it scans forward byte by byte for
    the next valid header (so a stray ID3/APE chunk mid-stream doesn't
    abort the walk). Stops after ``max_frames`` if given.
    """
    count = 0
    with open(filepath, "rb") as f:
        pos = start
        while pos + 4 <= end:
            f.seek(pos)
            b4 = f.read(4)
            hdr = decode_frame_header(b4)
            if hdr is None:
                pos += 1
                continue
            yield pos, hdr
            count += 1
            if max_frames is not None and count >= max_frames:
                return
            step = hdr["frame_length"]
            pos += step if step > 0 else 1


def find_id3v1(filepath):
    """Return the offset of a trailing 128-byte ID3v1 tag, or None."""
    size = os.path.getsize(filepath)
    if size < 128:
        return None
    with open(filepath, "rb") as f:
        f.seek(size - 128)
        if f.read(3) == b"TAG":
            return size - 128
    return None
