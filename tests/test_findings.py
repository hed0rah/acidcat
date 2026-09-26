"""Finding codes: registered, emitted where they are claimed, and keyed on.

Consumers select a finding by kind and code, never by its message text
(node-v1.md section 9). These tests hold the three things that makes true:
every code a walker or the forensic scan emits is in the registry, the
registry names no code nothing emits, and the consumers that used to match
text (`forced.py`, the TUI's look-inside) have a code to match instead.
"""

import ast
import pathlib
import re
import struct

import pytest

import seeds
from acidcat.core.forensics import anomalies
from acidcat.core.infra import findings
from acidcat.core.infra.findings import (
    REGISTRY, defect, environment, error, info,
)
from acidcat.core.infra.limits import CODES as CAP_CODES
from acidcat.core.primitives.notes import COVERAGE, DEFECT, ENVIRONMENT, Note

SRC = pathlib.Path(__file__).parent.parent / "src" / "acidcat"
_HELPERS = {"defect", "environment", "info", "error", "coded"}


def _emitted():
    """(code, where) for every helper call with a literal code in the source."""
    out = []
    for py in SRC.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and getattr(n.func, "id", None) in _HELPERS
                    and n.args and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)):
                out.append((n.args[0].value, f"{py.relative_to(SRC)}:{n.lineno}"))
    return out


def test_the_enumerator_still_finds_emit_sites():
    """Guards the guard: a scan that finds nothing passes everything."""
    assert len(_emitted()) > 80


def test_every_emitted_code_is_registered_with_the_right_kind():
    bad = []
    for n_code, where in _emitted():
        if n_code not in REGISTRY:
            bad.append(f"{where}: {n_code} is not registered")
    assert not bad, "\n".join(bad)


def test_the_registry_names_no_code_nothing_emits():
    """The other direction: a code no site emits is a promise nothing keeps."""
    emitted = {c for c, _w in _emitted()}
    derived = ({"legacy", "anomaly.check_failed"} | set(CAP_CODES.values())
               | {findings.anomaly_code(r) for r in findings.ANOMALY_RULES})
    ghosts = sorted(set(REGISTRY) - emitted - derived)
    assert not ghosts, f"registered codes nothing emits: {ghosts}"


def test_every_code_is_well_formed():
    pattern = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
    assert all(pattern.match(c) for c in REGISTRY)
    assert {k for k, _s, _d in REGISTRY.values()} <= {
        "defect", "coverage", "environment", "info", "error"}
    assert {s for _k, s, _d in REGISTRY.values()} <= {"alert", "warn", "notice", "info"}


class TestTheHelpers:
    def test_a_helper_gives_a_note_with_its_kind_and_code(self):
        n = defect("size.overrun", "chunk runs past the file")
        assert n == "chunk runs past the file"
        assert (n.kind, n.code) == (DEFECT, "size.overrun")
        e = environment("sibling.missing", "lib.psflib is not beside it")
        assert (e.kind, e.code) == (ENVIRONMENT, "sibling.missing")

    def test_an_unregistered_code_is_refused(self):
        with pytest.raises(ValueError):
            defect("size.overflow", "x")

    def test_a_code_used_with_the_wrong_kind_is_refused(self):
        with pytest.raises(ValueError):
            defect("sibling.missing", "x")
        with pytest.raises(ValueError):
            info("walker.error", "x")
        with pytest.raises(ValueError):
            error("triage.generic", "x")

    def test_the_code_survives_a_copy(self):
        import copy
        import pickle
        n = defect("magic.mismatch", "missing 'caff' magic")
        for m in (copy.copy(n), copy.deepcopy(n), pickle.loads(pickle.dumps(n))):
            assert (m.kind, m.code) == (DEFECT, "magic.mismatch")


# ── no plain-string warning is left ───────────────────────────────────

# a list a walker or a format decoder collects its warnings in; "notes" is a
# field's display notes, which are not findings
_WARN_LIST = re.compile(r"(warn|warning|cw|problems?)", re.I)
_CODED = _HELPERS | {"coded"}
# warning lists too short a name for the pattern (voc's and au's `w`, spc's `xw`)
_SHORT_LISTS = {"w", "xw"}

def _is_text(n):
    if isinstance(n, ast.Constant):
        return isinstance(n.value, str)
    if isinstance(n, ast.JoinedStr):
        return True
    if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Mod, ast.Add)):
        return _is_text(n.left)
    return False


def _is_coded(n):
    return isinstance(n, ast.Call) and getattr(n.func, "id", None) in _CODED


