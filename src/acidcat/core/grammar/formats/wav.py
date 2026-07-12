"""The RIFF/WAVE descriptor. v1 scope: the fmt chunk's core 16 bytes.

ctx keys are the walker's SEMANTIC names (see walk/wav._parse_fmt's
ctx.update): "bits", not "bits_per_sample", and none at all for
avg_bytes_per_sec. The scan/index path reads these keys downstream; a wrong
name here would silently break indexing parity later.
"""

from acidcat.core.grammar.model import (Case, Cmp, Field, Format, NoteLookup,
                                        Region, Switch)
from acidcat.core.grammar.types import Enum, Hex, Int

WAVE = Format(name="RIFF/WAVE", container="iff", regions={
    "fmt ": Region(
        kind="struct", min_len=16,
        min_len_msg="fmt payload is {n} bytes, spec minimum is {min}",
        fields=(
            Field("format_tag",        Enum(Int(2), "wave_format_tags"), ctx="format_tag"),
            Field("channels",          Int(2),                           ctx="channels"),
            Field("sample_rate",       Int(4), note="Hz",                ctx="sample_rate"),
            Field("avg_bytes_per_sec", Int(4)),
            Field("block_align",       Int(2),                           ctx="block_align"),
            Field("bits_per_sample",   Int(2),                           ctx="bits"),
            # WAVEFORMATEX cbSize (non-EXTENSIBLE); present only when the payload
            # runs to >= 18 bytes, which the interpreter's bounds check enforces.
            Field("cb_size",           Int(2), note="extension bytes",
                  when=(Cmp("format_tag", "!=", 0xFFFE),)),
            # tag-dependent extension, parsed within the cb_size window
            Switch(on="format_tag", window="cb_size", cases={
                0x0011: Case(min_window=2, fields=(       # IMA/DVI ADPCM
                    Field("samples_per_block", Int(2)),
                )),
                0x0055: Case(min_window=12, fields=(      # MPEGLAYER3WAVEFORMAT
                    Field("mp3_id",           Int(2), note=NoteLookup("mpeglayer3_id")),
                    Field("mp3_flags",        Hex(4), note=NoteLookup("mp3_padding", mask=0x3)),
                    Field("block_size",       Int(2), note="bytes/frame"),
                    Field("frames_per_block", Int(2)),
                    Field("codec_delay",      Int(2), note="samples"),
                )),
            }),
        )),
    "data": Region(kind="payload"),
})
