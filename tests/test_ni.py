"""Direct tests for core/ni.py: the MessagePack codec, the FastLZ decompressor,
and the hsin walker. These are the riskiest primitives (a decompressor + a codec
+ a recursive tree walker) and previously had no direct coverage."""
import struct

import pytest

from acidcat.core.formats import ni


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


def test_msgpack_forged_count_rejected():
    # array/map headers claiming more elements than the remaining payload could
    # possibly hold must raise, not iterate: _mp_decode stops advancing at EOF,
    # so an unchecked count of ~4 billion is a multi-minute hang plus an
    # OOM-sized output list.
    for payload in (b"\xdd\xff\xff\xff\xff",     # array32, count 2^32-1, no body
                    b"\xdf\xff\xff\xff\xff",     # map32
                    b"\xdc\xff\xff",             # array16
                    b"\xde\xff\xff",             # map16
                    b"\x9f\x01\x01\x01"):        # fixarray of 15, 3 bytes left
        with pytest.raises(ValueError):
            ni._mp_decode(payload)


def test_parse_nksf_forged_count_degrades():
    # the same forged header inside a real RIFF/NIKS container: parse_nksf must
    # return None promptly (this is also the unattended `acidcat index` path)
    nisi = struct.pack("<I", 1) + b"\xdd\xff\xff\xff\xff"
    body = (b"NIKS" + b"NISI" + struct.pack("<I", len(nisi)) + nisi
            + (b"\x00" if len(nisi) & 1 else b""))
    data = b"RIFF" + struct.pack("<I", len(body)) + body
    assert ni.parse_nksf(data) is None


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


# Level 2 (Kontakt 4.2 patch bodies): the top three bits of the first byte are
# 001. The streams below decode differently, or not at all, as level 1.

def test_fastlz_level2_short_match():
    # literal "ABCDE", then a 3-byte match 4 back
    assert ni.fastlz_decompress(b"\x24ABCDE\x20\x04") == b"ABCDEABC"


def test_fastlz_level2_long_length_continues_past_255():
    # a length-7 code extends with 255 and then 5: 7 + 255 + 5 + 2 = 269,
    # copied from one byte back, so the copy overlaps what it writes
    assert ni.fastlz_decompress(b"\x20A\xe0\xff\x05\x00") == b"A" * 270


def test_fastlz_level2_far_distance():
    # distance code 31/255 means a 16-bit distance follows, counted past 8191
    data = bytes(range(256)) * 32 + b"12345678"             # 8,200 bytes
    runs = b"".join(bytes([len(data[i:i + 32]) - 1]) + data[i:i + 32]
                    for i in range(0, len(data), 32))
    stream = bytes([runs[0] | 0x20]) + runs[1:] + b"\x3f\xff\x00\x00"
    assert ni.fastlz_decompress(stream) == data + data[8:11]


def test_fastlz_long_overlapping_run_is_fast():
    # a distance-1 match repeating one byte ~16 MB: built as a repeat, not a
    # byte-at-a-time loop
    import time
    n = 16 * 1024 * 1024 // 255
    stream = b"\x20A\xe0" + b"\xff" * n + b"\x00\x00"
    t = time.perf_counter()
    out = ni.fastlz_decompress(stream, max_out=64 * 1024 * 1024)
    assert time.perf_counter() - t < 2.0
    assert out == b"A" * len(out) and len(out) > 16_000_000


def test_decompress_subtree_bounds_the_work_across_candidates(monkeypatch):
    # many wrong candidates, each claiming a big output: the total is bounded
    calls = []
    real = ni.fastlz_decompress

    def counting(src, max_out=0):
        calls.append(max_out)
        return real(src, max_out)
    monkeypatch.setattr(ni, "fastlz_decompress", counting)
    # each body really inflates to about 1 MB (a level-2 run), and each claims
    # 2 MB, so every candidate is wrong after doing real work
    body = b"\x20A\xe0" + b"\xff" * 4100 + b"\x00\x00"
    cand = (b"\x01\x00\x00\x00\x01" + struct.pack("<II", 2 << 20, len(body)) + body)
    data = b"\x00" * 12 + b"hsin" + b"\x00" * 32 + cand * 64
    assert ni.decompress_subtree(data, total=3 << 20) is None
    assert len(calls) <= 3


def test_fastlz_level1_is_unchanged_by_level2_support():
    # the same bytes as the long-length case, read as level 1, stop early
    out = ni.fastlz_decompress(b"\x00A\xe0\xff\x05\x00")
    assert out != b"A" * 270


# ── hsin walker ────────────────────────────────────────────────────

def test_hsin_walk_depth_guard():
    with pytest.raises(ValueError):
        ni._hsin_walk(b"\x00" * 64, 0, [], depth=200)


def test_a_long_hsin_value_reads_back_whole():
    """The editor writes a SoundInfoItem string of up to 65,536 units; the
    reader's scan capped one at 256 to keep noise out. A 500-character
    description was written and never shown, and the scan then took a run of
    the item's own bytes for the name. The reader now locates the item
    through the same frame walk the editor uses."""
    import seeds
    data = seeds.ni_hsin(name="Pizz", author="", vendor="Seeds")
    out, _ = ni.edit_hsin(data, {"description": "x" * 500})
    meta = ni.parse_hsin(out)
    assert meta["description"] == "x" * 500
    assert meta["name"] == "Pizz"
    out, _ = ni.edit_hsin(data, {"name": "n" * 300})
    assert ni.parse_hsin(out)["name"] == "n" * 300


def test_a_numeric_hsin_name_is_the_name():
    """The scan wanted an alphabetic name, so a preset called `120` reported
    its author as its name (three Absynth factory presets did)."""
    import seeds
    meta = ni.parse_hsin(seeds.ni_hsin(name="120", author="Paradox"))
    assert meta["name"] == "120" and meta["author"] == "Paradox"


def test_an_hsin_value_past_the_count_field_is_refused():
    import seeds
    with pytest.raises(ni.NotHeld, match="at most"):
        ni.edit_hsin(seeds.ni_hsin(), {"description": "x" * 0x10001})
