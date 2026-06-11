"""
acidcat inspect -- readelf-style structural dump for audio files.

Walks the container chunk by chunk and prints a structural table, a
decoded field breakdown per known chunk (with byte offsets), and any
spec violations it noticed along the way. `--hex` adds the raw bytes
next to each decoded field. `-f json` emits the same structure for
machines.

Supports WAV/RIFF, AIFF/AIFC, and Standard MIDI Files.
"""

import json
import os
import struct
import sys

from acidcat.core.riff import iter_chunks
from acidcat.core.aiff import iter_chunks as iter_aiff_chunks
from acidcat.core.aiff import _parse_ieee_extended, _AIFC_KNOWN_COMPRESSION
from acidcat.core.midi import _read_vlq
from acidcat.util.midi import midi_note_to_name

_PAYLOAD_CAP = 65536

_FORMAT_TAGS = {
    0x0001: "PCM",
    0x0002: "MS ADPCM",
    0x0003: "IEEE float",
    0x0006: "A-law",
    0x0007: "mu-law",
    0x0011: "IMA ADPCM",
    0x0055: "MPEG Layer III",
    0xFFFE: "extensible",
}

_ACID_FLAGS = (
    (0x01, "one-shot"),
    (0x02, "root set"),
    (0x04, "stretch"),
    (0x08, "disk-based"),
)

_LOOP_TYPES = {0: "forward", 1: "ping-pong", 2: "reverse"}

_INFO_TAGS = {
    "INAM": "title", "IART": "artist", "ICMT": "comment", "ISFT": "software",
    "ICRD": "date", "IGNR": "genre", "ICOP": "copyright", "IKEY": "keywords",
    "ISBJ": "subject", "IENG": "engineer", "ITCH": "technician", "IPRD": "product",
}


def register(subparsers):
    p = subparsers.add_parser(
        "inspect",
        help="readelf-style structural dump of a WAV, AIFF, or MIDI file.",
    )
    p.add_argument("target", help="Path to a WAV, AIFF, or MIDI file.")
    p.add_argument("--hex", action="store_true", dest="show_hex",
                   help="Show raw bytes next to each decoded field.")
    p.add_argument("-f", "--format", default="table", choices=["table", "json"],
                   help="Output format (default: table).")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Chunk table only, no per-chunk field detail.")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


# ── field helpers ──────────────────────────────────────────────────
# a field is a dict: off (relative to payload), len, name, value, note


def _f(off, length, name, value, note=""):
    return {"off": off, "len": length, "name": name, "value": value, "note": note}


def _u16(b, off):
    return struct.unpack_from("<H", b, off)[0]


def _u32(b, off):
    return struct.unpack_from("<I", b, off)[0]


def _f32(b, off):
    return struct.unpack_from("<f", b, off)[0]


def _cstr(b, off, length):
    return b[off:off + length].split(b"\x00")[0].decode("ascii", errors="replace").strip()


def _flag_names(value, table):
    names = [name for bit, name in table if value & bit]
    return ", ".join(names) if names else "none"


# ── per-chunk parsers ──────────────────────────────────────────────
# each returns (summary, fields, warnings) and may read/update ctx,
# which accumulates cross-chunk facts (sample rate, frame count...)


def _parse_fmt(b, ctx):
    fields, warns = [], []
    if len(b) < 16:
        return "truncated", fields, [f"fmt payload is {len(b)} bytes, spec minimum is 16"]
    tag, ch, rate, avg, align, bits = struct.unpack_from("<HHIIHH", b, 0)
    tag_name = _FORMAT_TAGS.get(tag, f"unknown 0x{tag:04x}")
    fields.append(_f(0x00, 2, "format_tag", f"0x{tag:04x}", tag_name))
    fields.append(_f(0x02, 2, "channels", ch))
    fields.append(_f(0x04, 4, "sample_rate", rate, "Hz"))
    fields.append(_f(0x08, 4, "avg_bytes_per_sec", avg))
    fields.append(_f(0x0C, 2, "block_align", align))
    fields.append(_f(0x0E, 2, "bits_per_sample", bits))
    ctx.update({"format_tag": tag, "channels": ch, "sample_rate": rate,
                "block_align": align, "bits": bits})

    if tag == 1 and ch and bits and align != ch * bits // 8:
        warns.append(f"block_align {align} != channels*bits/8 = {ch * bits // 8}")
    if rate and align and avg != rate * align:
        warns.append(f"avg_bytes_per_sec {avg} != sample_rate*block_align = {rate * align}")

    if tag == 0xFFFE and len(b) >= 40:
        cb = _u16(b, 0x10)
        valid_bits = _u16(b, 0x12)
        mask = _u32(b, 0x14)
        sub = b[0x18:0x28]
        sub_tag = struct.unpack_from("<H", sub, 0)[0]
        sub_name = _FORMAT_TAGS.get(sub_tag, f"guid 0x{sub_tag:04x}")
        fields.append(_f(0x10, 2, "cb_size", cb))
        fields.append(_f(0x12, 2, "valid_bits_per_sample", valid_bits))
        fields.append(_f(0x14, 4, "channel_mask", f"0x{mask:03x}"))
        fields.append(_f(0x18, 16, "sub_format", sub_name))
        ctx["format_tag"] = sub_tag

    summary = f"{tag_name} {bits}-bit {ch}ch {rate} Hz"
    return summary, fields, warns


