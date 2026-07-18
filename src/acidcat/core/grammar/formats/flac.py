"""The FLAC descriptor. Foundation slice: STREAMINFO byte-exact vs
walk/flac._flac_streaminfo. Everything else (VORBIS_COMMENT, SEEKTABLE,
PICTURE, CUESHEET, APPLICATION, PADDING) stays walker-side and is shown as
unparsed payload here. SEEKTABLE/VORBIS_COMMENT/CUESHEET are walker-only by
the partition policy (composite per-record displays, count-dependent
synthesis, a computed xref); PICTURE/APPLICATION/PADDING are policy-clean but
unbuilt. The grammar engine is frozen at this foundation (decision in
internal_docs/2026-07-18-flac-repeat-vs-partition.md), so no repeat-over-records
construct is planned.

STREAMINFO forces the two type primitives WAV never needed: Codec (the 3-byte
big-endian frame sizes) and the BitGroup (sample_rate/channels/bits/
total_samples all packed into one 8-byte word at payload 0x0A, with
overlapping display spans). ctx keys are the walker's semantic names.
"""

from acidcat.core.grammar.model import (BitField, BitGroup, Field, Format,
                                        NoteLocal, Region)
from acidcat.core.grammar.types import Codec, Int, Raw

FLAC = Format(name="FLAC", container="flac_blocks", regions={
    "STREAMINFO": Region(
        kind="struct", min_len=34,
        min_len_msg="STREAMINFO is {n} bytes, spec says {min}",
        relations=("flac_streaminfo_relations",),
        summary="flac_streaminfo_summary",
        fields=(
            Field("min_block_size", Int(2, be=True), note="samples"),
            Field("max_block_size", Int(2, be=True), note="samples"),
            Field("min_frame_size", Codec("u24be"), note="bytes"),
            Field("max_frame_size", Codec("u24be"), note="bytes"),
            # the 8-byte packed word at payload 0x0A: rate(20) | channels-1(3) |
            # bits-1(5) | total(36), with the walker's overlapping display spans
            BitGroup(off=0x0A, nbytes=8, fields=(
                BitField(0x0A, 3, "sample_rate", bitpos=0, width=20, bias=0,
                         note="Hz", ctx="sample_rate"),
                BitField(0x0C, 1, "channels", bitpos=20, width=3, bias=-1,
                         ctx="channels"),
                BitField(0x0D, 1, "bits_per_sample", bitpos=23, width=5, bias=-1,
                         ctx="bits"),
                BitField(0x0D, 5, "total_samples", bitpos=28, width=36, bias=0,
                         note=NoteLocal("flac_total_samples")),
            )),
            Field("md5_signature", Raw(16, unset="0 (unset)")),
        )),
})
