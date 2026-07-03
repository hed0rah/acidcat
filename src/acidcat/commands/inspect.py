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
from acidcat.core import midi as midimod
from acidcat.core.midi import _read_vlq
from acidcat.core import flac as flacmod
from acidcat.core import mp3 as mp3mod
from acidcat.core import bitwig as bwmod
from acidcat.core import vital as vitalmod
from acidcat.core import ncw as ncwmod
from acidcat.core import mp4 as mp4mod
from acidcat.core import ni as nimod
from acidcat.util.midi import midi_note_to_name

_PAYLOAD_CAP = 65536
_FRAME_LISTING_CAP = 100000  # per-element rows kept for the --frames deep dump
# ID3v2 tags routinely carry embedded cover art far larger than the generic
# payload cap, so enumerating their frames needs a bigger read. bounded so a
# forged synchsafe tag size cannot force an unbounded allocation.
_ID3_READ_CAP = 16 * 1024 * 1024
# --full emits raw region bytes for chunks that have decoded fields; cap the
# hex so a huge header (embedded art) cannot bloat the dump without bound.
_FULL_RAW_CAP = 8192

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

# WAVEFORMATEXTENSIBLE dwChannelMask bit positions, low bit first.
_SPEAKER_POSITIONS = [
    "FL", "FR", "FC", "LFE", "BL", "BR", "FLC", "FRC", "BC", "SL", "SR",
    "TC", "TFL", "TFC", "TFR", "TBL", "TBC", "TBR",
]

# the fixed 14-byte tail of every KSDATAFORMAT_SUBTYPE GUID (the first 2 bytes
# are the format tag, little-endian).
_KSDATAFORMAT_TAIL = bytes.fromhex("000000001000800000aa00389b71")


def _channel_mask_names(mask):
    names = [n for i, n in enumerate(_SPEAKER_POSITIONS) if mask & (1 << i)]
    return ", ".join(names) if names else "none"

_INFO_TAGS = {
    "INAM": "title", "IART": "artist", "ICMT": "comment", "ISFT": "software",
    "ICRD": "date", "IGNR": "genre", "ICOP": "copyright", "IKEY": "keywords",
    "ISBJ": "subject", "IENG": "engineer", "ITCH": "technician", "IPRD": "product",
    "IBPM": "bpm",  # non-standard, written by Bitwig
}


def register(subparsers):
    p = subparsers.add_parser(
        "inspect",
        help="readelf-style structural dump of a WAV, AIFF, MIDI, MP3, or FLAC file.",
    )
    p.add_argument("targets", nargs="+", metavar="target",
                   help="One or more WAV, RF64, AIFF, MIDI, Serum, MP3, or FLAC "
                        "files. With more than one, each is printed under a "
                        "'File:' banner; JSON output becomes NDJSON (one record "
                        "per line).")
    p.add_argument("--hex", action="store_true", dest="show_hex",
                   help="Show raw bytes next to each decoded field.")
    p.add_argument("-f", "--format", default="table", choices=["table", "json"],
                   help="Output format (default: table).")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Chunk table only, no per-chunk field detail.")
    p.add_argument("--pretty", action="store_true",
                   help="Human-friendly view of the decoded tags and metadata "
                        "(no byte offsets), ideal for presets and tagged files.")
    p.add_argument("-F", "--frames", action="store_true",
                   help="Per-element deep dump: every MPEG frame (MP3) or "
                        "MIDI event. No effect on formats without per-element "
                        "structure (WAV, AIFF, FLAC).")
    p.add_argument("--only", metavar="IDS",
                   help="Show only these chunk ids (comma-separated, e.g. "
                        "'fmt,bext'). Case-insensitive, matched against the "
                        "displayed id. Compose with --hex to hexdump one chunk.")
    p.add_argument("--exclude", metavar="IDS",
                   help="Hide these chunk ids (comma-separated). Applied after "
                        "--only.")
    p.add_argument("--full", action="store_true",
                   help="Emit a self-contained structural dump (implies -f json): "
                        "each chunk with its raw region bytes and every field's "
                        "absolute byte offset, so build_explorer.py can render a "
                        "standalone HTML explorer for the file.")
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

    # a WAVEFORMATEX (extended, non-extensible) carries a cbSize at 0x10.
    if tag != 0xFFFE and len(b) >= 18:
        fields.append(_f(0x10, 2, "cb_size", _u16(b, 0x10), "extension bytes"))

    if tag == 0xFFFE and len(b) >= 40:
        cb = _u16(b, 0x10)
        valid_bits = _u16(b, 0x12)
        mask = _u32(b, 0x14)
        sub = b[0x18:0x28]
        sub_tag = struct.unpack_from("<H", sub, 0)[0]
        sub_name = _FORMAT_TAGS.get(sub_tag, f"guid 0x{sub_tag:04x}")
        tail_ok = sub[2:] == _KSDATAFORMAT_TAIL
        fields.append(_f(0x10, 2, "cb_size", cb))
        fields.append(_f(0x12, 2, "valid_bits_per_sample", valid_bits))
        fields.append(_f(0x14, 4, "channel_mask", f"0x{mask:x}",
                         _channel_mask_names(mask)))
        fields.append(_f(0x18, 16, "sub_format", sub_name,
                         "KSDATAFORMAT_SUBTYPE" if tail_ok else "non-standard GUID"))
        if not tail_ok:
            warns.append("sub_format GUID tail is not the standard "
                         "KSDATAFORMAT_SUBTYPE suffix")
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
    fact = ctx.get("fact_samples")
    # bytes / block_align is the frame count only for uncompressed audio.
    # block-compressed formats (ADPCM) pack many samples per block, so trust
    # the fact chunk's sample count when present. on an overrun we still
    # derive from the bytes actually present, never a declared count.
    frames = None
    if overrun:
        if align:
            frames = eff // align
    elif fact is not None:
        frames = fact
    elif align:
        frames = eff // align
    if frames is not None:
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
        elif fact is not None:
            note += ", from fact chunk"
        summary += f", {dur:.3f} s"
        fields.append(_f(0x00, eff, "frames", frames, note))
    if size == 0:
        warns.append("data chunk is empty")
    return summary, fields, warns


