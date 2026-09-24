"""LHA archives, as far as YM files need them: the member header and the
-lh5- method.

A member is a header and a compressed body. A level-0 header starts with
a one-byte header size and a one-byte checksum of the header; the method
id is five ASCII bytes ('-lh5-'); then the compressed size, the original
size, a DOS timestamp, an attribute byte and the level, all
little-endian; then the file name (length-prefixed) and a CRC-16 of the
original data. Levels 1 and 2 add extended headers and are refused: YM
files do not use them.

-lh5- is LZSS over an 8 KB window with static Huffman codes sent per
block. A block starts with its symbol count (16 bits), then three code
length tables: a small one (19 symbols, 5-bit count) that codes the
literal/length table's lengths, the literal/length table itself (510
symbols: 256 bytes and match lengths 3-256, 9-bit count), and the
position table (14 symbols, 4-bit count). A count of zero means a single
symbol, sent next, with no bits spent per use. Codes are canonical: by
length, then by symbol. A position symbol p > 1 is followed by p-1 raw
bits and means a distance of 2**(p-1) plus them; the copy starts that
distance plus one back. Bits are read most significant first.

The CRC is CRC-16/ARC (reflected 0x8005, initial 0), and an unpacked body
that does not match it is not returned.
"""

import struct

_NC = 510                                   # 256 literals + 254 lengths
_NT = 19
_NP = 14
_CBIT, _TBIT, _PBIT = 9, 5, 4
_WINDOW = 1 << 13


class LhaError(ValueError):
    """Not an LHA member this reader handles, or a damaged one."""


# -- the member header -------------------------------------------------------

def is_lha(raw, at=0):
    return (len(raw) >= at + 7 and raw[at + 2:at + 4] == b"-l"
            and raw[at + 6:at + 7] == b"-")


def parse_header(raw, at=0):
    """The member at `at`: a dict with method, sizes, level, name, crc,
    the header's own length and where the compressed body starts, and
    `checksum_ok` for levels 0 and 1."""
    if not is_lha(raw, at):
        raise LhaError("no LHA method id")
    if len(raw) < at + 22:
        raise LhaError("the header is cut off")
    method = raw[at + 2:at + 7].decode("latin-1")
    packed, size, stamp = struct.unpack_from("<III", raw, at + 7)
    attr, level = raw[at + 19], raw[at + 20]
    h = {"method": method, "packed": packed, "size": size, "stamp": stamp,
         "attr": attr, "level": level}
    if level != 0:
        raise LhaError("header level %d is not read" % level)
    hsize = raw[at]
    end = at + 2 + hsize
    if len(raw) < end:
        raise LhaError("the header is cut off")
    h["checksum_ok"] = (sum(raw[at + 2:end]) & 0xFF) == raw[at + 1]
    nlen = raw[at + 21]
    if at + 22 + nlen + 2 > end:
        raise LhaError("the name runs past the header")
    h["name"] = raw[at + 22:at + 22 + nlen].decode("latin-1")
    h["crc"] = struct.unpack_from("<H", raw, at + 22 + nlen)[0]
    h["header_len"] = end - at
    h["body"] = end
    return h


# -- CRC-16/ARC ------------------------------------------------------------

def _crc_table():
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
        t.append(c)
    return t


_CRC = _crc_table()


def crc16(data):
    c = 0
    for b in data:
        c = (c >> 8) ^ _CRC[(c ^ b) & 0xFF]
    return c


# -- -lh5- -------------------------------------------------------------------

class _Reader(object):
    """MSB-first bits over the compressed body; zeros past its end, which
    is what the format's own decoder reads there."""

    __slots__ = ("data", "pos", "buf", "n")

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.buf = 0                            # bits not yet consumed
        self.n = 0                              # how many

    def _fill(self, want):
        while self.n < want:
            b = self.data[self.pos] if self.pos < len(self.data) else 0
            self.pos += 1
            self.buf = (self.buf << 8) | b
            self.n += 8

    def peek(self, k):
        self._fill(k)
        return (self.buf >> (self.n - k)) & ((1 << k) - 1)

    def skip(self, k):
        self._fill(k)
        self.n -= k
        self.buf &= (1 << self.n) - 1

    def get(self, k):
        v = self.peek(k)
        self.skip(k)
        return v


