"""
acidcat inspect -- readelf-style structural dump for audio files.

Walks the container chunk by chunk and prints a structural table, a
decoded field breakdown per known chunk (with byte offsets), and any
spec violations it noticed along the way. `--hex` adds the raw bytes
next to each decoded field. `--frames` adds a per-element deep dump
(every MPEG frame for MP3, every event for MIDI). `--color` syntax-
highlights the table (auto/always/never, respects NO_COLOR). `-f json`
emits the same structure for machines.

Supports WAV/RIFF, RF64, AIFF/AIFC, Standard MIDI Files, Xfer Serum
presets, MP3 (ID3v2 + MPEG frames + Xing/LAME), and FLAC.
"""

import json
import os
import struct
import sys

from acidcat.core.riff import iter_chunks
from acidcat.core.aiff import iter_chunks as iter_aiff_chunks
from acidcat.core.aiff import _parse_ieee_extended, _AIFC_KNOWN_COMPRESSION
from acidcat.core.midi import _read_vlq
from acidcat.core import flac as flacmod
from acidcat.core import mp3 as mp3mod
from acidcat.util.midi import midi_note_to_name

_PAYLOAD_CAP = 65536
_FRAME_LISTING_CAP = 100000  # per-element rows kept for the --frames deep dump

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
        help="readelf-style structural dump of a WAV, AIFF, MIDI, MP3, or FLAC file.",
    )
    p.add_argument("target",
                   help="Path to a WAV, RF64, AIFF, MIDI, Serum, MP3, or FLAC file.")
    p.add_argument("--hex", action="store_true", dest="show_hex",
                   help="Show raw bytes next to each decoded field.")
    p.add_argument("-f", "--format", default="table", choices=["table", "json"],
                   help="Output format (default: table).")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Chunk table only, no per-chunk field detail.")
    p.add_argument("-F", "--frames", action="store_true",
                   help="Per-element deep dump: every MPEG frame (MP3) or "
                        "MIDI event. No effect on formats without per-element "
                        "structure (WAV, AIFF, FLAC).")
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                   help="Colorize table output: auto (default, when stdout is a "
                        "TTY), always, or never. Respects the NO_COLOR env var.")
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
    if tag == 1 and rate and align and avg != rate * align:
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


def _parse_data(b, ctx, size, avail=None):
    fields, warns = [], []
    align = ctx.get("block_align")
    rate = ctx.get("sample_rate")
    # a declared size larger than the bytes actually present is a lie we
    # already lint at the file level; never derive frames/duration from it.
    overrun = avail is not None and size > avail
    eff = avail if overrun else size
    frames = None
    if align:
        frames = eff // align
        ctx["frames"] = frames
    if overrun:
        summary = f"audio payload, {size:,} bytes declared, only {avail:,} present"
    else:
        summary = f"audio payload, {size:,} bytes"
    if frames is not None and rate:
        dur = frames / rate
        ctx["duration"] = dur
        note = f"{dur:.3f} s at {rate} Hz"
        if overrun:
            note += ", from bytes present (chunk overruns)"
        summary += f", {dur:.3f} s"
        fields.append(_f(0x00, eff, "frames", frames, note))
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
            avail = max(0, file_size - offset - 8)
            if size > avail:
                file_warns.append(
                    f"chunk {cid!r} at 0x{offset:08x} claims {size:,} bytes "
                    f"but only {avail:,} remain"
                )
            f.seek(offset + 8)
            payload = f.read(min(size, _PAYLOAD_CAP))

            entry = {"id": cid, "offset": offset, "size": size,
                     "summary": "", "fields": [], "warnings": []}
            parser = _PARSERS.get(cid)
            if cid == "data":
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _parse_data(payload, ctx, size, avail)
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


