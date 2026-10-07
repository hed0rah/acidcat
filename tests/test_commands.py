"""tests for acidcat CLI commands (info, scan, chunks, dump, survey)."""

import os
import csv
import json
import struct
import pytest

from acidcat.cli import main as cli_main


def _riff_wav_with_smpl(path, smpl_root_key=None, num_samples=4):
    """Write a minimal PCM WAV to path, optionally with a SMPL chunk.

    Used by the C-1 regression tests for the info command.
    """
    sample_rate, channels, bits = 44100, 1, 16
    block_align = channels * bits // 8
    byte_rate = sample_rate * block_align
    audio_data = b"\x00" * (num_samples * block_align)
    fmt = struct.pack(
        "<HHIIHH", 1, channels, sample_rate, byte_rate, block_align, bits,
    )
    fmt_chunk = b"fmt " + struct.pack("<I", 16) + fmt
    data_chunk = b"data" + struct.pack("<I", len(audio_data)) + audio_data
    smpl_chunk = b""
    if smpl_root_key is not None:
        smpl_body = struct.pack(
            "<IIIIIIiiI",
            0, 0, 0, smpl_root_key, 0, 0, 0, 0, 0,
        )
        smpl_chunk = b"smpl" + struct.pack("<I", len(smpl_body)) + smpl_body
    riff_body = b"WAVE" + fmt_chunk + data_chunk + smpl_chunk
    path.write_bytes(b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body)
    return str(path)


def run_cli(*args):
    """Run CLI with args, return (exit_code, stdout, stderr)."""
    import io
    import sys

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        result = cli_main(list(args))
    except SystemExit as e:
        result = e.code
    finally:
        out = sys.stdout.getvalue()
        err = sys.stderr.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    return result, out, err


class TestInfoWav:
    def test_minimal_wav_no_crash(self, minimal_wav):
        code, out, err = run_cli(minimal_wav)
        assert code == 0 or code is None
        assert "wav" in out.lower() or "WAV" in out

    def test_json_output(self, minimal_wav):
        code, out, err = run_cli(minimal_wav, "--json")
        assert code == 0 or code is None
        (data,) = json.loads(out)
        assert data["format"] == "wav" and data["label"] == "RIFF/WAVE"
        assert data["path"] == minimal_wav

    def test_not_riff_wav_says_so(self, not_riff):
        """Was `code in (0, 1, None)` -- an assertion that accepted the old
        behaviour, where a non-audio file got a card of dashes and exit 0.
        Unrecognized is could-not-run (2), like validate and chunks."""
        code, out, err = run_cli(not_riff)
        assert "Traceback" not in err
        assert code == 2
        assert "not a format acidcat recognizes" in err
        assert "acidcat classify" in err          # names the verb that can help

    def test_empty_file_says_so(self, empty_file):
        code, out, err = run_cli(empty_file)
        assert "Traceback" not in err
        assert code == 2

    def test_nonexistent_file_returns_error(self, tmp_path):
        code, out, err = run_cli(str(tmp_path / "ghost.wav"))
        assert code not in (0, None)

    def test_bad_mp3_no_crash(self, bad_mp3):
        """garbage MP3 should not raise after the mutagen fix."""
        code, out, err = run_cli(bad_mp3)
        assert code in (0, 1, None)
        assert "Traceback" not in err


class TestInfoTagged:
    @pytest.fixture(autouse=True)
    def need_mutagen(self):
        pytest.importorskip("mutagen")

    @pytest.mark.parametrize("name,fmt_keyword", [
        ("gs-16b-2c-44100hz.mp3", "MP3"),
        ("gs-16b-2c-44100hz.flac", "FLAC"),
        ("gs-16b-2c-44100hz.ogg", "OGG"),
        ("gs-16b-2c-44100hz.opus", "OPUS"),
        ("gs-16b-2c-44100hz.m4a", "M4A"),
    ])
    def test_format_shows_in_output(self, name, fmt_keyword):
        from conftest import corpus_path
        path = corpus_path(name)
        if path is None:
            pytest.skip(f"no specimen or stand-in for {name}")
        code, out, err = run_cli(path)
        assert code == 0 or code is None
        assert fmt_keyword in out.upper()


