"""SPC walker: the tag, the CPU state, the RAM, the DSP, and the samples in it.

An SPC is a frozen sound chip, and the interesting thing a reader can do with
one is find the samples. The DSP's DIR register names the page of RAM holding
the sample directory, and every entry that points somewhere sane is a BRR
sample -- the same nine-byte-block codec acidcat already decodes for SNES
ROMs. So the walk emits the fixed regions the spec lays out and then, inside
the RAM, one chunk per sample the directory reaches.

See core/formats/spc.py for the layout and where it came from.
"""

import os
import struct

from acidcat.core.formats import spc as spcmod
from acidcat.core.primitives.notes import coverage
from acidcat.core.walk.base import Unsupported as _Unsupported
from acidcat.core.walk.base import _f

# An SPC is 66,048 bytes plus an optional xid6 chunk, which real files keep
# under a kilobyte. The cap is for a forged one.
_SPC_READ_CAP = 4 * 1024 * 1024
# Samples listed on the directory chunk. The directory holds 256 slots and
# real tunes use a few dozen; the count is always reported.
_SPC_SAMPLE_LIST_CAP = 64
# xid6 sub-chunks listed. A real extension has under twenty.
_SPC_XID6_CAP = 64


def inspect_spc(filepath, deep=False):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as fh:
        raw = fh.read(min(size, _SPC_READ_CAP))
    if not spcmod.is_spc(raw):
        raise _Unsupported("not an SPC (no SNES-SPC700 magic)")

    warns = []
    if size > _SPC_READ_CAP:
        warns.append(coverage("file is %d bytes; parsed the first %d"
                              % (size, len(raw))))
    h = spcmod.parse_header(raw)
    if not h["ok"]:
        return [{"id": "header", "offset": 0, "size": min(size, spcmod.HEADER),
                 "summary": "not a resolvable SPC: %s" % h["why"],
                 "fields": [], "warnings": [], "payload_base": 0}], \
            ["header did not resolve: %s" % h["why"]]

    chunks = [_header_chunk(h)]
    if size < spcmod.BASE_SIZE:
        warns.append("file is %d bytes; a complete SPC is %d before any "
                     "extension, so the RAM image is truncated"
                     % (size, spcmod.BASE_SIZE))
        return chunks + [_region("ram", spcmod.RAM_AT, min(size, spcmod.RAM_AT
                                                             + spcmod.RAM)
                                 - spcmod.RAM_AT, "SPC700 RAM, truncated")], \
            warns

    # The RAM, with the samples the VOICES are set to play carved out of it.
    # The directory names up to 256; most are stale (see voice_sources), so
    # only the entries the eight SRCN registers name become chunks. The rest
    # is a count.
    directory = spcmod.sample_directory(raw)
    entries = {i: (st, lp) for i, st, lp in directory}
    sources = spcmod.voice_sources(raw)
    wanted = sorted({n for n in sources.values() if n in entries})
    ram = _region("ram", spcmod.RAM_AT, spcmod.RAM,
                  "64 KB of SPC700 RAM: the program, %d directory entries, "
                  "%d sample%s the voices are set to play"
                  % (len(directory), len(wanted), "" if len(wanted) == 1 else "s"))
    page = raw[spcmod.DSP_AT + spcmod.DSP_DIR]
    ram["fields"] = [
        _f(None, 0, "sample_directory", "$%02X00" % page,
           "DSP register DIR names this page; 256 slots of start and loop"),
        _f(None, 0, "directory_entries", len(directory),
           "slots pointing inside RAM; most are stale, left by other tunes"),
        _f(None, 0, "voice_samples", len(wanted),
           "the entries the eight voices' SRCN registers name"),
    ]
    for v, n in sources.items():
        ram["fields"].append(_f(None, 0, "voice[%d]" % v,
                                "sample %d" % n if n in entries
                                else "sample %d, not in the directory" % n))
    chunks.append(ram)

    listed = 0
    placed = []
    for idx in wanted:
        if listed >= _SPC_SAMPLE_LIST_CAP:
            break
        listed += 1
        start, loop = entries[idx]
        length = spcmod.brr_length(raw, start)
        loops = spcmod.brr_loops(raw, start, length)
        if loop < start or loop >= start + length:
            note = "loop address $%04X is outside the sample" % loop
        elif not loops:
            note = "one-shot"
        else:
            note = ("loops from the start" if loop == start
                    else "loops from $%04X" % loop)
        voices = [str(v) for v, n in sources.items() if n == idx]
        chunk = {
            "id": "sample[%d]" % idx, "offset": spcmod.RAM_AT + start,
            "size": length,
            "summary": "BRR sample at $%04X, %d bytes, %s; voice%s %s"
                       % (start, length, note, "" if len(voices) == 1 else "s",
                          ", ".join(voices)),
            "fields": [_f(None, 0, "start", "$%04X" % start, "in SPC700 RAM"),
                       _f(None, 0, "loop", "$%04X" % loop),
                       _f(None, 0, "blocks", length // spcmod.BRR_BLOCK,
                          "nine bytes each, sixteen samples per block"),
                       _f(None, 0, "voices", ", ".join(voices))],
            "warnings": [], "payload_base": spcmod.RAM_AT + start,
            "payload_len": length, "extent_len": length}
        for o_start, o_len, o_idx in placed:
            if start < o_start + o_len and o_start < start + length:
                chunk["warnings"].append(
                    "overlaps sample[%d]: two voices reading the same RAM "
                    "from different points, or a stale entry" % o_idx)
                break
        placed.append((start, length, idx))
        chunks.append(chunk)
    if len(wanted) > _SPC_SAMPLE_LIST_CAP:
        note = coverage("listing the first %d of %d voice samples"
                        % (_SPC_SAMPLE_LIST_CAP, len(wanted)))
        ram["warnings"].append(note)
        warns.append(note)

    dsp = _region("dsp", spcmod.DSP_AT, spcmod.DSP_SIZE, "128 DSP registers")
    dsp["fields"] = _dsp_fields(raw)
    chunks.append(dsp)
    chunks.append(_region("unused", spcmod.UNUSED_AT, 0x40, "unused, 64 bytes"))
    chunks.append(_region("ipl", spcmod.IPL_AT, spcmod.IPL_SIZE,
                          "the 64-byte IPL ROM area"))

    if size > spcmod.BASE_SIZE:
        chunks.extend(_xid6(raw, size, warns))
    return chunks, warns


