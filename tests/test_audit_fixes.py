"""Regressions for the correctness-audit fixes: OGG duration from the granule
position, and decoding of every ID3 T*** text frame (not just a hardcoded set)."""
import struct

from acidcat.core.walk import walk_file


def _ogg_page(serial, granule, htype, packet):
    seg = []
    rem = len(packet)
    while rem >= 255:
        seg.append(255)
        rem -= 255
    seg.append(rem)
    return (b"OggS" + bytes([0, htype]) + struct.pack("<q", granule)
            + struct.pack("<I", serial) + struct.pack("<I", 0) + bytes(4)
            + bytes([len(seg)]) + bytes(seg) + packet)


def test_ogg_duration_from_granule(tmp_path):
    # vorbis ident packet: \x01vorbis, version(4), channels=2, sample_rate=44100
    ident = (bytes([1]) + b"vorbis" + bytes(4) + bytes([2])
             + struct.pack("<I", 44100) + bytes(20))
    bos = _ogg_page(1, 0, 2, ident)                 # beginning of stream
    audio = _ogg_page(1, 44100, 0, bytes(64))       # granule 44100 -> 1.000 s
    f = tmp_path / "t.ogg"
    f.write_bytes(bos + audio)
    _label, chunks, _warns = walk_file(str(f))
    dur = next((fl["value"] for c in chunks for fl in c.get("fields", [])
                if fl.get("name") == "duration"), None)
    assert dur is not None and dur.startswith("1.000")


def _syncsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def test_mp3_decodes_unlisted_text_frame(tmp_path):
    # TPE2 (album artist) is a T*** text frame that used to render as "N bytes"
    body = bytes([0]) + b"Some Album Artist"           # encoding 0 = latin-1
    frame = b"TPE2" + struct.pack(">I", len(body)) + bytes(2) + body
    tag = b"ID3" + bytes([3, 0, 0]) + _syncsafe(len(frame)) + frame
    mpeg = b"\xff\xfb\x90\x00" + bytes(413)             # one MPEG frame
    f = tmp_path / "t.mp3"
    f.write_bytes(tag + mpeg)
    _label, chunks, _warns = walk_file(str(f))
    val = next((fl["value"] for c in chunks for fl in c.get("fields", [])
                if fl.get("name") == "TPE2"), None)
    assert val == "Some Album Artist"
