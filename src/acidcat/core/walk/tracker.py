"""Tracker-module structural walkers: MOD, XM, IT.

Each walker maps the header, the pattern order, and every sample as a chunk
carrying its real byte offset (so the hex pane shows the PCM and `carve
--offset` extracts it). IT's on-disk offset tables and IMPS sample pointers
are annotated as xref fields -- follow one in the TUI to jump to its target
and see a dangling (past-EOF) pointer flagged. Parsing lives in core/tracker."""

import os
import struct

from acidcat.core import tracker as tk
from acidcat.core.walk.base import Unsupported, _f

_SAMPLE_CAP = 400        # samples to list
_ORDER_CAP = 64          # order-table entries to list individually
_XREF_CAP = 256          # offset-table entries to annotate with xref


def _order_field(order, cap=_ORDER_CAP):
    shown = ", ".join(str(x) for x in order[:cap])
    if len(order) > cap:
        shown += f", ... (+{len(order) - cap})"
    return shown


def _truncated(fmt_id, size, msg):
    """Degraded single-chunk result for a header too short to parse. The magic
    matched, so the file is not Unsupported; it is a truncated instance of the
    format, and the contract is to degrade with a warning (like the RIFF walkers)
    rather than let the fixed-header unpack raise."""
    return ([{"id": fmt_id, "offset": 0, "size": size,
              "summary": f"{fmt_id}: header truncated, cannot decode",
              "fields": [], "warnings": [msg], "payload_base": 0}], [msg])


def inspect_mod(filepath):
    with open(filepath, "rb") as f:
        data = f.read(min(os.path.getsize(filepath), 64 * 1024 * 1024))
    if not tk.is_mod(data):
        raise Unsupported("no MOD magic at offset 1080")
    m = tk.parse_mod(data)
    used = sum(1 for s in m["samples"] if s["length"])
    chunks = [{
        "id": "MOD", "offset": 0, "size": len(data),
        "summary": f"ProTracker MOD, {m['channels']}ch ({m['magic']}), "
                   f"{m['num_patterns']} patterns, {used} samples"
                   + (f" -- '{m['title']}'" if m["title"] else ""),
        "fields": [
            _f(0x00, 20, "title", m["title"]),
            _f(0x438, 4, "magic", m["magic"], f"{m['channels']} channels"),
            _f(950, 1, "song_length", m["song_length"], "positions"),
            _f(951, 1, "restart", m["restart"]),
        ],
        "warnings": [], "payload_base": 0,
    }, {
        "id": "order", "offset": 952, "size": 128,
        "summary": f"pattern order, {m['song_length']} positions",
        "fields": [_f(0, 128, "order", _order_field(m["order"]))],
        "warnings": [], "payload_base": 952,
    }]
    for i, s in enumerate(m["samples"]):
        if not s["length"]:
            continue
        looped = "looped" if s["loop_len"] > 2 else "one-shot"
        chunks.append({
            "id": f"smp[{i + 1}]", "offset": s["offset"], "size": s["length"],
            "summary": f"{s['name'] or '(unnamed)'}  {s['length']:,} bytes 8-bit PCM, "
                       f"vol {s['volume']}, {looped}",
            "fields": [
                _f(s["hdr_off"], 22, "name", s["name"]),
                _f(s["hdr_off"] + 22, 2, "length", f"{s['length']:,}", "bytes"),
                _f(s["hdr_off"] + 24, 1, "finetune", s["finetune"]),
                _f(s["hdr_off"] + 25, 1, "volume", s["volume"]),
                _f(s["hdr_off"] + 26, 2, "loop_start", s["loop_start"]),
                _f(s["hdr_off"] + 28, 2, "loop_len", s["loop_len"]),
            ],
            "warnings": [], "payload_base": s["offset"],
        })
    return chunks, m["warnings"]


