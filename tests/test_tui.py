"""tests for the `acidcat tui` inspector plumbing. The interactive UI itself is
not unit-tested here (it is exercised by headless render during development);
these cover the pieces that must stay correct: the command registers without
the textual extra present, and the byte-offset / hex helpers match inspect's
addressing so the hex pane highlights the right bytes."""
import argparse
import os

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


def test_f_carries_optional_enc_raw():
    from acidcat.core.walk.base import _f
    d = _f(0, 2, "x", "0x0003", "note", enc="<H", raw=3)
    assert d["enc"] == "<H" and d["raw"] == 3
    plain = _f(0, 2, "y", 5)                          # optional, absent by default
    assert "enc" not in plain and "raw" not in plain


def test_all_walker_enc_annotations_verify():
    """Every field a walker annotates with enc/raw must re-encode to its actual
    on-disk bytes across the fixture corpus. A wrong endianness/width would be
    caught here (the TUI would also safely reject it, but annotating is pointless
    if it never verifies)."""
    pytest.importorskip("textual")
    from acidcat.core.walk import walk_file, Unsupported
    from acidcat.tui_app import encode_value, _field_abs
    fixtures = [
        "data/samples/Drum_Loop.wav",
        "data/test_formats/generated/mp3_44100.mp3",
        "data/test_formats/generated/aiff_pcm.aiff",
        "data/test_formats/generated/flac24.flac",
        "data/test_formats/gs-16b-2c-44100hz.ogg",
        "data/test_formats/gs-16b-2c-44100hz.m4a",
        "data/test_formats/generated/aiff_pcm.aiff",
    ]
    checked = 0
    for path in fixtures:
        if not os.path.isfile(path):
            continue
        data = open(path, "rb").read()
        try:
            _f, chunks, _w = walk_file(path, deep=True)
        except Unsupported:
            continue
        for c in chunks:
            for fl in c.get("fields", []):
                if "enc" not in fl:
                    continue
                ab = _field_abs(c, fl)
                if ab is None:
                    continue
                rb = data[ab:ab + fl["len"]]
                raw = fl.get("raw", fl.get("value"))
                assert encode_value(fl["enc"], str(raw)) == rb, (
                    f"{path} {c['id']} {fl['name']}: enc {fl['enc']!r} "
                    f"does not reproduce the on-disk bytes")
                checked += 1
    assert checked > 0, "no enc-annotated fields were checked"


def test_walker_enc_verified_against_bytes():
    """A walker's declared enc+raw must reproduce the on-disk bytes -- that is
    exactly the guard the TUI checks before trusting an annotation for value
    editing. format_tag stores a hex-string value, so enc/raw is what makes it
    value-editable at all."""
    pytest.importorskip("textual")
    from acidcat.core.walk import walk_file
    from acidcat.tui_app import encode_value, _field_abs
    _fmt, chunks, _w = walk_file("data/samples/Drum_Loop.wav", deep=True)
    fmtc = next(c for c in chunks if c["id"].strip() == "fmt")
    f = next(fl for fl in fmtc["fields"] if fl["name"] == "format_tag")
    assert f.get("enc") == "<H" and "raw" in f
    abs_off = _field_abs(fmtc, f)
    raw_bytes = open("data/samples/Drum_Loop.wav", "rb").read()[abs_off:abs_off + f["len"]]
    assert encode_value(f["enc"], str(f["raw"])) == raw_bytes


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


def test_working_copy_defers_write_until_save(tmp_path):
    """Edits apply to a temp working copy; the original file is untouched until an
    explicit save, which then makes a pristine backup."""
    pytest.importorskip("textual")
    import asyncio
    import shutil
    from acidcat.tui_app import AcidcatTUI
    from textual.widgets import Tree, Input

    orig = tmp_path / "d.wav"
    shutil.copyfile("data/samples/Drum_Loop.wav", orig)
    pristine = orig.read_bytes()

    async def scenario():
        app = AcidcatTUI(str(orig))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.work and os.path.isfile(app.work)
            node = None
            for cn in app.query_one("#tree", Tree).root.children:
                for fn in cn.children:
                    lbl = fn.label.plain if hasattr(fn.label, "plain") else str(fn.label)
                    if lbl.startswith("sample_rate"):
                        node = fn
            assert node is not None
            app._cur_node = node
            app.action_edit_field()
            await pilot.pause()
            app.query_one("#editbar", Input).value = "69"
            await pilot.press("enter")
            await pilot.pause()
            assert app.dirty and orig.read_bytes() == pristine   # not written yet
            app.action_save()
            await pilot.pause()
            assert not app.dirty and orig.read_bytes() != pristine

    asyncio.run(scenario())
    bak = tmp_path / "d_original.wav"
    assert bak.exists() and bak.read_bytes() == pristine


