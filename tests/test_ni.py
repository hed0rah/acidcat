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
    frames = [c for c in chunks if c["id"].startswith(("hsin", "RTKR", "DSIN"))]
    assert len(frames) == 3
    rtkr = frames[1]
    assert rtkr["offset"] == data.index(b"RTKR") - 4
    assert {f["name"]: f["value"] for f in rtkr["fields"]}["ref_domain"] == "RTKR"
    assert any(w.code == "decode.partial" and "SoundInfoItem" in w for w in warns)
    doc = contract.walk(BytesSource(data, name="x.ens"))
    assert doc["format"]["id"] == "ni"
    # the headers, the item records and every record's own data are nodes:
    # the 40 bytes of item 115 and item 5's empty data leave nothing unread
    gaps = [n for n in contract.iter_nodes(doc) if n["kind"] in ("unwalked", "padding")]
    assert gaps == []
    own = {c["id"]: c for c in chunks if c["id"].startswith("item ")}
    assert own["item 115"]["extent_len"] == 40           # its own data, whole


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


# ── the item stack, and the items' own data ──

def _frames(data):
    return ni.read_frames(lambda o, n: data[o:o + n], 0, len(data), 64)


def test_a_nested_record_size_counts_its_own_eight_bytes():
    """The u64 after an item record is the size of the record nested in it,
    those 8 bytes included, and the nested record ends before its parent:
    the bytes between are the parent's own data. Read as the size of what
    follows it, the SubtreeItem's data began 8 bytes late, inside its
    uncompressed size, and every item's data was left unwalked."""
    import seeds
    data = seeds.ni_hsin_preset()
    frames, problem = _frames(data)
    assert problem is None
    enc = next(f for f in frames if f["items"][0]["item_id"] == 116)
    recs = enc["items"]
    assert [r["item_id"] for r in recs] == [116, 115, 1]
    assert all(fr["stack_problem"] is None for fr in frames)
    base = enc["at"] + ni.FRAME_HEADER
    assert recs[0]["size_at"] == base + 12 and recs[1]["size_at"] == base + 32
    # item 1's data is the u32 1, then the SubtreeItem's: version, compressed,
    # the two sizes, the stream; the EncryptionItem's own 13 bytes end it
    assert recs[2]["own"] == (base + 52, base + 56)
    s, e = recs[1]["own"]
    assert s == base + 56 and data[s:s + 5] == b"\x01\x00\x00\x00\x01"
    assert recs[0]["own"] == (e, base + enc["data_size"])
    assert recs[0]["own"][1] - recs[0]["own"][0] == 13


def test_a_massive_preset_walks_whole_its_subtree_a_layer():
    """Every byte of a Massive-shaped preset is a node: the frame headers,
    the item records and every record's own data, decoded; the subtree's
    FastLZ stream opens to a layer holding the inner tree and the synth's
    state."""
    import seeds
    from acidcat.core.infra import contract
    from acidcat.core.infra.source import BytesSource
    from acidcat.core.walk import walk_bytes
    data = seeds.ni_hsin_preset(name="Glass Pad", author="Nobody",
                                bank=("Massive", "Seeds Vol 1"),
                                attributes=("Synth Pad", "Warm"),
                                properties={"\\@tempo": "42F00000"})
    _label, chunks, _warns = walk_bytes(data, suffix=".nmsv")
    root = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert root["name"] == "Glass Pad" and root["product"] == "Massive"
    assert root["bank"] == "Massive / Seeds Vol 1"
    assert root["attributes"] == "Synth Pad, Warm"
    assert root["tempo"] == "120" and root["version"] == "1.0.0.0"
    assert root["device_type"] == "Instrument"
    doc = contract.walk(BytesSource(data, name="x.nmsv"))
    nodes = list(contract.iter_nodes(doc))
    assert not [n for n in nodes if n["kind"] in ("unwalked", "padding")]
    assert [lay["name"] for lay in doc["layers"]] == ["file", "hsin subtree"]
    inner = [n for n in nodes if n.get("extent", {}).get("layer") == 1]
    assert any(n["name"] == "item 109" for n in inner)
    sub = next(c for c in chunks if c["id"] == "item 115")
    vals = {f["name"]: f["value"] for f in sub["fields"]}
    assert vals["compressed"] == 1
    assert vals["uncompressed_size"] == doc["layers"][1]["length"]


