"""The Document: read-only views over the contract v1 dict.

    doc = acidcat.open("space gun 1.ym")
    doc.format.id                            # "ym"
    f = doc.field("1:lh5/header#frames")     # an ADDR (node-v1 section 13)
    f.value, f.display, f.type, f.at         # 4, "4", "u32be", Loc(1, 12, 4)
    doc.layer_bytes(1)[:4]                   # b"YM5!"
    [x for x in doc.findings if x.kind == "defect"]
    doc.to_json()                            # the v1 dict

The dict is the source of truth (decision C5): every view reads it and none
copies it, so `to_json()` is exactly what the views show. `Document`, `Node`,
`Field`, `Layer`, `Finding` and `Loc` have no setters; editing goes through
`Document.edit` (a later milestone), not through a view.

Forensic findings join the Document here: `open()` runs the forensic scan
after the walk and adds its findings (not the walker's notes it echoes, which
are already there), each with a point locator at its offset and the node that
holds it.
"""

import copy
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

from acidcat.core.infra import addr as addrmod
from acidcat.core.infra import contract, layers as layersmod
from acidcat.core.infra.findings import REGISTRY
from acidcat.core.infra.limits import Limits
from acidcat.core.infra.source import Source

AddrError = addrmod.AddrError


@dataclass(frozen=True)
class Loc:
    """A locator. A byte locator has `off` and `len` in `layer`; a path
    locator (text layers) has `syntax` and `path`, and `off`/`len` only when
    the walker knew the element's span."""
    layer: int
    off: Optional[int]
    len: Optional[int]
    syntax: Optional[str] = None
    path: object = None

    @property
    def end(self):
        return None if self.off is None else self.off + self.len

    @classmethod
    def of(cls, d):
        if d is None:
            return None
        path = d.get("path")
        return cls(d.get("layer", 0), d.get("off"), d.get("len"), d.get("syntax"),
                   tuple(path) if isinstance(path, list) else path)

    def __repr__(self):
        if self.syntax is not None:
            return "Loc(%d, %s %r)" % (self.layer, self.syntax, self.path)
        return "Loc(%d, %d, %d)" % (self.layer, self.off, self.len)


class _View:
    """A view over one dict of the Document. `raw` is that dict itself:
    read it, do not change it."""
    __slots__ = ("raw", "_doc")

    def __init__(self, raw, doc):
        self.raw = raw
        self._doc = doc

    def __setattr__(self, name, value):
        if name in _View.__slots__ and not hasattr(self, name):
            object.__setattr__(self, name, value)
        else:
            raise AttributeError("%s is read-only; edit through Document.edit"
                                 % type(self).__name__)

    def __eq__(self, other):
        return type(other) is type(self) and other.raw is self.raw

    def __hash__(self):
        return id(self.raw)


class Field(_View):
    """One field of a node (node-v1 section 6)."""
    __slots__ = ("node",)

    def __init__(self, raw, node):
        super().__init__(raw, node._doc)
        object.__setattr__(self, "node", node)

    name = property(lambda s: s.raw["name"])
    key = property(lambda s: s.raw["key"])
    value = property(lambda s: s.raw.get("value"))
    display = property(lambda s: s.raw.get("display"))
    type = property(lambda s: s.raw.get("type"))
    type_source = property(lambda s: s.raw.get("type_source"))
    xform = property(lambda s: s.raw.get("xform"))
    note = property(lambda s: s.raw.get("note"))
    at = property(lambda s: Loc.of(s.raw.get("at")))

    @property
    def addr(self):
        """The ADDR that names this field; it resolves back to it."""
        return addrmod.addr_of(self.node.raw, self.raw)

    def __repr__(self):
        return "Field(%s = %r)" % (self.addr, self.value)


class Node(_View):
    """One node (node-v1 section 5)."""
    __slots__ = ("parent",)

    def __init__(self, raw, doc, parent=None):
        super().__init__(raw, doc)
        object.__setattr__(self, "parent", parent)

    id = property(lambda s: s.raw["id"])
    name = property(lambda s: s.raw["name"])
    kind = property(lambda s: s.raw["kind"])
    geometry = property(lambda s: s.raw.get("geometry"))
    summary = property(lambda s: s.raw.get("summary", ""))
    caps = property(lambda s: s.raw.get("caps") or {})
    rows = property(lambda s: s.raw.get("rows"))
    extent = property(lambda s: Loc.of(s.raw.get("extent")))
    payload = property(lambda s: Loc.of(s.raw.get("payload")))

    @property
    def fields(self):
        return [Field(f, self) for f in self.raw.get("fields") or []]

    @property
    def children(self):
        return [Node(c, self._doc, self) for c in self.raw.get("children") or []]

    def field(self, key):
        """This node's field `key` (a key, not an ADDR)."""
        for f in self.raw.get("fields") or []:
            if f.get("key") == key:
                return Field(f, self)
        raise AddrError("node %r has no field %r" % (self.id, key))

    @property
    def addr(self):
        return addrmod.addr_of(self.raw)

    def __repr__(self):
        return "Node(%s, %s)" % (self.addr, self.kind)


