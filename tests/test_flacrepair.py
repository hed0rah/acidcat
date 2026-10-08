"""FLAC structural repair: the metadata-block last-flag and PADDING zero-fill,
witnessed by the audio frame sync and the spec, through the constraint framework."""
from acidcat.core.write import constraints as C
from acidcat.core.write import flacrepair as F


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


def test_nonzero_padding_is_filler_not_a_failed_check(tmp_path):
    """Junk in a PADDING block is the same harmless filler as a RIFF pad byte
    (decisions.md F1): check passes the file, --fix still zeroes it."""
    from acidcat.cli import main
    from acidcat.core.write import constraints
    data = _flac(_blk(False, 0, b"\x00" * 34), _blk(True, 1, b"junkjunk"))
    rep = constraints.analyze(data)
    assert [v.field for v in rep.violations] == ["padding"] and rep.defects == []
    p = tmp_path / "pad.flac"
    p.write_bytes(data)
    assert main(["check", str(p)]) == 0


# non-zero PADDING is filler only inside an intact chain and only when it
# holds nothing a reader needs: a PADDING length that swallowed audio frames,
# or tags whose type byte now reads PADDING, is damage, and --fix must not
# zero it (it zeroed real audio and tags, exit 0)

def _frames(n=3):
    return b"".join(b"\xff\xf8" + bytes([0x69, 0x08, 0, 0x10 + i]) + b"\x55" * 26
                    for i in range(n))


def _swallowed_tail():
    # PADDING length grown into the first frame: the walk then lands mid-frame
    good = _flac(_blk(False, 0, b"\x00" * 34), _blk(True, 1, b"\x00" * 8),
                 audio=_frames())
    d = bytearray(good)
    d[4 + 38 + 1:4 + 38 + 4] = (8 + 10).to_bytes(3, "big")
    return bytes(d)


def _swallowed_frame():
    # PADDING length grown to exactly the second frame: the chain is ok
    good = _flac(_blk(False, 0, b"\x00" * 34), _blk(True, 1, b"\x00" * 8),
                 audio=_frames())
    d = bytearray(good)
    d[4 + 38 + 1:4 + 38 + 4] = (8 + 32).to_bytes(3, "big")
    return bytes(d)


def _tags_typed_padding():
    vendor = b"reference libFLAC 1.3.2 20170101"
    vc = len(vendor).to_bytes(4, "little") + vendor + (0).to_bytes(4, "little")
    return _flac(_blk(False, 0, b"\x00" * 34), _blk(True, 1, vc))


def test_padding_that_is_not_filler_is_a_defect_and_kept(tmp_path):
    from acidcat.cli import main
    for name, data in (("tail", _swallowed_tail()), ("frame", _swallowed_frame()),
                       ("tags", _tags_typed_padding())):
        rep = C.analyze(data)
        pad = [v for v in rep.violations if v.field == "padding"]
        assert len(pad) == 1, name
        assert not pad[0].filler and not pad[0].repairable, name
        assert pad[0] in rep.defects and "not filler" in pad[0].describe(), name
        assert F.repair_flac(data)[0] == data, name   # never zeroed
        p = tmp_path / f"{name}.flac"
        p.write_bytes(data)
        assert main(["check", str(p)]) == 1, name
        assert main(["check", "--fix", "--overwrite", str(p)]) == 1, name
        assert p.read_bytes() == data, name


def test_vorbis_tell_needs_the_whole_vendor_string():
    # a length that does not fit, or bytes that are not text, is not a tell
    assert not F._vorbis_like(b"junkjunk")
    assert not F._vorbis_like((3).to_bytes(4, "little") + b"a\x01c")
    assert F._vorbis_like((3).to_bytes(4, "little") + b"abc")
