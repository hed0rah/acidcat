"""SNDH walker: the three entry branches, the tag header, the player.

A bare SNDH is laid out as it sits: twelve bytes of entry branches, the
tags from 'SNDH' to 'HDNS' (or to where they stop), and the 68000 player
with its data, which is code and is not walked further. A packed one
(almost all of them) is a Pack-Ice stream: the 12-byte ICE header is a
real region and walked field by field, and the SNDH inside is described
with its fields unpositioned, since offsets into the unpacked image are
not file offsets. The unpacked image must come out at exactly the length
the ICE header states, with 'SNDH' where it belongs, before anything in it
is reported.
"""


from acidcat.core.codecs import ice
from acidcat.core.formats import sndh as sndhmod
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import Unsupported as _Unsupported, _open, _size
from acidcat.core.walk.base import _f

# The largest real SNDH measured unpacks to a few hundred KB (the DMA ones
# carry samples); 16 MB reads any of them and stops a forged length early.
_SNDH_READ_CAP = 16 * 1024 * 1024
_SNDH_UNPACK_CAP = _SNDH_READ_CAP


def inspect_sndh(filepath, deep=False):
    size = _size(filepath)
    with _open(filepath) as fh:
        raw = fh.read(min(size, _SNDH_READ_CAP))
    warns = []
    if size > _SNDH_READ_CAP:
        warns.append(hit("read_bytes", _SNDH_READ_CAP, size,
                         "file is %d bytes; parsed the first %d" % (size, _SNDH_READ_CAP)))
    if ice.is_ice(raw):
        return _packed(raw, warns)
    s = sndhmod.parse(raw)
    if not s["ok"]:
        raise _Unsupported(s["why"])
    warns.extend(s["warnings"])
    return _bare(raw, s), warns


def _mmss(sec):
    return "%d:%02d" % (sec // 60, sec % 60)


def _tag_field(tag, off, n, value, s, positioned, base):
    at = (off - base) if positioned else None
    ln = n if positioned else 0
    if tag in ("TITL", "COMM", "RIPP", "CONV", "YEAR"):
        name = {"TITL": "title", "COMM": "composer", "RIPP": "ripper",
                "CONV": "converter", "YEAR": "year"}[tag]
        return _f(at, ln, name, value)
    if tag == "FLAG":
        letters = value[1:] if value.startswith("~") else value
        known = [sndhmod.FLAG_NAMES.get(c) for c in letters]
        return _f(at, ln, "flags", value, ", ".join(k for k in known if k)
                  + ("" if all(known) else "; unknown letters present"))
    if tag == "##":
        return _f(at, ln, "subtunes", value)
    if tag in ("!#", "#!"):
        return _f(at, ln, "default_subtune", value,
                  "spelled '!#', as real files do" if tag == "!#" else "")
    if tag in ("TA", "TB", "TC", "TD", "!V"):
        return _f(at, ln, "replay", "%s, %d Hz" % (s["timer"][0] if s["timer"] else tag, value)
                  if s["timer"] else "%d Hz" % value)
    if tag == "TIME":
        return _f(at, ln, "lengths", ", ".join(_mmss(t) for t in value),
                  "seconds per subtune; 0 is unknown or endless")
    if tag == "FRMS":
        hz = s["timer"][1] if s["timer"] else 50
        return _f(at, ln, "frames", ", ".join(
            ("%d (%s)" % (fr, _mmss(fr // hz)) if fr else "0 (loops)") for fr in value),
            "frames per subtune at %d Hz" % hz)
    if tag in ("!#SN", "#!SN"):
        return _f(at, ln, "subtune_names", "; ".join(n if n is not None else "?" for n in value),
                  "offsets from the tag's start")
    if tag == "HDNS":
        return _f(at, ln, "end", "HDNS")
    return _f(at, ln, tag, value)


def _entry_fields(s, positioned, image_len):
    out = []
    for i, (name, target, kind) in enumerate(s["entries"]):
        at = 4 * i
        if target is None:
            out.append(_f(at if positioned else None, 4 if positioned else 0, name,
                          "not a branch", kind))
        elif kind == "rts":
            out.append(_f(at if positioned else None, 2 if positioned else 0, name,
                          "rts", "nothing to do"))
        else:
            xref = target if positioned and 0 <= target < image_len else None
            out.append(_f(at if positioned else None, 4 if positioned else 0, name,
                          "0x%X" % target, kind, xref=xref))
    return out


def _no_hdns(s):
    if s["hdns"]:
        return []
    return [_f(None, 0, "end", "no HDNS",
               "a header from before SNDH v2; the player starts at 0x%X" % s["header_end"])]


def _title(s):
    t = s["text"].get("title", "").strip()
    c = s["text"].get("composer", "").strip()
    if t and c:
        return " -- %s (%s)" % (t, c)
    return (" -- " + (t or c)) if (t or c) else ""


def _summary(s):
    parts = ["SNDH"]
    if s["subtunes"] > 1:
        parts.append("%d subtunes" % s["subtunes"])
    if s["timer"]:
        parts.append("%s %d Hz" % s["timer"])
    return ", ".join(parts)


def _chunk(cid, off, n, summary, fields=(), base=None):
    return {"id": cid, "offset": off, "size": n, "summary": summary,
            "fields": list(fields), "warnings": [],
            "payload_base": off if base is None else base}


def _bare(raw, s):
    end = s["header_end"]
    chunks = [
        _chunk("entries", 0, 12, "init, exit, play", _entry_fields(s, True, len(raw)), base=0),
        _chunk("header", 12, end - 12, _summary(s) + _title(s),
               [_f(0, 4, "tag", "SNDH")]
               + [_tag_field(t, o, n, v, s, True, 12) for t, o, n, v in s["tags"]]
               + _no_hdns(s)),
    ]
    if end < len(raw):
        chunks.append(_chunk("player", end, len(raw) - end,
                             "the 68000 player and its data, %s bytes" % format(len(raw) - end, ",")))
    return chunks


def _packed(raw, warns):
    magic, packed, unpacked = ice.header(raw)
    try:
        image = ice.unpack(raw, _SNDH_UNPACK_CAP)
    except ice.IceError as e:
        raise _Unsupported("Pack-Ice: %s" % e)
    s = sndhmod.parse(image)
    if not s["ok"]:
        raise _Unsupported("Pack-Ice data that is not SNDH: %s" % s["why"])
    warns.extend(s["warnings"])
    chunks = [_chunk("ice_header", 0, ice.HEADER, "Pack-Ice %s, %s bytes unpack to %s"
                     % (magic, format(packed, ","), format(unpacked, ",")),
                     [_f(0, 4, "magic", magic),
                      _f(4, 4, "packed_size", packed, "including this header"),
                      _f(8, 4, "unpacked_size", unpacked, "matches the unpacked image")],
                     base=0)]
    fields = ([_f(None, 0, "player", "%s bytes after the header" % format(len(image) - s["header_end"], ","))]
              + _entry_fields(s, False, len(image))
              + [_tag_field(t, o, n, v, s, False, 0) for t, o, n, v in s["tags"]]
              + _no_hdns(s))
    chunks.append(_chunk("ice", ice.HEADER, packed - ice.HEADER, _summary(s) + _title(s), fields))
    if packed < len(raw):
        tail = raw[packed:]
        zero = not any(tail)
        chunks.append(_chunk("padding" if zero else "unwalked", packed, len(tail),
                             ("%d bytes of zero" if zero else "%d bytes after the packed data")
                             % len(tail)))
    return chunks, warns
