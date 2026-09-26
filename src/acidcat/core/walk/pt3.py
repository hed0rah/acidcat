"""PT3 walker: the header, the pattern table, and every pointed region.

A PT3 reaches everything by absolute pointers, so the walk is the sorted
set of pointers: each region begins where a pointer says and ends where
the next begins, and it is named by everything that points at it (two
patterns can share a channel stream; an empty sample is shared by many).
Samples and ornaments declare their own length inside the region, and the
walker checks that it fits.
"""


from acidcat.core.formats import pt3 as pt3mod
from acidcat.core.primitives.notes import coverage
from acidcat.core.walk.base import Unsupported as _Unsupported, _open, _size
from acidcat.core.walk.base import _f

# A PT3 is a 16-bit address space: nothing past 64 KB can be pointed at.
_PT3_READ_CAP = 64 * 1024
# Regions listed as chunks. 32 samples + 16 ornaments + 3 streams per
# pattern; a large module has a few hundred.
_PT3_REGION_LIST_CAP = 512


def inspect_pt3(filepath, deep=False):
    size = _size(filepath)
    with _open(filepath) as fh:
        raw = fh.read(min(size, _PT3_READ_CAP))
    warns = []
    if size > _PT3_READ_CAP:
        warns.append(coverage("file is %d bytes; parsed the first %d, which is "
                              "all a 16-bit pointer can reach" % (size, _PT3_READ_CAP)))
    if pt3mod.is_pt3(raw):
        h = pt3mod.parse(raw, len(raw))
    else:
        h = pt3mod.parse_pt2(raw, len(raw))
        if not h["ok"]:
            raise _Unsupported("no ProTracker 3 signature, and not a PT2 by "
                               "arithmetic: " + h["why"])
    if not h["ok"]:
        raise _Unsupported(h["why"])

    used_samples = sum(1 for p in h["samples"] if p)
    if h["kind"] == "pt3":
        fields = [
            _f(0x00, pt3mod.SIG_LEN, "signature", h["signature"]),
            _f(pt3mod.NAME_AT, pt3mod.NAME_LEN, "name", h["name"] or "(empty)"),
            _f(pt3mod.AUTHOR_AT, pt3mod.AUTHOR_LEN, "author", h["author"] or "(empty)"),
            _f(pt3mod.TONE_TABLE_AT, 1, "tone_table", h["tone_table"],
               pt3mod.TONE_TABLES.get(h["tone_table"], "undefined")),
            _f(pt3mod.DELAY_AT, 1, "delay", h["delay"], "ticks per row"),
            _f(pt3mod.POSITIONS_AT, 1, "positions", h["positions"]),
            _f(pt3mod.LOOP_AT, 1, "loop", h["loop"], "position to return to"),
            _f(pt3mod.PATTERNS_PTR_AT, 2, "pattern_table", "0x%04X" % h["patterns_at"],
               "%d patterns, three pointers each" % h["pattern_count"]),
            _f(pt3mod.SAMPLES_AT, 64, "samples", "%d of 32 used" % used_samples),
            _f(pt3mod.ORNAMENTS_AT, 32, "ornaments",
               "%d of 16 used" % sum(1 for p in h["ornaments"] if p)),
            _f(pt3mod.POSITION_LIST_AT, h["header_size"] - pt3mod.POSITION_LIST_AT,
               "position_list", " ".join(str(p) for p in h["position_list"][:32])
               + (" ..." if len(h["position_list"]) > 32 else ""),
               "pattern numbers, stored times three, 0xFF-ended"),
        ]
        if h["tone_table"] not in pt3mod.TONE_TABLES:
            warns.append("tone table %d is not one of the four defined" % h["tone_table"])
    else:
        fields = [
            _f(None, 0, "layout", "Pro Tracker 2",
               "no signature; identified because the counts agree, every "
               "pointer is inside the file, and the first region starts "
               "where the header ends"),
            _f(pt3mod.PT2_DELAY_AT, 1, "delay", h["delay"], "ticks per row"),
            _f(pt3mod.PT2_POSITIONS_AT, 1, "positions", h["positions"]),
            _f(pt3mod.PT2_LOOP_AT, 1, "loop", h["loop"], "position to return to"),
            _f(pt3mod.PT2_SAMPLES_AT, 64, "samples", "%d of 32 used" % used_samples),
            _f(pt3mod.PT2_ORNAMENTS_AT, 32, "ornaments",
               "%d of 16 used" % sum(1 for p in h["ornaments"] if p)),
            _f(pt3mod.PT2_PATTERNS_PTR_AT, 2, "pattern_table", "0x%04X" % h["patterns_at"],
               "%d patterns, three pointers each" % h["pattern_count"]),
            _f(pt3mod.PT2_NAME_AT, pt3mod.PT2_NAME_LEN, "name", h["name"] or "(empty)"),
            _f(pt3mod.PT2_POSITION_LIST_AT, h["header_size"] - pt3mod.PT2_POSITION_LIST_AT,
               "position_list", " ".join(str(p) for p in h["position_list"][:32])
               + (" ..." if len(h["position_list"]) > 32 else ""),
               "pattern numbers, 0xFF-ended"),
        ]
    for kind, i, ptr in h["bad_pointers"][:8]:
        warns.append("%s %d points at %d, past the end of the file; the module "
                     "is truncated" % (kind, i, ptr))
    if len(h["bad_pointers"]) > 8:
        warns.append("%d more pointers past the end" % (len(h["bad_pointers"]) - 8))
    if h["loop"] >= h["positions"]:
        warns.append("loop position %d is past the last position" % h["loop"])
    title = h["name"] + (" -- " + h["author"] if h["author"] else "")
    chunks = [{"id": "header", "offset": 0, "size": h["header_size"],
               "summary": "%s, %d patterns, %d positions, %d samples%s"
                          % (h["signature"].rstrip(" :"), h["pattern_count"],
                             h["positions"], used_samples,
                             (" -- " + title) if h["name"] else ""),
               "fields": fields, "warnings": [], "payload_base": 0}]

    regs = pt3mod.regions(h, len(raw))
    if regs and regs[0][0] > h["header_size"]:
        gap = regs[0][0] - h["header_size"]
        chunks.append({"id": "unpointed", "offset": h["header_size"], "size": gap,
                       "summary": "%d bytes after the header nothing points at" % gap,
                       "fields": [], "warnings": [], "payload_base": h["header_size"]})
    listed = 0
    for at, n, names in regs:
        if listed >= _PT3_REGION_LIST_CAP:
            warns.append(coverage("listing the first %d of %d regions"
                                  % (_PT3_REGION_LIST_CAP, len(regs))))
            break
        listed += 1
        chunks.append(_region(raw, h, at, n, names, warns))
    if h["ts_footer"]:
        # the last region absorbed the footer; say so on the file
        warns.append("a Turbo Sound footer names two modules; only the first is walked")
    return chunks, warns


