"""Serum 1 presets: the VST2 chunk split into its two zlib streams and trailer,
the patch record's parameters, file references and text decoded.

Every fixture is built here from the published offsets (tests/seeds.py writes
the patch record itself), never copied from a real preset.
"""

import struct
import zlib

import pytest

from acidcat.core.catalogue import preset_meta
from acidcat.core.infra import contract
from acidcat.core.infra.source import BytesSource
from acidcat.core.walk import walk_file

import seeds


def _walk(tmp_path, blob, name="p.fxp"):
    p = tmp_path / name
    p.write_bytes(blob)
    return walk_file(str(p))


def _doc(blob):
    return contract.walk(BytesSource(blob, name="p.fxp"))


def _codes(warns):
    return [getattr(w, "code", None) for w in warns]


def _node(doc, suffix):
    hits = [n for n in contract.iter_nodes(doc) if n["id"].endswith(suffix)]
    assert len(hits) == 1, (suffix, [n["id"] for n in contract.iter_nodes(doc)])
    return hits[0]


def _fields(node):
    return {f["key"]: f for f in node["fields"]}


def test_the_chunk_is_two_streams_and_a_trailer(tmp_path):
    chunk = seeds.serum1_chunk()
    z1 = len(zlib.compress(seeds.serum1_patch()))
    label, chunks, warns = _walk(tmp_path, seeds.vst_fxp(chunk))
    assert [c["id"].split("/")[-1] for c in chunks] == ["CcnK", "patch", "tables", "trailer"]
    assert not warns
    patch, tables, trailer = chunks[1:]
    assert (patch["offset"], patch["size"]) == (60, z1)
    assert tables["offset"] == 60 + z1
    assert trailer["offset"] + 4 == 60 + len(chunk)
    tf = trailer["fields"][0]
    assert tf["name"] == "patch_stream_len" and tf["raw"] == z1
    assert "it matches" in tf["note"] and tf["xref"] == 60 + z1
    assert "Seed Patch" in chunks[0]["summary"] and "20,704-byte patch" in chunks[0]["summary"]


def test_the_layers_tile_the_file(tmp_path):
    doc = _doc(seeds.vst_fxp(seeds.serum1_chunk(tables=bytes(8192))))
    assert [n["extent"] for n in contract.iter_nodes(doc)
            if n["kind"] == "unwalked" and n["extent"]["layer"] == 0] == []
    lengths = sorted(l.get("length") for l in doc["layers"] if l["kind"] != "file")
    assert lengths == [8192, 20704]
    assert not [f for f in doc["findings"] if f["kind"] in ("defect", "error")]


def test_parameters_are_named_by_slot():
    doc = _doc(seeds.vst_fxp(seeds.serum1_chunk()))
    params = _node(doc, "patch/params")
    assert params["extent"] == {"layer": 1, "off": 0x3460, "len": 992}
    f = _fields(params)
    # the seed stores slot i as i/1000, so a name one slot off reads wrong
    for key, slot in (("master_vol", 0), ("a_vol", 1), ("a_octave", 3), ("b_vol", 14),
                      ("noise_level", 27), ("env1_attack", 35), ("env1_release", 39),
                      ("filter_cutoff", 45), ("filter_reso", 46), ("env2_attack", 51),
                      ("lfo1_rate", 61), ("reverb_wet", 81), ("delay_feedback", 132),
                      ("hyper_unison", 150), ("mod1_amount", 180), ("mod7_output", 193),
                      ("mod16_output", 211), ("macro1", 218), ("macro4", 221),
                      ("pitch_bend", 227)):
        assert f[key]["at"] == {"layer": 1, "off": 0x3460 + 4 * slot, "len": 4}, key
        assert f[key]["value"] == pytest.approx(slot / 1000, abs=1e-4), key
    assert "Mod 8 out" in f["mod7_output"]["note"]
    assert "0-1 spans 0-100 %" == f["master_vol"]["note"]
    assert len(f) == 248
    unnamed = sorted(int(k[6:-1]) for k in f if k.startswith("param["))
    assert unnamed == [80, 222] + list(range(228, 248))


def test_switches_read_on_and_off():
    vals = [0.0] * 248
    vals[212] = 1.0                     # osc A on
    vals[160] = 1.0                     # reverb on
    doc = _doc(seeds.vst_fxp(seeds.serum1_chunk(seeds.serum1_patch(params=vals))))
    f = _fields(_node(doc, "patch/params"))
    # shown as on and off; the machine value is the stored float
    assert (f["osc_a_on"]["display"], f["osc_b_on"]["display"]) == ("on", "off")
    assert f["reverb_on"]["value"] == 1.0 and f["reverb_on"]["type"] == "f32le"


