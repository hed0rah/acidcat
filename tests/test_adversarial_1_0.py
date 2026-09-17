"""Regressions for the defects an adversarial review found before 1.0.

Every one of these passed the suite. They are grouped here rather than scattered
because they share a shape worth seeing together: with one exception they are
all a partial or failed answer being presented as a whole, clean one -- the
count that was really a cap, the check that ran and was recorded as skipped, the
directory that was never walked, the crash that exited like a negative result.

The two data-loss ones (convert clobbering a sibling, repair not saying it kept
someone else's backup) are the same failure pointed at the filesystem.
"""

import json
import os
import pathlib
import re
import struct
import subprocess
import sys

import pytest


def _wav(path, pcm=b"\x00\x01" * 200, riff_size=None, extra=b""):
    body = (b"WAVE" + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm + extra)
    size = len(body) if riff_size is None else riff_size
    path.write_bytes(b"RIFF" + struct.pack("<I", size) + body)
    return path


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "acidcat", *args],
                          capture_output=True, text=True, cwd=cwd)


# ── B6: a crash must not look like a negative result ────────────────

def test_an_unexpected_exception_exits_2_not_1(tmp_path):
    """1 means "ran fine, the answer is no". A crash is neither.

    `validate f && ship f` read a traceback as a clean negative and `audit f ||
    quarantine f` quarantined on a bug. grep and diff, which acidcat's exit-code
    contract cites, both use 2 for "could not run".
    """
    f = _wav(tmp_path / "c.wav")
    bad = tmp_path / "r.json"
    bad.write_text('[{"length": 4}]')           # no 'offset'
    r = _run("carve", str(f), "--batch", str(bad), "-o", str(tmp_path / "o"))
    assert r.returncode == 2, (
        f"rc={r.returncode}; a failure to run must be distinguishable from a "
        f"legitimate 'no'\n{r.stderr}")


def test_a_real_negative_still_exits_1(tmp_path):
    """The other half. Widening a crash to 2 is worthless if it also moved the
    ordinary negative, which is what the contract is actually for."""
    f = _wav(tmp_path / "bad.wav", riff_size=999999)
    r = _run("validate", str(f))
    assert r.returncode == 1, f"rc={r.returncode}\n{r.stdout}{r.stderr}"


def test_a_clean_file_still_exits_0(tmp_path):
    r = _run("validate", str(_wav(tmp_path / "c.wav")))
    assert r.returncode == 0, f"rc={r.returncode}\n{r.stdout}{r.stderr}"


# ── F1: hand-written batch records ─────────────────────────────────

def test_malformed_batch_record_is_an_error_not_a_traceback(tmp_path):
    f = _wav(tmp_path / "c.wav")
    bad = tmp_path / "r.json"
    bad.write_text('[{"length": 4}]')
    r = _run("carve", str(f), "--batch", str(bad), "-o", str(tmp_path / "o"))
    assert "Traceback" not in r.stderr, r.stderr
    assert "offset" in r.stderr


def test_batch_record_accepts_a_hex_offset(tmp_path):
    """Every other address acidcat takes accepts hex, and these records are
    documented as hand-writable, so "0x10" raised TypeError deep in a sort."""
    f = _wav(tmp_path / "c.wav")
    rec = tmp_path / "r.json"
    rec.write_text('[{"offset": "0x10", "length": 4}]')
    out = tmp_path / "o"
    r = _run("carve", str(f), "--batch", str(rec), "-o", str(out))
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    assert list(out.iterdir()), "nothing was carved"


# ── B3/B4: validate --deep on MP3 ──────────────────────────────────

def _mp3(path):
    from conftest import CORPUS_MP3_GS as src
    with open(src, "rb") as f:
        path.write_bytes(f.read())
    return path


def test_deep_check_that_passed_is_not_reported_as_never_checked(tmp_path):
    """The check RAN and proved the payload matches its own frame data.

    Recording that as "not a structurally-modeled container" and exiting 2 meant
    `validate --deep f && ship f` could never pass an MP3 -- the headline
    feature of this release, inverted.
    """
    r = _run("validate", "--deep", str(_mp3(tmp_path / "song.mp3")))
    assert r.returncode == 0, f"rc={r.returncode}\n{r.stdout}{r.stderr}"
    assert "no structurally-modeled files" not in r.stdout


