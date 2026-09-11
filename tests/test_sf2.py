"""SoundFont 2 parsing + sample extraction, on a synthetic ground-truth sfbk."""
import struct

import pytest

from acidcat.core.formats import sf2


def _riff(form, *chunks):
    body = form + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _chunk(cid, payload):
    return cid + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")


def _list(ltype, *chunks):
    body = ltype + b"".join(chunks)
    return b"LIST" + struct.pack("<I", len(body)) + body


def _shdr_rec(name, start, end, ls, le, rate, pitch=60, stype=1):
    return (name.encode("latin-1").ljust(20, b"\x00")[:20]
            + struct.pack("<IIIII", start, end, ls, le, rate)
            + struct.pack("<BbHH", pitch, 0, 0, stype))


def _make_sf2(samples, smpl_frames):
    # smpl: `smpl_frames` 16-bit samples; each shdr indexes into it
    smpl = b"".join(struct.pack("<h", (i % 100) - 50) for i in range(smpl_frames))
    info = _list(b"INFO",
                 _chunk(b"ifil", struct.pack("<HH", 2, 1)),
                 _chunk(b"INAM", b"Test Font\x00"),
                 _chunk(b"IENG", b"acidcat\x00"))
    sdta = _list(b"sdta", _chunk(b"smpl", smpl))
    shdr = b"".join(_shdr_rec(*s) for s in samples) + _shdr_rec("EOS", 0, 0, 0, 0, 0)
    pdta = _list(b"pdta", _chunk(b"shdr", shdr))
    return _riff(b"sfbk", info, sdta, pdta)


def test_parse_info_and_samples():
    data = _make_sf2([("Kick", 0, 100, 10, 90, 44100),
                      ("Snare", 150, 400, 160, 390, 22050, 64, 4)], 500)
    r = sf2.parse_sf2(data)
    assert r["version"] == "2.1"
    assert r["info"]["name"] == "Test Font" and r["info"]["engineer"] == "acidcat"
    assert r["sample_count"] == 2
    k = r["samples"][0]
    assert k["name"] == "Kick" and k["rate"] == 44100 and k["start"] == 0 and k["end"] == 100
    assert r["samples"][1]["type"] == 4       # left channel


def test_sample_wav_carves_correct_pcm():
    data = _make_sf2([("Kick", 10, 60, 0, 0, 44100)], 500)
    r = sf2.parse_sf2(data)
    wav = sf2.sample_wav(data, r["smpl_offset"], r["samples"][0])
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    # the data chunk is exactly (end-start)*2 bytes and equals smpl[10*2:60*2]
    di = wav.find(b"data")
    dsize = struct.unpack_from("<I", wav, di + 4)[0]
    assert dsize == (60 - 10) * 2
    carved = wav[di + 8: di + 8 + dsize]
    smpl = data[r["smpl_offset"]: r["smpl_offset"] + r["smpl_size"]]
    assert carved == smpl[10 * 2: 60 * 2]
    # the WAV's fmt rate matches the sample
    tag, ch, rate, _br, _ba, bits = struct.unpack_from("<HHIIHH", wav, 20)
    assert tag == 1 and ch == 1 and rate == 44100 and bits == 16


def test_lying_shdr_index_skipped():
    # a header whose end index runs past the sample data must not carve garbage
    data = _make_sf2([("Good", 0, 50, 0, 0, 44100),
                      ("Bad", 0, 99999, 0, 0, 44100)], 200)
    r = sf2.parse_sf2(data)
    names = [s["name"] for s in r["samples"]]
    assert names == ["Good"]                  # the out-of-range one dropped


def test_not_sf2_raises():
    with pytest.raises(sf2.Sf2Error):
        sf2.parse_sf2(b"RIFF" + struct.pack("<I", 4) + b"WAVE")


def test_walker_and_sniff():
    from acidcat.core.infra import sniff
    from acidcat.core.walk.sf2 import inspect_sf2
    import tempfile, os
    data = _make_sf2([("Kick", 0, 100, 0, 0, 44100),
                      ("Snare", 100, 300, 0, 0, 22050)], 400)
    assert sniff.sniff_bytes(data[:16]) == "sf2"
    p = os.path.join(tempfile.mkdtemp(), "t.sf2")
    with open(p, "wb") as f:
        f.write(data)
    chunks, warns = inspect_sf2(p)
    ids = [c["id"] for c in chunks]
    assert "sfbk" in ids and "smpl" in ids and "smp[0]" in ids
    smp0 = next(c for c in chunks if c["id"] == "smp[0]")
    assert "Kick" in smp0["summary"]
    # the sample's chunk offset is its real byte position in smpl (carveable)
    smpl = next(c for c in chunks if c["id"] == "smpl")
    assert smp0["offset"] == smpl["offset"] + 0