def inspect_xm(filepath):
    with open(filepath, "rb") as f:
        data = f.read(min(os.path.getsize(filepath), 64 * 1024 * 1024))
    if data[:17] != b"Extended Module: ":
        raise Unsupported("not an Extended Module")
    try:
        x = tk.parse_xm(data)
    except (struct.error, IndexError):
        return _truncated("XM", len(data), "XM header is truncated (need 80 bytes)")
    linear = "linear" if x["flags"] & 0x01 else "amiga"
    chunks = [{
        "id": "XM", "offset": 0, "size": len(data),
        "summary": f"FastTracker II XM v{x['version'] >> 8}.{x['version'] & 0xFF:02x}, "
                   f"{x['channels']}ch, {x['num_patterns']} patterns, "
                   f"{x['num_instruments']} instruments"
                   + (f" -- '{x['modname']}'" if x["modname"] else ""),
        "fields": [
            _f(0x11, 20, "module_name", x["modname"]),
            _f(0x26, 20, "tracker", x["tracker"]),
            _f(0x3A, 2, "version", f"{x['version'] >> 8}.{x['version'] & 0xFF:02x}"),
            _f(0x40, 2, "song_length", x["song_length"]),
            _f(0x42, 2, "restart", x["restart"]),
            _f(0x44, 2, "channels", x["channels"]),
            _f(0x46, 2, "num_patterns", x["num_patterns"]),
            _f(0x48, 2, "num_instruments", x["num_instruments"]),
            _f(0x4A, 2, "flags", x["flags"], f"{linear} frequency table"),
            _f(0x4C, 2, "default_tempo", x["tempo"], "ticks/row"),
            _f(0x4E, 2, "default_bpm", x["bpm"]),
        ],
        "warnings": [], "payload_base": 0,
    }, {
        "id": "order", "offset": 0x50, "size": 256,
        "summary": f"pattern order, {x['song_length']} positions",
        "fields": [_f(0, x["song_length"], "order", _order_field(x["order"]))],
        "warnings": [], "payload_base": 0x50,
    }]
    idx = 0
    for ins in x["instruments"]:
        for sm in ins["samples"]:
            idx += 1
            if idx > _SAMPLE_CAP:
                break
            if sm["offset"] is None:
                continue
            bits = "16-bit" if sm["bits16"] else "8-bit"
            name = sm["name"] or ins["name"] or "(unnamed)"
            chunks.append({
                "id": f"smp[{idx}]", "offset": sm["offset"], "size": sm["length"],
                "summary": f"{name}  {sm['length']:,} bytes {bits} delta-PCM "
                           f"(instrument '{ins['name']}')",
                "fields": [
                    _f(sm["hdr_off"], 4, "length", f"{sm['length']:,}", "bytes"),
                    _f(sm["hdr_off"] + 14, 1, "type", f"0x{sm['type']:02x}", bits),
                    _f(sm["hdr_off"] + 18, 22, "name", sm["name"]),
                ],
                "warnings": [], "payload_base": sm["offset"],
            })
    warns = list(x["warnings"])
    if idx > _SAMPLE_CAP:
        warns.append(f"listing the first {_SAMPLE_CAP} of {idx} samples")
    return chunks, warns


