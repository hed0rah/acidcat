"""YM walker: ST-Sound's YM2149 register dumps, bare or in their LHA wrapper.

A bare YM is laid out region by region: the header, each digidrum, the
three strings, the register frames, the End! marker. A packed one (almost
all of them) is an LHA member: its header is a real region of the file and
is walked field by field, and the YM inside is described with its fields
unpositioned, since offsets into the unpacked image are not file offsets.
The unpacked body must match the member's CRC-16 before anything in it
is reported.
"""

import os
import time

from acidcat.core.codecs import lha
from acidcat.core.formats import ym as ymmod
from acidcat.core.primitives.notes import coverage
from acidcat.core.walk.base import Unsupported as _Unsupported
from acidcat.core.walk.base import _f

# The largest real YM measured unpacks to well under 1 MB (a six-minute
# YM5 is 288 KB); 16 MB reads any tune and stops a forged size early.
_YM_READ_CAP = 16 * 1024 * 1024
_YM_UNPACK_CAP = _YM_READ_CAP
# digidrums listed as their own chunks
_YM_DRUM_LIST_CAP = 64

_VOICES = "ABC"


def inspect_ym(filepath, deep=False):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as fh:
        raw = fh.read(min(size, _YM_READ_CAP))
    warns = []
    if size > _YM_READ_CAP:
        warns.append(coverage("file is %d bytes; parsed the first %d" % (size, _YM_READ_CAP)))
    if lha.is_lha(raw):
        return _packed(raw, warns)
    y = ymmod.parse(raw)
    if not y["ok"]:
        raise _Unsupported(y["why"])
    warns.extend(y["warnings"])
    if len(y["drums"]) > _YM_DRUM_LIST_CAP:
        warns.append(coverage("listing the first %d of %d digidrums"
                              % (_YM_DRUM_LIST_CAP, len(y["drums"]))))
    return _bare(raw, y), warns


# -- the tune, as fields -------------------------------------------------------

def _voices(image, y):
    """Which voices ever sound: a volume register (r8-r10) non-zero in some
    frame. Volume 16 and up means the envelope drives it."""
    n, regs = y["frames"], y["registers"]
    at = y["data_at"]
    live = []
    for v in range(3):
        k = 8 + v
        if y["interleaved"]:
            col = image[at + k * n:at + (k + 1) * n]
        else:
            col = image[at + k:at + n * regs:regs]
        if any(b & 0x1F for b in col):
            live.append(_VOICES[v])
    return live


