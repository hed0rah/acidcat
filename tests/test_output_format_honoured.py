"""A registered --output-format must actually change the output.

`scan` and `features` both call add_output_format_arg() and then, on the
directory path, ignored the result entirely -- always writing a CSV file. So
`scan DIR --json` accepted the flag, produced CSV, and piped nothing at all.
argparse accepting a flag is not the same as the command honouring it, and
nothing in the suite could tell the two apart.
"""

import json
import struct
import subprocess
import sys


def _wav(path, rate=44100, secs=0.05):
    n = int(rate * secs)
    data = b"\x00\x00" * n
    body = (b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(data)) + data)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def _run(args, cwd):
    # encoding="utf-8" explicitly, because text=True decodes with the LOCALE
    # encoding -- cp1252 on the Windows runners. acidcat reconfigures its stdout
    # to UTF-8 on purpose (cli._dispatch), so a locale-decoding reader turned
    # correct output into mojibake and the katakana test below failed on Windows
    # alone while the bytes on the pipe were right the whole time. errors, not
    # strict: a genuine encoding regression should surface as a failed assertion
    # about the content, not as a decode traceback inside the test harness.
    return subprocess.run([sys.executable, "-m", "acidcat"] + args,
                          cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def test_scan_json_goes_to_stdout(tmp_path):
    src = tmp_path / "lib"
    src.mkdir()
    _wav(src / "a.wav")
    _wav(src / "b.wav")

    r = _run(["scan", str(src), "--json", "-q"], tmp_path)
    assert r.returncode == 0, r.stderr

    rows = json.loads(r.stdout)          # the whole point: parseable on a pipe
    assert len(rows) == 2
    assert {row["format"] for row in rows} == {"wav"}

    # and it must NOT have silently written the CSV file instead
    assert not list(tmp_path.glob("*.csv"))


def test_scan_default_pipes_csv(tmp_path):
    """CSV on stdout, and nothing written unless -o asks for it.

    This test used to assert the opposite -- "the default is load-bearing,
    scripts depend on the CSV file" -- while test_directory_accounting.py
    carried an xfail calling the same behaviour a defect "documented for
    1.0.1". The suite held both positions at once. This is the deliberate
    resolution: a verb that invents a file in whatever directory you are
    standing in, and silently overwrites anything of that name, is not a
    default worth keeping. `-o` still writes the file.

    BREAKING for a script that runs `acidcat scan DIR` and then opens
    `DIR_metadata.csv`; such a script wants `-o DIR_metadata.csv` now.
    """
    src = tmp_path / "lib"
    src.mkdir()
    _wav(src / "a.wav")

    r = _run(["scan", str(src), "-q"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("path,format,label,bpm,key"), r.stdout[:80]
    assert not list(tmp_path.glob("*.csv")), "it wrote a file nobody asked for"

    # and -o still does what it always did
    r = _run(["scan", str(src), "-q", "-o", "asked.csv"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "asked.csv").read_text().startswith("path,format")


def test_scan_json_to_output_file(tmp_path):
    src = tmp_path / "lib"
    src.mkdir()
    _wav(src / "a.wav")

    dest = tmp_path / "out.json"
    r = _run(["scan", str(src), "--json", "-o", str(dest), "-q"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(dest.read_text())[0]["format"] == "wav"


def test_a_non_latin_filename_survives_the_pipe(tmp_path):
    """Piped stdout on Windows defaults to cp1252, and these rows carry file
    paths -- so one katakana filename would raise UnicodeEncodeError partway
    through, leaving a truncated CSV and a traceback.

    `cli._dispatch` already reconfigures stdout to UTF-8 with errors="replace"
    for exactly this, and the CSV path inherits it. Pinned because it is
    load-bearing and invisible: nothing else fails if that reconfigure is ever
    dropped, until someone scans a directory with a non-Latin name in it.
    """
    src = tmp_path / "lib"
    src.mkdir()
    _wav(src / "\u30c6\u30b9\u30c8_\u00e9\u00fc.wav")     # katakana + accents

    r = _run(["scan", str(src), "-q"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "UnicodeEncodeError" not in r.stderr, r.stderr
    assert "\u30c6\u30b9\u30c8" in r.stdout or "?" in r.stdout, (
        "the row vanished rather than being encoded or replaced")
    assert r.stdout.count("\n") >= 2, "the CSV was truncated mid-write"
