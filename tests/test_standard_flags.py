"""The standard flags are standard (review R9)."""

import argparse
import io
import json
import sys

import pytest

import seeds
from acidcat.cli import _build_parser, main


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        try:
            rc = main(argv)
        except SystemExit as e:
            rc = e.code
    finally:
        sys.stdout, sys.stderr = old
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(seeds.build("wav"))
    return str(p)


def _positionals(sp, name):
    for a in sp._actions:
        if isinstance(a, argparse._SubParsersAction):
            for n, s in a.choices.items():
                yield from _positionals(s, f"{name} {n}")
        elif not a.option_strings:
            yield name, a.metavar or a.dest


def test_positional_files_are_FILE():
    not_files = {"ADDR", "LIB", "DIR", "at", "value", "pattern", "format"}
    bad = [(v, m) for n, sp in _build_parser()._sub.choices.items()
           for v, m in _positionals(sp, n) if m not in not_files and m != "FILE"]
    assert not bad, bad


@pytest.mark.parametrize("argv", [
    ["od", "{wav}", "fmt"], ["classify", "{wav}"], ["locate", "{wav}"],
    ["audit", "{wav}"], ["formats", "wav"], ["probe", "-o", "{out}", "read",
                                             "RIFF/fmt_#sample_rate", "{wav}"],
])
def test_o_writes_the_report(argv, wav, tmp_path):
    out = str(tmp_path / "report.txt")
    argv = [a.format(wav=wav, out=out) for a in argv]
    if "-o" not in argv:
        argv += ["-o", out]
    rc, stdout, _err = _run(argv)
    assert rc == 0 and stdout == ""
    assert open(out, encoding="utf-8").read().strip()


def test_od_output_format(wav):
    rc, out, _ = _run(["od", wav, "fmt", "--output-format", "json"])
    assert rc == 0 and json.loads(out)[0]["addr"] == "RIFF/fmt_"


def test_q_silences_audit_and_edit_notes(wav, tmp_path):
    b = tmp_path / "b.wav"
    b.write_bytes(seeds.build("wav"))
    assert _run(["audit", wav, str(b)])[2]            # the per-file banners
    assert _run(["audit", wav, str(b), "-q"])[2] == ""
    rc, _o, err = _run(["edit", wav, "--set", "key=Am", "--dry-run", "-q"])
    assert rc == 0 and err == ""


def test_stats_takes_a_file_in_every_mode(wav):
    for by in ("meta", "shape", "chunks"):
        rc, out, _ = _run(["stats", wav, "--by", by, "--json", "-q"])
        assert rc == 0 and out.strip(), by


def test_stats_refuses_a_flag_of_another_mode(wav):
    rc, _o, err = _run(["stats", wav, "--by", "shape", "--jobs", "4"])
    assert rc == 2 and "--jobs: not for --by shape" in err
    rc, _o, err = _run(["stats", wav, "--by", "chunks", "--coarse"])
    assert rc == 2 and "--coarse" in err


def test_analyze_takes_several_files_and_needs_its_extra(wav, tmp_path):
    import importlib.util
    b = tmp_path / "b.wav"
    b.write_bytes(seeds.build("wav"))
    rc, out, err = _run(["analyze", "--bpm-key", wav, str(b), "--json"])
    if importlib.util.find_spec("librosa") is None:
        assert rc == 2 and "analysis extra" in err and out == ""
    else:
        assert rc == 0 and len(json.loads(out)) == 2