def _parse_data(b, ctx, size):
    fields, warns = [], []
    frames = None
    align = ctx.get("block_align")
    rate = ctx.get("sample_rate")
    if align:
        frames = size // align
        ctx["frames"] = frames
    summary = f"audio payload, {size:,} bytes"
    if frames is not None and rate:
        dur = frames / rate
        ctx["duration"] = dur
        summary += f", {dur:.3f} s"
        fields.append(_f(0x00, size, "frames", frames, f"{dur:.3f} s at {rate} Hz"))
    if size == 0:
        warns.append("data chunk is empty")
    return summary, fields, warns


def _parse_fact(b, ctx):
    if len(b) < 4:
        return "truncated", [], ["fact payload under 4 bytes"]
    n = _u32(b, 0)
    rate = ctx.get("sample_rate")
    note = f"{n / rate:.3f} s" if rate and n else ""
    ctx.setdefault("frames", n)
    return f"{n:,} samples/channel", [_f(0x00, 4, "sample_length", n, note)], []


def _parse_acid(b, ctx):
    fields, warns = [], []
    if len(b) < 24:
        return "truncated", fields, [f"acid payload is {len(b)} bytes, expected 24"]
    flags, root, q1, q2, beats, denom, numer, tempo = struct.unpack_from("<IHHfIHHf", b, 0)
    fields.append(_f(0x00, 4, "type_flags", f"0x{flags:08x}", _flag_names(flags, _ACID_FLAGS)))
    fields.append(_f(0x04, 2, "root_note", root, midi_note_to_name(root) if root else "unset"))
    fields.append(_f(0x06, 2, "unknown1", f"0x{q1:04x}"))
    fields.append(_f(0x08, 4, "unknown2", round(q2, 4)))
    fields.append(_f(0x0C, 4, "num_beats", beats))
    fields.append(_f(0x10, 2, "meter_denominator", denom))
    fields.append(_f(0x12, 2, "meter_numerator", numer))
    fields.append(_f(0x14, 4, "tempo", round(tempo, 2), "BPM"))

    if tempo and not (40 <= tempo <= 300):
        warns.append(f"acid tempo {tempo:.2f} outside sane range 40-300")
    dur = ctx.get("duration")
    if beats and tempo and dur:
        expected = beats / tempo * 60
        drift = abs(expected - dur) / dur if dur else 0
        if drift > 0.05:
            warns.append(
                f"acid says {beats} beats at {tempo:.2f} bpm = {expected:.3f} s "
                f"but data holds {dur:.3f} s ({drift * 100:.0f}% drift)"
            )
    kind = "one-shot" if flags & 0x01 else "loop"
    summary = f"{kind}, {beats} beats, {numer}/{denom}, {tempo:.2f} bpm"
    if root:
        summary += f", root {midi_note_to_name(root)}"
    return summary, fields, warns


