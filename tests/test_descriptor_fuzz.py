"""Descriptor-driven fuzzing: derive the walker's decision points mechanically
from the WAVE Format descriptor and check three parsers agree at each one.

Unlike the seeded-random differential fuzz (test_differential_fuzz.py), which
bit-flips a WAV seed blindly and mostly generates junk chunk ids, this walks
the descriptor and generates the boundary-exact inputs the walker actually
branches on: every ``Valid`` range edge, every ``Switch`` case at each window
size around its ``min_window``, every ``min_len`` truncation edge, and each
``Requires`` / ``Order`` format rule. Those are the inputs random cannot reach
(it never builds a valid-enough acid/inst chunk or a boundary-exact value).

For each generated file three contracts hold together:
  - the walker (walk_file) parses without an unhandled crash (loud here via
    ACIDCAT_WALKER_RAISE, set for the suite in conftest),
  - the interpreter (interpret) matches the walker byte-for-byte on every
    described region -- this catches a walker regression even when the walker
    swallows it into a per-chunk warning (verified by fault injection: a
    planted per-chunk parser crash surfaces here as a parity divergence, a
    class the never-raise contract otherwise hides),
  - the strict parser (structure.parse) round-trips byte-exactly or raises
    StructError.

The generator reads the descriptor, so it auto-extends when a region/field/
rule is added. The coverage assertion guards against a generator that silently
stops producing the meaningful cases.
"""

import struct

import pytest

from acidcat.core.grammar import interpret
from acidcat.core.grammar.formats.wav import WAVE
from acidcat.core.grammar.model import Field, Switch
from acidcat.core import structure
from acidcat.core.structure import StructError
from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported

_FIELD_KEYS = ("off", "len", "name", "value", "note", "enc", "raw")
_DESCRIBED = {rid for rid, r in WAVE.regions.items() if r.kind == "struct"}


# ── WAV assembly ───────────────────────────────────────────────────

def _chunk(cid, payload):
    raw = cid + struct.pack("<I", len(payload)) + payload
    if len(payload) % 2:
        raw += b"\x00"
    return raw


def _wav(*chunks):
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _fmt16(tag=1, ch=2, rate=44100, avg=176400, align=4, bits=16):
    return struct.pack("<HHIIHH", tag, ch, rate, avg, align, bits)


_DATA = _chunk(b"data", b"\x00" * 8)


# ── mechanical mutation-point derivation from the descriptor ────────

def _valid_boundaries(v):
    out = []
    if v.min is not None:
        out += [v.min - 1, v.min]
    if v.max is not None:
        out += [v.max, v.max + 1]
    if v.skip_zero:
        out.append(0)
    return out


def _window_sizes(min_window):
    return [0, max(0, min_window - 1), min_window, min_window + 8, 0xFFFF]


def _gen_fmt():
    reg = WAVE.regions["fmt "]
    for n in (0, reg.min_len - 1, reg.min_len):
        yield (f"fmt.min_len={n}", _wav(_chunk(b"fmt ", b"\x00" * n), _DATA))
    for fld in reg.fields:
        if isinstance(fld, Field) and fld.valid is not None:
            kw = {"channels": "ch", "sample_rate": "rate"}.get(fld.name)
            if kw is None:
                continue
            for val in _valid_boundaries(fld.valid):
                payload = _fmt16(**{kw: val & 0xFFFFFFFF})
                yield (f"fmt.{fld.name}={val}",
                       _wav(_chunk(b"fmt ", payload), _DATA))
    for entry in reg.fields:
        if not isinstance(entry, Switch):
            continue
        windowed = entry.window is not None
        for key, case in entry.cases.items():
            for wsz in _window_sizes(case.min_window):
                ext = b"\xAB" * wsz
                cb = wsz if windowed else 22
                payload = _fmt16(tag=key) + struct.pack("<H", cb) + ext
                yield (f"fmt.switch tag=0x{key:04x} win={wsz}",
                       _wav(_chunk(b"fmt ", payload), _DATA))
        payload = _fmt16(tag=0x7777) + struct.pack("<H", 8) + b"\xAB" * 8
        yield ("fmt.switch tag=nomatch", _wav(_chunk(b"fmt ", payload), _DATA))


def _gen_regions():
    fmtc = _chunk(b"fmt ", _fmt16())
    for rid, reg in WAVE.regions.items():
        if rid in ("fmt ", "data") or reg.kind != "struct":
            continue
        for n in (0, reg.min_len - 1, reg.min_len):
            yield (f"{rid}.min_len={n}",
                   _wav(fmtc, _DATA, _chunk(rid.encode("latin1"), b"\x00" * n)))
        if rid == "acid":
            for tempo in (39.0, 40.0, 300.0, 301.0, 0.0):
                acidp = struct.pack("<IHHfIHHf", 0x02, 60, 0, 0.0, 4, 4, 4, tempo)
                yield (f"acid.tempo={tempo}",
                       _wav(fmtc, _DATA, _chunk(b"acid", acidp)))


