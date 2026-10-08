"""PSF: Neill Corlett's container for emulated console music, eight machines in.

Made in 2002 for PlayStation dumps, then adopted by everyone who needed to
ship "a program and the RAM it runs in" for some other chip. The version byte
says which machine; everything else about the container is the same:

    0x00   "PSF"
    0x03   version         which machine, see VERSIONS
    0x04   reserved size   u32 LE
    0x08   program size    u32 LE, the COMPRESSED size
    0x0C   CRC32           of the compressed program
    0x10   reserved area   platform-specific, usually empty
           program         zlib
           "[TAG]"         optional, then key=value lines, LF-separated

The CRC is the gift. Most formats give a reader nothing to check; PSF checks
its own program, so "intact" is a fact the walker can state. 435 of 435 real
GSF files agree with their checksum.

MINI FILES AND LIBRARIES. A soundtrack shares one engine and one sample set
across every track, so the format splits them: the shared part goes in a
.psflib (.gsflib, .ssflib...) and each track is a .minipsf whose program is a
few bytes patched over the library's, and whose `_lib` tag names the library.
A player loads the lib, then the mini on top. The walker names both halves
and does not pretend a mini is a whole tune.

WHAT THE PROGRAM IS depends on the machine, and only the platforms whose
program header is documented are decoded past "a zlib blob of N bytes":

    2SF (0x24)   8 bytes: load offset, length -- then DS ROM bytes. The
                 reserved area, when used, is a "SAVE" block: zlib size,
                 CRC32, zlib; inflated it is (offset, length, data) again,
                 a patch into the emulator's save state. A mini's program
                 is two bytes (the song number) and its SAVE patch four.
    GSF (0x22)   12 bytes: entry point, load offset, length -- then a GBA
                 ROM image of exactly that length. Verified on 509 files,
                 zero mismatches. A mini's ROM is one or two bytes: the song
                 number, patched into the library at `offset`.

Everything else is reported as what it verifiably is: a compressed program
of a stated size for a stated machine, with its checksum checked. That is
honest and it is not nothing.

The specification has fallen off the internet -- the surviving wiki pages
describe it without offsets -- so the layout was taken from a reader that
plays the files, svenstucki's psf-dump, read as an oracle and not copied
(it carries no license). When the spec is gone, the code that plays the
files is the spec.
"""

import struct
import zlib

MAGIC = b"PSF"
HEADER = 16
TAG_MARK = b"[TAG]"

VERSIONS = {
    0x01: ("PSF1", "PlayStation"),
    0x02: ("PSF2", "PlayStation 2"),
    0x11: ("SSF", "Sega Saturn"),
    0x12: ("DSF", "Sega Dreamcast"),
    0x13: ("GSF/MD", "Sega Mega Drive"),      # rare; psf-dump lists it
    0x21: ("USF", "Nintendo 64"),
    0x22: ("GSF", "Game Boy Advance"),
    0x23: ("SNSF", "Super Nintendo"),
    0x24: ("2SF", "Nintendo DS"),             # 31,118 on Modland; found by a census
    0x25: ("NCSF", "Nintendo DS"),            # Nitro Composer; listed, not yet seen
    0x41: ("QSF", "Capcom QSound"),
}

# The GBA program header inside a GSF, after inflation.
GSF_PROGRAM_HEADER = 12
# The DS program header inside a 2SF: the same without the entry point,
# (offset, size) then the ROM bytes. 3,283 of 3,283 real files consistent.
TWOSF_PROGRAM_HEADER = 8
# A 2SF's reserved area, when present, is a save-state patch: "SAVE", the
# zlib size, its CRC32, the zlib stream; inflated, (offset, size, data)
# again. 219 of 219 real files, all inflate, 214 CRCs match.
SAVE_MARK = b"SAVE"
SAVE_HEADER = 12

# A program that inflates past this is not one any of these machines can
# address: 512 MB is the largest DS cartridge. Inflation is streamed and
# counted, never held, so the cap bounds time and not memory.
INFLATE_CAP = 512 * 1024 * 1024
_INFLATE_STEP = 1024 * 1024
# Tag lines a reader will decode. Real files carry a dozen; the bound is
# for a crafted tag block, and the count read is reported regardless.
TAG_LINE_CAP = 256


def is_psf(head):
    return len(head) >= 4 and head[:3] == MAGIC and head[3] in VERSIONS


