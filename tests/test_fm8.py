"""FM8 presets: the hsin metadata's bank chain and tags, and the FM8 sound
state (operators, envelopes, matrix) decoded from the compressed subtree.

Every fixture is built here from the layout in core/formats/fm8.py
(tests/seeds.py assembles the state, the hsin frames and a literal-only
FastLZ stream), never copied from a real preset.
"""

import struct

import pytest

from acidcat.core.catalogue import preset_meta
from acidcat.core.formats import fm8 as fm8mod
from acidcat.core.formats import ni as nimod
from acidcat.core.walk import walk_file

import seeds


def _walk(tmp_path, blob, name="a.nfm8", deep=True):
    p = tmp_path / name
    p.write_bytes(blob)
    return walk_file(str(p), deep=deep)


def _codes(warns):
    return [getattr(w, "code", None) for w in warns]


def _fields(chunk):
    return {f["name"]: f["value"] for f in chunk["fields"]}


def _chunk(chunks, cid):
    return next(c for c in chunks if c["id"] == cid)


# a voice shaped like the DX7's STEEL DRUM import: operator A fixed, B-F by
# ratio, C and E detuned, the matrix of algorithm 15
STEEL = dict(
    transpose=-12.0, lfo=(0.0, 40.0, 7.0, 0.0),
    ratio=(0.0, 5.32, 2.0022, 1.0, 1.7, 0.9989),
    offset=(398.11, 0.0, 1.82, 0.0, 0.0, -0.91),
    velocity=(0, 0, 14, 43, 29, 57), rate_scaling=(38, 38, 38, 12, 0, 88),
    points=5,
    matrix={(0, 2): 8, (1, 2): 16, (2, 3): 41, (4, 5): 19, (4, 4): 9,
            (3, 8): 55, (5, 8): 71})


def test_the_state_block_reads_by_position():
    st = fm8mod.parse(seeds.fm8_state(**STEEL))
    assert st["version"] == 207 and st["laid_out"]
    assert st["names"] == ["default"] * 4
    assert st["transpose"] == -12.0
    assert [st["lfo"][0][k] for k in ("wave", "rate", "delay", "sync")] == [0, 40, 7, 0]
    assert st["lfo"][1] == st["lfo"][0]
    assert st["ratio"][:6] == pytest.approx(STEEL["ratio"], abs=1e-4)
    assert st["offset"][:6] == pytest.approx(STEEL["offset"], abs=1e-3)
    assert st["velocity"][:6] == list(STEEL["velocity"])
    assert st["rate_scaling"][:6] == list(STEEL["rate_scaling"])
    assert st["key_sync"][:6] == [1] * 6
    assert [len(t) for t, _l, _s in st["env"]] == [5] * 9
    assert st["point_index"] == [[3] * 9, [2] * 9, [4] * 9]
    m = st["matrix"]
    assert m[0][2] == 8 and m[2][3] == 41 and m[4][4] == 9 and m[5][8] == 71
    assert st["blocks_agree"] and st["cells_agree"]


def test_names_of_any_length_move_the_layout_with_them():
    st = fm8mod.parse(seeds.fm8_state(names=("Pure DX7",) * 4, transpose=7.0))
    assert st["names"] == ["Pure DX7"] * 4
    assert st["base"] == 12 + 4 * (4 + 8)
    assert st["transpose"] == 7.0


def test_an_unlaid_version_reads_its_header_only():
    st = fm8mod.parse(seeds.fm8_state(version=205))
    assert st["version"] == 205 and not st["laid_out"]
    assert "ratio" not in st


def test_a_cut_block_is_short_not_a_crash():
    blob = seeds.fm8_state()
    with pytest.raises(fm8mod.Short):
        fm8mod.parse(blob[:0x700])
    with pytest.raises(ValueError):
        fm8mod.parse(b"E8MF" + struct.pack("<II", 207, 9148) + struct.pack("<I", 1 << 20))


def test_the_walker_decodes_an_nfm8_with_deep(tmp_path):
    _label, chunks, warns = _walk(tmp_path, seeds.ni_fm8(seeds.fm8_state(**STEEL),
                                                         name="STEEL DRUM"))
    assert not [w for w in warns if getattr(w, "code", "").startswith(("parse", "size"))]
    head = _fields(_chunk(chunks, "fm8"))
    assert head["state_version"] == 207 and head["transpose"] == "-12"
    assert head["lfo1_wave"] == "sine" and head["lfo1_sync"] == "off"
    a = _fields(_chunk(chunks, "op[A]"))
    assert a["frequency"] == "fixed 398.11 Hz" and a["modulates"] == "C 8"
    c = _fields(_chunk(chunks, "op[C]"))
    assert c["frequency"] == "ratio 2.0022 +1.82 Hz" and c["velocity"] == 14
    assert c["modulates"] == "D 41" and c["env_points"] == 5
    e = _fields(_chunk(chunks, "op[E]"))
    assert e["feedback"] == 9 and e["modulates"] == "F 19"
    f = _fields(_chunk(chunks, "op[F]"))
    assert f["output"] == 71 and f["rate_scaling"] == 88
    m = _fields(_chunk(chunks, "matrix"))
    assert m["A>C"] == 8 and m["C>D"] == 41 and m["carriers"] == "DF"
    assert "other_cells" not in m