def _parse_fact(b, ctx):
    if len(b) < 4:
        return "truncated", [], ["fact payload under 4 bytes"]
    n = _u32(b, 0)
    warns = []
    notes = []
    if n == 0xFFFFFFFF:
        # RF64 sentinel: the real 64-bit count lives in ds64. never
        # trust the sentinel itself as a sample count.
        if "ds64_samples" in ctx:
            n = ctx["ds64_samples"]
            notes.append("0xffffffff sentinel, resolved via ds64")
        else:
            warns.append("sample_length is the 0xffffffff sentinel but "
                         "no ds64 chunk provides the 64-bit count")
            return ("sample count deferred to ds64, which is absent",
                    [_f(0x00, 4, "sample_length", "0xffffffff", "sentinel")],
                    warns)
    rate = ctx.get("sample_rate")
    if rate and n:
        notes.append(f"{n / rate:.3f} s")
    ctx["fact_samples"] = n
    ctx.setdefault("frames", n)
    return (f"{n:,} samples/channel",
            [_f(0x00, 4, "sample_length", n, ", ".join(notes))], warns)


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

    # v1 (July 2001) adds a 64-byte SMPTE UMID at 0x15C; v2 (May 2011) adds
    # five int16 loudness values at 0x19C. the fixed area is always 602 bytes;
    # CodingHistory (ASCII) runs from 0x25A to the end of the chunk.
    if version >= 1 and len(b) >= 0x15C + 64:
        umid = b[0x15C:0x15C + 64]
        shown = umid.hex() if umid.strip(b"\x00") else "0 (no UMID)"
        fields.append(_f(0x15C, 64, "umid", shown, "SMPTE ST 330"))
    if version >= 2 and len(b) >= 0x1A6:
        loud = [("loudness_value", "LUFS"), ("loudness_range", "LU"),
                ("max_true_peak", "dBTP"), ("max_momentary", "LUFS"),
                ("max_short_term", "LUFS")]
        for i, (name, unit) in enumerate(loud):
            raw = struct.unpack_from("<h", b, 0x19C + i * 2)[0]
            # 0x7fff is the "not set" sentinel; the spec also says any value
            # outside +-99.99 (hundredths of a unit) shall be ignored.
            unset = raw == 0x7FFF or not (-9999 <= raw <= 9999)
            fields.append(_f(0x19C + i * 2, 2, name,
                             "unset" if unset else f"{raw / 100:+.2f} {unit}"))
    if len(b) > 0x25A:
        # writers pad CodingHistory with trailing NULs; trim before display.
        hist = b[0x25A:].split(b"\x00")[0].decode("ascii", errors="replace").strip()
        if hist:
            fields.append(_f(0x25A, len(b) - 0x25A, "coding_history",
                             hist[:120], "EBU R98 rows"))

    return f"BWF v{version}, {_cstr(b, 256, 32) or 'no originator'}", fields, warns


def _parse_bwbm(b, ctx):
    """Bitwig Beat Map: the loop tempo/beat metadata Bitwig writes into a WAV
    bounce in place of a Sony acid chunk. version u32, then two doubles at
    0x18/0x20 holding the loop length in beats and its duration in seconds.
    Verified against a Bitwig Studio 6.0.6 bounce."""
    fields, warns = [], []
    if len(b) < 40:
        return "truncated", fields, [f"BWBM payload is {len(b)} bytes, expected 40"]
    version = _u32(b, 0)
    beats = struct.unpack_from("<d", b, 0x18)[0]
    dur = struct.unpack_from("<d", b, 0x20)[0]
    fields.append(_f(0x00, 4, "version", version))
    fields.append(_f(0x18, 8, "beats", round(beats, 4)))
    fields.append(_f(0x20, 8, "duration", f"{dur:.4f} s"))
    bpm = beats / dur * 60 if dur else None
    if bpm and bpm > 0:
        fields.append(_f(None, 0, "derived_bpm", round(bpm, 2), "beats / duration * 60"))
    summary = f"Bitwig beat map, {beats:g} beats, {dur:.3f} s"
    if bpm and bpm > 0:
        summary += f", ~{bpm:.1f} bpm"
    return summary, fields, warns


_PARSERS = {
    "fmt ": _parse_fmt,
    "fact": _parse_fact,
    "acid": _parse_acid,
    "smpl": _parse_smpl,
    "inst": _parse_inst,
    "cue ": _parse_cue,
    "LIST": _parse_list,
    "bext": _parse_bext,
    "BWBM": _parse_bwbm,
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


# AIFC compression types that store real PCM sample frames (so frames/rate
# is an exact duration). Everything else is block/packet-coded, where
# num_sample_frames is a packet count and the duration is only approximate.
_AIFC_UNCOMPRESSED = ("NONE", "sowt", "twos", "raw ", "fl32", "fl64",
                      "FL32", "FL64", "in24", "in32", "23ni", "42ni")


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
    uncompressed = True
    if form_type == "AIFC":
        uncompressed = False
        if len(b) >= 22:
            comp4 = b[18:22].decode("ascii", errors="replace")
            comp = comp4.strip() or "none"
            uncompressed = comp4 in _AIFC_UNCOMPRESSED
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
    if not rate:
        dur = ""
    elif uncompressed:
        dur = f", {frames / rate:.3f} s"
    else:
        # num_sample_frames counts packets for compressed codecs, not sample
        # frames, so frames/rate is only a lower bound; label it approximate.
        dur = f", ~{frames / rate:.3f} s (approx)"
        warns.append("AIFC duration is approximate: num_sample_frames counts "
                     "packets, not sample frames, for compressed audio")
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
    if offset > max(0, eff - 8):
        warns.append(
            f"SSND offset {offset:,} exceeds the {max(0, eff - 8):,}-byte payload"
        )
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


def _aiff_comt(b):
    """AIFF Comments chunk: numComments then timestamped, marker-linked
    comment records (big-endian, text padded to even)."""
    fields, warns = [], []
    if len(b) < 2:
        return "truncated", fields, ["COMT payload under 2 bytes"]
    n = _bu16(b, 0)
    fields.append(_f(0x00, 2, "num_comments", n))
    pos = 2
    shown = 0
    for i in range(n):
        if pos + 8 > len(b):
            warns.append(f"declares {n} comments but payload ends at {i}")
            break
        marker = struct.unpack_from(">h", b, pos + 4)[0]
        count = _bu16(b, pos + 6)
        if pos + 8 + count > len(b):
            warns.append(f"comment[{i}] text overruns payload")
            break
        text = b[pos + 8:pos + 8 + count].decode("ascii", errors="replace").strip()
        note = f"marker {marker}" if marker else ""
        fields.append(_f(pos, 8 + count + (count & 1), f"comment[{i}]",
                         text[:60], note))
        pos += 8 + count + (count & 1)
        shown += 1
    return f"{shown} comment(s)", fields, warns


_AES_RATES = {0: "unindicated", 1: "48000", 2: "44100", 3: "32000"}
_AES_EMPHASIS = {0b000: "unindicated", 0b100: "none",
                 0b110: "50/15 us", 0b111: "CCITT J.17"}


def _aiff_aesd(b):
    """AIFF Audio Recording chunk: a 24-byte AES3 channel-status block. Byte 0
    carries the professional/consumer, audio, emphasis, and rate bits."""
    fields, warns = [], []
    if len(b) < 24:
        return "truncated", fields, [f"AESD is {len(b)} bytes, spec says 24"]
    b0 = b[0]
    pro = "professional" if (b0 & 0x01) else "consumer"
    kind = "non-audio" if (b0 & 0x02) else "PCM audio"
    emphasis = _AES_EMPHASIS.get((b0 >> 2) & 0x07, "reserved")
    rate = _AES_RATES.get((b0 >> 6) & 0x03, "?")
    fields.append(_f(0x00, 24, "channel_status", b[:24].hex(),
                     f"{pro}, {kind}, emphasis {emphasis}, {rate} Hz"))
    return f"AES3 status: {pro}, {rate} Hz", fields, warns


def _aiff_appl(b):
    """AIFF Application-specific chunk: 4-byte OSType signature then data.
    'pdos'/'stoc' begin the data with a pstring naming the app/structure."""
    fields, warns = [], []
    if len(b) < 4:
        return "truncated", fields, ["APPL under 4 bytes"]
    sig = b[:4].decode("ascii", errors="replace")
    fields.append(_f(0x00, 4, "signature", sig))
    if sig in ("pdos", "stoc") and len(b) > 4:
        nlen = b[4]
        name = b[5:5 + nlen].decode("ascii", errors="replace")
        fields.append(_f(0x04, 1 + nlen, "name", name, "pstring"))
    fields.append(_f(None, 0, "data", f"{len(b) - 4:,} bytes"))
    return f"app '{sig}', {len(b) - 4:,} bytes", fields, warns


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
                    fr, ch, bits = ctx.get("frames"), ctx.get("channels"), ctx.get("bits")
                    comp = ctx.get("compression")
                    if fr and ch and bits and (comp is None or comp in _AIFC_UNCOMPRESSED) \
                            and fr * ch * (bits // 8) > file_size:
                        entry["warnings"].append(
                            f"num_sample_frames {fr:,} implies more audio than the "
                            f"{file_size:,}-byte file holds; duration is not trustworthy"
                        )
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
                elif cid == "COMT":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _aiff_comt(payload)
                elif cid == "AESD":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _aiff_aesd(payload)
                elif cid == "APPL":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _aiff_appl(payload)
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
            elif etype == 0x54 and elen == 5:
                # SMPTE offset: hr byte top bits carry the frame rate.
                hr = edata[0]
                fps = {0: 24, 1: 25, 2: 29.97, 3: 30}.get((hr >> 5) & 0x03, "?")
                detail = (f"{hr & 0x1F:02d}:{edata[1]:02d}:{edata[2]:02d}:"
                          f"{edata[3]:02d}.{edata[4]:02d} @ {fps} fps")
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
        data = f.read(midimod.MAX_SMF_BYTES)
    chunks = []
    file_warns = []
    if file_size > len(data):
        file_warns.append(
            f"file is {file_size:,} bytes; parsed the first "
            f"{len(data):,} (cap)")
        file_size = len(data)

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
    hdr_warns = []
    if hdr_len > 6:
        fields.append(_f(0x06, hdr_len - 6, "extra_header",
                         f"{hdr_len - 6} bytes", "legal, skipped"))
    elif hdr_len < 6:
        # a negative extra_header length would reach --hex as
        # read(negative), i.e. the whole file. the six header bytes
        # were still decoded above (best effort).
        hdr_warns.append(
            f"MThd declares {hdr_len} bytes, spec minimum is 6")
    summary = f"format {fmt}, {ntrks} track(s)"
    chunks.append({"id": "MThd", "offset": 0, "size": hdr_len,
                   "summary": summary, "fields": fields,
                   "warnings": hdr_warns})

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
                   "warnings": [], "payload_base": 0})

    json_start = raw.find(b"{")
    if json_start < 0:
        file_warns.append("no JSON block after the magic")
        return chunks, file_warns

    text = raw[json_start:].decode("utf-8", errors="replace")
    # RecursionError: the json scanner recurses per nesting level, so a
    # forged preset with thousands of nested objects blows the stack.
    try:
        parsed, end = json.JSONDecoder().raw_decode(text)
    except (ValueError, RecursionError) as e:
        file_warns.append(f"JSON block does not parse: {e.__class__.__name__}: {e}")
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
    # raw_decode's end is a CHARACTER offset into the decoded text; the
    # blob boundary is a BYTE offset, so re-encode the parsed region to
    # measure it. exact for valid UTF-8 (which valid JSON is); off only
    # when the JSON region itself held invalid bytes, where any offset
    # is best-effort.
    end_bytes = len(text[:end].encode("utf-8"))
    chunks.append({"id": "json", "offset": json_start, "size": end_bytes,
                   "summary": f"'{name}' metadata, {len(parsed)} keys",
                   "fields": fields, "warnings": []})

    blob_off = json_start + end_bytes
    chunks.append({"id": "blob", "offset": blob_off,
                   "size": file_size - blob_off,
                   "summary": f"wavetable/modulation data, "
                              f"{file_size - blob_off:,} bytes (opaque)",
                   "fields": [], "warnings": []})
    return chunks, file_warns


