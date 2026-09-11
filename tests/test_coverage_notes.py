"""A cap we crossed is not a defect the file committed.

The bug this closes, end to end: a walker stops at one of our internal limits
and appends a warning; `anomalies.scan` turns every walker warning into a
`structure` finding; findings drive `audit`'s exit code. So a structurally
perfect file exited 1 for being large, and a script doing
`audit f || quarantine f` quarantined it. That was live across fourteen walker
sites that already announced their caps, which is why the other forty were held
back rather than added.

The fix has to distinguish the two kinds without reading the message text.
Matching prose would make the wording of a human-readable string load-bearing
across a module boundary, which is the same defect fixed elsewhere in 1.0 where
anomaly checks dispatched on a display label. The kind travels on the warning.

The other half matters just as much: a coverage note must still be REPORTED. A
bounded run that says nothing is the defect this release is named for. It is
printed; it just does not blame the file.
"""

import json
import os
import struct
import subprocess
import sys

import pytest

from acidcat.core.forensics import anomalies
from acidcat.core.primitives.notes import (
    COVERAGE, DEFECT, Note, coverage, is_coverage, kind_of,
)


class TestTheNoteItself:
    def test_it_is_a_string_everywhere_it_is_used(self):
        """427 existing warning sites and every consumer treat warnings as
        strings. If a Note is not one, this change is a rewrite."""
        n = coverage("stopped at the cap")
        assert isinstance(n, str)
        assert n == "stopped at the cap"
        assert f"{n}" == "stopped at the cap"
        assert json.loads(json.dumps({"w": n}))["w"] == "stopped at the cap"
        assert sorted([coverage("b"), "a"]) == ["a", "b"]

    def test_a_plain_string_is_a_defect(self):
        """The safe default: an unclassified warning keeps the behaviour it has
        today rather than quietly dropping out of the findings."""
        assert kind_of("size field overruns the file") == DEFECT
        assert not is_coverage("size field overruns the file")

    def test_coverage_is_coverage(self):
        assert kind_of(coverage("x")) == COVERAGE
        assert is_coverage(coverage("x"))

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(ValueError):
            Note("x", "probably-fine")

    def test_the_kind_survives_a_copy(self):
        """Structures get copied and pickled; a Note that silently downgrades
        to a defect on the way through is worse than no kind at all."""
        import copy
        import pickle
        n = coverage("stopped early")
        assert kind_of(copy.copy(n)) == COVERAGE
        assert kind_of(copy.deepcopy(n)) == COVERAGE
        assert kind_of(pickle.loads(pickle.dumps(n))) == COVERAGE

    def test_reformatting_drops_the_kind(self):
        """Documented, not accidental. str operations return plain str, so
        classification has to happen before any reformatting -- which is why
        anomalies.scan classifies before it prefixes the chunk id."""
        n = coverage("stopped early")
        assert kind_of(f"prefix: {n}") == DEFECT
        assert kind_of(n.strip()) == DEFECT


def _wav(path, extra_chunks=b""):
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", 8) + b"\x00" * 8 + extra_chunks)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


class TestScanClassifies:
    def test_a_coverage_warning_does_not_become_a_structure_finding(self, tmp_path):
        p = _wav(tmp_path / "a.wav")
        out = anomalies.scan(p, "WAV", [], [coverage("stopped at the 4-chunk cap")])
        cov = [f for f in out if f["rule"] == "coverage"]
        assert len(cov) == 1
        assert cov[0]["severity"] == "info"
        assert not [f for f in out if f["rule"] == "structure"]

    def test_a_defect_warning_still_becomes_one(self, tmp_path):
        p = _wav(tmp_path / "b.wav")
        out = anomalies.scan(p, "WAV", [], ["size field overruns the file"])
        s = [f for f in out if f["rule"] == "structure"]
        assert len(s) == 1 and s[0]["severity"] == "warn"

    def test_the_coverage_note_is_still_reported(self, tmp_path):
        """Silence would be the defect this release is named for."""
        p = _wav(tmp_path / "c.wav")
        out = anomalies.scan(p, "WAV", [], [coverage("listing the first 40 of 900")])
        assert any("listing the first 40 of 900" in f["message"] for f in out)

    def test_a_chunk_level_coverage_note_keeps_its_kind(self, tmp_path):
        """The chunk path prefixes the id onto the message, which returns a
        plain str -- so it has to classify first. This is the ordering bug the
        implementation note warns about, as a test."""
        p = _wav(tmp_path / "d.wav")
        chunks = [{"id": "data", "offset": 12, "size": 8,
                   "warnings": [coverage("listing the first 10 rows")]}]
        out = anomalies.scan(p, "WAV", chunks, [])
        cov = [f for f in out if f["rule"] == "coverage"]
        assert len(cov) == 1, [f["rule"] for f in out]
        assert "data:" in cov[0]["message"]