def _region(cid, at, length, summary):
    return {"id": cid, "offset": at, "size": length, "summary": summary,
            "fields": [], "warnings": [], "payload_base": at,
            "payload_len": length, "extent_len": length}


def _header_chunk(h):
    t = h["tag"]
    fields = [
        _f(0x00, 33, "magic", "SNES-SPC700 Sound File Data " + h["magic_version"],
           "" if h["magic_version"] == "v0.30"
           else "an older dumper; the spec documents only v0.30"),
        _f(0x23, 1, "tag_flag", "0x%02X" % (0x26 if h["has_tag"] else 0x1A),
           "the spec says 0x26 means a tag follows; real files carry 0x1A "
           "and a tag anyway, so the slots are read regardless"),
        _f(0x24, 1, "version_minor", h["version"]),
        _f(0x25, 2, "pc", "$%04X" % h["pc"], "where the SPC700 was stopped"),
        _f(0x27, 1, "a", "$%02X" % h["a"]),
        _f(0x28, 1, "x", "$%02X" % h["x"]),
        _f(0x29, 1, "y", "$%02X" % h["y"]),
        _f(0x2A, 1, "psw", "$%02X" % h["psw"]),
        _f(0x2B, 1, "sp", "$%02X" % h["sp"], "low byte; the stack is page 1"),
    ]
    # the spec's flag byte is reported above and not obeyed: every real file
    # has 0x1A there and a full tag. If the slots held text, show it.
    if h["tag"]:
        fields.append(_f(None, 0, "tag_style", h["tag_style"],
                         "the date, length and fade are text in one spelling "
                         "and packed numbers in the other; nothing says which"))
        for key, off, n in (("title", 0x2E, 32), ("game", 0x4E, 32),
                            ("dumper", 0x6E, 16), ("comment", 0x7E, 32)):
            if key in t:
                fields.append(_f(off, n, key, t[key]))
        if "artist" in t:
            fields.append(_f(None, 0, "artist", t["artist"]))
        if "date" in t:
            fields.append(_f(None, 0, "dumped", t["date"]))
        if t.get("seconds"):
            fields.append(_f(None, 0, "length", "%d s" % t["seconds"],
                             "play this long, then fade"))
        if t.get("fade_ms"):
            fields.append(_f(None, 0, "fade", "%d ms" % t["fade_ms"]))
        if h["emulator"] is not None:
            fields.append(_f(None, 0, "emulator",
                             spcmod.EMULATORS.get(h["emulator"],
                                                  "%d" % h["emulator"]),
                             "the one that made the dump"))
        if h["disables"]:
            fields.append(_f(None, 0, "channels_off", "0x%02X" % h["disables"],
                             "a set bit mutes that voice"))
    title = t.get("title") or "(untitled)"
    game = t.get("game")
    return {"id": "header", "offset": 0, "size": spcmod.HEADER,
            "summary": title + (" -- " + game if game else ""),
            "fields": fields, "warnings": [],
            "payload_base": 0, "payload_len": spcmod.HEADER,
            "extent_len": spcmod.HEADER}