def _tune_fields(image, y, positioned):
    def at(off):
        return off if positioned else None

    def ln(n):
        return n if positioned else 0

    secs = y["frames"] / y["rate"] if y["rate"] else 0
    f = [_f(at(0), ln(4), "magic", y["magic"])]
    if y["registers"] == 16:
        f += [
            _f(at(4), ln(8), "check", "LeOnArD!"),
            _f(at(12), ln(4), "frames", format(y["frames"], ","),
               "%d:%05.2f at %d Hz" % (secs // 60, secs % 60, y["rate"])),
            _f(at(16), ln(4), "attributes", "0x%08X" % y["attributes"],
               "interleaved" if y["interleaved"] else "frame by frame"),
            _f(at(20), ln(2), "digidrums", len(y["drums"])),
            _f(at(22), ln(4), "clock", "%d Hz" % y["clock"],
               {2000000: "Atari ST", 1773400: "ZX Spectrum",
                1000000: "Amstrad CPC"}.get(y["clock"], "")),
            _f(at(26), ln(2), "rate", "%d Hz" % y["rate"]),
            _f(at(28), ln(4), "loop_frame", y["loop"]),
            _f(at(32), ln(2), "extra_data", y["extra_len"], "bytes to skip"),
        ]
    else:
        f += [
            _f(None, 0, "frames", format(y["frames"], ","),
               "%d:%05.2f at 50 Hz; the count is what fits" % (secs // 60, secs % 60)),
            _f(None, 0, "layout", "14 registers per frame, interleaved"),
        ]
        if y["magic"] == "YM3b":
            f.append(_f(at(y["loop_at"]), ln(4), "loop_frame", y["loop"]))
    f.append(_f(None, 0, "voices", ", ".join(_voices(image, y)) or "none",
                "those whose volume is ever non-zero"))
    return f


def _string_fields(y, positioned):
    return [_f(off if positioned else None, n if positioned else 0, name, s)
            for name, off, n, s in y["strings"]]


def _title(y):
    s = {name: text for name, _o, _n, text in y["strings"]}
    t, a = s.get("title", ""), s.get("author", "")
    if t and a:
        return " -- %s (%s)" % (t, a)
    return (" -- " + (t or a)) if (t or a) else ""


def _summary(y):
    secs = y["frames"] / y["rate"] if y["rate"] else 0
    return "%s, %s frames, %d:%05.2f" % (y["magic"], format(y["frames"], ","),
                                         secs // 60, secs % 60)


# -- bare ----------------------------------------------------------------------

def _chunk(cid, off, n, summary, fields=(), base=None):
    return {"id": cid, "offset": off, "size": n, "summary": summary,
            "fields": list(fields), "warnings": [],
            "payload_base": off if base is None else base}


def _bare(raw, y):
    head = 4 if y["registers"] == 14 else 34 + y["extra_len"]
    chunks = [_chunk("header", 0, head, _summary(y) + _title(y),
                     [f for f in _tune_fields(raw, y, True)
                      if f["off"] is None or f["off"] < head], base=0)]
    drums = y["drums"]
    for i, (off, n) in enumerate(drums[:_YM_DRUM_LIST_CAP]):
        chunks.append(_chunk("digidrum_%d" % i, off, 4 + n,
                             "digidrum %d, %d bytes of 8-bit sample" % (i, n),
                             [_f(0, 4, "size", n)]))
    if len(drums) > _YM_DRUM_LIST_CAP:
        first = drums[_YM_DRUM_LIST_CAP][0]
        last = drums[-1][0] + 4 + drums[-1][1]
        chunks.append(_chunk("digidrums", first, last - first,
                             "%d more digidrums" % (len(drums) - _YM_DRUM_LIST_CAP)))
    if y["strings"]:
        s_at = y["strings"][0][1]
        s_end = y["data_at"]
        chunks.append(_chunk("strings", s_at, s_end - s_at, "name, author, comment",
                             [dict(f, off=f["off"] - s_at) for f in _string_fields(y, True)]))
    chunks.append(_chunk("registers", y["data_at"], y["data_len"],
                         "%s frames of %d registers, %s" % (
                             format(y["frames"], ","), y["registers"],
                             "interleaved" if y["interleaved"] else "frame by frame")))
    pos = y["data_at"] + y["data_len"]
    if y.get("loop_at") is not None:
        chunks.append(_chunk("loop", y["loop_at"], 4, "loop frame %d" % y["loop"],
                             [_f(0, 4, "loop_frame", y["loop"])]))
        pos = y["loop_at"] + 4
    if y.get("end_at") is not None:
        chunks.append(_chunk("end", y["end_at"], 4, "End! marker", [_f(0, 4, "marker", "End!")]))
        pos = y["end_at"] + 4
    if pos < len(raw):
        tail = raw[pos:]
        chunks.append(_chunk("padding" if not any(tail) else "unwalked", pos, len(tail),
                             "%d bytes of zero" % len(tail) if not any(tail) else
                             "%d bytes after the tune" % len(tail)))
    return chunks


# -- packed --------------------------------------------------------------------

def _dos_time(stamp):
    d, t = stamp >> 16, stamp & 0xFFFF
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", (
            1980 + (d >> 9), (d >> 5) & 15, d & 31, t >> 11, (t >> 5) & 63,
            (t & 31) * 2, 0, 1, -1))
    except (ValueError, OverflowError):
        return "0x%08X" % stamp


def _packed(raw, warns):
    try:
        h = lha.parse_header(raw)
    except lha.LhaError as e:
        raise _Unsupported("LHA: %s" % e)
    try:
        _h, image = lha.unpack(raw, cap=_YM_UNPACK_CAP)
    except lha.LhaError as e:
        raise _Unsupported("LHA member %r: %s" % (h.get("name", ""), e))
    y = ymmod.parse(image)
    if not y["ok"]:
        raise _Unsupported("an LHA member holding %s" % y["why"]
                           if image[:4] in ymmod.OTHER_TYPES else
                           "an LHA member that is not a YM tune: %s" % y["why"])
    warns.extend(y["warnings"])
    if not h["checksum_ok"]:
        warns.append("the LHA header checksum does not match its bytes")
    nlen = len(h["name"])
    hfields = [
        _f(0, 1, "header_size", h["header_len"] - 2),
        _f(1, 1, "header_sum", "0x%02X" % raw[1],
           "matches" if h["checksum_ok"] else "does not match"),
        _f(2, 5, "method", h["method"],
           {"-lh5-": "LZSS, 8 KB window, static Huffman", "-lh0-": "stored"}.get(h["method"], "")),
        _f(7, 4, "packed_size", h["packed"]),
        _f(11, 4, "size", h["size"]),
        _f(15, 4, "time", _dos_time(h["stamp"]), "DOS date and time"),
        _f(19, 1, "attribute", "0x%02X" % h["attr"]),
        _f(20, 1, "level", h["level"]),
        _f(21, 1, "name_length", nlen),
        _f(22, nlen, "name", h["name"]),
        _f(22 + nlen, 2, "crc16", "0x%04X" % h["crc"], "of the unpacked tune; matches"),
    ]
    chunks = [_chunk("lha_header", 0, h["header_len"],
                     "LHA level-0 member %r, %s" % (h["name"], h["method"]), hfields, base=0)]
    body_fields = ([_f(None, 0, "unpacked", "%s bytes from %s"
                       % (format(len(image), ","), format(h["packed"], ",")))]
                   + _tune_fields(image, y, False) + _string_fields(y, False))
    chunks.append(_chunk(h["method"].strip("-"), h["body"], h["packed"],
                         _summary(y) + _title(y), body_fields))
    end = h["body"] + h["packed"]
    if end < len(raw):
        tail = raw[end:]
        if tail == b"\x00":
            chunks.append(_chunk("archive_end", end, 1, "the zero byte that ends an LHA archive"))
        else:
            chunks.append(_chunk("unwalked", end, len(tail),
                                 "%d bytes after the member" % len(tail)))
    return chunks, warns
