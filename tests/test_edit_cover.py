"""`edit`'s cover path honours --dry-run and -o, and a WAV or AIFF takes a
cover.

`edit --set cover=@IMG` went through the 1.8 `cover` verb's parser, which has
no --dry-run and reads -o as "extract to", so a dry run embedded the cover
and wrote a backup, and `-o copy.mp3` rewrote the input in place. `--unset
cover` on a file with no cover rewrote it (and made a backup) to change
nothing. And the audio guard hashed a RIFF or FORM file whole, by the mp3
rule, so the ID3 chunk mutagen adds after the sound data read as changed
audio and every WAV and AIFF cover edit was refused.
"""

import hashlib

import pytest

import seeds
from acidcat.cli import main
from acidcat.core.write import edits

pytest.importorskip("mutagen")

JPEG = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xd9")


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def art(tmp_path):
    p = tmp_path / "art.jpg"
    p.write_bytes(JPEG)
    return p


def _seed(tmp_path, fmt, name):
    p = tmp_path / name
    p.write_bytes(seeds.build(fmt))
    return p


def test_a_dry_run_writes_nothing(tmp_path, art, capsys):
    mp3 = _seed(tmp_path, "mp3", "t.mp3")
    before = _sha(mp3)
    assert main(["edit", str(mp3), "--set", f"cover=@{art}", "--dry-run"]) == 0
    assert "nothing written" in capsys.readouterr().out
    assert _sha(mp3) == before
    assert not (tmp_path / "t_original.mp3").exists()


def test_an_output_leaves_the_input_alone(tmp_path, art):
    mp3 = _seed(tmp_path, "mp3", "t.mp3")
    before = _sha(mp3)
    out = tmp_path / "copy.mp3"
    assert main(["edit", str(mp3), "--set", f"cover=@{art}", "-o", str(out)]) == 0
    assert _sha(mp3) == before
    assert main(["edit", str(out), "--get", "cover", "-o", str(tmp_path / "x.jpg")]) == 0
    assert (tmp_path / "x.jpg").read_bytes() == JPEG


def test_removing_no_cover_writes_nothing(tmp_path, capsys):
    mp3 = _seed(tmp_path, "mp3", "t.mp3")
    before = _sha(mp3)
    assert main(["edit", str(mp3), "--unset", "cover"]) == 0
    assert "no embedded cover art" in capsys.readouterr().err
    assert _sha(mp3) == before
    assert not (tmp_path / "t_original.mp3").exists()


def test_a_removal_dry_run_writes_nothing(tmp_path, art):
    mp3 = _seed(tmp_path, "mp3", "t.mp3")
    assert main(["edit", str(mp3), "--set", f"cover=@{art}", "--overwrite"]) == 0
    before = _sha(mp3)
    assert main(["edit", str(mp3), "--unset", "cover", "--dry-run"]) == 0
    assert _sha(mp3) == before


@pytest.mark.parametrize("fmt,name,sound", [("wav", "c.wav", b"data"),
                                            ("aiff", "c.aiff", b"SSND")])
def test_a_wav_or_aiff_takes_a_cover_and_keeps_its_audio(tmp_path, art, fmt,
                                                          name, sound):
    p = _seed(tmp_path, fmt, name)
    old = p.read_bytes()
    assert main(["edit", str(p), "--set", f"cover=@{art}", "--overwrite"]) == 0
    new = p.read_bytes()
    assert new != old
    assert edits._audio_digest(new) == edits._audio_digest(old)
    assert main(["edit", str(p), "--get", "cover", "-o", str(tmp_path / "x.jpg")]) == 0
    assert (tmp_path / "x.jpg").read_bytes() == JPEG


def test_the_guard_still_sees_changed_wav_audio():
    old = seeds.build("wav")
    i = old.index(b"data") + 8
    new = old[:i] + bytes([old[i] ^ 0xFF]) + old[i + 1:]
    with pytest.raises(edits.EditError, match="audio payload changed"):
        edits._verify_audio_preserved(old, new)
    edits._verify_audio_preserved(old, old + b"id3 " + bytes(4))
