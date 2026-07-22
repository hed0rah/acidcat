"""Tests for the `acidcat recover` command (commands/recover.py)."""

import io
import json
import math
import os
import struct
import types
import wave

from acidcat.commands import recover as cmd


def _wav(n=6000, rate=11025, period=40, amp=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(amp*math.sin(2*math.pi*i/period)))
                               for i in range(n)))
    return buf.getvalue()


def _tone_u8(n, period=40, amp=60):
    return bytes(int(amp*math.sin(2*math.pi*i/period)) & 0xFF for i in range(n))


def _args(**kw):
    ns = types.SimpleNamespace(input=None, mode="normal", json=False,
                               extract=None, quiet=True)
    ns.__dict__.update(kw)
    return ns


def _img(tmp_path, blob, name="d.img"):
    p = tmp_path / name
    p.write_bytes(blob)
    return str(p)


def test_recover_table(tmp_path, capsys):
    p = _img(tmp_path, bytes(1024) + _wav() + bytes(1024))
    rc = cmd.run(_args(input=p))
    out = capsys.readouterr().out
    assert rc == 0
    assert "container" in out and "wav" in out


def test_recover_json(tmp_path, capsys):
    p = _img(tmp_path, bytes(1024) + _wav() + bytes(1024))
    rc = cmd.run(_args(input=p, json=True))
    recs = json.loads(capsys.readouterr().out)
    assert rc == 0 and recs
    assert recs[0]["kind"] == "container" and recs[0]["format"] == "wav"
    assert recs[0]["offset"] == 1024


def test_recover_extract_writes_carveable_files(tmp_path):
    p = _img(tmp_path, bytes(1024) + _wav() + bytes(1024))
    outdir = tmp_path / "out"
    cmd.run(_args(input=p, extract=str(outdir)))
    files = sorted(os.listdir(outdir))
    assert files and files[0].endswith(".wav")
    # the extracted bytes are a valid, walkable RIFF/WAVE
    with wave.open(str(outdir / files[0]), "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2


def test_recover_stdin(tmp_path, capsys, monkeypatch):
    img = bytes(1024) + _wav() + bytes(1024)
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(buffer=io.BytesIO(img)))
    rc = cmd.run(_args(input="-", json=True))
    recs = json.loads(capsys.readouterr().out)
    assert rc == 0 and recs[0]["format"] == "wav"


def test_recover_mode_strict_drops_headerless(tmp_path, capsys):
    # a bare tone, no container -> strict recovers nothing
    p = _img(tmp_path, bytes(2048) + _tone_u8(6000) + bytes(2048))
    cmd.run(_args(input=p, mode="strict"))
    assert "(no audio recovered)" in capsys.readouterr().out


def test_recover_mode_aggressive_keeps_headerless(tmp_path, capsys):
    p = _img(tmp_path, bytes(2048) + _tone_u8(6000) + bytes(2048))
    cmd.run(_args(input=p, mode="aggressive", json=True))
    recs = json.loads(capsys.readouterr().out)
    assert any(r["kind"] == "blob" for r in recs)


def test_recover_empty_input(tmp_path, capsys):
    p = _img(tmp_path, b"")
    rc = cmd.run(_args(input=p))
    assert rc == 1
    assert "no input" in capsys.readouterr().err
