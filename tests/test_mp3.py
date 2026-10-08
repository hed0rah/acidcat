"""MP3 walker tests. The Xing/Info tag location is the CRC-offset regression:
LAME holds the tag at the CRC-absent offset even on a CRC-protected frame
(VbrTag.c), so the walker must NOT add 2 for the CRC halfword."""

from acidcat.core.walk.mp3 import _xing_offset


def _hdr(version_id, channel_mode, has_crc):
    return {"version_id": version_id, "channel_mode": channel_mode,
            "has_crc": has_crc}


def test_xing_offset_base_cases():
    # tag offset from frame start = 4-byte header + side info
    # side info: MPEG-1 stereo 32 / mono 17; MPEG-2/2.5 stereo 17 / mono 9
    assert _xing_offset(_hdr(0b11, 0b00, False)) == 36   # MPEG-1 stereo
    assert _xing_offset(_hdr(0b11, 0b11, False)) == 21   # MPEG-1 mono
    assert _xing_offset(_hdr(0b10, 0b00, False)) == 21   # MPEG-2 stereo
    assert _xing_offset(_hdr(0b10, 0b11, False)) == 13   # MPEG-2 mono
    assert _xing_offset(_hdr(0b00, 0b00, False)) == 21   # MPEG-2.5 stereo
    assert _xing_offset(_hdr(0b00, 0b11, False)) == 13   # MPEG-2.5 mono


def test_xing_offset_ignores_crc():
    # regression: a CRC-protected first frame keeps the tag at the SAME offset;
    # a +2 for the CRC bytes would look past the tag and miss it.
    for ver in (0b11, 0b10, 0b00):
        for ch in (0b00, 0b11):
            assert (_xing_offset(_hdr(ver, ch, True))
                    == _xing_offset(_hdr(ver, ch, False)))


def _mp3_with_first_frame(pad):
    """An ID3v2 tag then one frame header, so the sniffer recognises it.

    A bare frame header does not sniff as mp3 -- identification wants a second
    frame to confirm -- so the tag is what makes this reach the walker at all.
    A synchsafe size of zero keeps the tag at its 10-byte header.
    """
    return (b"ID3" + bytes([4, 0, 0, 0, 0, 0, 0])
            + bytes([0xFF, 0xFB, 0x90, 0x00]) + b"\x00" * pad)


def test_a_frame_that_runs_past_the_end_says_so(tmp_path):
    """A frame header states its own length, and a truncated file can state one
    longer than the bytes that follow. The geometry engine marks the chunk
    invalid either way, but a chunk claiming bytes past the end with NO warning
    reads as a fact: 417 bytes reported in a 414-byte file, silently.

    Latent since 1.0.0 and unreachable until the synchsafe mask let the walker
    find a frame here at all. The seeded mp3 is a clean CBR stream whose frames
    fit, which is why the geometry invariant never saw it.
    """
    from acidcat.core.walk import walk_file

    p = tmp_path / "cut.mp3"
    p.write_bytes(_mp3_with_first_frame(400))          # the frame wants 417
    _label, chunks, _warns = walk_file(str(p))
    f0 = [c for c in chunks if c["id"] == "frame0"][0]
    assert any("follow it" in w for w in (f0["warnings"] or [])), (
        "frame0 claims %d bytes in a %d-byte file and says nothing: %s"
        % (f0["size"], p.stat().st_size, f0["warnings"]))


def test_a_frame_that_fits_says_nothing(tmp_path):
    """The control. A warning that fires on a whole frame is noise, and this
    one would fire on every well-formed MP3 in the corpus."""
    from acidcat.core.walk import walk_file

    p = tmp_path / "whole.mp3"
    p.write_bytes(_mp3_with_first_frame(900))
    _label, chunks, _warns = walk_file(str(p))
    f0 = [c for c in chunks if c["id"] == "frame0"][0]
    assert not any("follow it" in w for w in (f0["warnings"] or [])), f0["warnings"]


# ── regressions from the 2026-10-02 bug hunt ──

def _frame(fid, body):
    return fid + len(body).to_bytes(4, "big") + b"\x00\x00" + body


def _tag(frames):
    body = b"".join(frames)
    size = bytes([(len(body) >> s) & 0x7F for s in (21, 14, 7, 0)])
    return b"ID3\x04\x00\x00" + size + body


def test_a_zero_size_frame_is_stepped_over_not_the_end():
    from acidcat.core.formats.mp3 import id3v2_from_bytes
    data = _tag([b"TCOP" + b"\x00" * 6, _frame(b"TBPM", b"\x00126")])
    header, frames, warns = id3v2_from_bytes(data)
    assert ("TBPM", "126") in frames
    assert [w.code for w in warns] == ["value.invalid"]


def test_a_tag_cut_by_the_reader_is_a_cap_not_an_overrun():
    from acidcat.core.formats.mp3 import id3v2_from_bytes
    data = _tag([_frame(b"TIT2", b"\x00Kick"), _frame(b"APIC", b"\x00" * 5000)])
    header, frames, warns = id3v2_from_bytes(data[:200], whole=len(data))
    assert ("TIT2", "Kick") in frames
    assert [w.code for w in warns] == ["cap.payload"]
    # the same cut with no container size is the file running short
    _h, _f, warns = id3v2_from_bytes(data[:200])
    assert [w.code for w in warns] == ["size.overrun"]


def test_a_riff_id3_chunk_past_the_payload_cap_is_not_damage(tmp_path):
    import struct
    from acidcat.core.walk import walk_file
    tag = _tag([_frame(b"TIT2", b"\x00Kick"), _frame(b"APIC", b"\x00" * 70000)])
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", 4) + b"\x00" * 4
            + b"id3 " + struct.pack("<I", len(tag)) + tag + b"\x00" * (len(tag) & 1))
    p = tmp_path / "big_id3.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    _label, chunks, warns = walk_file(str(p))
    codes = {getattr(w, "code", None) for c in chunks for w in c.get("warnings", [])}
    codes |= {getattr(w, "code", None) for w in warns}
    assert "size.overrun" not in codes and "cap.payload" in codes



def test_text_tags_that_read_as_numbers_stay_text_in_json(tmp_path):
    # a title "05" became the number 5; a BPM, which the ID3 spec defines as a
    # numeric string, is still a number
    import json
    import struct
    import subprocess
    import sys
    tag = _tag([_frame(b"TIT2", b"\x0005"), _frame(b"TBPM", b"\x00126")])
    info = b"INFO" + b"INAM" + struct.pack("<I", 3) + b"05\x00" + b"\x00"
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", 4) + b"\x00" * 4
            + b"LIST" + struct.pack("<I", len(info)) + info
            + b"id3 " + struct.pack("<I", len(tag)) + tag + b"\x00" * (len(tag) & 1))
    p = tmp_path / "05.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    r = subprocess.run([sys.executable, "-m", "acidcat", "inspect", "--json", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout.splitlines()[0])
    got = {}

    def walk(nodes):
        for n in nodes:
            for f in n.get("fields", []):
                got[f["name"]] = f["value"]
            walk(n.get("children", []))
    walk(doc["nodes"])
    assert got["INAM"] == "05" and got["TIT2"] == "05"
    assert got["TBPM"] == 126