class TestChunksCommand:
    def test_valid_wav(self, minimal_wav):
        code, out, err = run_cli("chunks", minimal_wav)
        assert code == 0 or code is None
        assert "fmt" in out
        assert "data" in out

    def test_not_riff_file(self, not_riff):
        """2.0: `chunks` is `inspect --chunks`, which reads every format there
        is a walker for rather than refusing everything that is not RIFF."""
        code, out, err = run_cli("chunks", not_riff)
        assert "is `acidcat inspect --chunks" in err
        new_code, new_out, _ = run_cli("inspect", "--chunks", not_riff)
        assert (code, out) == (new_code, new_out)

    def test_nonexistent_file(self, tmp_path):
        code, out, err = run_cli("chunks", str(tmp_path / "missing.wav"))
        assert code == 2

    def test_json_output(self, minimal_wav):
        code, out, err = run_cli("inspect", "--chunks", minimal_wav, "--json")
        assert code == 0 or code is None
        data = json.loads(out)                    # the contract v1 Document
        assert data["contract"] == 1
        assert {c["id"] for c in data["nodes"][0]["children"]} >= {
            "RIFF/fmt_", "RIFF/data"}

    def test_chunk_rows_as_csv(self, minimal_wav):
        code, out, err = run_cli("inspect", "--chunks", minimal_wav, "--csv")
        assert code == 0 or code is None
        head, *rows = out.strip().splitlines()
        assert head.split(",")[:5] == ["file", "idx", "id", "name", "offset"]
        assert rows[0].split(",")[2:4] == ["RIFF/fmt_", "fmt"]
        assert len(rows) >= 2

    def test_chunk_offsets_present(self, minimal_wav):
        code, out, err = run_cli("inspect", "--quiet", minimal_wav)
        assert "0x0000000c" in out  # the first chunk's offset


class TestDumpCommand:
    def test_valid_chunk(self, minimal_wav):
        # "data" is an unambiguous 4-char chunk ID present in all WAV files
        code, out, err = run_cli("dump", minimal_wav, "data")
        assert code == 0 or code is None
        assert "data" in out.lower()

    def test_valid_chunk_short_name(self, minimal_wav):
        # "fmt" (3 chars) should match "fmt " (4-char RIFF ID with trailing space)
        code, out, err = run_cli("dump", minimal_wav, "fmt")
        assert code == 0 or code is None
        assert "fmt" in out.lower()

    def test_missing_chunk(self, minimal_wav):
        code, out, err = run_cli("od", minimal_wav, "acid")
        assert code == 1
        assert "no node 'acid'" in err

    def test_nonexistent_file(self, tmp_path):
        code, out, err = run_cli("dump", str(tmp_path / "ghost.wav"), "fmt")
        assert code == 2


class TestScanCommand:
    def test_scan_directory_with_wav(self, tmp_path, minimal_wav, monkeypatch):
        """`scan` writes CSV to STDOUT now, so there is nothing to own.

        It used to write `<dirname>_metadata.csv` relative to the working
        directory, which meant this test had to chdir into a temp dir or drop a
        file into the repo root on every run -- and it did exactly that for a
        while, complete with absolute local paths. The chdir stays because the
        assertion is partly that nothing lands there any more.
        """
        import shutil
        shutil.copy(minimal_wav, tmp_path / "test.wav")
        workdir = tmp_path / "cwd"
        workdir.mkdir()
        monkeypatch.chdir(workdir)

        code, out, err = run_cli("scan", str(tmp_path), "-q")
        assert code == 0 or code is None

        assert not list(workdir.glob("*.csv")), (
            "scan invented a CSV in the working directory")
        assert "path" in out.splitlines()[0], "CSV has no header row"
        assert "test.wav" in out, "the scanned file is missing from the CSV"

    def test_scan_empty_directory(self, tmp_path):
        code, out, err = run_cli("scan", str(tmp_path), "-q")
        # nothing to read is the answer no, in every stats mode (review V7);
        # not a crash, not could-not-run
        assert code == 1

    def test_scan_takes_a_file(self, minimal_wav):
        """2.0: `stats FILE` works (review R9), so its scan alias does."""
        code, out, err = run_cli("scan", minimal_wav, "-q")
        assert code == 0 and minimal_wav in out

    def test_scan_csv_has_header(self, tmp_path, minimal_wav):
        import shutil
        # use a fresh subdir so the minimal_wav fixture doesn't appear here too
        scan_dir = tmp_path / "scan_target"
        scan_dir.mkdir()
        shutil.copy(minimal_wav, scan_dir / "a.wav")
        out_csv = str(tmp_path / "out.csv")
        code, out, err = run_cli("scan", str(scan_dir), "-o", out_csv, "-q")
        assert os.path.isfile(out_csv)
        with open(out_csv) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert "path" in rows[0]
        assert "format" in rows[0]

    def test_scan_limit(self, tmp_path, minimal_wav):
        import shutil
        for i in range(5):
            shutil.copy(minimal_wav, tmp_path / f"file_{i}.wav")
        out_csv = str(tmp_path / "out.csv")
        code, out, err = run_cli("scan", str(tmp_path), "-o", out_csv, "-n", "2", "-q")
        assert os.path.isfile(out_csv)
        with open(out_csv) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2


