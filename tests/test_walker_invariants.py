"""Mechanical enforcement of the walker-fleet contracts.

Three invariants that used to be conventions a human had to re-verify by
reading every walker:

1. Bounded reads: no argless ``.read()`` inside ``core/walk/`` (the class
   behind the historical sf2/rmid memory-amplification bugs).
2. The ctx key registry: every semantic ctx key the fixed-key walkers
   (wav/aiff/midi) publish is in ``vocab.CTX_KEYS``, so a walker rename
   cannot silently desynchronize from the scan path. (The WAV half runs
   corpus-wide in test_grammar_wav.py.)
3. The walk_file degradation boundary: a walker bug degrades to a
   warning in production and re-raises under ACIDCAT_WALKER_RAISE.
"""

import ast
import glob
import os
import struct

import pytest

from acidcat.core import walk as walkmod
from acidcat.core.vocab import CTX_KEYS

WALK_DIR = os.path.dirname(walkmod.__file__)


# ── 1. bounded reads ───────────────────────────────────────────────

def test_no_argless_reads_in_walkers():
    """Every ``.read()`` in core/walk/ must pass an explicit byte count.
    An argless read's allocation is controlled by the file, not the code;
    that is exactly how the sf2 and rmid walkers once amplified a crafted
    file into hundreds of MB of peak memory. A capped read that happens
    to be preceded by a size gate still passes the cap explicitly, so
    this stays checkable without reading each call site in context."""
    offenders = []
    for py in sorted(glob.glob(os.path.join(WALK_DIR, "*.py"))):
        with open(py, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=py)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "read"
                    and not node.args and not node.keywords):
                offenders.append(f"{os.path.basename(py)}:{node.lineno}")
    assert not offenders, (
        f"argless .read() in walkers (unbounded allocation): {offenders}")


# ── 2. ctx keys stay registered ────────────────────────────────────

def _aiff_chunk(cid, payload):
    raw = cid + struct.pack(">I", len(payload)) + payload
    if len(payload) % 2:
        raw += b"\x00"
    return raw


def test_aiff_ctx_keys_registered(tmp_path):
    """An AIFF exercising COMM/MARK/INST/NAME/AUTH/(c)/ANNO/basc publishes
    only registered ctx keys."""
    from acidcat.core.walk.aiff import inspect_aiff
    rate = bytes.fromhex("400eac440000000000000000")[:10]  # 44100.0
    comm = _aiff_chunk(b"COMM", struct.pack(">hIh", 1, 441, 16) + rate)
    mark = _aiff_chunk(b"MARK", struct.pack(">H", 1)
                       + struct.pack(">HI", 1, 100) + b"\x03abc")
    inst = _aiff_chunk(b"INST", bytes([60, 0, 0, 127, 0, 127])
                       + struct.pack(">h", 0)
                       + struct.pack(">HHH", 1, 1, 1)     # sustain loop
                       + struct.pack(">HHH", 0, 0, 0))    # release loop
    basc = _aiff_chunk(b"basc", struct.pack(">IIHHHH", 1, 32, 57, 3, 4, 4)
                       + b"\x00" * 68)
    name = _aiff_chunk(b"NAME", b"loop name")
    auth = _aiff_chunk(b"AUTH", b"author")
    copy = _aiff_chunk(b"(c) ", b"2026")
    anno = _aiff_chunk(b"ANNO", b"note")
    ssnd = _aiff_chunk(b"SSND", struct.pack(">II", 0, 0) + b"\x00" * 8)
    body = b"AIFF" + comm + mark + inst + basc + name + auth + copy + anno + ssnd
    f = tmp_path / "keys.aiff"
    f.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)

    ctx = {}
    inspect_aiff(str(f), "AIFF", ctx=ctx)
    # the fixture must actually exercise the publishers it claims to
    for expected in ("channels", "duration", "marker_ids",
                     "inst_loop_marker_ids", "basc_beats", "name",
                     "author", "copyright", "annotation"):
        assert expected in ctx, f"fixture no longer publishes {expected!r}"
    missing = set(ctx) - set(CTX_KEYS)
    assert not missing, f"aiff walker publishes unregistered ctx keys: {missing}"


def test_midi_ctx_keys_registered(tmp_path):
    """An SMF exercising tempo/name/copyright/key sig/time sig/notes
    publishes only registered ctx keys."""
    from acidcat.core.walk.midi import inspect_midi
    track = (b"\x00\xFF\x51\x03\x07\xA1\x20"      # tempo 120 bpm
             b"\x00\xFF\x03\x04Bass"              # track name
             b"\x00\xFF\x02\x03(c)"               # copyright
             b"\x00\xFF\x58\x04\x04\x02\x18\x08"  # time sig 4/4
             b"\x00\xFF\x59\x02\x00\x01"          # key sig Am
             b"\x87\x40\x90\x3C\x64"              # note on at tick 960
             b"\x00\xFF\x2F\x00")
    hdr = b"MThd" + struct.pack(">IHHH", 6, 1, 1, 480)
    f = tmp_path / "keys.mid"
    f.write_bytes(hdr + b"MTrk" + struct.pack(">I", len(track)) + track)

    ctx = {}
    inspect_midi(str(f), ctx=ctx)
    for expected in ("tempo_bpm", "track_name", "copyright", "key_sig",
                     "time_sig", "note_count", "duration"):
        assert expected in ctx, f"fixture no longer publishes {expected!r}"
    missing = set(ctx) - set(CTX_KEYS)
    assert not missing, f"midi walker publishes unregistered ctx keys: {missing}"


# ── 3. the walk_file degradation boundary ──────────────────────────

def _minimal_wav(tmp_path):
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", 4) + b"\x00" * 4)
    p = tmp_path / "b.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p)


def _boom(path, deep):
    raise RuntimeError("injected walker bug")


def test_walk_file_degrades_on_walker_bug(monkeypatch, tmp_path):
    """In production (no env var) a walker bug must degrade to a warning,
    not crash the consumer: this is the never-raise contract, enforced at
    the one boundary every consumer shares."""
    monkeypatch.delenv("ACIDCAT_WALKER_RAISE", raising=False)
    monkeypatch.setitem(walkmod._WALKERS, "wav", ("RIFF/WAVE", _boom))
    label, chunks, warns = walkmod.walk_file(_minimal_wav(tmp_path))
    assert label == "RIFF/WAVE"
    assert chunks == []
    assert any("walker error (wav): RuntimeError: injected walker bug" in w
               for w in warns)


def test_walk_file_reraises_under_test_env(monkeypatch, tmp_path):
    """With ACIDCAT_WALKER_RAISE set (the suite default via conftest) the
    same bug stays a loud traceback, so CI never hides a walker defect
    behind the production degradation."""
    monkeypatch.setenv("ACIDCAT_WALKER_RAISE", "1")
    monkeypatch.setitem(walkmod._WALKERS, "wav", ("RIFF/WAVE", _boom))
    with pytest.raises(RuntimeError, match="injected walker bug"):
        walkmod.walk_file(_minimal_wav(tmp_path))


def test_walk_file_unsupported_still_raises(monkeypatch, tmp_path):
    """Unsupported is control flow, not a walker bug: it must pass through
    the boundary untouched in both modes."""
    monkeypatch.delenv("ACIDCAT_WALKER_RAISE", raising=False)
    p = tmp_path / "junk.bin"
    p.write_bytes(b"\x01\x02\x03\x04" * 8)
    with pytest.raises(walkmod.Unsupported):
        walkmod.walk_file(str(p))
