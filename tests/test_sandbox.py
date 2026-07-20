"""Tests for the optional parse sandbox (limits profile). The fork-based tests
run for real on Linux (incl. CI) and skip elsewhere; the availability/fail-loud
tests run everywhere."""

import json
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
    p = _minimal_wav(tmp_path)
    from acidcat.core.walk import walk_file
    d_label, d_chunks, _ = walk_file(str(p))
    s_label, s_chunks, _ = sandbox.run_walk(str(p), profile="limits")
    assert d_label == s_label
    assert [c["id"] for c in d_chunks] == [c["id"] for c in s_chunks]


# ── bwrap profile ───────────────────────────────────────────────────────────

def _minimal_wav(tmp_path):
    body = (b"WAVE"
            + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", 16) + b"\x00" * 16)
    wav = b"RIFF" + struct.pack("<I", len(body)) + body
    p = tmp_path / "a.wav"
    p.write_bytes(wav)
    return p


def test_resolve_profile_fails_loud_for_unavailable():
    # an explicit profile that cannot run must raise, never silently downgrade
    if not sandbox.available("bwrap"):
        with pytest.raises(sandbox.SandboxUnavailable):
            sandbox.resolve_profile("bwrap")
    if not sandbox.available("limits"):
        with pytest.raises(sandbox.SandboxUnavailable):
            sandbox.resolve_profile("limits")


def test_bwrap_argv_isolates_input_and_runtime():
    # pure string construction -- checkable on any platform
    import os
    argv = sandbox._bwrap_argv("/usr/bin/bwrap", __file__, deep=False)
    assert "--unshare-all" in argv          # no net / ipc / pid / user ns
    assert "--new-session" in argv          # TIOCSTI hardening
    assert "--clearenv" in argv
    # the input is bind-mounted read-only at the fixed sandbox path
    i = argv.index(sandbox._SANDBOX_INPUT)
    assert argv[i - 2] == "--ro-bind"
    assert argv[i - 1] == os.path.realpath(__file__)
    # the worker runs the bound input, nothing else
    assert argv[-3:] == ["-m", "acidcat._sandbox_worker", sandbox._SANDBOX_INPUT]
    # deep appends the flag
    argv_deep = sandbox._bwrap_argv("/usr/bin/bwrap", __file__, deep=True)
    assert argv_deep[-1] == "--deep"


def test_worker_module_emits_json_result(tmp_path, capsys):
    from acidcat import _sandbox_worker
    p = _minimal_wav(tmp_path)
    _sandbox_worker.main([str(p)])
    out = capsys.readouterr().out
    res = json.loads(out)
    assert res["ok"] is True
    assert res["label"].startswith("RIFF")
    assert any(c["id"] == "fmt " for c in res["chunks"])


bwrap_only = pytest.mark.skipif(
    not sandbox.available("bwrap"),
    reason="bwrap profile needs bubblewrap + unprivileged user namespaces")


@bwrap_only
def test_bwrap_walk_matches_direct(tmp_path):
    p = _minimal_wav(tmp_path)
    from acidcat.core.walk import walk_file
    d_label, d_chunks, _ = walk_file(str(p))
    s_label, s_chunks, _ = sandbox.run_walk(str(p), profile="bwrap")
    assert d_label == s_label
    assert [c["id"] for c in d_chunks] == [c["id"] for c in s_chunks]


@bwrap_only
def test_bwrap_memory_bomb_contained(tmp_path):
    # rlimits are inherited across exec into the bwrap'd python, so a bomb file
    # would still be capped; here we cap tiny and confirm a clean failure, not a
    # host crash. (Uses a normal file with an absurdly low cap.)
    p = _minimal_wav(tmp_path)
    with pytest.raises(sandbox.SandboxError):
        sandbox.run_walk(str(p), profile="bwrap", mem_mb=8, timeout_s=10)
