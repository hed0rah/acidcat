"""Node contract v1: today's walker output, described as one Document.

docs/contract/node-v1.md is the specification and node-v1.schema.json its
machine form; this module is the normaliser the spec names. It takes what a
walker returns -- the flat chunk list, the fields with display values and
payload-relative offsets, the warnings as strings -- and produces a Document:
layers, a tree of nodes with stable ids, fields located absolutely in their
layer with a machine value beside the display one, a storage type and where
that type came from, and one list of structured findings.

It adds nothing a walker did not say, and it says when it filled something in:

    type_source  declared | enc | inferred | none
    geometry     declared | defaulted | invalid | unpositioned   (geometry.py)
    origin       normaliser, on a node the walker never emitted (a gap)
    code         "legacy", on a finding whose walker has not given it a code

Walkers migrate from inferred to declared one at a time, the way geometry did
(f1dff46), and the `typing` counts on each Document show how far that has got.

The normaliser never raises on walker output. A chunk it cannot place becomes
an unpositioned node; a field whose bytes it cannot read keeps its display
value with `type: display`.
"""

import re
import struct
from typing import Any, List, Optional, TypedDict

from acidcat.core.infra import fieldcodec
from acidcat.core.primitives.notes import kind_of

CONTRACT = 1

# ── the shape, for readers and type checkers ───────────────────────────
#
# Plain dicts at runtime (decision C5): these describe them and cost nothing.
# node-v1.schema.json is the source of truth; tests/test_contract.py pins that
# every schema property has a key here and every key here is in the schema.


class Loc(TypedDict, total=False):
    kind: str                # "bytes" (default) or "path"
    layer: int
    off: int
    len: int
    syntax: str              # path locators: json-pointer | xml-steps
    path: Any


class Field(TypedDict, total=False):
    name: str
    key: str
    at: Loc
    type: str
    type_source: str         # declared | enc | inferred | none
    xform: str
    value: Any
    display: str
    note: str
    unit: str
    enum: str
    flags: List[str]
    ptr: Loc
    derived_from: List[str]
    remote: bool


class Node(TypedDict, total=False):
    id: str
    name: str
    kind: str
    extent: Loc
    payload: Loc
    geometry: str
    summary: str
    fields: List[Field]
    children: List["Node"]
    caps: dict
    rows: dict
    origin: str


class Layer(TypedDict, total=False):
    id: int
    name: str
    kind: str
    length: int
    parent: int
    from_node: str
    sources: List[dict]
    mapping: str
    decoder: dict
    length_known: bool
    truncated_at: int
    crypto: str
    verdict: dict


class Finding(TypedDict, total=False):
    kind: str
    code: str
    severity: str
    message: str
    node: str
    at: Loc
    cap: dict


class Document(TypedDict, total=False):
    contract: int
    producer: dict
    format: dict
    file: dict
    layers: List[Layer]
    nodes: List[Node]
    findings: List[Finding]
    limits: dict
    typing: dict

# ── limits ─────────────────────────────────────────────────────────────

# The limits every walk runs under until the Limits object (architecture-2.0.md
# section 3) replaces the per-walker constants. Recorded on each Document so two
# Documents can be compared knowing they were made the same way.
DEFAULT_LIMITS = {
    "read_bytes": 64 << 20,
    "chunk_payload": 64 << 10,
    "inflate_bytes": 64 << 20,
    "work_steps": 4_000_000,
    "list_rows": None,
    "frame_rows": 100_000,
    "depth": 32,
    "decode": False,
}

# ── types ──────────────────────────────────────────────────────────────

_STRUCT_TYPES = {
    "B": "u8", "b": "i8",
    "H": "u16", "h": "i16",
    "I": "u32", "i": "i32", "L": "u32", "l": "i32",
    "Q": "u64", "q": "i64",
    "f": "f32", "d": "f64",
}
_NAMED_TYPES = {"float80": "f80be", "u24be": "u24be", "synchsafe": "synchsafe"}

# Fixed-width integer types: name -> (width, signed, byteorder).
_INT_TYPES = {}
for _w, _bits in ((1, 8), (2, 16), (3, 24), (4, 32), (8, 64)):
    for _signed in (False, True):
        _p = ("i" if _signed else "u") + str(_bits)
        if _w == 1:
            _INT_TYPES[_p] = (1, _signed, "big")
        else:
            _INT_TYPES[_p + "le"] = (_w, _signed, "little")
            _INT_TYPES[_p + "be"] = (_w, _signed, "big")


