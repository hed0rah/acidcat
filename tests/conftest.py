"""shared fixtures for acidcat tests."""

import struct
import tempfile
import os
import pytest

# in production walk_file degrades a walker bug to a warning; in the suite a
# walker bug must stay a loud traceback (see core/walk/__init__.py)
os.environ.setdefault("ACIDCAT_WALKER_RAISE", "1")


@pytest.fixture(autouse=True)
def _isolate_acidcat_env(monkeypatch, tmp_path_factory):
    """Point every acidcat path at a throwaway home. Applied to every test.

    Deleting the env vars was not enough, and was in fact the bug: with
    ACIDCAT_REGISTRY unset, `paths.acidcat_home()` falls back to
    `os.path.expanduser("~")`, so the suite wrote per-library databases into
    the user's REAL `~/.acidcat/libraries/`. Two audit runs plus the test suite
    left 1,786 orphaned .db files there, 126 MB, against 32 genuinely
    registered libraries.

    Setting a fake HOME is what actually contains it: expanduser reads
    USERPROFILE on Windows and HOME on POSIX, and both have to be overridden
    because acidcat is developed on the former and tested on the latter.
    """
    home = tmp_path_factory.mktemp("acidcat_home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("ACIDCAT_REGISTRY", str(home / ".acidcat" / "registry.db"))
    monkeypatch.delenv("ACIDCAT_DB", raising=False)


def _make_riff_wav(sample_rate=44100, channels=1, bits=16, num_samples=4):
    """Build a minimal valid PCM WAV in memory."""
    block_align = channels * bits // 8
    byte_rate = sample_rate * block_align
    audio_data = b"\x00" * (num_samples * block_align)

    fmt = struct.pack(
        "<HHIIHH",
        1,           # PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
    )
    # fmt chunk: id + size + data (16 bytes)
    fmt_chunk = b"fmt " + struct.pack("<I", 16) + fmt
    # data chunk
    data_chunk = b"data" + struct.pack("<I", len(audio_data)) + audio_data
    # RIFF header: size = 4 (WAVE) + len(fmt_chunk) + len(data_chunk)
    riff_body = b"WAVE" + fmt_chunk + data_chunk
    return b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body


@pytest.fixture
def minimal_wav(tmp_path):
    """Write a minimal valid WAV to a temp file."""
    p = tmp_path / "minimal.wav"
    p.write_bytes(_make_riff_wav())
    return str(p)


@pytest.fixture
def silent_wav(tmp_path):
    """Slightly longer WAV: 4410 samples (0.1 s at 44100 Hz)."""
    p = tmp_path / "silent.wav"
    p.write_bytes(_make_riff_wav(num_samples=4410))
    return str(p)


@pytest.fixture
def not_riff(tmp_path):
    """A file with no RIFF magic bytes."""
    p = tmp_path / "not_riff.wav"
    p.write_bytes(b"\x00" * 64)
    return str(p)


@pytest.fixture
def empty_file(tmp_path):
    """A zero-byte file with a .wav extension."""
    p = tmp_path / "empty.wav"
    p.write_bytes(b"")
    return str(p)


@pytest.fixture
def truncated_riff(tmp_path):
    """A file that starts with RIFF but is truncated."""
    p = tmp_path / "truncated.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", 1000) + b"WAVE" + b"fmt ")  # cuts off
    return str(p)


@pytest.fixture
def bad_mp3(tmp_path):
    """An MP3-extension file that contains garbage (triggers mutagen error)."""
    p = tmp_path / "bad.mp3"
    p.write_bytes(b"\x00" * 72)
    return str(p)


# real test files (skip if absent)
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "test_formats")


def real_file(name):
    path = os.path.join(FIXTURES_DIR, name)
    return pytest.mark.skipif(
        not os.path.isfile(path),
        reason=f"test fixture {name} not present",
    )(path)


SAMPLE_WAV = os.path.join(
    os.path.dirname(__file__), "..", "data", "samples", "Drum_Loop.wav"
)

# data/test_formats/ is gitignored and 16 MB, so a fresh clone used to skip 64
# tests -- the whole TUI suite and every tagging round-trip. data/fixtures/ is
# a committed 34 KB stand-in: real encoder output, a third of a second each.
# Prefer the big corpus when it is present so local runs still exercise it.
SMALL_FIXTURES = os.path.join(os.path.dirname(__file__), "..", "data", "fixtures")


def corpus_or_fixture(name, small):
    """Path to the corpus file `name`, or the committed stand-in `small`."""
    big = os.path.join(FIXTURES_DIR, name)
    if os.path.isfile(big):
        return big
    return os.path.join(SMALL_FIXTURES, small)


# a plain WAV for the tests that just need real audio to point at
CORPUS_WAV = corpus_or_fixture(os.path.join("generated", "src.wav"), "tone.wav")

