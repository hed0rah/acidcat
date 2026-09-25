"""VGM walker: the chip clocks, the command stream, the data blocks, the tag.

A VGM is a register log, so the walker decodes what the container proves
and what the stream's byte code declares: which chips (a non-zero clock),
how long (total samples at 44.1 kHz), where it loops, how many writes went
to each chip, and every data block -- the PCM a chip's sample RAM was
loaded with, which is a carveable region. What the writes mean is the
chip's business and is not claimed.

A .vgz is the same file inside gzip. Its chunk offsets would be into the
inflated image, which is not the file, so a .vgz is walked as one chunk
spanning the gzip with every field unpositioned and the layout described
in the summary.
"""

import os

from acidcat.core.formats import vgm as vgmmod
from acidcat.core.primitives.notes import coverage
from acidcat.core.walk.base import Unsupported as _Unsupported
from acidcat.core.walk.base import _f

# The largest real VGM measured is a few MB; 64 MB reads anything a chip
# could be fed and stops a forged length from allocating more.
_VGM_READ_CAP = 64 * 1024 * 1024
# A .vgz inflates to a VGM; the same bound on the result.
_VGM_INFLATE_CAP = _VGM_READ_CAP
# Commands decoded from the stream. A long tune has a few hundred thousand.
_VGM_COMMAND_CAP = 4_000_000
# Data blocks listed as chunks. A YM2612 tune with PCM has a handful.
_VGM_BLOCK_LIST_CAP = 64

_BLOCK_TYPES = {
    0x00: "YM2612 PCM", 0x01: "RF5C68 PCM", 0x02: "RF5C164 PCM", 0x03: "PWM PCM",
    0x04: "OKIM6258 ADPCM", 0x05: "HuC6280 PCM", 0x06: "SCSP PCM",
    0x07: "NES APU DPCM", 0x40: "YM2612 PCM (compressed)",
    0x80: "SegaPCM ROM", 0x81: "YM2608 DELTA-T ROM", 0x82: "YM2610 ADPCM ROM",
    0x83: "YM2610 DELTA-T ROM", 0x84: "YMF278B ROM", 0x85: "YMF271 ROM",
    0x86: "YMZ280B ROM", 0x87: "YMF278B RAM", 0x88: "Y8950 DELTA-T ROM",
    0x89: "MultiPCM ROM", 0x8A: "uPD7759 ROM", 0x8B: "OKIM6295 ROM",
    0x8C: "K054539 ROM", 0x8D: "C140 ROM", 0x8E: "K053260 ROM", 0x8F: "QSound ROM",
    0x90: "ES5506 ROM", 0x91: "X1-010 ROM", 0x92: "C352 ROM", 0x93: "GA20 ROM",
    0xC0: "RF5C68 RAM write", 0xC1: "RF5C164 RAM write", 0xC2: "NES APU RAM write",
    0xE0: "SCSP RAM write", 0xE1: "ES5503 RAM write",
}


def _hz(n):
    return "%.6g MHz" % (n / 1e6) if n >= 1e6 else "%d Hz" % n