# ── bitwig walk ────────────────────────────────────────────────────


def _flac_audio_params(raw):
    """(channels, rate, seconds) from a FLAC STREAMINFO, or None."""
    if len(raw) < 42 or raw[:4] != b"fLaC":
        return None
    packed = struct.unpack_from(">Q", raw, 18)[0]  # STREAMINFO@8, packed field@+10
    rate = (packed >> 44) & 0xFFFFF
    ch = ((packed >> 41) & 0x07) + 1
    total = packed & 0xFFFFFFFFF
    return ch, rate, (total / rate if rate else 0)


def _summarize_embedded(raw):
    """One-line format identity of an embedded asset's bytes."""
    if not raw:
        return "unreadable / too large"
    if raw[:4] == b"fLaC":
        p = _flac_audio_params(raw)
        return f"FLAC, {p[0]}ch {p[1]} Hz, {p[2]:.2f} s" if p else "FLAC"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        return "WAV"
    if raw[:4] == b"OggS":
        return "OGG"
    if raw[:4] == b"BtWg":
        return "Bitwig preset (nested)"
    if raw[:2] == b"PK":
        return "zip"
    return f"{len(raw):,} bytes (opaque)"


def inspect_bitwig(filepath, deep=False):
    """Structural view of a Bitwig BtWg container (.bwpreset/.bwclip): header,
    metadata block, and a note for the embedded-asset zip. With deep (--verbose
    or --frames) it also deconstructs the device/module tree and unzips and
    identifies every embedded asset."""
    file_size = os.path.getsize(filepath)
    # read the whole preset (bounded) so the embedded-asset zip, which can sit
    # past the first few MB, is found. the meta scan is bounded internally.
    with open(filepath, "rb") as f:
        data = f.read(min(file_size, 64 * 1024 * 1024))
    chunks, file_warns = [], []

    ver = bwmod.read_header(data)
    chunks.append({"id": "BtWg", "offset": 0, "size": 14,
                   "summary": f"Bitwig container, format {ver}",
                   "fields": [_f(0x00, 4, "magic", "BtWg"),
                              _f(0x04, 10, "version", ver)],
                   "warnings": [], "payload_base": 0})

    meta = bwmod.parse_meta(data)
    fields = [_f(None, 0, label, meta[key][:200])
              for key, label in bwmod._META_FIELDS if key in meta]
    if meta:
        name = meta.get("device_name", "?")
        cat = meta.get("device_category", "?")
        summary = f"{name} ({cat})"
    else:
        summary = "no meta block decoded"
        file_warns.append("BtWg meta block not decoded")
    chunks.append({"id": "meta", "offset": 0, "size": 0,
                   "summary": summary, "fields": fields, "warnings": []})

    if deep:
        modules = bwmod.parse_structure(data)
        if modules:
            mfields = [_f(None, 0, f"module {i + 1}", m)
                       for i, m in enumerate(modules)]
            chunks.append({"id": "modules", "offset": 0, "size": 0,
                           "summary": f"{len(modules)} devices/modules in the "
                                      "chain (pre-order)",
                           "fields": mfields, "warnings": []})

    zoff = data.find(b"PK\x03\x04")
    if zoff >= 0:
        afields, asum = [], ("embedded asset zip (deflate); unzip for the "
                             "referenced samples/impulses")
        if deep:
            assets = bwmod.list_assets(data)
            afields = [_f(None, 0, name, f"{size:,} bytes, "
                          f"{_summarize_embedded(raw)}")
                       for name, size, raw in assets]
            asum = f"{len(assets)} embedded file(s) (deflate zip)"
        chunks.append({"id": "assets", "offset": zoff,
                       "size": file_size - zoff, "summary": asum,
                       "fields": afields, "warnings": []})
    return chunks, file_warns


