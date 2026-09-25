"""Node contract v1: every walk, described as a Document, keeps the spec's rules.

docs/contract/node-v1.md section 14 lists the conformance rules; this is them.
The corpus is every seed (so the rules gate in a clone), every committed
specimen under data/, and, when ACIDCAT_HUNT_CORPUS is set, a bounded slice of
the real local corpus.

Each rule that has known exceptions keeps them in a shrink-only ledger, the way
test_chunk_geometry does: the set is today's measurement and may only fall.
"""

import glob
import io
import json
import os
import pathlib
import re
import tempfile

import pytest

import seeds
from acidcat.core.infra import contract

ROOT = pathlib.Path(__file__).parent.parent
SCHEMA = json.loads((ROOT / "docs" / "contract" / "node-v1.schema.json")
                    .read_text(encoding="utf-8"))
_MAX = 8_000_000
_HUNT_PER_FORMAT = 20


def _corpus():
    tmp = tempfile.mkdtemp(prefix="acidcat-contract-")
    for name in sorted(seeds.SEEDS):
        p = os.path.join(tmp, name + seeds.suffix(name))
        with io.open(p, "wb") as fh:
            fh.write(seeds.build(name))
        yield name, p
    for p in sorted(glob.glob(str(ROOT / "data" / "**" / "*.*"), recursive=True)):
        if os.path.getsize(p) <= _MAX:
            yield os.path.basename(p), p
    roots = os.environ.get("ACIDCAT_HUNT_CORPUS")
    if roots:
        from acidcat.core.infra.sniff import sniff
        per = {}
        for root in roots.split(os.pathsep):
            for dirpath, _d, files in os.walk(root):
                for fn in files:
                    p = os.path.join(dirpath, fn)
                    try:
                        if os.path.getsize(p) > _MAX:
                            continue
                        fmt = sniff(p)
                    except OSError:
                        continue
                    if fmt and per.get(fmt, 0) < _HUNT_PER_FORMAT:
                        per[fmt] = per.get(fmt, 0) + 1
                        yield fn, p


@pytest.fixture(scope="module")
def docs():
    out = []
    from acidcat.core.walk.base import Unsupported
    for name, path in _corpus():
        try:
            doc = contract.walk(path, deep=True)
        except Unsupported:
            continue                    # not a file acidcat reads: no Document
        except Exception as e:          # rule 11: degrade, never raise
            out.append((name, path, e))
            continue
        with io.open(path, "rb") as fh:
            out.append((name, path, (doc, fh.read())))
    return out


def _ok(docs):
    return [(n, p, d) for n, p, d in docs if not isinstance(d, Exception)]


def _nodes(doc):
    return list(contract.iter_nodes(doc))


# ── rule 11: degrade, never raise ──────────────────────────────────────

def test_the_normaliser_never_raises(docs):
    raised = [(n, repr(d)[:120]) for n, p, d in docs if isinstance(d, Exception)]
    assert not raised, "the normaliser raised:\n" + "\n".join(
        f"  {n}: {e}" for n, e in raised[:10])


def test_it_saw_every_seed(docs):
    """Guards the guard: a corpus that shrank to nothing passes everything."""
    names = {n for n, p, d in docs}
    assert set(seeds.SEEDS) <= names


# ── rule 1: the schema ─────────────────────────────────────────────────

def test_every_document_validates_against_the_schema(docs):
    jsonschema = pytest.importorskip("jsonschema")
    v = jsonschema.Draft202012Validator(SCHEMA)
    bad = []
    for name, path, (doc, _data) in _ok(docs):
        # through JSON, so a tuple or a bytes value that would not survive
        # serialisation fails here rather than in a consumer
        errs = list(v.iter_errors(json.loads(json.dumps(doc))))
        if errs:
            bad.append((name, errs[0].json_path, errs[0].message[:120]))
    assert not bad, "documents that do not validate:\n" + "\n".join(
        f"  {n} {p}: {m}" for n, p, m in bad[:10])


# ── rules 2-5: locators, the tree, ids ─────────────────────────────────

