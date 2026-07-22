"""Tests for the `acidcat extract` command."""

import io
import json
import os
import struct
import types
import wave

from acidcat.commands import extract


def _make_mod(pcm_bytes=20):
    title = b"TEST".ljust(20, b"\x00")
    hdrs = []
    for i in range(31):
        name = b"kick".ljust(22, b"\x00") if i == 0 else b"\x00" * 22
        words = (pcm_bytes // 2) if i == 0 else 0
        hdrs.append(name + struct.pack(">H", words) + bytes([0, 64]) + struct.pack(">HH", 0, 1))
    order = bytes([0]) + b"\x00" * 127
    body = title + b"".join(hdrs) + bytes([1, 127]) + order + b"M.K."
    body += b"\x00" * (64 * 4 * 4)
    body += bytes(range(pcm_bytes))
    return body


def _args(**kw):
    ns = types.SimpleNamespace(input=None, output=None, json=False, quiet=True)
    ns.__dict__.update(kw)
    return ns


def _mod(tmp_path):
    p = tmp_path / "k.mod"
    p.write_bytes(_make_mod())
    return str(p)


def test_extract_writes_wavs(tmp_path):
    p = _mod(tmp_path)
    outdir = tmp_path / "out"
    rc = extract.run(_args(input=p, output=str(outdir)))
    assert rc == 0
    wavs = [f for f in os.listdir(outdir) if f.endswith(".wav")]
    assert len(wavs) == 1
    with wave.open(str(outdir / wavs[0]), "rb") as w:
        assert w.getnframes() == 20


def test_extract_json_manifest(tmp_path, capsys):
    p = _mod(tmp_path)
    rc = extract.run(_args(input=p, json=True))
    d = json.loads(capsys.readouterr().out)
    assert rc == 0 and len(d["samples"]) == 1
    assert d["samples"][0]["name"] == "kick"


def test_extract_default_outdir(tmp_path):
    p = _mod(tmp_path)
    assert extract.run(_args(input=p)) == 0
    assert os.path.isdir(os.path.splitext(p)[0] + "_samples")


def test_extract_unsupported(tmp_path, capsys):
    p = tmp_path / "x.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", 4) + b"WAVE")
    rc = extract.run(_args(input=str(p)))
    assert rc == 1
    assert "no sample extractor" in capsys.readouterr().err


def test_extract_missing_file(capsys):
    rc = extract.run(_args(input="/nope/x.mod"))
    assert rc == 1
    assert "No such file" in capsys.readouterr().err


def test_extract_stdin(tmp_path, capsys, monkeypatch):
    blob = _make_mod()
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(
        buffer=io.BytesIO(blob), isatty=lambda: False))
    rc = extract.run(_args(input="-", json=True))
    d = json.loads(capsys.readouterr().out)
    assert rc == 0 and d["samples"][0]["name"] == "kick"