def test_the_soundinfo_item_reads_its_bank_attributes_and_properties():
    import seeds
    data = seeds.ni_hsin(name="Wob", bank=("Absynth", "Seeds FX"),
                         attributes=("Delay", "Reverb"), flags=2,
                         properties={"\\@devicetypeflags": "2",
                                     "\\@tempo": "42F00000"})
    m = ni.parse_hsin(data)
    assert m["name"] == "Wob" and m["product"] == "Absynth"
    assert m["bankchain"] == ["Absynth", "Seeds FX"]
    assert m["attributes"] == ["Delay", "Reverb"]
    assert m["device_type"] == "Effect" and m["tempo"] == "120"
    # an unset tempo is "0", not a tempo
    assert "tempo" not in ni.parse_hsin(seeds.ni_hsin())


def test_controller_pages_name_their_controls():
    """ControllerAssignments: pages of named slots, each a label, a range and
    the synth parameter it drives, the knobs a preset puts on the panel."""
    import seeds
    from acidcat.core.walk import walk_bytes

    def slot(label, param, pos):
        return (struct.pack("<I", 1) + seeds._u16(label)
                + struct.pack("<ffII", 0.0, 1.0, param, pos))
    page = (struct.pack("<II", 1, 2) + seeds._u16("Macros")
            + slot("Cutoff", 7, 1) + slot("Drive", 9, 2))
    own = struct.pack("<III", 1, 1, 1) + page + struct.pack("<II", 1, 0)
    data = seeds._hsin_frame(118, children=[(b"DSIN", seeds._hsin_frame(121, own))])
    fields, meta, why = ni.decode_item(121, own)
    assert why is None
    (_at, _n, name, controls), = meta["pages"]
    assert name == "Macros"
    assert controls == [("Cutoff", 0.0, 1.0, 7, 1), ("Drive", 0.0, 1.0, 9, 2)]
    _label, chunks, _w = walk_bytes(data, suffix=".nmsv")
    c = next(c for c in chunks if c["id"] == "item 121")
    assert c["summary"] == "ControllerAssignments: 1 page(s)"
    assert [r["detail"] for r in c["rows"]] == ["1: Cutoff -> parameter 7",
                                               "2: Drive -> parameter 9"]


def test_an_item_whose_layout_stops_matching_says_so():
    import seeds
    from acidcat.core.walk import walk_bytes
    info = seeds.hsin_info_payload()[:40]          # cut inside the strings
    data = seeds._hsin_frame(118, children=[(b"DSIN", seeds._hsin_frame(108, info))])
    _label, chunks, _w = walk_bytes(data, suffix=".nmsv")
    c = next(c for c in chunks if c["id"] == "item 108")
    assert c["extent_len"] == 40
    assert any(w.code == "layout.unmeasured" for w in c["warnings"])


def test_an_item_stack_past_its_bound_is_a_verdict(monkeypatch):
    """_STACK_CAP: records nested deeper than it are reported on the frame,
    and the bytes past them stay unwalked."""
    import seeds
    monkeypatch.setattr(ni, "_STACK_CAP", 1)
    frames, _problem = _frames(seeds.ni_hsin())
    assert "nested item records" in frames[0]["stack_problem"]
    assert [r["item_id"] for r in frames[0]["items"]] == [118]


def _reaktor_ensemble(snapshot="Init", plain=b""):
    """A tree the way a Reaktor ensemble writes one: no SoundInfoItem at the
    top, a module item holding the path it was saved from and a version-like
    string as UTF-16 pascal strings, and one SoundInfoItem per snapshot five
    frames down. `plain` is put in a SubtreeItem stored as it is."""
    import seeds
    path = seeds._u16("D:\\Ensembles\\Seed.ens")
    module = seeds._hsin_frame(104, struct.pack("<IIQ", 1, 1, 8 + len(path))
                               + b"\x3a" + bytes(7) + path)
    snap = seeds._hsin_frame(101, struct.pack("<IBII", 1, 0, 9, 1)
                             + seeds._u16("6.0.3.16") + struct.pack("<II", 1, 3),
                             children=[(b"DSIN", seeds._hsin_frame(
                                 108, seeds.hsin_info_payload(
                                     snapshot, "", "", bank=("Reaktor", "Seeds"))))])
    deep = snap
    for iid in (103, 2, 1, 4):
        deep = seeds._hsin_frame(iid, struct.pack("<III", 1, 0, 0),
                                 children=[(b"RTKR", deep)])
    kids = [(b"RTKR", module), (b"RTKR", deep)]
    if plain:
        kids.insert(0, (b"DSIN", seeds._hsin_frame(115, struct.pack("<IB", 1, 0) + plain)))
    return seeds._hsin_frame(118, children=[(b"RTKR", seeds._hsin_frame(5, children=kids))])