def _seconds(samples):
    s = samples / vgmmod.SAMPLE_RATE
    return "%d:%05.2f" % (s // 60, s % 60)


def inspect_vgm(filepath, deep=False):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as fh:
        raw = fh.read(min(size, _VGM_READ_CAP))
    warns = []
    if size > _VGM_READ_CAP:
        warns.append(coverage("file is %d bytes; parsed the first %d"
                              % (size, _VGM_READ_CAP)))
    packed = vgmmod.is_vgz(raw)
    if packed:
        image = vgmmod.inflate(raw, _VGM_INFLATE_CAP)
        if image is None:
            raise _Unsupported("gzip that does not hold a VGM, or inflates "
                               "past %d bytes" % _VGM_INFLATE_CAP)
    elif vgmmod.is_vgm(raw):
        image = raw
    else:
        raise _Unsupported("no Vgm magic")

    h = vgmmod.parse_header(image)
    if not h["ok"]:
        raise _Unsupported(h["why"])
    end = min(h["gd3_at"] or h["eof"], len(image))
    if h["eof"] != len(image):
        warns.append("the header says the file ends at %d; it is %d bytes"
                     % (h["eof"], len(image)))
    w = vgmmod.walk_commands(image, h["data_at"], end, _VGM_COMMAND_CAP)
    if w["capped"]:
        warns.append(coverage("decoded the first %d commands" % _VGM_COMMAND_CAP))
    elif not w["ended"]:
        warns.append("the command stream has no end marker (0x66)"
                     + (": " + w["why"] if w["why"] else ""))
    elif w["end"] != end:
        warns.append("the stream ends at %d and the next region starts at %d"
                     % (w["end"], end))
    if h["loop_at"] is not None and not h["data_at"] <= h["loop_at"] < w["end"]:
        warns.append("the loop point 0x%X is outside the command stream" % h["loop_at"])
    if not w["capped"] and w["ended"] and w["waits"] != h["total_samples"]:
        warns.append("the header says %d samples and the waits add up to %d"
                     % (h["total_samples"], w["waits"]))
    clocked = {c["name"] for c in h["chips"]}
    for chip in sorted(w["writes"]):
        if chip not in clocked:
            warns.append("%d writes to %s, whose clock is zero" % (w["writes"][chip], chip))

    header_fields = _header_fields(h, w)
    gd3 = vgmmod.parse_gd3(image, h["gd3_at"]) if h["gd3_at"] is not None else None
    if h["gd3_at"] is not None and not gd3["ok"]:
        warns.append("the header points at a GD3 tag and there is none at 0x%X" % h["gd3_at"])
    title = _title(gd3)

    if packed:
        # one chunk, the gzip; offsets inside the image are not file offsets
        fields = ([_f(None, 0, "container", "gzip",
                      "a .vgz; %s bytes inflate to %s" % (format(size, ","),
                                                         format(len(image), ",")))]
                  + [dict(f, off=None, len=0) for f in header_fields]
                  + _stream_fields(w, unpositioned=True))
        if gd3 and gd3["ok"]:
            fields += _gd3_fields(gd3)
        return [{"id": "VGZ", "offset": 0, "size": size,
                 "summary": "VGM %s in gzip, %s%s" % (h["version_text"],
                                                        _chips_text(h), title),
                 "fields": fields, "warnings": [], "payload_base": 0}], warns

    chunks = [{"id": "header", "offset": 0, "size": h["header_size"],
               "summary": "VGM %s, %s%s" % (h["version_text"], _chips_text(h), title),
               "fields": header_fields + _stream_fields(w, unpositioned=False),
               "warnings": [], "payload_base": 0}]
    # the stream is a sequence: runs of commands, and data blocks between
    # them. Each is its own chunk, so the blocks are carveable and the
    # commands around them are accounted for, and nothing nests.
    pos = h["data_at"]
    blocks = w["blocks"]
    if len(blocks) > _VGM_BLOCK_LIST_CAP:
        warns.append(coverage("listing the first %d of %d data blocks"
                              % (_VGM_BLOCK_LIST_CAP, len(blocks))))
        blocks = blocks[:_VGM_BLOCK_LIST_CAP]
    run = 0
    for i, (at, btype, blen) in enumerate(blocks):
        if at - 7 > pos:
            chunks.append(_commands(run, pos, at - 7 - pos))
            run += 1
        kind = _BLOCK_TYPES.get(btype, "type 0x%02X" % btype)
        chunks.append({
            "id": "block[%d]" % i, "offset": at - 7, "size": 7 + blen,
            "summary": "%s, %s bytes" % (kind, format(blen, ",")),
            # the 7-byte block header sits before the payload this chunk
            # reads from, so its fields have negative offsets from it
            "fields": [_f(-5, 1, "type", "0x%02X" % btype, kind),
                       _f(-4, 4, "length", blen, "")],
            "warnings": [], "payload_base": at, "payload_len": blen})
        pos = at + blen
    if w["end"] > pos:
        chunks.append(_commands(run, pos, w["end"] - pos))
    if w["end"] < end:
        chunks.append({"id": "unwalked", "offset": w["end"], "size": end - w["end"],
                       "summary": "%d bytes between the stream's end and the next region"
                                  % (end - w["end"]),
                       "fields": [], "warnings": [], "payload_base": w["end"]})
    if gd3 and gd3["ok"]:
        chunks.append({"id": "Gd3", "offset": h["gd3_at"], "size": gd3["size"],
                       "summary": "GD3 tag" + title,
                       # the 12-byte tag header precedes the UTF-16 payload
                       "fields": [_f(-12, 4, "magic", "Gd3 "),
                                  _f(-8, 4, "version", "0x%08X" % gd3["version"]),
                                  _f(-4, 4, "length", gd3["length"], "bytes of UTF-16")]
                                 + _gd3_fields(gd3),
                       "warnings": [], "payload_base": h["gd3_at"] + 12,
                       "payload_len": gd3["length"]})
    tail = len(image) - (h["gd3_at"] + gd3["size"] if gd3 and gd3["ok"] else end)
    if tail > 0:
        at = len(image) - tail
        chunks.append({"id": "trailing", "offset": at, "size": tail,
                       "summary": "%d bytes after the last region" % tail,
                       "fields": [], "warnings": [], "payload_base": at})
    return chunks, warns


def _commands(n, at, size):
    return {"id": "commands" if n == 0 else "commands[%d]" % n,
            "offset": at, "size": size,
            "summary": "%s bytes of chip writes and waits" % format(size, ","),
            "fields": [], "warnings": [], "payload_base": at}


def _chips_text(h):
    names = [c["name"] + (" x2" if c["dual"] else "") for c in h["chips"]]
    return ", ".join(names) if names else "no chip clocked"


def _title(gd3):
    if not gd3 or not gd3["ok"]:
        return ""
    f = gd3["fields"]
    t = f.get("title") or f.get("title_jp")
    g = f.get("game") or f.get("game_jp")
    if not t and not g:
        return ""
    return " -- " + (t or "?") + (" (" + g + ")" if g else "")


def _header_fields(h, w):
    fields = [
        _f(0x00, 4, "magic", "Vgm "),
        _f(0x04, 4, "eof", h["eof"], "absolute; stored relative to 0x04"),
        _f(0x08, 4, "version", h["version_text"], "BCD"),
        _f(0x18, 4, "total_samples", h["total_samples"],
           "%s at 44,100 Hz" % _seconds(h["total_samples"])),
    ]
    if h["loop_at"] is not None:
        fields.append(_f(0x1C, 4, "loop_at", "0x%X" % h["loop_at"],
                         "absolute; stored relative to 0x1C"))
        fields.append(_f(0x20, 4, "loop_samples", h["loop_samples"],
                         _seconds(h["loop_samples"])))
    if h["rate"]:
        fields.append(_f(0x24, 4, "rate", "%d Hz" % h["rate"],
                         "the original machine's frame rate"))
    if h["version"] >= 0x150:
        fields.append(_f(0x34, 4, "data_at", "0x%X" % h["data_at"],
                         "absolute; stored relative to 0x34"))
    for c in h["chips"]:
        fields.append(_f(c["offset"], 4, c["name"], _hz(c["clock"]),
                         "two of them" if c["dual"] else ""))
    return fields


def _stream_fields(w, unpositioned):
    fields = [_f(None, 0, "commands", format(w["commands"], ",")),
              _f(None, 0, "waits", "%s samples, %s" % (format(w["waits"], ","),
                                                        _seconds(w["waits"])))]
    for chip, n in sorted(w["writes"].items(), key=lambda kv: -kv[1]):
        fields.append(_f(None, 0, "writes_" + chip.replace(" ", "_"), format(n, ",")))
    if unpositioned and w["blocks"]:
        fields.append(_f(None, 0, "data_blocks", len(w["blocks"]),
                         ", ".join(_BLOCK_TYPES.get(t, "0x%02X" % t)
                                   for _a, t, _n in w["blocks"][:8])))
    return fields


def _gd3_fields(gd3):
    return [_f(None, 0, k, v[:200]) for k, v in gd3["fields"].items()]