def test_deep_walk_of_a_directory_does_not_skip_mp3(tmp_path):
    """Exit 0 over a tree whose MP3s were never opened, with nothing said.

    The same file named directly WAS checked, so the two invocations disagreed
    about what validate does -- the worse half, since a gate that silently
    narrows its own scope reports success for files it never read.
    """
    _mp3(tmp_path / "song.mp3")
    _wav(tmp_path / "c.wav")
    r = _run("validate", "--deep", str(tmp_path))
    assert "song.mp3" in r.stdout, (
        f"the mp3 was not walked at all:\n{r.stdout}{r.stderr}")


def test_deep_still_fails_a_damaged_mp3(tmp_path):
    """Guards the fix: making a clean MP3 pass is worthless if a broken one
    now passes too."""
    p = _mp3(tmp_path / "broken.mp3")
    b = bytearray(p.read_bytes())
    for i in range(len(b) // 3, min(len(b), len(b) // 3 + 4000)):
        b[i] ^= 0xFF                      # shred the middle of the frame run
    p.write_bytes(bytes(b))
    r = _run("validate", "--deep", str(p))
    assert r.returncode == 1, (
        f"a shredded mp3 passed --deep (rc={r.returncode})\n{r.stdout}{r.stderr}")


# ── B5: repair must say when it kept someone else's backup ─────────

def test_repair_says_it_kept_an_existing_backup(tmp_path):
    """commit() returns None both for "made no backup" and "one was already
    there", and repair printed nothing for the second. The docs say repair
    "keeps a _original backup" unconditionally, so silence read as a backup
    having been made -- while the real original was overwritten in place."""
    f = _wav(tmp_path / "b.wav", riff_size=999999)
    keep = tmp_path / "b_original.wav"
    keep.write_bytes(b"IRREPLACEABLE")
    r = _run("repair", str(f))
    assert keep.read_bytes() == b"IRREPLACEABLE", "the pre-existing backup moved"
    assert "existing backup kept" in r.stdout, (
        f"repair rewrote the file in place and said nothing about the "
        f"backup:\n{r.stdout}")


# ── B1: convert must not destroy a file it named itself ────────────

def test_convert_refuses_to_overwrite_a_derived_path(tmp_path):
    """NCW and WAV siblings are ordinary in a Kontakt library, so
    `convert precious.ncw` writing over an unrelated `precious.wav` was
    reachable by running the documented command in a normal directory.

    Driven through util.outpath directly: the guard is the contract, and a real
    .ncw specimen is not in the corpus.
    """
    from acidcat.util import outpath
    victim = tmp_path / "precious.wav"
    victim.write_bytes(b"MASTER")
    assert outpath.refuse_clobber("convert", str(victim)) is not None
    assert outpath.refuse_clobber("convert", str(victim), force=True) is None
    assert outpath.refuse_clobber("convert", str(tmp_path / "nope.wav")) is None


def test_convert_exposes_the_force_escape_hatch():
    r = _run("convert", "--help")
    assert "--force" in r.stdout


# ── B2: a cap is not a count ───────────────────────────────────────

def test_concealment_reports_how_many_exist_not_how_many_it_listed():
    """scan() truncated to _MAX_FINDINGS and summarise() printed len() of the
    already-truncated list, so a catastrophic rip and a light one both said
    "20 concealed sector(s)" -- and audit --json carried only that sentence.

    This is the exact defect class the release is about, in the newest forensic
    check in the tree.
    """
    np = pytest.importorskip("numpy")
    from acidcat.core.forensics import concealment as C

    SF = C.SECTOR_FRAMES
    rng = np.random.default_rng(5)
    n = 400 * SF
    t = np.arange(n) / 44100.0
    x = np.stack([9000 * np.sin(2 * np.pi * 220 * t)
                  + 3000 * np.sin(2 * np.pi * 3300 * t) + rng.normal(0, 1500, n),
                  8000 * np.sin(2 * np.pi * 277 * t)
                  + 2600 * np.sin(2 * np.pi * 4100 * t) + rng.normal(0, 1500, n)],
                 axis=1).astype(np.int16)
    punched = list(range(5, 200, 3))       # isolated, so each is its own finding
    for k in punched:
        x[k * SF:(k + 1) * SF] = 0

    found = C.scan(x)
    assert len(found) > C._MAX_FINDINGS, (
        "the specimen no longer exceeds the cap, so this proves nothing")
    note = C.summarise(found)
    assert f"{len(found)} concealed" in note, note
    assert str(C._MAX_FINDINGS) in note, "the cap that bit is not mentioned"
    assert len(C.listed(found)) == C._MAX_FINDINGS


# ── the read cap: found by measuring the advertised throughput ─────

def _flac(tmp_path, name, mb, rate=44100):
    """A real FLAC via ffmpeg, or a skip. Synthetic bytes cannot exercise this:
    the bug is in how real frame boundaries interact with the scan bound."""
    from conftest import requires_tool
    requires_tool("ffmpeg")
    p = tmp_path / name
    secs = max(1, int(mb * 1024 * 1024 / (rate * 4 * 0.55)))
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"anoisesrc=d={secs}:c=pink:r={rate}:a=0.5",
         "-ac", "2", "-ar", str(rate), "-c:a", "flac", str(p)],
        capture_output=True)
    if r.returncode != 0 or not p.exists():
        pytest.skip("ffmpeg could not build a FLAC here")
    return p


