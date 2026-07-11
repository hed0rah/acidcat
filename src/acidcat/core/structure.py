"""A structural model of IFF-family containers (RIFF/WAVE, RF64, AIFF/AIFC) with
size-cascade solving, so that editing and repair are one operation: mutate the
tree, re-satisfy the structural constraints, re-emit the bytes.

The container grammar is self-describing. Every chunk is ``[id:4][size:4][payload]``
and a container (RIFF, RF64, FORM at the top; LIST when nested) is that plus a
4-byte form/list type ahead of its children. That means the entire *size cascade*
-- a container's size is a function of its children's sizes, a leaf's size is its
payload length -- can be derived with zero per-format knowledge. Recomputing those
derived sizes from the actual layout is exactly what fixes the most common real
corruption: a master RIFF size left stale after a tool appended or crashed
mid-write (the classic "riff_size says X, file is Y" lint).

The bedrock invariant is byte-exact round-trip: ``emit(parse(b)) == b`` for any
well-formed IFF file. Repair is the same pipeline with ``recompute()`` in the
middle; every byte it changes is a derived field it can justify from the layout.

This is the first concrete instance of acidcat's declarative-structure direction.
The derived-field vocabulary here (SIZE) is one of four the walkers already imply
through their field annotations (SIZE, COUNT, OFFSET-table, ZERO-fill); the model
is built to grow the others in without changing the round-trip contract.
"""

import struct

_RIFF = b"RIFF"
_RF64 = b"RF64"
_FORM = b"FORM"
_LIST = b"LIST"
# top-level magics that open a container, and the endianness of their size fields
_TOP_LE = (_RIFF, _RF64)          # RIFF/RF64: little-endian sizes
_TOP_BE = (_FORM,)                # IFF/AIFF: big-endian sizes
# ids that carry a 4-byte form/list type and hold child chunks
_CONTAINER_IDS = (_RIFF, _RF64, _FORM, _LIST)


class StructError(ValueError):
    """The bytes are not a parseable IFF container."""


def _id_ok(data, pos):
    """A plausible chunk id starts here: 4 printable-ASCII bytes in range."""
    return pos + 4 <= len(data) and all(0x20 <= b < 0x7f for b in data[pos:pos + 4])


class Node:
    """One span in the container tree.

    A container (``children is not None``) is ``[id][size][form_type][children]``;
    a leaf is ``[id][size][payload]``. ``pad`` is the count of on-disk pad bytes
    that follow the span (0 or 1; RIFF pads an odd payload to an even boundary,
    though some writers, e.g. MuseScore SF3, omit it -- preserved as parsed).
    ``pad_byte`` is the actual pad value seen, so round-trip keeps a non-zero pad
    while repair can normalize it to 0x00.
    """

    __slots__ = ("id", "offset", "endian", "form_type", "children", "payload",
                 "declared_size", "pad", "pad_byte", "tail", "tail_counts")

    def __init__(self, cid, offset, endian):
        self.id = cid                 # 4 raw bytes
        self.offset = offset          # absolute offset of the id
        self.endian = endian          # "<" or ">"
        self.form_type = None         # 4 bytes for a container, else None
        self.children = None          # list[Node] for a container, else None
        self.payload = None           # bytes for a leaf, else None
        self.declared_size = None     # the size field as read from disk
        self.pad = 0                  # on-disk pad bytes after this span (0/1)
        self.pad_byte = 0             # value of the pad byte seen on disk
        self.tail = b""               # bytes after the last child (containers)
        # whether ``tail`` counts toward this container's size. padding inside a
        # nested LIST counts; data appended AFTER the top container (a polyglot,
        # a crash-truncated re-write, an editor that appended without adjusting)
        # is outside the master size, matching edit_riff's riff_size convention.
        self.tail_counts = True

    @property
    def is_container(self):
        return self.children is not None

    @property
    def header_len(self):
        return 12 if self.is_container else 8

    def computed_size(self):
        """The size field this span *should* carry, derived from its content.
        Leaf: payload length. Container: form_type (4) + every child's on-disk
        total + any trailing bytes held inside it."""
        if self.is_container:
            n = 4
            for c in self.children:
                n += c.on_disk_len()
            return n + (len(self.tail) if self.tail_counts else 0)
        return len(self.payload)

    def on_disk_len(self):
        """Total bytes this span occupies on disk. Every chunk is
        ``[id:4][size:4][content]`` plus an optional pad byte, and a container's
        size field already counts its 4-byte form type, so the header is 8 for
        leaf and container alike -- the form type lives inside ``computed_size``."""
        return 8 + self.computed_size() + self.pad


# ── parse ──────────────────────────────────────────────────────────

def parse(data):
    """Parse an IFF container into a Node tree. Raises StructError if the head
    is not a RIFF/RF64/FORM magic. Tolerant of a lying master size and of
    unpadded odd chunks; trailing bytes past the last well-formed child are
    preserved on the nearest container so round-trip stays byte-exact."""
    if len(data) < 12:
        raise StructError("shorter than a 12-byte IFF header")
    magic = data[:4]
    if magic in _TOP_LE:
        endian = "<"
    elif magic in _TOP_BE:
        endian = ">"
    else:
        raise StructError("not a RIFF/RF64/FORM container")
    node, _ = _parse_container(data, 0, len(data), endian, top=True)
    return node


