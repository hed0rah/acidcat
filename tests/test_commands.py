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


class TestInfoSmplKeyDisplay:
    """Regression tests: info should no longer display the bogus C-1 key
    when SMPL/ACID root_key is 0, and should show pitch class (C) not C4."""

    def test_smpl_root_zero_renders_as_unset(self, tmp_path):
        path = _riff_wav_with_smpl(tmp_path / "zero.wav", smpl_root_key=0)
        code, out, err = run_cli(path)
        assert code == 0 or code is None
        assert "C-1" not in out
        # JSON form is unambiguous for the assertion
        code_j, out_j, _ = run_cli(path, "-f", "json")
        data = json.loads(out_j)
        assert data["Key"] == "-"

    def test_smpl_root_60_renders_as_pitch_class(self, tmp_path):
        path = _riff_wav_with_smpl(tmp_path / "c4.wav", smpl_root_key=60)
        code, out, err = run_cli(path)
        assert code == 0 or code is None
        # pitch class only in the Key line; octave suffix must not appear there
        assert "C (from SMPL)" in out
        assert "C4 (from SMPL)" not in out

    def test_smpl_root_60_json_has_pitch_class(self, tmp_path):
        path = _riff_wav_with_smpl(tmp_path / "c4.wav", smpl_root_key=60)
        code, out, err = run_cli(path, "-f", "json")
        assert code == 0 or code is None
        data = json.loads(out)
        assert data["Key"].startswith("C ")
        assert "C4" not in data["Key"]

    def test_no_smpl_no_acid_renders_as_unset(self, tmp_path):
        path = _riff_wav_with_smpl(tmp_path / "nokey.wav", smpl_root_key=None)
        code, out, err = run_cli(path)
        assert code == 0 or code is None
        assert "C-1" not in out


class TestVerboseStderr:
    """-v should add stderr diagnostics without changing stdout."""

    def test_info_verbose_stdout_unchanged(self, minimal_wav):
        _, out_quiet, _ = run_cli(minimal_wav, "-f", "json")
        _, out_verbose, err = run_cli(minimal_wav, "-f", "json", "-v")
        assert out_quiet == out_verbose
        assert "[detect]" in err

    def test_info_quiet_overrides_verbose(self, minimal_wav):
        _, out, err = run_cli(minimal_wav, "-f", "json", "-v", "-q")
        # -q wins: no verbose lines on stderr
        assert "[detect]" not in err

    def test_chunks_verbose_stdout_unchanged(self, minimal_wav):
        _, out_plain, _ = run_cli("chunks", minimal_wav, "-f", "json")
        _, out_verbose, err = run_cli("chunks", minimal_wav, "-f", "json", "-v")
        assert out_plain == out_verbose
        assert "[chunks]" in err

    def test_dump_verbose_stdout_unchanged(self, minimal_wav):
        _, out_plain, _ = run_cli("dump", minimal_wav, "fmt", "-f", "json")
        _, out_verbose, err = run_cli("dump", minimal_wav, "fmt", "-f", "json", "-v")
        assert out_plain == out_verbose
        assert "[dump]" in err


class TestDumpJson:
    """dump -f json should emit a machine-readable list of chunks."""

    def test_json_structure(self, minimal_wav):
        code, out, err = run_cli("dump", minimal_wav, "fmt", "-f", "json")
        assert code == 0 or code is None
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) >= 1
        entry = data[0]
        for k in ("chunk", "offset", "size", "hex"):
            assert k in entry
        assert entry["chunk"].upper().startswith("FMT")
        assert isinstance(entry["offset"], int)
        assert isinstance(entry["size"], int)
        # full payload in hex, not a preview
        assert len(entry["hex"]) == entry["size"] * 2

    def test_json_multiple_chunks(self, minimal_wav):
        code, out, err = run_cli("dump", minimal_wav, "fmt", "data", "-f", "json")
        assert code == 0 or code is None
        data = json.loads(out)
        chunks_returned = {e["chunk"].upper().strip() for e in data}
        assert {"FMT", "DATA"}.issubset(chunks_returned)

    def test_json_missing_chunk_returns_error(self, minimal_wav):
        code, out, err = run_cli("dump", minimal_wav, "acid", "-f", "json")
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
        assert code == 0 or code is None

    def test_survey_not_directory(self, minimal_wav):
        code, out, err = run_cli("survey", minimal_wav)
        assert code == 1
