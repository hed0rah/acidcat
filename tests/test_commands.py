"""tests for acidcat CLI commands (info, scan, chunks, dump, survey)."""

import os
import csv
import json
import pytest

from acidcat.cli import main as cli_main


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
        code, out, err = run_cli(minimal_wav, "-f", "json")
        assert code == 0 or code is None
        data = json.loads(out)
        assert "Format" in data or "format" in data or "File" in data

    def test_not_riff_wav_no_crash(self, not_riff):
        code, out, err = run_cli(not_riff)
        # should not raise -- either shows minimal info or prints an error
        assert code in (0, 1, None)

    def test_empty_wav_no_crash(self, empty_file):
        code, out, err = run_cli(empty_file)
        assert code in (0, 1, None)

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

    FIXTURES = os.path.join(os.path.dirname(__file__), "..", "data", "test_formats")

    @pytest.mark.parametrize("name,fmt_keyword", [
        ("gs-16b-2c-44100hz.mp3", "MP3"),
        ("gs-16b-2c-44100hz.flac", "FLAC"),
        ("gs-16b-2c-44100hz.ogg", "OGG"),
        ("gs-16b-2c-44100hz.opus", "OPUS"),
        ("gs-16b-2c-44100hz.m4a", "M4A"),
    ])
    def test_format_shows_in_output(self, name, fmt_keyword):
        path = os.path.join(self.FIXTURES, name)
        if not os.path.isfile(path):
            pytest.skip(f"{name} not present")
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
        code, out, err = run_cli("chunks", not_riff)
        assert code == 1
        assert "Not a RIFF" in err

    def test_nonexistent_file(self, tmp_path):
        code, out, err = run_cli("chunks", str(tmp_path / "missing.wav"))
        assert code == 1

    def test_json_output(self, minimal_wav):
        code, out, err = run_cli("chunks", minimal_wav, "-f", "json")
        assert code == 0 or code is None
        data = json.loads(out)
        assert isinstance(data, list)

    def test_chunk_offsets_present(self, minimal_wav):
        code, out, err = run_cli("chunks", minimal_wav)
        assert "@" in out  # offset marker


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
        code, out, err = run_cli("dump", minimal_wav, "acid")
        assert code == 1
        assert "not found" in err.lower() or "acid" in err.lower()

    def test_nonexistent_file(self, tmp_path):
        code, out, err = run_cli("dump", str(tmp_path / "ghost.wav"), "fmt")
        assert code == 1


class TestScanCommand:
    def test_scan_directory_with_wav(self, tmp_path, minimal_wav):
        import shutil
        shutil.copy(minimal_wav, tmp_path / "test.wav")
        code, out, err = run_cli("scan", str(tmp_path), "-q")
        assert code == 0 or code is None
        csv_path = str(tmp_path / "_metadata.csv")
        # a CSV should be written next to the dir or in cwd
        # find written CSV
        written = list(tmp_path.glob("*.csv")) + [
            f for f in [
                os.path.join(os.getcwd(), "test_metadata.csv"),
                os.path.join(os.getcwd(), "tmp_metadata.csv"),
            ] if os.path.isfile(f)
        ]
        # at least one CSV was created somewhere
        # (we just check the command didn't crash)

    def test_scan_empty_directory(self, tmp_path):
        code, out, err = run_cli("scan", str(tmp_path), "-q")
        assert code == 0 or code is None

    def test_scan_not_a_directory(self, minimal_wav):
        code, out, err = run_cli("scan", minimal_wav, "-q")
        assert code == 1

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
        assert "filename" in rows[0]
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


class TestSurveyCommand:
    def test_survey_wav_directory(self, tmp_path, minimal_wav):
        import shutil
        shutil.copy(minimal_wav, tmp_path / "a.wav")
        shutil.copy(minimal_wav, tmp_path / "b.wav")
        code, out, err = run_cli("survey", str(tmp_path), "-q")
        assert code == 0 or code is None
        assert "fmt" in out
        assert "data" in out

    def test_survey_empty_directory(self, tmp_path):
        code, out, err = run_cli("survey", str(tmp_path), "-q")
        assert code == 0 or code is None

    def test_survey_not_directory(self, minimal_wav):
        code, out, err = run_cli("survey", minimal_wav)
        assert code == 1
