"""acidcat.open() and the Document views (2.0, milestone 1).

A Document is a read-only view over the contract v1 dict: every seed opens,
every positioned field and every node is named by an ADDR that resolves back
to it, a field in a decoded layer is reachable by its layer-qualified address,
and the forensic findings sit in the same list as the walker's, once each.
"""

import json
import pathlib

import pytest

import acidcat
import seeds
from acidcat import AddrError, Loc
from acidcat.core.infra.source import BytesSource, as_source

SCHEMA = json.loads((pathlib.Path(__file__).parent.parent / "docs" / "contract"
                     / "node-v1.schema.json").read_text(encoding="utf-8"))


def _seed(tmp_path, fmt):
    p = tmp_path / ("seed" + seeds.suffix(fmt))
    p.write_bytes(seeds.build(fmt))
    return p


@pytest.fixture
def ym(tmp_path):
    p = tmp_path / "tune.ym"
    p.write_bytes(seeds.SEEDS["ym"][0](packed="lh5"))
    return p


@pytest.fixture
def tailed_wav(tmp_path):
    p = tmp_path / "tail.wav"
    p.write_bytes(seeds.build("wav") + b"PK\x03\x04" + bytes(40))
    return p


# ── every seed ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt", sorted(seeds.SEEDS))
def test_every_seed_opens_and_every_address_resolves_back(tmp_path, fmt):
    """The milestone's rule: every node and every positioned field of every
    seed is named by an ADDR that resolves to that same node or field, and a
    byte-positioned field's address resolves to its own bytes."""
    doc = acidcat.open(_seed(tmp_path, fmt))
    assert doc.format.label
    nodes = list(doc.walk())
    for n in nodes:
        assert doc.node(n.addr) == n, n.addr
        for f in n.fields:
            if f.at is None:
                continue
            assert doc.field(f.addr) == f, f.addr
            if f.at.off is not None:
                assert doc.resolve(f.addr) == Loc(f.at.layer, f.at.off, f.at.len)
                data = doc.layer_bytes(f.at.layer)
                assert doc.read(f.addr) == data[f.at.off:f.at.end]


def test_the_seeds_have_fields_to_resolve(tmp_path):
    """Guards the guard above: it has to be resolving something."""
    n = 0
    for fmt in sorted(seeds.SEEDS):
        doc = acidcat.open(_seed(tmp_path, fmt), forensics=False)
        n += sum(1 for x in doc.walk() for f in x.fields if f.at is not None)
    assert n > 500


# ── layers ─────────────────────────────────────────────────────────────

def test_a_field_in_a_decoded_layer_resolves_by_its_layer(ym):
    doc = acidcat.open(ym)
    f = doc.field("1:lh5/header#frames")
    assert f.name == "frames" and f.at == Loc(1, 12, 4)
    assert f.addr == "1:lh5/header#frames"
    assert doc.field("lh5/header#frames") == f          # the id says the layer
    assert doc.layer_bytes(1)[:4] == b"YM5!"
    assert doc.read(f.addr) == doc.layer_bytes(1)[12:16]
    assert [l.id for l in doc.layers] == [0, 1]
    assert doc.layers[1].decoder["name"] == "lha.lh5"


def test_a_layer_that_disagrees_with_the_node_is_an_error(ym):
    doc = acidcat.open(ym)
    with pytest.raises(AddrError, match="layer 1, not layer 0"):
        doc.field("0:lh5/header#frames")


def test_layer_zero_is_the_file(ym):
    doc = acidcat.open(ym)
    assert doc.layer_bytes(0) == ym.read_bytes()


# ── the grammar ────────────────────────────────────────────────────────

def test_a_node_address_means_its_payload(tmp_path):
    doc = acidcat.open(_seed(tmp_path, "wav"))
    fmt = doc.find("fmt")[0]
    assert doc.resolve(fmt.addr) == fmt.payload


@pytest.mark.parametrize("addr,want", [
    ("@0x10", Loc(0, 0x10, 0)),
    ("@16+4", Loc(0, 16, 4)),
    ("@0x10..0x18", Loc(0, 16, 8)),
])
def test_offsets_and_ranges(tmp_path, addr, want):
    doc = acidcat.open(_seed(tmp_path, "wav"))
    assert doc.resolve(addr) == want


def test_a_range_relative_to_a_payload(tmp_path):
    doc = acidcat.open(_seed(tmp_path, "wav"))
    fmt = doc.find("fmt")[0]
    loc = doc.resolve(fmt.id + "[4:4]")
    assert loc == Loc(0, fmt.payload.off + 4, 4)
    assert doc.read(loc) == doc.read(fmt.id + "#sample_rate")


def test_a_range_past_the_layer_is_an_error(tmp_path):
    doc = acidcat.open(_seed(tmp_path, "wav"))
    with pytest.raises(AddrError, match="run past"):
        doc.resolve("@0+999999")


