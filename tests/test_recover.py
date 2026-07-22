"""Tests for the forensic recovery orchestrator (core/recover.py) -- phases 2/3.

Covers the backtrack-to-container anchor, the three forensics levels, and the
governing rule that a missing header downgrades (never discards) a hit."""

import io
import math
import random
import struct
import wave

from acidcat.core import recover


def _noise(n, seed=1):
    r = random.Random(seed)
    return bytes(r.getrandbits(8) for _ in range(n))


def _tone_i16(n, period=40, amp=8000):
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * i / period)))
                    for i in range(n))


def _wav(n=6000, rate=11025):
    """A real 16-bit mono WAV (RIFF/WAVE with fmt + data), tone payload."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(_tone_i16(n))
    return buf.getvalue()


def _tone_u8(n, period=40, amp=60):
    return bytes(int(amp * math.sin(2 * math.pi * i / period)) & 0xFF for i in range(n))


# ---- backtrack -------------------------------------------------------------

def test_backtrack_finds_riff_before_region():
    wav = _wav()
    blob = _noise(4096, 2) + wav + _noise(2048, 3)
    # a region somewhere inside the WAV payload
    start = 4096 + 100
    bt = recover.backtrack_header(blob, start)
    assert bt["found"]
    assert bt["format"] == "wav"
    assert bt["container_start"] == 4096              # exactly at the RIFF magic


def test_backtrack_rejects_stray_magic_in_noise():
    # the ASCII "RIFF" can occur in random data, but without a valid WAVE at +8
    # sniff_bytes rejects it, so backtrack reports not-found
    blob = bytearray(_noise(8192, 4))
    blob[2000:2004] = b"RIFF"                          # stray magic, no WAVE tag
    bt = recover.backtrack_header(bytes(blob), 4000)
    assert bt["found"] is False


def test_backtrack_none_before_region():
    assert recover.backtrack_header(_noise(4096, 5), 4000)["found"] is False


# ---- classify + extent ------------------------------------------------------

def test_container_extent_from_riff_size():
    wav = _wav()
    blob = _noise(1024, 6) + wav + _noise(1024, 7)
    recs = recover.recover(blob, mode="strict")
    assert len(recs) == 1
    rec = recs[0]
    assert rec["kind"] == "container" and rec["format"] == "wav"
    assert rec["offset"] == 1024
    # extent comes from the RIFF declared size, so the whole file is carved
    assert rec["end"] == 1024 + len(wav)
    assert rec["inspectable"] is True


# ---- forensics levels -------------------------------------------------------

def test_strict_drops_headerless_blobs():
    blob = _noise(4096, 8) + _tone_u8(6000) + _noise(4096, 9)   # no container
    assert recover.recover(blob, mode="strict") == []


def test_aggressive_keeps_headerless_blob():
    blob = _noise(4096, 10) + _tone_u8(6000) + _noise(4096, 11)
    recs = recover.recover(blob, mode="aggressive")
    assert any(r["kind"] == "blob" for r in recs)
    blob_rec = next(r for r in recs if r["kind"] == "blob")
    assert blob_rec["offset"] >= 4096 - audioscan_window()
    assert blob_rec["inspectable"] is False


def test_normal_keeps_container_and_confident_blob():
    wav = _wav()
    blob = _noise(2048, 12) + wav + _noise(2048, 13) + _tone_u8(6000) + _noise(2048, 14)
    recs = recover.recover(blob, mode="normal")
    kinds = {r["kind"] for r in recs}
    assert "container" in kinds                       # the WAV is recovered
    # the strong headerless tone survives 'normal' too (high confidence)
    assert any(r["kind"] == "blob" for r in recs)


def test_missing_header_downgrades_not_discards():
    # same tone, once with a WAV wrapper and once bare: aggressive recovers both,
    # the bare one as a blob (downgrade), proving backtrack-miss != drop
    bare = _noise(1024, 15) + _tone_u8(6000) + _noise(1024, 16)
    recs = recover.recover(bare, mode="aggressive")
    assert recs and all(r["kind"] == "blob" for r in recs)


def test_invalid_mode_raises():
    try:
        recover.recover(b"", mode="paranoid")
    except ValueError as e:
        assert "mode" in str(e)
    else:
        assert False, "expected ValueError"


def audioscan_window():
    from acidcat.core import audioscan
    return audioscan.DEFAULT_WINDOW
