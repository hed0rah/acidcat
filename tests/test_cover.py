"""Cover-art extract/embed/remove and custom ID3 TXXX frames."""
import pytest

mutagen = pytest.importorskip("mutagen")  # cover/TXXX ride on mutagen (an extra)

from acidcat.core import cover, edits  # noqa: E402

# 16 MPEG1 Layer III 128 kbps / 44.1 kHz frames (417 bytes each) -> mutagen syncs
_MP3 = (b"\xff\xfb\x90\x00" + b"\x00" * 413) * 16
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_cover_embed_extract_remove(tmp_path):
    p = _write(tmp_path, "a.mp3", _MP3)
    assert cover.extract(p) is None
    cover.set_cover(p, _PNG)
    got = cover.extract(p)
    assert got is not None
    assert got[0] == "image/png" and got[1] == _PNG
    assert cover.remove_cover(p) is True
    assert cover.extract(p) is None


def test_txxx_write_and_read_back(tmp_path):
    p = _write(tmp_path, "b.mp3", _MP3)
    out, applied = edits.edit_tagged(_MP3, ".mp3",
                                     {"txxx:MOOD": "dark", "txxx:RATING": "5"})
    p2 = _write(tmp_path, "c.mp3", out)
    from mutagen.id3 import ID3
    frames = {fr.desc: fr.text[0] for fr in ID3(p2).getall("TXXX")}
    assert frames == {"MOOD": "dark", "RATING": "5"}
    # clearing a TXXX removes it
    out2, _ = edits.edit_tagged(out, ".mp3", {"txxx:MOOD": ""})
    p3 = _write(tmp_path, "d.mp3", out2)
    left = {fr.desc for fr in ID3(p3).getall("TXXX")}
    assert left == {"RATING"}


def test_decode_txxx_shows_description():
    from acidcat.core.walk.mp3 import _decode_txxx
    assert _decode_txxx(b"\x03MOOD\x00nocturnal", "TXXX") == "MOOD = nocturnal"
    assert _decode_txxx(b"\x03\x00justvalue", "TXXX") == "justvalue"