def inspect_s3m(filepath):
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(file_size, 64 * 1024 * 1024))
    if not tk.is_s3m(data):
        raise Unsupported("no SCRM magic at offset 0x2C")
    try:
        s = tk.parse_s3m(data)
    except (struct.error, IndexError):
        return _truncated("S3M", file_size, "S3M header is truncated (need 96 bytes)")
    writer = tk._S3M_WRITERS.get(s["cwt"] >> 12, "unknown")
    flag_names = ", ".join(n for b, n in tk._S3M_FLAGS if s["flags"] & b) or "none"
    cmap, active = tk._s3m_channel_map(s["channels"])
    chunks = [{
        "id": "S3M", "offset": 0, "size": file_size,
        "summary": f"ScreamTracker 3, made with 0x{s['cwt']:04x} ({writer}), "
                   f"{s['insnum']} instruments, {s['patnum']} patterns"
                   + (f" -- '{s['song_name']}'" if s["song_name"] else ""),
        "fields": [
            _f(0x00, 28, "song_name", s["song_name"]),
            _f(0x1D, 1, "type", 16, "ST3 module"),
            _f(0x20, 2, "order_count", s["ordnum"]),
            _f(0x22, 2, "instrument_count", s["insnum"]),
            _f(0x24, 2, "pattern_count", s["patnum"]),
            _f(0x26, 2, "flags", f"0x{s['flags']:04x}", flag_names),
            _f(0x28, 2, "created_with", f"0x{s['cwt']:04x}", writer),
            _f(0x2A, 2, "sample_format", s["ffi"],
               "signed PCM" if s["ffi"] == 1 else "unsigned PCM"),
            _f(0x30, 1, "global_volume", s["gvol"]),
            _f(0x31, 1, "initial_speed", s["speed"]),
            _f(0x32, 1, "initial_tempo", s["tempo"], "BPM"),
            _f(0x33, 1, "master_volume", s["mvol"] & 0x7F,
               "stereo" if s["mvol"] & 0x80 else "mono"),
            _f(0x40, 32, "channel_map", cmap or "(none)", f"{active} active"),
        ],
        "warnings": [], "payload_base": 0,
    }, {
        "id": "order", "offset": 0x60, "size": s["ordnum"],
        "summary": f"pattern order, {s['ordnum']} positions",
        "fields": [_f(0, s["ordnum"], "order", _order_field(s["order"]))],
        "warnings": [], "payload_base": 0x60,
    }]

    # parapointer tables: each entry is a paragraph (byte offset = value << 4),
    # annotated as an xref the TUI can follow (and flag when it dangles past EOF)
    for label, base, paras, target in (
        ("ins_parapointers", s["ins_base"], s["ins_para"], "instrument"),
        ("pat_parapointers", s["pat_base"], s["pat_para"], "pattern"),
    ):
        if not paras:
            continue
        flds = [_f(i * 2, 2, f"[{i}]", f"0x{p:04x}",
                   f"<<4 -> {target} @ 0x{p << 4:08x}", xref=p << 4)
                for i, p in enumerate(paras[:_XREF_CAP])]
        chunks.append({
            "id": label, "offset": base, "size": len(paras) * 2,
            "summary": f"{len(paras)} {target} parapointer(s), byte = value * 16",
            "fields": flds, "warnings": [], "payload_base": base,
        })

    sign = "unsigned" if s["ffi"] == 2 else "signed"
    for i, sm in enumerate(s["samples"][:_SAMPLE_CAP], 1):
        if not sm.get("valid"):
            continue
        if not sm.get("is_pcm"):                 # adlib instrument: no PCM to point at
            chunks.append({
                "id": f"ins[{i}]", "offset": sm["offset"], "size": 0x50,
                "summary": f"{sm['name'] or '(unnamed)'}  adlib instrument (no PCM)",
                "fields": [_f(0x00, 1, "type", sm["type"], "adlib"),
                           _f(0x30, 28, "name", sm["name"])],
                "warnings": [], "payload_base": sm["offset"],
            })
            continue
        bits = "16-bit" if sm["bits16"] else "8-bit"
        chan = "stereo (non-interleaved)" if sm["stereo"] else "mono"
        flag_note = ", ".join(n for b, n in tk._S3M_SAMPLE_FLAGS
                              if sm["flags"] & b) or "none"
        chunks.append({
            "id": f"smp[{i}]", "offset": sm["offset"], "size": 0x50,
            "summary": f"{sm['name'] or sm['dos_name'] or '(unnamed)'}  "
                       f"{sm['low_len']:,} pts {bits} {chan} {sign} PCM, "
                       f"C2 {sm['c2spd']} Hz  (data @ 0x{sm['pcm_off']:08x})",
            "fields": [
                _f(0x00, 1, "type", sm["type"], "PCM sample"),
                _f(0x01, 12, "dos_name", sm["dos_name"]),
                _f(0x0D, 3, "memseg", f"0x{sm['memseg']:06x}",
                   f"<<4 -> PCM @ 0x{sm['pcm_off']:08x}", xref=sm["pcm_off"]),
                _f(0x10, 4, "length", f"{sm['length']:,}", "sample points"),
                _f(0x14, 4, "loop_begin", sm["loop_beg"]),
                _f(0x18, 4, "loop_end", sm["loop_end"]),
                _f(0x1C, 1, "volume", sm["vol"]),
                _f(0x1F, 1, "flags", f"0x{sm['flags']:02x}", flag_note),
                _f(0x20, 4, "c2_speed", sm["c2spd"], "Hz"),
                _f(0x30, 28, "name", sm["name"]),
            ],
            "warnings": [], "payload_base": sm["offset"],
        })
    return chunks, s["warnings"]