# ── vital walk ─────────────────────────────────────────────────────


def inspect_vital(filepath):
    """Structural view of a Vital preset (bare JSON): the top-level metadata
    plus a note that the synth state under 'settings' is opaque."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(file_size, 32 * 1024 * 1024))
    # a fast bytes search for the marker before the full JSON parse: an
    # arbitrary large JSON file that merely starts with '{' is rejected without
    # paying for json.loads. the substring may sit anywhere (some presets emit
    # the big 'settings' object before synth_version).
    if b'"synth_version"' not in data:
        raise _Unsupported("not a Vital preset (no synth_version marker)")
    obj = vitalmod.parse_vital(data)
    if obj is None:
        raise _Unsupported("not a Vital preset (JSON did not parse or lacks "
                           "the synth_version key)")
    fields = []
    for k in vitalmod.META_KEYS:
        v = obj.get(k)
        if v is not None and not isinstance(v, (dict, list)):
            fields.append(_f(None, 0, k, str(v)[:200]))
    settings = obj.get("settings")
    nkeys = len(settings) if isinstance(settings, dict) else 0
    name = obj.get("preset_name") or "unnamed"
    chunks = [{"id": "vital", "offset": 0, "size": file_size,
               "summary": f"'{name}' by {obj.get('author', '?')}, "
                          f"{nkeys} settings keys",
               "fields": fields, "warnings": []}]
    return chunks, []


# ── ncw walk ───────────────────────────────────────────────────────


def inspect_ncw(filepath):
    """Structural view of an NI Compressed Wave (.ncw) file: the audio
    parameters from the header. The compressed blocks are opaque."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        head = f.read(64)
    hdr = ncwmod.parse_header(head)
    if hdr is None:
        raise _Unsupported("not a valid NCW header")
    dur = hdr["num_samples"] / hdr["sample_rate"] if hdr["sample_rate"] else 0
    fields = [
        _f(0x08, 2, "channels", hdr["channels"]),
        _f(0x0A, 2, "bits_per_sample", hdr["bits"]),
        _f(0x0C, 4, "sample_rate", hdr["sample_rate"], "Hz"),
        _f(0x10, 4, "num_samples", hdr["num_samples"],
           f"{dur:.3f} s" if dur else ""),
    ]
    chunks = [{"id": "NCW", "offset": 0, "size": file_size,
               "summary": f"NI Compressed Wave, {hdr['bits']}-bit "
                          f"{hdr['channels']}ch {hdr['sample_rate']} Hz, "
                          f"{dur:.3f} s (compressed audio opaque)",
               "fields": fields, "warnings": [], "payload_base": 0}]
    return chunks, []


# ── mp4 walk ───────────────────────────────────────────────────────


def inspect_mp4(filepath):
    """Structural view of an ISO-BMFF MP4/M4A file: the decoded metadata (from
    udta > meta > ilst and the movie duration) followed by the box tree."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(file_size, 8 * 1024 * 1024))  # box tree from the head
        # metadata lives in moov; non-faststart files (most Apple/ffmpeg output)
        # put moov at EOF, past the head window. locate and read just moov then.
        moov_data = data
        if not any(b["type"] == b"moov" for b in mp4mod.iter_boxes(data)) \
                and file_size > len(data):
            moff, msz = mp4mod.find_moov(filepath, file_size)
            if moff is not None:
                f.seek(moff)
                moov_data = f.read(min(msz, 32 * 1024 * 1024))
    chunks, warns = [], []

    ts, dur = mp4mod.movie_timescale_duration(moov_data)
    dur_s = dur / ts if ts and dur else None
    ainfo = mp4mod.audio_info(moov_data)
    meta = mp4mod.parse_ilst(moov_data)
    mfields = []
    if ainfo:
        codec, ch, rate = ainfo
        codec_names = {"mp4a": "AAC", "alac": "Apple Lossless", "Opus": "Opus",
                       "fLaC": "FLAC", "ac-3": "AC-3", "ec-3": "E-AC-3"}
        desc = codec_names.get(codec, codec)
        if ch:
            desc += f", {ch}ch {rate} Hz"
        mfields.append(_f(None, 0, "codec", desc))
    if dur_s:
        mfields.append(_f(None, 0, "duration", f"{dur_s:.3f} s"))
    for label in ("title", "artist", "album_artist", "album", "year", "genre",
                  "bpm", "composer", "encoder", "comment", "track", "disc",
                  "cover_art", "compilation"):
        if label in meta:
            mfields.append(_f(None, 0, label, str(meta[label])[:200]))
    if mfields:
        title = meta.get("title", "")
        chunks.append({"id": "tags", "offset": 0, "size": 0,
                       "summary": f"'{title}'" if title else "iTunes metadata",
                       "fields": mfields, "warnings": []})

    for b in mp4mod.iter_boxes(data):
        t = b["type"].decode("latin-1", errors="replace")
        summary = ". " * b["depth"] + t
        fields = []
        if b["truncated"]:
            warns.append(f"box {t!r} at 0x{b['offset']:08x} overruns its parent")
            summary += " (overruns parent)"
        elif b["type"] == b"ftyp" and b["depth"] == 0:
            brand = data[b["offset"] + b["hdr"]:b["offset"] + b["hdr"] + 4]
            summary += f"  major brand {brand.decode('latin-1', errors='replace')}"
            fields.append(_f(0x00, 4, "major_brand",
                             brand.decode("latin-1", errors="replace")))
        chunks.append({"id": t[:8], "offset": b["offset"], "size": b["size"],
                       "summary": summary, "fields": fields, "warnings": [],
                       "payload_base": b["offset"] + b["hdr"]})
    return chunks, warns


# ── native instruments walk ────────────────────────────────────────


def inspect_ni(filepath, deep=False):
    """Structural view of a Native Instruments preset: the readable metadata.
    Handles the hsin container (Massive .nmsv, Absynth .nabs, modern Kontakt
    .nki) and the older zlib-XML .ksd (Absynth/KORE). With deep (--verbose or
    --frames) it also FastLZ-decompresses the hsin subtree to report the inner
    preset-state container."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(file_size, 16 * 1024 * 1024))
    if nimod.is_ni_ksd(data):
        meta, kind = nimod.parse_ksd(data), "ksd"
    elif nimod.is_ni_nksf(data):
        meta, kind = nimod.parse_nksf(data), "nksf"
    else:
        meta, kind = nimod.parse_hsin(data), "hsin"
    if not meta:
        raise _Unsupported("not a recognized Native Instruments preset")
    order = ["name", "product", "plugin", "author", "vendor", "bank", "comment",
             "description", "device_type", "version", "tempo", "genre", "key"]
    fields = [_f(None, 0, k, str(meta[k])) for k in order if meta.get(k)]
    for k in meta:
        if k not in order:
            fields.append(_f(None, 0, k, str(meta[k])))
    prod = meta.get("product") or meta.get("plugin") or "NI"
    summary = f"{prod} preset '{meta.get('name', '(unnamed)')}'"
    chunks = [{"id": kind, "offset": 0, "size": file_size, "summary": summary,
               "fields": fields, "warnings": [], "payload_base": 0}]
    if deep and kind == "hsin":
        inner = nimod.decompress_subtree(data)
        if inner is not None:
            nested = nimod.is_ni_hsin(inner)
            chunks.append({"id": "payload", "offset": 0, "size": 0,
                           "summary": "FastLZ-compressed preset state",
                           "fields": [_f(None, 0, "decompressed_size",
                                         f"{len(inner):,} bytes"),
                                      _f(None, 0, "inner_container",
                                         "nested hsin (synth parameter state)"
                                         if nested else "opaque")],
                           "warnings": []})
    return chunks, []


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
    # validate the declared string lengths before slicing: a forged
    # length would otherwise decode the rest of the block (up to the
    # payload cap) as a garbage mime/description string.
    mlen = _bu32(b, pos)
    if pos + 4 + mlen > len(b):
        return "truncated", fields, [
            f"mime_type length {mlen:,} overruns block"]
    mime = b[pos + 4:pos + 4 + mlen].decode("ascii", errors="replace")
    pos += 4 + mlen
    if pos + 4 > len(b):
        return "truncated", fields, ["PICTURE ends before description length"]
    dlen = _bu32(b, pos)
    if pos + 4 + dlen > len(b):
        return "truncated", fields, [
            f"description length {dlen:,} overruns block"]
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


