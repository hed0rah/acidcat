"""Kurzweil .KRZ walker: a hermetic minimal bank plus a corpus smoke test that
skips when the local specimen library is absent.

The container framing (PRAM header, negative-blocksize object walk, hash =
type<<10|id, int32 end marker) was verified against 242 real Sweetwater banks;
this hermetic fixture pins the walker's decode of one Sample + one Program so a
regression is caught without the corpus.
"""

import glob
import os
import struct

import pytest

from acidcat.core.walk import walk_file
from acidcat.core.walk.krz import inspect_krz


def _object(type_code, oid, name, body):
    """Build one KRZ object block: negative blocksize, hash, size, name_ofs,
    name (padded), body."""
    n = len(name)
    pad = b"\x00" if n % 2 else b"\x00\x00"
    name_field = name.encode("ascii") + pad
    ofs = n + (3 if n % 2 else 4)                  # name_len + 3 (odd) / +4 (even)
    hash_ = (type_code << 10) | oid
    # block = blocksize(4) + hash(2) + size(2) + ofs(2) + name_field + body
    inner = struct.pack(">HHH", hash_, 0, ofs) + name_field + body
    block = inner
    total = 4 + len(block)
    total += (-total) % 4                          # pad block to 4-byte boundary
    block = block + b"\x00" * (total - 4 - len(block))
    return struct.pack(">i", -total) + block


def _sample_body(rootkey=60, rate=44100, one_shot=False):
    period = round(1e9 / rate)
    flags = 0xF0 if one_shot else 0x70
    ksample = struct.pack(">hhhBBhh", 1, 0, 8, 0, 0, 0, 0)
    sfh = (struct.pack(">BBBB", rootkey, flags, 0, 0)
           + struct.pack(">HH", 0, 0)               # maxPitch, offsetToName
           + struct.pack(">iiii", 0, 0, 100, 200)   # start, alt, loopStart, end
           + struct.pack(">HH", 8, 6)               # env offsets
           + struct.pack(">I", period))
    envs = struct.pack(">hhhhhh", -1, 1, 0, 0, -1600, 0) * 2
    return ksample + sfh + envs


def _program_body(layers=2):
    seg = b""
    seg += bytes([0x08]) + b"\x00" * 15             # PGM
    seg += bytes([0x0F]) + b"\x00" * 7              # FX (the 7-byte exception)
    for _ in range(layers):
        seg += bytes([0x09]) + b"\x00" * 15         # LYR
        seg += bytes([0x40]) + b"\x00" * 31         # CAL
    seg += struct.pack(">h", 0)                     # terminator
    return seg


def _bank(objects, pcm=b""):
    body = b"".join(objects) + struct.pack(">i", 0)  # objects + end marker
    osize = 32 + len(body)
    # 32-byte header: magic(4) + osize(4) + rest[0..5] (24), rest[2]@16 = version
    header = b"PRAM" + struct.pack(">i", osize) + struct.pack(">iii", 0, 0, 207) \
        + b"\x00" * 12
    return header + body + pcm


def test_minimal_bank_decodes(tmp_path):
    # the rate is stored as an integer samplePeriod = round(1e9/rate), so the
    # decoded rate is round(1e9/period) -- a small deterministic round-trip shift
    # (48000 -> 48001) inherent to the format, reproduced exactly here
    src_rate = 48000
    rt_rate = round(1e9 / round(1e9 / src_rate))
    objs = [
        _object(38, 200, "TestSample", _sample_body(rootkey=60, rate=src_rate)),
        _object(37, 200, "TestKeymap",
                struct.pack(">HHHHHH", 200, 0x13, 0, 100, 127, 5)
                + b"\x00" * 16 + struct.pack(">hHB", 0, 200, 1) * 4),
        _object(36, 200, "TestProgram", _program_body(layers=2)),
    ]
    p = tmp_path / "bank.krz"
    p.write_bytes(_bank(objs, pcm=b"\x00\x00" * 100))
    label, chunks, warns = walk_file(str(p))
    assert label.startswith("Kurzweil")
    ids = [c["id"] for c in chunks]
    assert ids[0] == "PRAM"
    assert "Sample" in ids and "Keymap" in ids and "Program" in ids
    assert "PCM" in ids

    sample = next(c for c in chunks if c["id"] == "Sample")
    assert f"{rt_rate} Hz" in sample["summary"]
    assert "root C3" in sample["summary"]          # MIDI 60
    program = next(c for c in chunks if c["id"] == "Program")
    layers = next(f for f in program["fields"] if f["name"] == "layers")
    assert layers["value"] == 2                    # FX-exception fix keeps this synced


def test_one_shot_sample_flag(tmp_path):
    p = tmp_path / "os.krz"
    p.write_bytes(_bank([_object(38, 200, "OS", _sample_body(one_shot=True))]))
    _, chunks, _ = walk_file(str(p))
    sample = next(c for c in chunks if c["id"] == "Sample")
    assert "one-shot" in sample["summary"]


def test_srom_recognized(tmp_path):
    p = tmp_path / "fx.krz"
    p.write_bytes(b"SROM" + struct.pack(">I", 1000) + b"\x00" * 100)
    _, chunks, _ = walk_file(str(p))
    assert chunks[0]["id"] == "SROM"


def test_truncated_header_degrades(tmp_path):
    p = tmp_path / "trunc.krz"
    p.write_bytes(b"PRAM\x00\x00")
    chunks, warns = inspect_krz(str(p))
    assert chunks == []
    assert warns and "32" in warns[0]


_CORPUS = "C:/Users/joshr/Downloads/kurzweil_docs/SWEETWTR"


@pytest.mark.skipif(not os.path.isdir(_CORPUS), reason="local KRZ corpus absent")
def test_corpus_never_raises():
    files = glob.glob(_CORPUS + "/**/*.krz", recursive=True) \
        + glob.glob(_CORPUS + "/**/*.KRZ", recursive=True)
    for f in files:
        walk_file(f)                               # must not raise on any specimen
