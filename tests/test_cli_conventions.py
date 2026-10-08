"""The CLI shape, held to the conventions long-lived tools converged on.

    TOOL [OPTIONS] OPERAND...
    TOOL SUBCOMMAND [OPTIONS] OPERAND...

Operands come last and are variadic (that is what makes a glob work, since the
shell expands it before the tool is reached); the subcommand follows the tool
immediately; `-` is stdin; `--` ends options.

This file exists because the CLI is the one surface that cannot be changed after
1.0 without breaking people.
"""

import os
import shutil
import subprocess
import sys

import pytest


def _cli(*args, **kw):
    return subprocess.run([sys.executable, "-m", "acidcat", *args],
                          capture_output=True, text=True, **kw)


@pytest.fixture
def two_wavs(tmp_path):
    from conftest import CORPUS_WAV as src
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    shutil.copyfile(src, a)
    shutil.copyfile(src, b)
    return str(a), str(b)


PROBE_SUBVERBS = ["table", "read", "scan", "find", "strings", "hexdump",
                  "diff", "entropy", "map"]


@pytest.mark.parametrize("verb", PROBE_SUBVERBS)
def test_probe_subverb_help_needs_no_file(verb):
    """`probe strings --help` must work on its own.

    With the operand on the parent the only way to read a sub-verb's options
    was to name a file you did not care about: `probe SOMEFILE strings --help`.
    """
    r = _cli("probe", verb, "--help")
    assert r.returncode == 0, r.stderr
    assert f"probe {verb}" in r.stdout


@pytest.mark.parametrize("argv", [
    ["strings"],
    ["find", "s:data"],
    ["read", "fmt.sample_rate"],
    ["entropy", "--width", "24"],
])
def test_probe_accepts_a_glob_of_files(two_wavs, argv):
    """The shell expands `probe *.wav strings` into
    `probe strings a.wav b.wav` -- which has to work, and did not when the
    operand sat between the command and its subcommand."""
    a, b = two_wavs
    r = _cli("probe", *argv, a, b)
    assert r.returncode == 0, r.stderr
    assert "==> " in r.stdout, "multi-file output is not labelled per file"


def test_probe_single_file_output_is_not_labelled(two_wavs):
    """grep/file behaviour: name the file only when there is more than one, so
    a single-file invocation still pipes cleanly."""
    a, _ = two_wavs
    r = _cli("probe", "strings", a)
    assert r.returncode == 0
    assert "==> " not in r.stdout


def test_probe_diff_takes_exactly_two(two_wavs):
    """diff(1) shape. Not variadic -- "diff these five files" means nothing."""
    a, b = two_wavs
    assert _cli("probe", "diff", a, b).returncode == 0
    assert _cli("probe", "diff", a).returncode == 2


def test_probe_without_a_subverb_is_a_usage_error(two_wavs):
    a, _ = two_wavs
    r = _cli("probe", a)
    assert r.returncode == 2


def test_dash_dash_ends_options(two_wavs):
    a, _ = two_wavs
    assert _cli("inspect", "--", a).returncode == 0


def test_options_may_follow_operands(two_wavs):
    """GNU-style permutation, which every coreutils tool allows."""
    a, _ = two_wavs
    assert _cli("inspect", a, "--output-format", "json").returncode == 0


@pytest.mark.parametrize("verb", ["inspect", "info", "od", "classify", "detect",
                                  "audit", "chunks", "shape", "validate"])
def test_dash_reads_stdin(verb):
    """Invariant 3. shape, audit and validate had no stdin handling at all --
    audit got it for free once it routed through targets.each."""
    if verb == "detect":
        # analyze exits 2, once, without the analysis extra; CI installs it
        pytest.importorskip("librosa")
    from conftest import CORPUS_WAV as src
    raw = open(src, "rb").read()
    r = subprocess.run([sys.executable, "-m", "acidcat", verb, "-"],
                       capture_output=True, input=raw)
    assert r.returncode == 0, r.stderr[:300]


def test_usage_and_missing_file_both_exit_2(two_wavs):
    """2 is the conventional usage-error code, and acidcat already uses it for
    a missing operand too."""
    a, _ = two_wavs
    assert _cli("inspect").returncode == 2
    assert _cli("inspect", a + ".nope").returncode == 2
    assert _cli("frobnicate").returncode == 2


# ── arity: a per-file report takes as many files as you hand it ──────

REPORT_VERBS = ["audit", "inspect", "info", "chunks", "shape", "classify",
                "validate"]


@pytest.mark.parametrize("verb", REPORT_VERBS)
def test_report_verbs_accept_several_files(two_wavs, verb):
    """`audit *.wav` is what the shell hands over, and it was a usage error.
    `inspect` and `audit` are the same kind of verb -- read a file, print about
    it -- and took opposite arities with no principle behind the split."""
    a, b = two_wavs
    r = _cli(verb, a, b)
    assert r.returncode == 0, f"{verb} rejected two files: {r.stderr[:200]}"


@pytest.mark.parametrize("verb", REPORT_VERBS)
def test_report_verbs_accept_a_directory(two_wavs, verb, tmp_path):
    a, _ = two_wavs
    d = os.path.dirname(a)
    r = _cli(verb, d)
    assert r.returncode == 0, f"{verb} rejected a directory: {r.stderr[:200]}"


@pytest.mark.parametrize("verb", ["audit", "info", "chunks"])
def test_single_file_output_is_unlabelled(two_wavs, verb):
    """One file must still pipe exactly as it did -- the per-file header is a
    multi-file affordance, the grep/file rule."""
    a, _ = two_wavs
    r = _cli(verb, a)
    assert "==> " not in r.stdout and "==> " not in r.stderr


def test_a_failure_in_one_file_fails_the_command(two_wavs, tmp_path):
    """Worst exit code wins, or `audit *.wav && deploy` proceeds on a bad file."""
    a, _ = two_wavs
    bad = tmp_path / "broken.wav"
    bad.write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunk")
    r = _cli("audit", a, str(bad))
    assert r.returncode != 0


@pytest.mark.parametrize("verb,args", [
    ("read", ["fmt.sample_rate"]), ("strings", []), ("entropy", []),
    ("find", ["s:data"]), ("scan", ["44100", "-t", "u32"]),
])
def test_probe_takes_the_standard_flags_after_the_subverb(two_wavs, tmp_path,
                                                          verb, args):
    """Review V9: `probe read AT F --json` was an argparse error; every other
    verb takes its flags anywhere. The same JSON either side of the subverb,
    and -o after it writes the report."""
    a, _b = two_wavs
    before = _cli("probe", "--json", verb, *args, a)
    after = _cli("probe", verb, *args, a, "--json")
    assert before.returncode == after.returncode == 0, after.stderr
    assert after.stdout == before.stdout and after.stdout.lstrip().startswith(("{", "["))
    out = tmp_path / "r.json"
    r = _cli("probe", verb, *args, a, "--output-format", "json", "-o", str(out))
    assert r.returncode == 0 and not r.stdout
    assert out.read_text(encoding="utf-8") == before.stdout
