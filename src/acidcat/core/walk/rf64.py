"""RF64/WAVE structural walker.

Same grammar as RIFF except the 32-bit size fields are 0xFFFFFFFF
sentinels resolved through the ds64 chunk. Reuses the RIFF per-chunk
parsers for everything but ds64.
"""

import os
import struct

from acidcat.core.walk.base import _PAYLOAD_CAP, _f, _u32
from acidcat.core.walk.wav import _PARSERS, _parse_data

def _parse_ds64(b, ctx):
    """EBU Tech 3306: 64-bit size overrides for RF64."""
    fields, warns = [], []
    if len(b) < 28:
        return "truncated", fields, [f"ds64 payload is {len(b)} bytes, spec minimum is 28"]
    riff_size, data_size, sample_count = struct.unpack_from("<QQQ", b, 0)
    table_len = _u32(b, 24)
    fields.append(_f(0x00, 8, "riff_size", f"{riff_size:,}"))
    fields.append(_f(0x08, 8, "data_size", f"{data_size:,}"))
    fields.append(_f(0x10, 8, "sample_count", f"{sample_count:,}"))
    fields.append(_f(0x18, 4, "table_length", table_len,
                     "additional chunk-size overrides"))
    ctx["ds64_riff_size"] = riff_size
    ctx["ds64_data_size"] = data_size
    ctx["ds64_samples"] = sample_count
    # the override table: table_len entries of (4-byte id, uint64 size) giving
    # 64-bit sizes for any chunk other than data that carries the sentinel.
    table = {}
    tpos = 28
    for i in range(table_len):
        if tpos + 12 > len(b):
            warns.append(f"declares {table_len} override entries but payload "
                         f"ends at entry {i}")
            break
        ent_id = b[tpos:tpos + 4].decode("ascii", errors="replace")
        ent_size = struct.unpack_from("<Q", b, tpos + 4)[0]
        table[ent_id] = ent_size
        fields.append(_f(tpos, 12, f"override[{i}]", f"{ent_id!r} = {ent_size:,}"))
        tpos += 12
    if table:
        ctx["ds64_table"] = table
    file_size = ctx.get("file_size")
    if file_size is not None and data_size > file_size:
        warns.append(
            f"data_size {data_size:,} exceeds the whole file "
            f"({file_size:,} bytes)")
    return f"64-bit sizes: data {data_size:,} bytes", fields, warns


def inspect_rf64(filepath):
    """Walk an RF64 file. Same grammar as RIFF except the 32-bit size
    fields are 0xFFFFFFFF sentinels resolved through the ds64 chunk,
    which must be the first chunk.
    """
    file_size = os.path.getsize(filepath)
    ctx = {"file_size": file_size}
    chunks = []
    file_warns = []
    seen = []
    sentinel = 0xFFFFFFFF

    with open(filepath, "rb") as f:
        hdr = f.read(12)
        riff_size = struct.unpack("<I", hdr[4:8])[0]
        if riff_size != sentinel:
            file_warns.append(
                f"RF64 header size is {riff_size:#x}, spec says the "
                f"0xffffffff sentinel"
            )

        pos = 12
        while pos + 8 <= file_size:
            f.seek(pos)
            ch = f.read(8)
            if len(ch) < 8:
                break
            cid = ch[0:4].decode("ascii", errors="ignore")
            size = struct.unpack("<I", ch[4:8])[0]
            real_size = size
            if size == sentinel:
                if cid == "data" and "ds64_data_size" in ctx:
                    real_size = ctx["ds64_data_size"]
                elif cid in ctx.get("ds64_table", {}):
                    real_size = ctx["ds64_table"][cid]
                else:
                    file_warns.append(
                        f"chunk {cid!r} carries the 64-bit sentinel but "
                        f"ds64 provides no override"
                    )
                    break
            seen.append(cid)
            payload = f.read(min(real_size, _PAYLOAD_CAP))

            entry = {"id": cid, "offset": pos, "size": real_size,
                     "summary": "", "fields": [], "warnings": []}
            try:
                if cid == "ds64":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _parse_ds64(payload, ctx)
                elif cid == "data":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _parse_data(payload, ctx, real_size,
                                    max(0, file_size - pos - 8))
                elif cid in _PARSERS:
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _PARSERS[cid](payload, ctx)
                else:
                    entry["summary"] = f"unparsed, first bytes: {payload[:16].hex(' ')}"
            except Exception as e:
                entry["warnings"] = [f"parse error: {e.__class__.__name__}: {e}"]
            chunks.append(entry)

            pos += 8 + real_size
            if real_size % 2 == 1:
                pos += 1

    if seen and seen[0] != "ds64":
        file_warns.append("first chunk is not ds64, violating EBU Tech 3306")
    riff64 = ctx.get("ds64_riff_size")
    if riff64 and riff64 + 8 != file_size:
        file_warns.append(
            f"ds64 riff_size says {riff64 + 8:,} bytes, file is {file_size:,}"
        )
    return chunks, file_warns
