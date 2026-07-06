"""Bitwig wavetable (.wt) walker.

A `vawt` container: a 12-byte little-endian header (magic, samples per
single-cycle wave, frame count, data offset) followed by
frame_count * frame_samples signed 16-bit LE samples, frame-major (each wave's
full cycle laid end to end, no interleave). Bitwig writes one of these when you
drop a WAV into Polymer or another wavetable device. Distinct from the `BWBM`
beat-map chunk Bitwig stores inside WAV files; here `vawt` is its own container
at byte 0. No footer: the file is exactly 12 + frame_count*frame_samples*2 bytes.
"""

import os
import struct

from acidcat.core.walk.base import _f


def inspect_wt(filepath):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as fh:
        head = fh.read(12)
    warns = []
    if head[:4] != b"vawt":
        warns.append("missing 'vawt' magic")

    frame_samples = struct.unpack_from("<I", head, 4)[0] if len(head) >= 8 else 0
    frame_count = struct.unpack_from("<H", head, 8)[0] if len(head) >= 10 else 0
    data_offset = struct.unpack_from("<H", head, 10)[0] if len(head) >= 12 else 0

    fields = [
        _f(0x00, 4, "magic", "vawt"),
        _f(0x04, 4, "frame_samples", frame_samples,
           "samples per single-cycle wave"),
        _f(0x08, 2, "frame_count", frame_count, "waves stacked in the table"),
        _f(0x0A, 2, "data_offset", data_offset, "byte offset where samples begin"),
    ]
    if data_offset and data_offset != 12:
        warns.append(f"data_offset is {data_offset}, expected 12")

    total_samples = frame_count * frame_samples
    expected = 12 + total_samples * 2                  # int16 LE, frame-major
    if frame_samples and frame_count and expected != size:
        warns.append(f"size {size:,} != header-implied {expected:,} "
                     f"(12 + {frame_count} x {frame_samples} x 2)")

    header = {"id": "vawt", "offset": 0, "size": min(size, 12),
              "summary": (f"Bitwig wavetable, {frame_count} frame(s) x "
                          f"{frame_samples} samples, 16-bit"),
              "fields": fields, "warnings": [], "payload_base": 0}
    data = {"id": "samples", "offset": 12, "size": max(0, size - 12),
            "summary": f"{total_samples:,} int16 LE samples, frame-major",
            "fields": [], "warnings": [], "payload_base": 12}
    return [header, data], warns