def _flac_cuesheet(b):
    """FLAC CUESHEET (block type 5), RFC 9639 section 8.7. Big-endian.
    396-byte prefix, then per-track 36 bytes + 12 bytes per index point."""
    fields, warns = [], []
    if len(b) < 396:
        return "truncated", fields, [f"CUESHEET is {len(b)} bytes, needs 396"]
    catalog = b[0:128].split(b"\x00")[0].decode("ascii", errors="replace").strip()
    lead_in = struct.unpack_from(">Q", b, 128)[0]
    is_cd = bool(b[136] & 0x80)
    n_tracks = b[395]
    fields.append(_f(0x00, 128, "catalog_number", catalog or "(none)"))
    fields.append(_f(0x80, 8, "lead_in_samples", f"{lead_in:,}"))
    fields.append(_f(0x88, 1, "is_cd", is_cd))
    fields.append(_f(0x18B, 1, "num_tracks", n_tracks))
    pos = 396
    for i in range(n_tracks):
        if pos + 36 > len(b):
            warns.append(f"declares {n_tracks} tracks but payload ends at track {i}")
            break
        offset = struct.unpack_from(">Q", b, pos)[0]
        tnum = b[pos + 8]
        isrc = b[pos + 9:pos + 21].split(b"\x00")[0].decode("ascii", errors="replace").strip()
        ttype = "non-audio" if (b[pos + 21] & 0x80) else "audio"
        preemph = " +pre-emphasis" if (b[pos + 21] & 0x40) else ""
        n_idx = b[pos + 35]
        # the last track is the lead-out: 170 for CD-DA, 255 otherwise.
        lead_out = " (lead-out)" if tnum in (170, 255) else ""
        detail = f"#{tnum}{lead_out}, {ttype}{preemph}, {n_idx} index"
        if isrc:
            detail += f", ISRC {isrc}"
        fields.append(_f(pos, 36 + n_idx * 12, f"track[{i}]",
                         f"offset {offset:,}", detail))
        pos += 36 + n_idx * 12
    summary = f"cue sheet, {n_tracks} track(s)" + (", CD-DA" if is_cd else "")
    return summary, fields, warns


def inspect_flac(filepath):
    """Walk a FLAC file: metadata blocks then the audio-frame region."""
    file_size = os.path.getsize(filepath)
    chunks = []
    file_warns = []
    seen = []
    last_end = 4

    chunks.append({"id": "fLaC", "offset": 0, "size": 4,
                   "summary": "FLAC signature",
                   "fields": [_f(0x00, 4, "magic", "fLaC")], "warnings": [],
                   "payload_base": 0})

    saw_last = False
    for btype, name, off, length, is_last in flacmod.iter_metadata_blocks(filepath):
        seen.append(name)
        last_end = off + 4 + length
        with open(filepath, "rb") as f:
            f.seek(off + 4)
            payload = f.read(min(length, _PAYLOAD_CAP))
        entry = {"id": name, "offset": off, "size": length,
                 "summary": "", "fields": [], "warnings": [],
                 "payload_base": off + 4}  # FLAC block header is 4 bytes
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
                entry["summary"], entry["fields"], entry["warnings"] = \
                    _flac_cuesheet(payload)
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

# ID3v2.2 used 3-character frame ids. without this map a v2.2 tag lists frame
# sizes but never decodes its title/artist/etc.
_ID3V22_TEXT_FRAMES = {
    "TT2": "title", "TP1": "artist", "TAL": "album", "TCO": "genre",
    "TBP": "bpm", "TKE": "initial key", "TYE": "year", "TRK": "track",
    "TSS": "encoder settings", "TEN": "encoded by", "COM": "comment",
}

_VBR_METHODS = {
    0: "unknown", 1: "CBR", 2: "ABR", 3: "VBR (rh)", 4: "VBR (mtrh)",
    5: "VBR (rh2)", 6: "VBR (constrained)",
}

# ID3v1 genre index -> name: 0-79 the original spec, 80-191 the Winamp
# extensions. 255 (and anything past the table) is "none/unknown".
_ID3_GENRES = [
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge",
    "Hip-Hop", "Jazz", "Metal", "New Age", "Oldies", "Other", "Pop", "R&B",
    "Rap", "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska",
    "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient",
    "Trip-Hop", "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical",
    "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel", "Noise",
    "AlternRock", "Bass", "Soul", "Punk", "Space", "Meditative",
    "Instrumental Pop", "Instrumental Rock", "Ethnic", "Gothic", "Darkwave",
    "Techno-Industrial", "Electronic", "Pop-Folk", "Eurodance", "Dream",
    "Southern Rock", "Comedy", "Cult", "Gangsta", "Top 40", "Christian Rap",
    "Pop/Funk", "Jungle", "Native American", "Cabaret", "New Wave",
    "Psychadelic", "Rave", "Showtunes", "Trailer", "Lo-Fi", "Tribal",
    "Acid Punk", "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll",
    "Hard Rock", "Folk", "Folk-Rock", "National Folk", "Swing", "Fast Fusion",
    "Bebob", "Latin", "Revival", "Celtic", "Bluegrass", "Avantgarde",
    "Gothic Rock", "Progressive Rock", "Psychedelic Rock", "Symphonic Rock",
    "Slow Rock", "Big Band", "Chorus", "Easy Listening", "Acoustic", "Humour",
    "Speech", "Chanson", "Opera", "Chamber Music", "Sonata", "Symphony",
    "Booty Bass", "Primus", "Porn Groove", "Satire", "Slow Jam", "Club",
    "Tango", "Samba", "Folklore", "Ballad", "Power Ballad", "Rhythmic Soul",
    "Freestyle", "Duet", "Punk Rock", "Drum Solo", "A capella", "Euro-House",
    "Dance Hall", "Goa", "Drum & Bass", "Club-House", "Hardcore", "Terror",
    "Indie", "BritPop", "Negerpunk", "Polsk Punk", "Beat",
    "Christian Gangsta Rap", "Heavy Metal", "Black Metal", "Crossover",
    "Contemporary Christian", "Christian Rock", "Merengue", "Salsa",
    "Thrash Metal", "Anime", "Jpop", "Synthpop",
]


