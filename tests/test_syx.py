"""SysEx dumps: every message named, Yamaha DX7 voices decoded.

Every fixture is built here from the owner's manual layout (tests/seeds.py
packs the voices itself), never copied from a real dump.
"""

import os

import pytest

from acidcat.core.formats import syx as syxmod
from acidcat.core.infra import sniff
from acidcat.core.walk import walk_file

import seeds

ROLAND = bytes([0xF0, 0x41, 0x10, 0x42, 0x12, 0x40, 0x00, 0x7F, 0x00, 0x41, 0xF7])


def _bank(**kw):
    return seeds.SEEDS["syx"][0](**kw)


def _walk(tmp_path, blob, name="a.syx"):
    p = tmp_path / name
    p.write_bytes(blob)
    return walk_file(str(p))


def _codes(warns):
    return [getattr(w, "code", None) for w in warns]


def _fields(chunk):
    return {f["name"]: f for f in chunk["fields"]}


def _voices(chunks):
    return [c for c in chunks if c["id"].startswith("voice[")]


def _tiles(chunks, size):
    pos = 0
    for c in chunks:
        if c["offset"] != pos:
            return False
        pos += c["size"]
    return pos == size


def test_a_dx7_bank_is_identified_as_sysex(tmp_path):
    p = tmp_path / "bank.syx"
    p.write_bytes(_bank())
    assert sniff.sniff(str(p)) == "syx"
    assert "syx" in sniff.KNOWN_FORMATS


def test_the_bank_header_reads_device_format_and_count(tmp_path):
    _label, chunks, warns = _walk(tmp_path, _bank(device=5))
    head = _fields(chunks[0])
    assert head["manufacturer"]["value"] == "Yamaha (0x43)"
    assert head["device"]["value"] == 6                  # low nibble 5, shown 1-16
    assert head["format"]["value"] == 9
    assert head["byte_count"]["value"] == "4,096"
    assert "device 6" in chunks[0]["summary"] and "32 voices" in chunks[0]["summary"]
    assert warns == []


def test_all_32_voice_names_show(tmp_path):
    _label, chunks, _warns = _walk(tmp_path, _bank())
    voices = _voices(chunks)
    assert [_fields(v)["name"]["value"] for v in voices] == ["SEED %02d" % i for i in range(32)]
    listed = _fields(chunks[0])["voices"]["value"]
    assert listed.startswith("1 SEED 00, 2 SEED 01") and listed.endswith("32 SEED 31")


@pytest.mark.parametrize("i", [0, 5, 13, 31])
def test_packed_voice_parameters_decode_to_what_was_packed(tmp_path, i):
    _label, chunks, _warns = _walk(tmp_path, _bank())
    f = _fields(_voices(chunks)[i])
    v = seeds.dx7_vced(i)                                # the bytes before packing
    assert f["algorithm"]["value"] == v[134] + 1
    assert f["feedback"]["value"] == v[135]
    assert f["osc_key_sync"]["value"] == ("on" if v[136] else "off")
    assert f["lfo_speed"]["value"] == 35 and f["lfo_delay"]["value"] == 1
    assert f["lfo_pitch_depth"]["value"] == 2 and f["lfo_amp_depth"]["value"] == 3
    assert f["lfo_sync"]["value"] == ("on" if v[141] else "off")
    assert f["lfo_wave"]["value"] == syxmod.LFO_WAVES[v[142]]
    assert f["pitch_mod_sens"]["value"] == v[143]
    assert f["transpose"]["value"] == v[144] - 24
    assert f["pitch_eg_rates"]["value"] == "99 98 97 96"
    for op in range(1, 7):
        o = (6 - op) * 21
        assert f["op%d_rates" % op]["value"] == " ".join(str(x) for x in v[o:o + 4])
        assert f["op%d_levels" % op]["value"] == " ".join(str(x) for x in v[o + 4:o + 8])
        assert f["op%d_level" % op]["value"] == v[o + 16]
        assert f["op%d_mode" % op]["value"] == ("fixed" if v[o + 17] else "ratio")
        assert f["op%d_coarse" % op]["value"] == v[o + 18]
        assert f["op%d_fine" % op]["value"] == v[o + 19]
        assert f["op%d_detune" % op]["value"] == v[o + 20] - 7
        assert f["op%d_velocity_sens" % op]["value"] == v[o + 15]


