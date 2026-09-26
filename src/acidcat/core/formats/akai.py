"""Akai S1000/S3000 program data, as it travels over MIDI and as `.s3p`.

The S1000 does not have a program FILE format. It has a program DUMP: a series
of MIDI System Exclusive messages, each carrying one 150-byte block. A program
is one common block plus one block per keygroup.

`.s3p` is those messages written to disk with a length in front of each. That
is the whole format:

    "PSYSSS30"            8 bytes
    keygroup count        u32 BE
    repeat:
      length              u32 BE
      F0 47 cc ff 48 ... F7     `length` bytes

The first message is always function 0x07 (PDATA, the program common block) and
the rest are 0x09 (KDATA, one keygroup each). So the file is a transcript of a
sampler talking, and reading it means reading MIDI rather than reading a file
layout -- which is why two things in it look strange for a file.

FIRST: the payload is NIBBLE-SPLIT. SysEx data bytes may not have bit 7 set, so
every byte is sent as two bytes, low nibble first. 192 transmitted bytes are 96
real ones, and a reader that forgets this sees plausible-looking garbage rather
than an obvious failure.

SECOND: the blocks carry INTERNAL POINTERS. `KGRP1@` and `NXTKG@` are addresses
in the sampler's own memory, meaningless on disk, and they are decoded here
only so that nobody mistakes them for something to follow.

Verified against 1,670 real programs and 29,600 keygroups. The cross-check that
settles the layout is that the program block's own `GROUPS` count equals the
number of keygroup messages in the container, in 1,670 of 1,670 files -- two
unrelated parts of the file agreeing.

Field names and offsets follow the published S1000 exclusive documentation.
One widely-copied field table describes a larger keygroup than these files
carry; see ZONE_LEN below for why this reads the smaller one, and for what
would have to be true for the other to apply.
"""

import struct

MAGIC = b"PSYSSS30"
HEADER = 12                       # magic + the u32 keygroup count

SYSEX_START = 0xF0
SYSEX_END = 0xF7
AKAI_ID = 0x47                    # Akai's MIDI manufacturer id
S1000_ID = 0x48                   # the model byte every S1000 message carries
PDATA = 0x07                      # program common block
KDATA = 0x09                      # one keygroup block

# Bytes before the payload: F0, 47, channel, function, 48 -- then a selector
# whose width depends on which block this is (a program needs to say WHICH
# program; a keygroup also needs to say which keygroup).
FRAME_PREFIX = 5
PDATA_SELECTOR = 2
KDATA_SELECTOR = 3

BLOCK = 192                       # real bytes after un-nibbling
BLOCK_USED = 150                  # what the sampler actually fills
# Measured: bytes 150..191 are zero in all 1,670 program blocks. The transfer
# is padded; the block is not.

# The character set is the sampler's own, not ASCII. A name read as ASCII comes
# out as control codes.
CHARSET = "0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ#+-."
NAME_LEN = 12

# Velocity zones inside a keygroup, at 34, 58, 82 and 106.
#
# A widely-copied field table gives four zones of FIFTY bytes starting at 84,
# running the keygroup out to offset 252. That cannot be this block: these are
# 150 bytes (byte 1 says so, and 150..191 are zero in every file), and 34 plus
# four fifty-byte zones needs 234 before the trailing fields even start.
#
# The likeliest explanation is not that the table is wrong but that it belongs
# to a LATER generation -- the S1100/S3000 keygroup is the bigger one -- and
# has been merged with the S1000's in retellings. Note the same table lists a
# zone's own fields as 34 through 57, which is twenty-four bytes, so it
# disagrees with itself.
#
# For the files acidcat actually meets, twenty-four is measured, not chosen:
# 29,600 keygroups put their second sample name at 58. If an S1100-era file
# turns up it will need its own branch, and the block size is what would
# distinguish it.
ZONE_AT = 34
ZONE_LEN = 24
ZONES = 4

# What a zone does with the sample's own loop settings.
ZPLAY = ("as the sample says", "loop", "loop until release", "no loop",
         "play to the end")


def akai_name(raw):
    """Decode a 12-byte name. Codes outside the set become '?' rather than
    being dropped, so a wrong offset looks wrong instead of looking empty."""
    return "".join(CHARSET[c] if c < len(CHARSET) else "?" for c in raw).rstrip()


def unnibble(raw):
    """Undo SysEx nibble-splitting: two bytes in, one byte out, low nibble
    first. An odd count means the message is truncated, so the last half-byte
    is dropped rather than being guessed at.

    Both halves are MASKED to four bits. A real message cannot carry a byte
    with bit 7 set -- that is the whole reason for the split -- but a damaged
    or forged one can, and `raw[i] | (raw[i + 1] << 4)` on such a byte
    produces a number larger than a byte and raises. The mask is what a
    receiver does; `has_high_bits` below is how a reader finds out it
    happened, because silently masking a corrupt message and presenting the
    result as a program would be worse than either.
    """
    return bytes((raw[i] & 0x0F) | ((raw[i + 1] & 0x0F) << 4)
                 for i in range(0, len(raw) - 1, 2))


