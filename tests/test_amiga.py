"""Tests for the Amiga music-format walkers (SMUS, OKT, MED, Future Composer)."""

import struct

from acidcat.core import sniff as sniffmod
from acidcat.core.walk import walk_file


def _chunk(cid, payload):
    return cid + struct.pack(">I", len(payload)) + payload + (
        b"\x00" if len(payload) & 1 else b"")


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# ── SMUS ────────────────────────────────────────────────────────────────────

def test_smus_sniff_and_decode(tmp_path):
    inner = (_chunk(b"SHDR", struct.pack(">HBB", 120, 100, 4))
             + _chunk(b"NAME", b"Test Score")
             + _chunk(b"INS1", b"\x00" * 8) + _chunk(b"INS1", b"\x00" * 8)
             + _chunk(b"TRAK", b"\x00" * 16))
    data = b"FORM" + struct.pack(">I", len(b"SMUS" + inner)) + b"SMUS" + inner
    p = _write(tmp_path, "s.smus", data)
    assert sniffmod.sniff(p) == "smus"
    label, chunks, warns = walk_file(p)
    assert label.startswith("IFF/SMUS")
    shdr = next(c for c in chunks if c["id"] == "SHDR")
    fv = {f["name"]: f["value"] for f in shdr["fields"]}
    assert fv["tempo"] == 120 and fv["volume"] == 100 and fv["ctTrack"] == 4
    assert "'Test Score'" in chunks[0]["summary"]
    assert "2 instrument(s)" in chunks[0]["summary"]


# ── OKT ─────────────────────────────────────────────────────────────────────

def test_okt_channels_and_samples(tmp_path):
    cmod = _chunk(b"CMOD", struct.pack(">HHHH", 1, 0, 1, 0))   # 2 split -> 6 voices
    samp = _chunk(b"SAMP", b"kick".ljust(20, b"\x00") + b"\x00" * 12
                  + b"snare".ljust(20, b"\x00") + b"\x00" * 12)  # 2 entries
    data = b"OKTASONG" + cmod + samp
    p = _write(tmp_path, "o.okt", data)
    assert sniffmod.sniff(p) == "okt"
    label, chunks, _ = walk_file(p)
    assert label == "Oktalyzer module"
    cm = next(c for c in chunks if c["id"] == "CMOD")
    assert "6 voices" in cm["summary"]
    sm = next(c for c in chunks if c["id"] == "SAMP")
    assert "2 entr" in sm["summary"]
    assert "'kick'" in chunks[0]["summary"]


# ── MED ─────────────────────────────────────────────────────────────────────

def test_med_recognized(tmp_path):
    data = b"MMD1" + struct.pack(">I", 40) + b"\x00" * 40
    p = _write(tmp_path, "m.med", data)
    assert sniffmod.sniff(p) == "med"
    label, chunks, _ = walk_file(p)
    assert label.startswith("MED")
    assert "OctaMED" in chunks[0]["summary"]         # MMD1 = OctaMED


def test_med_modlen_mismatch_flagged(tmp_path):
    data = b"MMD0" + struct.pack(">I", 999999) + b"\x00" * 20
    _, chunks, _ = walk_file(_write(tmp_path, "m2.med", data))
    assert any("modlen" in w for w in chunks[0]["warnings"])


# ── Future Composer ─────────────────────────────────────────────────────────

def test_fc_versions(tmp_path):
    for magic, ver in ((b"SMOD", "1.3"), (b"FC14", "1.4")):
        data = magic + struct.pack(">I", 100) + b"\x00" * 100
        p = _write(tmp_path, f"f_{ver}.fc", data)
        assert sniffmod.sniff(p) == "fc"
        _, chunks, _ = walk_file(p)
        assert f"v{ver}" in chunks[0]["summary"]


def test_non_amiga_declined(tmp_path):
    from acidcat.core.walk.amiga import inspect_smus, inspect_okt
    d1 = b"FORM" + struct.pack(">I", 8) + b"AIFF"
    assert inspect_smus(_write(tmp_path, "x.aiff", d1))[0] == []
    assert inspect_okt(_write(tmp_path, "x.bin", b"NOTOKTAS"))[0] == []
