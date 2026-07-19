"""Tests for the corpus census: RIFF walk correctness (incl. the bext-version
offset and the printable-FOURCC filter), the traversal, and thread determinism.
"""

import struct

from acidcat.core import census


def _chunk(cid, payload):
    return cid + struct.pack("<I", len(payload)) + payload + (
        b"\x00" if len(payload) & 1 else b"")


def _wav(chunks, form=b"WAVE", magic=b"RIFF"):
    body = form + b"".join(chunks)
    return magic + struct.pack("<I", len(body)) + body


_FMT = _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16))
_DATA = _chunk(b"data", b"\x00\x00" * 8)


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# ---- unit: helpers ---------------------------------------------------------

def test_safe_fourcc_printable_passthrough():
    assert census._safe_fourcc(b"fmt ") == "fmt "
    assert census._safe_fourcc(b"data") == "data"


def test_safe_fourcc_garbage_becomes_hex():
    out = census._safe_fourcc(b"\x00\x01\xff\x7f")
    assert out == "hex:0001ff7f"
    assert all(ord(c) >= 32 for c in out)          # never a control byte


# ---- unit: RIFF walk -------------------------------------------------------

def test_basic_riff_histogram(tmp_path):
    p = _write(tmp_path, "a.wav", _wav([_FMT, _DATA]))
    cx = census.Census()
    cx.census_file(p)
    assert cx.riff_files == 1
    assert cx.by_container == {"RIFF": 1}
    assert cx.chunk_counts["fmt "] == 1
    assert cx.chunk_counts["data"] == 1
    assert cx.fmt_tags == {1: 1}                    # PCM


def test_bext_version_offset_346(tmp_path):
    # BWF version is a u16 at payload offset 346, not 602 (the historical bug).
    payload = b"\x00" * 346 + struct.pack("<H", 2) + b"\x00" * 64
    wav = _wav([_FMT, _chunk(b"bext", payload), _DATA])
    p = _write(tmp_path, "b.wav", wav)
    cx = census.Census()
    cx.census_file(p)
    assert cx.bext_versions == {2: 1}              # read version 2, not garbage


def test_garbage_fourcc_does_not_break(tmp_path):
    # a chunk id with control bytes must group as a hex token, never crash or
    # inject control chars (which would break a strict JSON reader downstream).
    wav = _wav([_FMT, _chunk(b"\x00\x01\x02\x03", b"xx"), _DATA])
    p = _write(tmp_path, "g.wav", wav)
    cx = census.Census()
    cx.census_file(p)
    assert "hex:00010203" in cx.chunk_counts
    assert all(ord(c) >= 32 for k in cx.chunk_counts for c in k)


def test_rifx_big_endian(tmp_path):
    # RIFX: same layout, big-endian sizes; the walk must read sizes the other way.
    body = b"WAVE" + b"fmt " + struct.pack(">I", 16) + \
        struct.pack(">HHIIHH", 1, 2, 44100, 176400, 4, 16)
    data = b"RIFX" + struct.pack(">I", len(body)) + body
    p = _write(tmp_path, "x.wav", data)
    cx = census.Census()
    cx.census_file(p)
    assert cx.by_container == {"RIFX": 1}
    assert "rifx_big_endian" in cx.flags
    assert cx.chunk_counts.get("fmt ") == 1


def test_rf64_ds64_resolves_sentinel_data_size(tmp_path):
    # RF64: the data chunk's size field is the 0xFFFFFFFF sentinel; its real size
    # lives in the ds64 chunk. The walk must use it to step over data and reach a
    # trailing chunk (else it stops at data and misses everything after).
    data_payload = b"\x00\x00\x00\x00" * 4                    # 16 real bytes
    ds64 = struct.pack("<QQQI", 0, len(data_payload), 4, 0)   # riff/data/samples/tbl
    body = (b"WAVE"
            + b"ds64" + struct.pack("<I", len(ds64)) + ds64
            + _FMT
            + b"data" + struct.pack("<I", 0xFFFFFFFF) + data_payload
            + _chunk(b"id3 ", b"tag"))                        # trailing, past data
    rf64 = b"RF64" + struct.pack("<I", 0xFFFFFFFF) + body
    p = _write(tmp_path, "big.wav", rf64)
    cx = census.Census()
    cx.census_file(p)
    assert cx.by_container == {"RF64": 1}
    assert "ds64" in cx.chunk_counts
    assert "id3 " in cx.chunk_counts                          # reached past the data chunk
    assert "id3_in_wav" in cx.flags


def test_non_riff_file_skipped(tmp_path):
    p = _write(tmp_path, "not.wav", b"ID3\x03\x00" + b"\x00" * 100)
    cx = census.Census()
    cx.census_file(p)
    assert cx.files == 1
    assert cx.riff_files == 0                       # opened, but not RIFF-family


def test_short_file_never_raises(tmp_path):
    p = _write(tmp_path, "s.wav", b"RI")
    cx = census.Census()
    cx.census_file(p)                              # must not raise
    assert cx.riff_files == 0


def test_id3_and_cset_flags(tmp_path):
    wav = _wav([_FMT, _chunk(b"id3 ", b"junk"), _chunk(b"CSET", b"\x00" * 8), _DATA])
    p = _write(tmp_path, "f.wav", wav)
    cx = census.Census()
    cx.census_file(p)
    assert "id3_in_wav" in cx.flags
    assert "cset" in cx.flags


# ---- traversal + orchestration --------------------------------------------

def test_walk_tree_extension_filter(tmp_path):
    _write(tmp_path, "keep1.wav", _wav([_FMT, _DATA]))
    _write(tmp_path, "keep2.WAV", _wav([_FMT, _DATA]))    # case-insensitive
    _write(tmp_path, "skip.txt", b"nope")
    sub = tmp_path / "nested"
    sub.mkdir()
    _write(sub, "keep3.wav", _wav([_FMT, _DATA]))
    found = sorted(p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                   for p in census.walk_tree([str(tmp_path)], census.ScanOptions()))
    assert found == ["keep1.wav", "keep2.WAV", "keep3.wav"]


def test_run_census_thread_determinism(tmp_path):
    for i in range(20):
        _write(tmp_path, f"f{i}.wav", _wav([_FMT, _DATA]))
    _write(tmp_path, "note.txt", b"ignored")
    r1 = census.run_census([str(tmp_path)], jobs=1).result()
    r4 = census.run_census([str(tmp_path)], jobs=4).result()
    assert r1["riff_family_files"] == 20
    assert r1["chunk_histogram"] == r4["chunk_histogram"]
    assert r1["containers"] == r4["containers"]
    assert r1["errors"] == 0 == r4["errors"]


def test_merge_combines_accumulators(tmp_path):
    a = census.Census()
    a.census_file(_write(tmp_path, "a.wav", _wav([_FMT, _DATA])))
    b = census.Census()
    b.census_file(_write(tmp_path, "b.wav", _wav([_FMT, _DATA])))
    a.merge(b)
    assert a.riff_files == 2
    assert a.chunk_counts["fmt "] == 2
