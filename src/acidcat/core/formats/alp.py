"""Ableton Live Packs (.alp).

A pack is one gzip stream. Inside it, a 'pl-a' container:

    0x00  'pl-a'
    0x04  u32   offset of the index, from the start of the container
    0x08  u32   0
    0x0c        file data, back to back: each file's bytes as stored (Live
                documents gzipped, samples as FLAC)
    index       Ableton's self-describing object serialisation, opening with
                the same ab 1e 56 78 sentinel .asd files carry

The index declares its classes inline at first use and then stores instances.
The one that matters is the file tree, a PackageItem per file or folder:

    PackageItem  u8 len + 'PackageItem', u32 position in its parent, then
                 Name (u32 count + UTF-16LE), ModDate (u32, Unix seconds),
                 Children (a list), IsDir (u8), InsertVersionSubdir (u8),
                 FileDataOffset, FileSize, OriginalSize (u64 each; the offset
                 counts from the start of the container), IsCompressed (u8),
                 Metadata (a slot), MetadataOnly (u8)
    list, slot   u32 size, then per item u8 0, the class name and a u32
                 position, then the item; u8 0 and an empty name end it
    Metadata     MetadataRec{Items: [MetadataItem{Values: [text], Key}],
                 SqlSchemaVersion u32}

Measured on real packs, not taken from a published spec: parsing the tree
this way consumes every record to the byte, and the stored files, sorted by
offset, tile the data region with no gap or overlap.
"""

import struct
import zlib

MAGIC = b"pl-a"
INDEX_MAGIC = b"\xab\x1e\x56\x78"
DATA_START = 0x0C
_ITEM = b"\x0bPackageItem"


class AlpError(ValueError):
    pass


def is_alp_container(head):
    return head[:4] == MAGIC


def looks_like_alp(fh):
    """True when a gzip stream's first bytes are a pl-a container."""
    try:
        for _off, part in stream(fh, chunk=64 * 1024, max_out=16):
            return is_alp_container(part)
    except zlib.error:
        return False
    return False


