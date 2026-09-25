"""A decode failure is a statement about the audio, not about the name.

`estimate_librosa_metadata` parsed the filename's BPM and key into its result
record and then returned `estimated_bpm`/`estimated_key` as None -- so
`track_128_Am.wav` with a corrupt payload reported bpm null while holding 128 in
the very dict it was building. Evidence computed and thrown away.

The sibling ImportError branch, a hundred lines up, already did it correctly.
So the file got a BETTER answer with librosa ABSENT than with it installed,
which is the shape that makes this worth a test rather than a one-line fix: the
two branches drifted, and nothing held them together.
"""

import struct

import pytest

from acidcat.core.analysis.detect import estimate_librosa_metadata


def _undecodable(path, name):
    """A file librosa cannot load, whose NAME still carries the answer."""
    p = path / name
    p.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt not-real-audio-data")
    return str(p)


def test_filename_evidence_survives_a_decode_failure(tmp_path):
    got = estimate_librosa_metadata(_undecodable(tmp_path, "track_128_Am.wav"))
    assert got["estimated_bpm"] == 128
    assert got["estimated_key"] == "Am"
    assert got["bpm_source"] == "filename"
    assert got["key_source"] == "filename"


def test_the_raw_evidence_fields_still_agree_with_the_estimate(tmp_path):
    """filename_bpm was always populated -- the bug was that it disagreed with
    estimated_bpm. They must not diverge again."""
    got = estimate_librosa_metadata(_undecodable(tmp_path, "loop_140_Fm.wav"))
    assert got["filename_bpm"] == got["estimated_bpm"] == 140
    assert got["filename_key"] == got["estimated_key"] == "Fm"


def test_a_folder_name_key_is_found_on_this_path_too(tmp_path):
    """The failing branch used parse_key_from_filename (basename only) while the
    working branch used parse_key_from_path (basename + parents). Same evidence,
    two different readers, and only one of them looked at the folder."""
    d = tmp_path / "Am"
    d.mkdir()
    got = estimate_librosa_metadata(_undecodable(d, "loop_808.wav"))
    assert got["estimated_key"] == "Am"
    assert got["key_source"] == "filename"


def test_nothing_recoverable_still_reports_failure(tmp_path):
    """The other half: `failed` must keep meaning something. A file with no
    parseable name and no decodable audio has genuinely produced no answer."""
    # `failed` is the verdict after librosa tried and could not decode; without
    # the analysis extra the function never gets that far
    pytest.importorskip("librosa")
    got = estimate_librosa_metadata(_undecodable(tmp_path, "nameless.wav"))
    assert got["estimated_bpm"] is None
    assert got["estimated_key"] is None
    assert got["bpm_source"] == "failed"
    assert got["key_source"] == "failed"


def test_a_partial_answer_is_not_reported_as_total_failure(tmp_path):
    """A name carrying BPM but no key: bpm_source is filename, key_source is
    failed. Marking both `failed` would discard a real answer.

    `174_beat.wav`, not `beat_174.wav`: the filename parser does not read a BPM
    that sits immediately before the extension, which is a separate gap (135 of
    the first 4,000 files in a real corpus are named that way and all parse as
    None). Using an unsupported shape here would test the parser, not this.
    """
    # `failed` is the verdict after librosa tried and could not decode; without
    # the analysis extra the function never gets that far
    pytest.importorskip("librosa")
    got = estimate_librosa_metadata(_undecodable(tmp_path, "174_beat.wav"))
    assert got["estimated_bpm"] == 174
    assert got["bpm_source"] == "filename"
    assert got["key_source"] == "failed"


def test_a_decodable_file_is_unaffected(tmp_path):
    """The fix must not reroute healthy files through the fallback."""
    pytest.importorskip("librosa")
    import math
    n = 44100
    pcm = b"".join(struct.pack("<h", int(9000 * math.sin(i / 30.0)))
                   for i in range(n))
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    p = tmp_path / "real_999_Zz.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

    got = estimate_librosa_metadata(str(p))
    assert got["bpm_source"] != "failed"
    assert got["detected_bpm"] is not None      # it really decoded


def test_both_fallback_branches_agree(tmp_path, monkeypatch):
    """The root cause was drift between the ImportError branch and the decode
    -failure branch. Pin that they produce the same answer for the same file.
    """
    path = _undecodable(tmp_path, "track_120_Gm.wav")
    with_librosa = estimate_librosa_metadata(path)

    import builtins
    real = builtins.__import__

    def no_librosa(name, *a, **k):
        if name == "librosa":
            raise ImportError("blocked")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_librosa)
    without = estimate_librosa_metadata(path)
    monkeypatch.undo()

    for key in ("estimated_bpm", "estimated_key", "filename_bpm", "filename_key"):
        assert with_librosa[key] == without[key], key
