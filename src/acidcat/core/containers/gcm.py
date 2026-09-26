"""GameCube disc image (GCM / ISO) filesystem walk.

A GameCube disc is not ISO 9660. Its own format: a header carrying the magic
word 0xC2339F3D at 0x1C, a pointer at 0x424 to the FST (File String Table), and
the FST itself -- a flat array of 12-byte entries (file or directory) plus a
trailing string table. Directories store the index one past their last child,
so the tree is walked by index ranges, not nesting.

    from acidcat.core.containers import gcm
    for f in gcm.walk("game.iso"):
        print(f["path"], f["offset"], f["size"])
"""

import struct

from acidcat.core.infra.source import open_input, input_size

MAGIC = 0xC2339F3D                   # GameCube disc magic word, at offset 0x1C
_MAGIC_OFF = 0x1C
_MAX_DEPTH = 64          # a real disc nests a handful deep


def is_gcm(path):
    """True if `path` is a GameCube disc image (by the 0x1C magic word)."""
    try:
        with open_input(path) as f:
            f.seek(_MAGIC_OFF)
            return struct.unpack(">I", f.read(4))[0] == MAGIC
    except (OSError, struct.error):
        return False


def _entry(fst, i):
    return struct.unpack_from(">I", fst, i * 12)[0], \
        struct.unpack_from(">II", fst, i * 12 + 4)


def walk(path):
    """Yield {path, offset, size} for every file on a GameCube disc, or nothing
    if `path` is not one. offset/size are absolute byte positions in the image."""
    fsize = input_size(path)
    with open_input(path) as f:
        head = f.read(0x440)
        if len(head) < 0x440 or struct.unpack_from(">I", head, _MAGIC_OFF)[0] != MAGIC:
            return
        fst_off, fst_size = struct.unpack_from(">II", head, 0x424)
        # fst_size is an unchecked u32 straight from the image. f.read(n)
        # commits n bytes before it discovers the file is shorter, so a 1 KB
        # image declaring 0xFFFFFFFF allocated 4.3 GB. Clamp to what can
        # actually be there; a truncated FST fails the length checks below.
        if fst_off >= fsize:
            return
        fst_size = min(fst_size, fsize - fst_off)
        f.seek(fst_off)
        fst = f.read(fst_size)
    if len(fst) < 12:
        return
    num = struct.unpack_from(">I", fst, 8)[0]         # root entry's size = entry count
    strtab = num * 12
    if strtab > len(fst):
        return

    def name(i):
        no = struct.unpack_from(">I", fst, i * 12)[0] & 0xFFFFFF
        end = fst.find(b"\x00", strtab + no)
        return fst[strtab + no:end].decode("latin-1", "replace")

    out = []

    # A directory entry's "size" is an index past its last child, and nothing in
    # the image guarantees it points FORWARD. Two ways that hung or crashed:
    #
    #   idx = size where size <= idx  -- the cursor never advances and the while
    #   loop spins forever. A 1,114-byte file span indefinitely at 0% progress,
    #   with no output and no way to interrupt it in a pipeline.
    #
    #   a directory whose child-end points backwards re-enters the same range,
    #   so descend() recurses until RecursionError.
    #
    # Both are fixed by the same rule: the cursor must strictly increase, and a
    # child range must lie forward of the entry that declares it. Depth is
    # bounded too, since a legitimate disc is nowhere near this deep.
    def descend(idx, prefix, end, depth):
        while idx < end and idx < num:
            typ = fst[idx * 12]
            off, size = struct.unpack_from(">II", fst, idx * 12 + 4)
            nm = name(idx)
            if typ == 1:                              # directory: size = index past last child
                child_end = min(size, num)
                if child_end > idx + 1 and depth < _MAX_DEPTH:
                    descend(idx + 1, prefix + nm + "/", child_end, depth + 1)
                idx = child_end if child_end > idx else idx + 1
            else:
                out.append({"path": prefix + nm, "offset": off, "size": size})
                idx += 1

    descend(1, "", num, 0)
    yield from out


def read_file(path, entry, limit=None):
    """Read a file's bytes from its walk() entry (absolute offset/size).

    The size is clamped to what remains in the image. It comes from the FST
    unchecked, and a 1,118-byte file declaring a 0xFFFFFFFF entry allocated
    4.3 GB here before finding out the file was 1 KB long.
    """
    fsize = input_size(path)
    off = entry["offset"]
    if off >= fsize:
        return b""
    n = entry["size"] if limit is None else min(entry["size"], limit)
    n = min(n, fsize - off)
    with open_input(path) as f:
        f.seek(off)
        return f.read(n)
