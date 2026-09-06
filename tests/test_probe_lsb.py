"""`acidcat probe lsb` -- the sample-LSB entropy view.

Carriers are built inline with known LSB behaviour, so the assertions are about
what the reading SAYS, not about a specific corpus. The three shapes are the
ones the stego gallery demonstrates: a flat floor (nothing hidden), a uniformly
random floor (encrypted payload, or ordinary dithered audio, indistinguishable
on purpose), and a band of random LSBs over a quiet floor (a raw payload).
"""

import json
import random
import struct
from types import SimpleNamespace

from acidcat.commands import probe as cmd


def _args(path, **kw):
    base = dict(files=[str(path)], verb="lsb", type="u32", count=1, be=False,
                le=False, min=4, length=256, width=64, order=4, no_color=True,
                output_format="table")
    base.update(kw)
    return SimpleNamespace(**base)


def _wav(path, samples):
    """A mono 16-bit PCM WAV from signed int samples."""
    pcm = b"".join(struct.pack("<h", s) for s in samples)
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
    data = b"data" + struct.pack("<I", len(pcm)) + pcm
    body = b"WAVE" + fmt + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def test_a_flat_lsb_floor_reads_as_no_signal(tmp_path, capsys):
    p = _wav(tmp_path / "flat.wav", [1000] * 20000)     # every LSB is 0
    assert cmd.run(_args(p)) == 0
    out = capsys.readouterr().out
    assert "no LSB-entropy signature" in out
    assert "0/64" in out


def test_a_uniformly_random_floor_reads_as_uniform_high(tmp_path, capsys):
    rng = random.Random(0)
    p = _wav(tmp_path / "rand.wav", [1000 + rng.getrandbits(1) for _ in range(20000)])
    assert cmd.run(_args(p)) == 0
    out = capsys.readouterr().out
    assert "uniformly high" in out
    assert "heuristic, not proof" in out


def test_a_band_over_a_quiet_floor_reads_as_a_raw_payload(tmp_path, capsys):
    rng = random.Random(1)
    n = 20000
    half = [1000 + rng.getrandbits(1) for _ in range(n // 2)]   # random LSBs
    rest = [1000] * (n - n // 2)                                # then flat
    p = _wav(tmp_path / "band.wav", half + rest)
    assert cmd.run(_args(p)) == 0
    out = capsys.readouterr().out
    assert "band of high-entropy LSBs" in out


def test_json_carries_the_numbers(tmp_path, capsys):
    rng = random.Random(2)
    p = _wav(tmp_path / "j.wav", [1000 + rng.getrandbits(1) for _ in range(20000)])
    assert cmd.run(_args(p, output_format="json")) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["verb"] == "lsb"
    assert d["uniform_high"] is True
    assert 0.0 <= d["mean"] <= 1.0 and len(d["windows"]) == 64


def test_a_non_wav_is_refused(tmp_path, capsys):
    p = tmp_path / "x.bin"
    p.write_bytes(b"not a wav" * 100)
    assert cmd.run(_args(p)) == 2
    assert "not a PCM WAV" in capsys.readouterr().err
