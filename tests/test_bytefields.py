"""Tests for the typed-byte / offset-resolution engine (core/bytefields.py)."""

import struct

import pytest

from acidcat.core import bytefields as bf


def test_parse_type_ints_and_endian():
    assert bf.parse_type("u32", ">") == ("num", "I", 4, ">")
    assert bf.parse_type("u32le", ">") == ("num", "I", 4, "<")
    assert bf.parse_type("i16be", "<") == ("num", "h", 2, ">")
    assert bf.parse_type("f32", ">")[0] == "num"


def test_parse_type_strings():
    assert bf.parse_type("4s", ">") == ("str", None, 4, ">")
    assert bf.parse_type("cstr", ">")[0] == "cstr"


def test_parse_type_bad():
    with pytest.raises(bf.FieldError):
        bf.parse_type("u33", ">")


def test_decode_num():
    assert bf.decode(struct.pack(">I", 44100), bf.parse_type("u32be")) == 44100
    assert bf.decode(struct.pack("<i", -5), bf.parse_type("i32le")) == -5


def test_decode_strings():
    assert bf.decode(b"BFDC\x00\x00", bf.parse_type("4s")) == "BFDC"
    assert bf.decode(b"name\x00rest", bf.parse_type("cstr")) == "name"


def test_decode_both_endian():
    both = bf.decode_both_endian(struct.pack(">I", 44100), "u32")
    assert both["be"] == 44100 and both["le"] != 44100


def test_type_size_cstr_dynamic():
    p = bf.parse_type("cstr")
    assert bf.type_size(p, b"abc\x00xyz") == 4          # includes terminator
    assert bf.type_size(bf.parse_type("u16")) == 2


def test_split_delta():
    assert bf._split_delta("chunk:Indx+8") == ("chunk:Indx", 8)
    assert bf._split_delta("end-16") == ("end", -16)
    assert bf._split_delta("0x1c") == ("0x1c", 0)
    assert bf._split_delta("find:BFDi+0x10") == ("find:BFDi", 16)


def _bfdc(tmp_path):
    fmt = b"fmt " + struct.pack(">I", 20) + struct.pack(">IIIII", 24, 10, 100, 44100, 2)
    data = b"data" + struct.pack(">I", 8) + b"\x0a" * 8
    body = fmt + data
    blob = b"BFDC" + struct.pack(">I", len(body)) + body
    p = tmp_path / "s.bfdlac"
    p.write_bytes(blob)
    return str(p), len(blob)


def test_resolve_absolute_and_end(tmp_path):
    p, size = _bfdc(tmp_path)
    assert bf.resolve_offset("0x08", p, size) == 8
    assert bf.resolve_offset("end", p, size) == size
    assert bf.resolve_offset("end-4", p, size) == size - 4


def test_resolve_find(tmp_path):
    p, size = _bfdc(tmp_path)
    off = bf.resolve_offset("find:data", p, size)
    with open(p, "rb") as f:
        assert f.read()[off:off + 4] == b"data"
    assert bf.resolve_offset("find:data+8", p, size) == off + 8


def test_resolve_chunk_via_walker(tmp_path):
    p, size = _bfdc(tmp_path)
    assert bf.resolve_offset("chunk:fmt", p, size) == 8          # fmt id starts at 8
    assert bf.resolve_offset("chunk:fmt+8", p, size) == 16       # its payload


def test_resolve_missing(tmp_path):
    p, size = _bfdc(tmp_path)
    with pytest.raises(bf.FieldError):
        bf.resolve_offset("find:NOPE", p, size)
    with pytest.raises(bf.FieldError):
        bf.resolve_offset("chunk:ZZZZ", p, size)
