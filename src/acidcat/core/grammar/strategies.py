"""Container strategies: how to find the regions of a file.

A strategy is a LENIENT, walker-equivalent traversal -- the deliberate
opposite of core/structure, which is the strict write/repair model of the
same grammar. Where structure refuses an EOF-overrunning chunk (parks it in
tail), clamps payloads, probes for unpadded writers, and raises on a
sub-12-byte file, a strategy does what the walkers do: yields the lying
chunk with its DECLARED size plus a warning, skips the pad unconditionally,
streams payloads capped at _PAYLOAD_CAP, and degrades to warnings, never
raises. The walkers are the equivalence oracle; the strict/lenient pair is
the raw material for differential parsing later.
"""

import os
import struct
from collections import namedtuple

from acidcat.core.walk.base import _PAYLOAD_CAP

# size is the DECLARED chunk size (never clamped); payload is capped at
# _PAYLOAD_CAP and may come up short at EOF; payload_base is the absolute
# offset field offsets are measured from (iff: offset + 8).
Region = namedtuple("Region", "id offset payload_base payload size")


class IffStrategy:
    """RIFF/WAVE top-level chunks with riff.iter_chunks traversal semantics.

    Traverses ONLY RIFF..WAVE: RF64 sizes live in ds64 and belong to its own
    walker, and AIFF is a big-endian FORM for a later strategy variant. LIST
    is yielded flat (no recursion), exactly as the walker sees it.
    """

    def label(self, filepath):
        with open(filepath, "rb") as f:
            hdr = f.read(12)
        if len(hdr) >= 12 and hdr[0:4] == b"RIFF" and hdr[8:12] == b"WAVE":
            return "RIFF/WAVE"
        return None

    def regions(self, filepath):
        file_size = os.path.getsize(filepath)
        regions, warns = [], []
        with open(filepath, "rb") as f:
            hdr = f.read(12)
            if len(hdr) < 12:
                return [], [f"file is {len(hdr)} bytes; a RIFF header needs 12"]
            if hdr[0:4] != b"RIFF" or hdr[8:12] != b"WAVE":
                return [], ["not a RIFF/WAVE container"]
            riff_size = struct.unpack("<I", hdr[4:8])[0]
            if riff_size + 8 != file_size:
                warns.append(
                    f"riff_size says {riff_size + 8:,} bytes, file is "
                    f"{file_size:,} ({file_size - riff_size - 8:+,})"
                )
            pos = 12
            while pos + 8 <= file_size:
                f.seek(pos)
                ch = f.read(8)
                if len(ch) < 8:
                    break
                cid = ch[0:4].decode("ascii", errors="ignore")
                size = struct.unpack("<I", ch[4:8])[0]
                avail = max(0, file_size - pos - 8)
                if size > avail:
                    warns.append(
                        f"chunk {cid!r} at 0x{pos:08x} claims {size:,} bytes "
                        f"but only {avail:,} remain"
                    )
                payload = f.read(min(size, _PAYLOAD_CAP))
                regions.append(Region(cid, pos, pos + 8, payload, size))
                pos += 8 + size
                if size % 2 == 1:
                    pos += 1  # word alignment, unconditional like the walker
        return regions, warns


STRATEGIES = {"iff": IffStrategy()}