def _lame_replaygain(word):
    """Decode a LAME 16-bit replay-gain word; None if unset (0x0000)."""
    if word == 0:
        return None
    name = (word >> 13) & 0x07
    sign = (word >> 9) & 0x01
    mag = word & 0x1FF
    db = (-1 if sign else 1) * mag / 10.0
    kind = {1: "radio", 2: "audiophile"}.get(name, "")
    return f"{db:+.1f} dB" + (f" ({kind})" if kind else "")


def _id3v1_fields(tag):
    """Decode a 128-byte ID3v1/v1.1 trailer into display fields; also
    return the title for the chunk summary."""
    def s(a, b):
        return tag[a:b].decode("latin-1", errors="replace").split("\x00")[0].rstrip("\x00 ")
    fields = [
        _f(0x03, 30, "title", s(3, 33)),
        _f(0x21, 30, "artist", s(33, 63)),
        _f(0x3F, 30, "album", s(63, 93)),
        _f(0x5D, 4, "year", s(93, 97)),
    ]
    # ID3v1.1: byte 125 is zero and byte 126 (track) is nonzero, so the
    # comment is only 28 bytes. Otherwise it is a full 30-byte v1.0 comment.
    if tag[125] == 0 and tag[126] != 0:
        fields.append(_f(0x61, 28, "comment", s(97, 125)))
        fields.append(_f(0x7E, 1, "track", tag[126]))
    else:
        fields.append(_f(0x61, 30, "comment", s(97, 127)))
    g = tag[127]
    gname = _ID3_GENRES[g] if g < len(_ID3_GENRES) else ("none" if g == 255
                                                          else f"unknown {g}")
    fields.append(_f(0x7F, 1, "genre", g, gname))
    return fields, s(3, 33)


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
    tag_size = hdr["size"]
    with open(filepath, "rb") as f:
        f.seek(10)
        body = f.read(min(tag_size, _ID3_READ_CAP))
    fields.append(_f(0x03, 1, "version", f"2.{major}.{hdr['revision']}"))
    flags = hdr["flags"]
    flag_bits = []
    if flags & 0x80:
        flag_bits.append("unsync")
    if flags & 0x40:
        flag_bits.append("extended header")
    if flags & 0x20:
        flag_bits.append("experimental")
    if flags & 0x10:
        flag_bits.append("footer")
    fields.append(_f(0x05, 1, "flags", f"0x{flags:02x}",
                     ", ".join(flag_bits) if flag_bits else "none"))
    fields.append(_f(0x06, 4, "tag_size", f"{hdr['size']:,}", "synchsafe"))

    is_v22 = major == 2
    id_len = 3 if is_v22 else 4
    fhdr_len = 6 if is_v22 else 10

    # whole-tag unsynchronisation (flag bit 7) inserts a $00 after every $FF so
    # a frame body cannot masquerade as a frame sync. undo it before reading
    # sizes, or every size past the first $FF byte is wrong. this is a v2.2/v2.3
    # construct: in v2.4 unsync is per-frame and the frame size is the on-disk
    # length, so a global de-escape there would misalign every later frame.
    if flags & 0x80 and major != 4:
        body = body.replace(b"\xff\x00", b"\xff")
        warns.append("tag is unsynchronised; byte offsets shown are logical "
                     "(post-desync), not raw file positions")

    pos = 0
    # skip the extended header (flag bit 6) so it is not misread as a frame.
    if flags & 0x40 and not is_v22 and len(body) >= 4:
        if major == 4:
            ext_size = mp3mod.synchsafe(body[0:4])       # v2.4: size includes itself
        else:
            ext_size = struct.unpack(">I", body[0:4])[0] + 4  # v2.3: excludes the 4
        if 0 < ext_size <= len(body):
            fields.append(_f(10, ext_size, "extended_header",
                             f"{ext_size} bytes", "skipped"))
            pos = ext_size
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
        if data_start + fsize > tag_size:
            # the frame claims to run past the tag's own declared size:
            # a genuine structural error, compared against the true tag
            # size rather than however much we happened to read.
            warns.append(
                f"frame {fid_s!r} size {fsize} overruns the "
                f"{tag_size:,}-byte tag"
            )
            break
        if data_start + fsize > len(body):
            # fits inside the declared tag but past what we read (embedded
            # art beyond the read cap). record the frame and stop cleanly;
            # this is not a spec violation.
            note = "attached picture" if fid_s in ("APIC", "PIC") else ""
            fields.append(_f(10 + pos, fhdr_len + fsize, fid_s,
                             f"{fsize:,} bytes", note or "beyond read cap"))
            break
        raw = body[data_start:data_start + fsize]
        note = (_ID3V22_TEXT_FRAMES if is_v22 else _ID3_TEXT_FRAMES).get(fid_s, "")
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
    frame start: 4-byte header, an optional 2-byte CRC when the frame is
    protected, then the version/channel-dependent side info block."""
    mono = hdr["channel_mode"] == 0b11
    base = 4 + (2 if hdr.get("has_crc") else 0)
    if hdr["version_id"] == 0b11:        # MPEG 1
        return base + (17 if mono else 32)
    return base + (9 if mono else 17)    # MPEG 2 / 2.5


def _parse_vbri(buf, off):
    """Decode a Fraunhofer VBRI header. Returns the Xing-path 4-tuple
    (fields, warns, frame_count, tag). Offsets are frame-relative to match
    the frame0 chunk's payload_base. All fields are big-endian."""
    fields = []
    version = _bu16(buf, off + 4)
    nbytes = _bu32(buf, off + 10)
    frame_count = _bu32(buf, off + 14)
    fields.append(_f(off, 4, "vbr_tag", "VBRI", "VBR (Fraunhofer)"))
    fields.append(_f(off + 4, 2, "version", version))
    fields.append(_f(off + 10, 4, "byte_count", f"{nbytes:,}"))
    fields.append(_f(off + 14, 4, "frame_count", f"{frame_count:,}"))
    return fields, [], frame_count, b"VBRI"


