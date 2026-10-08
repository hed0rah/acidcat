"""Every verb that produces records can hand them to a machine.

An interface audit across all 29 verbs found six different output-format sets
and twelve verbs with no machine output at all. Two of those twelve were the
worst offenders for their own job:

  `shape` IS the data verb -- its entire output is records built for
  `sort | uniq -c` -- and TSV was hardcoded with no way to reach a JSON
  consumer, unlike its sibling `census`.

  `validate` is the CI-gate verb. You could branch on its exit code but not read
  WHICH file failed or WHY without scraping the human table.

The rule applied: flat records get all four renderings; nested or structural
output (inspect's chunk tree, dump's hex) keeps table+json. Defaults do not
change -- shape stays TSV because `sort | uniq -c` depends on it.
"""

import csv
import io
import json
import struct
import subprocess
import sys

import pytest


def _wav(path, riff_size=None):
    pcm = b"\x11\x22" * 256
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    raw = bytearray(b"RIFF" + struct.pack("<I", len(body)) + body)
    if riff_size is not None:
        struct.pack_into("<I", raw, 4, riff_size)
    path.write_bytes(bytes(raw))
    return path


def _run(*args):
    return subprocess.run([sys.executable, "-m", "acidcat"] + list(args),
                          capture_output=True, text=True)


@pytest.fixture
def files(tmp_path):
    return _wav(tmp_path / "ok.wav"), _wav(tmp_path / "bad.wav", riff_size=99999)


# ── shape ──────────────────────────────────────────────────────────

def test_shape_tsv_is_still_the_default_and_headerless(files):
    """`sort | uniq -c` counts every line, so a header row would corrupt the
    histogram. TSV must stay default AND stay headerless."""
    ok, _ = files
    r = _run("shape", "--no-path", str(ok))
    assert r.returncode == 0
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "\t" in lines[0]
    assert not lines[0].startswith("format")        # no header


@pytest.mark.parametrize("flag", ["--json", "--csv", "--output-format", "tsv"])
def test_shape_accepts_every_declared_format(files, flag):
    ok, _ = files
    args = ["shape", "--no-path", str(ok)]
    args += [flag] if flag != "--output-format" else ["--output-format", "table"]
    assert _run(*args).returncode == 0


def test_shape_json_is_parseable_records(files):
    ok, _ = files
    doc = json.loads(_run("shape", "--no-path", "--json", str(ok)).stdout)
    assert len(doc) == 1
    assert (doc[0]["format"], doc[0]["label"]) == ("wav", "RIFF/WAVE")
    assert doc[0]["chunks"] == "data,fmt"


def test_shape_csv_has_a_header(files):
    """The opposite of TSV: csv is for spreadsheets and consumers that expect
    a header, so it gets one."""
    ok, _ = files
    rows = list(csv.DictReader(io.StringIO(
        _run("shape", "--no-path", "--csv", str(ok)).stdout)))
    assert rows and (rows[0]["format"], rows[0]["label"]) == ("wav", "RIFF/WAVE")


# ── validate ───────────────────────────────────────────────────────

def test_validate_json_says_which_file_and_why(files):
    ok, bad = files
    r = _run("validate", str(ok), str(bad), "--json")
    doc = json.loads(r.stdout)

    by_status = {d["status"]: d for d in doc}
    assert by_status["ok"]["path"].endswith("ok.wav")
    assert by_status["fail"]["path"].endswith("bad.wav")
    assert by_status["fail"]["issues"] == 1
    assert by_status["fail"]["repairable"] is True
    assert by_status["fail"]["violations"][0]["describe"]


def test_validate_json_distinguishes_skipped_from_clean(tmp_path):
    """"checked and clean" and "never modelled" are different facts, and the
    exit code already treats them differently (0 vs 2)."""
    _wav(tmp_path / "a.wav")
    (tmp_path / "b.bin").write_bytes(bytes(range(256)))
    doc = json.loads(_run("validate", str(tmp_path / "a.wav"),
                          str(tmp_path / "b.bin"), "--json").stdout)
    assert {d["status"] for d in doc} == {"ok", "skipped"}


def test_validate_machine_output_is_not_polluted_by_the_summary(files):
    """The human tail ("1 of 2 file(s) have structural issues") made the JSON
    unparseable with "Extra data". stdout belongs to the records."""
    ok, bad = files
    r = _run("validate", str(ok), str(bad), "--json")
    json.loads(r.stdout)                        # must not raise
    assert "structural issues" in r.stderr


