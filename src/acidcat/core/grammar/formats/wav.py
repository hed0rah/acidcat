"""The RIFF/WAVE descriptor. v1 scope: the fmt chunk's core 16 bytes.

ctx keys are the walker's SEMANTIC names (see walk/wav._parse_fmt's
ctx.update): "bits", not "bits_per_sample", and none at all for
avg_bytes_per_sec. The scan/index path reads these keys downstream; a wrong
name here would silently break indexing parity later.
"""

from acidcat.core.grammar.model import (Case, Cmp, Field, Format, Helper,
                                        NoteFlags, NoteLookup, Order, Region,
                                        Requires, Switch, Valid)
from acidcat.core.grammar.types import Enum, Hex, Int

WAVE = Format(name="RIFF/WAVE", container="iff", regions={
    "fmt ": Region(
        kind="struct", min_len=16,
        min_len_msg="fmt payload is {n} bytes, spec minimum is {min}",
        relations=("wav_fmt_relations",), summary="wav_fmt_summary",
        fields=(
            Field("format_tag",        Enum(Int(2), "wave_format_tags"), ctx="format_tag"),
            Field("channels",          Int(2),                           ctx="channels",
                  valid=Valid("{v} channels is implausibly high", max=64)),
            Field("sample_rate",       Int(4), note="Hz",                ctx="sample_rate",
                  valid=Valid("sample_rate {v} Hz is outside any plausible range",
                              min=1000, max=768000, skip_zero=True)),
            Field("avg_bytes_per_sec", Int(4)),
            Field("block_align",       Int(2),                           ctx="block_align"),
            Field("bits_per_sample",   Int(2),                           ctx="bits"),
            # WAVEFORMATEX cbSize (non-EXTENSIBLE); present only when the payload
            # runs to >= 18 bytes, which the interpreter's bounds check enforces.
            Field("cb_size",           Int(2), note="extension bytes",
                  when=(Cmp("format_tag", "!=", 0xFFFE),)),
            # tag-dependent extension, parsed within the cb_size window
            Switch(on="format_tag", window="cb_size", cases={
                0x0002: Case(min_window=4, fields=(       # MS ADPCM
                    Field("samples_per_block", Int(2)),
                    Field("num_coef_pairs",    Int(2)),
                    Helper("wav_adpcm_coefs"),
                )),
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
            # EXTENSIBLE is windowless: the walker reads valid_bits/mask/sub at
            # fixed offsets, guarded by len(b) >= 40 (Remaining >= 24 at pos 0x10),
            # ignoring cb_size. The sub_format helper does the later-field-wins
            # ctx["format_tag"] = sub_tag override.
            Switch(on="format_tag", cases={
                0xFFFE: Case(min_window=24, fields=(
                    Field("cb_size",               Int(2)),
                    Field("valid_bits_per_sample", Int(2)),
                    Field("channel_mask",          Hex(4),
                          note=NoteFlags("wav_speaker_positions")),
                    Helper("wav_ext_subformat"),
                )),
            }),
        )),
    "data": Region(kind="payload"),
}, rules=(
    Requires("fmt ", "no fmt chunk: not decodable as audio"),
    Requires("data", "no data chunk: no audio payload"),
    Order("fmt ", "data",
          "fmt appears after data, violating the one RIFF ordering rule"),
))
