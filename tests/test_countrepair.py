"""COUNT-kind repair: clamp a RIFF table-count that exceeds payload capacity,
and the multi-repairer aggregation (size + count in one pass)."""
import struct

from acidcat.core import constraints as C
from acidcat.core import countrepair


def _wav(*chunks, size=None):
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    data = b"data" + struct.pack("<I", 8) + b"\x00" * 8
    body = b"WAVE" + fmt + data + b"".join(chunks)
    n = size if size is not None else len(body)
    return bytes(b"RIFF" + struct.pack("<I", n) + body)


def _cue(declared, actual):
    payload = struct.pack("<I", declared) + b"\x00" * (actual * 24)
    return b"cue " + struct.pack("<I", len(payload)) + payload


def _smpl(declared_loops, actual_loops):
    # 36-byte header (num_sample_loops at +28) then loops of 24 bytes
    header = b"\x00" * 28 + struct.pack("<I", declared_loops) + b"\x00" * 4
    payload = header + b"\x00" * (actual_loops * 24)
    return b"smpl" + struct.pack("<I", len(payload)) + payload


def test_over_capacity_cue_clamped():
    data = _wav(_cue(declared=9999, actual=3))
    findings = countrepair.analyze(data)
    assert findings and findings[0]["field"] == "num_cue_points"
    assert findings[0]["old"] == 9999 and findings[0]["new"] == 3
    new, _changes = countrepair.repair(data)
    assert struct.unpack_from("<I", new, new.find(b"cue ") + 8)[0] == 3
    assert len(new) == len(data)                    # length-preserving


def test_over_capacity_smpl_loops_clamped():
    data = _wav(_smpl(declared_loops=500, actual_loops=1))
    findings = countrepair.analyze(data)
    assert findings and findings[0]["field"] == "num_sample_loops"
    assert findings[0]["new"] == 1


def test_count_within_capacity_left_alone():
    # a chunk larger than its records (trailing room) is NOT a violation
    data = _wav(_cue(declared=2, actual=5))
    assert countrepair.analyze(data) == []


def test_count_kind_via_framework():
    data = _wav(_cue(declared=100, actual=2))
    report = C.analyze(data)
    v = next(v for v in report.violations if v.kind == "count")
    assert v.stored == 100 and v.computed == 2 and v.repairable


def test_aggregation_size_and_count_in_one_pass():
    # a WAV with BOTH a stale master size and an over-capacity cue
    good = _wav(_cue(declared=1, actual=1))
    broken = bytearray(good)
    struct.pack_into("<I", broken, 4, 9)            # stale master size
    struct.pack_into("<I", broken, good.find(b"cue ") + 8, 999)  # over-capacity count
    new, report = C.repair(bytes(broken))
    kinds = {v.kind for v in report.violations}
    assert kinds == {"size", "count"}               # both repairers ran
    assert C.analyze(new).violations == []          # fully consistent after