def _parse_xing_lame(filepath, frame_off, hdr):
    """Decode the Xing/Info VBR header and any LAME extension in the
    first frame. Returns (fields, warns, frame_count, tag) where tag is
    b"Xing" (VBR), b"Info" (CBR), or None if no tag is present."""
    fields, warns = [], []
    xoff = _xing_offset(hdr)
    with open(filepath, "rb") as f:
        f.seek(frame_off)
        buf = f.read(max(hdr["frame_length"], xoff + 200, 64))
    # VBRI (Fraunhofer) sits at a fixed offset, 32 bytes past the 4-byte frame
    # header, regardless of channel mode; Xing/Info sit at the side-info-
    # dependent xoff. a frame carries at most one of them.
    if len(buf) >= 36 + 18 and buf[36:40] == b"VBRI":
        return _parse_vbri(buf, 36)
    if xoff + 8 > len(buf):
        return None, [], None, None
    tag = buf[xoff:xoff + 4]
    if tag not in (b"Xing", b"Info"):
        return None, [], None, None
    kind = "VBR" if tag == b"Xing" else "CBR (LAME)"
    fields.append(_f(xoff, 4, "vbr_tag", tag.decode("ascii"), kind))
    flags = _bu32(buf, xoff + 4)
    pos = xoff + 8
    # each optional field is only present if its flag is set; the tag may be
    # truncated after any of them, so bound every read against the buffer.
    frame_count = None
    if flags & 0x01:
        if pos + 4 > len(buf):
            warns.append("Xing header truncated before frame_count")
            return fields, warns, frame_count, tag
        frame_count = _bu32(buf, pos)
        fields.append(_f(pos, 4, "frame_count", f"{frame_count:,}"))
        pos += 4
    if flags & 0x02:
        if pos + 4 > len(buf):
            warns.append("Xing header truncated before byte_count")
            return fields, warns, frame_count, tag
        nbytes = _bu32(buf, pos)
        fields.append(_f(pos, 4, "byte_count", f"{nbytes:,}"))
        pos += 4
    if flags & 0x04:
        if pos + 100 > len(buf):
            warns.append("Xing header truncated before seek table")
            return fields, warns, frame_count, tag
        fields.append(_f(pos, 100, "toc", "100-entry seek table"))
        pos += 100
    if flags & 0x08:
        if pos + 4 > len(buf):
            warns.append("Xing header truncated before quality")
            return fields, warns, frame_count, tag
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
            rg = _lame_replaygain(_bu16(buf, pos + 15))
            if rg:
                fields.append(_f(pos + 15, 2, "replay_gain", rg))
            bitrate = buf[pos + 20]
            if bitrate:
                fields.append(_f(pos + 20, 1, "bitrate", f"{bitrate} kbps",
                                 "min for VBR, target for ABR"))
            delay = (buf[pos + 21] << 4) | (buf[pos + 22] >> 4)
            padding = ((buf[pos + 22] & 0x0F) << 8) | buf[pos + 23]
            fields.append(_f(pos + 21, 3, "gapless", f"delay {delay}, pad {padding}",
                             "encoder delay / padding samples"))
    return fields, warns, frame_count, tag


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
                       "fields": flds, "warnings": warns,
                       "payload_base": 0})  # ID3 field offsets are absolute
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

    try:
        xing_fields, xing_warns, vbr_frames, vbr_tag = \
            _parse_xing_lame(filepath, frame_off, fh)
    except Exception as e:
        xing_fields, xing_warns, vbr_frames, vbr_tag = None, \
            [f"VBR header parse error: {e.__class__.__name__}"], None, None
    # Xing and VBRI both declare VBR; an Info tag is the same structure as
    # Xing written by LAME for CBR streams and must not force the VBR label.
    is_vbr_header = vbr_tag in (b"Xing", b"VBRI")
    if xing_fields is not None:
        fields.extend(xing_fields)
    chunks.append({"id": "frame0", "offset": frame_off, "size": fh["frame_length"],
                   "summary": (f"{fh['version']} {fh['layer']}, {fh['bitrate']} kbps, "
                               f"{fh['sample_rate']} Hz, {fh['channel_mode_name']}"),
                   "fields": fields, "warnings": xing_warns,
                   "payload_base": frame_off})  # fields are frame-relative

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
    walked = count
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
                    "warnings": [], "payload_base": frame_off}
    if vbr_frames and walked and abs(vbr_frames - walked) > max(2, walked // 20):
        frames_entry["warnings"].append(
            f"Xing/VBRI frame_count {vbr_frames:,} diverges from {walked:,} "
            f"frames walked; VBR duration may be wrong")
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
        v1_fields, title = _id3v1_fields(tag)
        chunks.append({"id": "ID3v1", "offset": id3v1_off, "size": 128,
                       "summary": f"ID3v1 trailer, {title or 'untitled'}",
                       "fields": v1_fields,
                       "warnings": [], "payload_base": id3v1_off})
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
    # bright-black (a real palette slot the terminal theme defines) rather
    # than faint (\033[2m): terminals render faint by blending the fg toward
    # the background, which turns muddy on any non-black background. 90 stays
    # legible against whatever background the user's theme actually uses.
    "dim": "\033[90m",
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


def _human_size(n):
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or unit == "TB":
            return f"{int(x)} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024


def _render_pretty(filepath, fmt_label, chunks, file_warns, args):
    """A clean, human-friendly view of the decoded tags/metadata: section per
    chunk, aligned key/value, no byte offsets. Made for presets and tagged
    files (Bitwig, Vital, Serum, MP4 tags, WAV/FLAC/MP3 metadata)."""
    p = _Paint(_color_enabled(args))
    size = os.path.getsize(filepath)
    print(p("id", os.path.basename(filepath)))
    print(p("dim", f"{fmt_label}, {_human_size(size)}"))
    for c in chunks:
        fields = [f for f in c["fields"]
                  if f["value"] not in (None, "") and str(f["value"]).strip()]
        if not fields:
            continue
        print()
        head = c["id"].strip()
        meta = f"  {p('dim', c['summary'])}" if c.get("summary") else ""
        print(p("id", head) + meta)
        w = max(len(f["name"]) for f in fields)
        for f in fields:
            key = p("dim", f"{f['name']:<{w}}")
            note = f"  {p('dim', '(' + str(f['note']) + ')')}" if f["note"] else ""
            print(f"  {key}  {p('val', f['value'])}{note}")
    all_warns = list(file_warns) + [w for c in chunks for w in c["warnings"]]
    if all_warns:
        print()
        print(p("warn", "warnings:"))
        for w in all_warns:
            print(p("warn", f"  ! {w}"))
    return 0


def _render_table(filepath, fmt_label, chunks, file_warns, args, total=None):
    file_size = os.path.getsize(filepath)
    p = _Paint(_color_enabled(args))
    if total is not None and total != len(chunks):
        count = f"showing {len(chunks)} of {total} chunks"
    else:
        count = f"{len(chunks)} chunks"
    print(f"{os.path.basename(filepath)}: {p('id', fmt_label)}, {file_size:,} bytes, "
          f"{count}")
    print()
    print(p("dim", f"  {'idx':<5} {'id':<5} {'offset':<11} {'size':<11} summary"))
    for i, c in enumerate(chunks):
        idx = p("dim", f"[{c.get('_idx', i):>2}]")
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
                    # field offsets are measured from the chunk's payload base.
                    # RIFF/AIFF/RF64/MThd all have an 8-byte id+size header, so
                    # that is the default; formats with a different header (FLAC
                    # blocks: 4 bytes) or whose fields are already absolute (MP3
                    # ID3 tags, MPEG frames, the FLAC/Serum magic) set their own.
                    base = c.get("payload_base")
                    if base is None:
                        base = c["offset"] + 8
                    hx = _hex_bytes(filepath, base + fl["off"], fl["len"])
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


def _id3_tagged_mp3(filepath):
    """A file starting with an ID3v2 tag is MP3 only if the tag does not
    merely wrap a different known container. Some tools prepend an ID3 tag
    to a WAV/AIFF/FLAC; that is not an MP3 and must not be claimed as one."""
    hdr = mp3mod.read_id3v2(filepath)
    if not hdr:
        return True  # "ID3" magic but an unreadable header; treat as an MP3 attempt
    with open(filepath, "rb") as f:
        f.seek(hdr["total"])
        nxt = f.read(4)
    return nxt not in (b"RIFF", b"RF64", b"FORM", b"fLaC", b"MThd")


def _parse_id_list(val):
    """A comma-separated chunk-id list into a normalized set (or None)."""
    if not val:
        return None
    return {x.strip().casefold() for x in val.split(",") if x.strip()}


def _select_chunks(chunks, only, exclude):
    """Filter chunks by --only/--exclude, tagging each survivor with its
    original index so the table keeps truthful [n] and file positions."""
    out = []
    for i, c in enumerate(chunks):
        cid = c["id"].strip().casefold()
        if only is not None and cid not in only:
            continue
        if exclude is not None and cid in exclude:
            continue
        c = dict(c)
        c["_idx"] = i
        out.append(c)
    return out


class _Unsupported(Exception):
    """A file inspect cannot structurally decode; message is user-facing."""


def _walk_file(filepath, deep):
    """Sniff the magic and dispatch to the format walker.

    Returns (fmt_label, chunks, file_warns); raises _Unsupported for a
    file inspect does not decode."""
    with open(filepath, "rb") as f:
        magic = f.read(16)
    if len(magic) >= 12 and magic[:4] == b"RIFF" and magic[8:12] == b"WAVE":
        return ("RIFF/WAVE", *inspect_wav(filepath))
    if len(magic) >= 12 and magic[:4] == b"FORM" and magic[8:12] in (b"AIFF", b"AIFC"):
        form_type = magic[8:12].decode("ascii")
        return (f"IFF/{form_type}", *inspect_aiff(filepath, form_type))
    if len(magic) >= 14 and magic[:4] == b"MThd":
        return ("Standard MIDI File", *inspect_midi(filepath, deep=deep))
    if len(magic) >= 12 and magic[:4] == b"RF64" and magic[8:12] == b"WAVE":
        return ("RF64/WAVE", *inspect_rf64(filepath))
    if magic[:8] == b"XferJson":
        return ("Xfer Serum preset", *inspect_serum(filepath))
    if magic[:4] == b"BtWg":
        return ("Bitwig preset", *inspect_bitwig(filepath, deep=deep))
    if magic[:4] == ncwmod.MAGIC:
        return ("NI Compressed Wave", *inspect_ncw(filepath))
    if magic[:1] == b"{":
        return ("Vital preset", *inspect_vital(filepath))
    if magic[4:8] == b"ftyp":
        return ("MP4/M4A", *inspect_mp4(filepath))
    if magic[12:16] == b"hsin" or magic[:4] == b"-in-" \
            or (magic[:4] == b"RIFF" and magic[8:12] == b"NIKS"):
        return ("Native Instruments preset", *inspect_ni(filepath, deep=deep))
    if magic[:4] == b"fLaC":
        return ("FLAC", *inspect_flac(filepath))
    if magic[:3] == b"ID3" and not _id3_tagged_mp3(filepath):
        raise _Unsupported("ID3 tag wraps a non-MP3 container; not supported")
    if magic[:3] == b"ID3" or (len(magic) >= 4
                               and mp3mod.decode_frame_header(magic[:4]) is not None):
        return ("MP3/MPEG audio", *inspect_mp3(filepath, deep=deep))
    raise _Unsupported("not a WAV, RF64, AIFF, MIDI, Serum, Bitwig, Vital, NCW, "
                       "MP4/M4A, MP3, or FLAC")


def _full_chunk(chunk, filepath):
    """Enrich a chunk for --full into a self-contained record: its absolute
    payload base, the raw region bytes as hex (capped), and every field's
    absolute byte offset. build_explorer.py needs nothing but this JSON."""
    c = {k: v for k, v in chunk.items() if k != "_idx"}
    pb = chunk.get("payload_base", chunk["offset"] + 8)
    c["payload_base"] = pb
    fields = []
    for f in chunk["fields"]:
        f2 = dict(f)
        # absolute file offset, so a field maps to raw[abs - offset]
        f2["abs"] = pb + f["off"] if f["off"] is not None else None
        fields.append(f2)
    c["fields"] = fields
    # only carry raw bytes for chunks that actually have positioned fields;
    # audio-data regions are huge and have nothing to highlight.
    if any(f["off"] is not None for f in chunk["fields"]):
        n = min(chunk["size"], _FULL_RAW_CAP)
        with open(filepath, "rb") as fh:
            fh.seek(chunk["offset"])
            raw = fh.read(n)
        c["raw"] = raw.hex()
        c["raw_base"] = chunk["offset"]
        if chunk["size"] > _FULL_RAW_CAP:
            c["raw_truncated"] = chunk["size"] - _FULL_RAW_CAP
    return c


def run(args):
    # accept either the multi-file `targets` or the legacy single `target`
    targets = getattr(args, "targets", None)
    if not targets:
        one = getattr(args, "target", None)
        targets = [one] if one else []
    if not targets:
        print("acidcat inspect: no target file given", file=sys.stderr)
        return 1

    deep = getattr(args, "frames", False) or getattr(args, "verbose", False)
    full = getattr(args, "full", False)
    as_json = args.format == "json" or full  # --full is a JSON dump
    multi = len(targets) > 1
    only = _parse_id_list(getattr(args, "only", None))
    exclude = _parse_id_list(getattr(args, "exclude", None))
    exit_code = 0

    try:
        for filepath in targets:
            if not os.path.isfile(filepath):
                print(f"acidcat inspect: {filepath}: No such file", file=sys.stderr)
                exit_code = 1
                continue
            try:
                fmt_label, chunks, file_warns = _walk_file(filepath, deep)
            except _Unsupported as e:
                print(f"acidcat inspect: {filepath}: {e}", file=sys.stderr)
                exit_code = 1
                continue
            except Exception as e:  # a walker bug must not sink the whole run
                print(f"acidcat inspect: {filepath}: {e.__class__.__name__}: {e}",
                      file=sys.stderr)
                exit_code = 1
                continue

            total = len(chunks)
            shown = _select_chunks(chunks, only, exclude)

            if as_json:
                # NDJSON: one compact record per file per line, so the stream
                # pipes cleanly into jq -c and other line-oriented tools.
                if full:
                    out_chunks = [_full_chunk(c, filepath) for c in shown]
                else:
                    out_chunks = [{k: v for k, v in c.items() if k != "_idx"}
                                  for c in shown]
                sys.stdout.write(json.dumps({
                    "file": filepath,
                    "format": fmt_label,
                    "size": os.path.getsize(filepath),
                    "full": full,
                    "chunks": out_chunks,
                    "warnings": file_warns,
                }) + "\n")
            else:
                pretty = getattr(args, "pretty", False)
                if multi and not pretty:
                    print(f"\nFile: {filepath}")  # readelf-style per-file banner
                elif multi:
                    print()  # separate files; --pretty prints its own name header
                if pretty:
                    _render_pretty(filepath, fmt_label, shown, file_warns, args)
                else:
                    _render_table(filepath, fmt_label, shown, file_warns, args, total)
    except BrokenPipeError:
        # a downstream pager or `head` closed the pipe: exit quietly the way
        # cat and grep do, without a traceback.
        try:
            sys.stdout.close()
        except Exception:
            pass
        return exit_code

    return exit_code