def _aiff_ssnd(b, ctx, size, avail=None):
    fields, warns = [], []
    if len(b) < 8:
        return "truncated", fields, ["SSND payload under 8 bytes"]
    offset, block = struct.unpack_from(">II", b, 0)
    fields.append(_f(0x00, 4, "offset", offset, "bytes to first frame"))
    fields.append(_f(0x04, 4, "block_size", block))
    # never size the payload from a declared chunk size larger than the file;
    # the overrun is linted at the file level. mirrors _parse_data.
    overrun = avail is not None and size > avail
    eff = avail if overrun else size
    audio_bytes = eff - 8 - offset
    summary = f"audio payload, {max(audio_bytes, 0):,} bytes"
    if overrun:
        summary += " (chunk overruns, from bytes present)"
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
            avail = max(0, file_size - offset - 8)
            if size > avail:
                file_warns.append(
                    f"chunk {cid!r} at 0x{offset:08x} claims {size:,} bytes "
                    f"but only {avail:,} remain"
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
                        _aiff_ssnd(payload, ctx, size, avail)
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

_META_NAMES = {
    0x00: "sequence number", 0x01: "text", 0x02: "copyright",
    0x03: "track name", 0x04: "instrument", 0x05: "lyric", 0x06: "marker",
    0x07: "cue point", 0x08: "program name", 0x09: "device name",
    0x20: "channel prefix", 0x21: "port", 0x2F: "end of track",
    0x51: "tempo", 0x54: "smpte offset", 0x58: "time sig", 0x59: "key sig",
    0x7F: "sequencer-specific",
}

_VOICE_NAMES = {
    0x80: "note off", 0x90: "note on", 0xA0: "poly aftertouch",
    0xB0: "control change", 0xC0: "program change",
    0xD0: "channel aftertouch", 0xE0: "pitch bend",
}


def _scan_track(trk, ctx, collect=False):
    """Collect display facts from one MTrk payload. Mirrors the event
    grammar in core/midi.py but keeps per-track stats. With ``collect``,
    also returns a per-event row list under the ``events`` key."""
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
    events = []

    def emit(name, detail=""):
        if collect:
            events.append({"tick": ticks, "event": name, "detail": detail})

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
            detail = ""
            if etype == 0x51 and elen == 3:
                us = (edata[0] << 16) | (edata[1] << 8) | edata[2]
                if us:
                    tempos.append(round(60_000_000 / us, 2))
                    detail = f"{round(60_000_000 / us, 2):g} bpm"
            elif etype == 0x58 and elen == 4:
                time_sig = f"{edata[0]}/{2 ** edata[1]}"
                detail = time_sig
            elif etype == 0x59 and elen == 2:
                sf = struct.unpack(">b", edata[0:1])[0]
                key_sig = f"{sf:+d} {'sharps' if sf >= 0 else 'flats'}" \
                          + (", minor" if edata[1] == 1 else "")
                detail = key_sig
            elif etype in (0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07):
                text = edata.decode("ascii", errors="replace").strip()
                if etype == 0x03 and text:
                    names.append(text)
                detail = text[:48]
            elif etype == 0x2F:
                has_eot = True
            emit("meta " + _META_NAMES.get(etype, f"0x{etype:02x}"), detail)
        elif status in (0xF0, 0xF7):
            running = 0
            slen, pos = _read_vlq(trk, pos + 1)
            if pos + slen > len(trk):
                break
            pos += slen
            emit("sysex", f"{slen} bytes")
        elif status & 0x80:
            running = status
            pos += 1
            mtype = status & 0xF0
            ch = status & 0x0F
            if mtype in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                if pos + 1 < len(trk):
                    d1, d2 = trk[pos], trk[pos + 1]
                    pos += 2
                    if mtype == 0x90 and d2 > 0:
                        notes += 1
                        channels.add(ch)
                        nmin = d1 if nmin is None else min(nmin, d1)
                        nmax = d1 if nmax is None else max(nmax, d1)
                    emit(_VOICE_NAMES[mtype], _voice_detail(mtype, d1, d2, ch))
                else:
                    break
            elif mtype in (0xC0, 0xD0):
                d1 = trk[pos] if pos < len(trk) else 0
                pos += 1
                emit(_VOICE_NAMES[mtype], f"{d1} ch{ch + 1}")
            else:
                pos += 2
        elif running:
            mtype = running & 0xF0
            ch = running & 0x0F
            d1 = status
            if mtype in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                if pos + 1 < len(trk):
                    d2 = trk[pos + 1]
                    pos += 2
                    if mtype == 0x90 and d2 > 0:
                        notes += 1
                        channels.add(ch)
                        nmin = d1 if nmin is None else min(nmin, d1)
                        nmax = d1 if nmax is None else max(nmax, d1)
                    emit(_VOICE_NAMES[mtype], _voice_detail(mtype, d1, d2, ch))
                else:
                    break
            else:
                pos += 1
                emit(_VOICE_NAMES.get(mtype, "voice"), f"{d1} ch{ch + 1}")
        else:
            pos += 1

    return {"ticks": ticks, "notes": notes, "nmin": nmin, "nmax": nmax,
            "channels": channels, "tempos": tempos, "names": names,
            "time_sig": time_sig, "key_sig": key_sig, "has_eot": has_eot,
            "events": events}


def _voice_detail(mtype, d1, d2, ch):
    """Human-readable detail for a channel-voice event."""
    if mtype in (0x80, 0x90):
        verb = "off" if (mtype == 0x80 or d2 == 0) else f"v{d2}"
        return f"{midi_note_to_name(d1)} {verb} ch{ch + 1}"
    if mtype == 0xA0:
        return f"{midi_note_to_name(d1)} {d2} ch{ch + 1}"
    if mtype == 0xB0:
        return f"cc{d1}={d2} ch{ch + 1}"
    if mtype == 0xE0:
        return f"{((d2 << 7) | d1) - 8192:+d} ch{ch + 1}"
    return f"{d1} {d2} ch{ch + 1}"


def inspect_midi(filepath, deep=False):
    """Walk a Standard MIDI File and return (chunks, file_warnings).
    With ``deep``, each MTrk carries a per-event listing."""
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
        st = _scan_track(trk, ctx, collect=deep)
        if deep:
            entry["rows"] = st["events"][:_FRAME_LISTING_CAP]
            if len(st["events"]) > _FRAME_LISTING_CAP:
                entry["warnings"].append(
                    f"event listing capped at {_FRAME_LISTING_CAP:,}"
                )
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


# ── rf64 walk ──────────────────────────────────────────────────────


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
    return f"64-bit sizes: data {data_size:,} bytes", fields, warns


def inspect_rf64(filepath):
    """Walk an RF64 file. Same grammar as RIFF except the 32-bit size
    fields are 0xFFFFFFFF sentinels resolved through the ds64 chunk,
    which must be the first chunk.
    """
    file_size = os.path.getsize(filepath)
    ctx = {}
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


# ── serum walk ─────────────────────────────────────────────────────


def inspect_serum(filepath):
    """Structural view of an Xfer Serum preset: XferJson magic, the
    JSON metadata block, then opaque wavetable/modulation data."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        raw = f.read(min(file_size, 4 * 1024 * 1024))
    chunks = []
    file_warns = []

    chunks.append({"id": "magc", "offset": 0, "size": 8,
                   "summary": "XferJson signature",
                   "fields": [_f(0x00, 8, "magic", "XferJson")],
                   "warnings": []})

    json_start = raw.find(b"{")
    if json_start < 0:
        file_warns.append("no JSON block after the magic")
        return chunks, file_warns

    text = raw[json_start:].decode("utf-8", errors="replace")
    try:
        parsed, end = json.JSONDecoder().raw_decode(text)
    except ValueError as e:
        file_warns.append(f"JSON block does not parse: {e}")
        return chunks, file_warns

    fields = []
    for key in ("fileType", "presetName", "presetAuthor",
                "presetDescription", "product", "productVersion",
                "tags", "vendor", "version"):
        if key in parsed:
            val = parsed[key]
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            fields.append(_f(None, 0, key, str(val)[:80]))
    name = parsed.get("presetName") or "unnamed"
    chunks.append({"id": "json", "offset": json_start, "size": end,
                   "summary": f"'{name}' metadata, {len(parsed)} keys",
                   "fields": fields, "warnings": []})

    blob_off = json_start + end
    chunks.append({"id": "blob", "offset": blob_off,
                   "size": file_size - blob_off,
                   "summary": f"wavetable/modulation data, "
                              f"{file_size - blob_off:,} bytes (opaque)",
                   "fields": [], "warnings": []})
    return chunks, file_warns


# ── flac walk ──────────────────────────────────────────────────────


def _flac_streaminfo(b):
    fields, warns = [], []
    if len(b) < 34:
        return "truncated", fields, [f"STREAMINFO is {len(b)} bytes, spec says 34"]
    min_block, max_block = _bu16(b, 0), _bu16(b, 2)
    min_frame = struct.unpack(">I", b"\x00" + b[4:7])[0]
    max_frame = struct.unpack(">I", b"\x00" + b[7:10])[0]
    packed = struct.unpack_from(">Q", b, 10)[0]
    rate = (packed >> 44) & 0xFFFFF
    channels = ((packed >> 41) & 0x07) + 1
    bits = ((packed >> 36) & 0x1F) + 1
    total = packed & 0xFFFFFFFFF
    md5 = b[18:34].hex()
    fields.append(_f(0x00, 2, "min_block_size", min_block, "samples"))
    fields.append(_f(0x02, 2, "max_block_size", max_block, "samples"))
    fields.append(_f(0x04, 3, "min_frame_size", min_frame, "bytes"))
    fields.append(_f(0x07, 3, "max_frame_size", max_frame, "bytes"))
    fields.append(_f(0x0A, 3, "sample_rate", rate, "Hz"))
    fields.append(_f(0x0C, 1, "channels", channels))
    fields.append(_f(0x0D, 1, "bits_per_sample", bits))
    dur = total / rate if rate else 0
    fields.append(_f(0x0D, 5, "total_samples", total,
                     f"{dur:.3f} s at {rate} Hz" if rate else ""))
    fields.append(_f(0x12, 16, "md5_signature",
                     md5 if md5 != "0" * 32 else "0 (unset)"))
    if rate == 0:
        warns.append("sample rate is 0")
    if min_block > max_block:
        warns.append(f"min_block_size {min_block} > max_block_size {max_block}")
    summary = f"{bits}-bit {channels}ch {rate} Hz, {total:,} samples, {dur:.3f} s"
    return summary, fields, warns


def _flac_vorbis_comment(b):
    fields, warns = [], []
    if len(b) < 8:
        return "truncated", fields, ["VORBIS_COMMENT under 8 bytes"]
    # vorbis comment lengths are little-endian, unlike the rest of FLAC
    vlen = struct.unpack_from("<I", b, 0)[0]
    pos = 4 + vlen
    if pos + 4 > len(b):
        return "truncated", fields, ["vendor string overruns block"]
    vendor = b[4:4 + vlen].decode("utf-8", errors="replace")
    fields.append(_f(0x00, vlen, "vendor", vendor[:80]))
    count = struct.unpack_from("<I", b, pos)[0]
    pos += 4
    shown = 0
    for i in range(count):
        if pos + 4 > len(b):
            warns.append(f"declares {count} comments but block ends at {i}")
            break
        clen = struct.unpack_from("<I", b, pos)[0]
        start = pos + 4
        if start + clen > len(b):
            warns.append(f"comment[{i}] overruns block")
            break
        text = b[start:start + clen].decode("utf-8", errors="replace")
        key, _, val = text.partition("=")
        fields.append(_f(pos, 4 + clen, key.upper()[:24], val[:80]))
        pos = start + clen
        shown += 1
    return f"{shown} comment(s), {vendor[:40]}", fields, warns


def _flac_picture(b):
    fields, warns = [], []
    if len(b) < 32:
        return "truncated", fields, ["PICTURE under 32 bytes"]
    ptype = _bu32(b, 0)
    pos = 4
    mlen = _bu32(b, pos)
    mime = b[pos + 4:pos + 4 + mlen].decode("ascii", errors="replace")
    pos += 4 + mlen
    dlen = _bu32(b, pos)
    desc = b[pos + 4:pos + 4 + dlen].decode("utf-8", errors="replace")
    pos += 4 + dlen
    if pos + 20 > len(b):
        return "truncated", fields, ["PICTURE header overruns block"]
    width, height, depth, colors, datalen = struct.unpack_from(">IIIII", b, pos)
    types = {0: "other", 3: "front cover", 4: "back cover"}
    fields.append(_f(0x00, 4, "picture_type", ptype, types.get(ptype, "")))
    fields.append(_f(None, 0, "mime_type", mime))
    if desc:
        fields.append(_f(None, 0, "description", desc[:60]))
    fields.append(_f(None, 0, "dimensions", f"{width}x{height}", f"{depth}-bit"))
    fields.append(_f(None, 0, "data_length", f"{datalen:,}", "bytes"))
    return f"{types.get(ptype, 'image')}, {mime}, {width}x{height}", fields, warns


def _flac_seektable(b):
    n = len(b) // 18
    placeholders = sum(
        1 for i in range(n)
        if struct.unpack_from(">Q", b, i * 18)[0] == 0xFFFFFFFFFFFFFFFF
    )
    note = f"{placeholders} placeholder" if placeholders else ""
    return f"{n} seek point(s)", [_f(0x00, len(b), "num_points", n, note)], []


def _flac_application(b):
    if len(b) < 4:
        return "truncated", [], ["APPLICATION under 4 bytes"]
    app_id = b[:4].decode("ascii", errors="replace")
    return (f"app '{app_id}', {len(b) - 4:,} bytes",
            [_f(0x00, 4, "application_id", app_id),
             _f(0x04, len(b) - 4, "data", f"{len(b) - 4:,} bytes")], [])


def inspect_flac(filepath):
    """Walk a FLAC file: metadata blocks then the audio-frame region."""
    file_size = os.path.getsize(filepath)
    chunks = []
    file_warns = []
    seen = []
    last_end = 4

    chunks.append({"id": "fLaC", "offset": 0, "size": 4,
                   "summary": "FLAC signature",
                   "fields": [_f(0x00, 4, "magic", "fLaC")], "warnings": []})

    saw_last = False
    for btype, name, off, length, is_last in flacmod.iter_metadata_blocks(filepath):
        seen.append(name)
        last_end = off + 4 + length
        with open(filepath, "rb") as f:
            f.seek(off + 4)
            payload = f.read(min(length, _PAYLOAD_CAP))
        entry = {"id": name, "offset": off, "size": length,
                 "summary": "", "fields": [], "warnings": []}
        try:
            if btype == 0:
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _flac_streaminfo(payload)
            elif btype == 4:
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _flac_vorbis_comment(payload)
            elif btype == 6:
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _flac_picture(payload)
            elif btype == 3:
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _flac_seektable(payload)
            elif btype == 2:
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _flac_application(payload)
            elif btype == 1:
                entry["summary"] = f"padding, {length:,} bytes"
            elif btype == 5:
                entry["summary"] = f"embedded cue sheet, {length:,} bytes"
            else:
                entry["summary"] = f"reserved block type {btype}, {length:,} bytes"
        except Exception as e:
            entry["warnings"] = [f"parse error: {e.__class__.__name__}: {e}"]
        if last_end > file_size:
            entry["warnings"].append(
                f"declared length {length:,} overruns the file by "
                f"{last_end - file_size:,} bytes "
                f"(only {max(0, file_size - off - 4):,} present)")
        chunks.append(entry)
        if is_last:
            saw_last = True
            break

    if not saw_last and seen:
        file_warns.append("no block had the last-metadata-block flag set")
    if seen and seen[0] != "STREAMINFO":
        file_warns.append("first metadata block is not STREAMINFO, violating the FLAC spec")
    audio_bytes = file_size - last_end
    if audio_bytes > 0:
        chunks.append({"id": "frames", "offset": last_end, "size": audio_bytes,
                       "summary": f"audio frames, {audio_bytes:,} bytes (opaque)",
                       "fields": [], "warnings": []})
    return chunks, file_warns


# ── mp3 walk ───────────────────────────────────────────────────────


_ID3_TEXT_FRAMES = {
    "TIT2": "title", "TPE1": "artist", "TALB": "album", "TCON": "genre",
    "TBPM": "bpm", "TKEY": "initial key", "TYER": "year", "TDRC": "year",
    "TRCK": "track", "TSSE": "encoder settings", "TENC": "encoded by",
    "COMM": "comment",
}

_VBR_METHODS = {
    0: "unknown", 1: "CBR", 2: "ABR", 3: "VBR (rh)", 4: "VBR (mtrh)",
    5: "VBR (rh2)", 6: "VBR (constrained)",
}


def _decode_id3_text(raw):
    """Decode an ID3v2 text-frame payload (leading encoding byte)."""
    if not raw:
        return ""
    enc = raw[0]
    body = raw[1:]
    codecs = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}
    try:
        text = body.decode(codecs.get(enc, "latin-1"), errors="replace")
    except Exception:
        text = body.decode("latin-1", errors="replace")
    return text.replace("\x00", " ").strip()


def _id3v2_frames(filepath, hdr):
    """Enumerate ID3v2 frames into display fields. Decodes common text
    frames to their values; lists every frame id and size otherwise."""
    fields, warns = [], []
    major = hdr["major"]
    with open(filepath, "rb") as f:
        f.seek(10)
        body = f.read(min(hdr["size"], _PAYLOAD_CAP))
    fields.append(_f(0x03, 1, "version", f"2.{major}.{hdr['revision']}"))
    fields.append(_f(0x05, 1, "flags", f"0x{hdr['flags']:02x}",
                     "has footer" if hdr["has_footer"] else ""))
    fields.append(_f(0x06, 4, "tag_size", f"{hdr['size']:,}", "synchsafe"))

    pos = 0
    is_v22 = major == 2
    id_len = 3 if is_v22 else 4
    fhdr_len = 6 if is_v22 else 10
    while pos + fhdr_len <= len(body):
        fid = body[pos:pos + id_len]
        if fid[0] == 0:  # padding
            break
        fid_s = fid.decode("ascii", errors="replace")
        if is_v22:
            fsize = struct.unpack(">I", b"\x00" + body[pos + 3:pos + 6])[0]
        elif major == 4:
            fsize = mp3mod.synchsafe(body[pos + 4:pos + 8])
        else:
            fsize = struct.unpack(">I", body[pos + 4:pos + 8])[0]
        data_start = pos + fhdr_len
        if data_start + fsize > len(body):
            warns.append(f"frame {fid_s!r} size {fsize} overruns tag")
            break
        raw = body[data_start:data_start + fsize]
        note = _ID3_TEXT_FRAMES.get(fid_s, "")
        if fid_s.startswith("T") and note:
            value = _decode_id3_text(raw)
        elif fid_s == "APIC" or fid_s == "PIC":
            value = f"{fsize:,} bytes"
            note = "attached picture"
        else:
            value = f"{fsize:,} bytes"
        fields.append(_f(10 + pos, fhdr_len + fsize, fid_s, value, note))
        pos = data_start + fsize
    return fields, warns


def _xing_offset(hdr):
    """Byte offset of the Xing/Info tag within the first frame, from the
    frame start: 4-byte header plus the version/channel-dependent side
    info block."""
    mono = hdr["channel_mode"] == 0b11
    if hdr["version_id"] == 0b11:        # MPEG 1
        return 4 + (17 if mono else 32)
    return 4 + (9 if mono else 17)       # MPEG 2 / 2.5


def _parse_xing_lame(filepath, frame_off, hdr):
    """Decode the Xing/Info VBR header and any LAME extension in the
    first frame. Returns (fields, warns, frame_count) or (None, [], None)
    if no tag is present."""
    fields, warns = [], []
    xoff = _xing_offset(hdr)
    with open(filepath, "rb") as f:
        f.seek(frame_off)
        buf = f.read(max(hdr["frame_length"], xoff + 200))
    if xoff + 8 > len(buf):
        return None, [], None
    tag = buf[xoff:xoff + 4]
    if tag not in (b"Xing", b"Info"):
        return None, [], None
    kind = "VBR" if tag == b"Xing" else "CBR (LAME)"
    fields.append(_f(xoff, 4, "vbr_tag", tag.decode("ascii"), kind))
    flags = _bu32(buf, xoff + 4)
    pos = xoff + 8
    frame_count = None
    if flags & 0x01:
        frame_count = _bu32(buf, pos)
        fields.append(_f(pos, 4, "frame_count", f"{frame_count:,}"))
        pos += 4
    if flags & 0x02:
        nbytes = _bu32(buf, pos)
        fields.append(_f(pos, 4, "byte_count", f"{nbytes:,}"))
        pos += 4
    if flags & 0x04:
        fields.append(_f(pos, 100, "toc", "100-entry seek table"))
        pos += 100
    if flags & 0x08:
        quality = _bu32(buf, pos)
        fields.append(_f(pos, 4, "quality", quality, "0=best, 100=worst"))
        pos += 4

    # LAME extension: 9-byte encoder string then 27 bytes of detail
    if pos + 9 <= len(buf) and buf[pos:pos + 4] in (b"LAME", b"L3.9", b"GOGO"):
        version = buf[pos:pos + 9].decode("latin-1", errors="replace").strip()
        fields.append(_f(pos, 9, "encoder", version))
        if pos + 24 <= len(buf):
            vbr_method = buf[pos + 9] & 0x0F
            lowpass = buf[pos + 10] * 100
            fields.append(_f(pos + 9, 1, "vbr_method", vbr_method,
                             _VBR_METHODS.get(vbr_method, "")))
            if lowpass:
                fields.append(_f(pos + 10, 1, "lowpass", f"{lowpass} Hz"))
            delay = (buf[pos + 21] << 4) | (buf[pos + 22] >> 4)
            padding = ((buf[pos + 22] & 0x0F) << 8) | buf[pos + 23]
            fields.append(_f(pos + 21, 3, "gapless", f"delay {delay}, pad {padding}",
                             "encoder delay / padding samples"))
    return fields, warns, frame_count


def inspect_mp3(filepath, deep=False):
    """Walk an MP3: optional ID3v2 tag, the MPEG frame run (with the
    first frame fully decoded and any Xing/LAME header), and an optional
    ID3v1 trailer. With ``deep``, the frame run carries a per-frame
    listing (offset, bitrate, sample rate, channel mode, size)."""
    file_size = os.path.getsize(filepath)
    chunks = []
    file_warns = []

    audio_start = 0
    hdr = mp3mod.read_id3v2(filepath)
    if hdr:
        flds, warns = _id3v2_frames(filepath, hdr)
        ntext = sum(1 for fl in flds if fl["off"] is not None and fl["off"] >= 10)
        chunks.append({"id": "ID3v2", "offset": 0, "size": hdr["total"],
                       "summary": f"ID3v2.{hdr['major']} tag, {ntext} frame(s)",
                       "fields": flds, "warnings": warns})
        audio_start = hdr["total"]

    id3v1_off = mp3mod.find_id3v1(filepath)
    audio_end = id3v1_off if id3v1_off is not None else file_size

    # find the first valid frame at or after audio_start
    first = None
    for off, fh in mp3mod.iter_frames(filepath, audio_start, audio_end, max_frames=1):
        first = (off, fh)
        break
    if first is None:
        file_warns.append("no valid MPEG audio frame found")
        return chunks, file_warns

    frame_off, fh = first
    if frame_off > audio_start:
        file_warns.append(
            f"{frame_off - audio_start} bytes of junk between the tag and the "
            f"first frame sync"
        )
    fields = [
        _f(0x00, 4, "sync", "0x7ff", f"{fh['version']}, {fh['layer']}"),
        _f(None, 0, "bitrate", fh["bitrate"], "kbps (first frame)"),
        _f(None, 0, "sample_rate", fh["sample_rate"], "Hz"),
        _f(None, 0, "channel_mode", fh["channel_mode_name"]),
        _f(None, 0, "crc_protected", fh["has_crc"]),
        _f(None, 0, "samples_per_frame", fh["samples_per_frame"]),
    ]
    if fh["emphasis"] != "none":
        fields.append(_f(None, 0, "emphasis", fh["emphasis"]))

    xing_fields, xing_warns, vbr_frames = _parse_xing_lame(filepath, frame_off, fh)
    is_vbr_header = xing_fields is not None
    if is_vbr_header:
        fields.extend(xing_fields)
    chunks.append({"id": "frame0", "offset": frame_off, "size": fh["frame_length"],
                   "summary": (f"{fh['version']} {fh['layer']}, {fh['bitrate']} kbps, "
                               f"{fh['sample_rate']} Hz, {fh['channel_mode_name']}"),
                   "fields": fields, "warnings": xing_warns})

    # count frames and derive duration. trust the Xing frame count when
    # present (accurate for VBR); otherwise walk the stream. with deep,
    # also record a per-frame row up to the listing cap.
    count = 0
    bitrates = set()
    rows = []
    truncated = False
    for off, f2 in mp3mod.iter_frames(filepath, frame_off, audio_end):
        count += 1
        bitrates.add(f2["bitrate"])
        if deep and len(rows) < _FRAME_LISTING_CAP:
            rows.append({
                "#": len(rows),
                "offset": f"0x{off:08x}",
                "kbps": f2["bitrate"],
                "Hz": f2["sample_rate"],
                "mode": f2["channel_mode_name"],
                "bytes": f2["frame_length"],
            })
        elif deep:
            truncated = True
    if vbr_frames:
        count = vbr_frames
    spf = fh["samples_per_frame"]
    duration = count * spf / fh["sample_rate"] if fh["sample_rate"] else 0
    cbr = len(bitrates) == 1 and not is_vbr_header
    summary = (f"{count:,} frames, {duration:.3f} s, "
               f"{'CBR' if cbr else 'VBR'}")
    if len(bitrates) > 1:
        summary += f", {min(bitrates)}-{max(bitrates)} kbps"
    frames_entry = {"id": "frames", "offset": frame_off,
                    "size": audio_end - frame_off, "summary": summary,
                    "fields": [_f(None, 0, "frame_count", f"{count:,}"),
                               _f(None, 0, "duration", f"{duration:.3f} s"),
                               _f(None, 0, "vbr", not cbr)],
                    "warnings": []}
    if deep:
        frames_entry["rows"] = rows
        if truncated:
            frames_entry["warnings"].append(
                f"frame listing capped at {_FRAME_LISTING_CAP:,}; "
                f"{count:,} frames total"
            )
    chunks.append(frames_entry)

    if id3v1_off is not None:
        with open(filepath, "rb") as f:
            f.seek(id3v1_off)
            tag = f.read(128)
        title = tag[3:33].decode("latin-1", errors="replace").rstrip("\x00 ")
        artist = tag[33:63].decode("latin-1", errors="replace").rstrip("\x00 ")
        chunks.append({"id": "ID3v1", "offset": id3v1_off, "size": 128,
                       "summary": f"ID3v1 trailer, {title or 'untitled'}",
                       "fields": [_f(0x03, 30, "title", title),
                                  _f(0x21, 30, "artist", artist)],
                       "warnings": []})
    return chunks, file_warns


# ── rendering ──────────────────────────────────────────────────────


def _hex_bytes(filepath, offset, length, cap=8):
    with open(filepath, "rb") as f:
        f.seek(offset)
        raw = f.read(min(length, cap))
    s = raw.hex(" ")
    return s + " .." if length > cap else s


# ── color ──────────────────────────────────────────────────────────
# small, meaningful palette: structure (cyan), value (green), positional
# metadata (dim), warning (red). codes are zero-width, so callers pad to
# the column width first and paint the padded string.

_ANSI = {
    "dim": "\033[2m",
    "id": "\033[1;36m",     # bold cyan: chunk ids, format label, anchors
    "val": "\033[32m",      # green: decoded field values
    "warn": "\033[1;31m",   # bold red: warnings
}
_RESET = "\033[0m"


def _color_enabled(args):
    # explicit always/never win; NO_COLOR governs auto only.
    mode = getattr(args, "color", "auto")
    if mode == "never":
        return False
    if mode == "always":
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


class _Paint:
    def __init__(self, on):
        self.on = on

    def __call__(self, role, text):
        text = str(text)
        return f"{_ANSI[role]}{text}{_RESET}" if self.on else text


def _render_rows(rows, paint):
    """Print a per-element listing as a compact dynamic-column table."""
    if not rows:
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    header = "    " + "  ".join(f"{c:<{widths[c]}}" for c in cols)
    print(paint("dim", header))
    for r in rows:
        print("    " + "  ".join(f"{str(r.get(c, '')):<{widths[c]}}" for c in cols))


def _render_table(filepath, fmt_label, chunks, file_warns, args):
    file_size = os.path.getsize(filepath)
    p = _Paint(_color_enabled(args))
    print(f"{os.path.basename(filepath)}: {p('id', fmt_label)}, {file_size:,} bytes, "
          f"{len(chunks)} chunks")
    print()
    print(p("dim", f"  {'idx':<5} {'id':<5} {'offset':<11} {'size':<11} summary"))
    for i, c in enumerate(chunks):
        idx = p("dim", f"[{i:>2}]")
        cid = p("id", f"{c['id']:<5}")
        off = p("dim", f"0x{c['offset']:08x}")
        print(f"  {idx}  {cid} {off}  {c['size']:<11,} {c['summary']}")

    if not args.quiet:
        for c in chunks:
            if not c["fields"] and not c.get("rows"):
                continue
            print()
            hdr_id = p("id", c["id"].strip())
            hdr_meta = p("dim", f"@ 0x{c['offset']:08x} ({c['size']} bytes)")
            print(f"{hdr_id} {hdr_meta}")
            for fl in c["fields"]:
                note = p("dim", f"  {fl['note']}") if fl["note"] else ""
                # derived stats (midi track facts) carry no byte offset
                off_col = f"+0x{fl['off']:04x}" if fl["off"] is not None else "      "
                off_col = p("dim", off_col)
                val = p("val", f"{fl['value']!s:<14}")
                if args.show_hex and fl["off"] is not None:
                    hx = _hex_bytes(filepath, c["offset"] + 8 + fl["off"], fl["len"])
                    print(f"  {off_col}  {p('dim', f'{hx:<26}')} "
                          f"{fl['name']:<22} {val}{note}")
                else:
                    print(f"  {off_col}  {fl['name']:<22} {val}{note}")
            if c.get("rows"):
                _render_rows(c["rows"], p)

    if getattr(args, "frames", False) and not any(c.get("rows") for c in chunks):
        print()
        print(p("dim", f"  (--frames: {fmt_label} has no per-element structure to dump)"))

    all_warns = list(file_warns)
    all_warns += [f"{c['id'].strip()}: {w}" for c in chunks for w in c["warnings"]]
    if all_warns:
        print()
        print(p("warn", "warnings:"))
        for w in all_warns:
            print(p("warn", f"  ! {w}"))
    return 0


def run(args):
    filepath = args.target
    if not os.path.isfile(filepath):
        print(f"acidcat inspect: {filepath}: No such file", file=sys.stderr)
        return 1
    deep = getattr(args, "frames", False)
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
        chunks, file_warns = inspect_midi(filepath, deep=deep)
    elif len(magic) >= 12 and magic[:4] == b"RF64" and magic[8:12] == b"WAVE":
        fmt_label = "RF64/WAVE"
        chunks, file_warns = inspect_rf64(filepath)
    elif magic[:8] == b"XferJson":
        fmt_label = "Xfer Serum preset"
        chunks, file_warns = inspect_serum(filepath)
    elif magic[:4] == b"fLaC":
        fmt_label = "FLAC"
        chunks, file_warns = inspect_flac(filepath)
    elif magic[:3] == b"ID3" or (len(magic) >= 4
                                 and mp3mod.decode_frame_header(magic[:4]) is not None):
        fmt_label = "MP3/MPEG audio"
        chunks, file_warns = inspect_mp3(filepath, deep=deep)
    else:
        print("acidcat inspect: not a WAV, RF64, AIFF, MIDI, Serum "
              "preset, MP3, or FLAC", file=sys.stderr)
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
