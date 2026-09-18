"""CMF: a MIDI track for the OPL2 with its patches in front."""

import os
import struct

import pytest

from acidcat.core.formats import cmf as cmfmod
from acidcat.core.infra import sniff
from acidcat.core.walk import cmf as walker

import seeds


def _cmf(**kw):
    return seeds.SEEDS["cmf"][0](**kw)


def _write(tmp_path, blob, name="a.cmf"):
    p = tmp_path / name
    p.write_bytes(blob)
    return p


def _tiles(chunks, size):
    pos = 0
    for c in sorted(chunks, key=lambda c: c["offset"]):
        if c["offset"] != pos:
            return False
        pos += c["size"]
    return pos == size


def test_a_cmf_is_identified_by_its_magic(tmp_path):
    assert sniff.sniff(str(_write(tmp_path, _cmf()))) == "cmf"
    assert "cmf" in sniff.KNOWN_FORMATS


def test_the_header_reads_offsets_clock_and_count():
    raw = _cmf(instruments=3)
    h = cmfmod.parse_header(raw, len(raw))
    assert h["ok"] and h["version_text"] == "1.1"
    assert h["instrument_count"] == 3 and h["tempo"] == 120
    assert h["ticks_per_quarter"] == 48 and h["ticks_per_second"] == 96
    assert h["channels_used"] == [0]


def test_a_1_0_header_has_no_count_and_derives_it_from_the_offsets():
    raw = _cmf(version=0x100, instruments=3)
    h = cmfmod.parse_header(raw, len(raw))
    assert h["header_size"] == cmfmod.HEADER_V10 and h["tempo"] is None
    assert h["instrument_count"] == 3


def test_an_instrument_is_two_interleaved_operators_and_a_connection():
    raw = _cmf(instruments=1)
    h = cmfmod.parse_header(raw, len(raw))
    ins = cmfmod.instrument(raw, h["instruments_at"])
    assert ins["modulator"]["ave"] == 0x01 and ins["carrier"]["ave"] == 0x11
    assert ins["modulator"]["attack_decay"] == 0xF1 and ins["carrier"]["attack_decay"] == 0xD2
    assert ins["feedback_conn"] == 0x06


def test_the_walk_tiles_with_the_track_read_by_the_midi_scanner(tmp_path):
    p = _write(tmp_path, _cmf(instruments=2, notes=4))
    chunks, warns = walker.inspect_cmf(str(p))
    ids = [c["id"] for c in chunks]
    assert ids == ["header", "title", "inst[0]", "inst[1]", "music", "terminator"]
    assert _tiles(chunks, os.path.getsize(p))
    music = chunks[4]
    assert "4 notes" in music["summary"]
    assert "SEED" in chunks[0]["summary"]
    assert not warns


def test_the_stray_0xff_after_end_of_track_is_named(tmp_path):
    """372 of 459 real files end with one 0xFF past the track's own end."""
    p = _write(tmp_path, _cmf(terminator=True))
    chunks, _w = walker.inspect_cmf(str(p))
    assert chunks[-1]["id"] == "terminator" and chunks[-1]["size"] == 1
    p = _write(tmp_path, _cmf(terminator=False), "b.cmf")
    chunks, _w = walker.inspect_cmf(str(p))
    assert chunks[-1]["id"] == "music"


def test_a_text_offset_inside_the_music_or_past_the_end_is_said_not_read(tmp_path):
    blob = bytearray(_cmf())
    h = cmfmod.parse_header(bytes(blob), len(blob))
    struct.pack_into("<H", blob, 0x0E, h["music_at"] + 3)          # title inside the track
    struct.pack_into("<H", blob, 0x10, 0x4000)                     # composer past the end
    chunks, warns = walker.inspect_cmf(str(_write(tmp_path, bytes(blob))))
    assert any("title offset" in w and "inside the music" in w for w in warns)
    assert any("composer offset" in w and "past the end" in w for w in warns)
    assert not any(c["id"] in ("title", "composer") for c in chunks)
    assert _tiles(chunks, len(blob))


def test_a_track_without_end_of_track_is_said(tmp_path):
    raw = _cmf(terminator=False)
    _chunks, warns = walker.inspect_cmf(str(_write(tmp_path, raw[:-4])))
    assert any("no End of Track" in w for w in warns)


@pytest.mark.parametrize("n", [5, 0x24, 0x30, 0x40])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    from acidcat.core.walk.base import Unsupported
    try:
        walker.inspect_cmf(str(_write(tmp_path, _cmf()[:n])))
    except Unsupported:
        pass


@pytest.mark.skipif(not os.environ.get("ACIDCAT_CMF_CORPUS"),
                    reason="set ACIDCAT_CMF_CORPUS to a dir of real .cmf files")
def test_real_corpus_walks_completely():
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file
    root = os.environ["ACIDCAT_CMF_CORPUS"]
    files = [os.path.join(r, f) for r, _d, fn in os.walk(root) for f in fn
             if f.lower().endswith(".cmf")]
    assert len(files) >= 50
    seen = tiled = 0
    for path in files:
        if sniff.sniff(path) != "cmf":
            continue
        seen += 1
        _label, chunks, _warns = walk_file(path)
        size = os.path.getsize(path)
        geometry.normalize(chunks, size)
        assert all(geometry.is_trustworthy(c) for c in chunks), path
        tiled += _tiles(chunks, size)
    assert seen >= len(files) * 0.97, "%d of %d not identified" % (len(files) - seen, len(files))
    assert tiled == seen, "%d of %d did not tile" % (seen - tiled, seen)