def test_without_deep_the_state_is_named_not_decoded(tmp_path):
    _label, chunks, warns = _walk(tmp_path, seeds.ni_fm8(), deep=False)
    # the frame tree is walked; the FM8 state's own chunks wait for --deep
    assert chunks[0]["id"] == "hsin"
    assert not [c for c in chunks if c["id"] in ("fm8", "pitch_env", "matrix")
                or c["id"].startswith("op[")]
    assert "decode.partial" in _codes(warns)


def test_an_unlaid_version_says_so(tmp_path):
    _label, chunks, warns = _walk(tmp_path, seeds.ni_fm8(seeds.fm8_state(version=205)))
    assert _fields(_chunk(chunks, "fm8"))["state_version"] == 205
    assert not [c for c in chunks if c["id"].startswith("op[")]
    assert "layout.unmeasured" in _codes(warns)


def test_a_short_state_is_a_finding(tmp_path):
    _label, chunks, warns = _walk(tmp_path, seeds.ni_fm8(seeds.fm8_state()[:0x700]))
    assert "size.overrun" in _codes(warns)
    assert not [c for c in chunks if c["id"] == "fm8"]


def test_copies_that_disagree_are_named(tmp_path):
    _label, _chunks, warns = _walk(tmp_path, seeds.ni_fm8(seeds.fm8_state(copies_equal=False)))
    assert any("copies" in str(w) for w in warns if getattr(w, "code", "") == "decode.partial")


def test_a_kore_sound_holding_fm8_is_decoded(tmp_path):
    _label, chunks, warns = _walk(tmp_path, seeds.ni_ksd_fm8(name="Pure DX7"), name="a.ksd")
    assert _fields(chunks[0])["plugin"] == "FM8"
    head = _fields(_chunk(chunks, "fm8"))
    assert head["state_version"] == 206 and head["names"] == " | ".join(["Pure DX7"] * 4)
    assert _fields(_chunk(chunks, "op[F]"))["output"] == 71


def test_the_sound_info_bank_chain_and_tags_are_read():
    blob = seeds.ni_hsin(name="Lead", chain=["Massive", "Factory"],
                         tags=["Synth Lead", "Dry"])
    m = nimod.parse_hsin(blob)
    assert m["name"] == "Lead" and m["bank"] == "Massive / Factory"
    assert m["attributes"] == ["Synth Lead", "Dry"]
    pm = preset_meta.extract(blob)
    assert pm["tags"] == ["Factory", "Synth Lead", "Dry"]


def test_an_fm8_preset_is_indexed_by_name_and_bank():
    pm = preset_meta.extract(seeds.ni_fm8(name="FM SETUP 1", bank="-SETUP-.SYX"))
    assert pm["preset_name"] == "FM SETUP 1" and pm["product"] == "FM8"
    assert pm["tags"] == ["-SETUP-.SYX"]


def test_a_sound_info_without_its_tail_still_reads():
    m = nimod.parse_hsin(seeds.ni_hsin(name="Plain"))
    assert m["name"] == "Plain" and "bank" not in m and "attributes" not in m


def test_a_tail_whose_count_overruns_is_ignored():
    blob = bytearray(seeds.ni_hsin(name="Lead", chain=["Massive", "Factory"]))
    at = blob.find("Massive".encode("utf-16-le")) - 8
    struct.pack_into("<I", blob, at, 50000)
    m = nimod.parse_hsin(bytes(blob))
    assert m["name"] == "Lead" and "bank" not in m


def test_bytes_after_the_root_frame_are_reported(tmp_path):
    _label, _chunks, warns = _walk(tmp_path, seeds.ni_fm8() + b"\x01" * 40, deep=False)
    assert "container.trailing" in _codes(warns)
    # zero fill after the root is a padding node, not a finding
    _label, chunks, warns = _walk(tmp_path, seeds.ni_fm8() + bytes(40), deep=False)
    assert "container.trailing" not in _codes(warns)
    assert chunks[-1]["id"] == "padding" and chunks[-1]["size"] == 40


def test_flipped_and_cut_states_degrade_to_findings():
    """Every byte of a state flipped, and the state cut every few bytes: the
    parser raises only ValueError, and the walker's decode only reports."""
    from acidcat.core.walk import ni as niwalk
    blob = seeds.fm8_state(**STEEL)
    bad = []
    variants = [blob[:n] for n in range(0, len(blob), 13)]
    for i in range(len(blob)):
        b = bytearray(blob)
        b[i] ^= 0xFF
        variants.append(bytes(b))
    for v in variants:
        try:
            fm8mod.parse(v)
        except ValueError:
            pass
        except Exception as e:  # noqa: BLE001 -- the point is to catch any
            bad.append(repr(e))
        niwalk._fm8_chunks(v, [])
    assert not bad, bad[:5]


def test_a_nan_waveform_is_named_by_its_value():
    assert fm8mod.lfo_wave(float("nan")) == "id nan"
    assert fm8mod.lfo_wave(float("inf")) == "id inf"
    assert fm8mod.lfo_wave(32.0) == "sample and hold"