def stream(fh, chunk=1 << 20, max_out=None):
    """Yield (offset, bytes) of the decompressed container, reading `fh`
    (the .alp) through its gzip layer."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    pos = 0
    while True:
        raw = fh.read(chunk)
        if not raw:
            tail = d.flush()
            if tail:
                yield pos, tail
            return
        out = d.decompress(raw)
        if out:
            yield pos, out
            pos += len(out)
            if max_out is not None and pos > max_out:
                return


def read_index(fh, cap):
    """(index_offset, total, index_bytes, complete) from one streamed pass.
    index_bytes holds at most `cap` bytes; complete is False when the gzip
    stream broke off (the exception is re-raised after the fields are set)."""
    idx = None
    index = bytearray()
    total = 0
    for off, part in stream(fh):
        if idx is None:
            if not is_alp_container(part):
                raise AlpError("gzip, but not a Live Pack container")
            idx = int.from_bytes(part[4:8], "little")
        end = off + len(part)
        if end > idx and len(index) < cap:
            index += part[max(0, idx - off):][:cap - len(index)]
        total = end
    if idx is None:
        raise AlpError("the gzip stream is empty")
    return idx, total, bytes(index)


def ranges(fh, wanted):
    """Yield (key, bytes) for each (offset, size, key) in `wanted`, in offset
    order, from one streamed pass. Only the file being collected is held."""
    wanted = sorted(wanted)
    i = 0
    buf = bytearray()
    for off, part in stream(fh):
        end = off + len(part)
        while i < len(wanted):
            start, size, key = wanted[i]
            stop = start + size
            if start >= end:
                break
            lo, hi = max(start, off), min(stop, end)
            if hi > lo:
                buf += part[lo - off:hi - off]
            if stop <= end:
                yield key, bytes(buf)
                buf = bytearray()
                i += 1
            else:
                break


class _Reader:
    def __init__(self, b, pos):
        self.b, self.p = b, pos

    def take(self, n):
        if self.p + n > len(self.b):
            raise AlpError(f"the index ends inside a record at {self.p:#x}")
        v = self.b[self.p:self.p + n]
        self.p += n
        return v

    def u8(self):
        return self.take(1)[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.take(8))[0]

    def text(self, max_chars=4096):
        n = self.u32()
        if n > max_chars:
            raise AlpError(f"a {n:,}-character string at {self.p - 4:#x}")
        return self.take(2 * n).decode("utf-16-le", "replace")

    def cls(self):
        n = self.u8()
        return self.take(n).decode("latin-1")


def _items(r, name, parse, budget):
    """A list or slot of `name` records. A name ending in '*' accepts any class
    with that prefix removed as a suffix: the metadata value list holds
    StringMetadatum, Int64Metadatum or ImmutableMetadatum."""
    # the u32 is the list's size, not how many items follow: a list can be
    # sparse (a size of 2 holding one item, at position 1). What ends it is a
    # zero byte and an empty class name, where the next item would begin.
    size = r.u32()
    out = []
    while True:
        if r.u8() != 0:
            raise AlpError(f"a list item at {r.p - 1:#x} is not an inline record")
        got = r.cls()
        if got == "":
            break
        if len(out) >= max(size, 1) or budget[0] <= 0:
            raise AlpError(f"a list at {r.p:#x} holds more items than its size, {size}")
        if name.startswith("*") and got.endswith(name[1:]):
            pass
        elif got != name:
            raise AlpError(f"expected {name} at {r.p:#x}, found {got!r}")
        r.u32()                                     # position in the list
        out.append(parse(r, budget, got) if name.startswith("*") else parse(r, budget))
    return out


# how each type tag in a class definition is stored in an instance
_SCALARS = {0x10: ("B", 1), 0x11: ("<I", 4), 0x12: ("<d", 8), 0x18: ("<Q", 8)}


def value_tag(index, cls):
    """The type tag of the single `Value` field a metadatum class declares.
    Classes are defined once, inline, at first use: u8 len + name, u32 field
    count, then per field u32 count + UTF-16LE name + u8 tag."""
    key = bytes([len(cls)]) + cls.encode("latin-1") + struct.pack("<I", 1) \
        + struct.pack("<I", 5) + "Value".encode("utf-16-le")
    at = index.find(key)
    if at < 0:
        raise AlpError(f"the index does not define {cls}")
    return index[at + len(key)]


def _metadatum(r, budget, cls):
    tags = budget[1]
    if cls not in tags:
        tags[cls] = value_tag(r.b, cls)
    tag = tags[cls]
    if tag == 0x14:
        return r.text()
    if tag in _SCALARS:
        fmt, n = _SCALARS[tag]
        return struct.unpack(fmt, r.take(n))[0]
    raise AlpError(f"{cls} stores its value with type tag {tag:#04x}, not decoded")


def _metadata_item(r, budget):
    values = _items(r, "*Metadatum", _metadatum, budget)
    key = r.text()
    return key, values


def _metadata_rec(r, budget):
    items = _items(r, "MetadataItem", _metadata_item, budget)
    r.u32()                                         # SqlSchemaVersion
    return items


def _item(r, budget, depth=0):
    budget[0] -= 1
    if budget[0] < 0 or depth > 64:
        raise AlpError("the file tree is deeper or larger than the index can hold")
    at = r.p
    name = r.text()
    mod = r.u32()
    kids = _items(r, "PackageItem", lambda rr, b: _item(rr, b, depth + 1), budget)
    is_dir = r.u8()
    r.u8()                                          # InsertVersionSubdir
    off, size, orig = r.u64(), r.u64(), r.u64()
    comp = r.u8()
    meta = [kv for rec in _items(r, "MetadataRec", _metadata_rec, budget) for kv in rec]
    r.u8()                                          # MetadataOnly
    return {"name": name, "mod_date": mod, "children": kids, "is_dir": bool(is_dir),
            "offset": off, "size": size, "original_size": orig,
            "compressed": bool(comp), "metadata": dict((k, v[0] if len(v) == 1 else v)
                                                       for k, v in meta),
            "at": at}


def find_root(index):
    """Offset (in `index`) of the root PackageItem's Name.

    The root is written straight after the class definitions, without the
    class-name header its descendants carry, so it is found from its first
    child: that child's header follows the root's Name, ModDate, child count
    and the list item's zero byte."""
    first = index.find(_ITEM + b"\x00\x00\x00\x00")
    while first >= 0:
        tail = first - 9                            # u32 ModDate, u32 count, u8 0
        for chars in range(1, 1024):
            at = tail - 2 * chars - 4
            if at < 0:
                break
            if struct.unpack_from("<I", index, at)[0] == chars:
                return at
        first = index.find(_ITEM + b"\x00\x00\x00\x00", first + 1)
    raise AlpError("no file tree in the index")


def parse_tree(index, max_records):
    """The root PackageItem as nested dicts."""
    r = _Reader(index, find_root(index))
    return _item(r, [max_records, {}])


def walk(tree, path=""):
    """(path, item) for every item under the root, depth first."""
    for kid in tree["children"]:
        p = f"{path}/{kid['name']}" if path else kid["name"]
        yield p, kid
        yield from walk(kid, p)
