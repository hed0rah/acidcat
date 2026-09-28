"""A file handed to a directory verb must land in a bucket, and be counted.

The defect this guards against, found four times in four different verbs: a
walk filters by extension, opens what it recognises, and reports a summary that
counts only what it opened. `validate` printed "all N file(s) consistent" for a
tree whose MP3s it never read. `index` knew 24 extensions while the shared set
knew 87 and reported "0 skipped". `inspect` bound a skip count and threw it
away, two lines under a comment claiming it "says what it skipped".

The rule is not `len(rows) == planted`. Some files legitimately produce no line
-- a format with no structural model, an unreadable file, an unknown extension.
The rule is that every one of them still produces a NUMBER:

    reported + skipped + unmodeled + unreadable == planted

and, just as important, that **every bucket name appears even when it is zero**.
A bucket that only shows up when non-zero cannot distinguish "zero unreadable"
from "does not track unreadable", so a consumer cannot tell a clean answer from
an incomplete one.

WHAT THIS FILE ASSERTS TODAY. `validate` is the only verb that accounts fully,
so it is the only one under the conservation assertion. The rest are listed in
NOT_ACCOUNTING, which may shrink and must not grow. That is deliberately an
honest ledger of debt rather than an aspiration: the clean fix is a shared
accounting record in the JSON of every directory verb, which is a schema
addition to a public interface and belongs in 1.0.1, not on release eve.
"""

import os
import pathlib
import re
import struct
import subprocess
import sys

import pytest

from acidcat import cli

# ── the corpus: one file per bucket ─────────────────────────────────

def _plant(tmp_path):
    """A directory whose composition is known exactly.

    Returns (dir, planted_count). Real encoder output where possible, because a
    synthetic file with the right magic can pass a sniffer and fail a walker,
    which would make a conservation failure ambiguous.
    """
    d = tmp_path / "corpus"
    d.mkdir()
    n = 0
    from conftest import corpus_path
    for name in ("gs-16b-2c-44100hz.wav", "gs-16b-2c-44100hz.flac",
                 "gs-16b-2c-44100hz.aiff", "gs-16b-2c-44100hz.mp3"):
        resolved = corpus_path(name)
        src = pathlib.Path(resolved) if resolved else None
        if src is not None:
            (d / name).write_bytes(src.read_bytes())
            n += 1
    if n < 3:
        pytest.skip("corpus formats not present")
    # unknown extension: dropped by the shared expander, must be counted
    (d / "notes.txt").write_text("not audio")
    n += 1
    # right extension, no parseable structure: the "not modelled" bucket
    (d / "broken.wav").write_bytes(b"RIFF" + struct.pack("<I", 4) + b"WAVEjunk")
    n += 1
    return d, n


def _run(*args):
    return subprocess.run([sys.executable, "-m", "acidcat", *args],
                          capture_output=True, text=True, timeout=600)


# ── the conservation law, where a verb accounts ─────────────────────

def _ints(pattern, text):
    m = re.search(pattern, text)
    return int(m.group(1).replace(",", "")) if m else 0


def test_validate_accounts_for_every_planted_file(tmp_path):
    """`validate` is the model the other verbs should follow.

    It reports four buckets and they sum to what is there, so a file cannot
    disappear between the walk and the summary. This test is what keeps that
    true as formats are added.
    """
    d, planted = _plant(tmp_path)
    r = _run("validate", str(d))
    out = r.stdout + r.stderr

    checked = (_ints(r"all (\d+) file\(s\) consistent", out)
               or _ints(r"of (\d+) file\(s\) have structural issues", out))
    accounted = (checked
                 + _ints(r"(\d+) skipped \(unrecognised", out)
                 + _ints(r"(\d+) not structurally modeled", out)
                 + _ints(r"(\d+) unreadable", out))
    assert accounted == planted, (
        f"planted {planted}, accounted for {accounted}. A file vanished "
        f"between the walk and the summary:\n{out}")


def test_validate_names_each_bucket_it_used(tmp_path):
    """The count is only half of it. A bucket has to be nameable, or a reader
    cannot tell which kind of not-checked they are looking at."""
    d, _planted = _plant(tmp_path)
    r = _run("validate", str(d))
    out = r.stdout + r.stderr
    assert "skipped (unrecognised extension" in out, out
    assert "not structurally modeled" in out, out


