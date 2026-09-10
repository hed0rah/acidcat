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