def test_every_byte_locator_is_inside_its_layer(docs):
    bad = []
    for name, path, (doc, _data) in _ok(docs):
        lengths = {l["id"]: l["length"] for l in doc["layers"]}
        for n in _nodes(doc):
            locs = [("extent", n.get("extent")), ("payload", n.get("payload"))]
            locs += [(f"#{f['key']}", f.get("at")) for f in n["fields"]]
            for what, loc in locs:
                if not loc or loc.get("kind") == "path":
                    continue
                if not (0 <= loc["off"] and loc["off"] + loc["len"]
                        <= lengths[loc["layer"]]):
                    bad.append((name, n["id"] + (what if what[0] == "#" else " " + what)))
    assert not bad, "locators outside their layer:\n" + "\n".join(
        f"  {n}: {w}" for n, w in bad[:10])


def test_fields_lie_inside_their_node(docs):
    bad = []
    for name, path, (doc, _data) in _ok(docs):
        for n in _nodes(doc):
            ext = n.get("extent")
            for f in n["fields"]:
                at = f.get("at")
                if not at or at.get("kind") == "path" or f.get("remote") or not ext:
                    continue
                if not (ext["off"] <= at["off"]
                        and at["off"] + at["len"] <= ext["off"] + ext["len"]):
                    bad.append((name, f"{n['id']}#{f['key']}"))
    assert not bad, "fields outside their node:\n" + "\n".join(
        f"  {n}: {w}" for n, w in bad[:10])


# (format or specimen name, parent id) -> why its children overlap. Shrink only.
KNOWN_SIBLING_OVERLAPS = {}


def test_children_lie_inside_their_parent_and_do_not_overlap(docs):
    outside, overlap = [], []
    for name, path, (doc, _data) in _ok(docs):
        for n in _nodes(doc):
            kids = [c for c in n["children"] if "extent" in c]
            if "extent" in n:
                lo, hi = n["extent"]["off"], n["extent"]["off"] + n["extent"]["len"]
                for c in kids:
                    e = c["extent"]
                    if not (lo <= e["off"] and e["off"] + e["len"] <= hi):
                        outside.append((name, c["id"]))
            kids.sort(key=lambda c: c["extent"]["off"])
            for a, b in zip(kids, kids[1:]):
                if a["extent"]["off"] + a["extent"]["len"] > b["extent"]["off"]:
                    if (name, n["id"]) not in KNOWN_SIBLING_OVERLAPS:
                        overlap.append((name, a["id"], b["id"]))
    assert not outside, "children outside their parent:\n" + "\n".join(
        f"  {n}: {c}" for n, c in outside[:10])
    assert not overlap, "siblings that overlap:\n" + "\n".join(
        f"  {n}: {a} / {b}" for n, a, b in overlap[:10])


def test_ids_and_keys_are_unique(docs):
    bad = []
    for name, path, (doc, _data) in _ok(docs):
        ids = [n["id"] for n in _nodes(doc)]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            bad.append((name, sorted(dup)[:3]))
        for n in _nodes(doc):
            keys = [f["key"] for f in n["fields"]]
            if len(keys) != len(set(keys)):
                bad.append((name, n["id"]))
    assert not bad, "duplicate ids or keys:\n" + "\n".join(
        f"  {n}: {w}" for n, w in bad[:10])


def test_ids_are_stable_across_runs(docs):
    """Bookmarks and golden tests key on ids; the same file must give the
    same ones twice."""
    moved = []
    for name, path, (doc, _data) in _ok(docs)[:40]:
        again = contract.walk(path, deep=True)
        if [n["id"] for n in _nodes(doc)] != [n["id"] for n in _nodes(again)]:
            moved.append(name)
    assert not moved, "ids changed between two walks: " + ", ".join(moved)


# ── rule 7: re-read ────────────────────────────────────────────────────

def test_every_encoded_field_reads_back_as_its_value(docs):
    """Every field typed from a walker's declaration (`declared`) or its legacy
    `enc` annotation is read from its bytes, transformed, and must equal its
    value. Inferred types are left out on purpose: the normaliser inferred them
    by reading the bytes, so they pass by construction and prove nothing."""
    bad, checked = [], 0
    for name, path, (doc, data) in _ok(docs):
        for n in _nodes(doc):
            node_off = n["extent"]["off"] if "extent" in n else None
            for f in n["fields"]:
                if f["type_source"] not in ("declared", "enc") or "at" not in f:
                    continue
                at = f["at"]
                b = data[at["off"]:at["off"] + at["len"]]
                try:
                    stored = contract.read_type(f["type"], b)
                except ValueError:
                    continue            # not a fixed-width type
                got = contract.apply_xform(f.get("xform"), stored, at["off"], node_off)
                checked += 1
                want = f["value"]
                if isinstance(got, float) or isinstance(want, float):
                    same = abs(float(got) - float(want)) <= 1e-6 * max(1.0, abs(float(want)))
                else:
                    same = got == want
                if not same:
                    bad.append((name, f"{n['id']}#{f['key']}", f["type"], want, got))
    assert checked > 50, f"only {checked} encoded fields checked; the corpus is wrong"
    assert not bad, "encoded fields whose bytes say otherwise:\n" + "\n".join(
        f"  {n}: {k} ({t}) value {w!r}, bytes {g!r}" for n, k, t, w, g in bad[:10])


