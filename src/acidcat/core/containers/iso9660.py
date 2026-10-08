"""ISO 9660 filesystem walk for CD images.

A CD-XA disc is not just a stream of sectors; its data track carries an ISO 9660
filesystem naming the game's files -- the .STR movies, .XA audio, .VAB/.VB sound
banks, .VAG samples. Walking it turns "raw audio channel" into named tracks and
lets us reach the sound effects, not just the streamed music.

Layout handled: raw Mode2/2352 images (user data 2048 bytes at sector offset 24,
the PlayStation case), raw Mode1/2352 (offset 16), and cooked 2048-byte images.
The volume descriptor at logical block 16 carries "CD001"; that anchors both the
sector geometry and the root directory.

    from acidcat.core.containers import iso9660
    for f in iso9660.walk("game.bin"):
        print(f["path"], f["lba"], f["size"])
    data = iso9660.read_file("game.bin", entry)
"""

import os
import struct

from acidcat.core.infra.source import open_input

_USER = 2048                         # ISO logical block size


def _layout(f):
    """Find the sector geometry by locating 'CD001' at logical block 16.
    Returns (sector_size, user_offset) or None."""
    for sector_size, user_off in ((2352, 24), (2352, 16), (2048, 0)):
        f.seek(16 * sector_size + user_off)
        vd = f.read(6)
        if len(vd) == 6 and vd[0] == 1 and vd[1:6] == b"CD001":
            return sector_size, user_off
    return None


def _read_block(f, lba, layout):
    sector_size, user_off = layout
    f.seek(lba * sector_size + user_off)
    return f.read(_USER)


def _read_extent(f, lba, size, layout):
    """Read `size` bytes starting at `lba`, bounded by the image.

    `size` comes from a directory record unchecked. A record declaring a 4 GiB
    extent cost 2,097,152 seek+read calls against a 110 KB image, and 24 such
    records in one file ran past 90 seconds -- a hang built from a handful of
    bytes. Clamping to the sectors the image actually holds makes the cost
    proportional to the file rather than to a number inside it.
    """
    sector_size, _user_off = layout
    try:
        fsize = os.fstat(f.fileno()).st_size
    except OSError:
        fsize = None
    n = (size + _USER - 1) // _USER
    if fsize is not None:
        avail = max(0, (fsize // sector_size) - lba)
        n = min(n, avail)
    return b"".join(_read_block(f, lba + k, layout) for k in range(n))[:size]


def _dir_records(block):
    """Yield the directory records in a directory extent."""
    i = 0
    while i < len(block):
        rec_len = block[i]
        if rec_len == 0:                     # zero padding runs to the next block
            i = ((i // _USER) + 1) * _USER
            continue
        rec = block[i:i + rec_len]
        if len(rec) >= 33:
            name_len = rec[32]
            yield {
                "lba": struct.unpack_from("<I", rec, 2)[0],
                "size": struct.unpack_from("<I", rec, 10)[0],
                "is_dir": bool(rec[25] & 0x02),
                "name": rec[33:33 + name_len],
            }
        i += rec_len


def _clean_name(raw):
    # ISO names are "NAME.EXT;VERSION"; drop the version and a trailing dot
    nm = raw.split(b";", 1)[0].decode("latin-1", "replace")
    return nm[:-1] if nm.endswith(".") else nm


def walk(path):
    """Yield {path, lba, size} for every file in the ISO 9660 tree (depth-first).
    Returns nothing if `path` has no ISO filesystem."""
    with open_input(path) as f:
        layout = _layout(f)
        if layout is None:
            return
        pvd = _read_block(f, 16, layout)
        root = pvd[156:156 + 34]                         # root directory record
        stack = [("", struct.unpack_from("<I", root, 2)[0],
                  struct.unpack_from("<I", root, 10)[0])]
        seen = set()
        while stack:
            prefix, lba, size = stack.pop()
            if (lba, size) in seen or not size:          # guard cycles / empty
                continue
            seen.add((lba, size))
            data = _read_extent(f, lba, size, layout)
            for rec in _dir_records(data):
                if rec["name"] in (b"\x00", b"\x01"):    # '.' and '..'
                    continue
                name = _clean_name(rec["name"])
                full = f"{prefix}/{name}" if prefix else name
                if rec["is_dir"]:
                    stack.append((full, rec["lba"], rec["size"]))
                else:
                    yield {"path": full, "lba": rec["lba"], "size": rec["size"]}


def read_file(path, entry, limit=None):
    """Read a file's bytes from its walk() entry. `limit` caps the bytes read (a
    prefix, for content sniffing). Reads Form1/2048 user data; XA (Form2) audio
    files are decoded via core.cdxa by sector range instead."""
    with open_input(path) as f:
        layout = _layout(f)
        if layout is None:
            raise ValueError("no ISO 9660 filesystem")
        size = entry["size"] if limit is None else min(entry["size"], limit)
        return _read_extent(f, entry["lba"], size, layout)
