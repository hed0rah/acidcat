"""S98: a register log for the PC-98's sound chips, and their cousins.

The same idea as VGM, from the Japanese PC scene: every write a program
made to its FM and PSG chips, with the time between, so a player with the
same chips plays it back. Three versions matter. v1 is the original: a
32-byte header, a plain-text title after it, the dump at the next 128-byte
boundary. v2 was never used. v3 adds a device table and a `[S98]` tag of
key=value lines at the END, after the dump.

    0x00   "S98"        magic
    0x03   '1' or '3'   version, as a text digit
    0x04   u32 LE       timer numerator   (0 means 10)
    0x08   u32 LE       timer denominator (0 means 1000): one sync is
                        num/den seconds, 10 ms by default
    0x0C   u32 LE       compression, always 0
    0x10   u32 LE       tag offset; 0 for none. In v1 it is the title,
                        BEFORE the dump; in v3 the [S98] block after it
    0x14   u32 LE       dump offset
    0x18   u32 LE       loop offset; 0 for none
    0x1C   u32 LE       device count (v3; the field is padding in v1)
    0x20   16 each      v3 devices: type, clock, pan, reserved

The dump is a byte code: 0x00-0x7F is a device write (device = op / 2,
port = op & 1) with a register and a value; 0xFF is one sync; 0xFE is a
sync count plus 2, as a 7-bit varint (low bits first, bit 7 continues) in
EVERY version -- a reading of "one byte in v1" breaks 200 real files whose
counts run past 127; 0xFD ends it.

Spec: the format's own s98v3.txt and the readers in m3u/kmpp players.
"""

import struct

MAGIC = b"S98"
HEADER = 0x20
DEVICE_ENTRY = 16
DEFAULT_TIMER = (10, 1000)
END = 0xFD
SYNC_N = 0xFE
SYNC_1 = 0xFF
TAG_MARK = b"[S98]"
TAG_UTF8 = b"utf8="

DEVICE_TYPES = {0: "none", 1: "YM2149 (PSG)", 2: "YM2203 (OPN)", 3: "YM2612 (OPN2)",
                4: "YM2608 (OPNA)", 5: "YM2151 (OPM)", 6: "YM2413 (OPLL)",
                7: "YM3526 (OPL)", 8: "YM3812 (OPL2)", 9: "YMF262 (OPL3)",
                15: "AY-3-8910 (PSG)", 16: "SN76489 (DCSG)"}
V1_DEVICE = "YM2608 (OPNA)"          # v1 has no table; one OPNA is implied


def is_s98(head):
    return head[:3] == MAGIC and head[3:4] in (b"1", b"2", b"3")


def parse_header(raw):
    h = {"ok": False, "why": "", "version": 0, "timer": DEFAULT_TIMER,
         "compressed": 0, "tag_at": None, "dump_at": 0, "loop_at": None,
         "devices": [], "header_size": HEADER, "tag_before_dump": False}
    if len(raw) < HEADER or not is_s98(raw):
        h["why"] = "no S98 magic"
        return h
    h["version"] = raw[3] - 0x30
    num, den, comp, tag, dump, loop, ndev = struct.unpack_from("<7I", raw, 4)
    h["timer"] = (num or DEFAULT_TIMER[0], den or DEFAULT_TIMER[1])
    h["compressed"] = comp
    h["tag_at"] = tag or None
    h["dump_at"] = dump
    h["loop_at"] = loop or None
    if h["version"] >= 3:
        for i in range(min(ndev, 64)):
            at = HEADER + i * DEVICE_ENTRY
            if at + DEVICE_ENTRY > len(raw):
                break
            dtype, clock, pan, _res = struct.unpack_from("<4I", raw, at)
            h["devices"].append({"at": at, "type": dtype,
                                 "name": DEVICE_TYPES.get(dtype, "type %d" % dtype),
                                 "clock": clock, "pan": pan})
        h["header_size"] = HEADER + len(h["devices"]) * DEVICE_ENTRY
    h["tag_before_dump"] = h["tag_at"] is not None and h["tag_at"] < dump
    h["ok"] = True
    return h


def walk_dump(raw, start, end, version, cap):
    """Decode command lengths from `start` until 0xFD, `end`, or `cap`
    commands. Returns commands, syncs, writes per device, where it ended,
    whether it ended on the marker, and a reason if not."""
    pos = start
    n = syncs = 0
    writes = {}
    ended = False
    why = ""
    while pos < end and n < cap:
        op = raw[pos]
        n += 1
        if op == END:
            pos += 1
            ended = True
            break
        if op == SYNC_1:
            pos += 1
            syncs += 1
            continue
        if op == SYNC_N:
            pos += 1
            v = sh = 0
            while pos < end:
                b = raw[pos]
                pos += 1
                v |= (b & 0x7F) << sh
                sh += 7
                if not b & 0x80:
                    break
            syncs += v + 2
            continue
        if op < 0x80:
            dev = op >> 1
            writes[dev] = writes.get(dev, 0) + 1
            pos += 3
            continue
        why = "opcode 0x%02X at 0x%X is not one the spec defines" % (op, pos)
        break
    if pos > end:
        why = why or "the last command runs past the end"
        pos = end
    return {"commands": n, "syncs": syncs, "writes": writes, "end": pos,
            "ended": ended, "why": why, "capped": n >= cap}


def parse_tag(raw, at, end):
    """The tag block. v3: "[S98]" then key=value lines, LF-separated, in
    Shift-JIS unless a "utf8=" line says otherwise. v1: plain text up to
    the dump, NUL-padded."""
    blob = raw[at:end]
    tag = {"ok": False, "fields": {}, "text": "", "size": len(blob), "encoding": "shift_jis"}
    if blob.startswith(TAG_MARK):
        body = blob[len(TAG_MARK):]
        if TAG_UTF8 in body:
            tag["encoding"] = "utf-8"
        text = body.decode(tag["encoding"], errors="replace")
        for line in text.split("\n"):
            line = line.strip("\r\x00")
            if "=" in line:
                k, v = line.split("=", 1)
                if k and v:
                    tag["fields"][k.lower()] = v
        tag["ok"] = True
        return tag
    text = blob.split(b"\x00", 1)[0]
    if text:
        tag["text"] = text.decode("shift_jis", errors="replace").strip()
        tag["fields"]["title"] = tag["text"]
        tag["ok"] = True
    return tag
