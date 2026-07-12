"""The RIFF/WAVE descriptor. v1 scope: the fmt chunk's core 16 bytes.

ctx keys are the walker's SEMANTIC names (see walk/wav._parse_fmt's
ctx.update): "bits", not "bits_per_sample", and none at all for
avg_bytes_per_sec. The scan/index path reads these keys downstream; a wrong
name here would silently break indexing parity later.
"""

from acidcat.core.grammar.model import Field, Format, Region
from acidcat.core.grammar.types import Enum, Int

WAVE = Format(name="RIFF/WAVE", container="iff", regions={
    "fmt ": Region(kind="struct", fields=(
        Field("format_tag",        Enum(Int(2), "wave_format_tags"), ctx="format_tag"),
        Field("channels",          Int(2),                           ctx="channels"),
        Field("sample_rate",       Int(4), note="Hz",                ctx="sample_rate"),
        Field("avg_bytes_per_sec", Int(4)),
        Field("block_align",       Int(2),                           ctx="block_align"),
        Field("bits_per_sample",   Int(2),                           ctx="bits"),
    )),
    "data": Region(kind="payload"),
})
