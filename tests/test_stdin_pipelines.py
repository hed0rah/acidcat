"""The two verbs that could not take a pipe.

`carve FILE --chunk data | inspect -` is the most natural two-step in the tool
and `inspect` was the half that rejected `-`, so every session detoured through
a temp file. `classify` is the documented entry point of the triage pipeline
("point it at anything") and `cat blob | acidcat classify -` was a
file-not-found.

Both now buffer stdin to a temp file for the same reason chunks/dump/probe do
(the walkers seek) -- and neither may let that temp path reach the output. A
path the user never named, which no longer exists by the time they read it, is
worse than no name at all.
"""

import json
import struct
import subprocess
import sys

import pytest


def _wav(path):
    pcm = b"\x00\x01" * 512
    body = (b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


@pytest.fixture
def wav(tmp_path):
    return _wav(tmp_path / "a.wav")


def _pipe(data, *args):
    return subprocess.run([sys.executable, "-m", "acidcat"] + list(args),
                          input=data, capture_output=True)


def test_inspect_reads_stdin(wav):
    r = _pipe(wav.read_bytes(), "inspect", "-")
    assert r.returncode == 0, r.stderr
    out = r.stdout.decode()
    assert "RIFF/WAVE" in out and "fmt" in out and "data" in out


def test_classify_reads_stdin(wav):
    r = _pipe(wav.read_bytes(), "classify", "-")
    assert r.returncode == 0, r.stderr
    assert "wav" in r.stdout.decode()


def test_carve_into_inspect_composes(wav, tmp_path):
    """The actual two-step, end to end, no temp file in between."""
    carve = subprocess.Popen(
        [sys.executable, "-m", "acidcat", "carve", str(wav),
         "--offset", "0", "--length", str(wav.stat().st_size)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    inspect = subprocess.Popen(
        [sys.executable, "-m", "acidcat", "inspect", "-", "--json"],
        stdin=carve.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    carve.stdout.close()
    out, err = inspect.communicate()
    carve.wait()

    assert inspect.returncode == 0, err.decode()
    doc = json.loads(out.decode().splitlines()[0])
    assert doc["format"]["label"] == "RIFF/WAVE"
    assert {c["id"] for c in doc["nodes"][0]["children"]} == {"RIFF/fmt_", "RIFF/data"}


@pytest.mark.parametrize("verb", ["inspect", "classify"])
def test_no_temp_path_reaches_the_output(wav, verb):
    """The leak this fix must not introduce -- `extract` had exactly it."""
    r = _pipe(wav.read_bytes(), verb, "-")
    blob = (r.stdout + r.stderr).decode(errors="replace")
    assert "<stdin>" in blob
    assert "acidcat_stdin" not in blob and "tmp" not in blob.lower().split("/")[-1]


@pytest.mark.parametrize("verb", ["inspect", "classify"])
def test_json_names_stdin_not_the_temp_copy(wav, verb):
    r = _pipe(wav.read_bytes(), verb, "-", "--json")
    text = r.stdout.decode()
    # inspect emits NDJSON (one record per file per line); classify emits one
    # pretty-printed array. Both are documented; parse each as what it is.
    doc = json.loads(text.splitlines()[0] if verb == "inspect" else text)
    rec = doc[0] if isinstance(doc, list) else doc
    # inspect's is a Document, whose file carries the path the caller gave
    got = rec["file"]["path"] if "file" in rec else rec["path"]
    assert got == "<stdin>"


@pytest.mark.parametrize("verb", ["inspect", "classify"])
def test_empty_stdin_is_reported(verb):
    r = _pipe(b"", verb, "-")
    assert r.returncode == 2                      # could not run
    assert b"stdin" in r.stderr.lower()


def test_named_files_are_unaffected(wav):
    r = subprocess.run([sys.executable, "-m", "acidcat", "inspect", str(wav)],
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stdout.startswith("a.wav:")


@pytest.mark.parametrize("argv", [["-"], ["inspect", "--summary", "-"]])
def test_the_summary_card_names_stdin(wav, argv):
    """`cat f.wav | acidcat -` printed `File  tmpXXXX.acidcat_stdin`: the
    shared target walker resolved `-` before the card was built, so the
    card's own stdin check never fired."""
    r = _pipe(wav.read_bytes(), *argv)
    out = r.stdout.decode()
    assert r.returncode == 0, r.stderr.decode()
    assert out.splitlines()[0].split() == ["File", "<stdin>"]
    assert "acidcat_stdin" not in out