def test_a_single_voice_decodes_the_same_as_its_packed_twin(tmp_path):
    """The two shapes of one voice must read identically: a decoder that has
    one bit split wrong disagrees with itself here."""
    _l, bank, _w = _walk(tmp_path, _bank(), "bank.syx")
    for i in (0, 7, 20):
        one = seeds.dx7_message(0, seeds.dx7_vced(i))
        _l, single, warns = _walk(tmp_path, one, "one.syx")
        assert warns == []
        assert len(single) == 3 and single[1]["size"] == 155
        a = {k: f["value"] for k, f in _fields(single[1]).items()}
        b = {k: f["value"] for k, f in _fields(_voices(bank)[i]).items()}
        assert a == b


def test_a_single_voice_dump_names_its_voice(tmp_path):
    _label, chunks, warns = _walk(tmp_path, _bank(single=True))
    assert warns == []
    assert "single voice" in chunks[0]["summary"] and chunks[0]["summary"].endswith("SEED 00")
    assert _fields(chunks[-1])["checksum"]["note"] == "matches"
    assert _tiles(chunks, 163)


def test_operator_frequency_ratio_and_fixed():
    assert syxmod.frequency(0, 0, 0) == "ratio 0.50"
    assert syxmod.frequency(0, 1, 0) == "ratio 1.00"
    assert syxmod.frequency(0, 2, 50) == "ratio 3.00"
    assert syxmod.frequency(1, 1, 0) == "fixed 10 Hz"
    assert syxmod.frequency(1, 4, 0) == "fixed 1 Hz"          # coarse wraps every four
    assert syxmod.frequency(1, 3, 50) == "fixed 3162 Hz"


def test_a_bad_checksum_is_a_finding_not_a_refusal(tmp_path):
    good = _bank()
    bad = good[:-2] + bytes([(good[-2] + 1) & 0x7F, 0xF7])
    _label, chunks, warns = _walk(tmp_path, bad)
    assert _codes(warns) == ["checksum.mismatch"]
    assert len(_voices(chunks)) == 32
    assert "computed" in _fields(chunks[-1])["checksum"]["note"]


def test_the_checksum_is_twos_complement_of_the_seven_bit_sum():
    assert syxmod.checksum(bytes([0x01, 0x02])) == 0x7D
    assert syxmod.checksum(bytes([0x40, 0x40])) == 0x00
    assert (sum(b"\x10\x20\x30") + syxmod.checksum(b"\x10\x20\x30")) & 0x7F == 0


def test_a_truncated_bank_keeps_its_whole_voices(tmp_path):
    cut = _bank()[:3000]
    _label, chunks, warns = _walk(tmp_path, cut)
    assert _codes(warns) == ["size.overrun"]
    assert len(_voices(chunks)) == (3000 - 6) // 128
    assert _tiles(chunks, 3000)


def test_a_header_cut_short_is_walked_not_raised(tmp_path):
    _label, chunks, warns = _walk(tmp_path, bytes([0xF0, 0x43, 0x00, 0x09]))
    assert _codes(warns) == ["required.missing", "decode.partial"]
    assert len(chunks) == 1 and _tiles(chunks, 4)


def test_a_message_a_status_byte_cuts_short(tmp_path):
    blob = _bank(single=True) + ROLAND[:-1] + bytes([0x90, 0x3C, 0x40])
    _label, chunks, warns = _walk(tmp_path, blob)
    assert "required.missing" in _codes(warns) and "bytes.stray" in _codes(warns)
    assert [c["id"] for c in chunks][-2:] == ["sysex[1]", "stray[0]"]
    assert _tiles(chunks, len(blob))
    # the same message first in a file is not claimed: a status byte inside
    # the head is not how a dump opens
    assert sniff.sniff_bytes(blob[163:]) is None


def test_non_yamaha_sysex_is_named_and_measured(tmp_path):
    p = tmp_path / "roland.syx"
    p.write_bytes(ROLAND)
    assert sniff.sniff(str(p)) == "syx"
    _label, chunks, warns = walk_file(str(p))
    assert len(chunks) == 1 and chunks[0]["size"] == len(ROLAND)
    assert _fields(chunks[0])["manufacturer"]["value"] == "Roland (0x41)"
    assert _codes(warns) == ["decode.partial"]
    assert "Roland" in warns[0]