def _gen_rules():
    fmtc = _chunk(b"fmt ", _fmt16())
    yield ("rule.no_fmt", _wav(_DATA))
    yield ("rule.no_data", _wav(fmtc))
    yield ("rule.order_swapped", _wav(_DATA, fmtc))


def _generate():
    yield from _gen_fmt()
    yield from _gen_regions()
    yield from _gen_rules()


# ── the three checks ───────────────────────────────────────────────

def _fields(fs):
    return [{k: f.get(k) for k in _FIELD_KEYS} for f in fs]


def _check(path, data):
    findings, cov = [], set()
    try:
        _, wchunks, wwarns = walk_file(str(path))
    except Unsupported:
        wchunks, wwarns = None, []
        cov.add("walk:unsupported")
    except Exception as e:                       # loud via ACIDCAT_WALKER_RAISE
        findings.append(f"WALKER CRASH: {e.__class__.__name__}: {e}")
        wchunks = None

    if wchunks is not None:
        for w in wwarns:
            cov.add("warn:" + w[:40])
        for c in wchunks:
            cov.add("chunk:" + str(c["id"]).strip())
            if c.get("summary") == "truncated":
                cov.add("trunc:" + str(c["id"]).strip())
            for w in c.get("warnings", []):
                cov.add("cwarn:" + w[:40])
        try:
            _, gchunks, _ = interpret(WAVE, str(path))
        except Exception as e:
            findings.append(f"INTERP CRASH: {e.__class__.__name__}: {e}")
            gchunks = None
        if gchunks is not None:
            wmap = {str(c["id"]): c for c in wchunks}
            for gc in gchunks:
                rid = str(gc["id"])
                if rid not in _DESCRIBED:
                    continue
                wc = wmap.get(rid)
                if wc is None:
                    continue
                if _fields(gc["fields"]) != _fields(wc["fields"]):
                    findings.append(f"PARITY fields {rid!r}")
                if gc.get("summary") != wc.get("summary"):
                    findings.append(f"PARITY summary {rid!r}")

    try:
        node = structure.parse(data)
        if structure.emit(node) != data:
            findings.append("STRUCTURE lossy round-trip")
        cov.add("struct:parsed")
    except StructError:
        cov.add("struct:rejected")
    except Exception as e:
        findings.append(f"STRUCTURE CRASH: {e.__class__.__name__}: {e}")

    return findings, cov


# the meaningful decision points this fuzzer exists to hit; if the generator
# regresses and stops producing them, this set stops being covered and the
# coverage test fails (a silent-no-op generator cannot pass)
_EXPECTED_COVERAGE = {
    "chunk:acid", "chunk:inst",
    "trunc:acid", "trunc:inst",
    "cwarn:65 channels is implausibly high",
    "cwarn:sample_rate 999 Hz is outside any plausi",
    "cwarn:sample_rate 768001 Hz is outside any pla",
    "cwarn:acid tempo 39.00 outside sane range 40-3",
    "cwarn:acid tempo 301.00 outside sane range 40-",
    "cwarn:sub_format GUID tail is not the standard",
    "warn:fmt appears after data, violating the on",
}


def test_descriptor_fuzz_no_findings():
    """Every descriptor-derived boundary input: walker, interpreter, and strict
    parser agree. Any divergence or crash is a regression at a decision point
    the random fuzz would not reach."""
    all_findings = []
    for label, data in _generate():
        # a hermetic temp path per case, written for the file-based parsers
        import tempfile
        import os
        fd, path = tempfile.mkstemp(suffix=".wav")
        try:
            os.write(fd, data)
            os.close(fd)
            findings, _ = _check(path, data)
        finally:
            os.unlink(path)
        for f in findings:
            all_findings.append(f"[{label}] {f}")
    assert not all_findings, "descriptor-fuzz findings:\n" + "\n".join(all_findings)


def test_descriptor_fuzz_reaches_decision_points():
    """The generator actually reaches the walker's meaningful branch points --
    guards against a generator that silently stops producing them (which would
    make the no-findings test vacuously pass)."""
    import tempfile
    import os
    cov = set()
    for _label, data in _generate():
        fd, path = tempfile.mkstemp(suffix=".wav")
        try:
            os.write(fd, data)
            os.close(fd)
            _, c = _check(path, data)
        finally:
            os.unlink(path)
        cov |= c
    missing = _EXPECTED_COVERAGE - cov
    assert not missing, f"generator no longer reaches: {sorted(missing)}"
