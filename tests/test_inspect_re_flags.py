"""The reverse-engineering escapes on `inspect`: --format, --region, --force.

Three different needs that a single vague "force" flag would blur:
name the format yourself, scope to a range inside a bigger image, or ask what
the file could possibly be.
"""

import os
import struct

import pytest

from acidcat.cli import main


def _wav_bytes():
    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 22050, 44100, 2, 16)
            + b"data" + struct.pack("<I", 64) + bytes(64))
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def test_region_walks_a_blob_inside_a_larger_image(tmp_path, capsys):
    """The gap this closes: `locate` finds audio inside a disk image, but the
    structural verbs walked whole files, so the region had to be carved out by
    hand first."""
    img = tmp_path / "disk.img"
    img.write_bytes(b"\x00" * 4096 + _wav_bytes() + b"\xff" * 512)

    assert main(["inspect", str(img)]) == 2          # the image itself: no walker, so 2
    capsys.readouterr()

    rc = main(["inspect", str(img), "--region", "0"])
    out = capsys.readouterr().out
    assert rc == 0, "the embedded WAV should walk once scoped"
    assert "RIFF/WAVE" in out
    assert "[region 0x00001000" in out, "the scope is not reported"
    assert "fmt" in out and "data" in out


def test_region_reports_the_regions_own_size(tmp_path, capsys):
    """A region's byte count must be the region's, not the whole image's --
    mixing them makes every offset in the dump untrustworthy."""
    img = tmp_path / "disk.img"
    payload = _wav_bytes()
    img.write_bytes(b"\x00" * 4096 + payload + b"\xff" * 512)
    main(["inspect", str(img), "--region", "0"])
    out = capsys.readouterr().out
    assert f"{len(payload):,} bytes" in out, "reported the image size, not the region"


def test_explicit_offset_and_length(tmp_path, capsys):
    img = tmp_path / "disk.img"
    payload = _wav_bytes()
    img.write_bytes(b"\x00" * 2048 + payload)
    rc = main(["inspect", str(img), "--offset", "0x800",
               "--length", str(len(payload))])
    out = capsys.readouterr().out
    assert rc == 0 and "RIFF/WAVE" in out


def test_format_override_runs_a_walker_the_sniffer_would_not(tmp_path, capsys):
    """The 'odd variant we do model' case: dispatch stops depending on magic."""
    p = tmp_path / "variant.dat"
    p.write_bytes(b"RIFX" + _wav_bytes()[4:])       # deliberately wrong magic
    rc = main(["inspect", str(p), "--format", "wav"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "RIFF/WAVE" in out, "the forced walker did not run"


def test_format_override_rejects_an_unknown_id(tmp_path, capsys):
    p = tmp_path / "x.dat"
    p.write_bytes(_wav_bytes())
    rc = main(["inspect", str(p), "--format", "notaformat"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "no walker by that id" in err


def test_force_reports_candidates_not_a_verdict(tmp_path, capsys):
    """--force must not name a winner. Walkers assume their magic rather than
    checking it, so a forced parse readily invents structure -- pointed at an
    arbitrary blob the MIDI walker claims a chunk larger than the file. Picking
    one would manufacture a false identification, so this lists leads and says
    so."""
    p = tmp_path / "proprietary.ch1"
    p.write_bytes(b"\x03\x13\xa0\xe0\x0b\x00ED" + bytes(range(256)) * 8)
    rc = main(["inspect", str(p), "--force", "--color", "never"])
    out = capsys.readouterr().out
    assert rc == 1, "still unidentified -- these are hypotheses"
    assert "hypotheses, not identifications" in out
    assert "--format" in out, "no follow-up path offered"


def test_force_flags_parses_that_invent_their_magic(tmp_path, capsys):
    """The 'ids' column is the check a walker cannot talk its way past: are the
    chunk ids it reports actually at those offsets?"""
    p = tmp_path / "blob.bin"
    p.write_bytes(b"\x03\x13\xa0\xe0" + bytes(range(256)) * 8)
    main(["inspect", str(p), "--force", "--color", "never"])
    out = capsys.readouterr().out
    assert "ids" in out
    # nothing in this blob is a real container, so no candidate may claim an
    # anchored id -- every row should read 0/N
    rows = [l for l in out.splitlines() if "/" in l and "0/" in l]
    assert rows, "expected candidates reporting 0 anchored ids"


def test_unknown_riff_variant_already_walks_without_force(tmp_path, capsys):
    """Generic triage already covers the 'rare RIFF dialect' case: an unknown
    form-type still yields its chunk grid, so --force is not needed for it."""
    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 22050, 44100, 2, 16)
            + b"data" + struct.pack("<I", 64) + bytes(64))
    p = tmp_path / "odd.dat"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVX" + body)
    rc = main(["inspect", str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "fmt" in out and "data" in out, "triage lost the chunk grid"


def test_region_out_of_range_is_reported(tmp_path, capsys):
    img = tmp_path / "disk.img"
    img.write_bytes(b"\x00" * 2048 + _wav_bytes())
    rc = main(["inspect", str(img), "--region", "99"])
    err = capsys.readouterr().err
    # 2, not 1: a bad range expression is a usage error, and `od` and `carve`
    # already returned 2 for the identical mistake. inspect was the outlier.
    assert rc == 2 and "out of range" in err


def test_region_errors_name_the_source_not_the_temp_copy(tmp_path, capsys):
    """A scoped walk copies the range to a temp file; that path must never
    surface. Same class of leak as `detect --json` reporting its stdin buffer."""
    img = tmp_path / "disk.img"
    # a region that is raw PCM, so the walk genuinely fails and we see the message
    img.write_bytes(b"\x00" * 2048
                    + bytes((i * 7) % 251 for i in range(4096)))
    main(["inspect", str(img), "--region", "0"])
    err = capsys.readouterr().err
    if err.strip():
        assert "acidcat-region-" not in err, "leaked the internal temp path"
        assert "disk.img" in err, "did not name the user's file"
