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

from acidcat.core import riff

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


STRATEGIES = {"iff": IffStrategy()}
