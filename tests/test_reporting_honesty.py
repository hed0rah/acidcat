"""A summary must describe what was actually examined.

Every case here is the same defect wearing different clothes: work was skipped
-- capped, unreadable, unparseable, or crashed -- and the report presented the
remainder as the whole answer. For a forensic tool that is worse than an error,
because the user has no reason to look further.
"""

import os
import struct

import pytest

from acidcat.cli import main


def _wav(n_frames=32):
    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", n_frames * 2) + bytes(n_frames * 2))
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def test_survey_distinguishes_absent_from_unreadable(tmp_path, capsys):
    """"no RIFF/WAV files found" for a directory of three WAVs was two false
    statements at once. For a specimen hunter, unparseable is the find."""
    for n in ("b1.wav", "b2.wav", "b3.wav"):
        (tmp_path / n).write_bytes(b"RIFF\x10\x00\x00\x00WAVEjunk")
    # survey is `stats --by chunks` in 2.0, and the census report keeps this
    assert main(["survey", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "3 unparseable" in out
    assert "none with a readable chunk" in out
    assert "no IFF-family files found" not in out


def test_survey_counts_unparseable_alongside_good_files(tmp_path, capsys):
    (tmp_path / "good.wav").write_bytes(_wav())
    (tmp_path / "bad.wav").write_bytes(b"RIFF\x10\x00\x00\x00WAVEjunk")
    assert main(["survey", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "2 IFF-family, 1 unparseable" in out


def test_survey_says_when_the_cap_stopped_it(tmp_path, capsys):
    for i in range(4):
        (tmp_path / f"f{i}.wav").write_bytes(_wav())
    main(["survey", str(tmp_path), "-n", "2"])
    got = capsys.readouterr()
    assert "stopped at --max-files 2" in got.err
    assert "counts from the first 2 file(s) only" in got.out


def test_validate_does_not_call_a_library_consistent_when_files_were_unreadable(
        tmp_path, capsys, monkeypatch):
    """A CI job over a library with locked files printed "all N consistent"
    and exited 0."""
    (tmp_path / "ok.wav").write_bytes(_wav())
    (tmp_path / "locked.wav").write_bytes(_wav())

    real_open = open

    def deny(path, *a, **k):
        if str(path).endswith("locked.wav"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", deny)
    rc = main(["validate", str(tmp_path)])
    out = capsys.readouterr().out
    assert "unreadable (not checked)" in out
    assert rc != 0, "an unchecked file exited as a clean pass"


def test_a_crashed_anomaly_rule_is_reported_not_erased():
    """Four rules were `except Exception: pass`, so a malformed structure
    removed the check and read as a clean file."""
    from acidcat.core.forensics import anomalies
    src = open(anomalies.__file__, encoding="utf-8").read()
    assert "except Exception:\n            pass" not in src, \
        "an anomaly rule still swallows its own failure"
    assert src.count("check_failed") >= 4


def test_check_failed_findings_say_what_was_not_screened(tmp_path):
    """The message must name the check, or the warning is unactionable."""
    from acidcat.core.forensics import anomalies
    src = open(anomalies.__file__, encoding="utf-8").read()
    for rule in ("ogg_multistream", "mp4_mdat_coverage", "id3_padding",
                 "dual_endian_pcm"):
        assert f"the {rule} check could not run" in src


def test_a_polyglot_beyond_one_megabyte_is_found(tmp_path, capsys):
    """The trailing-data search read the first 1 MiB while the accompanying
    finding announced the FULL trailing size -- so a PDF appended 3 MB past the
    container end was reported as 3 MB of trailing data and no polyglot, with
    nothing saying the search had stopped."""
    p = tmp_path / "far.wav"
    p.write_bytes(_wav() + bytes(3 * 1024 * 1024) + b"%PDF-1.7\n" + bytes(64))
    main(["audit", str(p)])
    out = capsys.readouterr().out
    assert "polyglot" in out, "a magic past the old 1 MiB cliff was missed"
    assert "PDF" in out


def test_a_nearby_polyglot_still_works(tmp_path, capsys):
    p = tmp_path / "near.wav"
    p.write_bytes(_wav() + bytes(512 * 1024) + b"%PDF-1.7\n" + bytes(64))
    main(["audit", str(p)])
    assert "polyglot" in capsys.readouterr().out


def test_a_magic_straddling_a_block_boundary_is_found(tmp_path, capsys):
    """The streaming search overlaps blocks by the longest magic; without the
    overlap a signature split across the boundary would vanish."""
    from acidcat.core.forensics.anomalies import _MAGIC_BLOCK
    pad = _MAGIC_BLOCK - 4                      # puts %PDF- across the seam
    p = tmp_path / "seam.wav"
    p.write_bytes(_wav() + bytes(pad) + b"%PDF-1.7\n" + bytes(64))
    main(["audit", str(p)])
    assert "polyglot" in capsys.readouterr().out


def test_fake_hires_is_judged_from_the_whole_file(tmp_path):
    """The bit-depth check read a contiguous first 8 MB -- about 28 seconds of
    24/48 stereo -- so a long master whose intro came from a 16-bit source was
    accused of being padded throughout. Sampling end to end costs the same."""
    import struct as _s
    from acidcat.core.forensics import integrity
    from acidcat.core.walk import walk_file

    def wav24(frames, padded_prefix):
        pcm = bytearray()
        for i in range(frames):
            v = (i * 7919) & 0xFFFFFF
            v = (v & 0xFFFF00) if i < padded_prefix else (v | 1)
            pcm += v.to_bytes(3, "little")
        body = (b"fmt " + _s.pack("<I", 16)
                + _s.pack("<HHIIHH", 1, 1, 48000, 144000, 3, 24)
                + b"data" + _s.pack("<I", len(pcm)) + bytes(pcm))
        return b"RIFF" + _s.pack("<I", len(body) + 4) + b"WAVE" + body

    honest = tmp_path / "long.wav"              # 16-bit-sourced intro, real 24-bit after
    honest.write_bytes(wav24(4_000_000, 3_000_000))
    label, chunks, _ = walk_file(str(honest))
    assert not integrity.analyze(label, chunks, honest.read_bytes()), \
        "a true 24-bit master was accused because of its intro"

    padded = tmp_path / "fake.wav"              # padded throughout
    padded.write_bytes(wav24(400_000, 400_000))
    label, chunks, _ = walk_file(str(padded))
    found = integrity.analyze(label, chunks, padded.read_bytes())
    assert found and "effective 16-bit" in found[0]["verdict"], \
        "genuinely padded audio is no longer detected"
