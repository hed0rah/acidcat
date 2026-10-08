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
    p, doc = _open(tmp_path, "8svx")
    f = doc.field("FORM/VHDR#samplesPerSec")
    assert f.type_source == "inferred"
    with pytest.raises(EditError, match="inferred type"):
        doc.edit({f.addr: 16000})
    assert doc.edit({f.addr: 16000}, force=True).applied


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
    off = doc.field("RIFF/fmt_#sample_rate").at.off
    patch = doc.edit({"@%d+4" % off: b"\x44\xac\x00\x00"}).verify()
    assert patch.spans() == [(off, b"\x44\xac\x00\x00", b"\x44\xac\x00\x00")]
    with pytest.raises(EditError, match="4 bytes; 3 given"):
        doc.edit({"@%d+4" % off: b"\x00\x00\x00"})


def test_overlapping_edits_are_refused(tmp_path):
    p, doc = _open(tmp_path, "wav")
    off = doc.field("RIFF/fmt_#sample_rate").at.off
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
        doc.edit({"RIFF/fmt_#sample_rate": 48000}, force=True).verify()
    doc.edit({"RIFF/fmt_#sample_rate": 48000, "RIFF/fmt_#avg_bytes_per_sec": 96000},
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
    patch.commit(backup=False)
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


# ── repair() and the documented example (review R2) ────────────────────

def _doc_example():
    """The edit lines of architecture-2.0.md section 6, as written."""
    import pathlib
    text = (pathlib.Path(__file__).resolve().parent.parent
            / "docs" / "contract" / "architecture-2.0.md").read_text(encoding="utf-8")
    lines = text.split("## 6. The Python API", 1)[1].split("```", 2)[1].splitlines()
    start = next(i for i, l in enumerate(lines) if 'acidcat.open("kick.wav")' in l)
    end = next(i for i, l in enumerate(lines) if ".commit(" in l)
    return [l.split("#", 1)[0].rstrip() if not l.lstrip().startswith("patch = doc.edit")
            else l for l in lines[start:end + 1]]


def test_the_documented_edit_runs_end_to_end(tmp_path):
    """open, edit the sample rate, repair, verify, commit: the example the
    architecture page and core/edit.py lead with."""
    src = tmp_path / "kick.wav"
    src.write_bytes(seeds.build("wav"))
    out = tmp_path / "out.wav"
    code = "\n".join(_doc_example())
    assert "patch.repair()" in code and "backup=True" in code
    code = code.replace('"kick.wav"', repr(str(src))).replace('"out.wav"', repr(str(out)))
    exec(code, {"acidcat": acidcat})
    doc = acidcat.open(out, forensics=False)
    assert doc.field("RIFF/fmt_#sample_rate").value == 48000
    align = doc.field("RIFF/fmt_#block_align").value
    assert doc.field("RIFF/fmt_#avg_bytes_per_sec").value == 48000 * align
    assert not [f for f in doc.findings if f.kind == "defect"]
    assert src.read_bytes() == seeds.build("wav"), "the input was touched"


def test_fmt_fields_are_typed_by_the_walker(tmp_path):
    _p, doc = _open(tmp_path, "wav")
    fmt = doc.node("RIFF/fmt_")
    assert {f.key: f.type_source for f in fmt.fields} == {
        k: "enc" for k in ("format_tag", "channels", "sample_rate",
                           "avg_bytes_per_sec", "block_align", "bits_per_sample")}
    # so a sample-rate edit needs no force=True
    doc.edit({"RIFF/fmt_#sample_rate": 48000}).repair().verify()


def test_repair_reports_what_it_changed_and_nothing_else(tmp_path):
    _p, doc = _open(tmp_path, "wav")
    patch = doc.edit({"RIFF/fmt_#sample_rate": 22050}).repair()
    align = doc.field("RIFF/fmt_#block_align").value
    old = doc.field("RIFF/fmt_#avg_bytes_per_sec").value
    assert patch.repairs == [("RIFF/fmt_#avg_bytes_per_sec", old, 22050 * align,
                              "sample_rate * block_align")]
    assert patch.repairs[0].follows == "sample_rate * block_align"
    assert not patch.verified
    patch.verify()


def test_repair_with_nothing_out_of_step_is_a_no_op(tmp_path):
    _p, doc = _open(tmp_path, "wav")
    patch = doc.edit({"title": "Kick"})
    data = patch.data
    assert patch.repair().data == data and patch.repairs == []


def test_repair_leaves_the_originals_own_violations_to_check_fix(tmp_path):
    """A file that already disagrees with itself is not this edit's to fix."""
    import struct
    raw = bytearray(seeds.build("wav"))
    struct.pack_into("<I", raw, 4, len(raw) - 12)   # a stale RIFF size
    p = tmp_path / "bad.wav"
    p.write_bytes(bytes(raw))
    doc = acidcat.open(p, forensics=False)
    with pytest.raises(PatchError, match="check --fix"):
        doc.edit({"RIFF/fmt_#sample_rate": 48000}).repair()


def test_a_rate_that_was_already_wrong_follows_the_new_one(tmp_path):
    """The edit moved what avg_bytes_per_sec should be, so it is the
    patch's to set, even though it was wrong before."""
    import struct
    raw = bytearray(seeds.build("wav"))
    struct.pack_into("<I", raw, 28, 1)
    p = tmp_path / "bad.wav"
    p.write_bytes(bytes(raw))
    doc = acidcat.open(p, forensics=False)
    patch = doc.edit({"RIFF/fmt_#sample_rate": 48000}).repair()
    assert [r.addr for r in patch.repairs] == ["RIFF/fmt_#avg_bytes_per_sec"]


def test_the_smpl_period_follows_the_rate(tmp_path):
    import struct
    from acidcat.core.write import raterepair
    body = b"WAVE"
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    body += b"fmt " + struct.pack("<I", 16) + fmt
    body += b"smpl" + struct.pack("<I", 36) + struct.pack("<9I", 0, 0, 22676, 60, 0, 0, 0, 0, 0)
    body += b"data" + struct.pack("<I", 8) + bytes(8)
    wav = b"RIFF" + struct.pack("<I", len(body)) + body
    assert raterepair.analyze(wav) == []
    doc = acidcat.open(wav, forensics=False)
    patch = doc.edit({"RIFF/fmt_#sample_rate": 48000}).repair()
    fields = {r.addr for r in patch.repairs}
    assert fields == {"RIFF/fmt_#avg_bytes_per_sec", "RIFF/smpl#sample_period"}
    assert struct.unpack_from("<I", patch.data, 12 + 24 + 8 + 8)[0] == round(1e9 / 48000)


def test_commit_backup_false_writes_in_place_without_one(tmp_path):
    import os
    p, doc = _open(tmp_path, "wav")
    written, backup = doc.edit({"title": "Kick"}).verify().commit(backup=False)
    assert written == str(p) and backup is None
    assert not os.path.exists(str(p) + "_original") and not [
        n for n in os.listdir(tmp_path) if "_original" in n]


# ── the CLI cascades (review R2, follow-up) ─────────────────────────────

def test_the_cli_edit_cascades_and_says_so(tmp_path, capsys):
    from acidcat.cli import main
    src = tmp_path / "f.wav"
    src.write_bytes(seeds.build("wav"))
    out = tmp_path / "out.wav"
    before = acidcat.open(src, forensics=False)
    old = before.field("RIFF/fmt_#avg_bytes_per_sec").value
    align = before.field("RIFF/fmt_#block_align").value
    rc = main(["edit", str(src), "--set", "RIFF/fmt_#sample_rate=22050",
               "-o", str(out)])
    got = capsys.readouterr()
    assert rc == 0, got.err
    assert ("  RIFF/fmt_#avg_bytes_per_sec: %d -> %d (follows sample_rate * "
            "block_align)" % (old, 22050 * align)) in got.out.splitlines()
    doc = acidcat.open(out, forensics=False)
    assert doc.field("RIFF/fmt_#sample_rate").value == 22050
    assert doc.field("RIFF/fmt_#avg_bytes_per_sec").value == 22050 * align
    assert src.read_bytes() == seeds.build("wav")


def test_the_cli_edit_no_cascade_refuses(tmp_path, capsys):
    from acidcat.cli import main
    src = tmp_path / "f.wav"
    src.write_bytes(seeds.build("wav"))
    out = tmp_path / "out.wav"
    rc = main(["edit", str(src), "--set", "RIFF/fmt_#sample_rate=22050",
               "--no-cascade", "-o", str(out)])
    got = capsys.readouterr()
    assert rc == 1 and "new field.inconsistent defect" in got.err
    assert not out.exists()


def test_the_cli_edit_json_lists_the_cascade(tmp_path, capsys):
    import json
    from acidcat.cli import main
    src = tmp_path / "f.wav"
    src.write_bytes(seeds.build("wav"))
    rc = main(["edit", str(src), "--set", "RIFF/fmt_#sample_rate=22050",
               "--dry-run", "--json"])
    got = capsys.readouterr()
    assert rc == 0, got.err
    row = json.loads(got.out)
    row = row[0] if isinstance(row, list) else row
    assert [c["field"] for c in row["cascade"]] == ["RIFF/fmt_#avg_bytes_per_sec"]
    assert row["cascade"][0]["follows"] == "sample_rate * block_align"
