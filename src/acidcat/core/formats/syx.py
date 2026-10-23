"""MIDI System Exclusive dumps (.syx), and the Yamaha DX7 voice data inside them.

A .syx file is not a file format. It is the bytes a synthesizer sends over MIDI
when asked for its memory, written to disk as they arrived: one or more
System Exclusive messages, each

    F0  manufacturer id  data bytes ...  F7

with every data byte below 0x80, because a byte with bit 7 set is a MIDI status
byte and ends the message. The manufacturer id is one byte (0x43 Yamaha, 0x41
Roland, ...) or 00 followed by two more; 7D is non-commercial, 7E and 7F are the
universal messages every device reads.

Yamaha's bulk dump puts a header after the id:

    F0 43 0n ff mm ll  data[count]  checksum  F7

    0n      high nibble the sub-status (0 = bulk dump), low nibble the device
            number (the MIDI channel, shown as 1-16)
    ff      what the data is: 0 one DX7 voice, 9 thirty-two DX7 voices
    mm ll   the byte count, seven bits in each: count = mm << 7 | ll
    sum     two's complement of the seven-bit sum of the data bytes, so data
            plus checksum sums to zero modulo 128

THE DX7 KEEPS ONE VOICE IN TWO SHAPES. A single-voice dump (format 0, 155
bytes) is the edit buffer with one byte per parameter (VCED). The 32-voice dump
(format 9, 4096 bytes) is the cartridge image: 32 voices of 128 bytes (VMEM),
where parameters that need only a few bits share a byte. Both list the six
operators OP6 first, then the voice-wide parameters, then a 10-character name.

    VCED (155)                         VMEM (128)
    per operator, 21 bytes             per operator, 17 bytes
      0-3   EG rates 1-4                 0-7  the same eight EG bytes
      4-7   EG levels 1-4                8-10 break point, left and right depth
      8     level scaling break point    11   bits 3-2 right curve, 1-0 left
      9,10  left, right depth            12   bits 6-3 detune, 2-0 rate scaling
      11,12 left, right curve            13   bits 4-2 velocity sens, 1-0 AMS
      13    rate scaling                 14   output level
      14    amp mod sensitivity          15   bits 5-1 coarse, bit 0 mode
      15    key velocity sensitivity     16   fine
      16    output level
      17    mode (0 ratio, 1 fixed)
      18    frequency coarse
      19    frequency fine
      20    detune (7 is centre)
    126-133 pitch EG rates, levels       102-109 the same
    134     algorithm (0-31)             110  bits 4-0 algorithm
    135     feedback                     111  bit 3 osc key sync, 2-0 feedback
    136     osc key sync
    137-140 LFO speed, delay, PMD, AMD   112-115 the same
    141     LFO key sync                 116  bits 6-4 pitch mod sens,
    142     LFO wave                          3-1 LFO wave, bit 0 LFO sync
    143     pitch mod sensitivity
    144     transpose (24 is C3)         117  transpose
    145-154 name                         118-127 name

Spec: the MIDI data format section of the Yamaha DX7 owner's manual. Checked on
9,648 real dumps (9,512 banks, 136 single voices, all from one converter): every
header, byte count and checksum agrees with this layout, and the factory INIT
VOICE decodes to its published values in both shapes (detune 7 in byte 12 bits
6-3, coarse 1 in byte 15, OKS in byte 111 bit 3, LFO sync and a PMS of 3 in
byte 116). The bit splits of bytes 11 and 13 are zero in every voice that
corpus holds, so for those two the manual is the only evidence.
"""

import re

SOX = 0xF0
EOX = 0xF7

YAMAHA = 0x43
BULK_DUMP = 0                       # the sub-status nibble of a bulk dump
YAMAHA_HEADER = 6                   # F0 43 0n ff mm ll
VCED = 0
VMEM = 9
VCED_LEN = 155
VMEM_LEN = 4096
VOICES = 32
PACKED_VOICE = 128
VCED_OP = 21
VMEM_OP = 17
OPS = 6
NAME_LEN = 10
VCED_NAME = 145
VMEM_NAME = 118

# Yamaha bulk-dump formats named by number. Only 0 and 9 are decoded.
YAMAHA_FORMATS = {
    VCED: "DX7 single voice (VCED)",
    VMEM: "DX7 32-voice bank (VMEM)",
    3: "4-operator single voice (VCED, DX21/DX27/DX100/TX81Z)",
    4: "4-operator 32-voice bank (VMEM, DX21/DX27/DX100/TX81Z)",
    0x7E: "universal bulk dump (ASCII-classified, DX7II/TX81Z era)",
}

