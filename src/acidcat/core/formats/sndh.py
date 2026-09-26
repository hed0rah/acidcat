"""SNDH: Atari ST music as its own 68000 player, with a tag header.

An SNDH file is a program. Its first twelve bytes are three branches --
init (subtune number in d0), exit, and play, called at the replay rate --
and 'SNDH' at offset 12 starts a run of tags that ends at 'HDNS', after
which comes the player and its data. Almost every file is then packed with
Pack-Ice (core/codecs/ice.py); this module reads the unpacked image.

The tags, big-endian where binary (SNDH v2.2, with what real files do):

  TITL COMM RIPP CONV YEAR   text, NUL-terminated
  FLAG                        '~' and feature letters, NUL-terminated
  ##nn                        subtune count, two ASCII digits; real files
                              often follow it with a NUL
  !#nn  (#!nn in the spec)    the default subtune; files spell it '!#'
  TAnnn TBnnn TCnnn TDnnn     replay rate in Hz on MFP timer A-D, NUL-
  !Vnn                        terminated; !V is the vertical blank
  TIME                        one 16-bit length in seconds per subtune
  FRMS                        one 32-bit length in frames per subtune
                              (v2.2; replaces TIME; 0 = loops forever)
  !#SN  (#!SN in the spec)    one 16-bit offset per subtune to its name,
                              measured from the start of the tag, then
                              the NUL-terminated names; the table is
                              word-aligned, so a tag at an odd address
                              is followed by a pad byte
  HDNS                        end of the header, on an even address

Tags may be separated by NUL padding. HDNS came with SNDH v2, and a third
of real files predate it: their tags simply stop and the player's code
starts at the next even address. The reader stops at the first bytes that are not a tag and says
so, rather than reading code as text.
"""

import re
import struct
from acidcat.core.infra.findings import defect

TAG = b"SNDH"
TEXT_TAGS = {b"TITL": "title", b"COMM": "composer", b"RIPP": "ripper",
             b"CONV": "converter", b"YEAR": "year", b"FLAG": "flags"}
TIMERS = {b"TA": "timer A", b"TB": "timer B", b"TC": "timer C", b"TD": "timer D",
          b"!V": "vertical blank"}
FLAG_NAMES = {
    "a": "timer A", "b": "timer B", "c": "timer C", "d": "timer D",
    "e": "STe", "f": "SFX", "g": "digital", "h": "HBL", "j": "jingles",
    "k": "kills the system", "l": "LMC", "p": "AGA", "s": "DSP", "x": "SFX",
    "y": "YM2149", "B": "blitter", "C": "68020", "F": "filters", "S": "stereo",
    "0": "6.25 kHz DMA", "1": "12.5 kHz DMA", "2": "25 kHz DMA", "3": "50 kHz DMA",
    "4": "12.2 kHz DMA", "5": "14 kHz DMA", "6": "16.3 kHz DMA", "7": "19.6 kHz DMA",
    "8": "24.5 kHz DMA", "9": "32.8 kHz DMA", "A": "49.1 kHz DMA",
}
_TEXT_MAX = 512                              # a tag's text; real ones are short
_DIGITS = re.compile(rb"[0-9]+")


def is_sndh(image):
    return len(image) >= 16 and image[12:16] == TAG


def _entry(image, at):
    """The target of the branch at `at`: BRA.W, BRA.S or JMP (abs.L); or
    the slot itself when it is an RTS, an entry with nothing to do."""
    op = struct.unpack_from(">H", image, at)[0]
    if op == 0x6000:
        disp = struct.unpack_from(">h", image, at + 2)[0]
        return at + 2 + disp, "bra.w"
    if op & 0xFF00 == 0x6000:
        disp = op & 0xFF
        return at + 2 + (disp - 256 if disp & 0x80 else disp), "bra.s"
    if op == 0x4EF9:
        return struct.unpack_from(">I", image, at + 2)[0], "jmp"
    if op == 0x4E75:
        return at, "rts"                          # an entry that does nothing
    return None, "0x%04X" % op


def _cstr(image, at, limit):
    end = image.find(b"\x00", at, min(len(image), at + limit))
    if end < 0:
        return None, at
    return image[at:end].decode("latin-1"), end + 1