def test_validate_csv_flattens_the_violations(files):
    """A nested list has no honest cell representation; csv/tsv drop the key
    rather than stringify it into something nobody can parse."""
    ok, bad = files
    rows = list(csv.DictReader(io.StringIO(
        _run("validate", str(ok), str(bad), "--csv").stdout)))
    assert "violations" not in rows[0]
    assert rows[1]["status"] == "fail" and rows[1]["issues"] == "1"
    assert rows[1]["detail"]


def test_validate_exit_codes_survive_every_rendering(files):
    """The gate must gate the same way whichever format you asked for."""
    ok, bad = files
    for fmt in ([], ["--json"], ["--csv"], ["--output-format", "tsv"]):
        assert _run("validate", str(ok), *fmt).returncode == 0, fmt
        assert _run("validate", str(ok), str(bad), *fmt).returncode == 1, fmt


def test_validate_table_output_is_unchanged(files):
    ok, bad = files
    out = _run("validate", str(ok), str(bad)).stdout
    assert "OK    ok.wav" in out
    assert "FAIL  bad.wav" in out


# ── the invariant: a declared format must actually render ──────────

def _declared_formats(verb):
    """The --output-format choices a verb advertises, read from its parser."""
    import argparse
    from acidcat import cli
    p = argparse.ArgumentParser()
    sub = p.add_subparsers()
    mod = __import__(f"acidcat.commands.{verb}", fromlist=["register"])
    mod.register(sub)
    for act in sub.choices[verb]._actions:
        if act.dest == "output_format" and act.choices:
            return list(act.choices)
    return []


# every verb whose output is flat records. The nested ones (audit's report,
# probe's dissection) deliberately offer table+json only. census's histogram
# is flat rows in csv/tsv (chunk id, files, occurrences, example): it is
# `stats --by chunks` in 2.0, and survey, which it absorbed, had csv.
_FLAT_RECORD_VERBS = ["chunks", "classify", "detect", "extract", "features",
                      "formats", "info", "locate", "query", "repair", "scan",
                      "similar", "shape", "validate", "write",
                      # 2.0: the new verbs, and inspect, whose --quiet chunk
                      # table is flat rows (one per chunk) in csv/tsv
                      "check", "edit", "stats", "analyze", "inspect"]


@pytest.mark.parametrize("verb", _FLAT_RECORD_VERBS)
def test_flat_record_verbs_offer_all_four_renderings(verb):
    """Six different format sets across the verbs was the finding. One rule:
    flat records get table/json/csv/tsv, so a user never has to remember which
    verb supports which."""
    assert set(_declared_formats(verb)) == {"table", "json", "csv", "tsv"}, verb


@pytest.mark.parametrize("verb", ["audit", "probe"])
def test_nested_verbs_deliberately_offer_fewer(verb):
    """Pinned so the rule above is a decision, not an oversight: csv/tsv have
    no honest representation for a chunk tree or a histogram-of-histograms."""
    assert set(_declared_formats(verb)) == {"table", "json"}, verb


@pytest.mark.parametrize("verb,argv", [
    ("classify", ["classify"]),
    ("locate", ["locate"]),
    ("chunks", ["chunks"]),
    ("info", ["info"]),
    ("shape", ["shape"]),
    ("validate", ["validate"]),
])
def test_every_declared_format_actually_changes_the_output(tmp_path, verb, argv):
    """A format listed in --help that falls through to the table renderer is a
    flag that is accepted and ignored -- exactly the bug `scan --json` had.
    classify ignored tsv, locate and formats ignored csv, all three silently.
    """
    src = _wav(tmp_path / "a.wav")
    base = _run(*argv, str(src), "--output-format", "table").stdout
    for fmt in ("json", "csv", "tsv"):
        got = _run(*argv, str(src), "--output-format", fmt).stdout
        assert got != base, f"{verb} --output-format {fmt} is ignored"


_BARE_JSON_BOOL = ["audit", "extract", "probe"]


@pytest.mark.parametrize("verb", _BARE_JSON_BOOL)
def test_no_verb_keeps_a_bare_json_bool(verb):
    """These three declared `--json` as a store_true, bypassing the shared
    registry -- so `--output-format json`, which works on 26 other verbs, was an
    error on the forensic one, the recovery one and the RE one. Both spellings
    must reach the same place.
    """
    fmts = _declared_formats(verb)
    assert "json" in fmts and "table" in fmts, verb


