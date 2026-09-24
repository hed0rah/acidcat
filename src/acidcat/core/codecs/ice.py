"""Pack-Ice: the Atari ST packer most SNDH files are shipped in.

A packed file is a 12-byte header -- the magic, the packed length (header
included) and the unpacked length, big-endian -- and a bit stream that is
read backwards from the end of the file while the output is written
backwards from the end of the unpacked image. Bits come from bytes taken
from the end, top bit first, with a marker bit in each byte telling the
reader when it is spent.

The stream alternates literal runs and back-references into what has
already been written (which, going backwards, is what comes later in the
image). Lengths and offsets are coded with small prefix tables:

  literal run   0            none
                10           1 byte
                11 + fields  fields of 2, 2, 3, 8 and 15 bits for 2-4,
                             5-7, 8-14, 15-269 and 270 or more bytes;
                             a field of all ones steps to the next
  string length 0 / 10 / 110+1 / 1110+2 / 1111+10 bits: 2, 3, 4-5, 6-9,
                10-1033
  offset        a 2-byte string: 0+6 bits or 1+9 bits
                otherwise 0+8 bits, 10+5 bits, 11+12 bits

After the image is complete, one more bit says whether the packer also
reordered a 32000-byte low-resolution picture from bitplanes into chunky
nibbles for better packing; music files do not use it, and it is refused
rather than guessed at.

Two magics exist: 'ICE!' (versions 2.3 and 2.4), decoded here, and 'Ice!'
(the earlier 2.x), which is recognised and refused: no specimen has been
checked against it, and a stream that decodes to the right length by the
wrong rules would still be wrong.
"""

import struct

MAGICS = (b"ICE!", b"Ice!")
HEADER = 12


class IceError(ValueError):
    """The stream does not decode to what its header says."""


def is_ice(raw):
    return len(raw) >= HEADER and raw[:4] in MAGICS


def header(raw):
    """(magic, packed_len, unpacked_len) or None."""
    if not is_ice(raw):
        return None
    packed, unpacked = struct.unpack_from(">II", raw, 4)
    return raw[:4].decode("ascii"), packed, unpacked


class _Bits(object):
    """The backwards bit reader."""

    __slots__ = ("src", "pos", "cur", "lo")

    def __init__(self, src, end, lo):
        self.src = src
        self.lo = lo                            # first byte of the stream
        self.pos = end
        self.cur = self._byte()                 # holds a marker bit

    def _byte(self):
        self.pos -= 1
        if self.pos < self.lo:
            raise IceError("the bit stream runs out before the image is full")
        return self.src[self.pos]

    def bit(self):
        c = self.cur << 1
        carry = c >> 8
        c &= 0xFF
        if not c:                               # the marker left: next byte
            n = self._byte()
            c = (n << 1) | carry
            carry = c >> 8
            c &= 0xFF
        self.cur = c
        return carry

    def bits(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self.bit()
        return v


# literal-run extensions: (field width, all-ones value, base count - 1)
_LITERAL = ((2, 3, 1), (2, 3, 4), (3, 7, 7), (8, 255, 14), (15, 0x7FFF, 269))


def unpack(raw, cap):
    """The unpacked image. `cap` bounds the unpacked length a header may
    claim. Raises IceError when the stream does not decode exactly."""
    h = header(raw)
    if h is None:
        raise IceError("no ICE! magic")
    magic, packed, size = h
    if magic != "ICE!":
        raise IceError("'%s' is the earlier Pack-Ice format, which is not decoded" % magic)
    if packed < HEADER + 1 or packed > len(raw):
        raise IceError("the packed length %d does not fit the %d-byte file"
                       % (packed, len(raw)))
    if size > cap:
        raise IceError("the header claims %d unpacked bytes; the cap is %d"
                       % (size, cap))
    out = bytearray(size)
    b = _Bits(raw, packed, HEADER)
    bit, bits = b.bit, b.bits
    w = size                                    # write position, moving down
    while True:
        # a literal run, possibly empty
        if bit():
            n = 1
            if bit():
                for width, full, base in _LITERAL:
                    v = bits(width)
                    if v != full:
                        break
                n = v + base + 1
            if n > w:
                raise IceError("a literal run of %d bytes overruns the image" % n)
            for _ in range(n):
                w -= 1
                out[w] = b._byte()
        if w <= 0:
            break
        # a string: length, then offset
        k = 0
        while k < 4 and bit():
            k += 1
        if k == 0:
            extra = 0
        elif k == 1:
            extra = 1
        elif k == 2:
            extra = 2 + bits(1)
        elif k == 3:
            extra = 4 + bits(2)
        else:
            extra = 8 + bits(10)
        if extra == 0:
            off = bits(9) + 0x3F if bit() else bits(6) - 1
        else:
            j = 0
            while j < 2 and bit():
                j += 1
            if j == 0:
                off = bits(8) + 0x1F
            elif j == 1:
                off = bits(5) - 1
                if off < 0:
                    off -= extra
            else:
                off = bits(12) + 0x11F
        n = extra + 2
        src = w + n + off
        if n > w or src > size or src - n < 0:
            raise IceError("a string at %d reaches outside the image" % w)
        for _ in range(n):
            src -= 1
            w -= 1
            out[w] = out[src]
        # the end is only tested after a literal phase, so a string that
        # fills the image is followed by one (empty) literal flag
    if bit():
        raise IceError("picture mode (a bitplane reorder after unpacking) "
                       "is not supported")
    return bytes(out)
