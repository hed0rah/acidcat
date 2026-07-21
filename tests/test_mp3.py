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
