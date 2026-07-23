"""Tests for headerless compressed-stream detection (core/framescan.py) -- the
third `locate` engine: MPEG audio found by frame-sync cadence, not magic."""

import random

from acidcat.core import framescan, locate


def _frame():
    # a valid MPEG-1 Layer III, 128 kbps, 44100 Hz mono frame header -> 417 bytes
    return bytes([0xFF, 0xFB, 0x90, 0xC0]) + b"\x00" * (417 - 4)


def _noise(n, seed=1):
    r = random.Random(seed)
    return bytes(r.getrandbits(8) for _ in range(n))


def test_finds_headerless_stream():
    streams = framescan.find_mpeg_streams(_frame() * 30)
    assert len(streams) == 1
    s = streams[0]
    assert s["kind"] == "stream" and s["format"] == "mp3" and s["frames"] == 30
    assert s["stream_info"]["sample_rate"] == 44100 and s["confidence"] > 0.8


def test_short_run_is_not_a_stream():
    # fewer than the minimum consecutive frames -> chance, not a stream
    assert framescan.find_mpeg_streams(_frame() * 3) == []


def test_no_false_positive_on_noise():
    assert framescan.find_mpeg_streams(_noise(300000, 2)) == []


def test_chain_breaks_on_config_change():
    # a run of MPEG-1 frames then a lone 22050 Hz (MPEG-2) frame: the chain stops
    other = bytes([0xFF, 0xF3, 0x90, 0xC0]) + b"\x00" * (417 - 4)  # MPEG-2 header
    streams = framescan.find_mpeg_streams(_frame() * 20 + other)
    assert len(streams) == 1 and streams[0]["frames"] == 20


def test_locate_finds_stream_in_strict_mode():
    # headerless MP3 buried in noise -> found even in strict (no statistical pass)
    blob = _noise(8192, 3) + _frame() * 40 + _noise(8192, 4)
    recs = locate.locate(blob, mode="strict")
    streams = [r for r in recs if r["kind"] == "stream"]
    assert len(streams) == 1
    assert streams[0]["offset"] == 8192 and streams[0]["format"] == "mp3"


def test_stream_not_double_counted_inside_container():
    # an MP3 stream that sits inside a found container is that file's payload;
    # a bare stream in noise stands alone (this checks the standalone path)
    recs = locate.locate(_noise(4096, 5) + _frame() * 40, mode="normal")
    assert sum(1 for r in recs if r["kind"] == "stream") == 1
