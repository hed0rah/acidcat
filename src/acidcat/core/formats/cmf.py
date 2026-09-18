"""CMF: Creative Music File, a MIDI track for the AdLib's OPL2 with its patches.

Creative's 1991 format for the Sound Blaster: a Standard MIDI track (one
MTrk's worth of events, running status and all) with the sixteen-byte FM
instrument patches the song needs in front of it, so a player needs no
General MIDI bank, only an OPL2. The header carries offsets to the
instrument block, the music, and three optional text strings.

    0x00   "CTMF"
    0x04   u16 LE   version, BCD: 0x0100 or 0x0101
    0x06   u16 LE   instrument block offset
    0x08   u16 LE   music (event stream) offset
    0x0A   u16 LE   ticks per quarter note
    0x0C   u16 LE   ticks per second (the clock, 1.1: what the timer runs at)
    0x0E   u16 LE   title offset, 0 for none
    0x10   u16 LE   composer offset
    0x12   u16 LE   remarks offset
    0x14   16       channels in use, one byte each, non-zero = used
    0x24   u16 LE   instrument count            (1.1)
    0x26   u16 LE   tempo, BPM                  (1.1)

A 1.0 header ends at 0x24 and its instrument count is what fits between
the two offsets. An instrument is 16 bytes: two operators of six
registers each (modulator then carrier: AM/VIB/EG/KSR/multi, KSL/level,
attack/decay, sustain/release, waveform), then feedback/connection, then
five reserved bytes. The event stream is SMF track syntax with two extra
controller numbers (0x66 marker, 0x67 rhythm mode, 0x68 pitch bend range,
0x69 transpose) and ends on End of Track.

Spec: Creative's own CMF description in the Sound Blaster SDK.
"""

import struct

MAGIC = b"CTMF"
HEADER_V10 = 0x24
HEADER_V11 = 0x28
INSTRUMENT = 16
CHANNELS = 16


def is_cmf(head):
    return head[:4] == MAGIC


def parse_header(raw, filesize):
    h = {"ok": False, "why": "", "version": 0, "version_text": "", "instruments_at": 0,
         "music_at": 0, "ticks_per_quarter": 0, "ticks_per_second": 0,
         "title_at": None, "composer_at": None, "remarks_at": None,
         "channels_used": [], "instrument_count": 0, "tempo": None,
         "header_size": HEADER_V10}
    if len(raw) < HEADER_V10 or not is_cmf(raw):
        h["why"] = "no CTMF magic"
        return h
    (v, ioff, moff, tpq, tps, toff, coff, roff) = struct.unpack_from("<8H", raw, 4)
    h["version"] = v
    h["version_text"] = "%d.%d" % (v >> 8, v & 0xFF)
    h["instruments_at"], h["music_at"] = ioff, moff
    h["ticks_per_quarter"], h["ticks_per_second"] = tpq, tps
    h["title_at"], h["composer_at"], h["remarks_at"] = toff or None, coff or None, roff or None
    h["channels_used"] = [i for i in range(CHANNELS) if raw[0x14 + i]]
    if v >= 0x101 and len(raw) >= HEADER_V11:
        h["header_size"] = HEADER_V11
        h["instrument_count"], h["tempo"] = struct.unpack_from("<HH", raw, 0x24)
    else:
        h["instrument_count"] = max(0, (moff - ioff) // INSTRUMENT)
    if not h["header_size"] <= ioff <= moff <= filesize:
        h["why"] = "the instrument and music offsets are not in order inside the file"
        return h
    h["ok"] = True
    return h


def instrument(raw, at):
    """One 16-byte patch as a dict of the register bytes, or None."""
    if at + INSTRUMENT > len(raw):
        return None
    b = raw[at:at + INSTRUMENT]
    # the ten operator bytes are interleaved: modulator, carrier, modulator ...
    ops = []
    for o in range(2):
        r = b[o:10:2]
        ops.append({"ave": r[0], "ksl_level": r[1], "attack_decay": r[2],
                    "sustain_release": r[3], "waveform": r[4]})
    return {"modulator": ops[0], "carrier": ops[1], "feedback_conn": b[10],
            "reserved": b[11:16]}


def cstring(raw, at):
    end = raw.find(b"\x00", at)
    if end < 0:
        end = len(raw)
    return raw[at:end].decode("cp437", errors="replace"), end + 1 - at
