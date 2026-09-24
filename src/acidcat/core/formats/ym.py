"""YM: ST-Sound's register dumps of the YM2149 (Atari ST, Amstrad CPC,
ZX Spectrum), by Arnaud Carre (Leonard).

A YM file holds no code: it is what the original player routine wrote to
the sound chip, one frame of registers per tick. Almost every one is
wrapped in an LHA archive (level 0 header, -lh5-); inside is the image
this module reads.

  YM2! / YM3!  the 4-byte magic, then 14 registers per frame, stored
               interleaved (every frame's r0, then every frame's r1, ...);
               the frame count is what fits. YM2 is the Mad Max variant,
               whose players used built-in drum samples.
  YM3b         YM3 plus a 32-bit loop frame as the last four bytes.
  YM5! / YM6!  the magic, "LeOnArD!", then big-endian: frame count (4),
               song attributes (4; bit 0 = interleaved), digidrum count
               (2), chip clock in Hz (4), player rate in Hz (2), loop
               frame (4), size of extra data to skip (2). Then each
               digidrum as a 32-bit size and its 8-bit samples, then the
               song name, author and comment as NUL-terminated strings,
               then 16 registers per frame, then "End!". YM6 encodes
               special effects (SID voice, digidrum, sync buzzer) in the
               spare bits of r1, r3, r6, r8, r10, r14 and r15.

YM4! exists but no specimen of it has been seen, so its header is not
decoded. ST-Sound also wrote two types that are not register dumps: MIX1,
a remix of samples, and YMT1/YMT2, a sample tracker. Their layouts are not
published; they are recognised and refused.
"""

import struct

MAGICS = (b"YM2!", b"YM3!", b"YM3b", b"YM5!", b"YM6!")
LEONARD = b"LeOnArD!"
END = b"End!"
ATTR_INTERLEAVED = 0x01


OTHER_TYPES = {b"MIX1": "sample-remix type", b"YMT1": "sample-tracker type",
               b"YMT2": "sample-tracker type"}


def is_ym(head):
    return head[:4] in MAGICS or head[:4] == b"YM4!" or head[:4] in OTHER_TYPES


def _cstring(raw, at):
    end = raw.find(b"\x00", at)
    if end < 0:
        return None, at
    return raw[at:end].decode("latin-1"), end + 1


def parse(image):
    """A dict describing the image. `ok` False with `why` when it is not a
    YM this module reads."""
    magic = image[:4]
    r = {"ok": False, "why": "", "magic": magic.decode("latin-1", "replace"),
         "warnings": []}
    if magic in OTHER_TYPES:
        r["why"] = ("%s is ST-Sound's %s, not a register dump; its layout "
                    "is not published" % (magic.decode(), OTHER_TYPES[magic]))
        return r
    if magic == b"YM4!":
        r["why"] = "YM4 has no decoded header layout (no specimen seen)"
        return r
    if magic not in MAGICS:
        r["why"] = "no YM magic"
        return r
    if magic in (b"YM2!", b"YM3!", b"YM3b"):
        end = len(image) - (4 if magic == b"YM3b" else 0)
        if end < 4:
            r["why"] = "the file ends inside its magic"
            return r
        span = end - 4
        frames, rest = divmod(span, 14)
        r.update(ok=True, registers=14, interleaved=True, frames=frames,
                 data_at=4, data_len=frames * 14, clock=2000000, rate=50,
                 loop=0, drums=[], strings=[], end_at=None, extra_at=None,
                 attributes=None)
        if rest:
            r["warnings"].append("%d bytes after the last whole frame" % rest)
        if magic == b"YM3b":
            r["loop"] = struct.unpack_from(">I", image, end)[0]
            r["loop_at"] = end
        return r

    # YM5 / YM6
    if len(image) < 34:
        r["why"] = "the header is cut off"
        return r
    if image[4:12] != LEONARD:
        r["why"] = "no LeOnArD! check string"
        return r
    frames, attrs, ndrums, clock, rate, loop, extra = struct.unpack_from(
        ">IIHIHIH", image, 12)
    pos = 34 + extra
    drums = []
    for i in range(ndrums):
        if pos + 4 > len(image):
            r["why"] = "digidrum %d's size runs past the end" % i
            return r
        n = struct.unpack_from(">I", image, pos)[0]
        if pos + 4 + n > len(image):
            r["why"] = "digidrum %d (%d bytes) runs past the end" % (i, n)
            return r
        drums.append((pos, n))
        pos += 4 + n
    strings = []
    for name in ("title", "author", "comment"):
        s, nxt = _cstring(image, pos)
        if s is None:
            r["why"] = "the %s string has no terminator" % name
            return r
        strings.append((name, pos, nxt - pos, s))
        pos = nxt
    data_len = frames * 16
    r.update(ok=True, registers=16, interleaved=bool(attrs & ATTR_INTERLEAVED),
             frames=frames, attributes=attrs, clock=clock, rate=rate, loop=loop,
             drums=drums, strings=strings, data_at=pos, extra_at=34 if extra else None,
             extra_len=extra)
    if pos + data_len > len(image):
        have = (len(image) - pos) // 16
        r["warnings"].append("the header says %d frames; %d fit in the file"
                             % (frames, have))
        data_len = have * 16
    r["data_len"] = data_len
    end = pos + data_len
    if image[end:end + 4] == END:
        r["end_at"] = end
        if end + 4 != len(image):
            r["warnings"].append("%d bytes after the End! marker"
                                 % (len(image) - end - 4))
    else:
        r["end_at"] = None
        r["warnings"].append("no End! marker after the register data")
    if frames and loop >= frames:
        r["warnings"].append("the loop frame %d is past the last frame" % loop)
    return r
