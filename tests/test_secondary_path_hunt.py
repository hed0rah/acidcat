"""Opt-in real-corpus hunts for the secondary paths: extract and repair.

The walk path is fuzzed, and the geometry invariants already sweep a real corpus
(`test_chunk_geometry.py`). Extract and repair are the seams the 2026-09-03 audit
named as never fuzzed on real input, only on synthetic fixtures. A synthetic
sample is always well-formed; only real files carry the truncation, the odd
loop point, the count that overruns, that a decoder or a repairer trips on.

Set `ACIDCAT_HUNT_CORPUS` (the same variable the geometry hunt uses, so one
setting sweeps all three seams) to a directory of real files. These point
`iter_samples` and the repair engine at every applicable one and assert the
contract both seams owe: malformed input degrades to a graceful refusal or a
report, never an unhandled exception. Nothing is written; the files are read
only. Skipped when the variable is unset, and skipped with a stated reason when
the corpus holds no file of an applicable format, which is honest rather than a
green run that checked nothing.

Measured once by hand before this landed: 885 extractable files across 11
formats and 509 repairable across 5, zero crashes. This makes that rerunnable.
"""

import os
import time
import traceback

import pytest

from acidcat.core.infra.sniff import sniff

_HUNT = os.environ.get("ACIDCAT_HUNT_CORPUS")
_PER = int(os.environ.get("ACIDCAT_HUNT_PER_FORMAT") or 60)
_SCAN_CAP = 400_000
_SECONDS = 600
_MAX = 128 * 1024 * 1024

pytestmark = pytest.mark.skipif(
    not _HUNT, reason="set ACIDCAT_HUNT_CORPUS to a directory of real files")


def _sweep(applicable, run):
    """Walk the corpus; for each file whose sniff id is in `applicable`, call
    `run(path, fid)`. An exception `run` lets escape is recorded as a crash.
    Returns (tried_by_format, crashes)."""
    tried, crashes, scanned, t0 = {}, [], 0, time.time()
    for dirpath, dirnames, filenames in os.walk(_HUNT):
        dirnames.sort()
        for name in sorted(filenames):
            if scanned > _SCAN_CAP or time.time() - t0 > _SECONDS:
                return tried, crashes
            path = os.path.join(dirpath, name)
            try:
                if not 12 <= os.path.getsize(path) <= _MAX:
                    continue
                fid = sniff(path)
            except OSError:
                continue
            if fid not in applicable or tried.get(fid, 0) >= _PER:
                continue
            tried[fid] = tried.get(fid, 0) + 1
            scanned += 1
            try:
                run(path, fid)
            except Exception as exc:                     # noqa: BLE001 -- recorded
                crashes.append((fid, os.path.relpath(path, _HUNT),
                                traceback.format_exc().strip().splitlines()[-1]))
    return tried, crashes


def _report(crashes):
    return "\n".join(f"  [{fid}] {rel}\n      {last}"
                     for fid, rel, last in crashes[:10])


def test_no_real_file_crashes_the_extract_path():
    """`iter_samples` must raise `SampleError` (or an OSError) on bad input, not
    an unhandled exception. Anything else escaping is a crash on the extract
    seam."""
    from acidcat.core.extract.samples import (EXTRACTABLE, SampleError,
                                              iter_samples)

    def run(path, fid):
        try:
            for _sample in iter_samples(path, fid):
                pass
        except (SampleError, OSError):
            pass                                         # the graceful contract

    tried, crashes = _sweep(EXTRACTABLE, run)
    if not tried:
        pytest.skip("no extractable-format files under ACIDCAT_HUNT_CORPUS")
    assert not crashes, (
        f"{len(crashes)} real file(s) crashed the extract path "
        f"(examined {sum(tried.values())} across {len(tried)} formats):\n"
        + _report(crashes))


_REPAIRABLE = {"wav", "rf64", "aiff", "aifc", "sf2", "8svx", "smus", "rmid",
               "akp", "e4b", "e5b", "flac", "mp4"}


def test_no_real_file_crashes_the_repair_path():
    """`constraints.analyze` and `repair` must return a report (or None) and
    degrade internally, never raise on a real file. Output is discarded; the
    file is never modified."""
    from acidcat.core.write import constraints

    def run(path, fid):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            return
        constraints.analyze(data)
        constraints.repair(data)

    tried, crashes = _sweep(_REPAIRABLE, run)
    if not tried:
        pytest.skip("no repairable-format files under ACIDCAT_HUNT_CORPUS")
    assert not crashes, (
        f"{len(crashes)} real file(s) crashed the repair path "
        f"(examined {sum(tried.values())} across {len(tried)} formats):\n"
        + _report(crashes))