class Layer(_View):
    """One layer (node-v1 section 3): layer 0 is the file; the others are
    derived from a node's bytes by a named decoder."""
    __slots__ = ()
    id = property(lambda s: s.raw["id"])
    name = property(lambda s: s.raw.get("name"))
    kind = property(lambda s: s.raw["kind"])
    length = property(lambda s: s.raw.get("length"))
    parent = property(lambda s: s.raw.get("parent"))
    from_node = property(lambda s: s.raw.get("from_node"))
    decoder = property(lambda s: s.raw.get("decoder"))
    mapping = property(lambda s: s.raw.get("mapping"))
    verdict = property(lambda s: s.raw.get("verdict"))

    def __repr__(self):
        return "Layer(%d, %s, %s bytes)" % (self.id, self.name, self.length)


class Finding(_View):
    """One finding (node-v1 section 9): a walker note or a forensic result."""
    __slots__ = ()
    kind = property(lambda s: s.raw["kind"])
    code = property(lambda s: s.raw["code"])
    severity = property(lambda s: s.raw["severity"])
    message = property(lambda s: s.raw["message"])
    node = property(lambda s: s.raw.get("node"))
    at = property(lambda s: Loc.of(s.raw.get("at")))
    cap = property(lambda s: s.raw.get("cap"))

    def __repr__(self):
        return "Finding(%s %s: %s)" % (self.kind, self.code, self.message)


class _Format:
    __slots__ = ("id", "label")

    def __init__(self, d):
        self.id = d.get("id")
        self.label = d.get("label")

    def __repr__(self):
        return "Format(%r, %r)" % (self.id, self.label)


class Document:
    """A walked file: its layers, its node tree, its findings, and the limits
    it was walked under, over the v1 dict (`to_json()`)."""

    def __init__(self, raw, data=None, path=None, source=None):
        self._raw = raw
        # how to get layer 0's bytes back: a path, the bytes, or the caller's
        # Source (theirs to close)
        self._path, self._data, self._source = path, data, source
        self._layer_cache = {}

    # the header
    contract = property(lambda s: s._raw["contract"])
    producer = property(lambda s: dict(s._raw["producer"]))
    format = property(lambda s: _Format(s._raw["format"]))
    size = property(lambda s: s._raw["file"]["size"])
    limits = property(lambda s: dict(s._raw["limits"]))
    typing = property(lambda s: dict(s._raw["typing"]))

    @property
    def layers(self):
        return [Layer(l, self) for l in self._raw["layers"]]

    @property
    def nodes(self):
        """The top-level nodes; `walk()` gives every node."""
        return [Node(n, self) for n in self._raw["nodes"]]

    @property
    def findings(self):
        return [Finding(f, self) for f in self._raw["findings"]]

    def walk(self):
        """Every node, depth first, in document order."""
        def rec(raws, parent):
            for r in raws:
                n = Node(r, self, parent)
                yield n
                yield from rec(r.get("children") or [], n)
        yield from rec(self._raw["nodes"], None)

    # ADDR lookups
    def _node_view(self, raw):
        for n in self.walk():
            if n.raw is raw:
                return n
        raise AddrError("node %r is not in this document" % raw.get("id"))

    def node(self, addr):
        """The node an ADDR names (a field address names its node)."""
        t = addrmod.resolve(self._raw, addr)
        if t.node is None:
            raise AddrError("%r names bytes, not a node" % addr)
        return self._node_view(t.node)

    def field(self, addr):
        """The field an ADDR names (`node#key`)."""
        t = addrmod.resolve(self._raw, addr)
        if t.field is None:
            raise AddrError("%r names a node, not a field; add #KEY" % addr)
        node = self._node_view(t.node)
        return next(f for f in node.fields if f.raw is t.field)

    def find(self, pattern):
        """Every node a NODE term or glob names (`RIFF/*`, `**/data`)."""
        want = {id(n) for n in addrmod.find_nodes(self._raw, pattern)}
        return [n for n in self.walk() if id(n.raw) in want]

    def resolve(self, addr):
        """The bytes an ADDR names, as a Loc. A node means its payload, a
        field its `at`; an unpositioned field has no bytes and is an error."""
        t = addrmod.resolve(self._raw, addr)
        if t.off is None:
            raise AddrError("%r has no byte range (it is unpositioned)" % addr)
        return Loc(t.layer, t.off, t.len)

    # bytes
    def _file_bytes(self):
        if self._data is not None:
            return self._data
        if self._source is not None:
            return self._source.buffer()
        with open(self._path, "rb") as fh:
            return fh.read()

    def layer_bytes(self, layer):
        """The bytes of a layer: the file for 0, a derived layer decoded on
        demand (under the walk's inflate cap) and kept."""
        if layer == 0:
            return bytes(self._file_bytes())
        if layer not in self._layer_cache:
            self._layer_cache[layer] = layersmod.layer_bytes(
                self._raw, layer, self._file_bytes())
        return self._layer_cache[layer]

    def read(self, where):
        """The bytes at a Loc, or at the place an ADDR names."""
        loc = where if isinstance(where, Loc) else self.resolve(where)
        return self.layer_bytes(loc.layer)[loc.off:loc.off + loc.len]

    def to_json(self):
        """The contract v1 dict (a copy)."""
        return copy.deepcopy(self._raw)

    def __repr__(self):
        return "Document(%s, %d bytes, %d findings)" % (
            self.format.id, self.size, len(self._raw["findings"]))