class TestScanSmplSentinel:
    """B-5: `acidcat scan` previously emitted `C-1` in the key column for
    any WAV whose SMPL chunk had root_key=0 (the documented "unset"
    sentinel, MIDI C-1). The info and index code paths already handle
    this; scan was the last holdout.
    """

    def test_scan_smpl_root_zero_not_c_minus_1(self, tmp_path):
        scan_dir = tmp_path / "scan_target"
        scan_dir.mkdir()
        _riff_wav_with_smpl(scan_dir / "zero.wav", smpl_root_key=0)
        out_csv = str(tmp_path / "out.csv")
        code, out, err = run_cli("scan", str(scan_dir), "-o", out_csv, "-q")
        assert code == 0 or code is None
        with open(out_csv, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        # key may fall back to filename or be empty; the regression is
        # that it must NOT be C-1.
        assert rows[0]["key"] != "C-1"

    def test_scan_smpl_root_60_renders_pitch_class(self, tmp_path):
        scan_dir = tmp_path / "scan_target"
        scan_dir.mkdir()
        _riff_wav_with_smpl(scan_dir / "c4.wav", smpl_root_key=60)
        out_csv = str(tmp_path / "out.csv")
        code, out, err = run_cli("scan", str(scan_dir), "-o", out_csv, "-q")
        assert code == 0 or code is None
        with open(out_csv, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["key"] == "C3"  # MIDI 60 = C3 (DAW convention)


class TestSurveyCommand:
    def test_survey_wav_directory(self, tmp_path, minimal_wav):
        import shutil
        shutil.copy(minimal_wav, tmp_path / "a.wav")
        shutil.copy(minimal_wav, tmp_path / "b.wav")
        code, out, err = run_cli("survey", str(tmp_path), "-q")
        assert code == 0 or code is None
        assert "fmt" in out
        assert "data" in out


class TestInfoSmplKeyDisplay:
    """Regression tests: info should no longer display the bogus C-1 key
    when SMPL/ACID root_key is 0, and should show pitch class (C) not C4."""

    def test_smpl_root_zero_renders_as_unset(self, tmp_path):
        path = _riff_wav_with_smpl(tmp_path / "zero.wav", smpl_root_key=0)
        code, out, err = run_cli(path)
        assert code == 0 or code is None
        assert "C-1" not in out
        # JSON form is unambiguous for the assertion
        code_j, out_j, _ = run_cli(path, "--json")
        (data,) = json.loads(out_j)            # rows, one per file
        assert data["key"] is None and data["key_source"] is None

    def test_smpl_root_60_renders_as_pitch_class(self, tmp_path):
        path = _riff_wav_with_smpl(tmp_path / "c4.wav", smpl_root_key=60)
        code, out, err = run_cli(path)
        assert code == 0 or code is None
        # pitch class only in the Key line; octave suffix must not appear there
        assert "C (from SMPL)" in out
        assert "C4 (from SMPL)" not in out

    def test_smpl_root_60_json_has_pitch_class(self, tmp_path):
        path = _riff_wav_with_smpl(tmp_path / "c4.wav", smpl_root_key=60)
        code, out, err = run_cli(path, "--json")
        assert code == 0 or code is None
        (data,) = json.loads(out)
        assert (data["key"], data["key_source"]) == ("C", "smpl")
        assert data["smpl_root"] == "C3"

    def test_no_smpl_no_acid_renders_as_unset(self, tmp_path):
        path = _riff_wav_with_smpl(tmp_path / "nokey.wav", smpl_root_key=None)
        code, out, err = run_cli(path)
        assert code == 0 or code is None
        assert "C-1" not in out


def _acid_loop_wav(path, beats=1, tempo=120.0, root=60, n=8192):
    """A minimal PCM WAV with an acid chunk: a loop of `beats` at `tempo`."""
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    data = b"\x00" * (n * 2)
    acid = struct.pack("<IHHfIHHf", 0x02, root, 0x8000, 0.0, beats, 4, 4, tempo)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", len(data)) + data
            + b"acid" + struct.pack("<I", len(acid)) + acid)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


class TestSummaryRowsAreTyped:
    """The summary's json/csv rows carry values, not the card's display
    strings: `Duration 0.19s` was `"duration": "0.19s"`, `BPM -` was `"-"`
    and `ACID no` was `"no"`, while `stats --by meta` gave the same file's
    duration as a number. The table keeps its words."""

    def test_json_values_are_typed(self, tmp_path):
        loop = _acid_loop_wav(tmp_path / "loop.wav")
        plain = _riff_wav_with_smpl(tmp_path / "plain.wav", num_samples=8192)
        code, out, _ = run_cli("inspect", "--summary", loop, plain, "--json")
        assert code in (0, None)
        a, b = json.loads(out)
        assert a["duration_sec"] == 0.1858 and a["bpm"] == 120.0
        assert (a["acid"], a["acid_beats"]) == (True, 1)
        assert (a["expected_duration"], a["duration_diff"]) == (0.5, -0.3142)
        assert (a["key"], a["key_source"], a["acid_root"]) == ("C", "acid", "C3")
        assert b["duration_sec"] == 0.1858
        assert (b["bpm"], b["key"], b["acid"]) == (None, None, False)
        assert (b["smpl_root"], b["smpl_loop_start"]) == (None, None)
        for row in (a, b):
            assert "duration" not in row and "smpl" not in row

    def test_json_matches_stats_meta_where_both_report(self, tmp_path):
        loop = _acid_loop_wav(tmp_path / "loop.wav")
        _c, out, _ = run_cli("inspect", "--summary", loop, "--json")
        (summary,) = json.loads(out)
        _c, out, _ = run_cli("stats", "--by", "meta", loop, "--json")
        (meta,) = json.loads(out)
        for k in ("duration_sec", "bpm", "acid_beats", "expected_duration",
                  "duration_diff"):
            assert summary[k] == meta[k] and type(summary[k]) is type(meta[k]), k

    def test_csv_leaves_an_absent_value_empty(self, tmp_path):
        plain = _riff_wav_with_smpl(tmp_path / "plain.wav", num_samples=8192)
        _c, out, _ = run_cli("inspect", "--summary", plain, "--csv")
        (row,) = list(csv.DictReader(out.splitlines()))
        assert row["duration_sec"] == "0.1858"
        assert row["bpm"] == "" and row["key"] == ""

    def test_the_table_keeps_its_words(self, tmp_path):
        plain = _riff_wav_with_smpl(tmp_path / "plain.wav", num_samples=8192)
        _c, out, _ = run_cli("inspect", "--summary", plain)
        for line in ("0.1858s", "BPM", "ACID", "SMPL"):
            assert line in out
        assert out.count(" -") >= 2 and " no" in out


class TestVerboseStderr:
    """2.0: -v adds stderr diagnostics and never changes stdout. (In 1.8
    `info -v` and `inspect -v` changed stdout; those spellings are aliases for
    the forms that do what they did.)"""

    def test_stats_verbose_stdout_unchanged(self, tmp_path, minimal_wav):
        import shutil
        shutil.copy(minimal_wav, tmp_path / "a.wav")
        _, out_plain, _ = run_cli("stats", str(tmp_path), "--json", "-q")
        _, out_verbose, err = run_cli("stats", str(tmp_path), "--json", "-v")
        assert out_plain == out_verbose

    def test_the_old_verbose_spellings_say_what_they_are_now(self, minimal_wav):
        _, _, err = run_cli("info", minimal_wav, "-v")
        assert "is `acidcat inspect " in err and "--summary" not in err.split("is `")[1]
        _, _, err = run_cli("inspect", minimal_wav, "-v")
        assert "is `acidcat inspect " in err and "--deep" in err


class TestDumpJson:
    """2.0: `dump --json` is `od --json`: each ADDR's bytes as JSON."""

    def test_json_structure(self, minimal_wav):
        code, out, err = run_cli("od", minimal_wav, "fmt", "--json")
        assert code == 0 or code is None
        data = json.loads(out)
        assert isinstance(data, list) and len(data) == 1
        entry = data[0]
        assert set(entry) == {"addr", "offset", "length", "hex"}
        assert entry["addr"] == "RIFF/fmt_"     # the id `fmt` resolved to
        assert isinstance(entry["offset"], int)
        # the whole payload, not a preview
        assert len(entry["hex"]) == entry["length"] * 2

    def test_json_multiple_chunks(self, minimal_wav):
        code, out, err = run_cli("od", minimal_wav, "fmt", "data", "--json")
        assert code == 0 or code is None
        assert [e["addr"] for e in json.loads(out)] == ["RIFF/fmt_", "RIFF/data"]

    def test_json_missing_chunk_returns_error(self, minimal_wav):
        code, out, err = run_cli("od", minimal_wav, "acid", "--json")
        assert code == 1
        # no stdout emitted on error
        assert out.strip() == ""

    def test_hex_still_default(self, minimal_wav):
        code, out, err = run_cli("dump", minimal_wav, "fmt")
        assert code == 0 or code is None
        # default hex output is not valid JSON
        try:
            json.loads(out)
            assert False, "hex default should not be valid JSON"
        except (json.JSONDecodeError, ValueError):
            pass

    def test_survey_empty_directory(self, tmp_path):
        code, out, err = run_cli("survey", str(tmp_path), "-q")
        # 1: it ran and found no audio. 0 said "surveyed a tree" about a
        # tree it had found nothing in.
        assert code == 1

    def test_survey_takes_a_file(self, minimal_wav):
        """2.0: `stats FILE --by chunks` reads the file it is named."""
        code, out, err = run_cli("survey", minimal_wav)
        assert code == 0 and "1 IFF-family" in out


class TestInfoMidiDivision:
    def _smf(self, tmp_path, division):
        track = b"\x00\xFF\x2F\x00"
        smf = (b"MThd" + struct.pack(">IHHH", 6, 0, 1, division)
               + b"MTrk" + struct.pack(">I", len(track)) + track)
        p = tmp_path / "d.mid"
        p.write_bytes(smf)
        return str(p)

    def test_ppq_division_labeled_ticks_per_beat(self, tmp_path):
        code, out, _ = run_cli(self._smf(tmp_path, 480))
        assert code == 0 or code is None
        assert "480 ticks/beat" in out

    def test_smpte_division_rendered_as_fps(self, tmp_path):
        # 0xE728: high byte -25 fps, low byte 40 ticks/frame. printing
        # the raw word as "59176 ticks/beat" was nonsense.
        code, out, _ = run_cli(self._smf(tmp_path, 0xE728))
        assert code == 0 or code is None
        assert "SMPTE 25 fps, 40 ticks/frame" in out
        assert "ticks/beat" not in out

    def test_smpte_2997_dropframe(self, tmp_path):
        # -29 encodes 29.97 drop-frame
        code, out, _ = run_cli(self._smf(tmp_path, 0xE350))
        assert code == 0 or code is None
        assert "29.97 fps" in out


class TestSummaryRoutesOnBytes:
    """--summary trusted a .mid/.aif extension over the bytes: 'hello' named
    t.mid was 'Format MIDI' and a RIFF/RMID read as 'MIDI type 21069'."""

    def test_an_extension_alone_is_not_a_format(self, tmp_path):
        for name, data in (("t.mid", b"hello"), ("t.aif", b"")):
            p = tmp_path / name
            p.write_bytes(data)
            code, out, _err = run_cli("inspect", "--summary", str(p))
            assert code == 2, name
            assert "Format" not in out

    def test_rmid_named_mid_gets_the_walker_summary(self, tmp_path):
        track = b"\x00\xFF\x2F\x00"
        smf = (b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
               + b"MTrk" + struct.pack(">I", len(track)) + track)
        body = b"RMID" + b"data" + struct.pack("<I", len(smf)) + smf
        p = tmp_path / "r.mid"
        p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
        code, out, _err = run_cli("inspect", "--summary", str(p))
        assert code in (0, None)
        assert "RMID" in out and "21069" not in out


def test_main_reconfigures_stdout_to_utf8(monkeypatch):
    # audio metadata is Unicode; the CLI must force UTF-8 output so a non-Latin
    # tag does not raise UnicodeEncodeError on a cp1252 console (Windows/pipe).
    import sys

    calls = []

    class FakeStream:
        encoding = "cp1252"

        def reconfigure(self, **kw):
            calls.append(kw)

        def write(self, s):
            return len(s)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", FakeStream())
    monkeypatch.setattr(sys, "stderr", FakeStream())
    try:
        cli_main(["--version"])  # reconfigure runs before argparse exits
    except SystemExit:
        pass
    assert any(c.get("encoding") == "utf-8" and c.get("errors") == "replace"
               for c in calls)
