"""XMP: Adobe's metadata packet, as RDF/XML.

A `_PMX` chunk in a RIFF file, an `XMP ` chunk elsewhere, an `uuid` box in an
MP4, an APP1 segment in a JPEG. Same packet every time, which is the argument
for reading it here rather than in each walker.

The packet is RDF/XML, so the properties are namespaced: `xmp:CreatorTool`,
`dc:title`, `xmpDM:artist`. Only the namespaces are named here -- a property
inside a known namespace is reported under its own name whether or not anyone
has seen it before, because inventing a whitelist of property names is how a
reader ends up silently dropping the one field that mattered.
"""

import re
import xml.etree.ElementTree as ET

from acidcat.core.infra.limits import hit
from acidcat.core.infra.findings import defect

# Namespace URI -> the prefix the spec uses for it. Anything outside this table
# is reported under its URI's last path segment, so an unrecognized vocabulary
# still reaches the reader.
_NS = {
    "http://ns.adobe.com/xap/1.0/": "xmp",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://ns.adobe.com/xmp/1.0/DynamicMedia/": "xmpDM",
    "http://ns.adobe.com/xap/1.0/rights/": "xmpRights",
    "http://ns.adobe.com/xap/1.0/mm/": "xmpMM",
    "http://ns.adobe.com/photoshop/1.0/": "photoshop",
    "http://ns.adobe.com/tiff/1.0/": "tiff",
    "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/": "Iptc4xmpCore",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
}

_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"

# The packet is text and a hostile one can be any size. 4 MB is far above any
# real packet (the largest measured is a few KB) and exists so a crafted chunk
# cannot be parsed without bound.
_PACKET_CAP = 4 * 1024 * 1024
_VALUE_CAP = 400

_XPACKET = re.compile(rb"<\?xpacket\b")


# An attribute name with TWO colons, which XML does not allow: a QName has at
# most one. Anchored on the space before it and the `=` after so it cannot
# match inside an attribute's value, where a colon is ordinary text.
_EXTRA_COLON = re.compile(
    rb"(\s)([A-Za-z_][\w.-]*):([\w.-]+):([\w.-]+)(\s*=)")
# A packet with hundreds of these is not one writer's quirk. Past this the
# repair stops and the packet stays reported as malformed.
_REPAIR_CAP = 256


def repair_qnames(body):
    """Rewrite `a:b:c=` to `a:b_c=`. Returns (repaired, how many).

    The extra colon becomes an underscore rather than being dropped, so the
    recovered name still says what the writer wrote: `dc:description_2`, not
    `dc:description` colliding with a real one.
    """
    count = [0]

    def sub(m):
        if count[0] >= _REPAIR_CAP:
            return m.group(0)
        count[0] += 1
        return (m.group(1) + m.group(2) + b":" + m.group(3) + b"_"
                + m.group(4) + m.group(5))

    return _EXTRA_COLON.sub(sub, body), count[0]


def is_xmp(data):
    """True if the bytes open as an XMP packet."""
    head = data[:256]
    return bool(_XPACKET.search(head)) or b"<x:xmpmeta" in head


def parse_xmp(data):
    """Read an XMP packet.

    Returns (properties, warnings): properties is a list of
    (qualified name, value) in document order, warnings names anything the
    packet got wrong about itself.

    Values come back flattened. RDF wraps a translated string in an `rdf:Alt`
    and a list in an `rdf:Bag` or `rdf:Seq`, so `dc:title` is a container in
    the XML and a string here. An Alt keeps its first entry and drops the rest,
    because they are the same text in another language; a Bag or a Seq keeps
    all of them, because they are different things.
    """
    warns = []
    if len(data) > _PACKET_CAP:
        warns.append(hit("chunk_payload", _PACKET_CAP, len(data),
                         f"XMP packet is {len(data):,} bytes; reading the first "
                         f"{_PACKET_CAP:,}"))
        data = data[:_PACKET_CAP]
    # the packet is bracketed by processing instructions that are not part of
    # the document, and a writer may pad after the end with whitespace or NULs
    start = data.find(b"<x:xmpmeta")
    if start < 0:
        start = data.find(b"<rdf:RDF")
    if start < 0:
        return [], warns + [defect("required.missing", "no xmpmeta or RDF element in the packet")]
    end = data.rfind(b"</x:xmpmeta>")
    end = end + len("</x:xmpmeta>") if end > start else data.rfind(b"</rdf:RDF>")
    if end <= start:
        return [], warns + [defect("parse.failed", "the packet's root element is not closed")]
    body = data[start:end + (len("</rdf:RDF>") if b"</x:xmpmeta>" not in data
                             else 0)]
    try:
        root = ET.fromstring(body)
    except ET.ParseError as first:
        # One malformation is common enough to be worth recovering from, and
        # only one: attribute names with an extra colon. Anything else stays
        # reported and unread.
        body, fixed = repair_qnames(body)
        if not fixed:
            return [], warns + [
                defect("parse.failed", f"the XMP packet is not well-formed XML ({first})")]
        try:
            root = ET.fromstring(body)
        except ET.ParseError as second:
            return [], warns + [
                defect("parse.failed", f"the XMP packet is not well-formed XML ({second})")]
        warns.append(
            defect("parse.failed",
                   f"the XMP packet is not well-formed XML: {fixed} attribute "
                   f"name(s) carry a second colon, which a QName may not. They were "
                   f"read as if the extra colon were an underscore; the values below "
                   f"are recovered, not as written"))

    props = []
    for desc in root.iter(_RDF + "Description"):
        # an rdf:Description carries simple properties as ATTRIBUTES as well as
        # as child elements, and a writer may use either for the same field
        for key, value in desc.attrib.items():
            name = _qualify(key)
            if name and value.strip():
                props.append((name, _clip(value)))
        for child in desc:
            name = _qualify(child.tag)
            if not name:
                continue
            value = _flatten(child)
            if value:
                props.append((name, _clip(value)))
    return props, warns


def _qualify(tag):
    """`{uri}local` -> `prefix:local`, or None for rdf's own plumbing."""
    if not tag.startswith("{"):
        return tag
    uri, _, local = tag[1:].partition("}")
    if uri == _RDF[1:-1]:
        return None                     # rdf:about, rdf:parseType, rdf:li
    prefix = _NS.get(uri)
    if prefix is None:
        prefix = uri.rstrip("/#").rsplit("/", 1)[-1]
    return f"{prefix}:{local}"


def _flatten(el):
    """The text of a property, through whatever RDF container holds it."""
    text = (el.text or "").strip()
    if text:
        return text
    for container in el:
        if container.tag not in (_RDF + "Alt", _RDF + "Bag", _RDF + "Seq"):
            continue
        items = [i for i in ((li.text or "").strip() for li in container) if i]
        if not items:
            continue
        # an Alt is one value in several languages; a Bag or a Seq is several
        # values. Joining an Alt would print the same sentence twice.
        return items[0] if container.tag == _RDF + "Alt" else ", ".join(items)
    return ""


def _clip(value):
    value = " ".join(value.split())
    return value if len(value) <= _VALUE_CAP else value[:_VALUE_CAP - 1] + "…"
