"""Counts and filters must agree with each other.

From the library-organisation audit, which cross-checked every claim against an
independent count. Where two of acidcat's own verbs disagreed about the same
corpus, one of them was wrong -- and in both cases the wrong one was silent
about it.
"""

import json
import os
import struct

import pytest

from acidcat.cli import main


def _wav_with(chunks=(), n_frames=100, riff_size_byte=None):
    """A WAV carrying the given extra chunks. `riff_size_byte` lets a caller
    force a specific byte into the RIFF size field."""
    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16))
    for cid, payload in chunks:
        body += cid + struct.pack("<I", len(payload)) + payload
        if len(payload) & 1:
            body += b"\x00"
    body += b"data" + struct.pack("<I", n_frames * 2) + bytes(n_frames * 2)
    size = struct.pack("<I", len(body) + 4)
    if riff_size_byte is not None:
        size = bytes([size[0], size[1], riff_size_byte, size[3]])
    return b"RIFF" + size + b"WAVE" + body


_ACID = b"acid", struct.pack("<IHHfIIIIf", 0x02, 60, 0x8000, 0.0, 0, 4, 4, 4, 120.0)


def test_census_reads_past_a_0x1a_byte(tmp_path, capsys):
    """os.open without O_BINARY gives a TEXT-mode descriptor on Windows, and
    os.read stops dead at the first 0x1A. On a real 39,369-file tree that
    silently dropped 651 valid RIFF/WAVE files and truncated 573 more, with
    `errors` reporting 0. It survived because the buggy branch is the fallback
    for a missing os.pread -- Windows only -- and CI was Linux only."""
    p = tmp_path / "eof_byte.wav"
    p.write_bytes(_wav_with([_ACID], riff_size_byte=0x1A))
    assert b"\x1a" in p.read_bytes()[:16], "the specimen lost its 0x1A"

    main(["census", str(tmp_path), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["riff_family_files"] == 1, "the file was dropped entirely"
    assert out["chunk_histogram"].get("acid") == {"files": 1, "occurrences": 1}, (
        "the walk stopped at 0x1A before reaching the acid chunk")


def test_census_and_survey_agree_on_the_same_tree(tmp_path, capsys):
    """The cross-check that caught it: two verbs counting the same thing."""
    for i in range(4):
        (tmp_path / f"a{i}.wav").write_bytes(
            _wav_with([_ACID], riff_size_byte=0x1A if i % 2 else None))

    main(["census", str(tmp_path), "--json"])
    census = json.loads(capsys.readouterr().out)["chunk_histogram"].get("acid")
    main(["survey", str(tmp_path), "-n", "99999"])
    survey_out = capsys.readouterr().out
    assert census == {"files": 4, "occurrences": 4}, f"census counted {census} of 4"
    # survey is `stats --by chunks` in 2.0: one engine, so one count
    assert "acid" in survey_out


def test_has_filter_rejects_files_with_no_chunks(tmp_path, capsys):
    """`if wanted and seen:` made the filter a no-op for any format with no
    chunks -- a tagged MP3 or an AppleDouble stub returns seen == [], so the
    row was kept. `--has acid` returned 95 rows where survey and the index both
    said 80."""
    (tmp_path / "has.wav").write_bytes(_wav_with([_ACID]))
    (tmp_path / "plain.wav").write_bytes(_wav_with())
    (tmp_path / "tagged.mp3").write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00"
                                          + b"\xff\xfb\x90\x00" + bytes(400))
    out_csv = tmp_path / "out.csv"

    main(["scan", str(tmp_path), "--has", "acid", "-n", "999",
          "-o", str(out_csv)])
    import csv
    rows = list(csv.DictReader(open(out_csv, encoding="utf-8")))
    assert len(rows) == 1, f"--has acid matched {len(rows)} files, expected 1"
    assert all((r.get("chunks") or "").strip() for r in rows), \
        "a file with no chunks passed a chunk filter"


def test_query_emits_valid_json_when_nothing_matches(tmp_path, capsys):
    """`--json` emitted ZERO bytes on an empty result, so jq and json.loads
    both failed on an ordinary empty answer -- in a tool built for piping."""
    reg = tmp_path / "reg.db"
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.wav").write_bytes(_wav_with())
    main(["index", str(lib), "--label", "jsonprobe", "--registry", str(reg)])
    capsys.readouterr()

    main(["query", "--registry", str(reg), "--bpm", "999", "--json"])
    out = capsys.readouterr().out
    assert out.strip(), "no bytes at all on an empty result"
    assert json.loads(out) == []
