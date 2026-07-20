"""Tests for the optional parse sandbox (limits profile). The fork-based tests
run for real on Linux (incl. CI) and skip elsewhere; the availability/fail-loud
tests run everywhere."""

import struct

import pytest

from acidcat.core import sandbox

linux_only = pytest.mark.skipif(
    not sandbox.available("limits"),
    reason="sandbox 'limits' profile needs Linux (fork + setrlimit)")


# ── availability / fail-loud (all platforms) ────────────────────────────────

def test_bwrap_profile_not_implemented():
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.run_walk("x.wav", profile="bwrap")


def test_unavailable_is_raised_off_linux():
    if sandbox.available("limits"):
        pytest.skip("this host can run the limits profile")
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.run_walk("x.wav")


# ── the real worker (Linux) ─────────────────────────────────────────────────

@linux_only
def test_normal_target_round_trips():
    def target():
        return ("LABEL", [{"id": "FMT", "n": 1}], ["a warning"])
    label, chunks, warns = sandbox.run_limited(target, mem_mb=512, timeout_s=10)
    assert label == "LABEL"
    assert chunks == [{"id": "FMT", "n": 1}]
    assert warns == ["a warning"]


@linux_only
def test_memory_bomb_is_contained():
    def bomb():
        _ = bytearray(800 * 1024 * 1024)          # 800 MB, over the 512 MB cap
        return ("x", [], [])
    with pytest.raises(sandbox.SandboxError):
        sandbox.run_limited(bomb, mem_mb=512, timeout_s=10)


@linux_only
def test_cpu_spin_is_killed():
    def spin():
        while True:
            pass
    with pytest.raises(sandbox.SandboxError):
        sandbox.run_limited(spin, mem_mb=512, timeout_s=1)


@linux_only
def test_worker_exception_becomes_sandbox_error():
    def boom():
        raise ValueError("kaboom")
    with pytest.raises(sandbox.SandboxError) as ei:
        sandbox.run_limited(boom, mem_mb=256, timeout_s=10)
    assert "kaboom" in str(ei.value)


@linux_only
def test_sandboxed_walk_matches_direct(tmp_path):
    from acidcat.core.walk import walk_file
    body = (b"WAVE"
            + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", 16) + b"\x00" * 16)
    wav = b"RIFF" + struct.pack("<I", len(body)) + body
    p = tmp_path / "a.wav"
    p.write_bytes(wav)
    d_label, d_chunks, _ = walk_file(str(p))
    s_label, s_chunks, _ = sandbox.run_walk(str(p))
    assert d_label == s_label
    assert [c["id"] for c in d_chunks] == [c["id"] for c in s_chunks]