_DSP_GLOBAL = (
    (0x0C, "MVOL_L", "main volume, left"), (0x1C, "MVOL_R", "main volume, right"),
    (0x2C, "EVOL_L", "echo volume, left"), (0x3C, "EVOL_R", "echo volume, right"),
    (0x4C, "KON", "key on, one bit per voice"), (0x5C, "KOFF", "key off"),
    (0x6C, "FLG", "reset, mute, echo off, noise clock"),
    (0x7C, "ENDX", "voices that reached the end of their sample"),
    (0x0D, "EFB", "echo feedback"), (0x2D, "PMON", "pitch modulation"),
    (0x3D, "NON", "noise on"), (0x4D, "EON", "echo on"),
    (0x5D, "DIR", "sample directory page"), (0x6D, "ESA", "echo buffer page"),
    (0x7D, "EDL", "echo delay"),
)


def _dsp_fields(raw):
    base = spcmod.DSP_AT
    out = []
    for reg, name, note in _DSP_GLOBAL:
        out.append(_f(reg, 1, name, "$%02X" % raw[base + reg], note))
    kon = raw[base + 0x4C]
    voices = [v for v in range(8) if kon & (1 << v)]
    if voices:
        out.append(_f(None, 0, "keyed_on", ", ".join(str(v) for v in voices),
                      "voices sounding at the moment of the dump"))
    return out


def _xid6(raw, size, warns):
    """The extended tag, if one follows the base image."""
    at = spcmod.XID6_AT
    if raw[at:at + 4] != b"xid6":
        n = size - at
        warns.append("%d bytes after the base image are not an xid6 chunk" % n)
        return [_region("trailing", at, n, "%d bytes, not xid6" % n)]
    declared = struct.unpack_from("<I", raw, at + 4)[0]
    length = min(8 + declared, size - at)
    fields = [_f(0x04, 4, "size", declared)]
    pos = at + 8
    end = at + length
    n = 0
    xw = []
    while pos + 4 <= end and n < _SPC_XID6_CAP:
        sid, stype, data = raw[pos], raw[pos + 1], struct.unpack_from("<H", raw, pos + 2)[0]
        n += 1
        if stype == 0:
            fields.append(_f(pos - at, 4, "sub[0x%02X]" % sid, data,
                             "value held in the header"))
            pos += 4
        else:
            if stype == 1:
                text = raw[pos + 4:pos + 4 + data].split(b"\x00", 1)[0]
                value = text.decode("latin-1")
            elif stype == 4:
                value = struct.unpack_from("<I", raw, pos + 4)[0] if data >= 4 else data
            else:
                value = "type %d, %d bytes" % (stype, data)
            fields.append(_f(pos - at, 4 + data, "sub[0x%02X]" % sid, value,
                             _XID6_IDS.get(sid, "")))
            pos += 4 + ((data + 3) & ~3)
    if n >= _SPC_XID6_CAP:
        xw.append(coverage("listing the first %d xid6 sub-chunks" % n))
        warns.extend(xw)
    if 8 + declared > size - at:
        xw.append("xid6 declares %d bytes and %d remain" % (declared, size - at - 8))
    return [{"id": "xid6", "offset": at, "size": length,
             "summary": "extended ID666, %d sub-chunk%s" % (n, "" if n == 1 else "s"),
             "fields": fields, "warnings": xw, "payload_base": at + 8,
             "payload_len": length - 8, "extent_len": length}]


_XID6_IDS = {
    0x01: "song name", 0x02: "game name", 0x03: "artist", 0x04: "dumper",
    0x05: "dump date", 0x06: "emulator", 0x07: "comments",
    0x10: "OST title", 0x11: "OST disc", 0x12: "OST track",
    0x13: "publisher", 0x14: "copyright year",
    0x30: "intro length, 1/64000 s", 0x31: "loop length", 0x32: "end length",
    0x33: "fade length", 0x34: "muted voices", 0x35: "loop count",
    0x36: "amplification",
}
