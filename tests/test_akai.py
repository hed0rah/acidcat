"""Akai .akp (S5000/S6000) program walker: the RIFF/APRG keygroup structure and
the per-keygroup zone sample references.

The fixture is synthesized here from the documented IFF layout -- no real pack."""
import struct

from acidcat.core.sniff import sniff
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
    from acidcat.core.sniff import sniff_bytes
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