@pytest.mark.slow
def test_a_clean_flac_larger_than_the_read_cap_is_not_reported_damaged(tmp_path):
    """The cap was manufacturing the evidence.

    flac_frames stopped at 64 MB, then tried to validate the frame straddling
    that boundary against two bytes that were the middle of a frame rather than
    its CRC. A pristine 78 MB file reported "1 of 3183 frame(s) fail their
    CRC-16" at an offset 606 bytes short of 64 MiB. Nothing was wrong with the
    file. Surfaced by timing the ~10 MB/s figure in --deep's help text, which
    turned out to be accurate; the file built to measure it was the specimen.
    """
    p = _flac(tmp_path, "big.flac", 70, rate=96000)
    if p.stat().st_size <= 64 * 1024 * 1024:
        pytest.skip("could not build a FLAC past the 64 MB cap")
    r = _run("validate", "--deep", str(p))
    assert r.returncode == 0, (
        f"a pristine FLAC past the cap was reported damaged\n{r.stdout}{r.stderr}")


@pytest.mark.slow
def test_damage_past_the_read_cap_is_found(tmp_path):
    """--deep is documented as costing a full read, and capping it at 64 MB
    contradicted that while leaving the tail of every large file unverified.
    Damage at 70 MB used to come back "all 1 file(s) consistent", exit 0."""
    p = _flac(tmp_path, "big.flac", 70, rate=96000)
    if p.stat().st_size <= 64 * 1024 * 1024:
        pytest.skip("could not build a FLAC past the 64 MB cap")
    b = bytearray(p.read_bytes())
    at = 68 * 1024 * 1024
    for i in range(at, min(at + 64, len(b))):
        b[i] ^= 0xFF
    p.write_bytes(bytes(b))
    r = _run("validate", "--deep", str(p))
    assert r.returncode == 1, (
        f"damage past the cap went unreported\n{r.stdout}{r.stderr}")


def test_a_bounded_scan_still_says_when_it_stopped(tmp_path):
    """validate is uncapped now, but the bound remains for other callers, and
    an unverified frame must never be counted as a failed one."""
    from acidcat.core.forensics import checksums
    p = _flac(tmp_path, "m.flac", 8)
    data = p.read_bytes()
    pos = 4
    while pos + 4 <= len(data):
        hdr = data[pos]
        pos += 4 + int.from_bytes(data[pos + 1:pos + 4], "big")
        if hdr & 0x80:
            break
    r = checksums.flac_frames(data, pos, len(data), cap=1024 * 1024)
    assert r["partial"] is True, "the cap did not bite, so this proves nothing"
    assert r["failed"] == 0, (
        f"a bounded scan invented {r['failed']} CRC failure(s) on a clean file")
    assert r["unverified"] >= 1


# ── the conservation law: nothing present may vanish from the count ──