def type_of_enc(enc):
    """(type, byte shift, byte length or None, xform or None) for a legacy
    `enc`, or None when it has no v1 spelling. Shift and length move the
    field's locator onto its container for the bit-field encodings."""
    if not isinstance(enc, str) or not enc:
        return None
    if enc in _NAMED_TYPES:
        return _NAMED_TYPES[enc], 0, None, None
    bf = fieldcodec.parse_bitfield(enc)
    if bf:
        delta, clen, bitpos, width, bias = bf
        return (f"bits:{clen}:{bitpos}:{width}", delta, clen,
                f"add({-bias})" if bias else None)
    for parse in (fieldcodec.parse_bitsmap, fieldcodec.parse_bitsdyn):
        bm = parse(enc)
        if bm:
            delta, clen, bitpos, width, _map = bm
            return f"bits:{clen}:{bitpos}:{width}", delta, clen, None
    order, code = ("<", enc) if enc[0] not in "<>!=@" else (enc[0], enc[1:])
    if len(code) != 1 or code not in _STRUCT_TYPES:
        return None
    base = _STRUCT_TYPES[code]
    if base in ("u8", "i8"):
        return base, 0, None, None
    return base + ("be" if order in ">!" else "le"), 0, None, None


def read_type(typ, b):
    """The value `typ` stores in bytes `b`, or raise ValueError. Only the
    fixed-width types are readable; the rest are not claims about bytes."""
    if typ in _INT_TYPES:
        w, signed, order = _INT_TYPES[typ]
        if len(b) != w:
            raise ValueError(f"{typ} needs {w} bytes, got {len(b)}")
        return int.from_bytes(b, order, signed=signed)
    if typ in ("f32le", "f32be", "f64le", "f64be"):
        fmt = ("<" if typ.endswith("le") else ">") + ("f" if typ[1:3] == "32" else "d")
        return struct.unpack(fmt, b)[0]
    if typ == "f80be":
        return fieldcodec.decode_value("float80", b)
    if typ == "synchsafe":
        return fieldcodec.decode_value("synchsafe", b)
    if typ.startswith("bits:"):
        clen, bitpos, width = (int(x) for x in typ.split(":")[1:])
        if len(b) != clen:
            raise ValueError(f"{typ} needs {clen} bytes, got {len(b)}")
        return fieldcodec.bitfield_extract(b, bitpos, width, 0)
    if typ == "fourcc":
        if len(b) != 4:
            raise ValueError("fourcc needs 4 bytes")
        return b.decode("latin-1")
    if typ == "ascii":
        return b.decode("latin-1").rstrip("\x00 ")
    raise ValueError(f"{typ} is not readable")


_XFORM = re.compile(r"(add|mul|fixed|mask|rel)\(([^)]*)\)|(neg|ascii-dec)")


def apply_xform(xform, stored, at=None, node_off=None):
    """The value a transform chain makes of a stored value (spec 6.2)."""
    v = stored
    for step in (xform or "").split(";"):
        if not step:
            continue
        m = _XFORM.fullmatch(step)
        if not m:
            raise ValueError(f"unknown transform {step!r}")
        op, arg, bare = m.group(1), m.group(2), m.group(3)
        if bare == "neg":
            v = -v
        elif bare == "ascii-dec":
            v = int("".join(ch for ch in str(v) if ch.isdigit()) or "0")
        elif op == "add":
            v = v + int(arg)
        elif op == "mul":
            v = v * int(arg)
        elif op == "mask":
            v = v & int(arg, 16)
        elif op == "fixed":
            _m, n = (int(x) for x in arg.split("."))
            v = v / float(1 << n)
        elif op == "rel":
            v = v + (at if arg == "self" else node_off)
    return v


# ── ids ────────────────────────────────────────────────────────────────

_SLUG_BAD = re.compile(r"[^A-Za-z0-9_.\-\[\]]")


def slug(name):
    """A node name as one path step: letters, digits, `_ . - [ ]` kept, the rest
    `_`. `fmt ` becomes `fmt_`, which keeps RIFF's padded ids distinct."""
    s = _SLUG_BAD.sub("_", str(name))
    return s or "_"


def _dedupe(names):
    """Repeated names get `~2`, `~3`, ... in order (spec 5.1)."""
    seen, out = {}, []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n}~{seen[n]}")
    return out


# ── findings ───────────────────────────────────────────────────────────

_SEVERITY = {"defect": "warn", "coverage": "info", "environment": "notice",
             "info": "info", "error": "alert"}