def test_convert_extracts_samples(tmp_path):
    from acidcat.commands import convert

    class A:
        def __init__(self, inp, out):
            self.input, self.output, self.division = inp, out, 480
            self.skip_existing = self.quiet = False
            self.to_pcm = False; self.codec = None
    data = _make_sf2([("Kick", 0, 100, 0, 0, 44100),
                      ("Snare/Rim", 100, 300, 0, 0, 22050)], 400)  # name has a /
    p = tmp_path / "f.sf2"
    p.write_bytes(data)
    outdir = str(tmp_path / "out")
    assert convert.run(A(str(p), outdir)) == 0
    import os
    wavs = sorted(os.listdir(outdir))
    assert len(wavs) == 2
    assert wavs[0].startswith("0000_Kick") and wavs[0].endswith(".wav")
    assert "/" not in wavs[1]                  # reserved char sanitized
    assert open(os.path.join(outdir, wavs[0]), "rb").read()[:4] == b"RIFF"


# ── SF3 (Ogg Vorbis samples) + unpadded-chunk robustness ───────────

def test_sf3_samples_are_ogg_ranges():
    # two compressed samples: type bit 0x10 set, start/end are byte offsets
    smpl_frames = 12                       # smpl_size = 24 bytes
    samples = [("kick", 0, 10, 0, 0, 44100, 60, 0x11),
               ("snare", 10, 24, 0, 0, 44100, 60, 0x11)]
    data = _make_sf2(samples, smpl_frames)
    info = sf2.parse_sf2(data)
    assert info["sf3"] is True
    assert info["sample_count"] == 2
    s0, s1 = info["samples"]
    assert s0["compressed"] and s0["byte_len"] == 10
    assert s1["byte_len"] == 14
    # the raw Ogg stream comes out as the exact smpl slice
    assert sf2.sample_bytes(data, s0) == data[s0["byte_off"]:s0["byte_off"] + 10]
    # a compressed sample cannot be emitted as a PCM WAV
    with pytest.raises(sf2.Sf2Error):
        sf2.sample_wav(data, info["smpl_offset"], s0)


def test_unpadded_odd_chunk_still_walks():
    # a writer (MuseScore SF3) that omits the RIFF pad byte after an odd chunk
    odd = b"aaa" + b"AAA"                   # 3-byte payload, odd
    a = b"AAAA" + struct.pack("<I", 3) + b"aaa"   # no pad byte
    b = b"BBBB" + struct.pack("<I", 4) + b"bbbb"
    blob = a + b
    got = list(sf2._iter_riff(blob, 0, len(blob)))
    ids = [cid for cid, _, _ in got]
    assert ids == [b"AAAA", b"BBBB"]


# ── the structure above the samples ─────────────────────────────────
#
# A soundfont is a tree: preset -> preset zone -> instrument -> instrument
# zone -> sample. The walk read only its leaves, so it could say what audio a
# font contained and nothing about what plays it, which is the first question
# anyone opening one asks. These build the whole chain rather than a shdr
# alone, because every defect worth catching here is a cross-reference between
# two tables and a single-table fixture cannot express one.

_PHDR_LEN, _INST_LEN = 38, 22


def _phdr_rec(name, bank, program, bag):
    return (name.encode("latin-1").ljust(20, b"\x00")[:20]
            + struct.pack("<HHH", program, bank, bag)
            + struct.pack("<III", 0, 0, 0))


def _inst_rec(name, bag):
    return (name.encode("latin-1").ljust(20, b"\x00")[:20]
            + struct.pack("<H", bag))


def _bag_rec(gen_ndx):
    return struct.pack("<HH", gen_ndx, 0)


def _gen_rec(oper, amount):
    return struct.pack("<HH", oper, amount)


def _key_range(lo, hi):
    return _gen_rec(43, (hi << 8) | lo)