def test_preset_text_and_file_references():
    patch = seeds.serum1_patch(
        name="A Name Longer Than Twenty-Eight", author="someone", description="a pad",
        macros=("TONE", "Macro 2", "", "SPACE"), osc_b="Digital/Other.wav",
        fx_order=(9, 8, 7, 6, 5, 4, 3, 2, 1, 0))
    doc = _doc(seeds.vst_fxp(seeds.serum1_chunk(patch)))
    info = _fields(_node(doc, "patch/info"))
    # text stops at its NUL; the 0xFF after it is leftover bytes
    assert info["preset_name"]["value"] == "A Name Longer Than Twenty-Eight"
    assert info["preset_name"]["at"] == {"layer": 1, "off": 0x4972, "len": 32}
    assert info["author"]["value"] == "someone"
    assert info["description"]["value"] == "a pad"
    assert [info["macro%d_label" % i]["value"] for i in range(1, 5)] == [
        "TONE", "Macro 2", "", "SPACE"]
    src = _fields(_node(doc, "patch/sources"))
    assert src["osc_a_wavetable"]["value"] == "Analog/Seed Saw.wav"
    assert src["osc_b_wavetable"]["value"] == "Digital/Other.wav"
    assert src["noise_sample"]["value"] == "Seed/Noise.wav"
    assert src["noise_sample"]["at"]["off"] == 0x4008
    assert _fields(_node(doc, "patch/fx_order"))["fx_order"]["value"] == "9 8 7 6 5 4 3 2 1 0"


def test_wavetable_frames_and_a_tuning():
    tun = b"; AnaMark section\r\n[Scale Begin]\r\n" + b"note 0=0.0\r\n" * 20
    frames = struct.pack("<4096f", *([0.25] * 4096))
    doc = _doc(seeds.vst_fxp(seeds.serum1_chunk(tables=frames + tun)))
    tables = _node(doc, "tables")
    assert "2 frames of 2048 samples, and a tuning" in tables["summary"]
    assert _fields(tables)["frames"]["value"] == 2
    t = _node(doc, "tables/tuning")
    assert t["extent"] == {"layer": 2, "off": 16384, "len": len(tun)}
    assert _fields(t)["first_line"]["value"] == "; AnaMark section"
    assert _node(doc, "tables/samples")["extent"]["len"] == 16384


def test_an_older_shorter_record_decodes_what_it_holds(tmp_path):
    # a record that ends inside the text block: parameters and paths, no info
    label, chunks, warns = _walk(tmp_path, seeds.vst_fxp(seeds.serum1_chunk(
        seeds.serum1_patch()[:17000])))
    assert not warns
    ids = [c["id"] for c in chunks[1]["layer_chunks"]]
    assert ids == ["params", "fx_order", "sources"]


@pytest.mark.parametrize("build,code", [
    (lambda: seeds.serum1_chunk(trailer=12345), "field.inconsistent"),
    (lambda: seeds.serum1_chunk(tail=b"junk"), "bytes.stray"),
    (lambda: seeds.serum1_chunk()[:-2], "chunk.short"),
    (lambda: (lambda c, n: c[:n] + b"\x00" * 6 + c[n:])(
        seeds.serum1_chunk(), len(zlib.compress(seeds.serum1_patch()))), "parse.failed"),
])
def test_a_broken_chunk_is_a_finding_not_a_crash(tmp_path, build, code):
    label, chunks, warns = _walk(tmp_path, seeds.vst_fxp(build()))
    assert code in _codes(warns)
    assert chunks[1]["id"].endswith("patch")       # the patch still decodes
    doc = _doc(seeds.vst_fxp(build()))
    assert not [f for f in doc["findings"] if f["kind"] == "error"]


def test_a_chunk_that_is_not_serum_stays_opaque(tmp_path):
    label, chunks, warns = _walk(tmp_path, seeds.vst_fxp(bytes(16)))
    assert [c["id"].split("/")[-1] for c in chunks] == ["CcnK", "chunk"]
    assert "parse.failed" in _codes(warns)
    # another plugin's chunk is not judged at all
    label, chunks, warns = _walk(tmp_path, seeds.vst_fxp(bytes(16), plugin=b"NiMs"))
    assert [c["id"].split("/")[-1] for c in chunks] == ["CcnK", "chunk"] and not warns


def test_preset_meta_reads_the_record():
    blob = seeds.vst_fxp(seeds.serum1_chunk(seeds.serum1_patch(
        name="A Name Longer Than Twenty-Eight", author="someone", description="a pad")),
        name=b"A Name Longer Than Twenty-Eight")
    m = preset_meta.extract(blob)
    assert m == {"preset_name": "A Name Longer Than Twenty-Eight", "device": "Serum",
                 "product": "Xfer Serum", "creator": "someone", "category": None,
                 "description": "a pad", "tags": []}
    # an empty record name falls back to the header's 28 bytes
    m = preset_meta.extract(seeds.vst_fxp(seeds.serum1_chunk(seeds.serum1_patch(name=""))))
    assert m["preset_name"] == "Seed Preset"
    assert preset_meta.extract(seeds.vst_fxp(bytes(16))) is None
    assert preset_meta.extract(seeds.vst_fxp(seeds.serum1_chunk(), plugin=b"NiMs")) is None


def test_the_index_reads_a_serum_preset(tmp_path):
    from acidcat.core.catalogue import indexing
    p = tmp_path / "s.fxp"
    p.write_bytes(seeds.vst_fxp(seeds.serum1_chunk()))
    assert indexing._sniff_format(str(p)) == "preset"
    q = tmp_path / "n.fxp"
    q.write_bytes(seeds.vst_fxp(bytes(16), plugin=b"NiMs"))
    assert indexing._sniff_format(str(q)) is None