def _finding(text, node=None):
    kind = kind_of(text)
    msg = str(text)
    code = "legacy"
    if msg.startswith("walker error ("):
        kind, code = "error", "walker.error"
    elif msg.startswith("geometry error ("):
        kind, code = "error", "geometry.error"
    elif msg.startswith("generic structural triage:"):
        kind, code = "info", "triage.generic"
    f = {"kind": kind, "code": code, "severity": _SEVERITY[kind], "message": msg}
    if node is not None:
        f["node"] = node
    return f


# ── the normaliser ─────────────────────────────────────────────────────

def _loc(off, n, layer=0):
    return {"layer": layer, "off": off, "len": n}


def _positioned(c):
    return (isinstance(c.get("offset"), int) and isinstance(c.get("extent_len"), int)
            and isinstance(c.get("payload_base"), int)
            and isinstance(c.get("payload_len"), int)
            and c.get("geometry") != "unpositioned")


def _infer(value, b, prefer_be):
    """The one storage type consistent with an int value and its bytes, given
    the format's native order, or None. A byte string that reads the same both
    ways is only inferred when the order is known (spec 11)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    w = len(b)
    if w == 1:
        return "u8" if b[0] == value else ("i8" if b[0] - 256 == value else None)
    if w not in (2, 3, 4, 8):
        return None
    hits = []
    for signed in (False, True):
        for order in ("little", "big"):
            if int.from_bytes(b, order, signed=signed) == value:
                hits.append(("i" if signed else "u") + str(w * 8)
                            + ("le" if order == "little" else "be"))
    if not hits:
        return None
    unsigned = [h for h in hits if h[0] == "u"]
    hits = unsigned or hits
    orders = {h[-2:] for h in hits}
    if len(orders) == 1:
        return hits[0]
    want = "be" if prefer_be else "le"
    return next((h for h in hits if h.endswith(want)), None)


def _field(fl, node_pos, data, prefer_be, key):
    """One legacy field -> one v1 field."""
    value = fl.get("value")
    out = {"name": str(fl.get("name")), "key": key, "note": str(fl.get("note") or ""),
           "display": "" if value is None else str(value)}
    off, n = fl.get("off"), fl.get("len") or 0
    at = None
    if off is not None and node_pos is not None:
        at = node_pos + off
        if at < 0 or (data is not None and at + n > len(data)):
            at = None                     # a field past the layer is not placed
    if fl.get("remote"):
        out["remote"] = True
    if "xref" in fl and isinstance(fl["xref"], int):
        out["ptr"] = _loc(fl["xref"], 0)

    enc = fl.get("enc")
    mapped = type_of_enc(enc) if enc else None
    if at is not None and mapped:
        typ, shift, clen, xform = mapped
        at, n = at + shift, (clen if clen is not None else n)
        out["at"] = _loc(at, n)
        out["type"], out["type_source"] = typ, "enc"
        if xform:
            out["xform"] = xform
        if enc.startswith(("bitsmap:", "bitsdyn:")):
            # the value is the raw bits; the legacy display is the label
            try:
                out["value"] = read_type(typ, bytes(data[at:at + n]))
            except (ValueError, TypeError, struct.error):
                out["value"] = fl.get("raw", value)
            out["enum"] = out["display"]
        else:
            out["value"] = fl.get("raw", value)
        return out

    if at is None:
        out["type"], out["type_source"] = ("derived" if off is None else "display",
                                           "none")
        out["value"] = fl.get("raw", value)
        return out

    out["at"] = _loc(at, n)
    b = bytes(data[at:at + n]) if data is not None else b""
    typ = _infer(value, b, prefer_be) if len(b) == n else None
    if typ is None and isinstance(value, str) and n and len(b) == n:
        text = b.decode("latin-1")
        if n == 4 and text == value and value.isprintable():
            typ = "fourcc"
        elif value and text.rstrip("\x00 ") == value and value.isprintable():
            typ = "ascii"
    if typ is not None:
        out["type"], out["type_source"] = typ, "inferred"
        out["value"] = value
    else:
        out["type"], out["type_source"] = "display", "none"
        out["value"] = fl.get("raw", value)
    return out


def _rows(rows, size):
    """Legacy per-element rows -> {total, kept, cap, items} (spec 10). A row's
    position becomes a byte locator under `at`: MP3 writes `offset` as a hex
    string, the rest as ints."""
    from acidcat.core.walk.base import _FRAME_LISTING_CAP
    items = []
    for r in rows:
        r = dict(r)
        off = r.pop("offset", None)
        n = r.pop("size", None)
        if isinstance(off, str):
            try:
                off = int(off, 0)
            except ValueError:
                off = None
        if isinstance(off, int) and 0 <= off <= size:
            n = n if isinstance(n, int) and n >= 0 and off + n <= size else 0
            r["at"] = _loc(off, n)
        items.append(r)
    return {"total": len(items), "kept": len(items), "cap": _FRAME_LISTING_CAP,
            "items": items}


def _encloses(a, b):
    ao, al = a["extent"]["off"], a["extent"]["len"]
    bo, bl = b["extent"]["off"], b["extent"]["len"]
    return ao <= bo and bo + bl <= ao + al


def _gaps(lo, hi, spans):
    """[(off, len)] of bytes in [lo, hi) no span covers."""
    out, pos = [], lo
    for o, n in sorted(spans):
        if o > pos:
            out.append((pos, o - pos))
        pos = max(pos, o + n)
    if pos < hi:
        out.append((pos, hi - pos))
    return [(o, n) for o, n in out if n > 0]


def _unwalked(off, n):
    return {"name": "unwalked", "kind": "unwalked", "origin": "normaliser",
            "extent": _loc(off, n), "payload": _loc(off, n),
            "geometry": "declared",
            "summary": f"{n:,} bytes no walker node describes",
            "fields": [], "children": [], "caps": {}}


def document(fmt_id, label, chunks, warns, data, *, forced=False,
             producer_version=None, caps_fn=None, prefer_be=False):
    """The v1 Document for one walk.

    `data` is the file's bytes (or a read-only view of them): the normaliser
    reads fields' bytes to infer and to decode typed values, never to parse.
    `caps_fn(fmt_id, label, chunks)` returns {chunk index: caps}; see
    core/infra/capabilities.py."""
    size = len(data) if data is not None else 0
    try:
        caps_map = caps_fn(fmt_id, label, chunks or []) if caps_fn else {}
    except Exception:                     # a cap heuristic never breaks a walk
        caps_map = {}
    findings = []
    chunk_texts = set()
    nodes = []
    for idx, c in enumerate(chunks or []):
        name = str(c.get("id", "?"))
        node = {"name": name.strip() or name, "kind": "chunk",
                "geometry": c.get("geometry") or "unpositioned",
                "summary": str(c.get("summary") or ""),
                "fields": [], "children": [], "caps": {}, "_idx": idx,
                "_slug": slug(name), "_chunk": c}
        pos = None
        node["_warnings"] = list(c.get("warnings") or [])
        if not _positioned(c):
            node["geometry"] = "unpositioned"
        elif node["geometry"] == "invalid" or c["offset"] < 0 or c["extent_len"] < 0:
            # geometry.py marked the claimed range as not fitting; a locator
            # must be inside its layer, so the claim becomes a finding instead
            node["geometry"] = "invalid"
            node["_warnings"].append(
                "the chunk claims 0x%X+%d (payload 0x%X+%d), which does not fit "
                "in the file" % (c["offset"], c["extent_len"], c["payload_base"],
                                 c["payload_len"]))
            node["_invalid"] = True
        else:
            node["extent"] = _loc(c["offset"], c["extent_len"])
            node["payload"] = _loc(c["payload_base"], c["payload_len"])
            pos = c["payload_base"]
        keys = _dedupe([str(f.get("name")) for f in c.get("fields") or []])
        node["fields"] = [_field(f, pos, data, prefer_be, k)
                          for f, k in zip(c.get("fields") or [], keys)]
        if c.get("rows"):
            node["rows"] = _rows(c["rows"], size)
        chunk_texts.update(str(w) for w in node["_warnings"])
        nodes.append(node)

    # the tree: smallest enclosing node is the parent; equal extents nest in
    # emission order; unpositioned nodes are roots after the positioned ones
    placed = sorted((n for n in nodes if "extent" in n),
                    key=lambda n: (n["extent"]["off"], -n["extent"]["len"], n["_idx"]))
    roots, stack = [], []
    for n in placed:
        while stack and not _encloses(stack[-1], n):
            stack.pop()
        (stack[-1]["children"] if stack else roots).append(n)
        stack.append(n)
    roots += [n for n in nodes if "extent" not in n]

    # gaps: inside each parent's payload, and across the file at the top
    def fill(parent_nodes, lo, hi):
        spans = [(c["extent"]["off"], c["extent"]["len"])
                 for c in parent_nodes if "extent" in c]
        gaps = [_unwalked(o, n) for o, n in _gaps(lo, hi, spans)]
        return gaps

    def walk(n):
        for c in n["children"]:
            walk(c)
        if n["children"]:
            p = n["payload"]
            gaps = fill(n["children"], p["off"], p["off"] + p["len"])
            if gaps:
                n["children"] = sorted(n["children"] + gaps,
                                       key=lambda x: x["extent"]["off"])
            if n["kind"] == "chunk":
                n["kind"] = "container"
    for r in roots:
        if "extent" in r:
            walk(r)
    top_gaps = fill(roots, 0, size)
    if top_gaps:
        positioned = sorted([r for r in roots if "extent" in r] + top_gaps,
                            key=lambda x: x["extent"]["off"])
        roots = positioned + [r for r in roots if "extent" not in r]

    # ids, findings, caps, and the private keys removed
    counts = {"fields": 0, "positioned": 0, "typed_declared": 0, "typed_enc": 0,
              "typed_inferred": 0, "caps_declared": 0, "caps_inferred": 0,
              "nodes": 0, "nodes_unwalked": 0, "findings_legacy": 0}

    by_idx = {}

    def finish(siblings, prefix):
        steps = _dedupe([n.get("_slug") or slug(n["name"]) for n in siblings])
        for n, step in zip(siblings, steps):
            nid = f"{prefix}/{step}" if prefix else step
            n["id"] = nid
            counts["nodes"] += 1
            if n["kind"] == "unwalked":
                counts["nodes_unwalked"] += 1
            invalid = n.pop("_invalid", False)
            ws = n.pop("_warnings", [])
            for i, w in enumerate(ws):
                f = _finding(w, nid)
                if invalid and i == len(ws) - 1:
                    f.update(kind="defect", code="geometry.invalid", severity="warn")
                findings.append(f)
            n.pop("_chunk", None)
            if "_idx" in n:
                by_idx[n["_idx"]] = n
                n["caps"] = {k: dict(v) for k, v in caps_map.get(n["_idx"], {}).items()}
            for cap in n["caps"].values():
                counts["caps_" + ("declared" if cap.get("source") == "declared"
                                  else "inferred")] += 1
            for f in n["fields"]:
                counts["fields"] += 1
                if "at" in f:
                    counts["positioned"] += 1
                src = f["type_source"]
                if src in ("declared", "enc", "inferred"):
                    counts["typed_" + src] += 1
            for k in ("_idx", "_slug"):
                n.pop(k, None)
            finish(n["children"], nid)
    finish(roots, "")

    # audio caps name their source fields by (chunk index, field name)
    for n in by_idx.values():
        a = n["caps"].get("audio")
        if not a:
            continue
        refs = []
        for idx, fname in a.get("fields", []):
            src = by_idx.get(idx)
            f = next((f for f in src["fields"] if f["name"] == fname), None) if src else None
            if f is not None:
                refs.append(f"{src['id']}#{f['key']}")
        a["fields"] = refs

    for w in warns or []:
        # walkers copy chunk notes up to file level; report each once
        if str(w) in chunk_texts:
            continue
        findings.append(_finding(w))
    counts["findings_legacy"] = sum(1 for f in findings if f["code"] == "legacy")

    fmt = {"id": fmt_id, "label": label}
    if forced:
        fmt["forced"] = True
    return {
        "contract": CONTRACT,
        "producer": {"name": "acidcat", "version": producer_version or _version()},
        "format": fmt,
        "file": {"size": size},
        "layers": [{"id": 0, "name": "file", "kind": "file", "length": size}],
        "nodes": roots,
        "findings": findings,
        "limits": dict(DEFAULT_LIMITS, hit=[]),
        "typing": counts,
    }


def _version():
    from acidcat import __version__
    return __version__


# ── walking a file into a Document ─────────────────────────────────────

def walk(path, deep=False, fmt_override=None):
    """Walk a file, or a Source, and return its v1 Document."""
    from acidcat.core.infra import capabilities, sniff as sniffmod
    from acidcat.core.infra.source import Source, as_source
    from acidcat.core.walk import walk_file
    owned = not isinstance(path, Source)
    src = as_source(path)
    try:
        fmt_id = fmt_override or sniffmod.sniff(src)
        label, chunks, warns = walk_file(src, deep=deep, fmt_override=fmt_override)
        return document(fmt_id, label, chunks, warns, src.buffer(),
                        forced=bool(fmt_override),
                        caps_fn=capabilities.caps,
                        prefer_be=capabilities.prefers_be(fmt_id, label))
    finally:
        if owned:
            src.close()


# ── lookups ────────────────────────────────────────────────────────────

def iter_nodes(doc):
    """Every node, depth first, in document order."""
    stack = list(reversed(doc["nodes"]))
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n["children"]))


def node(doc, node_id):
    for n in iter_nodes(doc):
        if n["id"] == node_id:
            return n
    return None
