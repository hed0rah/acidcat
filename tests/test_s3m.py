"""ScreamTracker 3 (.s3m) walker: header, order, the instrument/pattern
parapointer tables (byte = value << 4), and per-sample memseg xref to the PCM.

The fixture is synthesized here from the documented layout -- no real module."""
import struct

from acidcat.core import tracker as tk
from acidcat.core.sniff import sniff
from acidcat.core.walk import tracker as wtk


def _make_s3m(ffi=2, eof_marker=True, ins_para=0x07):
    b = bytearray(0xD8)
    b[0:9] = b"test song"
    b[0x1C] = 0x1A if eof_marker else 0x00
    b[0x1D] = 16
    struct.pack_into("<H", b, 0x20, 2)          # ordnum
    struct.pack_into("<H", b, 0x22, 1)          # insnum
    struct.pack_into("<H", b, 0x24, 1)          # patnum
    struct.pack_into("<H", b, 0x26, 0x10)       # flags: vol0_optimizations
    struct.pack_into("<H", b, 0x28, 0x1320)     # cwt: high nibble 1 = ST3
    struct.pack_into("<H", b, 0x2A, ffi)
    b[0x2C:0x30] = b"SCRM"
    b[0x30], b[0x31], b[0x32] = 64, 6, 125      # gvol, speed, tempo
    b[0x33] = 0x80 | 48                          # master vol, stereo bit set
    for i in range(0x40, 0x60):
        b[i] = 255
    b[0x40], b[0x41] = 0, 8                       # channels L1, R1 active
    b[0x60], b[0x61] = 0, 255                     # order: pattern 0, end marker
    struct.pack_into("<H", b, 0x62, ins_para)    # ins parapointer -> 0x70
    struct.pack_into("<H", b, 0x64, 0x0C)        # pat parapointer -> 0xC0
    h = 0x70                                      # instrument header
    b[h] = 1                                      # type: PCM
    b[h + 1:h + 11] = b"SAMPLE.WAV"
    struct.pack_into("<H", b, h + 0x0E, 0x0D)    # memseg lo -> PCM @ 0xD0
    struct.pack_into("<I", b, h + 0x10, 8)       # length (points)
    b[h + 0x1C] = 64                             # volume
    struct.pack_into("<I", b, h + 0x20, 8363)    # c2spd
    b[h + 0x30:h + 0x3B] = b"test sample"
    b[h + 0x4C:h + 0x50] = b"SCRS"
    struct.pack_into("<H", b, 0xC0, 2)           # pattern packed length
    b[0xD0:0xD8] = bytes(range(8))               # PCM
    return bytes(b)


def _write(tmp_path, data, name="song.s3m"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_s3m_sniffs_by_content(tmp_path):
    # sniff is pure content (SCRM at 0x2C); a .mod extension would not fool it
    assert sniff(_write(tmp_path, _make_s3m(), name="x.mod")) == "s3m"


def test_s3m_is_s3m_gate():
    d = _make_s3m()
    assert tk.is_s3m(d)
    assert not tk.is_s3m(d[:40])                  # needs 48 bytes
    assert not tk.is_s3m(b"\x00" * 48)            # no SCRM


def test_s3m_header_and_parapointers(tmp_path):
    chunks, warns = wtk.inspect_s3m(_write(tmp_path, _make_s3m()))
    assert warns == []
    head = chunks[0]
    assert head["id"] == "S3M" and "ScreamTracker 3" in head["summary"]
    f = {x["name"]: x for x in head["fields"]}
    assert f["created_with"]["note"] == "ScreamTracker 3"
    assert f["sample_format"]["note"] == "unsigned PCM"
    assert f["master_volume"]["note"] == "stereo"
    assert f["channel_map"]["note"] == "2 active"
    # parapointers resolve via << 4 to the instrument header and pattern
    ins = next(c for c in chunks if c["id"] == "ins_parapointers")
    assert ins["fields"][0]["xref"] == 0x70
    pat = next(c for c in chunks if c["id"] == "pat_parapointers")
    assert pat["fields"][0]["xref"] == 0xC0


def test_s3m_sample_chunk_memseg_xref(tmp_path):
    chunks, _ = wtk.inspect_s3m(_write(tmp_path, _make_s3m()))
    smp = next(c for c in chunks if c["id"] == "smp[1]")
    assert smp["offset"] == 0x70 and smp["size"] == 0x50
    memseg = next(x for x in smp["fields"] if x["name"] == "memseg")
    assert memseg["xref"] == 0xD0                 # 0x0D << 4
    assert "unsigned PCM" in smp["summary"]


def test_s3m_signed_format_noted(tmp_path):
    chunks, _ = wtk.inspect_s3m(_write(tmp_path, _make_s3m(ffi=1)))
    smp = next(c for c in chunks if c["id"] == "smp[1]")
    assert "signed PCM" in smp["summary"]


def test_s3m_missing_eof_marker_warns(tmp_path):
    _, warns = wtk.inspect_s3m(_write(tmp_path, _make_s3m(eof_marker=False)))
    assert any("0x1A DOS-EOF" in w for w in warns)


def test_s3m_parapointer_past_eof_flagged(tmp_path):
    # an instrument parapointer past EOF: invalid header, warning, no crash
    _, warns = wtk.inspect_s3m(_write(tmp_path, _make_s3m(ins_para=0xFFF)))
    assert any("SCRS/SCRI" in w for w in warns)
