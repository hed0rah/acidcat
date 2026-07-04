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


class Unsupported(Exception):
    """A file the walkers cannot structurally decode; message is user-facing."""

_PAYLOAD_CAP = 65536
_FRAME_LISTING_CAP = 100000  # per-element rows kept for the --frames deep dump
# ID3v2 tags routinely carry embedded cover art far larger than the generic
# payload cap, so enumerating their frames needs a bigger read. bounded so a
# forged synchsafe tag size cannot force an unbounded allocation.
_ID3_READ_CAP = 16 * 1024 * 1024


# ── field helpers ──────────────────────────────────────────────────
# a field is a dict: off (relative to payload), len, name, value, note


def _f(off, length, name, value, note=""):
    return {"off": off, "len": length, "name": name, "value": value, "note": note}


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