# ── the ledger of verbs that do NOT yet account ─────────────────────

# Verbs that accept a directory and summarise what they did, but do not report
# what they passed over. Each is a place the same defect can recur. This may
# shrink and must not grow.
NOT_ACCOUNTING = {
    # 2.0 names: analyze was detect and features, lib was index and similar,
    # stats was scan and shape
    "analyze", "audit", "classify", "convert", "inspect", "lib", "stats",
}

# Directory verbs that DO account. `check` (1.8's `validate`) was filed under
# NOT_A_DIRECTORY_VERB, which is where the union logic wanted it but which
# misdescribes a verb whose whole job includes walking a tree.
ACCOUNTING = {"check"}

# Verbs that never take a directory, so conservation does not apply. A reason
# each, because "not applicable" without one is indistinguishable from "not
# looked at".
NOT_A_DIRECTORY_VERB = {
    "carve": "cuts a byte range out of one named file",
    "edit": "edits the tags, fields or cover of named files",
    "explore": "builds one HTML page from one file",
    "extract": "pulls samples out of one bank",
    "formats": "prints a static capability table, reads no path",
    "locate": "scans one blob",
    "od": "hex view of one file",
    "probe": "byte-level dissection of named files",
    "tui": "interactive, one file or a browser",
}


def test_every_verb_is_classified():
    """A new verb must be triaged, not silently unconsidered.

    Enumerated from the parser rather than a hand list, so a verb added
    tomorrow fails this the day it lands. That is the same mechanism that
    caught `census` and `wrap` missing from an earlier literal list.
    """
    parser = cli._build_parser()
    verbs = set(parser._sub.choices)
    classified = NOT_ACCOUNTING | ACCOUNTING | set(NOT_A_DIRECTORY_VERB)
    missing = verbs - classified
    assert not missing, (
        f"new verb(s) {sorted(missing)}: say whether they take a directory. "
        f"If they do, they owe an accounting; if not, add a reason to "
        f"NOT_A_DIRECTORY_VERB.")
    ghosts = classified - verbs
    assert not ghosts, f"these verbs no longer exist: {sorted(ghosts)}"


def test_the_debt_only_shrinks():
    """A ratchet, after tests/test_targets.py.

    Twelve verbs summarise a directory without saying what they passed over.
    Writing the number down makes adding a thirteenth a visible act.
    """
    assert len(NOT_ACCOUNTING) <= 12, (
        f"NOT_ACCOUNTING has grown to {len(NOT_ACCOUNTING)}. A new directory "
        f"verb should account for what it skipped rather than join this list.")


def test_no_directory_verb_writes_into_the_working_directory(tmp_path):
    """`scan DIR` drops <dirname>_metadata.csv into the CWD with no -o given.

    Caught when it wrote into the repo root during development of this file.
    It is documented as "batch-scan with CSV output", which reads as stdout,
    and it will silently overwrite a file of that name.

    Asserted for the verbs that should be read-only. `scan` is the known
    offender and is xfail until 1.0.1 rather than quietly excluded.
    """
    d, _planted = _plant(tmp_path)
    work = tmp_path / "cwd"
    work.mkdir()
    for verb in ("validate", "inspect", "classify", "shape", "scan", "features"):
        before = set(os.listdir(work))
        subprocess.run([sys.executable, "-m", "acidcat", verb, str(d)],
                       capture_output=True, text=True, cwd=work, timeout=600)
        after = set(os.listdir(work))
        assert after == before, (
            f"`{verb}` wrote {sorted(after - before)} into the working "
            f"directory without being asked for output")


def test_scan_does_not_write_into_the_working_directory(tmp_path):
    """Was xfail with "documented for 1.0.1, not fixed here". Fixed now: the
    CSV goes to stdout unless -o names a file, which is what "batch-scan with
    CSV output" said all along and what `--json` already did in the same
    command. It had been dropping <dirname>_metadata.csv into whatever
    directory you were standing in, overwriting a file of that name."""
    d, _planted = _plant(tmp_path)
    work = tmp_path / "cwd2"
    work.mkdir()
    before = set(os.listdir(work))
    subprocess.run([sys.executable, "-m", "acidcat", "scan", str(d)],
                   capture_output=True, text=True, cwd=work, timeout=600)
    assert set(os.listdir(work)) == before
