"""Tests for `inspect --anomalies` (core.anomalies)."""
import io
import struct
import zipfile

from acidcat.core import anomalies
from acidcat.commands import inspect as I


def _chunk(cid, p):
    return cid + struct.pack("<I", len(p)) + p + (b"\x00" if len(p) % 2 else b"")


def _wav(*chunks):
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


_FMT = _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16))


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _scan(path):
    fmt, chunks, warns = I._walk_file(path, deep=False)
    return anomalies.scan(path, fmt, chunks, warns)


def test_clean_wav_has_no_anomalies(tmp_path):
    path = _write(tmp_path, "clean.wav", _wav(_FMT, _chunk(b"data", b"\x00" * 32)))
    findings = _scan(path)
    assert not any(f["rule"] in ("polyglot", "trailing_data") for f in findings)


def test_wav_zip_polyglot_flagged(tmp_path):
    wav = _wav(_FMT, _chunk(b"data", b"\x00" * 32))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("hidden.txt", b"payload")
    path = _write(tmp_path, "poly.wav", wav + buf.getvalue())
    findings = _scan(path)
    rules = {f["rule"] for f in findings}
    assert "polyglot" in rules and "trailing_data" in rules
    assert any(f["severity"] == "alert" and "ZIP" in f["message"] for f in findings)


def test_trailing_junk_flagged_without_polyglot(tmp_path):
    wav = _wav(_FMT, _chunk(b"data", b"\x00" * 32))
    path = _write(tmp_path, "trail.wav", wav + b"just some trailing text bytes")
    findings = _scan(path)
    rules = {f["rule"] for f in findings}
    assert "trailing_data" in rules and "polyglot" not in rules