# MIDI manufacturer ids, the one-byte ones a synth library meets
MANUFACTURERS = {
    0x01: "Sequential Circuits", 0x04: "Moog", 0x06: "Lexicon", 0x07: "Kurzweil",
    0x0F: "Ensoniq", 0x10: "Oberheim", 0x18: "E-mu", 0x40: "Kawai",
    0x41: "Roland", 0x42: "Korg", 0x43: "Yamaha", 0x44: "Casio", 0x47: "Akai",
    0x7D: "non-commercial", 0x7E: "universal non-realtime",
    0x7F: "universal realtime",
}

# operator parameters: name -> (offset in a VCED operator, (byte, lsb, width) in
# a VMEM operator)
OP_PARAMS = {
    "r1": (0, (0, 0, 7)), "r2": (1, (1, 0, 7)), "r3": (2, (2, 0, 7)), "r4": (3, (3, 0, 7)),
    "l1": (4, (4, 0, 7)), "l2": (5, (5, 0, 7)), "l3": (6, (6, 0, 7)), "l4": (7, (7, 0, 7)),
    "break_point": (8, (8, 0, 7)), "left_depth": (9, (9, 0, 7)),
    "right_depth": (10, (10, 0, 7)), "left_curve": (11, (11, 0, 2)),
    "right_curve": (12, (11, 2, 2)), "rate_scaling": (13, (12, 0, 3)),
    "amp_mod_sens": (14, (13, 0, 2)), "velocity_sens": (15, (13, 2, 3)),
    "level": (16, (14, 0, 7)), "mode": (17, (15, 0, 1)), "coarse": (18, (15, 1, 5)),
    "fine": (19, (16, 0, 7)), "detune": (20, (12, 3, 4)),
}

# voice-wide parameters: name -> (offset in VCED, (byte, lsb, width) in VMEM)
VOICE_PARAMS = {
    "pr1": (126, (102, 0, 7)), "pr2": (127, (103, 0, 7)),
    "pr3": (128, (104, 0, 7)), "pr4": (129, (105, 0, 7)),
    "pl1": (130, (106, 0, 7)), "pl2": (131, (107, 0, 7)),
    "pl3": (132, (108, 0, 7)), "pl4": (133, (109, 0, 7)),
    "algorithm": (134, (110, 0, 5)), "feedback": (135, (111, 0, 3)),
    "osc_key_sync": (136, (111, 3, 1)), "lfo_speed": (137, (112, 0, 7)),
    "lfo_delay": (138, (113, 0, 7)), "lfo_pitch_depth": (139, (114, 0, 7)),
    "lfo_amp_depth": (140, (115, 0, 7)), "lfo_sync": (141, (116, 0, 1)),
    "lfo_wave": (142, (116, 1, 3)), "pitch_mod_sens": (143, (116, 4, 3)),
    "transpose": (144, (117, 0, 7)),
}

# the largest value each parameter takes; a byte above it is outside the spec
MAXIMA = {
    "r1": 99, "r2": 99, "r3": 99, "r4": 99, "l1": 99, "l2": 99, "l3": 99, "l4": 99,
    "break_point": 99, "left_depth": 99, "right_depth": 99, "left_curve": 3,
    "right_curve": 3, "rate_scaling": 7, "amp_mod_sens": 3, "velocity_sens": 7,
    "level": 99, "mode": 1, "coarse": 31, "fine": 99, "detune": 14,
    "pr1": 99, "pr2": 99, "pr3": 99, "pr4": 99, "pl1": 99, "pl2": 99, "pl3": 99,
    "pl4": 99, "algorithm": 31, "feedback": 7, "osc_key_sync": 1, "lfo_speed": 99,
    "lfo_delay": 99, "lfo_pitch_depth": 99, "lfo_amp_depth": 99, "lfo_sync": 1,
    "lfo_wave": 5, "pitch_mod_sens": 7, "transpose": 48,
}

LFO_WAVES = ("triangle", "saw down", "saw up", "square", "sine", "sample and hold")
CURVES = ("-LIN", "-EXP", "+EXP", "+LIN")
TRANSPOSE_C3 = 24
DETUNE_CENTRE = 7

_STATUS = re.compile(rb"[\x80-\xff]")


def looks_like_syx(head):
    """True if the head opens a System Exclusive message.

    F0 alone is one byte. What makes it a message is that every byte after it,
    up to the F7 that closes it or the end of the head, is a data byte (below
    0x80), and there is a manufacturer id before any F7. Twenty such bytes in a
    row are rare in anything that is not MIDI: UTF-8 text never opens with F0
    followed by an ASCII byte, and binary headers carry high bytes.
    """
    if len(head) < 4 or head[0] != SOX:
        return False
    m = _STATUS.search(head, 1)
    if m is None:
        return True
    return head[m.start()] == EOX and m.start() >= 3


def manufacturer(body):
    """(id text, name) for the id at the front of a message body."""
    if not body:
        return "", "no id"
    if body[0] == 0x00:
        if len(body) < 3:
            return "00", "extended id, cut short"
        return "0x00 0x%02X 0x%02X" % (body[1], body[2]), "extended id"
    return "0x%02X" % body[0], MANUFACTURERS.get(body[0], "unlisted id")