def _parse_smpl(b, ctx):
    fields, warns = [], []
    if len(b) < 36:
        return "truncated", fields, [f"smpl payload is {len(b)} bytes, header needs 36"]
    (manuf, product, period, unity, frac,
     smpte_fmt, smpte_off, n_loops, vendor) = struct.unpack_from("<IIIIIiiII", b, 0)
    fields.append(_f(0x00, 4, "manufacturer", manuf))
    fields.append(_f(0x04, 4, "product", product))
    fields.append(_f(0x08, 4, "sample_period", period, "ns/sample"))
    fields.append(_f(0x0C, 4, "midi_unity_note", unity,
                     midi_note_to_name(unity) if unity else "0 = unset sentinel"))
    fields.append(_f(0x10, 4, "midi_pitch_frac", frac))
    fields.append(_f(0x14, 4, "smpte_format", smpte_fmt))
    fields.append(_f(0x18, 4, "smpte_offset", smpte_off))
    fields.append(_f(0x1C, 4, "num_sample_loops", n_loops))
    fields.append(_f(0x20, 4, "sampler_data", vendor, "trailing vendor bytes"))

    rate = ctx.get("sample_rate")
    if rate and period and abs(period - round(1e9 / rate)) > 1:
        warns.append(f"sample_period {period} disagrees with fmt rate {rate}")

    capacity = max(0, (len(b) - 36) // 24)
    if n_loops > capacity:
        warns.append(f"declares {n_loops} loops but payload holds {capacity}")
    frames = ctx.get("frames")
    for i in range(min(n_loops, capacity)):
        base = 36 + i * 24
        cue_id, ltype, start, end, lfrac, count = struct.unpack_from("<IIIIII", b, base)
        type_name = _LOOP_TYPES.get(ltype, f"unknown {ltype}")
        plays = "forever" if count == 0 else f"{count}x"
        fields.append(_f(base, 24, f"loop[{i}]",
                         f"{start}..{end}", f"{type_name}, {plays}"))
        if end < start:
            warns.append(f"loop[{i}] end {end} before start {start}")
        elif frames and end > frames:
            warns.append(f"loop[{i}] end {end} past last frame {frames}")

    summary = f"root {midi_note_to_name(unity)}" if unity else "root unset"
    summary += f", {n_loops} loop(s)"
    return summary, fields, warns


def _parse_inst(b, ctx):
    if len(b) < 7:
        return "truncated", [], [f"inst payload is {len(b)} bytes, expected 7"]
    base, detune, gain = struct.unpack_from("<bbb", b, 0)
    low_n, high_n, low_v, high_v = b[3], b[4], b[5], b[6]
    fields = [
        _f(0x00, 1, "base_note", base, midi_note_to_name(base) if base >= 0 else ""),
        _f(0x01, 1, "detune", detune, "cents"),
        _f(0x02, 1, "gain", gain, "dB"),
        _f(0x03, 1, "low_note", low_n, midi_note_to_name(low_n)),
        _f(0x04, 1, "high_note", high_n, midi_note_to_name(high_n)),
        _f(0x05, 1, "low_velocity", low_v),
        _f(0x06, 1, "high_velocity", high_v),
    ]
    summary = (f"base {midi_note_to_name(base)}, "
               f"keys {midi_note_to_name(low_n)}-{midi_note_to_name(high_n)}")
    return summary, fields, []


def _parse_cue(b, ctx):
    fields, warns = [], []
    if len(b) < 4:
        return "truncated", fields, ["cue payload under 4 bytes"]
    declared = _u32(b, 0)
    capacity = max(0, (len(b) - 4) // 24)
    fields.append(_f(0x00, 4, "num_cue_points", declared))
    if declared > capacity:
        warns.append(f"declares {declared} cue points but payload holds {capacity}")
    for i in range(min(declared, capacity)):
        base = 4 + i * 24
        cid, pos, fcc, cstart, bstart, sample = struct.unpack_from("<II4sIII", b, base)
        fields.append(_f(base, 24, f"cue[{i}]", sample,
                         f"id {cid}, in '{fcc.decode('ascii', errors='replace')}'"))
    return f"{min(declared, capacity)} marker(s)", fields, warns


def _parse_list(b, ctx):
    fields, warns = [], []
    if len(b) < 4:
        return "truncated", fields, ["LIST payload under 4 bytes"]
    list_type = b[:4].decode("ascii", errors="replace")
    pos = 4
    count = 0
    while pos + 8 <= len(b):
        sub_id = b[pos:pos + 4].decode("ascii", errors="replace")
        sub_size = _u32(b, pos + 4)
        start, end = pos + 8, pos + 8 + sub_size
        if end > len(b):
            warns.append(f"sub-chunk {sub_id!r} overruns LIST payload")
            break
        if list_type == "adtl" and sub_id in ("labl", "note") and sub_size >= 4:
            cue_id = _u32(b, start)
            text = _cstr(b, start + 4, sub_size - 4)
            fields.append(_f(pos, 8 + sub_size, sub_id, text, f"cue id {cue_id}"))
        else:
            text = _cstr(b, start, sub_size)
            note = _INFO_TAGS.get(sub_id, "")
            fields.append(_f(pos, 8 + sub_size, sub_id, text, note))
        count += 1
        pos = end + (sub_size & 1)
    return f"{list_type}, {count} entries", fields, warns


def _parse_bext(b, ctx):
    fields, warns = [], []
    if len(b) < 348:
        return "truncated", fields, [f"bext payload is {len(b)} bytes, v0 minimum is 348"]
    fields.append(_f(0x000, 256, "description", _cstr(b, 0, 256)))
    fields.append(_f(0x100, 32, "originator", _cstr(b, 256, 32)))
    fields.append(_f(0x120, 32, "originator_reference", _cstr(b, 288, 32)))
    fields.append(_f(0x140, 10, "origination_date", _cstr(b, 320, 10)))
    fields.append(_f(0x14A, 8, "origination_time", _cstr(b, 330, 8)))
    low, high = _u32(b, 338), _u32(b, 342)
    timeref = low + (high << 32)
    rate = ctx.get("sample_rate")
    note = f"{timeref / rate:.3f} s since midnight" if rate and timeref else ""
    fields.append(_f(0x152, 8, "time_reference", timeref, note))
    version = _u16(b, 346)
    fields.append(_f(0x15A, 2, "version", version))
    return f"BWF v{version}, {_cstr(b, 256, 32) or 'no originator'}", fields, warns


_PARSERS = {
    "fmt ": _parse_fmt,
    "fact": _parse_fact,
    "acid": _parse_acid,
    "smpl": _parse_smpl,
    "inst": _parse_inst,
    "cue ": _parse_cue,
    "LIST": _parse_list,
    "bext": _parse_bext,
}


# ── walk ───────────────────────────────────────────────────────────


def inspect_wav(filepath):
    """Walk a WAV file and return (chunks, file_warnings).

    Each chunk is a dict: id, offset, size, summary, fields, warnings.
    """
    file_size = os.path.getsize(filepath)
    ctx = {}
    chunks = []
    file_warns = []
    seen = []

    with open(filepath, "rb") as f:
        hdr = f.read(12)
        riff_size = struct.unpack("<I", hdr[4:8])[0]
        if riff_size + 8 != file_size:
            file_warns.append(
                f"riff_size says {riff_size + 8:,} bytes, file is {file_size:,} "
                f"({file_size - riff_size - 8:+,})"
            )

        for cid, offset, size in iter_chunks(filepath):
            seen.append(cid)
            if offset + 8 + size > file_size:
                file_warns.append(
                    f"chunk {cid!r} at 0x{offset:08x} claims {size:,} bytes "
                    f"but only {file_size - offset - 8:,} remain"
                )
            f.seek(offset + 8)
            payload = f.read(min(size, _PAYLOAD_CAP))

            entry = {"id": cid, "offset": offset, "size": size,
                     "summary": "", "fields": [], "warnings": []}
            parser = _PARSERS.get(cid)
            if cid == "data":
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _parse_data(payload, ctx, size)
            elif parser:
                try:
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        parser(payload, ctx)
                except Exception as e:
                    entry["warnings"] = [f"parse error: {e.__class__.__name__}: {e}"]
            else:
                preview = payload[:16].hex(" ")
                entry["summary"] = f"unparsed, first bytes: {preview}"
            chunks.append(entry)

    if "fmt " not in seen:
        file_warns.append("no fmt chunk: not decodable as audio")
    if "data" not in seen:
        file_warns.append("no data chunk: no audio payload")
    if "fmt " in seen and "data" in seen and seen.index("fmt ") > seen.index("data"):
        file_warns.append("fmt appears after data, violating the one RIFF ordering rule")

    return chunks, file_warns


# ── aiff walk ──────────────────────────────────────────────────────


def _bu16(b, off):
    return struct.unpack_from(">H", b, off)[0]


def _bu32(b, off):
    return struct.unpack_from(">I", b, off)[0]


def _aiff_comm(b, ctx, form_type):
    fields, warns = [], []
    if len(b) < 18:
        return "truncated", fields, [f"COMM payload is {len(b)} bytes, spec minimum is 18"]
    ch, frames, bits = struct.unpack_from(">hIh", b, 0)
    rate = _parse_ieee_extended(b[8:18])
    fields.append(_f(0x00, 2, "num_channels", ch))
    fields.append(_f(0x02, 4, "num_sample_frames", frames))
    fields.append(_f(0x06, 2, "bits_per_sample", bits))
    rate_note = "80-bit IEEE 754 extended"
    fields.append(_f(0x08, 10, "sample_rate", int(rate) if rate else 0, rate_note))
    ctx.update({"channels": ch, "frames": frames, "bits": bits, "rate": rate})
    if not rate:
        warns.append("sample rate decodes to 0")

    comp = "PCM"
    if form_type == "AIFC":
        if len(b) >= 22:
            comp4 = b[18:22].decode("ascii", errors="replace")
            comp = comp4.strip() or "none"
            known = "" if comp4 in _AIFC_KNOWN_COMPRESSION else "unknown type"
            fields.append(_f(0x12, 4, "compression_type", comp4, known))
            if known:
                warns.append(f"compression type {comp4!r} not in the known set")
            if len(b) >= 23:
                name_len = b[22]
                name = b[23:23 + name_len].decode("ascii", errors="replace")
                if name:
                    fields.append(_f(0x16, 1 + name_len, "compression_name", name,
                                     "pascal string"))
            ctx["compression"] = comp4
        else:
            warns.append("AIFC COMM missing the compression type")
    dur = f", {frames / rate:.3f} s" if rate else ""
    summary = f"{comp} {bits}-bit {ch}ch {int(rate)} Hz{dur}"
    return summary, fields, warns


def _aiff_ssnd(b, ctx, size):
    fields, warns = [], []
    if len(b) < 8:
        return "truncated", fields, ["SSND payload under 8 bytes"]
    offset, block = struct.unpack_from(">II", b, 0)
    fields.append(_f(0x00, 4, "offset", offset, "bytes to first frame"))
    fields.append(_f(0x04, 4, "block_size", block))
    audio_bytes = size - 8 - offset
    summary = f"audio payload, {max(audio_bytes, 0):,} bytes"
    frames, ch, bits = ctx.get("frames"), ctx.get("channels"), ctx.get("bits")
    comp = ctx.get("compression", "NONE")
    uncompressed = comp in ("NONE", "none", "sowt", "twos", "raw ")
    if frames and ch and bits and uncompressed:
        expected = frames * ch * (bits // 8)
        if audio_bytes >= 0 and abs(audio_bytes - expected) > max(16, expected * 0.01):
            warns.append(
                f"SSND holds {audio_bytes:,} audio bytes but COMM frames "
                f"imply {expected:,}"
            )
    return summary, fields, warns


def _aiff_mark(b, ctx):
    fields, warns = [], []
    if len(b) < 2:
        return "truncated", fields, ["MARK payload under 2 bytes"]
    n = _bu16(b, 0)
    fields.append(_f(0x00, 2, "num_markers", n))
    pos = 2
    ids = {}
    for i in range(n):
        if pos + 7 > len(b):
            warns.append(f"declares {n} markers but payload ends at marker {i}")
            break
        mid = struct.unpack_from(">h", b, pos)[0]
        position = _bu32(b, pos + 2)
        name_len = b[pos + 6]
        name = b[pos + 7:pos + 7 + name_len].decode("ascii", errors="replace")
        fields.append(_f(pos, 7 + name_len, f"marker[{i}]", position,
                         f"id {mid}" + (f", '{name}'" if name else "")))
        ids[mid] = position
        # pascal string pads so the 1+len total lands even
        pos += 6 + 1 + name_len + ((1 + name_len) % 2)
    ctx["marker_ids"] = ids
    return f"{len(ids)} marker(s)", fields, warns


def _aiff_inst(b, ctx):
    fields, warns = [], []
    if len(b) < 20:
        return "truncated", fields, [f"INST payload is {len(b)} bytes, spec says 20"]
    base, detune = struct.unpack_from(">bb", b, 0)
    low_n, high_n, low_v, high_v = b[2], b[3], b[4], b[5]
    gain = struct.unpack_from(">h", b, 6)[0]
    fields.append(_f(0x00, 1, "base_note", base, midi_note_to_name(base) if base >= 0 else ""))
    fields.append(_f(0x01, 1, "detune", detune, "cents"))
    fields.append(_f(0x02, 1, "low_note", low_n, midi_note_to_name(low_n)))
    fields.append(_f(0x03, 1, "high_note", high_n, midi_note_to_name(high_n)))
    fields.append(_f(0x04, 1, "low_velocity", low_v))
    fields.append(_f(0x05, 1, "high_velocity", high_v))
    fields.append(_f(0x06, 2, "gain", gain, "dB"))
    loop_ids = []
    for label, off in (("sustain_loop", 8), ("release_loop", 14)):
        mode, begin, end = struct.unpack_from(">hhh", b, off)
        mode_name = {0: "off", 1: "forward", 2: "ping-pong"}.get(mode, f"unknown {mode}")
        fields.append(_f(off, 6, label, mode_name,
                         f"markers {begin}..{end}" if mode else ""))
        if mode:
            loop_ids.extend((begin, end))
    ctx["inst_loop_marker_ids"] = loop_ids
    summary = f"base {midi_note_to_name(base)}, keys " \
              f"{midi_note_to_name(low_n)}-{midi_note_to_name(high_n)}"
    return summary, fields, warns


def _aiff_basc(b, ctx):
    """Apple Loops basic description. no official spec; layout
    field-verified against 103 indexed loops (derived bpm matched the
    filename bpm on every file)."""
    fields, warns = [], []
    if len(b) < 16:
        return "truncated", fields, [f"basc payload is {len(b)} bytes, expected 84"]
    ver, beats = struct.unpack_from(">II", b, 0)
    root, scale, sig_n, sig_d = struct.unpack_from(">HHHH", b, 8)
    fields.append(_f(0x00, 4, "version", ver))
    fields.append(_f(0x04, 4, "num_beats", beats))
    fields.append(_f(0x08, 2, "root_key", root,
                     midi_note_to_name(root) if root else "unset"))
    fields.append(_f(0x0A, 2, "scale_type", scale, "enum unverified"))
    fields.append(_f(0x0C, 4, "time_sig", f"{sig_n}/{sig_d}"))
    summary = f"apple loop, {beats} beats"
    frames, rate = ctx.get("frames"), ctx.get("rate")
    if beats and frames and rate:
        bpm = beats / (frames / rate) * 60
        fields.append(_f(None, 0, "derived_bpm", round(bpm, 2),
                         "beats / duration * 60"))
        summary += f", ~{bpm:.0f} bpm"
    if root:
        summary += f", root {midi_note_to_name(root)}"
    return summary, fields, warns


def inspect_aiff(filepath, form_type):
    """Walk an AIFF/AIFC file and return (chunks, file_warnings)."""
    file_size = os.path.getsize(filepath)
    ctx = {}
    chunks = []
    file_warns = []
    seen = []

    with open(filepath, "rb") as f:
        hdr = f.read(12)
        form_size = struct.unpack(">I", hdr[4:8])[0]
        if form_size + 8 != file_size:
            file_warns.append(
                f"FORM size says {form_size + 8:,} bytes, file is "
                f"{file_size:,} ({file_size - form_size - 8:+,})"
            )

        for cid, offset, size in iter_aiff_chunks(filepath):
            seen.append(cid)
            if offset + 8 + size > file_size:
                file_warns.append(
                    f"chunk {cid!r} at 0x{offset:08x} claims {size:,} bytes "
                    f"but only {file_size - offset - 8:,} remain"
                )
            f.seek(offset + 8)
            payload = f.read(min(size, _PAYLOAD_CAP))

            entry = {"id": cid, "offset": offset, "size": size,
                     "summary": "", "fields": [], "warnings": []}
            try:
                if cid == "COMM":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _aiff_comm(payload, ctx, form_type)
                elif cid == "SSND":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _aiff_ssnd(payload, ctx, size)
                elif cid == "MARK":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _aiff_mark(payload, ctx)
                elif cid == "INST":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _aiff_inst(payload, ctx)
                elif cid == "basc":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _aiff_basc(payload, ctx)
                elif cid in ("NAME", "AUTH", "(c) ", "ANNO"):
                    text = payload.decode("ascii", errors="replace").strip("\x00").strip()
                    entry["summary"] = text[:60]
                    entry["fields"] = [_f(0x00, size, "text", text[:200])]
                elif cid == "ID3 ":
                    entry["summary"] = f"embedded ID3v2 tag, {size:,} bytes"
                elif cid == "cate":
                    entry["summary"] = "apple loops category data"
                elif cid == "trns":
                    entry["summary"] = "apple loops transient/slice data"
                elif cid == "FLLR":
                    entry["summary"] = "filler/padding"
                else:
                    entry["summary"] = f"unparsed, first bytes: {payload[:16].hex(' ')}"
            except Exception as e:
                entry["warnings"] = [f"parse error: {e.__class__.__name__}: {e}"]
            chunks.append(entry)

    if "COMM" not in seen:
        file_warns.append("no COMM chunk: not decodable as audio")
    if "SSND" not in seen and ctx.get("frames"):
        file_warns.append("no SSND chunk despite COMM declaring frames")
    loop_ids = ctx.get("inst_loop_marker_ids") or []
    markers = ctx.get("marker_ids") or {}
    for mid in loop_ids:
        if mid not in markers:
            file_warns.append(
                f"INST loop references marker id {mid} that MARK does not define"
            )
    return chunks, file_warns


# ── midi walk ──────────────────────────────────────────────────────


_MIDI_FORMATS = {0: "single track", 1: "multi-track sync", 2: "independent patterns"}


def _scan_track(trk, ctx):
    """Collect display facts from one MTrk payload. Mirrors the event
    grammar in core/midi.py but keeps per-track stats."""
    pos = 0
    running = 0
    ticks = 0
    notes = 0
    nmin = nmax = None
    channels = set()
    tempos = []
    names = []
    time_sig = key_sig = None
    has_eot = False

    while pos < len(trk):
        delta, pos = _read_vlq(trk, pos)
        ticks += delta
        if pos >= len(trk):
            break
        status = trk[pos]
        if status == 0xFF:
            running = 0
            if pos + 2 >= len(trk):
                break
            etype = trk[pos + 1]
            elen, pos = _read_vlq(trk, pos + 2)
            if pos + elen > len(trk):
                break
            edata = trk[pos:pos + elen]
            pos += elen
            if etype == 0x51 and elen == 3:
                us = (edata[0] << 16) | (edata[1] << 8) | edata[2]
                if us:
                    tempos.append(round(60_000_000 / us, 2))
            elif etype == 0x58 and elen == 4:
                time_sig = f"{edata[0]}/{2 ** edata[1]}"
            elif etype == 0x59 and elen == 2:
                sf = struct.unpack(">b", edata[0:1])[0]
                key_sig = f"{sf:+d} {'sharps' if sf >= 0 else 'flats'}" \
                          + (", minor" if edata[1] == 1 else "")
            elif etype == 0x03:
                name = edata.decode("ascii", errors="replace").strip()
                if name:
                    names.append(name)
            elif etype == 0x2F:
                has_eot = True
        elif status in (0xF0, 0xF7):
            running = 0
            slen, pos = _read_vlq(trk, pos + 1)
            if pos + slen > len(trk):
                break
            pos += slen
        elif status & 0x80:
            running = status
            pos += 1
            mtype = status & 0xF0
            if mtype in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                if pos + 1 < len(trk):
                    d1, d2 = trk[pos], trk[pos + 1]
                    pos += 2
                    if mtype == 0x90 and d2 > 0:
                        notes += 1
                        channels.add(status & 0x0F)
                        nmin = d1 if nmin is None else min(nmin, d1)
                        nmax = d1 if nmax is None else max(nmax, d1)
                else:
                    break
            elif mtype in (0xC0, 0xD0):
                pos += 1
            else:
                pos += 2
        elif running:
            mtype = running & 0xF0
            d1 = status
            if mtype in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                if pos + 1 < len(trk):
                    d2 = trk[pos + 1]
                    pos += 2
                    if mtype == 0x90 and d2 > 0:
                        notes += 1
                        channels.add(running & 0x0F)
                        nmin = d1 if nmin is None else min(nmin, d1)
                        nmax = d1 if nmax is None else max(nmax, d1)
                else:
                    break
            else:
                pos += 1
        else:
            pos += 1

    return {"ticks": ticks, "notes": notes, "nmin": nmin, "nmax": nmax,
            "channels": channels, "tempos": tempos, "names": names,
            "time_sig": time_sig, "key_sig": key_sig, "has_eot": has_eot}


def inspect_midi(filepath):
    """Walk a Standard MIDI File and return (chunks, file_warnings)."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read()
    chunks = []
    file_warns = []

    hdr_len = struct.unpack(">I", data[4:8])[0]
    fmt, ntrks, division = struct.unpack(">HHH", data[8:14])
    fields = [
        _f(0x00, 2, "format", fmt, _MIDI_FORMATS.get(fmt, "unknown")),
        _f(0x02, 2, "ntrks", ntrks),
    ]
    ctx = {"division": division}
    if division & 0x8000:
        fps = -struct.unpack(">b", bytes([(division >> 8) & 0xFF]))[0]
        tpf = division & 0xFF
        shown = 29.97 if fps == 29 else fps
        fields.append(_f(0x04, 2, "division", f"0x{division:04x}",
                         f"SMPTE: {shown} fps, {tpf} ticks/frame"))
        ctx["ticks_per_sec"] = (29.97 if fps == 29 else fps) * tpf
    else:
        fields.append(_f(0x04, 2, "division", division, "ticks per quarter note"))
    if hdr_len != 6:
        fields.append(_f(0x06, hdr_len - 6, "extra_header",
                         f"{hdr_len - 6} bytes", "legal, skipped"))
    summary = f"format {fmt}, {ntrks} track(s)"
    chunks.append({"id": "MThd", "offset": 0, "size": hdr_len,
                   "summary": summary, "fields": fields, "warnings": []})

    offset = 8 + hdr_len
    found = 0
    first_tempo = None
    max_ticks = 0
    while offset + 8 <= file_size and found < ntrks:
        if data[offset:offset + 4] != b"MTrk":
            file_warns.append(
                f"expected MTrk at 0x{offset:08x}, found "
                f"{data[offset:offset + 4]!r}; stopping"
            )
            break
        trk_len = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        trk = data[offset + 8:offset + 8 + trk_len]
        entry = {"id": "MTrk", "offset": offset, "size": trk_len,
                 "summary": "", "fields": [], "warnings": []}
        if len(trk) < trk_len:
            entry["warnings"].append(
                f"declares {trk_len:,} bytes but only {len(trk):,} remain"
            )
        st = _scan_track(trk, ctx)
        flds = entry["fields"]
        if st["names"]:
            flds.append(_f(None, 0, "name", st["names"][0]))
        flds.append(_f(None, 0, "events_ticks", st["ticks"]))
        if st["notes"]:
            flds.append(_f(None, 0, "notes", st["notes"],
                           f"{midi_note_to_name(st['nmin'])}-"
                           f"{midi_note_to_name(st['nmax'])}"))
            flds.append(_f(None, 0, "channels",
                           ",".join(str(c + 1) for c in sorted(st["channels"]))))
        if st["tempos"]:
            shown = ", ".join(f"{t:g}" for t in st["tempos"][:4])
            more = f" (+{len(st['tempos']) - 4} more)" if len(st["tempos"]) > 4 else ""
            flds.append(_f(None, 0, "tempo", shown + more, "BPM"))
            if first_tempo is None:
                first_tempo = st["tempos"][0]
        if st["time_sig"]:
            flds.append(_f(None, 0, "time_sig", st["time_sig"]))
        if st["key_sig"]:
            flds.append(_f(None, 0, "key_sig", st["key_sig"]))
        if not st["has_eot"]:
            entry["warnings"].append("no end-of-track meta event")
        max_ticks = max(max_ticks, st["ticks"])

        bits = []
        if st["names"]:
            bits.append(f"'{st['names'][0]}'")
        bits.append(f"{st['notes']} notes" if st["notes"] else "no notes")
        if st["tempos"]:
            bits.append(f"{st['tempos'][0]:g} bpm")
        entry["summary"] = ", ".join(bits)
        chunks.append(entry)

        found += 1
        offset += 8 + trk_len

    if found < ntrks:
        file_warns.append(f"MThd declares {ntrks} tracks, found {found}")
    if not (division & 0x8000) and first_tempo is None and found:
        file_warns.append("no tempo event in any track; players assume 120 bpm")

    return chunks, file_warns


# ── rendering ──────────────────────────────────────────────────────


def _hex_bytes(filepath, offset, length, cap=8):
    with open(filepath, "rb") as f:
        f.seek(offset)
        raw = f.read(min(length, cap))
    s = raw.hex(" ")
    return s + " .." if length > cap else s


def _render_table(filepath, fmt_label, chunks, file_warns, args):
    file_size = os.path.getsize(filepath)
    print(f"{os.path.basename(filepath)}: {fmt_label}, {file_size:,} bytes, "
          f"{len(chunks)} chunks")
    print()
    print(f"  {'idx':<5} {'id':<5} {'offset':<11} {'size':<11} summary")
    for i, c in enumerate(chunks):
        print(f"  [{i:>2}]  {c['id']:<5} 0x{c['offset']:08x}  "
              f"{c['size']:<11,} {c['summary']}")

    if not args.quiet:
        for c in chunks:
            if not c["fields"]:
                continue
            print()
            print(f"{c['id'].strip()} @ 0x{c['offset']:08x} ({c['size']} bytes)")
            for fl in c["fields"]:
                note = f"  {fl['note']}" if fl["note"] else ""
                # derived stats (midi track facts) carry no byte offset
                off_col = f"+0x{fl['off']:04x}" if fl["off"] is not None else "      "
                if args.show_hex and fl["off"] is not None:
                    hx = _hex_bytes(filepath, c["offset"] + 8 + fl["off"], fl["len"])
                    print(f"  {off_col}  {hx:<26} "
                          f"{fl['name']:<22} {fl['value']!s:<14}{note}")
                else:
                    print(f"  {off_col}  {fl['name']:<22} "
                          f"{fl['value']!s:<14}{note}")

    all_warns = list(file_warns)
    all_warns += [f"{c['id'].strip()}: {w}" for c in chunks for w in c["warnings"]]
    if all_warns:
        print()
        print("warnings:")
        for w in all_warns:
            print(f"  ! {w}")
    return 0


def run(args):
    filepath = args.target
    if not os.path.isfile(filepath):
        print(f"acidcat inspect: {filepath}: No such file", file=sys.stderr)
        return 1
    with open(filepath, "rb") as f:
        magic = f.read(14)
    if len(magic) >= 12 and magic[:4] == b"RIFF" and magic[8:12] == b"WAVE":
        fmt_label = "RIFF/WAVE"
        chunks, file_warns = inspect_wav(filepath)
    elif len(magic) >= 12 and magic[:4] == b"FORM" \
            and magic[8:12] in (b"AIFF", b"AIFC"):
        form_type = magic[8:12].decode("ascii")
        fmt_label = f"IFF/{form_type}"
        chunks, file_warns = inspect_aiff(filepath, form_type)
    elif len(magic) >= 14 and magic[:4] == b"MThd":
        fmt_label = "Standard MIDI File"
        chunks, file_warns = inspect_midi(filepath)
    elif magic[:4] == b"RF64":
        print("acidcat inspect: RF64 not supported yet", file=sys.stderr)
        return 1
    else:
        print("acidcat inspect: not a WAV, AIFF, or MIDI file",
              file=sys.stderr)
        return 1

    if args.format == "json":
        json.dump({
            "file": filepath,
            "format": fmt_label,
            "size": os.path.getsize(filepath),
            "chunks": chunks,
            "warnings": file_warns,
        }, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    return _render_table(filepath, fmt_label, chunks, file_warns, args)