def test_edit_mode_toggle(tmp_path):
    """ctrl+t flips a field edit between value and raw hex, converting the bar
    text so the two views stay consistent."""
    pytest.importorskip("textual")
    import asyncio
    import shutil
    from acidcat.tui_app import AcidcatTUI
    from textual.widgets import Tree, Input

    orig = tmp_path / "t.wav"
    shutil.copyfile("data/samples/Drum_Loop.wav", orig)

    async def scenario():
        app = AcidcatTUI(str(orig))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            node = None
            for cn in app.query_one("#tree", Tree).root.children:
                for fn in cn.children:
                    lbl = fn.label.plain if hasattr(fn.label, "plain") else str(fn.label)
                    if lbl.startswith("sample_rate"):
                        node = fn
            app._cur_node = node
            app.action_edit_field()
            await pilot.pause()
            bar = app.query_one("#editbar", Input)
            assert app._edit_target["mode"] == "value" and bar.value == "44100"
            app.action_toggle_mode()
            await pilot.pause()
            assert app._edit_target["mode"] == "hex" and bar.value == "44 ac 00 00"
            app.action_toggle_mode()
            await pilot.pause()
            assert app._edit_target["mode"] == "value" and bar.value == "44100"

    asyncio.run(scenario())


def test_text_field_routes_to_metadata_engine(tmp_path):
    """A variable-length text field (INFO comment) edits as text via the write
    engine, so a longer value is written correctly instead of a same-length byte
    patch that couldn't change the length."""
    pytest.importorskip("textual")
    import asyncio
    import shutil
    from acidcat.tui_app import AcidcatTUI
    from acidcat.core.walk import walk_file
    from textual.widgets import Tree, Input

    orig = tmp_path / "c.wav"
    shutil.copyfile("data/samples/Drum_Loop.wav", orig)
    new = "a deliberately much longer comment than the original, to prove length"

    async def scenario():
        app = AcidcatTUI(str(orig))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            node = None
            for cn in app.query_one("#tree", Tree).root.children:
                for fn in cn.children:
                    lbl = fn.label.plain if hasattr(fn.label, "plain") else str(fn.label)
                    if lbl.startswith("ICMT"):
                        node = fn
            assert node is not None
            app._cur_node = node
            app.action_edit_field()
            await pilot.pause()
            assert app._edit_target["mode"] == "text"
            assert app._edit_target["metafield"] == "comment"
            app.query_one("#editbar", Input).value = new
            await pilot.press("enter")
            await pilot.pause()
            assert app.dirty
            app.action_save()
            await pilot.pause()

    asyncio.run(scenario())
    _f, chunks, _w = walk_file(str(orig), deep=True)
    got = None
    for c in chunks:
        for fl in c.get("fields", []):
            if fl["name"] == "ICMT":
                got = fl["value"]
    assert got == new


def test_in_pane_hex_edit(tmp_path):
    """Tab into the hex pane, move a cursor, and overwrite bytes in place; Enter
    applies to the working copy (still unsaved until ctrl+s)."""
    pytest.importorskip("textual")
    import asyncio
    import shutil
    from acidcat.tui_app import AcidcatTUI, HexPane
    from textual.widgets import Tree

    orig = tmp_path / "h.wav"
    shutil.copyfile("data/samples/Drum_Loop.wav", orig)

    async def scenario():
        app = AcidcatTUI(str(orig))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            node = None
            for cn in app.query_one("#tree", Tree).root.children:
                for fn in cn.children:
                    lbl = fn.label.plain if hasattr(fn.label, "plain") else str(fn.label)
                    if lbl.startswith("sample_rate"):
                        node = fn
            app._cur_node = node
            off, _length, _ = app._nodemeta[id(node)]
            app.action_hex_focus()
            await pilot.pause()
            assert app._hexedit is not None
            assert isinstance(app.focused, HexPane)
            await pilot.press("4")             # high nibble
            await pilot.press("5")             # low nibble -> byte 0 = 0x45
            await pilot.pause()
            assert app._hexedit["cur"] == 1    # cursor advanced
            await pilot.press("enter")
            await pilot.pause()
            assert open(app.work, "rb").read()[off] == 0x45 and app.dirty

    asyncio.run(scenario())


def test_hex_text_offsets_and_empty(tmp_path):
    pytest.importorskip("textual")
    from acidcat.tui_app import hex_text
    p = tmp_path / "b.bin"
    p.write_bytes(bytes(range(48)))            # 3 rows of 16
    t = hex_text(str(p), 0, 48, "#56e0f0").plain
    assert "00000000" in t and "00000010" in t and "00000020" in t
    # a node with no byte range renders a placeholder, not a crash
    assert "no byte range" in hex_text(str(p), None, 0, "#56e0f0").plain