# ── rules 8 and 10: pointers and audio caps ────────────────────────────

def test_every_pointer_lands_in_its_layer_or_says_it_dangles(docs):
    bad = []
    for name, path, (doc, _data) in _ok(docs):
        lengths = {l["id"]: l["length"] for l in doc["layers"]}
        dangling = {f.get("node") for f in doc["findings"]
                    if f["code"] == "pointer.dangling"}
        for n in _nodes(doc):
            for f in n["fields"]:
                p = f.get("ptr")
                if p and not (0 <= p["off"] <= lengths[p["layer"]]) \
                        and n["id"] not in dangling:
                    bad.append((name, f"{n['id']}#{f['key']}", p["off"]))
    # xref values that point past EOF are real (a damaged file); until the
    # normaliser emits pointer.dangling for them, this lists them rather than
    # hiding them. Today's seeds have none.
    assert not bad, "pointers outside their layer:\n" + "\n".join(
        f"  {n}: {k} -> 0x{o:x}" for n, k, o in bad[:10])


def test_audio_caps_agree_with_the_fields_they_name(docs):
    bad, seen = [], 0
    for name, path, (doc, _data) in _ok(docs):
        by_addr = {f"{n['id']}#{f['key']}": f
                   for n in _nodes(doc) for f in n["fields"]}
        for n in _nodes(doc):
            a = n["caps"].get("audio")
            if not a:
                continue
            seen += 1
            for addr in a["fields"]:
                f = by_addr.get(addr)
                if f is None:
                    bad.append((name, addr, "names no field"))
                    continue
                want = {"sample_rate": a["rate"], "channels": a["channels"],
                        "num_channels": a["channels"],
                        "bits_per_sample": a["bits"]}.get(f["name"])
                if want is not None and f["value"] != want:
                    bad.append((name, addr, f"{f['value']!r} != cap {want!r}"))
    assert seen, "no audio cap anywhere; the capability rules did not run"
    assert not bad, "audio caps that disagree with their fields:\n" + "\n".join(
        f"  {n}: {a} {w}" for n, a, w in bad[:10])


# ── findings and typing ────────────────────────────────────────────────

def test_a_warning_is_reported_once(docs):
    """Walkers copy chunk notes to file level; the Document reports each
    message once per place."""
    bad = []
    for name, path, (doc, _data) in _ok(docs):
        seen = {}
        for f in doc["findings"]:
            seen[f["message"]] = seen.get(f["message"], 0) + 1
        twice = [m for m, c in seen.items() if c > 1
                 and len({x.get("node") for x in doc["findings"]
                          if x["message"] == m}) == 1]
        if twice:
            bad.append((name, twice[0][:80]))
    assert not bad, "findings reported twice in one place:\n" + "\n".join(
        f"  {n}: {m}" for n, m in bad[:10])


def test_typing_counts_are_what_the_document_holds(docs):
    for name, path, (doc, _data) in _ok(docs):
        fields = [f for n in _nodes(doc) for f in n["fields"]]
        t = doc["typing"]
        assert t["fields"] == len(fields), name
        assert t["positioned"] == sum(1 for f in fields if "at" in f), name
        for src in ("declared", "enc", "inferred"):
            assert t["typed_" + src] == sum(1 for f in fields
                                            if f["type_source"] == src), name
        assert t["nodes"] == len(_nodes(doc)), name