def test_another_yamaha_format_is_named_not_decoded(tmp_path):
    """A TX81Z bank has the same header and checksum as a DX7 one and a
    different voice layout, so it is checked and named, not decoded."""
    tx = seeds.dx7_message(4, bytes(4096))
    _label, chunks, warns = _walk(tmp_path, tx)
    assert len(chunks) == 1 and not _voices(chunks)
    assert "4-operator 32-voice bank" in chunks[0]["summary"]
    assert _fields(chunks[0])["checksum"]["note"] == "matches"
    assert _codes(warns) == ["decode.partial"]
    bad = seeds.dx7_message(4, bytes(4096), checksum=5)
    _label, _chunks, warns = _walk(tmp_path, bad, "b.syx")
    assert "checksum.mismatch" in _codes(warns)


def test_several_messages_and_stray_bytes_between_them(tmp_path):
    blob = ROLAND + b"\x00\x00" + _bank(single=True) + _bank(single=True)
    _label, chunks, warns = _walk(tmp_path, blob)
    assert [c["id"] for c in chunks] == [
        "sysex[0]", "stray[0]", "sysex[1]", "voice[1]", "end[1]",
        "sysex[2]", "voice[2]", "end[2]"]
    assert sorted(_codes(warns)) == ["bytes.stray", "decode.partial"]
    assert _tiles(chunks, len(blob))


def test_a_parameter_above_its_maximum_is_reported(tmp_path):
    v = bytearray(seeds.dx7_vced(0))
    v[16] = 120                                          # OP6 output level, max 99
    v[142] = 7                                           # LFO wave, max 5
    _label, chunks, warns = _walk(tmp_path, seeds.dx7_message(0, bytes(v)))
    assert _codes(warns) == ["value.invalid"]
    assert "op6 level" in warns[0] and "lfo_wave" in warns[0]
    assert _fields(_voices(chunks)[0])["lfo_wave"]["value"] == "undefined (7)"


@pytest.mark.parametrize("head", [
    b"\xf0",                                        # one byte is not a message
    b"\xf0\x9f\x98\x80 an emoji opens this text",   # UTF-8 text
    b"\xf0\x43\xf7\x00",                            # F7 before any data
    b"\xf0\x43\x00\x09\x80\x00\x00\x00",            # a status byte inside
    b"\x00\xf0\x43\x00\x09\x20\x00",                # F0 not at the start
])
def test_what_is_not_claimed_as_sysex(head):
    assert sniff.sniff_bytes(head) != "syx"


def test_an_ni_preset_whose_size_byte_is_f0_stays_ni():
    """An hsin preset opens with its little-endian size. One real .nki was
    0x19F0 bytes, so its head is F0 and then nothing but data bytes."""
    head = bytes([0xF0, 0x19, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0]) + b"hsin" + bytes(4)
    assert syxmod.looks_like_syx(head)
    assert sniff.sniff_bytes(head) == "ni"


def test_preset_index_reads_the_voice_names(tmp_path):
    from acidcat.core.catalogue import indexing, preset_meta
    m = preset_meta.extract(_bank())
    assert m["product"] == "Yamaha DX7" and m["preset_name"] is None
    assert m["description"].startswith("32 voices: SEED 00, SEED 01")
    one = preset_meta.extract(_bank(single=True))
    assert one["preset_name"] == "SEED 00" and one["description"] is None
    assert preset_meta.extract(ROLAND) is None
    p = tmp_path / "bank.syx"
    p.write_bytes(_bank())
    row = indexing._extract_for_index(str(p), str(tmp_path), 0.0, p.stat().st_size, 0.0)
    assert row["format"] == "syx" and row["product"] == "Yamaha DX7"
    assert "SEED 31" in row["comment"]


@pytest.mark.corpus
@pytest.mark.skipif(not os.environ.get("ACIDCAT_SYX_CORPUS"),
                    reason="set ACIDCAT_SYX_CORPUS to a dir of real .syx files")
def test_real_corpus_walks_completely():
    """A bounded, evenly spaced slice: a big SysEx corpus is usually on a slow
    drive, and one converter's output repeats itself."""
    root = os.environ["ACIDCAT_SYX_CORPUS"]
    files = sorted(os.path.join(r, f) for r, _d, fn in os.walk(root) for f in fn
                   if f.lower().endswith(".syx"))
    assert len(files) >= 50
    step = max(1, len(files) // 300)
    seen = 0
    for path in files[::step]:
        assert sniff.sniff(path) == "syx", path
        _label, chunks, warns = walk_file(path)
        assert _tiles(chunks, os.path.getsize(path)), path
        assert "walker.error" not in _codes(warns), path
        seen += 1
    assert seen >= 50
