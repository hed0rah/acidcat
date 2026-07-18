"""Container strategies: how to find the regions of a file.

A strategy is a LENIENT, walker-equivalent traversal -- the deliberate
opposite of core/structure, which is the strict write/repair model of the
same grammar. Where structure refuses an EOF-overrunning chunk (parks it in
tail), clamps payloads, probes for unpadded writers, and raises on a
sub-12-byte file, a strategy does what the walkers do: yields the lying
chunk with its DECLARED size plus a warning, skips the pad unconditionally,
and degrades to warnings, never raises. The walkers are the equivalence
oracle; the strict/lenient pair is the raw material for differential parsing
later.

The iff strategy owns no traversal loop of its own: it delegates to
core/riff.iter_spans, the single lenient RIFF/WAVE traversal that the walker
(via iter_chunks) also enumerates through, so the chunk-walk arithmetic can
never drift between the two. Span lives in core/riff for the same reason.
"""

import os

from acidcat.core import riff
from acidcat.core import flac as flacmod
from acidcat.core.riff import PAYLOAD_CAP

Span = riff.Span  # re-exported for callers that name the type


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
        return riff.iter_spans(filepath)


class FlacStrategy:
    """FLAC metadata blocks: the ``fLaC`` magic, then a chain of blocks each a
    4-byte header + payload. Delegates block enumeration to
    core/flac.iter_metadata_blocks (the one traversal the walker also uses), so
    the block-walk arithmetic never drifts. payload_base is offset+4 (the FLAC
    block header is 4 bytes, unlike RIFF's 8).

    Yields the metadata-block regions only. The synthetic ``fLaC`` magic and the
    opaque ``frames`` region the walker appends are walker-side synthesis, out
    of descriptor scope; the harness compares described regions, not the frame.
    """

    def label(self, filepath):
        with open(filepath, "rb") as f:
            return "FLAC" if f.read(4) == b"fLaC" else None

    def regions(self, filepath):
        spans = []
        with open(filepath, "rb") as f:
            for _bt, name, off, length, _last in \
                    flacmod.iter_metadata_blocks(filepath):
                f.seek(off + 4)
                payload = f.read(min(length, PAYLOAD_CAP))
                spans.append(Span(name, off, off + 4, payload, length))
        return spans, []


STRATEGIES = {"iff": IffStrategy(), "flac_blocks": FlacStrategy()}
