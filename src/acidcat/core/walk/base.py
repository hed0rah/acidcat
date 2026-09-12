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
from acidcat.core.primitives.notes import coverage


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


# How far into a vendor chunk to scan for readable text, and how many runs to
# list. A real one is a few KB.
_OPAQUE_SCAN_CAP = 64 * 1024
_OPAQUE_RUN_CAP = 12

# Chunks whose writer is known and whose layout is not. Shared, because these
# ids turn up in more than one container -- LGWV appears in both RIFF and AIFF,
# which is what made it a tool's fingerprint rather than a container quirk.
#
# LGWV and LGBM are Logic Pro. Measured rather than assumed: of the
# LGWV-carrying files in a real library that also have a bext chunk, 28 of 33
# name "Logic Pro X" or "Logic Pro" as the originator, and Apple say the same
# in their own support forum. `LG` is Logic.
VENDOR_CHUNKS = {
    "umid": "Avid/Pro Tools material identifier",
    "minf": "Avid/Pro Tools media info",
    "regn": "Avid/Pro Tools region table",
    "elm1": "Avid/Pro Tools element data",
    "elmo": "Avid/Pro Tools element data",
    "DGDA": "Digidesign analysis data",
    "LGWV": "Logic Pro",
    "LGBM": "Logic Pro",
    "SMED": "Soundminer metadata",
}


def _looks_like_a_label(text):
    """Is this run a name someone wrote, or four bytes that happened to print?

    Binary data throws off short printable runs constantly -- `W]0!`, `^3~^uL[`
    -- and listing those as "text" dresses noise up as a finding, which is the
    failure this whole reader exists to avoid. A label a vendor wrote looks
    like one: long enough to be deliberate, starting with a letter, and mostly
    letters and digits. "AnalysisSetsHdr" and "Z Drop" pass; punctuation soup
    does not.

    It is a filter, not a judgement, and it has a known blind spot: base64
    passes, because base64 is alphanumeric. A Soundminer chunk lists a handful
    of six-character runs that are encoded data rather than names. The field is
    called `text` and not `label` for that reason -- it reports what is legible,
    and does not claim to know what it means.
    """
    if len(text) < 6 or not text[:1].isalpha():
        return False
    wordish = sum(1 for c in text if c.isalnum() or c in " _-.")
    return wordish / len(text) >= 0.85


def parse_opaque(payload, what):
    """A chunk whose writer is known and whose layout is not.

    Says who wrote it, how big it is, and what is legible inside it. It does
    not guess at fields: a field map inferred from one vendor's files is a
    guess that reads like a fact, and the whole point of naming the writer is
    that a reader can go and ask them.

    The readable runs are worth listing because vendors label their own
    structures in the clear -- a Digidesign analysis block names
    "AnalysisSetsHdr" and "PacketStreamData", and a Pro Tools region table
    carries the region's name.
    """
    fields = [_f(None, 0, "bytes", f"{len(payload):,}")]
    runs, i = [], 0
    window = payload[:_OPAQUE_SCAN_CAP]
    while i < len(window):
        if 32 <= window[i] < 127:
            j = i
            while j < len(window) and 32 <= window[j] < 127:
                j += 1
            text = window[i:j].decode("ascii")
            if _looks_like_a_label(text) and text not in runs:
                runs.append(text)
            i = j
        else:
            i += 1
    for text in runs[:_OPAQUE_RUN_CAP]:
        fields.append(_f(None, 0, "text", text[:80]))
    warns = []
    if len(runs) > _OPAQUE_RUN_CAP:
        warns.append(coverage(f"listing the first {_OPAQUE_RUN_CAP} of "
                              f"{len(runs)} readable runs"))
    return f"{what}, {len(payload):,} bytes", fields, warns
