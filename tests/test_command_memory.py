"""Peak-memory bounds for the command layer on large input.

audit / od / probe used to slurp the whole file (``f.read()``), so a large or
crafted input cost ~2x its size in RAM before any parsing began. They now mmap
it, and the walker/analysis reads are capped or zero-copy, so peak Python-heap
allocation must stay far below the file size. tracemalloc counts only Python
allocations (mmap pages belong to the OS), which is exactly the bound that
matters: no full-size bytes copy may be materialized.

Also covers the mmap edge cases: byte-wise iteration through a memoryview
(iterating an mmap yields 1-byte bytes, not ints) and the zero-byte file
(which cannot be mapped and falls back to ``b""``).
"""

import gc
import struct
import tracemalloc
from types import SimpleNamespace

import pytest

from conftest import _make_riff_wav
from acidcat.commands import audit, od
from acidcat.commands import probe as probe_cmd
from acidcat.core.mapped import map_file

_DATA_SIZE = 48 * 1024 * 1024
# well below the 48 MB file (and the ~96 MB the old f.read() path peaked at),
# well above the walkers' capped reads
_PEAK_BOUND = 16 * 1024 * 1024


@pytest.fixture(scope="module")
def big_wav(tmp_path_factory):
    """A structurally valid ~48 MB WAV, written sparsely so the fixture is
    cheap. 8-bit PCM so integrity's per-sample scan does not slow the test."""
    p = tmp_path_factory.mktemp("mem") / "big.wav"
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 44100, 1, 8)
    riff_size = 4 + (8 + len(fmt)) + (8 + _DATA_SIZE)
    hdr = (b"RIFF" + struct.pack("<I", riff_size) + b"WAVE"
           + b"fmt " + struct.pack("<I", len(fmt)) + fmt
           + b"data" + struct.pack("<I", _DATA_SIZE))
    with open(p, "wb") as f:
        f.write(hdr)
        f.seek(len(hdr) + _DATA_SIZE - 1)
        f.write(b"\x00")
    return str(p)


def _peak(fn):
    gc.collect()
    tracemalloc.start()
    try:
        rc = fn()
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return rc, peak


def test_audit_peak_bounded_on_large_file(big_wav, capsys):
    rc, peak = _peak(lambda: audit.run(SimpleNamespace(input=big_wav, json=True)))
    capsys.readouterr()
    assert rc == 0
    assert peak < _PEAK_BOUND, f"audit peaked at {peak:,} bytes"


def test_od_peak_bounded_on_large_file(big_wav, capsys):
    rc, peak = _peak(lambda: od.run(
        SimpleNamespace(target=big_wav, color="never", width=16)))
    capsys.readouterr()
    assert rc == 0
    assert peak < _PEAK_BOUND, f"od peaked at {peak:,} bytes"


def test_probe_read_peak_bounded_on_large_file(big_wav, capsys):
    rc, peak = _peak(lambda: probe_cmd.run(SimpleNamespace(
        file=big_wav, verb="read", at="fmt.sample_rate", type="u32",
        count=1, be=False, le=False)))
    out = capsys.readouterr().out
    assert rc == 0 and "44100" in out
    assert peak < _PEAK_BOUND, f"probe read peaked at {peak:,} bytes"


def test_probe_scan_peak_bounded_on_large_file(big_wav, capsys):
    rc, peak = _peak(lambda: probe_cmd.run(SimpleNamespace(
        file=big_wav, verb="scan", value="44100", type="u32")))
    capsys.readouterr()
    assert rc == 0
    assert peak < _PEAK_BOUND, f"probe scan peaked at {peak:,} bytes"


# -- mmap correctness edges -------------------------------------------------

def test_probe_strings_iterates_mapped_file(tmp_path, capsys):
    # iterating an mmap yields 1-byte bytes; the command wraps it in a
    # memoryview so pr.strings sees ints -- this would TypeError otherwise
    p = tmp_path / "s.wav"
    p.write_bytes(_make_riff_wav())
    rc = probe_cmd.run(SimpleNamespace(file=str(p), verb="strings", min=4))
    out = capsys.readouterr().out
    assert rc == 0
    assert "WAVEfmt" in out


def test_probe_entropy_and_map_on_mapped_file(tmp_path, capsys):
    p = tmp_path / "s.wav"
    p.write_bytes(_make_riff_wav(num_samples=256))
    assert probe_cmd.run(SimpleNamespace(file=str(p), verb="entropy", width=32)) == 0
    assert probe_cmd.run(SimpleNamespace(
        file=str(p), verb="map", order=3, no_color=True)) == 0
    capsys.readouterr()


def test_map_file_empty_falls_back_to_bytes(tmp_path):
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    data, close = map_file(str(p))
    try:
        assert data == b"" and len(data) == 0
    finally:
        close()


def test_audit_empty_file(tmp_path, capsys):
    p = tmp_path / "empty.wav"
    p.write_bytes(b"")
    assert audit.run(SimpleNamespace(input=str(p), json=False)) == 0
    assert "unknown" in capsys.readouterr().out


def test_probe_empty_file(tmp_path, capsys):
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    rc = probe_cmd.run(SimpleNamespace(
        file=str(p), verb="read", at="0x0", type="u32",
        count=1, be=False, le=False))
    capsys.readouterr()
    assert rc == 1                     # nothing to read, but no crash
    assert probe_cmd.run(SimpleNamespace(file=str(p), verb="strings", min=4)) == 0
    capsys.readouterr()
