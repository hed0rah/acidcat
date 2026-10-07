"""Akai .akp (S5000/S6000) program walker: the RIFF/APRG keygroup structure and
the per-keygroup zone sample references.

The fixture is synthesized here from the documented IFF layout -- no real pack."""
import struct

from acidcat.core.infra.sniff import sniff
from acidcat.core.walk import akai
from acidcat.core.walk.base import Unsupported


def _chunk(tag, body):
    return tag + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) & 1 else b"")


def _zone(name):
    n = name.encode("latin-1")
    body = (bytes([1, len(n)]) + n).ljust(46, b"\x00")
    return _chunk(b"zone", body)


def _kgrp(low, high, samples):
    kloc = _chunk(b"kloc", bytes([1, 3, 1, 4, low, high]).ljust(16, b"\x00"))
    return _chunk(b"kgrp", kloc + b"".join(_zone(s) for s in samples))


def _make_akp(tmp_path, name="Test Prog", keygroups=(("Kick", 0, 63),
                                                     ("Snare", 64, 127)),
              declared=None):
    n = declared if declared is not None else len(keygroups)
    prg = _chunk(b"prg ", bytes([1, 5, n, 0, 2, 0]))
    body = b"APRG" + prg + b"".join(_kgrp(lo, hi, [s]) for s, lo, hi in keygroups)
    p = tmp_path / (name + ".akp")
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p)


def test_akp_sniffs_by_riff_form():
    assert sniff  # imported ok
    from acidcat.core.infra.sniff import sniff_bytes
    assert sniff_bytes(b"RIFF\x00\x00\x00\x00APRG") == "akp"
    assert sniff_bytes(b"RIFF\x00\x00\x00\x00WAVE") == "wav"   # unchanged


def test_akp_program_and_keygroups(tmp_path):
    p = _make_akp(tmp_path, name="12 STRING 4")
    assert sniff(p) == "akp"
    chunks, warns = akai.inspect_akp(p)
    assert warns == []
    f = {x["name"]: x["value"] for x in chunks[0]["fields"]}
    assert f["program_name"] == "12 STRING 4"
    assert f["keygroups"] == 2
    assert f["midi_program"] == 5
    assert f["referenced_samples"] == 2
    kgs = [c for c in chunks if c["id"].startswith("kgrp")]
    assert len(kgs) == 2
    kf = {x["name"]: x["value"] for x in kgs[0]["fields"]}
    assert kf["key_range"] == "0-63"
    assert kf["zone[0]"] == "Kick"
    # the keygroup chunk points at a real byte region
    raw = open(p, "rb").read()
    assert raw[kgs[0]["offset"]:kgs[0]["offset"] + 4] == b"kloc"


def test_akp_dedupes_shared_samples(tmp_path):
    p = _make_akp(tmp_path, keygroups=(("Pad", 0, 40), ("Pad", 41, 80),
                                       ("Bell", 81, 127)))
    chunks, _ = akai.inspect_akp(p)
    f = {x["name"]: x["value"] for x in chunks[0]["fields"]}
    assert f["referenced_samples"] == 2            # Pad counted once


def test_akp_keygroup_count_mismatch_warns(tmp_path):
    p = _make_akp(tmp_path, declared=9)            # prg says 9, only 2 kgrp present
    _, warns = akai.inspect_akp(p)
    assert any("declares 9 keygroups" in w for w in warns)


def test_akp_rejects_non_aprg(tmp_path):
    p = tmp_path / "x.akp"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    try:
        akai.inspect_akp(str(p))
        assert False, "expected Unsupported"
    except Unsupported:
        pass


def _zero_size(p):
    raw = bytearray(open(p, "rb").read())
    raw[4:8] = bytes(4)
    open(p, "wb").write(bytes(raw))
    return bytes(raw)


def test_a_zero_riff_size_is_akais_convention_not_damage(tmp_path):
    """The S5000/S6000 writes 0 in an .akp's RIFF size and most real programs
    carry it: check must not call them broken, and --fix must not rewrite one."""
    from acidcat.core.write import constraints
    raw = _zero_size(_make_akp(tmp_path))
    rep = constraints.analyze(raw)
    assert rep.violations == []
    assert "Akai" in rep.note
    new, _rep = constraints.repair(raw)
    assert new == raw


def test_a_wrong_nonzero_riff_size_is_still_reported(tmp_path):
    from acidcat.core.write import constraints
    p = _make_akp(tmp_path)
    raw = bytearray(open(p, "rb").read())
    raw[4:8] = (len(raw) - 100).to_bytes(4, "little")
    rep = constraints.analyze(bytes(raw))
    assert [(v.path, v.field) for v in rep.violations] == [("RIFF", "size")]


def test_a_zero_riff_size_does_not_hide_an_overrunning_last_chunk(tmp_path):
    """The size-0 filter dropped the only signal the IFF model has for a
    chunk running past EOF, so check called a truncated program consistent.
    The 0 is the convention; the chunks must still end where the file does."""
    from acidcat.core.write import constraints
    raw = bytearray(_zero_size(_make_akp(tmp_path)))
    last = raw.rfind(b"kgrp")
    size = struct.unpack_from("<I", raw, last + 4)[0]
    struct.pack_into("<I", raw, last + 4, size + 64)       # 64 bytes past EOF
    rep = constraints.analyze(bytes(raw))
    assert [(v.path, v.field, v.repairable) for v in rep.violations] == [
        ("RIFF", "size", False)]
    assert "overruns the file" in rep.violations[0].describe()
    new, rep = constraints.repair(bytes(raw))
    assert new == bytes(raw) and not rep.repairable          # nothing to write


def test_fix_refuses_a_pad_byte_that_is_part_of_the_next_chunk_id(tmp_path):
    """prg's size one too large swallows the 'o' of the next "out ", and the
    parse reads the 'u' as prg's pad. --fix zeroed it, writing new damage into
    a chunk id; APRG has no audio payload, so no audio guard stopped it.
    The layout is the real S5000 one: prg, then out (8 bytes), then kgrps."""
    import pytest
    from acidcat.cli import main
    from acidcat.core.write import constraints
    from acidcat.core.write.repairers import AudioGuardError
    body = (b"APRG" + _chunk(b"prg ", bytes([1, 7, 1, 0, 2, 0]))
            + _chunk(b"out ", bytes(8)) + _kgrp(0, 127, ["Kick"]))
    for size in (len(body), 0):
        raw = bytearray(b"RIFF" + struct.pack("<I", size) + body)
        struct.pack_into("<I", raw, raw.find(b"prg ") + 4, 7)
        raw = bytes(raw)
        p = tmp_path / "desync.akp"
        p.write_bytes(raw)
        rep = constraints.analyze(raw)
        assert "'out '" in rep.violations[0].describe()
        assert not rep.repairable                 # check must not offer --fix
        with pytest.raises(AudioGuardError):
            constraints.repair(raw)
        assert main(["check", "--fix", "--overwrite", str(p)]) == 1
        assert p.read_bytes() == raw              # nothing written


def test_a_zero_riff_size_survives_another_repair(tmp_path):
    """A program with a real fault elsewhere is fixed there and keeps its 0."""
    from acidcat.core.write import constraints
    p = _make_akp(tmp_path)
    raw = _zero_size(p) + b"junk" + (3).to_bytes(4, "little") + b"abc" + b"U"
    new, rep = constraints.repair(raw)
    assert [v.field for v in rep.violations] == ["pad_byte"]
    assert new[4:8] == bytes(4) and new[-1:] == bytes(1)
