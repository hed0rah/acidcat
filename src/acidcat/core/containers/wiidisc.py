"""Wii optical disc (RVL) filesystem walk -- encrypted partitions.

A Wii disc carries the magic 0x5D1C9EA3 at 0x18 and a partition table at
0x40000. Each game partition is AES-128-CBC encrypted: a ticket holds the title
key (itself encrypted with the console-independent "common key"), and the
partition data is split into 0x8000 clusters of 0x400 encrypted hash bytes plus
0x7C00 encrypted payload -- the payload IV is lifted from bytes 0x3D0 of the
decrypted hash block. Decrypted clusters concatenate into a GameCube-style
logical image (header + FST), so the walk mirrors core/gcm.py once decrypted.

The AES needs `cryptography`, which is an optional extra to keep the base
install dependency-light:  pip install acidcat[crypto]

    from acidcat.core.containers import wiidisc
    disc = wiidisc.WiiDisc("game.iso")
    for f in disc.files():
        if f["path"].endswith(".brstm"):
            data = disc.read(f)
"""

import os
import struct

from acidcat.core.infra.source import open_input

_MAX_DEPTH = 64          # a real disc nests a handful deep

MAGIC = 0x5D1C9EA3                   # Wii disc magic word, at offset 0x18
_MAGIC_OFF = 0x18
# retail Wii common key -- a fixed, publicly documented console constant used to
# unwrap every retail title key. Present so acidcat can read a disc you already
# own; it decrypts nothing on its own without the disc's ticket.
_COMMON_KEY = bytes.fromhex("ebe42a225e8593e448d9c5457381aaf7")
_CLUSTER = 0x8000
_CLUSTER_HASH = 0x400
_CLUSTER_DATA = _CLUSTER - _CLUSTER_HASH             # 0x7C00 payload bytes per cluster


class WiiError(Exception):
    pass


def is_wii(path):
    """True if `path` is a Wii disc image (by the 0x18 magic word)."""
    try:
        with open_input(path) as f:
            f.seek(_MAGIC_OFF)
            return struct.unpack(">I", f.read(4))[0] == MAGIC
    except (OSError, struct.error):
        return False


def _aes_cbc(key, iv, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return d.update(data) + d.finalize()


def _require_crypto():
    try:
        import cryptography  # noqa: F401
    except ImportError:
        raise WiiError("encrypted Wii disc support needs the crypto extra: "
                       "pip install acidcat[crypto]")


class WiiDisc:
    """A decrypting reader over the first data partition of a Wii disc image.

    Holds the image open; call close() (or use as a context manager). Reads and
    decrypts clusters lazily with a small cache, never slurping the whole disc.
    """

    def __init__(self, path):
        _require_crypto()
        if not is_wii(path):
            raise WiiError("not a Wii disc image")
        self.path = path
        self._f = open_input(path)
        self._cache = {}
        self._load_partition()
        self._load_fst()

    def _load_partition(self):
        f = self._f
        f.seek(0x40000)
        npart, ptoff = struct.unpack(">II", f.read(8))
        # npart is an unchecked u32 from the disc. read(npart * 8) commits the
        # full length before discovering the file is shorter, so a 262 KB image
        # declaring 0xFFFFFFFF asked for 34.4 GB -- 130,000x the input. Clamp to
        # what the image can actually hold; a short table fails the loop below.
        fsize = os.fstat(f.fileno()).st_size
        base = ptoff << 2
        if base >= fsize:
            raise WiiError("partition table starts past the end of the image")
        npart = min(npart, max(0, (fsize - base) // 8))
        f.seek(base)
        table = f.read(npart * 8)
        part = None
        for i in range(npart):
            poff, ptype = struct.unpack_from(">II", table, i * 8)
            if ptype == 0:                               # DATA (the game); skip UPDATE/CHANNEL
                part = poff << 2
                break
        if part is None:
            raise WiiError("no data partition found")
        f.seek(part)
        ticket = f.read(0x2A4)
        iv = ticket[0x1DC:0x1E4] + b"\x00" * 8           # title id, zero-padded
        self._title_key = _aes_cbc(_COMMON_KEY, iv, ticket[0x1BF:0x1CF])
        f.seek(part + 0x2B8)
        data_off, _data_size = struct.unpack(">II", f.read(8))
        self._pstart = part + (data_off << 2)

    def _cluster(self, n):
        if n in self._cache:
            return self._cache[n]
        self._f.seek(self._pstart + n * _CLUSTER)
        raw = self._f.read(_CLUSTER)
        if len(raw) < _CLUSTER:
            return b""
        iv = _aes_cbc(self._title_key, b"\x00" * 16, raw[:_CLUSTER_HASH])[0x3D0:0x3E0]
        out = _aes_cbc(self._title_key, iv, raw[_CLUSTER_HASH:])
        if len(self._cache) > 64:                        # bounded LRU-ish cache
            self._cache.clear()
        self._cache[n] = out
        return out

    def _read_logical(self, off, size):
        out = bytearray()
        while size > 0:
            blk = self._cluster(off // _CLUSTER_DATA)
            w = off % _CLUSTER_DATA
            chunk = blk[w:w + size]
            if not chunk:
                break
            out += chunk
            off += len(chunk)
            size -= len(chunk)
        return bytes(out)

    def _load_fst(self):
        hdr = self._read_logical(0, 0x440)
        fst_off = struct.unpack_from(">I", hdr, 0x424)[0] << 2
        fst_size = struct.unpack_from(">I", hdr, 0x428)[0] << 2
        fst = self._read_logical(fst_off, fst_size)
        if len(fst) < 12:
            raise WiiError("unreadable FST")
        num = struct.unpack_from(">I", fst, 8)[0]        # root entry size = entry count
        strtab = num * 12
        if strtab > len(fst):
            raise WiiError("corrupt FST")

        def name(i):
            no = struct.unpack_from(">I", fst, i * 12)[0] & 0xFFFFFF
            end = fst.find(b"\x00", strtab + no)
            return fst[strtab + no:end].decode("latin-1", "replace")

        files = []

        # Same rule as gcm.descend, and for the same reason: this function is a
        # byte-for-byte copy of it, so it carried the identical hang (idx = size
        # where size <= idx never advances) and the identical unbounded
        # recursion. Only the gcm copy was reachable with a synthetic specimen
        # -- getting here needs bytes that AES-decrypt to a walkable FST -- so
        # this one was fixed by inspection alongside its twin rather than by
        # repro. If these two ever need to diverge, they should stop being
        # copies first.
        def descend(idx, prefix, end, depth):
            while idx < end and idx < num:
                typ = fst[idx * 12]
                off, size = struct.unpack_from(">II", fst, idx * 12 + 4)
                nm = name(idx)
                if typ == 1:                             # directory: size = index past children
                    child_end = min(size, num)
                    if child_end > idx + 1 and depth < _MAX_DEPTH:
                        descend(idx + 1, prefix + nm + "/", child_end, depth + 1)
                    idx = child_end if child_end > idx else idx + 1
                else:
                    files.append({"path": prefix + nm, "offset": off << 2, "size": size})
                    idx += 1

        descend(1, "", num, 0)
        self._files = files

    def files(self):
        """All files in the data partition: {path, offset, size} (logical)."""
        return list(self._files)

    def read(self, entry, limit=None):
        """Decrypt and return a file's bytes from its files() entry."""
        n = entry["size"] if limit is None else min(entry["size"], limit)
        return self._read_logical(entry["offset"], n)

    def close(self):
        try:
            self._f.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