def test_a_reaktor_ensemble_has_no_name_only_snapshots():
    """124 of 223 real Reaktor ensembles read a "name" that was not theirs:
    the string scan, run when the editor's walk refused the RTKR domain or a
    16 MB read cut the tree, took the path a file was saved from, `Panel|
    Structure`, or a byte run for the name, author and vendor. A tree that
    reads is now read item by item; an ensemble has no SoundInfoItem at its
    top, and the ones five frames down are its snapshots'."""
    import seeds
    from acidcat.core.catalogue import preset_meta
    from acidcat.core.walk import walk_bytes
    data = _reaktor_ensemble()
    m = ni.parse_hsin(data)
    assert "name" not in m and "author" not in m and "vendor" not in m
    assert m["snapshots"] == ["Init"] and m["version"] == "6.0.3.16"
    # cut by the read before the snapshot: still no name made up
    cut = ni.parse_hsin(data[:data.index("D:".encode("utf-16-le")) + 8])
    assert cut is not None and "name" not in cut
    _label, chunks, warns = walk_bytes(data, suffix=".ens")
    assert chunks[0]["summary"] == "hsin tree: 1 snapshot(s), the first 'Init'"
    assert any("only its snapshots'" in w for w in warns)
    mod = next(c for c in chunks if c["id"] == "item 104")
    assert "Reaktor module opening 3a" in mod["summary"]
    pm = preset_meta.extract(data)
    assert pm["preset_name"] is None and pm["description"] == "1 snapshot: Init"
    # a Massive preset's SoundInfoItem sits right under the root's
    # Authoring Application, and is the preset's
    assert ni.parse_hsin(seeds.ni_hsin_preset(name="Pad"))["name"] == "Pad"


def test_a_subtree_stored_as_it_is_is_walked_in_place():
    """A Reaktor ensemble's SubtreeItem is not compressed (compressed 0): an
    hsin tree follows the five-byte header in the file. Read as the FastLZ
    header, its first bytes were taken for two sizes and the tree, 98% of a
    real ensemble, was one opaque item."""
    import seeds
    from acidcat.core.infra import contract
    from acidcat.core.infra.source import BytesSource
    from acidcat.core.walk import walk_bytes
    inner = seeds._hsin_frame(7, struct.pack("<I", 1))
    data = _reaktor_ensemble(plain=inner)
    _label, chunks, _w = walk_bytes(data, suffix=".ens")
    sub = next(c for c in chunks if c["id"] == "item 115")
    assert sub["payload_len"] == len(inner) and "stored as it is" in sub["summary"]
    at = data.index(inner)
    assert sub["payload_base"] == at
    assert any(c["id"] == "subtree" and c["offset"] == at for c in chunks)
    doc = contract.walk(BytesSource(data, name="x.ens"))
    assert not [n for n in contract.iter_nodes(doc) if n["kind"] == "unwalked"]


def test_device_flags_are_the_category_and_nks_types_are_tags():
    """The SoundInfoItem's device flags (2 on every Absynth effect preset
    measured) are the index's category; an NKS preset's types and
    characters are its tags, kept whole."""
    import seeds
    from acidcat.core.catalogue import preset_meta
    m = preset_meta.extract(seeds.ni_hsin(name="Wob", bank=("Absynth", "Seeds FX"),
                                          attributes=("Delay",), flags=2))
    assert m["category"] == "Effect" and m["tags"] == ["Seeds FX", "Delay"]
    assert preset_meta.extract(seeds.ni_hsin(flags=1, bank=("Massive",)))[
        "category"] == "Instrument"
    mp = (b"\x84\xa4name\xa3Pad\xa9bankchain\x91\xa9Massive X"
          b"\xa5types\x91\x92\xa4Bass\xaaSynth Bass\xaacharacters\x91\xa4Warm")
    nisi = b"NISI" + struct.pack("<II", 4 + len(mp), 1) + mp
    riff = b"RIFF" + struct.pack("<I", 4 + len(nisi)) + b"NIKS" + nisi
    pm = preset_meta.extract(riff)
    assert pm["preset_name"] == "Pad"
    assert pm["tags"][-3:] == ["Bass", "Synth Bass", "Warm"]