def parse(image):
    """A dict: entries, tags [(tag, offset, length, value)], header_end,
    hdns, subtunes, default, timer, times, frames, names, text, warnings.
    `ok` False with `why` when this is not an SNDH image."""
    r = {"ok": False, "why": "", "warnings": [], "tags": []}
    if not is_sndh(image):
        r["why"] = "no SNDH tag at offset 12"
        return r
    r["entries"] = [(name,) + _entry(image, at)
                    for name, at in (("init", 0), ("exit", 4), ("play", 8))]
    tags = r["tags"]
    text = {}
    subtunes = default = None
    timer = None
    times = frames = names = None
    early = False                                # a per-subtune table before ##
    pos = 16
    hdns = False
    n = len(image)
    while pos < n:
        while pos < n and image[pos] == 0:
            pos += 1
        if pos + 4 > n:
            break
        t4 = image[pos:pos + 4]
        t2 = image[pos:pos + 2]
        if t4 == b"HDNS":
            tags.append(("HDNS", pos, 4, ""))
            pos += 4
            hdns = True
            break
        if t4 in TEXT_TAGS:
            s, nxt = _cstr(image, pos + 4, _TEXT_MAX)
            if s is None:
                r["warnings"].append(defect("text.invalid",
                                            "the %s tag at 0x%X has no terminator"
                                            % (t4.decode(), pos)))
                break
            tags.append((t4.decode(), pos, nxt - pos, s))
            text[TEXT_TAGS[t4]] = s
            pos = nxt
            continue
        if t2 == b"##" and image[pos + 2:pos + 4].isdigit():
            if early:
                r["warnings"].append(defect("chunk.order",
                                            "a per-subtune table comes before ##; it was "
                                            "read as one subtune"))
            subtunes = int(image[pos + 2:pos + 4])
            tags.append(("##", pos, 4, subtunes))
            pos += 4
            continue
        if t2 in (b"!#", b"#!") and image[pos + 2:pos + 4].isdigit():
            default = int(image[pos + 2:pos + 4])
            tags.append((t2.decode(), pos, 4, default))
            pos += 4
            continue
        if t4 in (b"TIME", b"FRMS", b"!#SN", b"#!SN"):
            early = early or subtunes is None
            count = subtunes or 1
            if t4 == b"TIME":
                size = 2 * count
                if pos + 4 + size > n:
                    break
                times = list(struct.unpack_from(">%dH" % count, image, pos + 4))
                tags.append(("TIME", pos, 4 + size, times))
                pos += 4 + size
            elif t4 == b"FRMS":
                size = 4 * count
                if pos + 4 + size > n:
                    break
                frames = list(struct.unpack_from(">%dI" % count, image, pos + 4))
                tags.append(("FRMS", pos, 4 + size, frames))
                pos += 4 + size
            else:
                # the table is word-aligned: a tag on an odd address is
                # followed by a pad byte. Offsets still count from the tag.
                table = pos + 4
                if table & 1 and table < n and image[table] == 0:
                    table += 1
                if table + 2 * count > n:
                    break
                offs = struct.unpack_from(">%dH" % count, image, table)
                names, end = [], table + 2 * count
                for o in offs:
                    s, nxt = _cstr(image, pos + o, _TEXT_MAX)
                    if s is None:
                        r["warnings"].append(defect("reference.unresolved",
                                                    "a subtune name offset 0x%X in %s points "
                                                    "at no string" % (o, t4.decode())))
                        names.append(None)
                        continue
                    names.append(s)
                    end = max(end, nxt)
                tags.append((t4.decode(), pos, end - pos, names))
                pos = end
            continue
        if t2 in TIMERS:
            m = _DIGITS.match(image, pos + 2)
            if m and m.end() < n and image[m.end()] == 0:
                hz = int(m.group())
                timer = (TIMERS[t2], hz)
                tags.append((t2.decode(), pos, m.end() + 1 - pos, hz))
                pos = m.end() + 1
                continue
        break                                     # not a tag: the header is over
    header_end = pos
    if not hdns:
        header_end += header_end & 1              # the code starts on an even address
    if default is not None and subtunes and not 1 <= default <= subtunes:
        r["warnings"].append(defect("value.invalid",
                                    "the default subtune %d is outside 1-%d"
                                    % (default, subtunes)))
    for name, target, kind in r["entries"]:
        if target is None:
            r["warnings"].append(defect("value.invalid",
                                        "the %s entry is not a branch (%s)" % (name, kind)))
        elif not 0 <= target < n:
            r["warnings"].append(defect("pointer.dangling",
                                        "the %s entry branches to 0x%X, outside the image"
                                        % (name, target)))
    r.update(ok=True, header_end=min(header_end, n), hdns=hdns, subtunes=subtunes or 1,
             subtunes_tagged=subtunes is not None, default=default, timer=timer,
             times=times, frames=frames, names=names, text=text)
    return r
