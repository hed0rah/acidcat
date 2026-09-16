"""The OKI MSM6258 decoder behind `extract` on a PDX bank.

An oracle exists for everything but one rounding choice: ffmpeg's
adpcm_ima_oki decodes WAVE format 0x0010, and with its own rounding our
decoder is bit-exact against it, which pins the nibble order, the clamp,
the index table and the output scale. The rounding itself is the OKI
datasheet's (each term of step/8 + step/4 + step/2 + step truncated on its
own), and the corpus decides that one: with it, real samples end near
silence; with ffmpeg's ((2d+1)*step >> 3) they end hundreds of units off,
because the encoders of the era modelled the chip.
"""

import array
import glob
import os
import shutil
import struct
import subprocess

import pytest

from acidcat.core.codecs import adpcm
from acidcat.core.extract import samples as smod
from acidcat.core.formats import pdx as pdxmod

import seeds


def _pcm(b):
    return array.array("h", b)


# ── the arithmetic, by hand ─────────────────────────────────────────

def test_a_zero_nibble_from_rest_is_the_smallest_step_over_eight():
    # step[0] = 16, diff = 16 >> 3 = 2, twice; 12-bit values scaled by 16
    assert list(_pcm(adpcm.decode_oki(b"\x00"))) == [2 << 4, 4 << 4]


def test_the_low_nibble_of_each_byte_is_decoded_first():
    # 0x70 is nibbles (0, 7): a tiny step then the biggest positive one.
    # 0x07 is the reverse. The X68000 order is low first.
    low, high = _pcm(adpcm.decode_oki(b"\x70")), _pcm(adpcm.decode_oki(b"\x07"))
    assert low[0] == 2 << 4 and high[0] > low[0]
    assert list(adpcm.decode_oki(b"\x07", high_first=True)) == list(adpcm.decode_oki(b"\x70"))


def test_the_predictor_is_twelve_bits():
    up = _pcm(adpcm.decode_oki(b"\x77" * 64))         # climb as fast as possible
    assert max(up) == 2047 << 4
    down = _pcm(adpcm.decode_oki(b"\xff" * 64))
    assert min(down) == -2048 << 4


def test_the_step_index_stays_inside_the_table():
    # 0x77 pushes the index up by 8 each nibble; 0x00 pulls it down by 1.
    # Neither may leave [0, 48]; a bad clamp is an IndexError here.
    adpcm.decode_oki(b"\x77" * 32 + b"\x00" * 200)


# ── extract ─────────────────────────────────────────────────────────

def test_extract_yields_one_wav_per_live_slot_with_twice_the_frames():
    blob = seeds.build("pdx")
    out = [r for r in smod._pdx_samples(blob) if r["wav"]]
    t = pdxmod.parse_table(blob, len(blob))
    live = [(n, ln) for n, (off, ln) in enumerate(t["slots"]) if ln]
    assert [r["name"] for r in out] == ["slot%03d" % n for n, _ in live]
    for r, (_n, ln) in zip(out, live):
        assert struct.unpack_from("<I", r["wav"], 40)[0] == ln * 2 * 2   # data size
        assert struct.unpack_from("<I", r["wav"], 24)[0] == smod._PDX_RATE


def test_an_aliased_slot_is_reported_not_extracted_twice():
    from test_pdx import _pdx
    blob = _pdx(samples=(64, 32), alias=[(5, 0)])
    out = list(smod._pdx_samples(blob))
    assert [r["name"] for r in out] == ["slot000", "slot001", "slot005"]
    assert out[2]["wav"] is None and "same bytes as slot 0" in out[2]["note"]


def test_pdx_is_extractable():
    assert "pdx" in smod.EXTRACTABLE


# ── the oracle ──────────────────────────────────────────────────────

def _ffmpeg():
    return os.environ.get("ACIDCAT_FFMPEG") or shutil.which("ffmpeg")


def _ffmpeg_oki(adpcm_bytes):
    """ffmpeg reading the bytes as WAVE format 0x0010, high nibble first."""
    fmt = struct.pack("<HHIIHH", 0x0010, 1, 15625, 15625 // 2, 1, 4)
    wav = (b"RIFF" + struct.pack("<I", 36 + len(adpcm_bytes)) + b"WAVEfmt "
           + struct.pack("<I", 16) + fmt + b"data"
           + struct.pack("<I", len(adpcm_bytes)) + adpcm_bytes)
    r = subprocess.run([_ffmpeg(), "-v", "error", "-f", "wav", "-i", "-",
                        "-f", "s16le", "-"], input=wav, capture_output=True)
    return r.stdout


def _swap_nibbles(b):
    return bytes(((x & 0xF) << 4) | (x >> 4) for x in b)


def _decode_with_ffmpeg_rounding(data):
    """Our decoder with the one thing changed that ffmpeg does differently."""
    out = bytearray()
    pred = idx = 0
    for byte in data:
        for nib in (byte & 0x0F, byte >> 4):
            step = adpcm._OKI_STEP[idx]
            diff = ((2 * (nib & 7) + 1) * step) >> 3
            pred += -diff if nib & 8 else diff
            pred = max(-2048, min(2047, pred))
            idx = max(0, min(48, idx + adpcm._IMA_INDEX[nib]))
            out += struct.pack("<h", pred << 4)
    return bytes(out)


def _corpus_samples(limit):
    root = os.environ["ACIDCAT_MDX_CORPUS"]
    files = sorted(glob.glob(os.path.join(root, "**", "*.PDX"), recursive=True))
    n = 0
    for path in files:
        with open(path, "rb") as fh:
            raw = fh.read()
        t = pdxmod.parse_table(raw, len(raw))
        if not t["ok"]:
            continue
        for off, ln in t["slots"]:
            if ln >= 256 and off + ln <= len(raw):
                yield raw[off:off + ln]
                n += 1
                if n >= limit:
                    return


@pytest.mark.skipif(not (os.environ.get("ACIDCAT_MDX_CORPUS") and _ffmpeg()),
                    reason="needs ACIDCAT_MDX_CORPUS and ffmpeg on PATH or ACIDCAT_FFMPEG")
def test_everything_but_the_rounding_is_bit_exact_against_ffmpeg():
    """200 real samples, 1.2 MB of nibbles: nibble order, clamp, index
    table and scale agree with ffmpeg to the sample."""
    n = 0
    for a in _corpus_samples(200):
        assert _ffmpeg_oki(_swap_nibbles(a)) == _decode_with_ffmpeg_rounding(a)
        n += 1
    assert n == 200


@pytest.mark.skipif(not os.environ.get("ACIDCAT_MDX_CORPUS"),
                    reason="set ACIDCAT_MDX_CORPUS to a dir with real .pdx banks")
def test_the_datasheet_rounding_brings_real_samples_back_to_silence():
    """A recorded hit ends in silence, and an encoder that modelled the
    chip leaves the decoder there. Measured on the first bank's slots 3-10:
    datasheet rounding ends within 112 of zero, ffmpeg's within 1,888."""
    ours = theirs = 0
    n = 0
    for a in _corpus_samples(200):
        u, v = _pcm(adpcm.decode_oki(a)), _pcm(_decode_with_ffmpeg_rounding(a))
        ours += abs(sum(u[-200:]) / 200)
        theirs += abs(sum(v[-200:]) / 200)
        n += 1
    assert ours < theirs, "datasheet rounding drifted more: %.0f vs %.0f" % (ours, theirs)
    assert ours / n < theirs / n / 2, "the two roundings are closer than the corpus said"
