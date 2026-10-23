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


def _reaktor_like_tree():
    """An hsin tree the way a Reaktor 6 ensemble writes one: a root frame
    whose child sits behind an RTKR reference, and no SoundInfoItem
    anywhere, so there is no name, author or vendor to read."""
    import seeds
    inner = seeds._hsin_frame(115, payload=b"\x07" * 40)
    return seeds._hsin_frame(118, children=[(b"RTKR", seeds._hsin_frame(
        5, children=[(b"DSIN", inner)]))])


def test_an_hsin_tree_without_metadata_still_walks():
    """14 of 40 Reaktor ensembles sniffed as `ni` and then inspect refused
    them, "not a recognized Native Instruments preset": the walker raised
    when the tree held no SoundInfoItem. A valid tree is a walk; the frames
    are its structure and the missing metadata is a note, not a refusal."""
    from acidcat.core.infra import contract
    from acidcat.core.infra.source import BytesSource
    from acidcat.core.walk import walk_bytes
    data = _reaktor_like_tree()
    assert not ni.parse_hsin(data)
    label, chunks, warns = walk_bytes(data, suffix=".ens")
    assert label == "Native Instruments preset"
    root = chunks[0]
    assert (root["id"], root["offset"], root["extent_len"]) == ("hsin", 0, len(data))
    names = {f["name"]: f["value"] for f in root["fields"]}
    assert names["frame_size"] == len(data) and names["magic"] == "hsin"
    # every frame of the tree, the reference before a child included
    frames = [c for c in chunks if c["id"] != "items"]
    assert len(frames) == 3
    rtkr = frames[1]
    assert rtkr["offset"] == data.index(b"RTKR") - 4
    assert {f["name"]: f["value"] for f in rtkr["fields"]}["ref_domain"] == "RTKR"
    assert any(w.code == "decode.partial" and "SoundInfoItem" in w for w in warns)
    doc = contract.walk(BytesSource(data, name="x.ens"))
    assert doc["format"]["id"] == "ni"
    # the headers and the item records are described; what is left is the
    # item data (40 bytes) and the 12 uninterpreted bytes ending each stack
    gaps = [n for n in contract.iter_nodes(doc) if n["kind"] in ("unwalked", "padding")]
    assert sum(n["extent"]["len"] for n in gaps) == 40 + 3 * 12


def test_a_reaktor_instrument_sniffs_by_its_class_record():
    """497 Reaktor .ism read as unknown: the sniff looked for hsin at 0x0C,
    and an instrument opens with `NRKT` (or an older 0x3a lead) and the
    KSModul class record, its NI metadata kilobytes before the end."""
    import seeds
    from acidcat.core.infra.sniff import sniff
    from acidcat.core.infra.source import BytesSource
    for old in (False, True):
        data = seeds.ni_reaktor_module(old_layout=old)
        assert sniff(BytesSource(data, name="x.ism")) == "ni", old
    # the class record is the evidence; neither half alone claims a file
    record = b"\x5b" + struct.pack("<I", 7) + b"KSModul"
    for data in (b"NRKT" + bytes(200),                       # no class record
                 b"\x00" * 46 + record + bytes(200),         # not a Reaktor lead
                 b"NRKT" + bytes(200) + record + bytes(64)):  # record too far in
        assert sniff(BytesSource(data, name="x.ism")) is None, data[:8]


def test_a_reaktor_instrument_surfaces_its_trailing_metadata():
    import seeds
    from acidcat.core.infra import contract
    from acidcat.core.infra.source import BytesSource
    from acidcat.core.walk import walk_bytes
    data = seeds.ni_reaktor_module(name="Init Snapshot", author="Nobody")
    frame_at = data.index(b"hsin") - 12
    label, chunks, warns = walk_bytes(data, suffix=".ism")
    assert label == "Native Instruments preset"
    assert [c["id"] for c in chunks[:2]] == ["NRKT", "class"]
    assert chunks[1]["offset"] == 46
    assert "Reaktor instrument" in chunks[0]["summary"]
    meta = next(c for c in chunks if c["offset"] == frame_at and c["id"] == "hsin")
    vals = {f["name"]: f["value"] for f in meta["fields"]}
    assert vals["name"] == "Init Snapshot" and vals["author"] == "Nobody"
    assert vals["frame_size"] == len(seeds.ni_hsin(name="Init Snapshot",
                                                   author="Nobody"))
    from acidcat.util import targets
    assert {".ism", ".ens"} <= targets.KNOWN_EXTS     # a directory walk takes them
    doc = contract.walk(BytesSource(data, name="x.ism"))
    assert doc["format"]["id"] == "ni"
    gaps = [(n["extent"]["off"], n["extent"]["len"])
            for n in doc["nodes"] if n["kind"] == "unwalked"]
    # the header's middle, the module body and Reaktor's tail stay unread
    assert gaps == [(4, 42), (58, 64), (frame_at + meta["extent_len"], 32)]


def test_a_reaktor_instrument_without_a_metadata_frame_still_walks():
    import seeds
    from acidcat.core.walk import walk_bytes
    data = seeds.ni_reaktor_module()
    data = data[:data.index(b"hsin") - 12]
    _label, chunks, warns = walk_bytes(data, suffix=".ism")
    assert [c["id"] for c in chunks] == ["NRKT", "class"]
    assert any("no hsin metadata frame" in w for w in warns)


def test_garbage_behind_the_hsin_magic_is_still_refused():
    from acidcat.core.walk import Unsupported, walk_bytes
    data = bytearray(_reaktor_like_tree())
    data[0:8] = (0x20).to_bytes(8, "little")     # a frame smaller than its header
    with pytest.raises(Unsupported, match="not a recognized"):
        walk_bytes(bytes(data), suffix=".ens")