def test_an_end_before_the_start_is_an_error(tmp_path):
    doc = acidcat.open(_seed(tmp_path, "wav"))
    with pytest.raises(AddrError, match="before the start"):
        doc.resolve("@0x20..0x10")


def test_globs_list_nodes_and_are_refused_where_one_is_needed(ym):
    doc = acidcat.open(ym)
    ids = [n.id for n in doc.find("lh5/*")]
    assert ids[:2] == ["lh5/header", "lh5/digidrum_0"]
    assert [n.id for n in doc.find("**/header")] == ["lh5/header"]
    with pytest.raises(AddrError) as e:
        doc.node("lh5/*")
    assert "lh5/header" in e.value.candidates


def test_a_bare_name_resolves_only_when_it_is_unique(tmp_path):
    doc = acidcat.open(_seed(tmp_path, "wav"))
    assert doc.node("fmt").name.strip() == "fmt"
    with pytest.raises(AddrError, match="no node"):
        doc.node("nothing_is_called_this")


def test_a_missing_field_lists_the_keys_there_are(tmp_path):
    doc = acidcat.open(_seed(tmp_path, "wav"))
    fmt = doc.find("fmt")[0]
    with pytest.raises(AddrError) as e:
        doc.field(fmt.id + "#nope")
    assert "sample_rate" in e.value.candidates


def test_a_node_address_is_not_a_field(tmp_path):
    doc = acidcat.open(_seed(tmp_path, "wav"))
    with pytest.raises(AddrError, match="add #KEY"):
        doc.field(doc.find("fmt")[0].id)


@pytest.mark.parametrize("bad", ["", "@zz", "@0x10+zz"])
def test_nonsense_is_an_address_error(tmp_path, bad):
    doc = acidcat.open(_seed(tmp_path, "wav"))
    with pytest.raises(AddrError):
        doc.resolve(bad)


def test_hex_and_decimal_are_the_same_number(tmp_path):
    doc = acidcat.open(_seed(tmp_path, "wav"))
    assert doc.resolve("@0x20+0x4") == doc.resolve("@32+4")


# ── findings ───────────────────────────────────────────────────────────

def test_forensic_findings_join_the_walkers_once_each(tailed_wav):
    doc = acidcat.open(tailed_wav)
    codes = [f.code for f in doc.findings]
    assert codes.count("container.trailing") == 1       # the walker's, not echoed
    assert "anomaly.polyglot" in codes and "anomaly.trailing_data" in codes
    poly = next(f for f in doc.findings if f.code == "anomaly.polyglot")
    assert poly.kind == "info" and poly.at == Loc(0, 172, 0)   # a suspicion (review R8)


def test_forensics_can_be_left_out(tailed_wav):
    doc = acidcat.open(tailed_wav, forensics=False)
    assert not [f for f in doc.findings if f.code.startswith("anomaly.")]


def test_a_document_with_forensic_findings_is_still_a_v1_document(tailed_wav):
    jsonschema = pytest.importorskip("jsonschema")
    doc = acidcat.open(tailed_wav)
    jsonschema.Draft202012Validator(SCHEMA).validate(doc.to_json())


# ── inputs ─────────────────────────────────────────────────────────────

def test_bytes_and_a_path_give_the_same_document(ym):
    a = acidcat.open(ym).to_json()
    b = acidcat.open(ym.read_bytes()).to_json()
    assert a == b


def test_bytes_leave_no_temporary_file_behind(ym, tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    before = set(tmp_path.iterdir())
    acidcat.open(ym.read_bytes())
    assert set(tmp_path.iterdir()) == before


def test_a_source_opens_and_stays_the_callers(ym):
    src = as_source(str(ym))
    try:
        doc = acidcat.open(src)
        assert doc.field("1:lh5/header#frames").at == Loc(1, 12, 4)
        assert src.read(0, 1)                         # not closed by open()
    finally:
        src.close()
    doc = acidcat.open(BytesSource(ym.read_bytes()))
    assert doc.layer_bytes(1)[:4] == b"YM5!"


# ── read-only ──────────────────────────────────────────────────────────

def test_views_are_read_only(ym):
    doc = acidcat.open(ym)
    f = doc.field("1:lh5/header#frames")
    for obj, attr in ((f, "value"), (f.node, "summary"), (doc.layers[0], "name"),
                      (doc.findings[0] if doc.findings else f, "code")):
        with pytest.raises(AttributeError):
            setattr(obj, attr, 1)
    with pytest.raises(AttributeError):
        f.whatever = 1


def test_to_json_is_a_copy(ym):
    doc = acidcat.open(ym)
    d = doc.to_json()
    d["nodes"].clear()
    assert doc.nodes


def test_the_views_read_the_dict_not_a_copy(ym):
    doc = acidcat.open(ym)
    f = doc.field("1:lh5/header#frames")
    assert f.raw is next(x for x in f.node.raw["fields"] if x["key"] == "frames")
