"""Tests for the `acidcat locate` command (commands/locate.py)."""

import io
import json
import math
import struct
import types
import wave

from acidcat.commands import locate as cmd


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
    ns = types.SimpleNamespace(input=None, mode="normal", analyze=False,
                               format="table", quiet=True)
    ns.__dict__.update(kw)
    return ns


def _img(tmp_path, blob, name="d.img"):
    p = tmp_path / name
    p.write_bytes(blob)
    return str(p)


def test_locate_table(tmp_path, capsys):
    p = _img(tmp_path, bytes(1024) + _wav() + bytes(1024))
    rc = cmd.run(_args(input=p))
    out = capsys.readouterr().out
    assert rc == 0
    assert "container" in out and "wav" in out


def test_locate_json(tmp_path, capsys):
    p = _img(tmp_path, bytes(1024) + _wav() + bytes(1024))
    rc = cmd.run(_args(input=p, format="json"))
    recs = json.loads(capsys.readouterr().out)
    assert rc == 0 and recs
    assert recs[0]["kind"] == "container" and recs[0]["format"] == "wav"
    assert recs[0]["offset"] == 1024


def test_locate_tsv(tmp_path, capsys):
    p = _img(tmp_path, bytes(1024) + _wav() + bytes(1024))
    cmd.run(_args(input=p, format="tsv"))
    out = capsys.readouterr().out.strip().splitlines()
    cols = out[0].split("\t")
    assert cols[0] == "0x00000400" and cols[2] == "container" and cols[3] == "wav"


def test_locate_no_extract_attribute():
    # locate never extracts -- the flag must not exist
    import inspect as _i
    src = _i.getsource(cmd.register)
    assert "--extract" not in src and "extract" not in src.replace("extractable", "")


def test_locate_analyze_adds_geometry(tmp_path, capsys):
    # a headerless 8-bit tone -> aggressive keeps it -> --analyze infers geometry
    p = _img(tmp_path, bytes(2048) + _tone_u8(6000) + bytes(2048))
    cmd.run(_args(input=p, mode="aggressive", analyze=True, format="json"))
    recs = json.loads(capsys.readouterr().out)
    blob = next(r for r in recs if r["kind"] == "blob")
    g = blob["geometry"]
    assert g["width"] in (8, 16) and g["channels"] in (1, 2)
    assert g["rate"] is None and g["rate_candidates"]


def test_locate_stdin(tmp_path, capsys, monkeypatch):
    img = bytes(1024) + _wav() + bytes(1024)
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(buffer=io.BytesIO(img)))
    rc = cmd.run(_args(input="-", format="json"))
    recs = json.loads(capsys.readouterr().out)
    assert rc == 0 and recs[0]["format"] == "wav"


def test_locate_mode_strict_drops_headerless(tmp_path, capsys):
    p = _img(tmp_path, bytes(2048) + _tone_u8(6000) + bytes(2048))
    cmd.run(_args(input=p, mode="strict"))
    assert "(no audio located)" in capsys.readouterr().out


def test_locate_mode_aggressive_keeps_headerless(tmp_path, capsys):
    p = _img(tmp_path, bytes(2048) + _tone_u8(6000) + bytes(2048))
    cmd.run(_args(input=p, mode="aggressive", format="json"))
    recs = json.loads(capsys.readouterr().out)
    assert any(r["kind"] == "blob" for r in recs)


def test_locate_empty_input(tmp_path, capsys):
    p = _img(tmp_path, b"")
    rc = cmd.run(_args(input=p))
    assert rc == 1
    assert "no input" in capsys.readouterr().err