# The rest of the corpus, each with a committed stand-in, because a test that
# names a gitignored path does not fail on a runner -- it SKIPS, and a skip is a
# green run that checked nothing. 85 of CI's 90 skips were this one cause, so
# the suite that gates a release was 85 tests smaller than the suite anybody
# ran locally, and the gap was invisible from either side.
#
# The name is still the big corpus file, so a machine that has it keeps
# exercising the real specimen; only the fallback is new.
CORPUS_WAV_GS = corpus_or_fixture("gs-16b-2c-44100hz.wav", "tone.wav")
CORPUS_MP3 = corpus_or_fixture(os.path.join("generated", "mp3_44100.mp3"),
                               "tone.mp3")
CORPUS_MP3_GS = corpus_or_fixture("gs-16b-2c-44100hz.mp3", "tone.mp3")
CORPUS_OGG = corpus_or_fixture("gs-16b-2c-44100hz.ogg", "tone.ogg")
CORPUS_FLAC = corpus_or_fixture("gs-16b-2c-44100hz.flac", "tone.flac")
CORPUS_M4A = corpus_or_fixture("gs-16b-2c-44100hz.m4a", "tone.m4a")

# Structurally distinct rather than differently encoded: 24-bit width,
# WAVE_FORMAT_EXTENSIBLE with a channel mask, big-endian AIFF, and bit depth
# living in FLAC's STREAMINFO bitfield. Each is the only committed file
# exercising its header path.
# A real 504 KB Nintendo stream when the corpus is present, and the synthetic
# one the BRSTM tests already build when it is not. Without the stand-in the
# real-specimen test named a gitignored path and therefore ran nowhere.
CORPUS_BRSTM = corpus_or_fixture(os.path.join("reference", "brstm.brstm"),
                                 "brstm.brstm")
CORPUS_WAV24 = corpus_or_fixture("wav24.wav", "tone24.wav")
CORPUS_WAV51 = corpus_or_fixture("wav51.wav", "tone51.wav")
CORPUS_AIFF = corpus_or_fixture(os.path.join("generated", "aiff_pcm.aiff"),
                                "tone.aiff")
CORPUS_FLAC24 = corpus_or_fixture(os.path.join("generated", "flac24.flac"),
                                  "tone24.flac")


# For call sites that name a specimen rather than holding a constant -- a
# parametrized list, say, where the name is also the output filename.
_STANDIN = {
    "gs-16b-2c-44100hz.wav": "tone.wav",
    "gs-16b-2c-44100hz.mp3": "tone.mp3",
    "gs-16b-2c-44100hz.ogg": "tone.ogg",
    "gs-16b-2c-44100hz.flac": "tone.flac",
    "gs-16b-2c-44100hz.m4a": "tone.m4a",
    "gs-16b-2c-44100hz.opus": "tone.opus",
    "gs-16b-2c-44100hz.aiff": "tone.aiff",
    "wav24.wav": "tone24.wav",
    "wav51.wav": "tone51.wav",
    "generated/src.wav": "tone.wav",
    "generated/mp3_44100.mp3": "tone.mp3",
    "generated/aiff_pcm.aiff": "tone.aiff",
    "generated/flac24.flac": "tone24.flac",
}


def corpus_glob(pattern):
    """Every file matching `pattern` under the corpus AND the committed
    fixtures, by absolute path.

    For parametrize, where a relative glob is worse than a missing file: it
    collects zero cases, and a module that reports no tests looks exactly like a
    module whose tests passed.
    """
    import glob as _glob
    found = []
    for root in (FIXTURES_DIR, SMALL_FIXTURES):
        found += _glob.glob(os.path.join(root, "**", pattern), recursive=True)
    return sorted(found)


def corpus_path(name):
    """Resolve a corpus specimen by name, or its committed stand-in.

    Returns None when neither exists, so a caller can still skip; that is a
    real answer for the specimens nothing can synthesize (.nksf, .nmsv, a
    187 MB game archive) rather than a corpus that merely happens to be absent.
    """
    big = os.path.join(FIXTURES_DIR, *name.split("/"))
    if os.path.isfile(big):
        return big
    small = _STANDIN.get(name)
    if small:
        p = os.path.join(SMALL_FIXTURES, small)
        if os.path.isfile(p):
            return p
    return None


def have_tool(name):
    """Is an external tool actually runnable?

    subprocess.run RAISES FileNotFoundError when the binary is absent -- it does
    not return a non-zero code. A guard written as `run(...); if returncode:
    skip()` therefore never fires, and the test dies with a traceback instead of
    skipping. That is exactly how CI went red on every platform at once: the
    local machine has ffmpeg, the runners do not.

    One helper, so the mistake has one place to live.
    """
    import shutil
    import subprocess
    if shutil.which(name) is None:
        return False
    try:
        subprocess.run([name, "-version"], capture_output=True, timeout=30)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def requires_tool(name):
    """Skip the calling test unless `name` is runnable."""
    import pytest as _pytest
    if not have_tool(name):
        _pytest.skip(f"{name} not available")


# ── waiting on a Textual app without guessing how long it takes ──────

async def until(pilot, cond, tries=100, step=0.1):
    """Wait for a background answer rather than assuming a duration.

    A flat `pause(0.5)` is a timing assumption reported as a result: it passed
    on three CI platforms and failed on the two slowest, which is not a
    property of the code under test. Polling costs nothing when the answer is
    already in, and the failure it produces names the condition that never
    became true rather than a duration that turned out to be short.

    One definition, because the mistake was made twice in separate files.
    """
    for _ in range(tries):
        if cond():
            return True
        await pilot.pause(step)
    return cond()


