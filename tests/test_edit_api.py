"""The edit front door (2.0, milestone 2): doc.edit -> Patch -> verify -> commit.

A field is written through its type and the inverse of its transform; a field
whose type was only inferred is refused unless forced; raw bytes replace an
exact range; a tag goes through its metadata profile; and verify() re-reads
every edit from the new bytes and re-walks them, so a planted round-trip
failure, a disturbed neighbour bit or a new defect is caught before commit.
"""

import pytest

import acidcat
import seeds
from acidcat.core import edit as editmod
from acidcat.core.edit import PatchError, unxform, write_type
from acidcat.core.write.edits import EditError


def _open(tmp_path, fmt):
    p = tmp_path / ("seed" + seeds.suffix(fmt))
    p.write_bytes(seeds.build(fmt))
    return p, acidcat.open(p, forensics=False)


# ── fields ─────────────────────────────────────────────────────────────

def test_a_declared_bit_field_with_a_transform_writes_and_reads_back(tmp_path):
    """FLAC channels: three bits of an eight-byte container, stored minus one,
    beside the sample rate and the bit depth."""
    p, doc = _open(tmp_path, "flac")
    patch = doc.edit({"STREAMINFO#channels": 1}).verify()
    assert patch.applied == [("STREAMINFO#channels", 2, 1)]
    after = acidcat.open(patch.data, forensics=False)
    assert after.field("STREAMINFO#channels").value == 1
    assert after.field("STREAMINFO#sample_rate").value == 44100
    assert after.field("STREAMINFO#bits_per_sample").value == 16


def test_an_inferred_type_is_refused_without_force(tmp_path):
    p, doc = _open(tmp_path, "wav")
    f = doc.field("fmt_#block_align")
    assert f.type_source == "inferred"
    with pytest.raises(EditError, match="inferred type"):
        doc.edit({f.addr: 4})
    assert doc.edit({f.addr: 2}, force=True).applied


def test_a_field_with_no_encoding_takes_bytes_not_a_value():
    doc = acidcat.open(seeds.build("wav"), forensics=False)
    rows = [f for n in doc.walk() for f in n.fields
            if f.at is not None and f.type in ("display",)]
    if not rows:
        pytest.skip("no display-typed positioned field in the WAV seed")
    f = rows[0]
    with pytest.raises(EditError, match="no known encoding"):
        doc.edit({f.addr: 1}, force=True)


def test_a_value_that_does_not_fit_is_refused(tmp_path):
    p, doc = _open(tmp_path, "nsf")
    with pytest.raises(EditError, match="does not fit"):
        doc.edit({"header#songs": 300})


def test_a_derived_layer_is_not_editable(tmp_path):
    p = tmp_path / "tune.ym"
    p.write_bytes(seeds.SEEDS["ym"][0](packed="lh5"))
    doc = acidcat.open(p, forensics=False)
    with pytest.raises(EditError, match="layer 1"):
        doc.edit({"1:lh5/header#frames": b"\x00\x00\x00\x05"})


# ── bytes ──────────────────────────────────────────────────────────────

def test_raw_bytes_replace_an_exact_range(tmp_path):
    p, doc = _open(tmp_path, "wav")
    off = doc.field("fmt_#sample_rate").at.off
    patch = doc.edit({"@%d+4" % off: b"\x44\xac\x00\x00"}).verify()
    assert patch.spans() == [(off, b"\x44\xac\x00\x00", b"\x44\xac\x00\x00")]
    with pytest.raises(EditError, match="4 bytes; 3 given"):
        doc.edit({"@%d+4" % off: b"\x00\x00\x00"})


def test_overlapping_edits_are_refused(tmp_path):
    p, doc = _open(tmp_path, "wav")
    off = doc.field("fmt_#sample_rate").at.off
    with pytest.raises(EditError, match="overlaps"):
        doc.edit({"@%d+4" % off: b"\x00" * 4, "@%d+2" % (off + 2): b"\x00" * 2})


def test_bytes_and_tags_are_two_edits_not_one(tmp_path):
    p, doc = _open(tmp_path, "wav")
    with pytest.raises(EditError, match="not both"):
        doc.edit({"@0+4": b"RIFF", "title": "x"})


# ── verify ─────────────────────────────────────────────────────────────

