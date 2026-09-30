"""Universal MIDI Packet (UMP) -- the MIDI 2.0 wire / file primitive.

A UMP is 1-4 32-bit words. The top nibble of word 1 is the Message Type (MT),
and the MT alone fixes the packet size (Table 4 below), so a UMP stream is
self-delimiting -- no length prefixes, no framing. ``iter_ump`` walks a byte
buffer packet by packet; ``decode`` turns one packet into a typed dict. This is
the shared engine behind the .midi2 clip-file walker and any raw-UMP view.

Byte order is deliberately out of scope of the core UMP spec (it is pinned per
transport / file). The MIDI Clip File is big-endian, which is the default here;
pass endian="little" for a native-endian capture.

Reference: MMA M2-104-UM, UMP & MIDI 2.0 Protocol v1.1.2.

    from acidcat.core.formats import ump
    for off, words, msg in ump.iter_ump(data):   # big-endian by default
        print(msg["kind"], msg)
"""

import struct

# words consumed per Message Type (index = MT nibble). Reserved MTs still have a
# fixed size, so the walk stays synchronized through message types it cannot decode.
_MT_WORDS = (1, 1, 1, 2, 2, 4, 1, 1, 2, 2, 2, 3, 3, 4, 4, 4)

_CV = {0x8: "note_off", 0x9: "note_on", 0xA: "poly_pressure", 0xB: "control_change",
       0xC: "program_change", 0xD: "channel_pressure", 0xE: "pitch_bend"}
# MIDI 2.0-only channel-voice opcodes (MT 0x4)
_M2 = {0x0: "per_note_rcontroller", 0x1: "per_note_acontroller", 0x2: "rpn",
       0x3: "nrpn", 0x4: "rel_rpn", 0x5: "rel_nrpn", 0x6: "per_note_pitch_bend",
       0xF: "per_note_management"}
_UTIL = {0x0: "noop", 0x1: "jr_clock", 0x2: "jr_timestamp",
         0x3: "dctpq", 0x4: "delta_clockstamp"}
_SYSEX_STATUS = {0x0: "complete", 0x1: "start", 0x2: "continue", 0x3: "end"}
# SysEx8's Message Type (0x5) also carries the Mixed Data Set messages
_SYSEX8_STATUS = {**_SYSEX_STATUS, 0x8: "mixed_data_set_header",
                  0x9: "mixed_data_set_payload"}
# UMP Stream status values, M2-104-UM v1.1.2 section 7.1 and Appendix F
_STREAM_STATUS = {0x00: "endpoint_discovery", 0x01: "endpoint_info",
                  0x02: "device_identity", 0x03: "endpoint_name",
                  0x04: "product_instance_id", 0x05: "stream_config_request",
                  0x06: "stream_config_notification",
                  0x10: "function_block_discovery", 0x11: "function_block_info",
                  0x12: "function_block_name",
                  0x20: "start_of_clip", 0x21: "end_of_clip"}


def mt_words(mt):
    """Number of 32-bit words a packet of Message Type ``mt`` occupies (1-4)."""
    return _MT_WORDS[mt & 0xF]


def iter_ump(data, endian="big"):
    """Yield (offset, words, message) for each UMP in ``data``. ``words`` is the
    tuple of raw 32-bit ints; ``message`` is decode()'s typed dict. A truncated
    final packet is skipped (the walk stops cleanly at a short tail)."""
    pre = ">" if endian != "little" else "<"
    pos, n = 0, len(data)
    while pos + 4 <= n:
        w0 = struct.unpack_from(pre + "I", data, pos)[0]
        nwords = _MT_WORDS[w0 >> 28]
        if pos + nwords * 4 > n:
            break
        words = struct.unpack_from(pre + str(nwords) + "I", data, pos)
        yield pos, words, decode(words)
        pos += nwords * 4


def _bytes_be(words):
    out = bytearray()
    for w in words:
        out += struct.pack(">I", w)
    return bytes(out)


