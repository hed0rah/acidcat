"""Shared contract of the format walkers.

Every walker returns (chunks, file_warnings) where each chunk is a dict:
id, offset, size, summary, fields, warnings, and optionally payload_base
(the absolute offset field offsets are measured from, when it is not the
default offset+8) and rows (a per-element listing for --frames).

A field is a dict: off (relative to the payload base), len, name, value,
note. Build one with ``_f``. The helpers keep their historical
underscore names from commands/inspect.py so the move stays mechanical.
"""

import struct

# single source for the per-chunk payload read cap: the walkers and the grammar
# strategy share riff.PAYLOAD_CAP so a bump cannot diverge their payload lengths.
from acidcat.core.formats.riff import PAYLOAD_CAP as _PAYLOAD_CAP


class Unsupported(Exception):
    """A file the walkers cannot structurally decode; message is user-facing."""

_FRAME_LISTING_CAP = 100000  # per-element rows kept for the --frames deep dump
# ID3v2 tags routinely carry embedded cover art far larger than the generic
# payload cap, so enumerating their frames needs a bigger read. bounded so a
# forged synchsafe tag size cannot force an unbounded allocation.
_ID3_READ_CAP = 16 * 1024 * 1024


# ── field helpers ──────────────────────────────────────────────────
# a field is a dict: off (relative to payload), len, name, value, note.
# optional: enc (a struct format string, e.g. "<I", describing the on-disk
# layout) and raw (the numeric value to re-encode with enc, when `value` is a
# formatted display string like "1,234"). These let an editor re-encode a new
# value byte-for-byte; a consumer must still VERIFY enc/raw against the actual
# bytes before trusting them (a wrong annotation must never write blind).


def _f(off, length, name, value, note="", enc=None, raw=None, xref=None):
    d = {"off": off, "len": length, "name": name, "value": value, "note": note}
    if enc is not None:
        d["enc"] = enc
    if raw is not None:
        d["raw"] = raw
    if xref is not None:
        # this field is a pointer: `xref` is the absolute file offset it points
        # at, so the TUI can follow it and flag a dangling (out-of-bounds) one
        d["xref"] = xref
    return d


def _u16(b, off):
    return struct.unpack_from("<H", b, off)[0]


def _u32(b, off):
    return struct.unpack_from("<I", b, off)[0]


def _f32(b, off):
    return struct.unpack_from("<f", b, off)[0]


def _dtext(raw):
    """Decode metadata text: UTF-8, falling back to latin-1. Modern DAWs (and
    bandcamp) write RIFF/AIFF text as UTF-8; ascii/errors='replace' silently
    destroyed non-Latin tags (Korean, CJK, the whole non-ASCII world) into
    U+FFFD. latin-1 never raises, so a real cp1252 tag still round-trips."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _cstr(b, off, length):
    return _dtext(b[off:off + length].split(b"\x00")[0]).strip()


def _flag_names(value, table):
    names = [name for bit, name in table if value & bit]
    return ", ".join(names) if names else "none"


def _bu16(b, off):
    return struct.unpack_from(">H", b, off)[0]


def _bu32(b, off):
    return struct.unpack_from(">I", b, off)[0]


# How far into a non-zero padding block to look for readable text. The finding
# is that the padding is not zero, which is counted over the whole block; this
# only bounds the preview.
_PADDING_TEXT_CAP = 4096


def parse_padding(payload):
    """A padding block, in whatever container declares one.

    RIFF spells it JUNK, FLLR or PAD; FLAC gives it block type 1. Same
    structure and the same question about it, so the same reader.

    Named rather than hex-dumped, and CHECKED rather than assumed: padding is
    supposed to be zero, and padding that is not is space that was overwritten
    in place with something shorter. What is left is the tail of whatever used
    to be there, which is a find rather than filler.
    """
    fields, warns = [], []
    nonzero = sum(1 for x in payload if x)
    fields.append(_f(None, 0, "bytes", f"{len(payload):,}"))
    if not payload:
        return "empty", fields, warns
    if not nonzero:
        return f"padding, {len(payload):,} zero bytes", fields, warns
    fields.append(_f(None, 0, "non_zero", f"{nonzero:,}",
                     "padding is written zero; these are not"))
    fields.append(_f(0x00, min(len(payload), 16), "first_bytes",
                     payload[:16].hex(" ")))
    text = _dtext(payload[:_PADDING_TEXT_CAP])
    readable = "".join(c for c in text if c.isprintable())
    if len(readable) >= 8:
        fields.append(_f(None, 0, "readable", readable[:120]))
    warns.append(f"{nonzero:,} of {len(payload):,} padding bytes are not zero; "
                 f"this block may hold the tail of something overwritten in "
                 f"place")
    return (f"padding, {len(payload):,} bytes, {nonzero:,} NOT zero",
            fields, warns)