def test_validate_accounts_for_every_file_in_a_directory(tmp_path):
    """A file handed to validate lands in exactly one bucket, and every bucket
    is reported.

    validate kept its own 14-entry extension tuple, so a walk opened .wav and
    silently passed over .w64, .ogg, .opus, .caf and every tracker and preset
    type, then printed "all N file(s) consistent" with exit 0 -- a CI gate
    giving a clean bill of health to a tree it had not read. Adding .mp3 to that
    tuple fixed one symptom; the cause was keeping a second list at all.

    This asserts the accounting, not the list, so it keeps holding as formats
    are added.
    """
    import re
    from conftest import corpus_path
    planted = 0
    for name in ("gs-16b-2c-44100hz.wav", "gs-16b-2c-44100hz.flac",
                 "gs-16b-2c-44100hz.ogg", "gs-16b-2c-44100hz.aiff"):
        resolved = corpus_path(name)
        src = pathlib.Path(resolved) if resolved else None
        if src is not None:
            (tmp_path / name).write_bytes(src.read_bytes())
            planted += 1
    if planted < 3:
        pytest.skip("not enough corpus formats to plant")
    (tmp_path / "notes.txt").write_text("not audio")
    planted += 1

    r = _run("validate", str(tmp_path))
    out = r.stdout + r.stderr

    checked = int(re.search(r"all (\d+) file\(s\) consistent", out).group(1)) \
        if "consistent" in out else 0
    def _n(pat):
        m = re.search(pat, out)
        return int(m.group(1)) if m else 0
    accounted = (checked + _n(r"(\d+) skipped \(unrecognised")
                 + _n(r"(\d+) not structurally modeled")
                 + _n(r"(\d+) unreadable"))
    assert accounted == planted, (
        f"planted {planted} files, accounted for {accounted}:\n{out}")


def test_validate_does_not_keep_a_private_extension_list():
    """The cause, not the symptom. util/targets.py exists because eight
    commands each grew their own list and they drifted apart."""
    import inspect
    from acidcat.commands import validate
    src = inspect.getsource(validate)
    assert "_targets.expand" in src, "validate stopped using the shared expander"
    assert not re.search(r'_EXTS\s*=\s*\(', src), (
        "validate grew a private extension tuple again")


# ── whose fault is it: the file's, or ours? ─────────────────────────

def test_a_check_that_crashed_does_not_condemn_the_file():
    """"check-failed" means our analyser raised, not that the file is bad.

    It was counted as a finding, and findings drive the exit code, so
    `audit f || quarantine f` quarantined a clean file because acidcat's own
    concealment check hit an exception. That is an infrastructure failure
    charged to the input.

    It still prints as NOT CHECKED -- the gap has to stay visible. Only the
    blame moves.
    """
    from acidcat.commands import audit as A
    integ = [{"check": "concealment", "verdict": "check-failed",
              "detail": "could not run (ValueError); this file was NOT screened"}]
    assert A._real_findings(integ) == [], (
        "a crashed check still counts as a finding about the file")
    assert len(A._skipped_notes(integ)) == 1, (
        "a crashed check vanished entirely; it must still be reported")
    assert A._code(True, [], [], integ) == 0, (
        "a clean file whose check crashed still exits 1")


def test_a_real_finding_still_condemns_the_file():
    """The other half. Widening the not-a-finding class is worthless if it also
    swallows the mismatches audit exists to report."""
    from acidcat.commands import audit as A
    integ = [{"check": "bandwidth", "verdict": "lossy-wall",
              "detail": "content stops dead at 16.0 kHz"}]
    assert len(A._real_findings(integ)) == 1
    assert A._code(True, [], [], integ) == 1


def test_scan_json_says_when_it_stopped_early(tmp_path):
    """The cap note lived only on the CSV path; the --json branch returned
    before it. So the machine-readable face was the one that could not tell a
    complete run from a truncated one."""
    from conftest import corpus_path
    planted = 0
    for name in ("gs-16b-2c-44100hz.wav", "gs-16b-2c-44100hz.flac",
                 "gs-16b-2c-44100hz.mp3"):
        resolved = corpus_path(name)
        src = pathlib.Path(resolved) if resolved else None
        if src is not None:
            (tmp_path / name).write_bytes(src.read_bytes())
            planted += 1
    if planted < 3:
        pytest.skip("corpus formats not present")

    r = _run("scan", str(tmp_path), "-n", "2", "--json")
    assert "stopped at the -n 2 cap" in r.stderr, r.stderr
    json.loads(r.stdout)                     # stdout must stay parseable

    r = _run("scan", str(tmp_path), "-n", "99", "--json")
    assert "stopped at the" not in r.stderr, (
        f"a complete run claimed it stopped early:\n{r.stderr}")