def parse(raw, filesize):
    """Read the container. Never raises; `ok` says whether it holds."""
    h = {"ok": False, "why": "", "version": None, "platform": None,
         "reserved_at": HEADER, "reserved_size": 0,
         "program_at": 0, "program_size": 0, "crc": 0, "crc_ok": None,
         "tags_at": None, "tags": {}, "libs": [], "tag_lines": 0,
         "inflated_size": None, "gsf": None, "save": None}
    if len(raw) < HEADER or raw[:3] != MAGIC:
        h["why"] = "no PSF magic"
        h["code"] = "magic.mismatch"
        return h
    v = raw[3]
    if v not in VERSIONS:
        h["why"] = "version byte 0x%02X names no known machine" % v
        h["code"] = "value.invalid"
        return h
    h["version"] = v
    h["platform"] = VERSIONS[v]
    rs, cs, crc = struct.unpack_from("<III", raw, 4)
    h["reserved_size"], h["program_size"], h["crc"] = rs, cs, crc
    h["program_at"] = HEADER + rs
    if h["program_at"] + cs > filesize:
        h["why"] = ("reserved %d + program %d bytes run past the end of the "
                    "file" % (rs, cs))
        h["code"] = "size.overrun"
        return h

    prog = raw[h["program_at"]:h["program_at"] + cs]
    if cs and len(prog) == cs:
        h["crc_ok"] = (zlib.crc32(prog) & 0xFFFFFFFF) == crc
        h["inflated_size"], h["gsf"] = _inflate(prog, v)
    if v == 0x24 and rs >= SAVE_HEADER:
        h["save"] = _save_block(raw[HEADER:HEADER + rs])

    tags_at = h["program_at"] + cs
    h["tags_at"] = tags_at
    if raw[tags_at:tags_at + len(TAG_MARK)] == TAG_MARK:
        h["tags"], h["libs"], h["tag_lines"] = _tags(raw[tags_at + len(TAG_MARK):])
    h["ok"] = True
    return h


def _inflate(prog, version):
    """Inflate the program, bounded, and decode its header where known.

    Returns (inflated size or None, gsf dict or None). The whole ROM is not
    kept: a GBA library inflates to 16 MB and the walker needs its header.
    """
    size, out = _inflate_counting(prog)
    if size is None:
        return None, None
    gsf = None
    if version == 0x22 and size >= GSF_PROGRAM_HEADER:
        entry, offset, length = struct.unpack_from("<III", out, 0)
        gsf = {"entry": entry, "offset": offset, "length": length,
               "rom_bytes": size - GSF_PROGRAM_HEADER,
               "consistent": size == GSF_PROGRAM_HEADER + length}
    elif version == 0x24 and size >= TWOSF_PROGRAM_HEADER:
        offset, length = struct.unpack_from("<II", out, 0)
        gsf = {"entry": None, "offset": offset, "length": length,
               "rom_bytes": size - TWOSF_PROGRAM_HEADER,
               "consistent": size == TWOSF_PROGRAM_HEADER + length}
    return size, gsf


def _inflate_counting(z, keep=16):
    """Inflate a stream a megabyte at a time, keeping only the first `keep`
    bytes and the total. A DS library inflates to 64 MB and a cartridge can
    be 512; nothing that large is ever held. Returns (size, head), or
    (None, b"") if the stream is not zlib or runs past INFLATE_CAP."""
    d = zlib.decompressobj()
    total, head = 0, b""
    try:
        piece = d.decompress(z, _INFLATE_STEP)
        while piece:
            if len(head) < keep:
                head += piece[:keep - len(head)]
            total += len(piece)
            if total > INFLATE_CAP:
                return None, b""
            piece = d.decompress(d.unconsumed_tail, _INFLATE_STEP)
        if not d.eof:
            piece = d.flush()
            total += len(piece)
            if len(head) < keep:
                head += piece[:keep - len(head)]
    except zlib.error:
        return None, b""
    return total, head


def _save_block(blk):
    """The 2SF reserved area. Returns a dict, `ok` False if it is not a
    SAVE block. Inflated content is bounded like the program."""
    sv = {"ok": False, "size": 0, "crc_ok": None, "inflated_size": None,
          "offset": None, "length": None, "consistent": None}
    if blk[:4] != SAVE_MARK:
        return sv
    size, crc = struct.unpack_from("<II", blk, 4)
    z = blk[SAVE_HEADER:SAVE_HEADER + size]
    sv["ok"] = True
    sv["size"] = size
    sv["fits"] = SAVE_HEADER + size == len(blk)
    sv["crc_ok"] = len(z) == size and (zlib.crc32(z) & 0xFFFFFFFF) == crc
    size, out = _inflate_counting(z)
    if size is None:
        return sv
    sv["inflated_size"] = size
    if size >= 8:
        sv["offset"], sv["length"] = struct.unpack_from("<II", out, 0)
        sv["consistent"] = size == 8 + sv["length"]
    return sv


def _tags(blob):
    """key=value lines, LF-separated. A key that repeats is a multi-line
    value and is joined. `_lib`, `_lib2`... are collected in order."""
    tags = {}
    libs = []
    n = 0
    for line in blob.split(b"\n"):
        if n >= TAG_LINE_CAP:
            break
        line = line.strip(b"\r ")
        if not line or b"=" not in line:
            continue
        n += 1
        k, v = line.split(b"=", 1)
        key = k.strip().decode("latin-1").lower()
        val = v.strip().decode("utf-8", "replace")
        if key.startswith("_lib"):
            libs.append(val)
            continue
        if key in tags:
            tags[key] += "\n" + val
        else:
            tags[key] = val
    return tags, libs, n
