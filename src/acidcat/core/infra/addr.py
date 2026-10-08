"""ADDR: one text syntax that names a place in a Document (node-v1 section 13).

    ADDR   := [LAYER ':'] TARGET [RANGE]
    LAYER  := integer                         0 is the file
    TARGET := NODE ['#' KEY]                  RIFF/fmt_   RIFF/fmt_#sample_rate
            | '@' OFFSET                      @0x5d1000   @1234
    NODE   := id | glob | name                RIFF/LIST[1]  RIFF/*   **/data   fmt
    RANGE  := '+' LEN                         from TARGET's start
            | '..' END                        to an absolute end
            | '[' OFF ':' LEN ']'             relative to TARGET's payload

A node address means its payload; a field address means the field's `at`.
`@OFFSET` without a layer is in layer 0. A node or field needs no layer: its
id says where it is, so `lh5/header#frames` and `1:lh5/header#frames` name the
same field, and a layer that disagrees with the node's is an error rather than
a silent correction.

This module works on the v1 dict alone. `acidcat.core.document` wraps what it
returns in the read-only views.
"""

import re
from collections import namedtuple

from acidcat.core.infra.contract import iter_nodes


class AddrError(ValueError):
    """An address that does not parse, or names nothing, or names more than
    one place where one is needed. `candidates` lists the full ids a bare name
    or a glob matched, so the message can say what to type instead."""

    def __init__(self, message, candidates=()):
        super().__init__(message)
        self.candidates = list(candidates)


# what an address resolves to: the node (or None for `@OFFSET`), the field (or
# None), and the byte range (layer, off, len), or None for an unpositioned field
Target = namedtuple("Target", "node field layer off len")

_NUM = r"(?:0[xX][0-9a-fA-F]+|\d+)"
_RANGES = (
    ("plus", re.compile(r"^(?P<t>.+?)\+(?P<a>%s)$" % _NUM)),
    ("to", re.compile(r"^(?P<t>.+?)\.\.(?P<a>%s)$" % _NUM)),
    ("sub", re.compile(r"^(?P<t>.+?)\[(?P<a>%s):(?P<b>%s)\]$" % (_NUM, _NUM))),
)
_LAYER = re.compile(r"^(?P<l>\d+):(?P<rest>.+)$")


def number(text):
    """An ADDR number: `0x` hex or decimal, never signed. int() takes a
    leading '-', and a negative offset slices from the end of the file."""
    t = text.strip()
    if t[:1] in "+-":
        raise AddrError("%r is not an offset (offsets are unsigned)" % text)
    try:
        return int(t, 16) if t[:2].lower() == "0x" else int(t, 10)
    except ValueError:
        raise AddrError("%r is not a number (0x hex or decimal)" % text) from None


def is_glob(text):
    return any(c in text for c in "*?[")