def decode(words):
    """Decode one UMP (a tuple of 32-bit words) into a typed dict. Always returns
    at least {mt, kind}; unhandled/reserved types get kind='reserved'."""
    w0 = words[0]
    mt = w0 >> 28
    group = (w0 >> 24) & 0xF                          # meaningless for MT 0x0/0xF (groupless)

    if mt == 0x0:                                     # utility
        st = (w0 >> 20) & 0xF
        kind = _UTIL.get(st, "utility")
        m = {"mt": mt, "kind": kind}
        if kind == "jr_clock" or kind == "jr_timestamp" or kind == "dctpq":
            m["value"] = w0 & 0xFFFF
        elif kind == "delta_clockstamp":
            m["ticks"] = w0 & 0xFFFFF                 # 20-bit
        return m

    if mt == 0x2:                                     # MIDI 1.0 channel voice
        st = (w0 >> 20) & 0xF
        return {"mt": mt, "kind": _CV.get(st, "cv1"), "group": group,
                "channel": (w0 >> 16) & 0xF, "data1": (w0 >> 8) & 0x7F, "data2": w0 & 0x7F}

    if mt == 0x4:                                     # MIDI 2.0 channel voice
        st = (w0 >> 20) & 0xF
        ch = (w0 >> 16) & 0xF
        w1 = words[1]
        kind = _CV.get(st) or _M2.get(st, "cv2")
        m = {"mt": mt, "kind": kind, "group": group, "channel": ch}
        if st in (0x8, 0x9):                          # note off / on
            m.update(note=(w0 >> 8) & 0x7F, attr_type=w0 & 0xFF,
                     velocity=(w1 >> 16) & 0xFFFF, attr_data=w1 & 0xFFFF)
        elif st == 0xB:                               # control change
            m.update(index=(w0 >> 8) & 0x7F, data=w1)
        elif st == 0xC:                               # program change
            m.update(options=w0 & 0xFF, program=(w1 >> 24) & 0x7F,
                     bank=(((w1 >> 8) & 0x7F) << 7) | (w1 & 0x7F))
        elif st == 0xA:                               # poly pressure
            m.update(note=(w0 >> 8) & 0x7F, data=w1)
        elif st == 0xF:                               # per-note management
            m.update(note=(w0 >> 8) & 0x7F, options=w0 & 0xFF)
        elif st == 0xD:                               # channel pressure
            m.update(data=w1)
        elif st == 0xE:                               # pitch bend
            m.update(data=w1)
        elif st in (0x2, 0x3, 0x4, 0x5):              # RPN / NRPN (registered/assignable)
            m.update(bank=(w0 >> 8) & 0x7F, param=w0 & 0x7F, data=w1)
        elif st in (0x0, 0x1):                        # per-note controller
            m.update(note=(w0 >> 8) & 0x7F, index=w0 & 0xFF, data=w1)
        elif st == 0x6:                               # per-note pitch bend
            m.update(note=(w0 >> 8) & 0x7F, data=w1)
        else:
            m["raw"] = words
        return m

    if mt in (0x3, 0x5):                              # SysEx7 (64b) / SysEx8 (128b)
        st = (w0 >> 20) & 0xF
        nbytes = (w0 >> 16) & 0xF
        m = {"mt": mt, "kind": "sysex7" if mt == 0x3 else "sysex8", "group": group,
             "status": (_SYSEX8_STATUS if mt == 0x5 else _SYSEX_STATUS).get(st, "?"),
             "nbytes": nbytes}
        if mt == 0x5:
            m["stream_id"] = (w0 >> 8) & 0xFF
        return m

    if mt == 0xD:                                     # Flex Data (tempo / time sig / text)
        form = (w0 >> 22) & 0x3
        addr = (w0 >> 20) & 0x3
        status_bank = (w0 >> 8) & 0xFF
        status = w0 & 0xFF
        m = {"mt": mt, "kind": "flex_data", "group": group, "form": form,
             "address": addr, "channel": (w0 >> 16) & 0xF,
             "status_bank": status_bank, "status": status}
        if status_bank == 0x00 and status == 0x00:    # Set Tempo (10ns units / quarter)
            tempo = words[1]
            m.update(kind="set_tempo", tempo_10ns=tempo,
                     bpm=(6_000_000_000 / tempo) if tempo else None)
        elif status_bank == 0x00 and status == 0x01:  # Set Time Signature
            w1 = words[1]
            m.update(kind="set_time_signature", numerator=(w1 >> 24) & 0xFF,
                     denom_pow2=(w1 >> 16) & 0xFF, clocks32=(w1 >> 8) & 0xFF)
        elif status_bank in (0x01, 0x02):             # metadata / performance text
            text = _bytes_be(words[1:]).split(b"\x00", 1)[0]
            m.update(kind="flex_text", status_bank=status_bank,
                     text=text.decode("utf-8", "replace"))
        return m

    if mt == 0xF:                                     # UMP Stream (groupless)
        status = (w0 >> 16) & 0x3FF
        return {"mt": mt, "kind": _STREAM_STATUS.get(status, "ump_stream"), "status": status}

    if mt == 0x1:                                     # system real-time / common
        return {"mt": mt, "kind": "system", "group": group, "status": (w0 >> 16) & 0xFF}

    return {"mt": mt, "kind": "reserved", "words": _MT_WORDS[mt]}