def test_a_display_field_makes_no_claim_about_its_bytes(docs):
    """`display` means no type is known; it must not also claim `enc`-level
    trust. `derived` means no bytes; it must not carry a locator."""
    bad = []
    for name, path, (doc, _data) in _ok(docs):
        for n in _nodes(doc):
            for f in n["fields"]:
                if f["type"] == "display" and f["type_source"] != "none":
                    bad.append((name, n["id"], f["key"], "display with a source"))
                if f["type"] == "derived" and "at" in f:
                    bad.append((name, n["id"], f["key"], "derived with a locator"))
    assert not bad, "\n".join(f"  {a}: {b}#{c} {d}" for a, b, c, d in bad[:10])


# ── the pieces, directly ───────────────────────────────────────────────

@pytest.mark.parametrize("enc,typ", [
    ("<H", "u16le"), (">H", "u16be"), ("<I", "u32le"), (">i", "i32be"),
    ("B", "u8"), ("<Q", "u64le"), (">f", "f32be"), ("float80", "f80be"),
    ("u24be", "u24be"), ("synchsafe", "synchsafe"),
])
def test_legacy_encodings_map_to_v1_types(enc, typ):
    assert contract.type_of_enc(enc)[0] == typ


def test_a_bit_field_moves_onto_its_container_and_carries_its_bias():
    typ, shift, clen, xform = contract.type_of_enc("bits:2:3:4:3:-1")
    assert (typ, shift, clen, xform) == ("bits:3:4:3", 2, 3, "add(1)")


@pytest.mark.parametrize("xform,stored,want", [
    ("add(1)", 1, 2), ("neg", -84, 84), ("mul(2)", 1, 2),
    ("fixed(16.16)", 44100 << 16, 44100.0), ("mask(0x7f)", 0xB0, 0x30),
    ("ascii-dec", "##02", 2), ("mask(0x7f);add(1)", 0x81, 2),
])
def test_transforms(xform, stored, want):
    assert contract.apply_xform(xform, stored) == want


def test_rel_transforms_use_their_anchor():
    assert contract.apply_xform("rel(self)", 0xF9, at=4) == 0xFD
    assert contract.apply_xform("rel(node)", 8, node_off=100) == 108


def test_node_ids_follow_the_spec():
    assert contract.slug("fmt ") == "fmt_"
    assert contract.slug("smp[3]") == "smp[3]"
    assert contract._dedupe(["LIST", "LIST", "data", "LIST"]) == \
        ["LIST", "LIST~2", "data", "LIST~3"]


def test_the_wav_seed_reads_the_way_the_spec_example_does(tmp_path):
    """The spec's WAV example, checked against the real normaliser."""
    p = tmp_path / "a.wav"
    p.write_bytes(seeds.build("wav"))
    doc = contract.walk(str(p))
    ids = [n["id"] for n in contract.iter_nodes(doc)]
    assert "fmt_" in ids and "data" in ids
    fmt = contract.node(doc, "fmt_")
    rate = next(f for f in fmt["fields"] if f["name"] == "sample_rate")
    assert rate["value"] == 44100 and rate["type"] == "u32le"
    assert rate["at"] == {"layer": 0, "off": 24, "len": 4}
    audio = contract.node(doc, "data")["caps"]["audio"]
    assert audio["rate"] == 44100 and "fmt_#sample_rate" in audio["fields"]
    # nothing describes the RIFF header yet: the gap is shown, not hidden
    assert ids[0] == "unwalked" and contract.node(doc, "unwalked")["extent"] == \
        {"layer": 0, "off": 0, "len": 12}


# ── the TypedDicts mirror the schema ───────────────────────────────────

@pytest.mark.parametrize("typed,schema_def", [
    ("Document", None), ("Node", "node"), ("Field", "field"),
    ("Layer", "layer"), ("Finding", "finding"),
])
def test_typeddicts_and_schema_name_the_same_keys(typed, schema_def):
    """Two descriptions of one shape drift unless something holds them
    together (decision C5)."""
    td = getattr(contract, typed).__annotations__
    props = (SCHEMA["properties"] if schema_def is None
             else SCHEMA["$defs"][schema_def]["properties"])
    assert set(td) == set(props), (
        f"{typed}: only in the TypedDict {sorted(set(td) - set(props))}, "
        f"only in the schema {sorted(set(props) - set(td))}")


def test_loc_covers_both_locator_kinds():
    keys = set(contract.Loc.__annotations__)
    assert set(SCHEMA["$defs"]["byteLoc"]["properties"]) <= keys
    assert set(SCHEMA["$defs"]["pathLoc"]["properties"]) <= keys
