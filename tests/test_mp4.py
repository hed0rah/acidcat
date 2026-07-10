"""MP4 box-walk: a large box read only in part is beyond_cap, not truncated."""
import struct

from acidcat.core import mp4


def _boxes(data, **kw):
    return list(mp4.iter_boxes(data, **kw))


def test_large_mdat_beyond_cap_not_truncated():
    ftyp = struct.pack(">I", 16) + b"ftypM4A " + b"\x00" * 4
    mdat = struct.pack(">I", 1000) + b"mdat" + b"\x00" * 992
    full = ftyp + mdat
    # only the first 100 bytes were read, but file_size is the true length
    boxes = _boxes(full[:100], file_size=len(full))
    md = [b for b in boxes if b["type"] == b"mdat"]
    assert md, "mdat should still be enumerated from its header"
    assert md[0]["truncated"] is False
    assert md[0]["beyond_cap"] is True


def test_mdat_overrunning_the_file_is_truncated():
    ftyp = struct.pack(">I", 16) + b"ftypM4A " + b"\x00" * 4
    # mdat claims 9999 bytes but the file only has room for far less
    mdat = struct.pack(">I", 9999) + b"mdat" + b"\x00" * 20
    full = ftyp + mdat
    boxes = _boxes(full, file_size=len(full))
    md = [b for b in boxes if b["type"] == b"mdat"]
    assert md and md[0]["truncated"] is True


def _box(btype, payload):
    return struct.pack(">I", 8 + len(payload)) + btype + payload


def test_gnre_atom_resolves_id3v1_genre():
    # gnre stores type indicator 0 and a u16 of ID3v1 genre index + 1
    # (18 -> Rock); older iTunes wrote genre this way instead of \xa9gen
    data_box = _box(b"data", struct.pack(">II", 0, 0) + struct.pack(">H", 18))
    ilst = _box(b"ilst", _box(b"gnre", data_box))
    meta = _box(b"meta", b"\x00\x00\x00\x00" + ilst)
    tree = _box(b"moov", _box(b"udta", meta))
    assert mp4.parse_ilst(tree)["genre"] == "Rock"


def _stsd(entry):
    # stsd FullBox: version/flags u32 + entry_count u32 + the sample entry
    return _box(b"stsd", struct.pack(">II", 0, 1) + entry)


def test_audio_info_v0_sample_entry():
    body = (b"\x00" * 6 + struct.pack(">H", 1)        # reserved, data_ref_index
            + struct.pack(">HH", 0, 0) + b"\x00" * 4  # version 0, revision, vendor
            + struct.pack(">HHHH", 2, 16, 0, 0)       # channels, bits, cid, pkt
            + struct.pack(">I", 44100 << 16))         # rate, 16.16 fixed
    assert mp4.audio_info(_stsd(_box(b"mp4a", body))) == ("mp4a", 2, 44100)


def test_audio_info_v2_quicktime_sample_entry():
    # a v2 (QuickTime) AudioSampleEntry stores the rate as a float64 at +32
    # and the channel count as a u32 at +40; the v0 offsets there hold the
    # constants 3 / 65536 and previously decoded as 3ch at a garbage rate
    body = (b"\x00" * 6 + struct.pack(">H", 1)
            + struct.pack(">HH", 2, 0) + b"\x00" * 4       # version 2
            + struct.pack(">HHhH", 3, 16, -2, 0)           # v2 constants
            + struct.pack(">I", 0x00010000)                # always 65536
            + struct.pack(">I", 72)                        # sizeOfStructOnly
            + struct.pack(">d", 96000.0)                   # audioSampleRate
            + struct.pack(">I", 6)                         # numAudioChannels
            + b"\x00" * 8)
    assert mp4.audio_info(_stsd(_box(b"lpcm", body))) == ("lpcm", 6, 96000)
