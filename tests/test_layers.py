"""Decoded layers (node-v1.md section 3): a packed YM's tune is layer 1.

The done test for layers: the YM frame count has a byte range in layer 1, and
`carve --layer 1` writes the unpacked image byte for byte as the walk verified
it. Built on seeds rather than real files: the -lh5- member is a real stream
(one block of literals), and its CRC-16 is checked on both paths.
"""

import os
import subprocess
import sys

import pytest

import seeds
from acidcat.core.infra import contract, layers
from acidcat.core.infra.limits import Limits
from acidcat.core.walk import walk_file

IMAGE = seeds.SEEDS["ym"][0]()


def _packed(tmp_path, how):
    p = tmp_path / ("tune-%s.ym" % how)
    p.write_bytes(seeds.SEEDS["ym"][0](packed=how))
    return str(p)


@pytest.fixture(params=["lh5", True], ids=["lh5", "lh0"])
def packed(request, tmp_path):
    return _packed(tmp_path, request.param)


def test_the_frame_count_has_a_byte_range_in_layer_1(packed):
    doc = contract.walk(packed)
    lay = doc["layers"][1]
    assert lay["kind"] == "derived" and lay["parent"] == 0
    assert lay["verdict"] == {"result": "verified", "method": "crc16",
                              "detail": "0x%04X" % seeds_crc()}
    head = contract.node(doc, lay["from_node"] + "/header")
    frames = next(f for f in head["fields"] if f["name"] == "frames")
    assert frames["at"] == {"layer": 1, "off": 12, "len": 4}
    b = layers.layer_bytes(doc, 1, open(packed, "rb").read())
    assert int.from_bytes(b[12:16], "big") == 4 == int(frames["value"])


def seeds_crc():
    from acidcat.core.codecs.lha import crc16
    return crc16(IMAGE)


def test_the_decoder_and_mapping_are_the_member_method(tmp_path):
    lh5 = contract.walk(_packed(tmp_path, "lh5"))["layers"][1]
    assert (lh5["decoder"]["name"], lh5["mapping"]) == ("lha.lh5", "opaque")
    assert "layer_off" not in lh5["sources"][0]
    lh0 = contract.walk(_packed(tmp_path, True))["layers"][1]
    assert (lh0["decoder"]["name"], lh0["mapping"]) == ("lha.lh0", "exact")
    assert lh0["sources"][0]["layer_off"] == 0


def test_the_packed_node_opens_the_layer(packed):
    doc = contract.walk(packed)
    lay = doc["layers"][1]
    node = contract.node(doc, lay["from_node"])
    assert node["caps"]["descend"] == {"layer": 1, "source": "declared"}
    assert node["extent"]["layer"] == 0
    kids = [c["id"].rsplit("/", 1)[1] for c in node["children"]]
    assert kids[:1] == ["header"] and "registers" in kids
    assert all(c["extent"]["layer"] == 1 for c in node["children"])


def test_the_legacy_chunk_list_is_unchanged(packed):
    """Every consumer of walk_file reads a chunk's offset as a file offset, so
    the layer's chunks ride on their container and never join the list."""
    _label, chunks, _w = walk_file(packed)
    ids = [c["id"] for c in chunks]
    assert ids in (["lha_header", "lh5", "archive_end"],
                   ["lha_header", "lh0", "archive_end"])
    body = chunks[1]
    assert body["layer"]["decoder"] in ("lha.lh5", "lha.lh0")
    assert [c["id"] for c in body["layer_chunks"]][0] == "header"
    assert all(c["geometry"] == "declared" for c in body["layer_chunks"])


def _carve(*args):
    return subprocess.run([sys.executable, "-m", "acidcat", "carve", *args],
                          capture_output=True, timeout=120)


def test_carve_layer_1_writes_the_verified_image(packed, tmp_path):
    out = tmp_path / "unpacked.ym"
    r = _carve(packed, "--layer", "1", "-o", str(out))
    assert r.returncode == 0, r.stderr.decode()
    assert out.read_bytes() == IMAGE
    assert b"verified crc16" in r.stderr


def test_carve_layer_0_is_the_file(packed, tmp_path):
    out = tmp_path / "same.ym"
    assert _carve(packed, "--layer", "0", "-o", str(out)).returncode == 0
    assert out.read_bytes() == open(packed, "rb").read()


def test_carve_a_layer_the_file_does_not_have(tmp_path):
    bare = tmp_path / "bare.ym"
    bare.write_bytes(IMAGE)
    r = _carve(str(bare), "--layer", "1", "-o", str(tmp_path / "x"))
    assert r.returncode == 1, r
    assert b"no layer 1" in r.stderr
    assert not os.path.exists(tmp_path / "x")


def test_a_depth_limit_stops_the_descent_and_says_so(packed):
    doc = contract.walk(packed, limits=Limits(depth=0))
    assert len(doc["layers"]) == 1
    cov = [f for f in doc["findings"] if f["code"] == "cap.depth"]
    assert cov and doc["limits"]["hit"] == ["depth"]


def test_an_inflate_limit_stops_the_decode_and_says_so(packed):
    doc = contract.walk(packed, limits=Limits(inflate_bytes=64))
    assert len(doc["layers"]) == 1
    cov = [f for f in doc["findings"] if f["code"] == "cap.inflate"]
    assert cov and cov[0]["cap"] == {"name": "inflate_bytes", "limit": 64,
                                     "used": len(IMAGE)}


def test_a_layer_that_does_not_decode_is_an_error_finding(packed, monkeypatch):
    """The walker verified the image; if the registry then cannot decode it,
    that is a bug in acidcat, reported rather than raised."""
    def broken(src, params, cap):
        raise layers.LayerError("simulated")
    for name in ("lha.lh5", "lha.lh0"):
        _fn, check, m = layers.DECODERS[name]
        monkeypatch.setitem(layers.DECODERS, name, (broken, check, m))
    doc = contract.walk(packed)
    assert len(doc["layers"]) == 1
    assert [f for f in doc["findings"] if f["code"] == "layer.error"
            and f["kind"] == "error"]


def test_layer_bytes_refuses_bytes_that_fail_their_check(packed):
    doc = contract.walk(packed)
    data = bytearray(open(packed, "rb").read())
    s = doc["layers"][1]["sources"][0]
    data[s["parent_off"] + s["len"] - 2] ^= 0xFF      # inside the body
    with pytest.raises(layers.LayerError):
        layers.layer_bytes(doc, 1, bytes(data))


def test_the_layered_document_validates(packed):
    jsonschema = pytest.importorskip("jsonschema")
    import json
    import pathlib
    schema = json.loads((pathlib.Path(__file__).parent.parent / "docs" / "contract"
                         / "node-v1.schema.json").read_text(encoding="utf-8"))
    doc = json.loads(json.dumps(contract.walk(packed)))
    errs = list(jsonschema.Draft202012Validator(schema).iter_errors(doc))
    assert not errs, [(e.json_path, e.message[:100]) for e in errs[:3]]
