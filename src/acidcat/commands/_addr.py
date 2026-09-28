"""ADDR on the command line: `od FILE ADDR`, `carve FILE ADDR`, `--at ADDR`.

An ADDR (node-v1 section 13) names a node, a field or a byte range. The
search anchors `--at` has always taken (`end[-N]`, `find:STR`, `find:0xHEX`,
`chunk:ID[+N]`, a bare number) stay outside the grammar with their own syntax
(cli-2.0.md section 4, answer 3): an address never searches, so a value that
reads as an anchor is an anchor.
"""

import re

_ANCHOR = re.compile(r"^(?:end(?:-\S+)?|find:.+|chunk:.+|0[xX][0-9a-fA-F]+|\d+)$")


def is_anchor(text):
    """True for a search anchor or a bare offset, which keep their 1.8 meaning."""
    return bool(_ANCHOR.match(str(text).strip()))


def open_doc(path):
    import acidcat
    return acidcat.open(path, forensics=False)


_BYTES = re.compile(r"^(?:0:)?@(?P<off>0[xX][0-9a-fA-F]+|\d+)"
                    r"(?:\+(?P<len>0[xX][0-9a-fA-F]+|\d+)"
                    r"|\.\.(?P<end>0[xX][0-9a-fA-F]+|\d+))?$")


def byte_range(addr):
    """(start, length or None) for a plain `@OFF`, `@OFF+LEN` or `@OFF..END`,
    which needs no walk: it names bytes, whatever the file is. None for any
    other address."""
    m = _BYTES.match(str(addr).strip())
    if not m:
        return None
    off = int(m.group("off"), 0)
    if m.group("len"):
        return off, int(m.group("len"), 0)
    if m.group("end"):
        end = int(m.group("end"), 0)
        if end < off:
            raise ValueError("the end 0x%x is before the start 0x%x" % (end, off))
        return off, end - off
    return off, None


def resolve(path, addr, raw=False):
    """(start, length) in layer 0 for an ADDR in the file at `path`; see
    resolve_named."""
    return resolve_named(path, addr, raw)[:2]


def resolve_named(path, addr, raw=False):
    """(start, length, name) in layer 0 for an ADDR in the file at `path`,
    `name` being the node id (with `#key` for a field) the address resolved
    to, so a header can print what pastes back, or the ADDR itself for a
    plain byte range. A node
    means its payload, or its whole extent with `raw`; a field its bytes; a
    plain byte range is read without walking the file, and `@OFF` alone runs
    to the end of it. Raises acidcat.AddrError for an address that names
    nothing there, and ValueError for one that names bytes outside layer 0 or
    none at all, or a node in a file no walker reads."""
    import os
    from acidcat.core.infra import addr as addrmod
    rng = byte_range(addr)
    if rng is not None:
        off, n = rng
        size = os.path.getsize(path)
        if off > size:
            raise ValueError("0x%x is past the end of the %d-byte file" % (off, size))
        return off, (size - off if n is None else n), str(addr)
    from acidcat.core.walk.base import Unsupported
    try:
        doc = open_doc(path)
    except Unsupported:
        raise ValueError("no walker reads this file, so only a byte range "
                         "(@OFF+LEN, @OFF..END) names anything in it") from None
    t = addrmod.resolve(doc.to_json(), addr)
    name = str(addr)
    if t.node is not None:
        # the id, when the address named the node's or the field's own bytes;
        # a sub-range (`RIFF/fmt_[4:4]`, `fmt+2`) keeps the spelling given
        own = (t.field or {}).get("at") if t.field is not None else t.node.get("payload")
        if own and (own.get("off"), own.get("len")) == (t.off, t.len):
            name = t.node["id"] + ("#" + t.field["key"] if t.field is not None else "")
    if raw and t.node is not None and t.field is None:
        e = t.node.get("extent")
        if e:
            return _layer0(addr, e["layer"], e["off"], e["len"]) + (name,)
    if t.off is None:
        raise ValueError("%s has no byte range (it is unpositioned)" % addr)
    return _layer0(addr, t.layer, t.off, t.len) + (name,)


def _layer0(addr, layer, off, n):
    if layer != 0:
        raise ValueError("%s is in layer %d, a decoded image; `carve --layer %d` "
                         "writes that layer's bytes" % (addr, layer, layer))
    return off, n


def fields(path, addr):
    """[(field addr, value)] for an ADDR that names fields: one field, or
    `GLOB#KEY` for that key in every node the glob matches (`**#sample_rate`).
    Raises acidcat.AddrError when nothing matches."""
    from acidcat.core.infra import addr as addrmod
    from acidcat.core.walk.base import Unsupported
    try:
        doc = open_doc(path)
    except Unsupported:
        raise addrmod.AddrError("no walker reads this file, so it has no "
                                "fields") from None
    term, _, key = addr.rpartition("#")
    if addrmod.is_glob(term):
        out = [(f.addr, f.value) for n in doc.find(term) for f in n.fields
               if f.key == key]
        if not out:
            keys = sorted({f.key for n in doc.walk() for f in n.fields})
            raise addrmod.AddrError("no field %r in any node %r matches; fields "
                                    "here: %s" % (key, term, ", ".join(keys)), keys)
        return out
    f = doc.field(addr)
    return [(f.addr, f.value)]


def names_fields(addr):
    return "#" in str(addr)