def id_len(body):
    return 3 if body[:1] == b"\x00" else 1


def messages(raw, limit):
    """Split bytes into SysEx messages and the runs between them.

    Returns (spans, complete): each span is a dict with `kind` "sysex" or
    "stray", `start`, `end` (exclusive) and, for a message, `end_by`: "eox"
    when an F7 closes it, "status" when another status byte cuts it short (the
    byte is not part of it), "eof" when the file ends first. `complete` is False
    when `limit` spans were taken before the bytes ran out.
    """
    spans, pos, n = [], 0, len(raw)
    while pos < n:
        if len(spans) >= limit:
            return spans, False
        if raw[pos] != SOX:
            nxt = raw.find(bytes([SOX]), pos)
            end = n if nxt < 0 else nxt
            spans.append({"kind": "stray", "start": pos, "end": end})
            pos = end
            continue
        m = _STATUS.search(raw, pos + 1)
        if m is None:
            spans.append({"kind": "sysex", "start": pos, "end": n, "end_by": "eof"})
            pos = n
        elif raw[m.start()] == EOX:
            spans.append({"kind": "sysex", "start": pos, "end": m.start() + 1,
                          "end_by": "eox"})
            pos = m.start() + 1
        else:
            spans.append({"kind": "sysex", "start": pos, "end": m.start(),
                          "end_by": "status"})
            pos = m.start()
    return spans, True


def yamaha_bulk(body):
    """The Yamaha bulk-dump header of a message body (the bytes after F0), or
    None when the body is not one."""
    if len(body) < YAMAHA_HEADER - 1 or body[0] != YAMAHA or body[1] >> 4 != BULK_DUMP:
        return None
    return {"device": body[1] & 0x0F, "format": body[2],
            "count": (body[3] << 7) | body[4]}


def checksum(data):
    """The checksum a Yamaha bulk dump carries for these data bytes."""
    return -sum(data) & 0x7F


def param(block, packed, name, op=None):
    """One parameter of a voice block. `op` is 1-6 for an operator parameter.
    The value is the stored number, before any display offset."""
    byte, lsb, width = where(packed, name, op)
    return (block[byte] >> lsb) & ((1 << width) - 1)


def where(packed, name, op=None):
    """(byte offset in the voice block, lsb, width) of a parameter."""
    if op is None:
        vced, vmem = VOICE_PARAMS[name]
        return vmem if packed else (vced, 0, 7)
    vced, (vb, lsb, width) = OP_PARAMS[name]
    if packed:
        return (OPS - op) * VMEM_OP + vb, lsb, width
    return (OPS - op) * VCED_OP + vced, 0, 7


def voice_name(block, packed):
    at = VMEM_NAME if packed else VCED_NAME
    raw = block[at:at + NAME_LEN]
    return "".join(chr(b) if 32 <= b < 127 else "?" for b in raw)


def frequency(mode, coarse, fine):
    """An operator's frequency as the DX7 shows it: a ratio to the key, or a
    fixed frequency in Hz (coarse picks the decade, wrapping every four)."""
    if mode:
        return "fixed %s Hz" % _sig(10 ** (coarse & 3) * 10 ** (fine / 100))
    base = 0.5 if coarse == 0 else coarse
    return "ratio %.2f" % (base * (1 + fine / 100))


def _sig(x):
    return ("%.4g" % x) if x < 1000 else "%d" % round(x)


def out_of_range(block, packed):
    """Names of the parameters whose stored value is above the spec's maximum."""
    bad = [n for n in VOICE_PARAMS if param(block, packed, n) > MAXIMA[n]]
    for op in range(1, OPS + 1):
        bad += ["op%d %s" % (op, n) for n in OP_PARAMS
                if param(block, packed, n, op) > MAXIMA[n]]
    return bad


def dx7_voices(data):
    """The name of every complete DX7 voice in the bytes, or None when
    they do not open with a DX7 voice dump. Reads the first message only: the
    hook for an index, which wants the names and nothing else."""
    if len(data) < YAMAHA_HEADER or data[0] != SOX:
        return None
    h = yamaha_bulk(data[1:YAMAHA_HEADER])
    if h is None:
        return None
    body = data[YAMAHA_HEADER:YAMAHA_HEADER + h["count"]]
    if h["format"] == VMEM and h["count"] == VMEM_LEN:
        return [voice_name(body[i * PACKED_VOICE:(i + 1) * PACKED_VOICE], True)
                for i in range(len(body) // PACKED_VOICE)] or None
    if h["format"] == VCED and h["count"] == VCED_LEN and len(body) == VCED_LEN:
        return [voice_name(body, False)]
    return None
