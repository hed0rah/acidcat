"""Direct tests for core/ni.py: the MessagePack codec, the FastLZ decompressor,
and the hsin walker. These are the riskiest primitives (a decompressor + a codec
+ a recursive tree walker) and previously had no direct coverage."""
import struct

import pytest

from acidcat.core import ni


# ── MessagePack codec ──────────────────────────────────────────────

def test_msgpack_round_trip():
    # loop rather than parametrize so a 70k-char string does not become a
    # 70k-char pytest test id.
    cases = [
        0, 127, 255, 300, 70000, 5_000_000_000,
        -5, -200, -70000, -5_000_000_000,
        3.14, -2.5, True, False, None,
        "hi", "x" * 40, "y" * 300, "z" * 70000,
        [1, 2, "a"], {"k": "v", "n": 300, "neg": -200},
        {"a": [1, {"b": 70000}]},
    ]
    for obj in cases:
        enc = ni._mp_encode(obj)
        dec, pos = ni._mp_decode(enc)
        if isinstance(obj, float):
            assert abs(dec - obj) < 1e-4, obj
        else:
            assert dec == obj, repr(obj)[:40]
        assert pos == len(enc)


def test_msgpack_decodes_real_int_widths():
    # a genuine NI msgpack map can carry uint16/uint32/int8/float64 fields; the
    # decoder must read them (a value >127 previously raised "unsupported type").
    assert ni._mp_decode(b"\xcd\x01\x2c")[0] == 300          # uint16
    assert ni._mp_decode(b"\xce\x00\x01\x00\x00")[0] == 65536  # uint32
    assert ni._mp_decode(b"\xd0\xff")[0] == -1               # int8
    assert abs(ni._mp_decode(b"\xcb" + struct.pack(">d", 1.5))[0] - 1.5) < 1e-9


# ── FastLZ ─────────────────────────────────────────────────────────

def test_fastlz_literal_run():
    # a level-1 literal block: opcode (len-1) followed by that many raw bytes
    assert ni.fastlz_decompress(bytes([4]) + b"hello") == b"hello"


def test_fastlz_bomb_cap():
    # output that would exceed max_out is refused (returns None), not expanded
    assert ni.fastlz_decompress(bytes([4]) + b"hello", max_out=2) is None


# ── hsin walker ────────────────────────────────────────────────────

def test_hsin_walk_depth_guard():
    with pytest.raises(ValueError):
        ni._hsin_walk(b"\x00" * 64, 0, [], depth=200)