def test_verify_catches_a_planted_round_trip_failure(tmp_path, monkeypatch):
    """The encoder writes one more than asked: the bytes change, the patch
    looks fine, and only reading them back shows it."""
    p, doc = _open(tmp_path, "nsf")
    real = editmod.write_type
    monkeypatch.setattr(editmod, "write_type",
                        lambda typ, v, old: real(typ, v + 1, old))
    patch = doc.edit({"header#songs": 2})
    with pytest.raises(PatchError, match="reads back 3, not 2"):
        patch.verify()
    assert not patch.verified


def test_verify_catches_a_disturbed_neighbour_bit(tmp_path, monkeypatch):
    p, doc = _open(tmp_path, "flac")
    real = editmod.write_type

    def sloppy(typ, v, old):
        out = bytearray(real(typ, v, old))
        out[-1] ^= 0x01                      # a bit that is not channels'
        return bytes(out)
    monkeypatch.setattr(editmod, "write_type", sloppy)
    with pytest.raises(PatchError, match="bits beside it"):
        doc.edit({"STREAMINFO#channels": 1}).verify()


def test_verify_catches_a_new_defect(tmp_path):
    """A sample rate changed alone leaves avg_bytes_per_sec disagreeing."""
    p, doc = _open(tmp_path, "wav")
    with pytest.raises(PatchError, match="new field.inconsistent defect"):
        doc.edit({"fmt_#sample_rate": 48000}, force=True).verify()
    doc.edit({"fmt_#sample_rate": 48000, "fmt_#avg_bytes_per_sec": 96000},
             force=True).verify()


def test_verify_reads_a_tag_back_through_its_profile(tmp_path, monkeypatch):
    p, doc = _open(tmp_path, "wav")
    patch = doc.edit({"title": "Kick"})
    assert patch.format == "WAV" and patch.applied == [("title", None, "Kick")]
    patch.verify()
    real = editmod.edits.edit_metadata_data

    def forgetful(data, name, changes):
        fmt, out, applied = real(data, name, changes)
        return fmt, out, [(f, "something else", n) for f, _o, n in applied]
    monkeypatch.setattr(editmod.edits, "edit_metadata_data", forgetful)
    with pytest.raises(PatchError, match="title reads back"):
        patch.verify()


# ── commit ─────────────────────────────────────────────────────────────

def test_commit_in_place_keeps_a_backup(tmp_path):
    p, doc = _open(tmp_path, "wav")
    original = p.read_bytes()
    written, backup = doc.edit({"title": "Kick"}).verify().commit()
    assert written == str(p) and backup and open(backup, "rb").read() == original
    assert p.read_bytes() != original


def test_commit_to_another_file_leaves_the_original(tmp_path):
    p, doc = _open(tmp_path, "wav")
    original = p.read_bytes()
    out = tmp_path / "out.wav"
    doc.edit({"title": "Kick"}).verify().commit(out=str(out))
    assert p.read_bytes() == original and out.exists()


def test_a_patch_from_bytes_needs_somewhere_to_go():
    doc = acidcat.open(seeds.build("wav"), forensics=False)
    with pytest.raises(EditError, match="give out="):
        doc.edit({"title": "Kick"}).commit()


# ── the codecs ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("xform,value,old,want", [
    ("add(1)", 2, 0, 1),
    ("add(-3)", 5, 0, 8),
    ("neg", -84, 0, 84),
    ("mul(4)", 12, 0, 3),
    ("fixed(16.16)", 44100, 0, 44100 << 16),
    ("mask(0x7f)", 0x10, 0x80, 0x90),
    ("rel(self)", 0x40, 0, 0x30),
    ("mask(0x7f);add(1)", 0x11, 0x80, 0x90),
])
def test_unxform_undoes_each_transform(xform, value, old, want):
    assert unxform(xform, value, old, at=0x10, node_off=0) == want


@pytest.mark.parametrize("xform,value", [("mul(4)", 13), ("mask(0x0f)", 0x10),
                                          ("ascii-dec", 2)])
def test_unxform_refuses_what_it_cannot_undo(xform, value):
    with pytest.raises(EditError):
        unxform(xform, value, 0)


