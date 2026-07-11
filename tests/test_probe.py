"""The `acidcat probe` byte-dissection primitives and format-aware addressing."""
import struct
from types import SimpleNamespace

from acidcat.core import probe as pr
from acidcat.commands import probe as cmd


def _wav(tmp_path, rate=44100):
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 2, rate, rate * 4, 4, 16)
    data = b"data" + struct.pack("<I", 8) + b"\x01\x02\x03\x04\x05\x06\x07\x08"
    body = b"WAVE" + fmt + data
    p = tmp_path / "t.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p)


# ── primitives ─────────────────────────────────────────────────────

def test_parse_int_hex_dec_neg():
    assert pr.parse_int("0x2c") == 44
    assert pr.parse_int("44") == 44
    assert pr.parse_int("-5") == -5


def test_read_typed_le_be():
    data = struct.pack("<I", 44100) + struct.pack(">I", 48000)
    assert pr.read_typed(data, 0, "u32", 1, "little") == [44100]
    assert pr.read_typed(data, 4, "u32", 1, "big") == [48000]
    assert pr.read_typed(bytes(range(8)), 0, "u16", 4, "little")[0] == 0x0100


def test_read_u24():
    data = (1234).to_bytes(3, "little") + (5678).to_bytes(3, "little")
    assert pr.read_typed(data, 0, "u24", 2, "little") == [1234, 5678]


def test_scan_value_both_orders():
    data = b"\x00\x00" + struct.pack("<I", 44100) + struct.pack(">I", 44100)
    hits = pr.scan_value(data, 44100, "u32")
    assert (2, "le") in hits and (6, "be") in hits


def test_find_bytes_and_strings():
    data = b"junk\x00hello world\x00\xffdata"
    assert pr.find_bytes(data, b"data") == [len(data) - 4]
    strs = dict((t, o) for o, t in pr.strings(data, 4))
    assert "hello world" in strs


def test_diff_ranges():
    a = b"abcdefgh"
    b = b"abXdefYh"
    ranges, la, lb = pr.diff(a, b)
    assert ranges == [(2, 3), (6, 7)] and la == lb == 8


# ── format-aware addressing ────────────────────────────────────────

def test_resolve_raw_offset(tmp_path):
    off, ln, note = pr.resolve(_wav(tmp_path), "0x2c")
    assert off == 44 and note == "offset"


def test_resolve_chunk_and_field(tmp_path):
    p = _wav(tmp_path, rate=48000)
    off, ln, note = pr.resolve(p, "data")
    assert note == "chunk data" and ln == 8
    foff, flen, fnote = pr.resolve(p, "fmt.sample_rate")
    # reading that field back gives the rate
    data = open(p, "rb").read()
    assert pr.read_typed(data, foff, "u32", 1, "little") == [48000]
    assert fnote == "fmt.sample_rate"


# ── command ────────────────────────────────────────────────────────

def _args(file, verb, **kw):
    base = dict(file=file, verb=verb, type="u32", count=1, be=False, le=False,
                min=4, length=256)
    base.update(kw)
    return SimpleNamespace(**base)


def test_cmd_read_field_by_name(tmp_path, capsys):
    p = _wav(tmp_path, rate=48000)
    rc = cmd.run(_args(p, "read", at="fmt.sample_rate", type="u32"))
    out = capsys.readouterr().out
    assert rc == 0 and "48000" in out and "fmt.sample_rate" in out


def test_cmd_scan_finds_rate(tmp_path, capsys):
    p = _wav(tmp_path, rate=44100)
    rc = cmd.run(_args(p, "scan", value="44100", type="u32"))
    out = capsys.readouterr().out
    assert rc == 0 and "hit(s)" in out and "(le)" in out


def test_cmd_diff(tmp_path, capsys):
    a = _wav(tmp_path, rate=44100)
    b = tmp_path / "b.wav"
    d = bytearray(open(a, "rb").read())
    d[0x18] ^= 0xFF                          # flip a byte in the fmt chunk
    b.write_bytes(bytes(d))
    rc = cmd.run(_args(a, "diff", other=str(b)))
    out = capsys.readouterr().out
    assert rc == 0 and "changed range" in out
