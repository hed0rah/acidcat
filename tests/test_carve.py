"""Tests for `acidcat carve` -- structural byte-range extraction."""
import struct

import pytest

from acidcat.commands import carve


class _Args:
    def __init__(self, **kw):
        d = {"target": None, "offset": None, "length": None, "end": None,
             "trailing": False, "chunk": None, "raw": False, "output": None,
             "quiet": True, "at": None, "type": None, "count": 1,
             "endian": "be", "struct": None, "field": None, "format": None}
        d.update(kw)
        for k, v in d.items():
            setattr(self, k, v)


def _wav(*chunks):
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _chunk(cid, payload):
    raw = cid + struct.pack("<I", len(payload)) + payload
    return raw + (b"\x00" if len(payload) % 2 else b"")


_FMT = _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, 8000, 8000, 1, 8))
_DATA = _chunk(b"data", bytes(range(16)))


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_carve_explicit_offset_length(tmp_path):
    p = _write(tmp_path, "f.wav", _wav(_FMT, _DATA))
    out = str(tmp_path / "o.bin")
    rc = carve.run(_Args(target=p, offset="0x0", length="4", output=out))
    assert rc == 0
    assert open(out, "rb").read() == b"RIFF"


def test_carve_offset_end(tmp_path):
    p = _write(tmp_path, "f.wav", _wav(_FMT, _DATA))
    out = str(tmp_path / "o.bin")
    rc = carve.run(_Args(target=p, offset="0", end="4", output=out))
    assert rc == 0 and open(out, "rb").read() == b"RIFF"


def test_carve_trailing_blob(tmp_path):
    # the forensics use case: pull the appended blob a polyglot check flags
    appended = b"PK\x03\x04HIDDEN-PAYLOAD"
    p = _write(tmp_path, "poly.wav", _wav(_FMT, _DATA) + appended)
    out = str(tmp_path / "trail.bin")
    rc = carve.run(_Args(target=p, trailing=True, output=out))
    assert rc == 0
    assert open(out, "rb").read() == appended       # exactly the appended bytes


def test_carve_chunk_payload(tmp_path):
    p = _write(tmp_path, "f.wav", _wav(_FMT, _DATA))
    out = str(tmp_path / "audio.raw")
    rc = carve.run(_Args(target=p, chunk="data", output=out))
    assert rc == 0
    assert open(out, "rb").read() == bytes(range(16))   # payload only


def test_carve_chunk_raw_includes_header(tmp_path):
    p = _write(tmp_path, "f.wav", _wav(_FMT, _DATA))
    out = str(tmp_path / "c.bin")
    rc = carve.run(_Args(target=p, chunk="data", raw=True, output=out))
    assert rc == 0
    blob = open(out, "rb").read()
    assert blob[:4] == b"data" and blob[8:] == bytes(range(16))


def test_carve_chunk_short_name_padded(tmp_path):
    # "fmt" -> "fmt " (RIFF ids are 4 bytes)
    p = _write(tmp_path, "f.wav", _wav(_FMT, _DATA))
    out = str(tmp_path / "fmt.bin")
    rc = carve.run(_Args(target=p, chunk="fmt", output=out))
    assert rc == 0 and len(open(out, "rb").read()) == 16


def test_carve_requires_exactly_one_target(tmp_path):
    p = _write(tmp_path, "f.wav", _wav(_FMT, _DATA))
    assert carve.run(_Args(target=p, trailing=True, offset="0")) == 2   # two
    assert carve.run(_Args(target=p)) == 2                              # none


def test_carve_missing_chunk_errors(tmp_path):
    p = _write(tmp_path, "f.wav", _wav(_FMT, _DATA))
    assert carve.run(_Args(target=p, chunk="XXXX", output=str(tmp_path / "x"))) == 2


def test_carve_range_past_eof_clamped(tmp_path):
    p = _write(tmp_path, "f.wav", _wav(_FMT, _DATA))
    size = len(_wav(_FMT, _DATA))
    out = str(tmp_path / "o.bin")
    rc = carve.run(_Args(target=p, offset=str(size - 4), length="999", output=out))
    assert rc == 0 and len(open(out, "rb").read()) == 4      # clamped to EOF


def test_carve_no_trailing_data(tmp_path):
    # a clean WAV with nothing appended has no trailing region
    p = _write(tmp_path, "clean.wav", _wav(_FMT, _DATA))
    assert carve.run(_Args(target=p, trailing=True, output=str(tmp_path / "x"))) == 2


def test_carve_missing_file():
    assert carve.run(_Args(target="/nonexistent/nope.wav", offset="0")) == 1