def _region(raw, h, at, n, names, warns):
    kinds = [k for k, _i in names]
    if "pattern_table" in kinds:
        want = h["pattern_table_size"]
        rows = ["%d: %s" % (i, " ".join("0x%04X" % p for p in trio))
                for i, trio in enumerate(h["channel_streams"][:8])]
        c = {"id": "patterns", "offset": at, "size": n,
             "summary": "pattern table, %d patterns x 3 channel pointers" % h["pattern_count"],
             "fields": [_f(0, want, "pointers", "; ".join(rows)
                           + (" ..." if h["pattern_count"] > 8 else ""),
                           "channel A, B, C; absolute")],
             "warnings": [], "payload_base": at}
        if n > want:
            c["fields"].append(_f(want, n - want, "after_table", "%d bytes" % (n - want),
                                  "before the next pointed region"))
        return c
    if "sample" in kinds:
        idx = [i for k, i in names if k == "sample"]
        s = pt3mod.sample_size(raw, at, h["sample_row"])
        c = {"id": "smp[%d]" % idx[0] if len(idx) == 1 else "smp[%s]" % ",".join(map(str, idx)),
             "offset": at, "size": n, "fields": [], "warnings": [], "payload_base": at}
        if s is None:
            c["summary"] = "sample %s: record runs past the end" % idx
            warns.append("sample %s has no room for its two-byte head" % idx)
            return c
        loop, length, want = s
        row = h["sample_row"]
        c["summary"] = "sample, %d rows of %d bytes%s" % (
            length, row, ", looping at %d" % loop if loop < length else "")
        c["fields"] = [_f(0, 1, "loop", loop), _f(1, 1, "length", length, "rows"),
                       _f(2, min(length * row, n - 2), "rows",
                          "%d x (flags, volume, tone offset)" % length)]
        if want > n:
            c["warnings"].append("declares %d bytes and %d fit before the next region" % (want, n))
            warns.append("sample %s runs into the next region" % idx)
        elif want < n:
            c["fields"].append(_f(want, n - want, "after_record", "%d bytes" % (n - want)))
        return c
    if "ornament" in kinds:
        idx = [i for k, i in names if k == "ornament"]
        s = pt3mod.ornament_size(raw, at)
        c = {"id": "orn[%d]" % idx[0] if len(idx) == 1 else "orn[%s]" % ",".join(map(str, idx)),
             "offset": at, "size": n, "fields": [], "warnings": [], "payload_base": at}
        if s is None:
            c["summary"] = "ornament %s: record runs past the end" % idx
            warns.append("ornament %s has no room for its two-byte head" % idx)
            return c
        loop, length, want = s
        c["summary"] = "ornament, %d semitone offsets%s" % (
            length, ", looping at %d" % loop if loop < length else "")
        c["fields"] = [_f(0, 1, "loop", loop), _f(1, 1, "length", length),
                       _f(2, min(length, n - 2), "offsets", "%d signed bytes" % length)]
        if want > n:
            c["warnings"].append("declares %d bytes and %d fit before the next region" % (want, n))
            warns.append("ornament %s runs into the next region" % idx)
        elif want < n:
            c["fields"].append(_f(want, n - want, "after_record", "%d bytes" % (n - want)))
        return c
    # channel streams
    slots = [i for k, i in names if k == "channel"]
    labels = ["pattern %d ch %s" % (i // 3, "ABC"[i % 3]) for i in slots]
    ends = raw[at:at + n].endswith(b"\x00") if n else False
    return {"id": "chan[%d]" % slots[0] if len(slots) == 1 else "chan[%s]" % ",".join(map(str, slots)),
            "offset": at, "size": n,
            "summary": "%s: %d bytes of note stream" % (", ".join(labels), n),
            "fields": [_f(None, 0, "used_by", ", ".join(labels)),
                       _f(None, 0, "ends_with_0x00", "yes" if ends else "no",
                          "the stream's end marker" if ends else
                          "the region ends where the next pointer begins, not at a marker")],
            "warnings": [], "payload_base": at}
