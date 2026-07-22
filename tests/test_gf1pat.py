"""Tests for the Gravis UltraSound GF1 patch (.PAT) walker."""

import struct

from acidcat.core import sniff as sniffmod
from acidcat.core.walk import walk_file
from acidcat.core.walk.gf1pat import inspect_gf1pat, parse_gf1


def gf1_patch(pcm, bits16=False, unsigned=True, rate=22050, name=b"snare"):
    modes = (0x01 if bits16 else 0) | (0x02 if unsigned else 0)
    hdr = bytearray(129)
    hdr[0:12] = b"GF1PATCH110\x00"
    hdr[82] = 1                                          # instruments
    inst = bytearray(63); inst[22] = 1                   # layers
    layer = bytearray(47); layer[6] = 1                  # samples in layer
    sh = bytearray(96)
    sh[0:len(name)] = name
    struct.pack_into("<I", sh, 8, len(pcm))              # data_size
    struct.pack_into("<H", sh, 20, rate)                 # sample_rate
    sh[55] = modes
    return bytes(hdr + inst + layer + sh) + pcm


def _write(tmp_path, data):
    p = tmp_path / "x.pat"
    p.write_bytes(data)
    return str(p)


def test_sniff():
    assert sniffmod.sniff_bytes(b"GF1PATCH110\x00" + b"\x00" * 20) == "gf1pat"


def test_walk(tmp_path):
    p = _write(tmp_path, gf1_patch(b"\x80" * 64, rate=44100))
    label, chunks, warns = walk_file(p)
    assert label == "Gravis UltraSound patch"
    assert chunks[0]["id"] == "GF1"
    smp = chunks[1]
    assert smp["size"] == 64 and "44100 Hz" in smp["summary"]
    assert "8-bit" in smp["summary"] and "unsigned" in smp["summary"]


def test_parse_fields(tmp_path):
    info = parse_gf1(gf1_patch(b"\x00" * 100, bits16=True, unsigned=False, rate=32000))
    assert len(info["samples"]) == 1
    s = info["samples"][0]
    assert s["data_size"] == 100 and s["rate"] == 32000
    assert s["bits16"] and not s["unsigned"]


def test_not_gf1(tmp_path):
    chunks, warns = inspect_gf1pat(_write(tmp_path, b"RIFF" + b"\x00" * 40))
    assert chunks == [] and warns and "GF1PATCH" in warns[0]
