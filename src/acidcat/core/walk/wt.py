"""Vember Audio / Surge wavetable (.wt) walker.

A `vawt` container: a 12-byte little-endian header (magic, samples per
single-cycle wave, wave count, flags) followed by the waves laid end to end,
frame-major, no interleave. Data always begins at byte 12; there is no offset
field and no footer.

The flags word is the only thing that says how wide a sample is. With bit 2
clear the payload is float32, which is what Surge writes and what 151 of 152
real specimens on hand use; with it set the payload is int16. Reading the
width from anything but this bit puts the file size check off by 2x and
reports every float32 table as corrupt.
"""

import struct

from acidcat.core.infra.findings import defect
from acidcat.core.walk.base import _f, _open, _size

WTF_IS_SAMPLE = 0x01        # a one-shot sample rather than a wavetable
WTF_LOOP_SAMPLE = 0x02      # that sample loops
WTF_INT16 = 0x04            # payload is int16 LE; clear means float32 LE
WTF_INT16_IS_16 = 0x08      # int16 full scale is +/-32768, not +/-16384
WTF_HAS_METADATA = 0x10     # null-terminated <wtmeta> XML follows the samples

_FLAG_NAMES = ((WTF_IS_SAMPLE, "is_sample"), (WTF_LOOP_SAMPLE, "loop_sample"),
               (WTF_INT16, "int16"), (WTF_INT16_IS_16, "int16_is_16"),
               (WTF_HAS_METADATA, "has_metadata"))


def _describe(flags):
    named = [name for bit, name in _FLAG_NAMES if flags & bit]
    unknown = flags & ~0x0F
    if unknown:
        named.append(f"unknown 0x{unknown:04X}")
    return "+".join(named) if named else "none"


def inspect_wt(filepath):
    size = _size(filepath)
    with _open(filepath) as fh:
        head = fh.read(12)
    warns = []
    if head[:4] != b"vawt":
        warns.append(defect("magic.mismatch", "missing 'vawt' magic"))

    frame_samples = struct.unpack_from("<I", head, 4)[0] if len(head) >= 8 else 0
    frame_count = struct.unpack_from("<H", head, 8)[0] if len(head) >= 10 else 0
    flags = struct.unpack_from("<H", head, 10)[0] if len(head) >= 12 else 0

    width = 2 if flags & WTF_INT16 else 4
    depth = "16-bit" if width == 2 else "32-bit float"

    fields = [
        _f(0x00, 4, "magic", "vawt"),
        _f(0x04, 4, "frame_samples", frame_samples,
           "samples per single-cycle wave"),
        _f(0x08, 2, "frame_count", frame_count, "waves stacked in the table"),
        _f(0x0A, 2, "flags", f"0x{flags:04X}", _describe(flags)),
    ]

    total_samples = frame_count * frame_samples
    payload = total_samples * width
    expected = 12 + payload
    has_meta = bool(flags & WTF_HAS_METADATA)
    if frame_samples and frame_count:
        # with the metadata bit set an XML trailer follows, so the payload is a
        # floor rather than the file size
        short = size < expected
        if short or (not has_meta and size != expected):
            warns.append(defect("count.mismatch",
                                f"size {size:,} != header-implied {expected:,} "
                                f"(12 + {frame_count} x {frame_samples} x {width})"))

    header = {"id": "vawt", "offset": 0, "size": min(size, 12),
              "summary": (f"wavetable, {frame_count} frame(s) x "
                          f"{frame_samples} samples, {depth}"),
              "fields": fields, "warnings": [], "payload_base": 0}
    data = {"id": "samples", "offset": 12,
            "size": min(payload, max(0, size - 12)) if payload else max(0, size - 12),
            "summary": f"{total_samples:,} {depth} samples, frame-major",
            "fields": [], "warnings": [], "payload_base": 12}
    chunks = [header, data]
    if has_meta and size > expected:
        chunks.append({"id": "wtmeta", "offset": expected, "size": size - expected,
                       "summary": "null-terminated <wtmeta> XML trailer",
                       "fields": [], "warnings": [], "payload_base": expected})
    return chunks, warns