async def settled(pilot, measure, tries=90, step=0.1, stable=3):
    """Wait until a measurement stops changing, and return it.

    Different from `until`, which waits for a known target. Here there is no
    target: the question is whether the app has finished, and the only evidence
    available is that it has stopped moving. `stable` consecutive identical
    readings, because one identical pair happens constantly in the gaps between
    a worker finishing and its results reaching the tree.

    Written for the tree-settles test, which snapshotted a node count, expanded
    again, and asserted the count had not moved. On a loaded runner the
    snapshot landed mid-growth -- it read 4 nodes of an eventual 12 -- and the
    test then reported the tree as "still growing" when what was still growing
    was the first measurement.
    """
    last = object()
    runs = 0
    for _ in range(tries):
        now = measure()
        if now == last:
            runs += 1
            if runs >= stable:
                return now
        else:
            runs, last = 0, now
        await pilot.pause(step)
    return measure()


async def measured(pilot, measure, ok, tries=90, step=0.1):
    """The measurement, once it satisfies `ok` -- or the last one taken.

    The third member of the family, and the one the other two could not
    express. `until` waits for a condition but hands back only a boolean;
    `settled` hands back a measurement but waits for it to stop MOVING, which
    is a proxy. The proxy has a hole: a Textual worker that has been dispatched
    and has not yet delivered leaves the thing being measured perfectly still,
    so `settled` returns a half-built answer that merely looks finished.

    Waiting on the answer itself closes that. A run that genuinely never
    produces it still fails, with the same assertion and the same values, after
    spending the timeout rather than guessing early.
    """
    value = measure()
    for _ in range(tries):
        if ok(value):
            return value
        await pilot.pause(step)
        value = measure()
    return value


async def quiet(pilot, app, tries=150, step=0.1):
    """Wait until the app has no work outstanding, then let its results land.

    The condition itself, at last. `settled` waits for a measurement to stop
    moving and `measured` waits for it to satisfy a predicate, but both are
    asking the tree what the WORKERS are doing, and the tree only knows once
    the answer has already arrived. Textual's WorkerManager knows directly:
    it is iterable and sized, so "is anything still running" is one question
    with an exact answer.

    That distinction is not academic. The tree-settles test asks whether
    expanding an already-complete tree adds nodes, and it has no condition to
    wait for other than completion -- so a stability window is the only tool
    `settled` could offer it, and a window short enough to keep the suite quick
    is a window a loaded runner will outrun. It read 4 nodes of an eventual 12
    on Windows and reported the tree as still growing.

    Two pauses bracket the wait on purpose. The first lets `expand()` finish
    posting its messages so the workers it spawns actually exist before they
    are counted; without it an immediate check sees an empty manager and
    returns before the work has started. The second lets the finished workers'
    results reach the tree, because a worker leaving the manager and its output
    appearing as nodes are separate events.
    """
    await pilot.pause()
    for _ in range(tries):
        if not len(app.workers):
            break
        await pilot.pause(step)
    await pilot.pause()
    return not len(app.workers)


async def press_until(pilot, key, cond, attempts=4, tries=40, step=0.1):
    """Press `key` until `cond()` holds, re-pressing if it does not.

    A keystroke is a request, and under load Textual's pilot can deliver one
    into an app that is not yet in a state to act on it -- the key is consumed
    and nothing happens. Waiting longer does not help, because there is nothing
    in flight to wait for; the press has to happen again.

    That is why this exists rather than a longer `until`. The failing case had
    already been given a confirmed-focus precondition AND a ten-second wait,
    and still reported "space did not reach the action" on a loaded runner: not
    a slow action, a lost keystroke.

    Bounded on purpose. If four presses spread over sixteen seconds do not move
    the state, the feature is broken and this must say so rather than loop.
    """
    for _ in range(attempts):
        await pilot.press(key)
        if await until(pilot, cond, tries=tries, step=step):
            return True
    return cond()


# ── running in parallel ──────────────────────────────────────────────
#
# The suite is half an hour serial and two minutes under pytest-xdist, but
# only if the Textual pilots do not compete for the CPU: twenty-four of them
# at once time out, and a few hang. So every TUI test is pinned to one
# worker group, and so are the two memory-profile tests, which measure peak
# RSS and cannot share a process with anything else. `--dist loadgroup` is
# what honours the groups; pyproject sets it.

def pytest_collection_modifyitems(config, items):
    for item in items:
        name = item.fspath.basename
        if name.startswith("test_tui") or name.startswith("test_viz"):
            item.add_marker(pytest.mark.xdist_group("tui"))
        elif name.startswith("test_audioscan") and "memory" in item.name:
            item.add_marker(pytest.mark.xdist_group("memory"))
        elif name == "test_chunk_geometry.py":
            # the hunt-corpus sweep runs once per process and its "did it
            # reach any file" check reads that shared count; split across
            # workers, the check ran on a process that had swept nothing
            item.add_marker(pytest.mark.xdist_group("geometry"))
