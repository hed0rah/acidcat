"""tests for `write --strip` -- removing identifying metadata while preserving
audio and functional chunks."""
import os
import shutil
import struct

import pytest

from acidcat.core import edit_aiff, edit_riff, edits
from acidcat.commands.write import _strip

WAV = "data/samples/Drum_Loop.wav"
MP3 = "data/test_formats/generated/mp3_44100.mp3"
FIXTURES = "data/test_formats"


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


# ── AIFF strip (rewrites the whole FORM; previously untested) ──────

def _aiff_chunk(cid, payload):
    return (cid + struct.pack(">I", len(payload)) + payload
            + (b"\x00" if len(payload) & 1 else b""))


def _aiff(*chunks):
    body = b"AIFF" + b"".join(chunks)
    return b"FORM" + struct.pack(">I", len(body)) + body


_RATE80 = bytes.fromhex("400EAC44000000000000")   # 44100 Hz, 80-bit extended


def test_strip_aiff_removes_text_keeps_audio():
    ssnd_payload = struct.pack(">II", 0, 0) + bytes(range(8))
    data = _aiff(
        _aiff_chunk(b"COMM", struct.pack(">HIH", 1, 4, 16) + _RATE80),
        _aiff_chunk(b"NAME", b"Secret Sample"),
        _aiff_chunk(b"ANNO", b"made with love"),   # even/odd payloads mixed
        _aiff_chunk(b"SSND", ssnd_payload),
    )
    new, dropped = edit_aiff.strip_aiff(data)
    assert sorted(dropped) == ["ANNO", "NAME"]
    chunks, _ = edit_aiff._iter_chunks(new)
    ids = {c[0] for c in chunks}
    assert b"NAME" not in ids and b"ANNO" not in ids
    assert b"COMM" in ids and b"SSND" in ids
    assert next(c[1] for c in chunks if c[0] == b"SSND") == ssnd_payload
    # FORM size recomputed to cover exactly the remaining chunks
    assert struct.unpack_from(">I", new, 4)[0] == len(new) - 8


def test_strip_dispatch_aiff(tmp_path):
    p = tmp_path / "a.aiff"
    p.write_bytes(_aiff(
        _aiff_chunk(b"COMM", struct.pack(">HIH", 1, 4, 16) + _RATE80),
        _aiff_chunk(b"NAME", b"x"),
        _aiff_chunk(b"SSND", struct.pack(">II", 0, 0) + bytes(8)),
    ))
    fmt, new, removed = _strip(str(p))
    assert fmt == "AIFF" and removed == ["NAME"]


# ── tagged strips beyond mp3, with the audio-preservation invariant ─

@pytest.mark.parametrize("name,suffix", [
    ("gs-16b-2c-44100hz.mp3", ".mp3"),
    ("gs-16b-2c-44100hz.flac", ".flac"),
    ("gs-16b-2c-44100hz.ogg", ".ogg"),
    ("gs-16b-2c-44100hz.opus", ".opus"),
    ("gs-16b-2c-44100hz.m4a", ".m4a"),
])
def test_strip_tagged_preserves_audio(name, suffix):
    pytest.importorskip("mutagen")
    p = os.path.join(FIXTURES, name)
    if not os.path.isfile(p):
        pytest.skip(f"no {suffix} fixture")
    data = open(p, "rb").read()
    tagged, _ = edits.edit_tagged(data, suffix, {"title": "Secret"})
    stripped, removed = edits.strip_tagged(tagged, suffix)
    assert removed
    # the audio fingerprint survives both the edit and the strip
    _, d0 = edits._audio_digest(data)
    _, d1 = edits._audio_digest(tagged)
    _, d2 = edits._audio_digest(stripped)
    assert d0 == d1 == d2
