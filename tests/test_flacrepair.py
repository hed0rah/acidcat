"""FLAC structural repair: the metadata-block last-flag and PADDING zero-fill,
witnessed by the audio frame sync and the spec, through the constraint framework."""
from acidcat.core import constraints as C
from acidcat.core import flacrepair as F


def _blk(last, btype, body):
    return bytes([(0x80 if last else 0) | btype]) + len(body).to_bytes(3, "big") + body


def _flac(*blocks, audio=b"\xff\xf8" + b"\x00" * 16):
    return b"fLaC" + b"".join(blocks) + audio


def _healthy():
    # STREAMINFO (not last) + PADDING all-zero (last), then audio
    return _flac(_blk(False, 0, b"\x00" * 34), _blk(True, 1, b"\x00" * 8))


def test_healthy_flac_is_noop():
    data = _healthy()
    assert F.analyze(data) == []
    new, changes = F.repair_flac(data)
    assert new == data and changes == []


def test_nonzero_padding_zeroed():
    data = _flac(_blk(False, 0, b"\x00" * 34), _blk(True, 1, b"junkjunk"))
    new, changes = F.repair_flac(data)
    assert any(c["field"] == "padding" for c in changes)
    # the padding body is now zero, audio untouched
    _blocks, start, _ok = F.walk(new)
    assert new[start:] == data[start:]                # audio frames identical
    pad_body = new[4 + 38 + 4:4 + 38 + 4 + 8]
    assert pad_body == b"\x00" * 8
    assert len(new) == len(data)


def test_misplaced_last_flag_corrected():
    # last-flag wrongly on STREAMINFO; the real last block is the PADDING
    data = _flac(_blk(True, 0, b"\x00" * 34), _blk(False, 1, b"\x00" * 8))
    new, changes = F.repair_flac(data)
    assert any(c["field"] == "last_flag" for c in changes)
    assert not (new[4] & 0x80)                         # STREAMINFO flag cleared
    assert new[4 + 38] & 0x80                          # PADDING flag set


def test_framework_dispatches_flac_and_witnesses():
    data = _flac(_blk(False, 0, b"\x00" * 34), _blk(True, 1, b"xx"))
    report = C.analyze(data)
    assert report.label == "FLAC"
    pad = next(v for v in report.violations if v.field == "padding")
    assert pad.kind == "zero" and pad.repairable
    new, _rep = C.repair(data)
    assert C.analyze(new).violations == []             # fixed and idempotent


def test_repair_refuses_when_chain_not_witnessed():
    # no frame sync after the blocks -> the last-flag boundary is not witnessed,
    # so only spec-witnessed padding is touched, never the flag
    data = b"fLaC" + _blk(False, 0, b"\x00" * 34) + _blk(False, 1, b"\x00" * 4)
    # (ends without a 0xFFF8 sync)
    vios = F.analyze(data)
    assert all(v["field"] != "last_flag" for v in vios)
