"""The 2.0 CLI, and every 1.8 spelling as an alias of it (cli-2.0.md).

Three promises, each checked here:

- every old spelling still works: it prints one line to stderr naming its new
  form, then gives exactly the new form's stdout and exit code;
- a spelling 2.0 removed (the 1.x deprecations) exits 2 and says what to use;
- the exit-code rule holds on every verb: 0 ok, 1 the answer is no, 2 could
  not run.

And the page itself stays complete: every flag of every 1.8 verb and every 2.0
verb is named on it.
"""

import argparse
import io
import json
import pathlib
import re
import shutil
import struct
import sys

import pytest

import seeds
from acidcat import cli_aliases
from acidcat.cli import _build_parser, main

DOC = (pathlib.Path(__file__).parent.parent / "docs" / "contract"
       / "cli-2.0.md").read_text(encoding="utf-8")

VERBS_2 = {"inspect", "od", "carve", "probe", "classify", "locate", "audit",
           "check", "edit", "stats", "analyze", "lib", "convert", "extract",
           "formats", "explore", "tui"}


def _subparsers(parser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a.choices
    return {}


def _flags(parser):
    out = {o for a in parser._actions for o in a.option_strings
           if o not in ("-h", "--help")}
    for sub in _subparsers(parser).values():
        out |= _flags(sub)
    return out


# ── the parser ─────────────────────────────────────────────────────────

def test_the_parser_has_the_seventeen_verbs():
    verbs = set(_subparsers(_build_parser()))
    assert VERBS_2 <= verbs
    assert verbs == VERBS_2


def _sections():
    parts = re.split(r"^### `([a-z0-9-]+)`\s*$", DOC, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


_OLD = {"info": "info", "scan": "scan", "shape": "shape", "od": "od",
        "chunks": "chunks", "survey": "survey", "detect": "detect",
        "features": "features", "similar": "similar", "dump": "dump",
        "index": "index", "query": "query", "inspect": "inspect",
        "convert": "convert", "write": "write", "cover": "cover",
        "explore": "explore", "tui": "tui", "carve": "carve", "repair": "repair",
        "validate": "validate", "audit": "audit", "probe": "probe",
        "census": "census", "locate": "locate", "extract": "extract",
        "formats": "formats", "classify": "classify", "wrap": "wrap"}


def test_every_old_verb_has_a_section_and_a_row():
    sections = _sections()
    table = DOC.split("## 2. Verbs", 1)[1].split("## 3.", 1)[0]
    assert set(_OLD) <= set(sections)
    assert not [v for v in _OLD if f"| `{v}`" not in table]


def test_every_new_verb_is_named_in_the_verb_table():
    table = DOC.split("## 2. Verbs", 1)[1].split("## 3.", 1)[0]
    missing = [v for v in VERBS_2 if f"`{v}" not in table]
    assert not missing, missing


def test_every_2_0_flag_is_on_the_page():
    """A flag the 2.0 parser has and the page never names is a flag nobody
    decided on."""
    verbs = _subparsers(_build_parser())
    missing = sorted({f for v in VERBS_2 for f in _flags(verbs[v])
                      if f"`{f}" not in DOC and f"{f}" not in DOC})
    assert not missing, "2.0 flags cli-2.0.md does not name: %s" % missing


# ── running ────────────────────────────────────────────────────────────

def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return code or 0, out.getvalue(), err.getvalue()


@pytest.fixture
def files(tmp_path):
    """A tagged WAV, a copy of it in a directory, and a MIDI file."""
    d = tmp_path / "lib"
    d.mkdir()
    wav = tmp_path / "a.wav"
    wav.write_bytes(seeds.build("wav"))
    shutil.copy(wav, d / "a.wav")
    mid = tmp_path / "m.mid"
    mid.write_bytes(seeds.build("midi"))
    return {"wav": str(wav), "dir": str(d), "mid": str(mid), "tmp": tmp_path}


# every old spelling that can run read-only (or --dry-run) on those files:
# the verb aliases, and each renamed flag on the verbs that keep their name
OLD_SPELLINGS = [
    ["info", "{wav}"],
    ["info", "{wav}", "--json"],
    ["info", "{wav}", "-v"],
    ["chunks", "{wav}"],
    ["chunks", "{wav}", "--csv"],
    ["dump", "{wav}", "fmt"],
    ["dump", "{wav}", "fmt", "data", "--json"],
    ["dump", "{wav}", "fmt", "-b", "8"],
    ["probe", "hexdump", "0x10", "-l", "8", "{wav}"],
    ["probe", "hexdump", "fmt", "{wav}"],
    ["probe", "hexdump", "fmt.sample_rate", "{wav}"],
    ["validate", "{wav}"],
    ["validate", "{dir}", "-q"],
    ["repair", "{wav}", "--dry-run"],
    ["write", "{wav}", "--set", "title=Kick", "--dry-run"],
    ["cover", "{wav}"],
    ["scan", "{dir}"],
    # -q: the census engine's stderr summary carries the elapsed time
    ["survey", "{dir}", "-q"],
    ["survey", "{dir}", "-n", "1", "--has", "fmt", "--csv", "-q"],
    ["census", "{dir}", "-q"],
    ["census", "{dir}", "--limit", "2", "--top", "3", "--examples", "2",
     "--jobs", "1", "-q"],
    ["scan", "{dir}", "-n", "1", "--json"],
    ["shape", "{dir}"],
    ["shape", "{dir}", "--format", "wav", "--json"],
    ["query", "--limit", "3"],
    ["index", "--list"],
    ["inspect", "{wav}", "--pretty"],
    ["inspect", "{wav}", "--format", "wav", "-q"],
    ["inspect", "{wav}", "--offset", "0", "--length", "12", "-q"],
    ["inspect", "{mid}", "-v", "-q"],
    ["od", "{wav}", "--offset", "16", "--length", "8", "--color", "never"],
    ["od", "{wav}", "--offset", "16", "--end", "24", "--color", "never"],
    ["carve", "{wav}", "--chunk", "fmt", "--encoding", "hex"],
    ["carve", "{wav}", "--field", "sample_rate"],
    ["carve", "{wav}", "--offset", "0", "--length", "4", "--encoding", "hex"],
    ["carve", "{wav}", "--offset", "22", "--type", "u32", "--endian", "le"],
    ["probe", "read", "0x18", "-t", "u32", "--le", "{wav}"],
    # bug hunt 2026-10-02: a padded id with a range, options before FILE, and
    # a numeric --at standing in for --offset
    ["dump", "{wav}", "fmt ", "-b", "8"],
    ["od", "--color", "never", "--offset", "0", "--length", "16", "{wav}"],
    ["carve", "--encoding", "hex", "--offset", "0", "--length", "4", "{wav}"],
    ["od", "{wav}", "--at", "16", "--length", "8", "--color", "never"],
]


def _fill(argv, files):
    return [a.format(**files) for a in argv]


@pytest.mark.parametrize("old", OLD_SPELLINGS, ids=lambda a: " ".join(a))
def test_an_old_spelling_is_exactly_its_new_form(files, old):
    old = _fill(old, files)
    news, line = cli_aliases.translate(old)
    assert line is not None, "%s is not translated" % old
    assert line.startswith("acidcat: `acidcat ") and "` in 2.0" in line
    code, out, err = _run(old)
    want_code, want_out, want_err = 0, "", ""
    for n in news:
        c, o, e = _run(n)
        want_code, want_out, want_err = max(want_code, c), want_out + o, want_err + e
    assert (code, out) == (want_code, want_out)
    # one line more on stderr, first, and nothing else different
    assert err.splitlines()[0] == line
    assert err.split("\n", 1)[1] == want_err


def test_a_numeric_at_folds_into_the_range():
    t = cli_aliases.translate
    assert t(["od", "f", "--at", "16", "--length", "8"])[0] == [["od", "f", "@16+8"]]
    assert t(["carve", "-o", "x", "--offset", "0", "--length", "4", "f"])[0] == [
        ["carve", "-o", "x", "f", "@0+4"]]


def test_the_addr_lands_right_after_file():
    # not at the end: argparse before 3.13 takes FILE and ADDR... together, so
    # an ADDR after an option is "unrecognized" there. A 3.13 dev box forgives
    # it, which is how 17 CI tests broke on 3.11 and none locally.
    t = cli_aliases.translate
    assert t(["od", "--color", "never", "--offset", "0", "--length", "16", "f",
              "--width", "8"])[0] == [
        ["od", "--color", "never", "f", "@0+16", "--width", "8"]]
    assert t(["carve", "f", "--encoding", "hex", "--offset", "0", "--length", "4"])[0] == [
        ["carve", "f", "@0+4", "--encoding", "hex"]]


def test_survey_and_census_caps_become_max_files():
    """survey's `-n` and census's `--limit` are `--max-files`; without one the
    10,000 default applies, which neither verb had."""
    t = cli_aliases.translate
    assert t(["survey", "d", "-n", "7"])[0] == [
        ["stats", "d", "--by", "chunks", "--max-files", "7"]]
    assert t(["survey", "d"])[0] == [["stats", "d", "--by", "chunks"]]
    assert t(["census", "d", "e", "--limit", "9", "--noatime"])[0] == [
        ["stats", "d", "e", "--by", "chunks", "--max-files", "9", "--noatime"]]


def test_has_is_all_of_for_chunks_and_any_of_for_meta(tmp_path):
    """The two modes kept their verbs' --has: scan's any-of rows, census's
    all-of counts. cli-2.0.md section 5 says so."""
    fmt_only = b"fmt " + struct.pack("<I", 16) + struct.pack(
        "<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    data = b"data" + struct.pack("<I", 2) + b"\0\0"
    acid = b"acid" + struct.pack("<I", 4) + b"\0" * 4
    for name, body in (("a.wav", fmt_only + data + acid), ("b.wav", fmt_only + data)):
        riff = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body
        (tmp_path / name).write_bytes(riff)
    code, out, _ = _run(["stats", str(tmp_path), "--by", "chunks", "--has",
                         "data,acid", "--json", "-q"])
    got = json.loads(out)
    assert code == 0 and got["filtered_out"] == 1
    assert got["chunk_histogram"]["data"] == {"files": 1, "occurrences": 1}
    code, out, _ = _run(["stats", str(tmp_path), "--by", "meta", "--has",
                         "data,acid", "--json", "-q"])
    assert len(json.loads(out)) == 2


def test_every_old_verb_is_covered_above():
    covered = {a[0] if a[0] != "probe" else "probe" for a in OLD_SPELLINGS}
    aliased = set(cli_aliases._VERBS) - {"detect", "features", "similar", "wrap"}
    assert aliased <= covered, sorted(aliased - covered)


@pytest.mark.parametrize("old,new", [
    (["detect", "x.wav", "-n", "3"],
     [["analyze", "--bpm-key", "x.wav", "--max-files", "3"]]),
    (["features", "x.wav"],
     [["analyze", "--features", "x.wav", "--output-format", "csv",
       "--max-files", "500"]]),
    (["similar", "x.wav", "-n", "9"], [["lib", "similar", "x.wav", "--top", "9"]]),
    (["wrap", "raw.pcm", "--rate", "22050", "--endian", "be", "-o", "o.wav"],
     [["carve", "raw.pcm", "--as-wav", "--rate", "22050", "--byte-order", "be",
       "-o", "o.wav"]]),
    (["write", "a.wav", "--set", "title=x"], [["edit", "a.wav", "--set", "title=x"]]),
    (["cover", "a.mp3", "--set", "art.png"],
     [["edit", "a.mp3", "--set", "cover=@art.png"]]),
    (["cover", "a.mp3", "--remove"], [["edit", "a.mp3", "--unset", "cover"]]),
    (["cover", "a.mp3", "-o", "art.jpg"],
     [["edit", "a.mp3", "--get", "cover", "-o", "art.jpg"]]),
    (["repair", "a.wav", "--overwrite"], [["check", "--fix", "a.wav", "--overwrite"]]),
    (["index", "lib", "--force", "--deep"], [["lib", "index", "lib", "--reread",
                                               "--analyze"]]),
    (["index", "--stats", "Drums"], [["lib", "stats", "Drums"]]),
    (["index", "--refresh-stats"], [["lib", "stats", "--refresh"]]),
    (["index", "--forget", "Drums"], [["lib", "forget", "Drums"]]),
    (["index", "--remove", "Drums"], [["lib", "forget", "Drums", "--delete-db"]]),
    (["index", "--orphans"], [["lib", "list", "--orphans"]]),
    (["query", "--format", "wav", "--bpm", "120"],
     [["lib", "query", "--bpm", "120", "--only-format", "wav"]]),
    (["scan", "d", "--features"], [["analyze", "--features", "d"]]),
    (["info", "a.wav", "--deep"], [["inspect", "--summary", "a.wav"],
                                   ["analyze", "--bpm-key", "--features", "a.wav"]]),
    (["dump", "a.wav", "smpl", "--write", "out"],
     [["carve", "a.wav", "smpl", "-o", "out/"]]),
    (["inspect", "a.wav", "--force"], [["inspect", "a.wav", "--try-all"]]),
    (["inspect", "a.wav", "--full"], [["inspect", "a.wav", "--json"]]),
])
def test_the_writing_aliases_translate_to_their_new_form(old, new):
    """The aliases that write or need an extra are checked by translation:
    running both would write twice or skip for want of librosa."""
    assert cli_aliases.translate(old)[0] == new


@pytest.mark.parametrize("argv,hint", [
    (["inspect", "a.wav", "-f", "json"], "--output-format"),
    (["info", "a.wav", "-fjson"], "--output-format"),
    (["probe", "map", "--no-color", "a.wav"], "--color never"),
    (["formats", "--format-out", "json"], "--output-format"),
    (["carve", "a.wav", "--format", "hex"], "--encoding"),
    # an anchor runs to EOF in 2.0; a length on it was read from offset 0
    (["od", "a.wav", "--at", "find:data", "--length", "8"], "ADDR"),
])
def test_a_removed_spelling_exits_2_and_says_what_to_use(argv, hint):
    code, out, err = _run(argv)
    assert code == 2 and not out
    assert "removed in 2.0" in err and hint in err


# ── the exit-code rule ─────────────────────────────────────────────────

@pytest.mark.parametrize("verb", sorted(VERBS_2 - {"tui"}))
def test_a_bad_flag_is_2_on_every_verb(verb):
    argv = [verb] + (["index"] if verb == "lib" else []) + ["--no-such-flag"]
    assert _run(argv)[0] == 2


@pytest.mark.parametrize("argv", [
    ["inspect", "{missing}"], ["od", "{missing}"], ["carve", "{missing}", "@0+1"],
    ["probe", "strings", "{missing}"], ["classify", "{missing}"],
    ["locate", "{missing}"], ["audit", "{missing}"], ["check", "{missing}"],
    ["edit", "{missing}", "--set", "title=x"], ["stats", "{missing}"],
    ["extract", "{missing}"], ["explore", "{missing}"],
    ["convert", "{missing}"], ["lib", "similar", "{missing}"],
], ids=lambda a: a[0])
def test_an_unreadable_input_is_2(files, argv):
    missing = str(files["tmp"] / "no-such-file.wav")
    assert _run([a.format(missing=missing) for a in argv])[0] == 2


def _defective(files):
    """A WAV whose RIFF size is wrong: a defect, and inconsistent."""
    raw = bytearray(seeds.build("wav"))
    raw[4:8] = struct.pack("<I", len(raw) + 100)
    p = files["tmp"] / "bad.wav"
    p.write_bytes(bytes(raw))
    return str(p)


def test_no_is_1(files):
    bad = _defective(files)
    assert _run(["audit", bad])[0] == 1
    assert _run(["check", bad])[0] == 1
    assert _run(["od", files["wav"], "no_such_node"])[0] == 1
    assert _run(["carve", files["wav"], "no_such_node"])[0] == 1
    assert _run(["stats", files["dir"], "--by", "shape", "--only-format",
                 "flac"])[0] == 1


def test_od_prints_every_addr_that_resolves(files):
    """`dump FILE acid smpl` (the cheatsheet's) printed the acid chunk and
    skipped a missing smpl with exit 0. Its alias is `od FILE acid smpl`,
    which gave up at the first miss and printed nothing."""
    code, out, err = _run(["od", files["wav"], "fmt", "no_such_node"])
    assert code == 0
    assert "  RIFF/fmt_  0x" in out
    assert "no node 'no_such_node'" in err
    code, out, err = _run(["dump", files["wav"], "fmt", "no_such_node"])
    assert code == 0 and "  RIFF/fmt_  0x" in out
    code, out, err = _run(["od", files["wav"], "no_such_node", "nor_this"])
    assert (code, out) == (1, "")
    assert "'no_such_node'" in err and "'nor_this'" in err
    code, out, _ = _run(["od", files["wav"], "fmt", "no_such_node", "--json"])
    assert code == 0 and [r["addr"] for r in json.loads(out)] == ["RIFF/fmt_"]


def test_yes_is_0(files):
    for argv in (["inspect", files["wav"]], ["od", files["wav"], "fmt"],
                 ["check", files["wav"]], ["stats", files["dir"]],
                 ["inspect", "--summary", files["wav"]], ["formats"]):
        assert _run(argv)[0] == 0, argv


def test_no_verb_is_2():
    assert _run([])[0] == 2 or not sys.stdin.isatty()


# ── stats --max-files ──────────────────────────────────────────────────

def test_stats_stops_at_max_files_as_coverage(files, tmp_path):
    for i in range(3):
        shutil.copy(files["wav"], pathlib.Path(files["dir"]) / ("x%d.wav" % i))
    code, out, err = _run(["stats", files["dir"], "--max-files", "2", "--json"])
    assert code == 0 and len(json.loads(out)) == 2
    assert err.count("stopped at --max-files 2") == 1
    code, out, err = _run(["stats", files["dir"], "--max-files", "0", "--json"])
    assert code == 0 and len(json.loads(out)) == 4 and "stopped" not in err


def test_stats_has_a_default_max_files_and_lib_index_has_none():
    from acidcat.commands import stats
    verbs = _subparsers(_build_parser())
    assert stats.DEFAULT_MAX_FILES == 10000
    lib_index = _subparsers(verbs["lib"])["index"]
    assert "--max-files" not in _flags(lib_index)


def test_no_walker_and_bad_values_are_could_not_run(files):
    """Review R5: a file no walker reads is 2 for inspect, as for audit and
    check; a bad argument value is 2. classify's "opaque" and --try-all's
    leads are answers, so 1."""
    junk = files["tmp"] / "junk.bin"
    junk.write_bytes(bytes(range(256)) * 16)
    for argv in (["inspect", str(junk)], ["audit", str(junk)], ["check", str(junk)],
                 ["formats", "nope"], ["formats", "--fields"],
                 ["inspect", "--force-format", "nope", files["wav"]]):
        assert _run(argv)[0] == 2, argv
    assert _run(["classify", str(junk)])[0] == 1
    assert _run(["inspect", "--try-all", str(junk)])[0] == 1


@pytest.mark.parametrize("at,want", [("0x10", "@0x10+8"), ("fmt", "fmt"),
                                     ("fmt.sample_rate", "fmt#sample_rate")])
def test_probe_hexdump_names_an_address_od_takes(files, at, want):
    """Review V2: the alias made `@fmt+256` of a chunk name, which od
    refused. Only an offset takes the @; `chunk.field` is `chunk#field`."""
    old = ["probe", "hexdump", at, "-l", "8", files["wav"]]
    news, _line = cli_aliases.translate(old)
    assert news == [["od", files["wav"], want]]
    code, out, err = _run(old)
    assert code == 0, err
    assert out
