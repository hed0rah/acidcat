"""The Kontakt sample container: .nkx and .nkr files, and the body of a
Kontakt 2-4 monolith .nki.

A directory tree, then the stored objects back to back. Every object opens
with a four-byte kind and a u16 version (0x110 or 0x111).

    directory  54 ac 70 5e   +14 u32 entry count, entries from +22
    sample     0a f8 cc 16   +19 u32 payload length, payload from +31
    resource   fa 05 e9 2a   +14 u32 payload length, payload from +22
    patch      3c e6 16 49   +19 u32 payload length, payload from +27

A directory entry is u16 entry length, u32 value, u16 type, then the name in
UTF-16LE with a terminating NUL. The type says what the value is:

    1  directory  offset of its directory object
    2  file       an identifier, not an offset; the data is stored in order
    3  patch      offset of its patch object
    4  resource   offset of its resource object

Offsets are from the start of the file, including inside a monolith, where
the container starts after the patch header.

Measured on 663 real containers, not taken from a published spec: every .nkx
and .nkr walk ended exactly at end of file. Older monoliths also hold a fourth
object kind, `9f 17 40 00`, whose length is not decoded; a walk stops there.
"""

import struct

DIRECTORY = b"\x54\xac\x70\x5e"
SAMPLE = b"\x0a\xf8\xcc\x16"
RESOURCE = b"\xfa\x05\xe9\x2a"
PATCH = b"\x3c\xe6\x16\x49"
UNDECODED = b"\x9f\x17\x40\x00"

# kind -> (name, offset of the u32 length, header length)
OBJECTS = {
    SAMPLE: ("sample", 19, 31),
    RESOURCE: ("resource", 14, 22),
    PATCH: ("patch", 19, 27),
}

ENTRY_TYPES = {1: "directory", 2: "file", 3: "patch", 4: "resource"}
_DIR_HEAD = 22


def is_container(head):
    return head[:4] == DIRECTORY and head[4:6] in (b"\x10\x01", b"\x11\x01")


def read_tree(read_at, base, max_entries, max_depth=32):
    """Walk the directory tree from `base`.

    Returns (entries, end, problem): entries are dicts {path, type, value,
    at} in directory order, end is the first byte past the directory region,
    and problem is None or a short string saying why the walk stopped early.
    `max_entries` bounds the total, and reaching it is a problem too."""
    entries = []
    end = base
    problem = None

    def walk(off, prefix, depth):
        nonlocal end, problem
        if depth > max_depth:
            problem = "depth"
            return
        head = read_at(off, _DIR_HEAD)
        if len(head) < _DIR_HEAD or head[:4] != DIRECTORY:
            problem = f"no directory object at {off:#x}"
            return
        count = struct.unpack_from("<I", head, 14)[0]
        pos = off + _DIR_HEAD
        for _ in range(count):
            if len(entries) >= max_entries:
                problem = "entries"
                return
            eh = read_at(pos, 8)
            if len(eh) < 8:
                problem = f"directory entry at {pos:#x} runs past the end"
                return
            elen, value, etype = struct.unpack("<HIH", eh)
            if elen < 10:
                problem = f"directory entry at {pos:#x} is {elen} bytes"
                return
            raw = read_at(pos + 8, elen - 8)
            name = raw.decode("utf-16-le", "replace").split("\0", 1)[0]
            path = f"{prefix}/{name}" if prefix else name
            entries.append({"path": path, "type": etype, "value": value, "at": pos})
            pos += elen
            end = max(end, pos)
            if etype == 1:
                walk(value, path, depth + 1)
                if problem:
                    return
        end = max(end, pos)

    walk(base, "", 0)
    return entries, end, problem


_MIN_ENTRY = 10          # u16 length, u32 value, u16 type, a NUL name


def _directory_length(read_at, pos, count, size):
    """Bytes a directory object of `count` entries spans, or None if an entry
    is malformed or the entries cannot fit in the file."""
    if pos + _DIR_HEAD + count * _MIN_ENTRY > size:
        return None
    off = pos + _DIR_HEAD
    for _ in range(count):
        eh = read_at(off, 2)
        if len(eh) < 2:
            return None
        elen = struct.unpack("<H", eh)[0]
        if elen < _MIN_ENTRY:
            return None
        off += elen
    return off - pos


def read_objects(read_at, start, size, max_steps):
    """Every object from `start` on, in file order, directories included --
    they are not always gathered at the front. Dicts {kind, at, payload_at,
    length, magic}. Returns (objects, end, stop): stop is None when the walk
    reached end of file, else why it stopped. "steps" means `max_steps`, which
    counts objects and directory entries together, was reached."""
    objs = []
    pos = start
    steps = 0
    while pos < size:
        if steps >= max_steps:
            return objs, pos, "steps"
        head = read_at(pos, 31)
        if head[:4] == DIRECTORY and len(head) >= _DIR_HEAD:
            count = struct.unpack_from("<I", head, 14)[0]
            # a count the file cannot hold is malformed, whatever the budget
            if pos + _DIR_HEAD + count * _MIN_ENTRY > size:
                return objs, pos, "directory"
            if steps + 1 + count > max_steps:
                return objs, pos, "steps"
            span = _directory_length(read_at, pos, count, size)
            if span is None:
                return objs, pos, "directory"
            steps += 1 + count
            objs.append({"kind": "directory", "at": pos, "payload_at": pos + _DIR_HEAD,
                         "length": span - _DIR_HEAD, "magic": b""})
            pos += span
            continue
        spec = OBJECTS.get(head[:4])
        if spec is None:
            return objs, pos, "undecoded" if head[:4] == UNDECODED else "unknown"
        name, len_at, hlen = spec
        if len(head) < len_at + 4:
            return objs, pos, "short"
        length = struct.unpack_from("<I", head, len_at)[0]
        if pos + hlen + length > size:
            return objs, pos, "overrun"
        payload = read_at(pos + hlen, 4)
        objs.append({"kind": name, "at": pos, "payload_at": pos + hlen,
                     "length": length, "magic": payload})
        pos += hlen + length
        steps += 1
    return objs, pos, None


def pair_files(entries, objects):
    """Pair each type-2 entry with its stored object.

    A type-2 entry's value is not an offset, so pairing is by order: the
    objects no type-3 or type-4 entry points at are the files, in directory
    order. Returns the list of (entry, object), or None when the counts
    disagree -- then the pairing is not safe to claim."""
    claimed = {e["value"] for e in entries if e["type"] in (3, 4)}
    files = [e for e in entries if e["type"] == 2]
    stored = [o for o in objects
              if o["kind"] != "directory" and o["at"] not in claimed]
    if len(files) != len(stored):
        return None
    return list(zip(files, stored))
