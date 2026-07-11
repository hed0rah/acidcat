"""The `acidcat audit` command: composes structure + forensics + provenance."""
import json
import struct
from types import SimpleNamespace

from acidcat.commands import audit


def _args(inp, as_json=False):
    return SimpleNamespace(input=inp, json=as_json)


def _wav(payload=b"\x00" * 64, software=None):
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    data = b"data" + struct.pack("<I", len(payload)) + payload
    body = b"WAVE" + fmt + data
    if software is not None:
        info = b"ISFT" + struct.pack("<I", len(software) + 1) + software + b"\x00"
        lst = b"LIST" + struct.pack("<I", 4 + len(info)) + b"INFO" + info
        body += lst
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_audit_reports_repairable_structure(tmp_path, capsys):
    good = _wav(b"\x11" * 100)
    broken = bytearray(good)
    struct.pack_into("<I", broken, 4, 3)
    p = tmp_path / "bad.wav"
    p.write_bytes(bytes(broken))
    rc = audit.run(_args(str(p)))
    assert rc == 0
    out = capsys.readouterr().out
    assert "STRUCTURE" in out and "repairable" in out
    assert "VERDICT" in out and "structural fix" in out


def test_audit_clean_file(tmp_path, capsys):
    p = tmp_path / "ok.wav"
    p.write_bytes(_wav())
    audit.run(_args(str(p)))
    out = capsys.readouterr().out
    assert "consistent" in out


def test_audit_surfaces_provenance(tmp_path, capsys):
    p = tmp_path / "prov.wav"
    p.write_bytes(_wav(software=b"Adobe Audition"))
    audit.run(_args(str(p)))
    out = capsys.readouterr().out
    assert "PROVENANCE" in out and "Adobe Audition" in out


def test_audit_json(tmp_path, capsys):
    good = _wav(b"\x22" * 40)
    broken = bytearray(good)
    struct.pack_into("<I", broken, 4, 9)
    p = tmp_path / "j.wav"
    p.write_bytes(bytes(broken))
    audit.run(_args(str(p), as_json=True))
    doc = json.loads(capsys.readouterr().out)
    assert doc["format"] and doc["structure"]
    assert doc["structure"][0]["kind"] == "size"
    assert doc["structure"][0]["repairable"] is True


def test_audit_hidden_section_and_carve_hint(tmp_path, capsys):
    # a WAV with an appended blob past the container -> HIDDEN region + carve hint
    wav = _wav(b"\x33" * 200)
    p = tmp_path / "poly.wav"
    p.write_bytes(wav + b"APPENDED-SECRET-PAYLOAD" * 4)
    audit.run(_args(str(p)))
    out = capsys.readouterr().out
    assert "HIDDEN" in out and "past the" in out
    assert "carve" in out and "--trailing" in out
    assert "hidden region" in out.lower()      # verdict mentions it


def test_audit_clean_file_no_hidden(tmp_path, capsys):
    p = tmp_path / "clean.wav"
    p.write_bytes(_wav())
    audit.run(_args(str(p)))
    out = capsys.readouterr().out
    assert "no concealed or appended data" in out