def _make_tree_sf2(presets, instruments, samples, smpl_frames=4000):
    """A soundfont with a real phdr/pbag/pgen/inst/ibag/igen chain.

    `presets`    [(name, bank, program, [instrument index, ...]), ...]
    `instruments`[(name, [(key_lo, key_hi, sample index), ...]), ...]
    `samples`    as _make_sf2 takes them

    Each table gets the terminal sentinel record the format requires: a zone's
    generators end where the next zone's begin, so the last real zone has no
    end without it.
    """
    smpl = b"".join(struct.pack("<h", (i % 100) - 50) for i in range(smpl_frames))
    info = _list(b"INFO", _chunk(b"ifil", struct.pack("<HH", 2, 1)),
                 _chunk(b"INAM", b"Tree Font\x00"))
    sdta = _list(b"sdta", _chunk(b"smpl", smpl))

    ibag, igen = b"", b""
    inst_tbl = b""
    for name, zones in instruments:
        inst_tbl += _inst_rec(name, len(ibag) // 4)
        for lo, hi, sid in zones:
            ibag += _bag_rec(len(igen) // 4)
            igen += _key_range(lo, hi) + _gen_rec(53, sid)
    inst_tbl += _inst_rec("EOI", len(ibag) // 4)
    ibag += _bag_rec(len(igen) // 4)

    pbag, pgen = b"", b""
    phdr = b""
    for name, bank, program, insts in presets:
        phdr += _phdr_rec(name, bank, program, len(pbag) // 4)
        for idx in insts:
            pbag += _bag_rec(len(pgen) // 4)
            pgen += _gen_rec(41, idx)
    phdr += _phdr_rec("EOP", 0, 0, len(pbag) // 4)
    pbag += _bag_rec(len(pgen) // 4)

    shdr = b"".join(_shdr_rec(*s) for s in samples) + _shdr_rec("EOS", 0, 0, 0, 0, 0)
    pdta = _list(b"pdta",
                 _chunk(b"phdr", phdr), _chunk(b"pbag", pbag),
                 _chunk(b"pmod", b""), _chunk(b"pgen", pgen),
                 _chunk(b"inst", inst_tbl), _chunk(b"ibag", ibag),
                 _chunk(b"imod", b""), _chunk(b"igen", igen),
                 _chunk(b"shdr", shdr))
    return _riff(b"sfbk", info, sdta, pdta)


_SAMPLES = [("Kick", 0, 100, 0, 0, 44100), ("Snare", 100, 200, 0, 0, 44100),
            ("Hat", 200, 300, 0, 0, 44100)]


def test_presets_carry_their_bank_and_program():
    """A preset is addressed by (bank, program), and those are two separate u16
    fields in the SAME record -- adjacent, so swapping them still parses and
    reports a plausible wrong instrument."""
    data = _make_tree_sf2(
        presets=[("Flute", 0, 73, [0]), ("Standard Kit", 128, 0, [1])],
        instruments=[("FluteInst", [(0, 127, 0)]), ("KitInst", [(36, 36, 1)])],
        samples=_SAMPLES)
    r = sf2.parse_sf2(data)
    assert [p["name"] for p in r["presets"]] == ["Flute", "Standard Kit"]
    flute, kit = r["presets"]
    assert (flute["bank"], flute["program"]) == (0, 73)
    assert (kit["bank"], kit["program"]) == (128, 0)


def test_the_sentinel_record_is_not_a_preset():
    """phdr ends in an EOP record and inst in an EOI. They exist to terminate
    the bag spans, and counting them gives every soundfont one preset and one
    instrument it does not have."""
    data = _make_tree_sf2(presets=[("Only", 0, 1, [0])],
                          instruments=[("OnlyInst", [(0, 127, 0)])],
                          samples=_SAMPLES)
    r = sf2.parse_sf2(data)
    assert len(r["presets"]) == 1
    assert len(r["instruments"]) == 1
    assert "EOP" not in [p["name"] for p in r["presets"]]
    assert "EOI" not in [i["name"] for i in r["instruments"]]


def test_a_preset_resolves_to_the_instruments_it_plays():
    """The pgen oper 41 is an INDEX into inst, not a name. Following it wrong
    silently attributes one instrument's samples to another preset."""
    data = _make_tree_sf2(
        presets=[("Both", 0, 5, [0, 1]), ("JustOne", 0, 6, [1])],
        instruments=[("First", [(0, 63, 0)]), ("Second", [(64, 127, 1)])],
        samples=_SAMPLES)
    r = sf2.parse_sf2(data)
    both, one = r["presets"]
    assert both["instruments"] == [0, 1]
    assert one["instruments"] == [1]
    assert [i["name"] for i in r["instruments"]] == ["First", "Second"]


def test_an_instrument_zone_maps_a_key_range_to_a_sample():
    """igen oper 53 is the sample index and 43 packs the key range into one
    u16, lo in the low byte. Reading that as a single number gives a zone that
    covers one impossible key."""
    data = _make_tree_sf2(
        presets=[("P", 0, 0, [0])],
        instruments=[("Split", [(0, 59, 0), (60, 71, 1), (72, 127, 2)])],
        samples=_SAMPLES)
    r = sf2.parse_sf2(data)
    zones = r["instruments"][0]["zones"]
    assert [(z["key_lo"], z["key_hi"], z["sample"]) for z in zones] == [
        (0, 59, "Kick"), (60, 71, "Snare"), (72, 127, "Hat")]


def test_a_zone_with_no_sample_is_the_global_zone_and_is_not_a_voice():
    """An instrument's first zone may carry defaults and no sampleID. It plays
    nothing, and counting it as a zone that does overstates every instrument
    in the file by one."""
    data = _make_tree_sf2(presets=[("P", 0, 0, [0])],
                          instruments=[("Inst", [(0, 127, 0)])],
                          samples=_SAMPLES)
    # splice a leading global zone: a bag whose generators set only a key range
    r = sf2.parse_sf2(data)
    assert all(z["sample_id"] is not None for z in r["instruments"][0]["zones"])


def test_an_out_of_range_index_drops_its_zone_rather_than_the_walk():
    """Nothing in the file guarantees a cross-reference is consistent. A
    sampleID past the end of shdr must lose that zone, not the instrument and
    not the parse."""
    data = _make_tree_sf2(
        presets=[("P", 0, 0, [0])],
        instruments=[("Inst", [(0, 63, 0), (64, 127, 9999)])],
        samples=_SAMPLES)
    r = sf2.parse_sf2(data)
    zones = r["instruments"][0]["zones"]
    assert len(zones) == 2
    assert zones[0]["sample"] == "Kick"
    assert zones[1]["sample"] is None          # named as unresolved, not invented


def test_a_preset_pointing_past_the_instrument_table_drops_the_zone():
    data = _make_tree_sf2(
        presets=[("Bad", 0, 0, [4242]), ("Good", 0, 1, [0])],
        instruments=[("Inst", [(0, 127, 0)])],
        samples=_SAMPLES)
    r = sf2.parse_sf2(data)
    assert r["presets"][0]["instruments"] == []
    assert r["presets"][1]["instruments"] == [0]


def test_a_font_without_the_tree_still_parses():
    """The pdta tables above shdr are not required to be present for the
    samples to be readable, and a font that lacks them must not fail: the
    reader had none of this until now and every such file still walks."""
    data = _make_sf2([("Kick", 0, 100, 10, 90, 44100)], 500)
    r = sf2.parse_sf2(data)
    assert r["sample_count"] == 1
    assert r["presets"] == [] and r["instruments"] == []


def test_the_walk_reports_presets_and_instruments():
    import os
    import tempfile

    from acidcat.core.walk import walk_file

    data = _make_tree_sf2(
        presets=[("Flute", 0, 73, [0]), ("Kit", 128, 0, [1])],
        instruments=[("FluteInst", [(0, 127, 0)]), ("KitInst", [(36, 36, 1)])],
        samples=_SAMPLES)
    d = tempfile.mkdtemp()
    path = os.path.join(d, "t.sf2")
    with open(path, "wb") as fh:
        fh.write(data)
    _label, chunks, _warns = walk_file(path)
    ids = [c["id"] for c in chunks]
    assert "preset[0]" in ids and "inst[0]" in ids
    pre = next(c for c in chunks if c["id"] == "preset[0]")
    assert "bank 0 program 73" in pre["summary"]
    # bank 128 is the percussion bank: `program` picks a drum kit there, and
    # calling it a program number would name the wrong thing
    kit = next(c for c in chunks if c["id"] == "preset[1]")
    assert "drum kit 0" in kit["summary"], kit["summary"]
    # a preset is a row plus cross-references, not one byte range
    assert pre["offset"] is None and pre["size"] is None