class TestAuditExitCode:
    """The behaviour a script actually depends on."""

    def _code(self, findings):
        from acidcat.commands.audit import _code
        return _code(scanned=True, vios=[], findings=findings, integ=[])

    def test_a_capped_walk_of_a_clean_file_exits_zero(self):
        assert self._code([{"rule": "coverage", "severity": "info",
                            "message": "stopped at the cap", "offset": 0}]) == 0

    def test_a_real_finding_still_exits_one(self):
        assert self._code([{"rule": "structure", "severity": "warn",
                            "message": "size overruns", "offset": 0}]) == 1

    def test_coverage_alongside_a_real_finding_still_exits_one(self):
        """The coverage note must not mask a genuine defect."""
        assert self._code([
            {"rule": "coverage", "severity": "info", "message": "cap", "offset": 0},
            {"rule": "structure", "severity": "warn", "message": "bad", "offset": 0},
        ]) == 1

    def test_a_clean_file_still_exits_zero(self):
        assert self._code([]) == 0


class TestTheWalkersActuallyUseIt:
    def test_every_reclassified_site_emits_a_coverage_note(self):
        """Pins that the fourteen sites converted here stayed converted. A
        walker whose cap note reverts to a plain string silently returns to
        failing a clean file."""
        import pathlib
        import re
        root = pathlib.Path(__file__).parent.parent / "src/acidcat/core/walk"
        expected = {
            "ableton.py": 2, "bfdlac.py": 1, "flac.py": 1, "krz.py": 1,
            "midi2.py": 1, "mpc.py": 2, "rmid.py": 1, "rx2.py": 1,
            # 5 since the preset/instrument tree landed: the sample list had one
            # cap and the file cap made two, and the preset, instrument and
            # per-instrument zone listings each added one. A number that
            # rises because a walker reads MORE is the ratchet working.
            "sf2.py": 5, "sigmf.py": 2,
        }
        for fn, n in expected.items():
            src = (root / fn).read_text(encoding="utf-8")
            found = len(re.findall(r"append\(coverage\(", src))
            assert found == n, f"{fn}: expected {n} coverage sites, found {found}"

    @pytest.mark.parametrize("fn", [
        "ableton.py", "bfdlac.py", "flac.py", "krz.py", "midi2.py",
        "mpc.py", "rmid.py", "rx2.py", "sf2.py", "sigmf.py",
    ])
    def test_each_walker_imports_what_it_calls(self, fn):
        import importlib
        mod = importlib.import_module(f"acidcat.core.walk.{fn[:-3]}")
        assert hasattr(mod, "coverage")


def test_a_capped_real_file_exits_zero_through_the_cli(tmp_path):
    """End to end, through the process a script would actually run.

    An SF2 whose sample count crosses the listing cap is structurally fine and
    used to exit 1, so `audit f || quarantine f` quarantined it.
    """
    from acidcat.core.walk import sf2 as sf2mod
    cap = sf2mod._SAMPLE_LIST_CAP

    # A minimal sfbk with more sample headers than the listing cap. It needs
    # BOTH sdta/smpl and pdta/shdr: the walker declines a file missing either,
    # so a fixture with only headers never reaches the cap it is testing.
    n = cap + 5
    shdr = b""
    for i in range(n):
        shdr += (f"s{i}".encode().ljust(20, b"\x00")
                 + struct.pack("<IIIIIBbHH", 0, 8, 2, 6, 44100, 60, 0, 0, 1))
    shdr += b"EOS".ljust(20, b"\x00") + struct.pack("<IIIIIBbHH", 0, 0, 0, 0, 0, 0, 0, 0, 0)

    smpl = b"\x00" * 64
    sdta = (b"LIST" + struct.pack("<I", 4 + 8 + len(smpl)) + b"sdta"
            + b"smpl" + struct.pack("<I", len(smpl)) + smpl)
    pdta = (b"LIST" + struct.pack("<I", 4 + 8 + len(shdr)) + b"pdta"
            + b"shdr" + struct.pack("<I", len(shdr)) + shdr)
    body = b"sfbk" + sdta + pdta
    p = tmp_path / "big.sf2"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

    r = subprocess.run([sys.executable, "-m", "acidcat", "audit", str(p)],
                       capture_output=True, text=True,
                       env=dict(os.environ, PYTHONUTF8="1"))
    combined = r.stdout + r.stderr
    assert "listing the first" in combined, combined[:400]
    assert r.returncode == 0, (
        f"a capped walk of a clean file exited {r.returncode}; "
        f"`audit f || quarantine f` would quarantine it\n{combined[:600]}")