# ── forensic findings ──────────────────────────────────────────────────

# the rules under which the scan echoes the walker's own notes, which the
# Document already carries
_ECHOED = {"structure", "coverage", "environment", "info"}


def _deepest_at(doc, off):
    best = None
    for n in contract.iter_nodes(doc):
        e = n.get("extent")
        if e and e["layer"] == 0 and e["off"] <= off < e["off"] + e["len"]:
            if best is None or e["len"] <= best["extent"]["len"]:
                best = n
    return best


def forensic_findings(doc, scan):
    """v1 findings for the forensic scan's results (`scan` is its list),
    leaving out the walker notes it repeats."""
    out = []
    size = doc["file"]["size"]
    for r in scan:
        if r.get("rule") in _ECHOED:
            continue
        code = r.get("code") or "anomaly.%s" % r.get("rule")
        if code not in REGISTRY:
            continue
        f = {"kind": REGISTRY[code][0], "code": code,
             "severity": r.get("severity") or REGISTRY[code][1],
             "message": str(r.get("message", ""))}
        off = r.get("offset")
        if r.get("rule") != "check_failed" and isinstance(off, int) and 0 <= off <= size:
            f["at"] = {"layer": 0, "off": off, "len": 0}
            node = _deepest_at(doc, off)
            if node is not None:
                f["node"] = node["id"]
        out.append(f)
    return out


def _walk(target, limits, fmt, forensics, path):
    """The v1 dict for `target`, with the forensic findings added when asked;
    the scan reuses the walk rather than walking again."""
    from acidcat.core.forensics import anomalies
    seen = {}
    raw = contract.walk(target, fmt_override=fmt, limits=limits,
                        on_walk=lambda l, c, w: seen.update(l=l, c=c, w=w))
    if forensics:
        scan = anomalies.scan(path, raw["format"]["label"],
                              seen.get("c", []), seen.get("w", []))
        raw["findings"].extend(forensic_findings(raw, scan))
    return raw


# ── open ───────────────────────────────────────────────────────────────

def open_document(target, limits=None, fmt=None, forensics=True):
    """Walk `target` (a path, bytes, or a Source) into a Document.

    `limits` is the Limits to walk under (the defaults otherwise); `fmt`
    forces a walker by its id. With `forensics` the forensic scan runs too
    and its findings join the Document's; bytes with no path are scanned from
    a temporary file, which is removed afterwards."""
    limits = limits or Limits()
    if isinstance(target, (bytes, bytearray, memoryview)):
        data = bytes(target)
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix="acidcat_open_")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            return _open_path(tmp, limits, fmt, forensics, data=data)
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    if isinstance(target, Source):
        path = target.path
        if path is None:
            return open_document(target.buffer(), limits, fmt, forensics)
        return Document(_walk(target, limits, fmt, forensics, path), source=target)
    return _open_path(os.fspath(target), limits, fmt, forensics)


def _open_path(path, limits, fmt, forensics, data=None):
    raw = _walk(path, limits, fmt, forensics, path)
    return Document(raw, data=data, path=None if data is not None else path)