def _parse_leaf(data, off, endian, hard_end):
    """Parse one leaf chunk at ``off``. Returns (node, next_off). ``hard_end``
    bounds the payload against a lying size."""
    node = Node(data[off:off + 4], off, endian)
    size = struct.unpack_from(endian + "I", data, off + 4)[0]
    body_off = off + 8
    avail = max(0, hard_end - body_off)
    take = min(size, avail)
    node.declared_size = size
    node.payload = data[body_off:body_off + take]
    nxt = body_off + take
    # detect a pad byte after an odd payload: present unless the writer omitted
    # it (the next position parses straight into a valid chunk id)
    if take & 1 and nxt < hard_end:
        if _id_ok(data, nxt) and not _id_ok(data, nxt + 1):
            pass                       # unpadded writer
        else:
            node.pad = 1
            node.pad_byte = data[nxt]
            nxt += 1
    return node, nxt


def _parse_container(data, off, end, endian, top=False):
    """Parse a container at ``off`` spanning up to ``end``. Returns
    (node, next_off)."""
    node = Node(data[off:off + 4], off, endian)
    size = struct.unpack_from(endian + "I", data, off + 4)[0]
    node.declared_size = size
    node.form_type = data[off + 8:off + 12]
    node.children = []
    # at the top level a lying master size must not stop us short of real chunks
    # (the crash-truncated-size case we repair), so parse to the buffer end and
    # treat any post-container remainder as appended data outside the size; a
    # nested LIST is trusted to bound its own children.
    declared_end = off + 8 + size
    span_end = len(data) if top else min(end, declared_end)
    node.tail_counts = not top
    pos = off + 12
    while pos + 8 <= span_end:
        if not _id_ok(data, pos):
            break                      # garbage where a chunk id should be
        csize = struct.unpack_from(endian + "I", data, pos + 4)[0]
        if pos + 8 + csize > span_end:
            # a chunk claiming more bytes than remain is not real structure: it
            # is appended data whose first bytes happened to look like a header,
            # or unrecoverable corruption. leave it (and the rest) as tail rather
            # than absorb it into the container's size.
            break
        child_hard_end = min(span_end, pos + 8 + csize + 1)  # +1 for a pad byte
        if data[pos:pos + 4] == _LIST and pos + 12 <= span_end:
            child, nxt = _parse_container(data, pos, child_hard_end, endian)
        else:
            child, nxt = _parse_leaf(data, pos, endian, child_hard_end)
        node.children.append(child)
        if nxt <= pos:                 # no progress: stop rather than spin
            break
        pos = nxt
    if pos < span_end:
        node.tail = data[pos:span_end]
    return node, span_end


# ── recompute (repair) ─────────────────────────────────────────────

def recompute(node, normalize_pad=True):
    """Walk the tree bottom-up and correct every derived size field to match
    the actual content, and (when ``normalize_pad``) reset a non-zero pad byte
    to the spec's 0x00. Returns a list of change records:
    ``{path, field, old, new}``. An empty list means the file was already
    internally consistent."""
    changes = []
    _recompute(node, "", changes, normalize_pad)
    return changes


def _recompute(node, path, changes, normalize_pad):
    here = path + "/" + node.id.decode("latin-1", "replace").strip()
    if node.is_container:
        for c in node.children:
            _recompute(c, here, changes, normalize_pad)
    computed = node.computed_size()
    if node.declared_size != computed:
        changes.append({"path": here.lstrip("/"), "field": "size",
                        "old": node.declared_size, "new": computed})
        node.declared_size = computed
    if normalize_pad and node.pad and node.pad_byte != 0:
        changes.append({"path": here.lstrip("/"), "field": "pad_byte",
                        "old": node.pad_byte, "new": 0})
        node.pad_byte = 0


# ── emit ───────────────────────────────────────────────────────────

def emit(node):
    """Serialize the tree back to bytes using each node's ``declared_size``
    (which ``recompute`` may have corrected). For an untouched tree this is
    byte-exact with the parsed input."""
    out = bytearray()
    _emit(node, out)
    return bytes(out)


def _emit(node, out):
    out += node.id
    out += struct.pack(node.endian + "I", node.declared_size)
    if node.is_container:
        out += node.form_type
        for c in node.children:
            _emit(c, out)
        out += node.tail
    else:
        out += node.payload
    if node.pad:
        out += bytes([node.pad_byte])


# ── convenience ────────────────────────────────────────────────────

def is_iff(data):
    """True when ``data`` opens with a RIFF/RF64/FORM magic this model parses."""
    return len(data) >= 12 and data[:4] in (_TOP_LE + _TOP_BE)


def repair_bytes(data, normalize_pad=True):
    """Parse, recompute, and re-emit. Returns (new_bytes, changes). Raises
    StructError for non-IFF input."""
    node = parse(data)
    changes = recompute(node, normalize_pad=normalize_pad)
    return emit(node), changes
