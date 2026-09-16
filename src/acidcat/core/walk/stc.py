"""STC walker: header, samples, positions, ornaments, pattern table, streams.

The blocks come in a fixed order and each declares its own record size,
so the file tiles from the header to the pattern table by arithmetic, and
from there the channel streams tile by their pointers, as in PT3.
"""

import os

from acidcat.core.formats import stc as stcmod
from acidcat.core.primitives.notes import coverage
from acidcat.core.walk.base import Unsupported as _Unsupported
from acidcat.core.walk.base import _f

# A 16-bit address space: nothing past 64 KB can be pointed at.
_STC_READ_CAP = 64 * 1024
# Records listed as chunks: 32 samples, 32 ornaments, 3 streams per pattern.
_STC_LIST_CAP = 512


def inspect_stc(filepath, deep=False):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as fh:
        raw = fh.read(min(size, _STC_READ_CAP))
    warns = []
    if size > _STC_READ_CAP:
        warns.append(coverage("file is %d bytes; parsed the first %d, which is "
                              "all a 16-bit pointer can reach" % (size, _STC_READ_CAP)))
    h = stcmod.parse(raw, len(raw))
    if not h["ok"]:
        raise _Unsupported("not an STC by arithmetic: " + h["why"])
    if h["size"] != size:
        warns.append("the header says %d bytes and the file is %d" % (h["size"], size))
    fields = [
        _f(stcmod.DELAY_AT, 1, "delay", h["delay"], "ticks per row"),
        _f(stcmod.POSITIONS_PTR_AT, 2, "positions_at", "0x%04X" % h["positions_at"]),
        _f(stcmod.ORNAMENTS_PTR_AT, 2, "ornaments_at", "0x%04X" % h["ornaments_at"]),
        _f(stcmod.PATTERNS_PTR_AT, 2, "patterns_at", "0x%04X" % h["patterns_at"]),
        _f(stcmod.IDENT_AT, stcmod.IDENT_LEN, "identifier", h["identifier"] or "(empty)"),
        _f(stcmod.SIZE_AT, 2, "size", h["size"], "bytes, the module's own count"),
    ]
    chunks = [{"id": "header", "offset": 0, "size": stcmod.HEADER,
               "summary": "STC, %d patterns, %d positions, %d samples, %d ornaments%s"
                          % (len(h["patterns"]), len(h["positions"]), len(h["samples"]),
                             len(h["ornaments"]),
                             (" -- " + h["identifier"]) if h["identifier"] else ""),
               "fields": fields, "warnings": [], "payload_base": 0}]
    listed = 0
    capped = False

    def room():
        nonlocal listed, capped
        if listed >= _STC_LIST_CAP:
            capped = True
            return False
        listed += 1
        return True

    for s in h["samples"]:
        if not room():
            break
        looped = s["repeat_len"] > 0 and s["repeat_at"] < stcmod.SAMPLE_ROWS
        chunks.append({"id": "smp[%d]" % s["number"], "offset": s["at"],
                       "size": stcmod.SAMPLE_RECORD,
                       "summary": "sample %d, 32 rows of 3 bytes%s" % (
                           s["number"], ", repeating from %d for %d" % (s["repeat_at"], s["repeat_len"])
                           if looped else ""),
                       "fields": [_f(0, 1, "number", s["number"]),
                                  _f(1, 96, "rows", "32 x (volume and noise, tone offset)"),
                                  _f(97, 1, "repeat_at", s["repeat_at"]),
                                  _f(98, 1, "repeat_len", s["repeat_len"])],
                       "warnings": [], "payload_base": s["at"]})
    pos = h["positions_at"]
    chunks.append({"id": "positions", "offset": pos, "size": h["ornaments_at"] - pos,
                   "summary": "%d positions" % len(h["positions"]),
                   "fields": [_f(0, 1, "count", len(h["positions"]), "stored as count - 1"),
                              _f(1, 2 * len(h["positions"]), "list",
                                 " ".join("%d%+d" % (p, t) if t else "%d" % p
                                          for p, t in h["positions"][:32])
                                 + (" ..." if len(h["positions"]) > 32 else ""),
                                 "pattern number, and a transposition in semitones")],
                   "warnings": [], "payload_base": pos})
    for o in h["ornaments"]:
        if not room():
            break
        chunks.append({"id": "orn[%d]" % o["number"], "offset": o["at"],
                       "size": stcmod.ORNAMENT_RECORD,
                       "summary": "ornament %d, 32 semitone offsets" % o["number"],
                       "fields": [_f(0, 1, "number", o["number"]),
                                  _f(1, 32, "offsets", "32 signed bytes")],
                       "warnings": [], "payload_base": o["at"]})
    gap = h["patterns_at"] - h["ornaments_end"]
    if gap > 0:
        chunks.append({"id": "comment" if h["comment"] else "unpointed",
                       "offset": h["ornaments_end"], "size": gap,
                       "summary": ("text the compiler left here: " + h["comment"])
                                  if h["comment"] else
                                  "%d bytes between the ornaments and the pattern table" % gap,
                       "fields": [_f(0, gap, "text", h["comment"])] if h["comment"] else [],
                       "warnings": [], "payload_base": h["ornaments_end"]})
    pat = h["patterns_at"]
    rows = ["%d: %s" % (p["number"], " ".join("0x%04X" % s for s in p["streams"]))
            for p in h["patterns"][:8]]
    chunks.append({"id": "patterns", "offset": pat, "size": h["pattern_table_end"] - pat,
                   "summary": "pattern table, %d patterns x 3 channel pointers, 0xFF-ended"
                              % len(h["patterns"]),
                   "fields": [_f(0, h["pattern_table_end"] - pat - 1, "entries",
                                 "; ".join(rows) + (" ..." if len(h["patterns"]) > 8 else ""),
                                 "number, then channel A, B, C; absolute"),
                              _f(h["pattern_table_end"] - pat - 1, 1, "end", "0xFF")],
                   "warnings": [], "payload_base": pat})
    regs = stcmod.stream_regions(h, len(raw))
    if regs and regs[0][0] > h["pattern_table_end"]:
        g = regs[0][0] - h["pattern_table_end"]
        chunks.append({"id": "unpointed", "offset": h["pattern_table_end"], "size": g,
                       "summary": "%d bytes after the pattern table nothing points at" % g,
                       "fields": [], "warnings": [], "payload_base": h["pattern_table_end"]})
    for at, n, names in regs:
        if not room():
            break
        labels = ["pattern %d ch %s" % (p, "ABC"[ch]) for p, ch in names]
        chunks.append({"id": "chan[%d%s]" % (names[0][0], "ABC"[names[0][1]]),
                       "offset": at, "size": n,
                       "summary": "%s: %d bytes of note stream" % (", ".join(labels), n),
                       "fields": [_f(None, 0, "used_by", ", ".join(labels))],
                       "warnings": [], "payload_base": at})
    if capped:
        total = len(h["samples"]) + len(h["ornaments"]) + len(regs)
        warns.append(coverage("listing the first %d of %d records" % (_STC_LIST_CAP, total)))
    return chunks, warns
