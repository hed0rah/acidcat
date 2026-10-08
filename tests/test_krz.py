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


def test_keymap_method_03_three_byte_entries(tmp_path):
    # real Sweetwater banks use keymap method 0x03: a 3-byte entry whose
    # sampleID sits at offset 0 (no tuning prefix), unlike the 5-byte 0x13
    # layout where it sits at offset 2. regression: the id was always read at
    # offset 2, so a 0x03 keymap referencing sample 200 reported 0x0100 = 256.
    hdr = struct.pack(">HHHHHH", 0, 0x03, 0, 100, 127, 3) + b"\x00" * 16
    entries = struct.pack(">HB", 200, 1) * 8        # 3-byte: sampleID u16, SSNr u8
    objs = [
        _object(38, 200, "S", _sample_body(rootkey=60, rate=48000)),
        _object(37, 200, "K03", hdr + entries),
    ]
    p = tmp_path / "m03.krz"
    p.write_bytes(_bank(objs, pcm=b"\x00\x00" * 100))
    _, chunks, _ = walk_file(str(p))
    keymap = next(c for c in chunks if c["id"] == "Keymap")
    refs = next(f for f in keymap["fields"] if f["name"] == "sample_refs")
    assert refs["value"] == "200"                   # id at offset 0, not 256
    method = next(f for f in keymap["fields"] if f["name"] == "method")
    assert "sampleID|subSample" in method["note"]


def test_info_routes_structural_format_to_walker(tmp_path):
    """`acidcat info` (and bare-path dispatch) must give a walker-backed summary
    for a structural format like KRZ, not mis-parse it as a headerless WAV."""
    from acidcat.commands import info
    p = tmp_path / "bank.krz"
    p.write_bytes(_bank([_object(38, 200, "S", _sample_body())]))
    assert info._detect_format(str(p)) == "walker"

    class _A:
        target = str(p)
        quiet = True
        verbose = False
    rec = info._info_walker(str(p), _A())
    assert rec["Format"].startswith("Kurzweil")
    assert "Kurzweil bank" in rec["Summary"]
    assert "inspect" in rec["Inspect"]
    assert "Chunks" not in rec                    # not the WAV mis-parse output


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


# Opt-in via the environment, like ACIDCAT_ABLETON_CORPUS. It was one
# developer's absolute home directory, which put that account name in the sdist
# and therefore on PyPI in every release since 0.55.0. A path that only exists
# on one machine is configuration, not source.
_CORPUS = os.environ.get("ACIDCAT_KRZ_CORPUS", "")


@pytest.mark.skipif(not (_CORPUS and os.path.isdir(_CORPUS)),
                    reason="set ACIDCAT_KRZ_CORPUS to a dir of real .krz banks")
def test_corpus_never_raises():
    files = glob.glob(_CORPUS + "/**/*.krz", recursive=True) \
        + glob.glob(_CORPUS + "/**/*.KRZ", recursive=True)
    for f in files:
        walk_file(f)                               # must not raise on any specimen


def test_pcm_region_owns_exactly_its_bytes(tmp_path):
    """The PCM region is raw, headerless bytes after the object walk. Without a
    declared payload_base it inherited geometry's RIFF default of offset + 8
    and claimed eight bytes past the end of the file. It did so on 39 of 40
    real Sweetwater banks, silently, because PCM is the last chunk and nothing
    sat after it to collide with."""
    import os
    from acidcat.core.infra import geometry
    pcm = bytes([0, 1]) * 100
    p = tmp_path / "bank.krz"
    p.write_bytes(_bank([], pcm=pcm))
    _label, chunks, _w = walk_file(str(p))
    geometry.normalize(chunks, os.path.getsize(p))
    region = next(c for c in chunks if c["id"] == "PCM")
    base, n = geometry.payload_of(region)
    assert base == region["offset"], "PCM has no header to skip"
    assert base + n == os.path.getsize(p), (base, n, os.path.getsize(p))


def test_sample_refs_are_a_list_in_the_document(tmp_path):
    """Review V6: `1,234` in the display is two samples; the Document's
    value is the list, not the int 1234."""
    import acidcat
    hdr = struct.pack(">HHHHHH", 0, 0x03, 0, 100, 127, 3) + b"\x00" * 16
    entries = struct.pack(">HB", 1, 1) * 4 + struct.pack(">HB", 234, 1) * 4
    p = tmp_path / "refs.krz"
    p.write_bytes(_bank([_object(37, 200, "K", hdr + entries)]))
    doc = acidcat.open(p, forensics=False)
    refs = [f for n in doc.walk() for f in n.fields if f.key == "sample_refs"]
    assert refs and refs[0].value == [1, 234]
    assert refs[0].display == "1,234"
