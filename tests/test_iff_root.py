"""An IFF-shaped file's Document has one root node, the container its header
declares, so ids are `RIFF/fmt_`, `FORM/COMM`, `RF64/ds64` (node-v1.md
sections 5.1 and 13).

Before, every WAV's ids were `unwalked, fmt_, data`: the 12-byte header was a
gap and `RIFF/fmt_#sample_rate`, the address every doc used, named nothing.
The root is made in the normaliser from the header's own bytes (or, for a
walker that gave the header a node of its own, by growing that node), so the
walkers' flat chunk lists, the 1.x tuple API, are unchanged.
"""

import re
import struct

import pytest

import acidcat
import seeds
from acidcat.core.walk import walk_file


def _ck(cid, p):
    return cid + struct.pack("<I", len(p)) + p + (b"\0" if len(p) & 1 else b"")


_FMT = struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)


def _wav(extra=b"", riff_size=None):
    body = b"WAVE" + _ck(b"fmt ", _FMT) + _ck(b"data", bytes(64))
    size = len(body) if riff_size is None else riff_size
    return b"RIFF" + struct.pack("<I", size) + body + extra


@pytest.mark.parametrize("fmt,root,child", [
    ("wav", "RIFF", "RIFF/fmt_"),
    ("rf64", "RF64", "RF64/ds64"),
    ("aiff", "FORM", "FORM/COMM"),
    ("aifc", "FORM", "FORM/COMM"),
    ("8svx", "FORM", "FORM/VHDR"),
    ("smus", "FORM", "FORM/SHDR"),
    ("w64", "wave64", "wave64/fmt_"),
])
def test_the_header_is_the_root(fmt, root, child):
    doc = acidcat.open(seeds.build(fmt), forensics=False)
    assert [n.id for n in doc.nodes] == [root]
    assert child in [n.id for n in doc.walk()]
    top = doc.nodes[0]
    assert top.extent.off == 0 and top.payload.off in (12, 40)
    # the header's bytes are the root's, not a gap
    assert not any(n.id.split("/")[-1].startswith("unwalked")
                   and n.extent.off == 0 for n in doc.walk())


def test_the_documented_address_resolves():
    doc = acidcat.open(_wav(), forensics=False)
    assert doc.field("RIFF/fmt_#sample_rate").value == 44100
    assert doc.typing["nodes_unwalked"] == 0
    assert [(f.key, f.value) for f in doc.node("RIFF").fields] == [
        ("magic", "RIFF"), ("riff_size", len(_wav()) - 8), ("form_type", "WAVE")]


def test_bytes_past_the_declared_end_are_a_sibling():
    """An appended archive is outside the RIFF chunk, and the tree says so."""
    doc = acidcat.open(_wav(extra=b"PK\x03\x04" + bytes(28)), forensics=False)
    top = [n.id for n in doc.nodes]
    assert top[0] == "RIFF" and len(top) > 1
    assert doc.nodes[0].extent.len == len(_wav())


def test_a_size_past_the_file_is_clamped_to_it():
    raw = _wav(riff_size=0x7FFFFFF0)
    doc = acidcat.open(raw, forensics=False)
    assert doc.nodes[0].extent.len == len(raw)
    assert "RIFF/data" in [n.id for n in doc.walk()]


def test_a_walker_header_node_is_grown_not_duplicated():
    """8SVX's walker names the header FORM with its own fields; the root is
    that node, its fields still where the walker put them."""
    doc = acidcat.open(seeds.build("8svx"), forensics=False)
    root = doc.nodes[0]
    assert root.id == "FORM"
    assert [(f.key, f.at.off) for f in root.fields] == [
        ("magic", 0), ("form_size", 4), ("form_type", 8)]


def test_a_walker_that_describes_its_own_container_keeps_it():
    doc = acidcat.open(seeds.build("sf2"), forensics=False)
    assert doc.nodes[0].id == "sfbk"


def test_the_tuple_api_is_unchanged(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(_wav())
    _label, chunks, _w = walk_file(str(p))
    assert [str(c["id"]).strip() for c in chunks] == ["fmt", "data"]


def test_a_non_iff_file_gets_no_root():
    doc = acidcat.open(seeds.build("flac"), forensics=False)
    assert doc.nodes[0].id not in ("RIFF", "FORM")


_STEP = r"[A-Za-z0-9_.~]+(?:\[\d+\])?"
_ADDR = re.compile(r"(?<![\w/])(RIFF(?:/" + _STEP + r")+(?:#[A-Za-z0-9_]+)?"
                   r"(?:\[[0-9a-fx]+:[0-9a-fx]+\])?)")


def test_every_documented_riff_address_resolves():
    """Each `RIFF/...` address in the contract docs, the CLI page and the
    edit and addr modules resolves on a WAV that has the chunks they name."""
    import pathlib
    from acidcat.core.infra import addr
    root = pathlib.Path(__file__).resolve().parent.parent
    info = b"INFO" + _ck(b"INAM", b"title\0") + _ck(b"IART", b"me\0\0")
    body = (b"WAVE" + _ck(b"fmt ", _FMT) + _ck(b"LIST", info)
            + _ck(b"LIST", b"adtl")
            + _ck(b"smpl", struct.pack("<9I", 0, 0, 22675, 60, 0, 0, 0, 0, 0))
            + _ck(b"data", bytes(64)))
    doc = acidcat.open(b"RIFF" + struct.pack("<I", len(body)) + body,
                       forensics=False).to_json()
    found = set()
    for rel in ("docs/contract/node-v1.md", "docs/contract/cli-2.0.md",
                "docs/contract/architecture-2.0.md", "src/acidcat/core/edit.py",
                "src/acidcat/core/infra/addr.py", "src/acidcat/core/document.py"):
        for m in _ADDR.finditer((root / rel).read_text(encoding="utf-8")):
            a = m.group(1)
            if a.startswith("RIFF/WAVE") or "*" in a:
                continue                  # prose ("RIFF/WAVE") and globs
            found.add(a)
    assert {"RIFF/fmt_#sample_rate", "RIFF/fmt_[4:4]", "RIFF/LIST~2"} <= found
    for a in sorted(found):
        addr.resolve(doc, a)              # raises AddrError on a miss
