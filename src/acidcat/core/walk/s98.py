"""S98 walker: header, devices, the tag, the dump, in whichever order.

v1 puts the title before the dump and runs the dump to the end of the
file; v3 puts the [S98] tag after the dump. The walk lays out whichever it
finds, decodes the dump to its end marker, and counts syncs and writes
per device.
"""


from acidcat.core.formats import s98 as s98mod
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import Unsupported as _Unsupported, _open, _size
from acidcat.core.walk.base import _f

# The largest real S98 measured is a few MB; 64 MB reads anything.
_S98_READ_CAP = 64 * 1024 * 1024
# Commands decoded from the dump. A long tune has a few hundred thousand.
_S98_COMMAND_CAP = 4_000_000


def inspect_s98(filepath, deep=False):
    size = _size(filepath)
    with _open(filepath) as fh:
        raw = fh.read(min(size, _S98_READ_CAP))
    warns = []
    if size > _S98_READ_CAP:
        warns.append(hit("read_bytes", _S98_READ_CAP, size,
                         "file is %d bytes; parsed the first %d" % (size, _S98_READ_CAP)))
    h = s98mod.parse_header(raw)
    if not h["ok"]:
        raise _Unsupported(h["why"])
    if h["compressed"]:
        warns.append("the compression field is %d; the spec defines only 0" % h["compressed"])
    if h["dump_at"] < h["header_size"] or h["dump_at"] >= len(raw):
        raise _Unsupported("the dump offset %d is outside the file" % h["dump_at"])

    # where the dump may run to: the tag if it follows, else the end
    tag_at = h["tag_at"]
    if tag_at is not None and not 0 < tag_at < len(raw):
        warns.append("the tag offset %d is outside the file" % tag_at)
        tag_at = None
    dump_end = tag_at if (tag_at is not None and tag_at > h["dump_at"]) else len(raw)
    w = s98mod.walk_dump(raw, h["dump_at"], dump_end, h["version"], _S98_COMMAND_CAP)
    if w["capped"]:
        warns.append(hit("work_steps", _S98_COMMAND_CAP, _S98_COMMAND_CAP,
                         "decoded the first %d commands" % _S98_COMMAND_CAP))
    elif not w["ended"]:
        warns.append("the dump has no end marker (0xFD)" + (": " + w["why"] if w["why"] else ""))
    if h["loop_at"] is not None and not h["dump_at"] <= h["loop_at"] < w["end"]:
        warns.append("the loop offset 0x%X is outside the dump" % h["loop_at"])
    num, den = h["timer"]
    seconds = w["syncs"] * num / den if den else 0
    devs = h["devices"] if h["version"] >= 3 else [
        {"type": 4, "name": s98mod.V1_DEVICE, "clock": 0, "pan": 0, "at": None}]
    for dev, n in sorted(w["writes"].items()):
        if dev >= len(devs) or devs[dev]["type"] == 0:
            warns.append("%d writes to device %d, which the header does not declare" % (n, dev))

    tag = s98mod.parse_tag(raw, tag_at, h["dump_at"] if h["tag_before_dump"] else len(raw)) \
        if tag_at is not None else None
    title = ""
    if tag and tag["ok"]:
        t = tag["fields"].get("title") or ""
        g = tag["fields"].get("game") or ""
        title = (" -- " + t + (" (" + g + ")" if g else "")) if (t or g) else ""

    fields = [
        _f(0x00, 4, "magic", "S98%d" % h["version"]),
        _f(0x04, 8, "timer", "%d/%d s per sync" % (num, den),
           "%.2f ms" % (1000 * num / den)),
        _f(0x0C, 4, "compression", h["compressed"], "always 0"),
        _f(0x10, 4, "tag_at", "0x%X" % tag_at if tag_at is not None else "none",
           "the title, before the dump" if h["tag_before_dump"] else "the [S98] block, after the dump"),
        _f(0x14, 4, "dump_at", "0x%X" % h["dump_at"]),
        _f(0x18, 4, "loop_at", "0x%X" % h["loop_at"] if h["loop_at"] is not None else "none"),
        _f(0x1C, 4, "devices", len(h["devices"]) if h["version"] >= 3 else "1 implied (OPNA)",
           "" if h["version"] >= 3 else "v1 has no device table"),
        _f(None, 0, "syncs", "%s, %s" % (format(w["syncs"], ","), _mmss(seconds))),
        _f(None, 0, "commands", format(w["commands"], ",")),
    ]
    for d in devs:
        idx = devs.index(d)
        fields.append(_f(d["at"], s98mod.DEVICE_ENTRY if d["at"] is not None else 0,
                         "device_%d" % idx,
                         d["name"] + ((" @ %s" % _hz(d["clock"])) if d["clock"] else ""),
                         "%s writes" % format(w["writes"].get(idx, 0), ",")))
    chunks = [{"id": "header", "offset": 0, "size": h["header_size"],
               "summary": "S98 v%d, %s, %s%s" % (
                   h["version"], ", ".join(d["name"].split(" (")[0] for d in devs if d["type"]),
                   _mmss(seconds), title),
               "fields": fields, "warnings": [], "payload_base": 0}]

    regions = []
    if tag_at is not None and tag and tag["ok"]:
        tag_end = h["dump_at"] if h["tag_before_dump"] else len(raw)
        tfields = [_f(None, 0, k, v[:200]) for k, v in tag["fields"].items()]
        if not h["tag_before_dump"]:
            tfields.insert(0, _f(0, 5, "mark", "[S98]", tag["encoding"]))
        regions.append({"id": "tag", "offset": tag_at, "size": tag_end - tag_at,
                        "summary": ("title, plain text" if h["tag_before_dump"] else
                                    "[S98] tag, %d fields" % len(tag["fields"])) + title,
                        "fields": tfields, "warnings": [], "payload_base": tag_at})
    regions.append({"id": "dump", "offset": h["dump_at"], "size": w["end"] - h["dump_at"],
                    "summary": "%s commands, %s syncs" % (format(w["commands"], ","),
                                                           format(w["syncs"], ",")),
                    "fields": [_f(None, 0, "writes", ", ".join(
                        "%s: %s" % (devs[d]["name"].split(" (")[0] if d < len(devs) else "dev %d" % d,
                                    format(n, ",")) for d, n in sorted(w["writes"].items())) or "none")],
                    "warnings": [], "payload_base": h["dump_at"]})
    regions.sort(key=lambda c: c["offset"])
    # gaps: header to first region, between regions, after the last
    pos = h["header_size"]
    for r in regions:
        if r["offset"] > pos:
            chunks.append(_gap(pos, r["offset"] - pos, raw))
        chunks.append(r)
        pos = r["offset"] + r["size"]
    if pos < len(raw):
        chunks.append(_gap(pos, len(raw) - pos, raw))
    return chunks, warns


def _gap(at, n, raw):
    zero = not any(raw[at:at + n])
    return {"id": "padding" if zero else "unwalked", "offset": at, "size": n,
            "summary": ("%d bytes of zero" % n) if zero else
                       "%d bytes no field or region accounts for" % n,
            "fields": [], "warnings": [], "payload_base": at}


def _hz(n):
    return "%.6g MHz" % (n / 1e6) if n >= 1e6 else "%d Hz" % n


def _mmss(s):
    return "%d:%05.2f" % (s // 60, s % 60)
