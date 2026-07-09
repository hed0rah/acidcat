"""tests for the `acidcat tui` inspector plumbing. The interactive UI itself is
not unit-tested here (it is exercised by headless render during development);
these cover the pieces that must stay correct: the command registers without
the textual extra present, and the byte-offset / hex helpers match inspect's
addressing so the hex pane highlights the right bytes."""
import argparse

import pytest


def test_tui_command_registers_without_textual():
    # register() and the CLI must import with no textual installed; the extra is
    # only touched inside run(). This just needs the module to import + register.
    from acidcat.commands import tui
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    tui.register(sub)
    args = parser.parse_args(["tui", "some/file.wav"])
    assert args.command == "tui" and args.file == "some/file.wav"
    # the file arg is optional (bare `acidcat tui` opens the browser)
    bare = parser.parse_args(["tui"])
    assert bare.command == "tui" and bare.file is None


def test_edit_profile_routing(tmp_path):
    pytest.importorskip("textual")
    from acidcat.tui_app import edit_profile

    def _profile(head, name="x", ext=".bin"):
        p = tmp_path / (name + ext)
        p.write_bytes(head + b"\0" * 32)
        prof = edit_profile(str(p))
        return prof[0] if prof else None

    assert _profile(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "WAV"
    assert _profile(b"FORM\x00\x00\x00\x00AIFFCOMM") == "AIFF"
    assert _profile(b"ID3\x04\x00\x00", ext=".mp3") == "tagged"
    assert _profile(b"fLaC\x00\x00\x00\x22") == "tagged"
    # Bitwig/NI editing is disabled -> no editor offered
    assert _profile(b"BtWg\x00\x00\x00\x00") is None
    # an unknown container has no editor
    assert _profile(b"\x00\x01\x02\x03\x04\x05\x06\x07") is None


def test_field_abs_addressing():
    pytest.importorskip("textual")
    from acidcat.tui_app import _field_abs
    # default base is chunk offset + 8 (RIFF/AIFF id+size header)
    chunk = {"offset": 0x30}
    assert _field_abs(chunk, {"off": 4, "len": 2}) == 0x30 + 8 + 4
    # explicit payload_base wins (FLAC blocks, absolute-offset formats)
    chunk2 = {"offset": 0x30, "payload_base": 0x100}
    assert _field_abs(chunk2, {"off": 4, "len": 2}) == 0x104
    # derived fields (no offset) carry no byte range
    assert _field_abs(chunk, {"off": None, "len": 0}) is None


def test_infer_enc_roundtrip_and_encode():
    pytest.importorskip("textual")
    from acidcat.tui_app import infer_enc, encode_value
    # sample_rate 44100 stored little-endian u32 -> infer <I, re-encode 69
    assert infer_enc(44100, b"\x44\xac\x00\x00") == "<I"
    assert encode_value("<I", "69") == b"\x45\x00\x00\x00"
    assert encode_value("<I", "0x45") == b"\x45\x00\x00\x00"   # hex literal ok
    # big-endian u16, and a float32, both recovered by round-trip
    assert infer_enc(2, b"\x00\x02") == ">H"
    import struct
    assert infer_enc(120.0, struct.pack("<f", 120.0)) == "<f"
    # non-numeric or non-round-tripping value -> None (caller falls back to hex)
    assert infer_enc("Am", b"\x41\x6d") is None
    assert infer_enc(True, b"\x01") is None


def test_hex_text_offsets_and_empty(tmp_path):
    pytest.importorskip("textual")
    from acidcat.tui_app import hex_text
    p = tmp_path / "b.bin"
    p.write_bytes(bytes(range(48)))            # 3 rows of 16
    t = hex_text(str(p), 0, 48, "#56e0f0").plain
    assert "00000000" in t and "00000010" in t and "00000020" in t
    # a node with no byte range renders a placeholder, not a crash
    assert "no byte range" in hex_text(str(p), None, 0, "#56e0f0").plain
