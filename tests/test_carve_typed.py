"""Tests for `acidcat carve` typed / struct / field / anchored modes."""

import struct

from acidcat.commands import carve


class _Args:
    def __init__(self, **kw):
        d = {"target": None, "offset": None, "length": None, "end": None,
             "trailing": False, "chunk": None, "raw": False, "output": None,
             "quiet": True, "at": None, "type": None, "count": 1,
             "endian": "be", "struct": None, "field": None, "format": None, "batch": None}
        d.update(kw)
        for k, v in d.items():
            setattr(self, k, v)


def _bfdc(tmp_path):
    fmt = b"fmt " + struct.pack(">I", 20) + struct.pack(">IIIII", 24, 10, 100, 44100, 2)
    idx = b"Indx" + struct.pack(">I", 16) + struct.pack(">IIII", 1024, 2, 0, 15101)
    data = b"data" + struct.pack(">I", 8) + b"\x0a" * 8
    body = fmt + idx + data
    blob = b"BFDC" + struct.pack(">I", len(body)) + body
    p = tmp_path / "s.bfdlac"
    p.write_bytes(blob)
    return str(p)


def test_typed_value(tmp_path, capsys):
    p = _bfdc(tmp_path)
    assert carve.run(_Args(target=p, at="0x1c", type="u32be")) == 0
    assert capsys.readouterr().out.strip() == "44100"


def test_typed_via_chunk_anchor(tmp_path, capsys):
    p = _bfdc(tmp_path)
    carve.run(_Args(target=p, at="chunk:fmt+8", type="u32be"))
    assert capsys.readouterr().out.strip() == "24"       # bits_per_sample


def test_typed_array(tmp_path, capsys):
    p = _bfdc(tmp_path)
    carve.run(_Args(target=p, at="chunk:Indx+16", type="u32be", count=2))
    assert capsys.readouterr().out.split() == ["0", "15101"]


def test_endian_both(tmp_path, capsys):
    p = _bfdc(tmp_path)
    carve.run(_Args(target=p, at="0x1c", type="u32", endian="both"))
    out = capsys.readouterr().out
    assert "be=44100" in out and "le=" in out


def test_struct(tmp_path, capsys):
    p = _bfdc(tmp_path)
    carve.run(_Args(target=p, struct="@0x10 bits:u32be _:u32be samples:u32be "
                                     "rate:u32be ch:u32be"))
    out = capsys.readouterr().out
    assert "bits" in out and "44100" in out
    assert "_" not in out                                # skipped field not shown


def test_field_by_name(tmp_path, capsys):
    p = _bfdc(tmp_path)
    carve.run(_Args(target=p, field="sample_rate"))
    assert capsys.readouterr().out.strip() == "44100"


def test_field_unknown_lists_available(tmp_path, capsys):
    p = _bfdc(tmp_path)
    assert carve.run(_Args(target=p, field="nope")) == 2
    assert "available" in capsys.readouterr().err


def test_format_hex(tmp_path, capsys):
    p = _bfdc(tmp_path)
    carve.run(_Args(target=p, offset="0", length="4", format="hex"))
    assert capsys.readouterr().out.strip() == "42 46 44 43"      # "BFDC"


def test_format_py(tmp_path, capsys):
    p = _bfdc(tmp_path)
    carve.run(_Args(target=p, offset="0", length="4", format="py"))
    assert capsys.readouterr().out.strip() == "b'BFDC'"


def test_raw_range_still_works(tmp_path):
    # raw bytes to a file (the classic path, unchanged)
    p = _bfdc(tmp_path)
    out = tmp_path / "o.bin"
    assert carve.run(_Args(target=p, offset="0", length="4", output=str(out))) == 0
    assert out.read_bytes() == b"BFDC"