def _table(lengths):
    """A lookup table for canonical codes of these lengths: (bits, table)
    where table[peek(bits)] is (symbol, length). Raises if the lengths do
    not form a complete code."""
    maxlen = max(lengths)
    count = [0] * (maxlen + 1)
    for n in lengths:
        if n:
            count[n] += 1
    code, start = 0, [0] * (maxlen + 2)
    for n in range(1, maxlen + 1):
        start[n] = code
        code = (code + count[n]) << 1
    if code >> 1 != 1 << maxlen:
        raise LhaError("a Huffman table's code lengths do not form a complete code")
    table = [None] * (1 << maxlen)
    nxt = start[:]
    for sym, n in enumerate(lengths):
        if not n:
            continue
        c = nxt[n]
        nxt[n] += 1
        lo = c << (maxlen - n)
        entry = (sym, n)
        for k in range(lo, lo + (1 << (maxlen - n))):
            table[k] = entry
    return maxlen, table


class _Code(object):
    """One block's decoder for one alphabet: a single symbol or a table."""

    __slots__ = ("single", "bits", "table")

    def __init__(self, lengths=None, single=None):
        self.single = single
        if single is None:
            self.bits, self.table = _table(lengths)

    def decode(self, r):
        if self.single is not None:
            return self.single
        sym, n = self.table[r.peek(self.bits)]
        r.skip(n)
        return sym


def _read_pt(r, nn, nbit, special):
    n = r.get(nbit)
    if n == 0:
        return _Code(single=r.get(nbit))
    if n > nn:
        raise LhaError("a table claims %d of %d symbols" % (n, nn))
    lengths = []
    while len(lengths) < n:
        c = r.peek(3)
        if c == 7:
            r.skip(3)
            while r.get(1):
                c += 1
                if c > 16:
                    raise LhaError("a code length over 16")
        else:
            r.skip(3)
        lengths.append(c)
        if len(lengths) == special:
            lengths.extend([0] * r.get(2))
    lengths = (lengths + [0] * nn)[:nn]
    return _Code(lengths)


def _read_c(r, pt):
    n = r.get(_CBIT)
    if n == 0:
        return _Code(single=r.get(_CBIT))
    if n > _NC:
        raise LhaError("the literal table claims %d symbols" % n)
    lengths = []
    while len(lengths) < n:
        c = pt.decode(r)
        if c == 0:
            lengths.append(0)
        elif c == 1:
            lengths.extend([0] * (r.get(4) + 3))
        elif c == 2:
            lengths.extend([0] * (r.get(_CBIT) + 20))
        else:
            lengths.append(c - 2)
    if len(lengths) > _NC:
        raise LhaError("the literal table runs past %d symbols" % _NC)
    lengths = (lengths + [0] * _NC)[:_NC]
    return _Code(lengths)


def unpack_lh5(body, size, cap):
    """Decode `size` bytes from an -lh5- body. `cap` bounds `size`."""
    if size > cap:
        raise LhaError("the header claims %d bytes; the cap is %d" % (size, cap))
    r = _Reader(body)
    out = bytearray()
    left = 0
    c_code = p_code = None
    while len(out) < size:
        if left == 0:
            left = r.get(16)
            if left == 0:
                raise LhaError("an empty block")
            pt = _read_pt(r, _NT, _TBIT, 3)
            c_code = _read_c(r, pt)
            p_code = _read_pt(r, _NP, _PBIT, -1)
        if r.pos > len(body) + 4:
            raise LhaError("the body ends before %d bytes are decoded" % size)
        left -= 1
        c = c_code.decode(r)
        if c < 256:
            out.append(c)
            continue
        length = c - 253
        p = p_code.decode(r)
        if p > 1:
            p = (1 << (p - 1)) + r.get(p - 1)
        src = len(out) - p - 1
        if src < 0:
            raise LhaError("a match reaches before the start of the data")
        for k in range(length):
            out.append(out[src + k])
    del out[size:]
    return bytes(out)


def unpack(raw, at=0, cap=1 << 26):
    """(header, data) for the member at `at`. Stored (-lh0-) and -lh5-
    bodies are decoded; the CRC must match."""
    h = parse_header(raw, at)
    body = raw[h["body"]:h["body"] + h["packed"]]
    if len(body) < h["packed"]:
        raise LhaError("the body is cut off: %d of %d bytes"
                       % (len(body), h["packed"]))
    if h["method"] == "-lh0-":
        if h["size"] != h["packed"]:
            raise LhaError("a stored member whose sizes differ")
        data = bytes(body)
    elif h["method"] == "-lh5-":
        data = unpack_lh5(body, h["size"], cap)
    else:
        raise LhaError("method %s is not decoded" % h["method"])
    if crc16(data) != h["crc"]:
        raise LhaError("the CRC does not match")
    return h, data