def _warning_sites():
    """(where, message source, coded) for every warning written as text in
    core/walk and core/formats: appended or extended onto a warning list,
    returned as the last element of a tuple, or the value of a "warnings"
    key. A coded one is wrapped in a findings helper; a plain one reaches a
    Document as `code: legacy`."""
    out = []

    def texts(node):
        if isinstance(node, (ast.List, ast.Tuple)):
            return list(node.elts)
        return [node]

    for sub in ("walk", "formats"):
        for py in sorted((SRC / "core" / sub).glob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            cands = []
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr in ("append", "extend", "insert") and n.args
                        and (_WARN_LIST.search(ast.unparse(n.func.value))
                             or ast.unparse(n.func.value) in _SHORT_LISTS)):
                    cands += texts(n.args[-1])
                elif (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add)
                        and isinstance(n.right, ast.List)):
                    cands += n.right.elts          # warns + ["..."]
                elif isinstance(n, ast.Assign) and _WARN_LIST.search(
                        " ".join(ast.unparse(t) for t in n.targets)):
                    if isinstance(n.value, (ast.List, ast.Tuple)):
                        cands += n.value.elts
                elif isinstance(n, ast.Dict):
                    for k, v in zip(n.keys, n.values):
                        if isinstance(k, ast.Constant) and k.value in ("warnings", "warns"):
                            if isinstance(v, (ast.List, ast.Tuple)):
                                cands += v.elts
                elif (isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)
                        and n.value.elts and isinstance(n.value.elts[-1], ast.List)):
                    cands += n.value.elts[-1].elts
            seen = set()
            for c in cands:
                if id(c) in seen:
                    continue
                seen.add(id(c))
                if _is_coded(c) or _is_text(c):
                    msg = ast.unparse(c.args[1] if _is_coded(c) else c)
                    out.append((f"{sub}/{py.name}:{c.lineno}", msg, _is_coded(c)))
    return out


def test_the_enumerator_still_finds_warning_sites():
    """Guards the guard: it has to be finding the coded sites to be trusted
    about the plain ones."""
    sites = _warning_sites()
    assert sum(1 for _w, _m, coded in sites if coded) > 400


def test_every_walker_warning_has_a_code():
    """2.0 gave every walker and format-decoder warning a code (milestone 3);
    a new one is written with a findings helper, so none reaches a Document
    as `legacy`."""
    plain = [f"{w}: {m[:70]}" for w, m, coded in _warning_sites() if not coded]
    assert not plain, ("plain-string warnings; wrap each in a findings "
                       "helper (acidcat.core.infra.findings):\n  "
                       + "\n  ".join(plain))


def test_no_seed_walks_to_a_legacy_finding(tmp_path):
    """Every registered seed, walked into a Document: no finding is `legacy`."""
    from acidcat.core.infra import contract
    bad = []
    for fmt in sorted(seeds.SEEDS):
        p = tmp_path / ("seed" + seeds.suffix(fmt))
        p.write_bytes(seeds.build(fmt))
        for f in contract.walk(str(p))["findings"]:
            if f["code"] == "legacy":
                bad.append(f"{fmt}: {f['message']}")
    assert not bad, "\n".join(bad)


# ── the consumers ─────────────────────────────────────────────────────

def _wav(path):
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", 8) + b"\x00" * 8)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


def test_every_forensic_finding_carries_a_registered_code(tmp_path):
    p = tmp_path / "t.wav"
    p.write_bytes(open(_wav(tmp_path / "a.wav"), "rb").read() + b"PK\x03\x04" + bytes(64))
    notes = [defect("size.overrun", "x"), "plain", environment("sibling.missing", "y"),
             Note("z", COVERAGE, cap={"name": "list_rows", "limit": 1, "used": 2},
                  code="cap.list")]
    out = anomalies.scan(str(p), "WAV", [], notes)
    assert out
    assert all(f["code"] in REGISTRY for f in out), [f["code"] for f in out]
    by_msg = {f["message"]: f for f in out}
    assert by_msg["x"]["rule"] == "structure" and by_msg["x"]["code"] == "size.overrun"
    assert by_msg["plain"]["code"] == "legacy"
    assert by_msg["y"]["rule"] == "environment"
    assert by_msg["z"]["rule"] == "coverage"


def test_an_environment_finding_does_not_fail_an_audit():
    """A library not beside the file says nothing about the file's bytes."""
    from acidcat.commands.audit import _code
    env = {"rule": "environment", "severity": "notice", "message": "m",
           "offset": 0, "code": "sibling.missing"}
    assert _code(scanned=True, vios=[], findings=[env], integ=[]) == 0


def test_forced_picks_the_wrong_format_complaint_by_code(tmp_path):
    """forced.py matched message words ("magic", "spec says"); it keys on
    codes now, so rewording a message cannot change what it reports."""
    from acidcat.core.forensics import forced
    p = tmp_path / "blob.bin"
    p.write_bytes(bytes(range(256)) * 8)
    rows = forced._forced_candidates(str(p), False)
    assert rows
    for r in rows:
        if r["complaint"]:
            assert isinstance(r["complaint"], str)
    caf = [r for r in rows if r["format"] == "caf"]
    assert caf and "magic" in caf[0]["complaint"], caf


def test_the_generic_triage_note_is_found_by_code(tmp_path):
    """The TUI's look-inside hides the triage preamble; it matched the
    message's first words and now matches its code."""
    from acidcat.core.forensics import triage
    p = tmp_path / "blob.bin"
    p.write_bytes(seeds.build("unknown-container"))
    _label, _chunks, warns = triage.generic_walk(str(p))
    assert [w for w in warns if getattr(w, "code", None) == "triage.generic"]


def test_a_sandboxed_walk_keeps_each_note_kind_and_code(tmp_path):
    """The sandbox returns its walk as JSON, which used to turn every note
    back into a plain string: a missing sibling or a cap hit came back a
    defect."""
    from acidcat.core.infra import sandbox
    if not sandbox.available("limits"):
        pytest.skip("the limits sandbox needs os.fork")
    p = tmp_path / ("seed" + seeds.suffix("psf"))
    p.write_bytes(seeds.build("psf"))
    _label, _chunks, warns = sandbox.run_walk(str(p), False, profile="limits")
    env = [w for w in warns if getattr(w, "code", None) == "sibling.missing"]
    assert env and env[0].kind == "environment", [repr(w) for w in warns]