def has_high_bits(raw):
    """True if any byte of a nibble payload has bit 7 set, which no valid
    System Exclusive message does."""
    return any(b & 0x80 for b in raw)


def _frames(raw, filesize):
    """Yield (offset, length, function, payload) for each message. Stops at the
    first thing that is not a well-formed frame."""
    pos = HEADER
    while pos + 4 <= filesize:
        length = struct.unpack_from(">I", raw, pos)[0]
        body = pos + 4
        if length < FRAME_PREFIX + 2 or body + length > filesize:
            return
        blk = raw[body:body + length]
        if (blk[0] != SYSEX_START or blk[1] != AKAI_ID
                or blk[4] != S1000_ID or blk[-1] != SYSEX_END):
            return
        yield body, length, blk[3], blk
        pos = body + length


def looks_like_s3p(raw, filesize):
    """True if the bytes open an Akai program transcript.

    The magic alone would do, being eight specific bytes, but the first frame
    is checked too: a file that opens PSYSSS30 and then holds no Akai message
    is not something to hand to this parser.
    """
    if len(raw) < HEADER + 4 or raw[:len(MAGIC)] != MAGIC:
        return False
    for _off, _ln, fn, _blk in _frames(raw, filesize):
        return fn == PDATA
    return False


def parse_program(raw, filesize):
    """Read a .s3p. Returns a dict; `ok` says whether it holds together."""
    h = {"ok": False, "why": "", "declared_keygroups": 0, "program": None,
         "program_at": 0, "program_len": 0, "keygroups": [],
         "consumed": HEADER, "corrupt": 0}
    if len(raw) < HEADER or raw[:len(MAGIC)] != MAGIC:
        h["why"] = "no PSYSSS30 magic"
        h["code"] = "magic.mismatch"
        return h
    h["declared_keygroups"] = struct.unpack_from(">I", raw, len(MAGIC))[0]

    for off, length, fn, blk in _frames(raw, filesize):
        h["consumed"] = off + length
        sel = PDATA_SELECTOR if fn == PDATA else KDATA_SELECTOR
        payload = blk[FRAME_PREFIX + sel:-1]
        if has_high_bits(payload):
            h["corrupt"] += 1
        body = unnibble(payload)
        if fn == PDATA and h["program"] is None:
            h["program"] = body
            h["program_at"] = off
            h["program_len"] = length
        elif fn == KDATA:
            h["keygroups"].append((off, length, body))
        else:
            h["why"] = "unexpected function 0x%02X at offset %d" % (fn, off)
            h["code"] = "value.invalid"
            return h

    if h["program"] is None:
        h["why"] = "no program block"
        h["code"] = "required.missing"
        return h
    h["ok"] = True
    return h


def program_fields(body):
    """The program common block, as (name, value, note) triples.

    Only fields the corpus corroborates are given a meaning. `KGRP1@` is
    reported as an address precisely so nobody follows it.
    """
    return [
        ("ident", body[0], "1 = program common block"),
        ("kgrp1_addr", struct.unpack_from("<H", body, 1)[0],
         "the sampler's own memory address for the first keygroup; "
         "meaningless in a file"),
        ("name", akai_name(body[3:3 + NAME_LEN]), ""),
        ("program_number", body[15], "MIDI program 0-127"),
        ("midi_channel", "omni" if body[16] == 0xFF else body[16], ""),
        ("polyphony", body[17], "voices, 1-16"),
        ("play_low", body[19], _note_name(body[19])),
        ("play_high", body[20], _note_name(body[20])),
        ("output", "off" if body[22] == 0xFF else body[22], ""),
        ("level", body[23], "0-99"),
        ("pan", _signed(body[24]), "-50 to +50"),
        ("loudness", body[25], "0-99"),
        ("bend_range", body[39], "semitones"),
        ("keygroups", body[42], "the count the program declares"),
    ]


def keygroup_zones(body):
    """The four velocity zones of a keygroup, as dicts. Empty zones are kept:
    the zone number is its identity, the same way a slot number is in a PDX."""
    out = []
    for z in range(ZONES):
        at = ZONE_AT + z * ZONE_LEN
        if at + ZONE_LEN > len(body):
            break
        out.append({
            "zone": z + 1, "at": at,
            "sample": akai_name(body[at:at + NAME_LEN]),
            "low_velocity": body[at + 12],
            "high_velocity": body[at + 13],
            "loudness": _signed(body[at + 16]),
            "filter": _signed(body[at + 17]),
            "pan": _signed(body[at + 18]),
            "play": ZPLAY[body[at + 19]] if body[at + 19] < len(ZPLAY) else "",
        })
    return out


_NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _note_name(midi):
    """MIDI note as the sampler writes it: 24 is C0 on an Akai, not C1."""
    if not 0 <= midi <= 127:
        return ""
    return "%s%d" % (_NOTES[midi % 12], midi // 12 - 2)


def _signed(byte):
    """A parameter documented as +/-50. The sampler stores it two's complement
    in one byte, so 0xCE is -50 rather than 206."""
    return byte - 256 if byte > 127 else byte
