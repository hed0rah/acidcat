"""tests for `write --strip` -- removing identifying metadata while preserving
audio and functional chunks."""
import os
import shutil

import pytest

from acidcat.core import edit_riff, edits
from acidcat.commands.write import _strip

WAV = "data/samples/Drum_Loop.wav"
MP3 = "data/test_formats/generated/mp3_44100.mp3"


def test_strip_wav_removes_tags_keeps_audio():
    data = open(WAV, "rb").read()
    new, dropped = edit_riff.strip_wav(data)
    assert "LIST" in dropped
    chunks, _ = edit_riff._iter_chunks(new)
    ids = {c[0] for c in chunks}
    assert b"LIST" not in ids and b"bext" not in ids   # tag chunks gone
    assert b"fmt " in ids and b"data" in ids            # audio + format kept
    # audio payload preserved byte-for-byte
    d0 = next(c[1] for c in edit_riff._iter_chunks(data)[0] if c[0] == b"data")
    d1 = next(c[1] for c in chunks if c[0] == b"data")
    assert d0 == d1


def test_strip_dispatch_wav(tmp_path):
    p = tmp_path / "a.wav"
    shutil.copyfile(WAV, p)
    fmt, new, removed = _strip(str(p))
    assert fmt == "WAV" and "LIST" in removed


def test_strip_tagged_mp3(tmp_path):
    pytest.importorskip("mutagen")
    if not os.path.isfile(MP3):
        pytest.skip("no mp3 fixture")
    data = open(MP3, "rb").read()
    tagged, _ = edits.edit_tagged(data, ".mp3", {"title": "Secret", "artist": "Me"})
    stripped, removed = edits.strip_tagged(tagged, ".mp3")
    assert removed                                   # something was removed
    # a second strip finds nothing left
    _again, removed2 = edits.strip_tagged(stripped, ".mp3")
    assert removed2 == []


def test_strip_unsupported_raises(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x00\x01\x02\x03" * 8)
    with pytest.raises(edits.EditError):
        _strip(str(p))