def test_extract_machine_output_reports_nothing_extracted(tmp_path):
    """The json path returned 0 unconditionally while the table path returned 1
    for the same empty result -- the exit code depended on how you asked."""
    p = tmp_path / "empty.mod"
    p.write_bytes(bytes(32))
    for fmt in ([], ["--json"], ["--csv"]):
        assert _run("extract", str(p), *fmt).returncode in (1, 2), fmt


# -- repair / write: the two verbs that CHANGE your files -----------

def test_repair_json_says_what_it_did(tmp_path):
    """repair rewrites your file and could not tell a script which one or how."""
    p = _wav(tmp_path / "b.wav", riff_size=99999)
    doc = json.loads(_run("repair", str(p), "--json").stdout)
    assert len(doc) == 1
    r = doc[0]
    assert r["action"] == "repaired"
    assert r["written"].endswith("b.wav")
    assert r["backup"].endswith("b_original.wav")
    assert r["violations"][0]["describe"]


def test_repair_dry_run_reports_would_repair(tmp_path):
    p = _wav(tmp_path / "b.wav", riff_size=99999)
    before = p.read_bytes()
    doc = json.loads(_run("repair", "--dry-run", str(p), "--json").stdout)
    assert doc[0]["action"] == "would-repair"
    assert doc[0]["written"] is None
    assert p.read_bytes() == before            # --dry-run still writes nothing


def test_write_json_says_which_fields_moved(tmp_path):
    p = _wav(tmp_path / "a.wav")
    doc = json.loads(_run("write", str(p), "--set", "artist=X",
                          "--set", "title=Y", "--json").stdout)
    changes = {c["field"]: c["new"] for c in doc[0]["changes"]}
    assert changes == {"artist": "X", "title": "Y"}
    assert doc[0]["backup"].endswith("a_original.wav")


def test_write_dry_run_is_marked_and_writes_nothing(tmp_path):
    p = _wav(tmp_path / "a.wav")
    before = p.read_bytes()
    doc = json.loads(_run("write", str(p), "--set", "artist=X",
                          "--dry-run", "--json").stdout)
    assert doc[0]["dry_run"] is True and doc[0]["written"] is None
    assert p.read_bytes() == before
    assert not (tmp_path / "a_original.wav").exists()


@pytest.mark.parametrize("verb,extra_args", [
    ("repair", []),
    ("write", ["--set", "artist=X"]),
])
def test_mutating_verbs_keep_their_human_output(tmp_path, verb, extra_args):
    """The default is still the human report -- these renderings are additive."""
    p = _wav(tmp_path / "b.wav", riff_size=99999)
    out = _run(verb, str(p), *extra_args).stdout
    assert "wrote" in out
    assert not out.lstrip().startswith(("{", "["))


@pytest.mark.parametrize("fmt", ["json", "csv", "tsv"])
def test_formats_fields_honours_the_output_format(fmt):
    # --fields printed its table whatever was asked for
    r = _run("formats", "--fields", "--output-format", fmt, "wav")
    assert r.returncode == 0, r.stderr
    if fmt == "json":
        rows = json.loads(r.stdout)
    else:
        rows = list(csv.DictReader(io.StringIO(r.stdout),
                                   delimiter="," if fmt == "csv" else "\t"))
    assert rows and set(rows[0]) == {"field", "kind", "access", "goes_to"}
    assert any(row["field"] == "artist" for row in rows)


def test_no_rows_in_json_is_an_empty_array(files, tmp_path):
    # the exit code said "nothing matched"; stdout said nothing at all
    ok, _bad = files
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00" * 64)
    for argv in (["stats", "--json", str(junk)],
                 ["stats", "--by", "shape", "--only-format", "nope", "--json", str(ok)]):
        r = _run(*argv)
        assert r.returncode == 1, (argv, r.stderr)
        assert json.loads(r.stdout) == [], argv


def test_stats_by_shape_writes_its_output_file(files, tmp_path):
    ok, _bad = files
    out = tmp_path / "shape.tsv"
    r = _run("stats", str(ok), "--by", "shape", "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert not r.stdout and "RIFF" in out.read_text(encoding="utf-8")