def glob_regex(pattern):
    """A glob over node ids: `*` and `?` stay inside one path step, `**`
    crosses steps (and `**/` also matches no step at all)."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append(r"(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(r".*")
            i += 2
        elif c == "*":
            out.append(r"[^/]*")
            i += 1
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _all_nodes(doc):
    return list(iter_nodes(doc))


def find_nodes(doc, pattern):
    """The nodes a NODE term names, in document order: the node with that id,
    else every node whose id matches the glob, else every node with that
    name, else every node whose id ends in that step (`fmt_` finds
    `RIFF/fmt_`, as a name would, spelled as the id spells it)."""
    nodes = _all_nodes(doc)
    exact = [n for n in nodes if n["id"] == pattern]
    if exact:
        return exact
    if is_glob(pattern):
        rx = glob_regex(pattern)
        hits = [n for n in nodes if rx.match(n["id"])]
        if hits:
            return hits
        # `[` is a glob character and an index (`LIST[1]`): a pattern that
        # globs to nothing is tried as the literal step it may be
    named = [n for n in nodes if n.get("name") == pattern]
    if named or "/" in pattern:
        return named
    return [n for n in nodes if n["id"].rsplit("/", 1)[-1] == pattern]


def _one_node(doc, term):
    hits = find_nodes(doc, term)
    if not hits:
        raise AddrError("no node %r" % term)
    if len(hits) > 1:
        ids = [n["id"] for n in hits]
        shown = ", ".join(ids[:12]) + (", ..." if len(ids) > 12 else "")
        what = "matches" if is_glob(term) else "names"
        raise AddrError("%r %s %d nodes; one is needed: %s"
                        % (term, what, len(ids), shown), ids)
    return hits[0]


def _field_of(node, key):
    for f in node.get("fields") or []:
        if f.get("key") == key:
            return f
    keys = [f.get("key") for f in node.get("fields") or []]
    raise AddrError("node %r has no field %r (it has: %s)"
                    % (node["id"], key, ", ".join(keys) or "no fields"), keys)


def _layer_length(doc, layer):
    for lay in doc.get("layers") or []:
        if lay["id"] == layer:
            return lay.get("length")
    raise AddrError("the document has no layer %d" % layer)


def _split_range(text):
    """(target text, range kind or None, range numbers)."""
    for kind, rx in _RANGES:
        m = rx.match(text)
        if m:
            nums = [number(m.group("a"))]
            if kind == "sub":
                nums.append(number(m.group("b")))
            return m.group("t"), kind, nums
    return text, None, []


def _target(doc, text, layer):
    """Resolve a TARGET (no range) to a Target."""
    if text.startswith("@"):
        lay = 0 if layer is None else layer
        _layer_length(doc, lay)
        return Target(None, None, lay, number(text[1:]), 0)
    term, _, key = text.partition("#")
    node = _one_node(doc, term)
    if key:
        field = _field_of(node, key)
        at = field.get("at")
        if at is None:
            loc = (None, None, None)            # unpositioned
        elif "off" not in at:
            loc = (at.get("layer"), None, None)  # a path locator with no span
        else:
            loc = (at["layer"], at["off"], at["len"])
        where = loc[0]
    else:
        field = None
        p = node.get("payload") or node.get("extent")
        loc = (p["layer"], p["off"], p["len"]) if p else (None, None, None)
        where = loc[0]
    if layer is not None and where is not None and where != layer:
        raise AddrError("%r is in layer %d, not layer %d"
                        % (node["id"] + ("#" + key if key else ""), where, layer))
    return Target(node, field, *loc)


def resolve(doc, addr):
    """The Target an ADDR names in the v1 dict `doc`.

    The whole text is tried as a target first, and a trailing RANGE is split
    off only if that fails, so a field key that happens to end in `+2` still
    resolves as itself."""
    text = str(addr).strip()
    if not text:
        raise AddrError("an empty address")
    layer = None
    m = _LAYER.match(text)
    if m:
        layer, text = int(m.group("l")), m.group("rest")
    try:
        return _target(doc, text, layer)
    except AddrError as first:
        t, kind, nums = _split_range(text)
        if kind is None:
            raise
        try:
            base = _target(doc, t, layer)
        except AddrError:
            raise first from None
    if base.off is None:
        raise AddrError("%r has no byte range to take a range of" % t)
    if kind == "plus":
        off, n = base.off, nums[0]
    elif kind == "to":
        if nums[0] < base.off:
            raise AddrError("the end 0x%x is before the start 0x%x"
                            % (nums[0], base.off))
        off, n = base.off, nums[0] - base.off
    else:
        off, n = base.off + nums[0], nums[1]
    length = _layer_length(doc, base.layer)
    if length is not None and off + n > length:
        raise AddrError("%d bytes at 0x%x run past the %d-byte layer %d"
                        % (n, off, length, base.layer))
    return Target(base.node, base.field, base.layer, off, n)


def addr_of(node, field=None):
    """The ADDR that names a node or one of its fields, as every report prints
    it: the layer is written when it is not 0, so the text pastes back."""
    where = (field or {}).get("at") or node.get("extent") or node.get("payload")
    layer = where.get("layer", 0) if where else 0
    text = node["id"] + ("#" + field["key"] if field is not None else "")
    return ("%d:%s" % (layer, text)) if layer else text