@pytest.mark.parametrize("typ,value,old,want", [
    ("u16le", 0x1234, b"\x00\x00", b"\x34\x12"),
    ("i32be", -2, b"\x00" * 4, b"\xff\xff\xff\xfe"),
    ("u24be", 13224, b"\x00" * 3, b"\x00\x33\xa8"),
    ("bits:1:2:3", 5, b"\x81", b"\xa9"),     # 10 101 001: bits 2-4 from the top
    ("fourcc", "WAVE", b"xxxx", b"WAVE"),
    ("ascii", "hi", b"....", b"hi\x00\x00"),
])
def test_write_type(typ, value, old, want):
    assert write_type(typ, value, old) == want


@pytest.mark.parametrize("typ,value", [("u8", 256), ("i8", -129), ("bits:1:0:2", 4),
                                        ("ascii", "toolong"), ("display", 1)])
def test_write_type_refuses_what_does_not_fit(typ, value):
    with pytest.raises(EditError):
        write_type(typ, value, b"\x00" * 4 if typ == "ascii" else b"\x00")


# ── the callers go through it ──────────────────────────────────────────

def _plant_verify_failure(monkeypatch):
    def refuse(self):
        raise PatchError("planted")
    monkeypatch.setattr(editmod.Patch, "verify", refuse)


def test_cli_write_goes_through_the_patch(tmp_path, monkeypatch, capsys):
    from acidcat.cli import main
    p, _doc = _open(tmp_path, "wav")
    before = p.read_bytes()
    _plant_verify_failure(monkeypatch)
    assert main(["write", str(p), "--set", "title=Kick"]) == 1
    assert "planted" in capsys.readouterr().err
    assert p.read_bytes() == before


def test_cli_strip_goes_through_the_patch(tmp_path, monkeypatch, capsys):
    from acidcat.cli import main
    p, _doc = _open(tmp_path, "wav")
    main(["write", str(p), "--set", "title=Kick", "--overwrite"])
    before = p.read_bytes()
    _plant_verify_failure(monkeypatch)
    assert main(["write", str(p), "--strip"]) == 1
    assert "planted" in capsys.readouterr().err
    assert p.read_bytes() == before


_MP3 = (b"\xff\xfb\x90\x00" + b"\x00" * 413) * 16
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def test_the_cover_is_a_pseudo_field(tmp_path):
    pytest.importorskip("mutagen")
    p = tmp_path / "a.mp3"
    p.write_bytes(_MP3)
    patch = editmod.edit_path(str(p), {"cover": _PNG}).verify()
    assert patch.records[0].kind == "cover"
    patch.commit(overwrite=True)
    gone = editmod.edit_path(str(p), {"cover": None}).verify()
    assert gone.records[0].field["removed"] is True


def test_cli_cover_goes_through_the_patch(tmp_path, monkeypatch, capsys):
    pytest.importorskip("mutagen")
    from acidcat.cli import main
    p = tmp_path / "a.mp3"
    p.write_bytes(_MP3)
    img = tmp_path / "art.png"
    img.write_bytes(_PNG)
    assert main(["cover", str(p), "--set", str(img), "--overwrite"]) == 0
    out = tmp_path / "back.png"
    assert main(["cover", str(p), "-o", str(out)]) == 0
    assert out.read_bytes() == _PNG
    before = p.read_bytes()
    _plant_verify_failure(monkeypatch)
    assert main(["cover", str(p), "--remove", "--overwrite"]) == 1
    assert "planted" in capsys.readouterr().err
    assert p.read_bytes() == before


def test_the_tui_field_edit_goes_through_the_patch(tmp_path, monkeypatch):
    """The TUI's value and hex edits land as a byte-range Patch on its
    working copy."""
    pytest.importorskip("textual")
    import asyncio
    from textual.widgets import Input, Tree
    from acidcat.tui_app import app as appmod
    from acidcat.tui_app.app import AcidcatTUI

    p, _doc = _open(tmp_path, "wav")
    calls = []
    real = appmod.edit_plan

    def spy(data, name, changes, **kw):
        calls.append(dict(changes))
        return real(data, name, changes, **kw)
    monkeypatch.setattr(appmod, "edit_plan", spy)

    async def scenario():
        app = AcidcatTUI(str(p))
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
            bar.value = "48000"
            bar.focus()
            await pilot.press("enter")
            await pilot.pause()
            with open(app.work, "rb") as fh:
                return fh.read()
    work = asyncio.run(scenario())
    assert calls and list(calls[0].values()) == [b"\x80\xbb\x00\x00"]
    assert list(calls[0])[0].startswith("@") and b"\x80\xbb\x00\x00" in work