def inspect_it(filepath):
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(file_size, 64 * 1024 * 1024))
    if data[:4] != b"IMPM":
        raise Unsupported("not an Impulse Tracker module")
    try:
        it = tk.parse_it(data)
    except (struct.error, IndexError):
        return _truncated("IMPM", file_size, "IT header is truncated (need 52 bytes)")
    flag_names = ", ".join(n for b, n in tk._IT_FLAGS if it["flags"] & b) or "none"
    chunks = [{
        "id": "IMPM", "offset": 0, "size": file_size,
        "summary": f"Impulse Tracker, made with 0x{it['cwt']:04x} / needs 0x{it['cmwt']:04x}, "
                   f"{it['insnum']} instruments, {it['smpnum']} samples, {it['patnum']} patterns"
                   + (f" -- '{it['songname']}'" if it["songname"] else ""),
        "fields": [
            _f(0x04, 26, "song_name", it["songname"]),
            _f(0x20, 2, "order_count", it["ordnum"]),
            _f(0x22, 2, "instrument_count", it["insnum"]),
            _f(0x24, 2, "sample_count", it["smpnum"]),
            _f(0x26, 2, "pattern_count", it["patnum"]),
            _f(0x28, 2, "created_with", f"0x{it['cwt']:04x}"),
            _f(0x2A, 2, "compatible_with", f"0x{it['cmwt']:04x}"),
            _f(0x2C, 2, "flags", f"0x{it['flags']:04x}", flag_names),
            _f(0x30, 1, "global_volume", it["gvol"]),
            _f(0x31, 1, "mix_volume", it["mvol"]),
            _f(0x32, 1, "initial_speed", it["speed"]),
            _f(0x33, 1, "initial_tempo", it["tempo"], "BPM"),
        ],
        "warnings": [], "payload_base": 0,
    }, {
        "id": "order", "offset": 192, "size": it["ordnum"],
        "summary": f"pattern order, {it['ordnum']} positions",
        "fields": [_f(0, it["ordnum"], "order", _order_field(it["order"]))],
        "warnings": [], "payload_base": 192,
    }]

    # the three on-disk offset tables: each entry is an absolute file offset, so
    # annotate it as an xref the TUI can follow (and flag if it dangles past EOF)
    for label, base, offs, target in (
        ("ins_offsets", it["ins_base"], it["ins_off"], "instrument"),
        ("smp_offsets", it["smp_base"], it["smp_off"], "sample header"),
        ("pat_offsets", it["pat_base"], it["pat_off"], "pattern"),
    ):
        if not offs:
            continue
        flds = []
        for i, o in enumerate(offs[:_XREF_CAP]):
            flds.append(_f(i * 4, 4, f"[{i}]", f"0x{o:08x}",
                           f"-> {target}", xref=o))
        chunks.append({
            "id": label, "offset": base, "size": len(offs) * 4,
            "summary": f"{len(offs)} {target} pointer(s)",
            "fields": flds, "warnings": [], "payload_base": base,
        })

    for i, s in enumerate(it["samples"][:_SAMPLE_CAP]):
        if not s.get("valid"):
            continue
        bits = "16-bit" if s["bits16"] else "8-bit"
        chan = "stereo" if s["stereo"] else "mono"
        codec = "IT-compressed" if s["compressed"] else "PCM"
        name = s["name"] or s["dos_name"] or "(unnamed)"
        # the IMPS header at s['offset'], its data pointer at +72 xrefs the PCM
        chunks.append({
            "id": f"smp[{i + 1}]", "offset": s["offset"], "size": 80,
            "summary": f"{name}  {s['length']:,} pts {bits} {chan} {codec}, "
                       f"C5 {s['c5_speed']} Hz  (data @ 0x{s['data_off']:08x})",
            "fields": [
                _f(0, 4, "magic", "IMPS"),
                _f(0x14, 26, "name", s["name"]),
                _f(0x30, 4, "length", f"{s['length']:,}", "sample points"),
                _f(0x3C, 4, "c5_speed", s["c5_speed"], "Hz"),
                _f(0x48, 4, "sample_pointer", f"0x{s['data_off']:08x}",
                   f"{s['byte_len']:,} bytes of {codec} PCM", xref=s["data_off"]),
            ],
            "warnings": [], "payload_base": s["offset"],
        })
    return chunks, it["warnings"]
