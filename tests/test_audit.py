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
    assert rc == 1                    # it has structural findings to report
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
    (doc,) = json.loads(capsys.readouterr().out)     # rows, one per file
    assert doc["format"] == "wav" and doc["label"] and doc["structure"]
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


def test_audit_deep_walks_mp3_only(tmp_path, monkeypatch):
    # audit should request a deep frame walk for MP3 (to surface the Xing-vs-actual
    # frame-count truncation tell) but not for other formats
    import acidcat.commands.audit as A
    seen = {}

    def fake_walk(path, deep=False):
        seen["deep"] = deep
        return "MP3/MPEG audio", [], []

    monkeypatch.setattr(A, "walk_file", fake_walk)
    monkeypatch.setattr(A.anomalies, "scan", lambda *a, **k: [])
    monkeypatch.setattr(A.provenance, "identify", lambda *a, **k: [])
    monkeypatch.setattr(A.integrity, "analyze", lambda *a, **k: [])

    mp3 = tmp_path / "x.mp3"
    mp3.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64)  # sniffs as mp3
    A._gather(str(mp3))
    assert seen["deep"] is True

    wav = tmp_path / "x.wav"
    wav.write_bytes(_wav())
    A._gather(str(wav))
    assert seen["deep"] is False


def test_audit_survives_a_data_chunk_that_overruns_the_file(tmp_path, capsys):
    """A declared size larger than the file is the commonest damage acidcat
    describes, and it made `audit` traceback out with numpy installed.

    `_effective_bits` built its spans from the DECLARED data size and never
    clamped them to the buffer, so np.frombuffer raised ValueError. Nothing
    caught it: zero stdout, a raw traceback, exit 1. The pure-Python fallback
    hid it locally, and CI has numpy but no truncated specimen -- every one
    lives in the gitignored corpus -- so nobody saw it. Hence a file built
    here rather than a fixture path.
    """
    good = _wav(b"\x11\x22" * 400)
    broken = bytearray(good)
    # data says 176,400 bytes; far more than remain
    struct.pack_into("<I", broken, len(good) - 800 - 4, 176400)
    p = tmp_path / "overrun.wav"
    p.write_bytes(bytes(broken))

    rc = audit.run(_args(str(p)))                 # must not raise

    out = capsys.readouterr().out
    assert out.strip(), "audit produced no report at all"
    assert "VERDICT" in out
    assert rc in (0, 1)


def test_a_rom_is_named_and_pointed_at_extract(tmp_path, capsys):
    """A SNES ROM sniffs as one and `extract` recovers its samples, yet audit
    printed it as [unknown] and suggested `locate`. It names what it recognised
    and the verb that gets at the audio; exit 2 still says nothing was checked."""
    rom = bytearray(0x8000)
    rom[0x7FC0 + 0x15] = 0x20                              # LoROM map mode
    rom[0x7FC0 + 0x1C:0x7FC0 + 0x20] = bytes((0x34, 0x12, 0xCB, 0xED))  # complement, checksum
    p = tmp_path / "cart.sfc"
    p.write_bytes(bytes(rom))
    rc = audit.run(SimpleNamespace(input=str(p), signal=False, output_format="table",
                                   json=False))
    out = capsys.readouterr().out
    assert "[SNES ROM]" in out and "acidcat extract" in out and "[unknown]" not in out
    assert rc == 2
