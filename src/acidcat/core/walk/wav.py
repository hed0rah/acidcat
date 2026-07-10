"""RIFF/WAVE structural walker: per-chunk field decoding for inspect."""

import os
import struct

from acidcat.core.riff import iter_chunks
from acidcat.core.walk.base import (
    _PAYLOAD_CAP, _f, _u16, _u32, _cstr, _flag_names,
)
from acidcat.util.midi import midi_note_to_name

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


# ── per-chunk parsers ──────────────────────────────────────────────
# each returns (summary, fields, warnings) and may read/update ctx,
# which accumulates cross-chunk facts (sample rate, frame count...)


def _parse_fmt(b, ctx):
    fields, warns = [], []
    if len(b) < 16:
        return "truncated", fields, [f"fmt payload is {len(b)} bytes, spec minimum is 16"]
    tag, ch, rate, avg, align, bits = struct.unpack_from("<HHIIHH", b, 0)
    tag_name = _FORMAT_TAGS.get(tag, f"unknown 0x{tag:04x}")
    fields.append(_f(0x00, 2, "format_tag", f"0x{tag:04x}", tag_name,
                     enc="<H", raw=tag))
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

    # a WAVEFORMATEX (extended, non-extensible) carries a cbSize at 0x10;
    # its extension bytes are format-specific and worth breaking out.
    if tag != 0xFFFE and len(b) >= 18:
        cb = _u16(b, 0x10)
        fields.append(_f(0x10, 2, "cb_size", cb, "extension bytes"))
        ext = b[0x12:0x12 + cb]
        if tag == 0x0002 and len(ext) >= 4:        # MS ADPCM
            spb, ncoef = _u16(ext, 0), _u16(ext, 2)
            fields.append(_f(0x12, 2, "samples_per_block", spb))
            fields.append(_f(0x14, 2, "num_coef_pairs", ncoef))
            pairs = []
            for i in range(min(ncoef, (len(ext) - 4) // 4)):
                c1, c2 = struct.unpack_from("<hh", ext, 4 + i * 4)
                pairs.append(f"({c1},{c2})")
            if pairs:
                std = pairs[:7] == ["(256,0)", "(512,-256)", "(0,0)",
                                    "(192,64)", "(240,0)", "(460,-208)",
                                    "(392,-232)"] and ncoef == 7
                fields.append(_f(0x16, len(pairs) * 4, "adpcm_coefficients",
                                 " ".join(pairs),
                                 "the standard predictor set" if std
                                 else "custom predictors"))
                if ncoef > len(pairs):
                    warns.append(f"declares {ncoef} coefficient pairs but the "
                                 f"extension holds {len(pairs)}")
        elif tag == 0x0011 and len(ext) >= 2:      # IMA/DVI ADPCM
            fields.append(_f(0x12, 2, "samples_per_block", _u16(ext, 0)))
        elif tag == 0x0055 and len(ext) >= 12:     # MPEGLAYER3WAVEFORMAT
            wid = _u16(ext, 0)
            fdw = _u32(ext, 2)
            pad = {0: "ISO padding", 1: "padding always", 2: "padding never"}
            fields.append(_f(0x12, 2, "mp3_id", wid,
                             "MPEGLAYER3_ID_MPEG" if wid == 1 else ""))
            fields.append(_f(0x14, 4, "mp3_flags", f"0x{fdw:x}",
                             pad.get(fdw & 0x3, ""), enc="<I", raw=fdw))
            fields.append(_f(0x18, 2, "block_size", _u16(ext, 6), "bytes/frame"))
            fields.append(_f(0x1A, 2, "frames_per_block", _u16(ext, 8)))
            fields.append(_f(0x1C, 2, "codec_delay", _u16(ext, 10), "samples"))

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
                         _channel_mask_names(mask), enc="<I", raw=mask))
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
    fields.append(_f(0x00, 4, "type_flags", f"0x{flags:08x}",
                     _flag_names(flags, _ACID_FLAGS), enc="<I", raw=flags))
    fields.append(_f(0x04, 2, "root_note", root, midi_note_to_name(root) if root else "unset"))
    fields.append(_f(0x06, 2, "unknown1", f"0x{q1:04x}", enc="<H", raw=q1))
    fields.append(_f(0x08, 4, "unknown2", round(q2, 4)))
    fields.append(_f(0x0C, 4, "num_beats", beats))
    fields.append(_f(0x10, 2, "meter_denominator", denom))
    fields.append(_f(0x12, 2, "meter_numerator", numer))
    fields.append(_f(0x14, 4, "tempo", round(tempo, 2), "BPM"))

    ctx["acid_bpm"] = round(tempo, 2) if tempo else None
    ctx["acid_root"] = root
    ctx["acid_beats"] = beats
    ctx["acid_one_shot"] = bool(flags & 0x01)
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
    # all nine header fields are unsigned DWORDs (dwSMPTEFormat/dwSMPTEOffset
    # included); a signed read would show large offsets as negatives
    (manuf, product, period, unity, frac,
     smpte_fmt, smpte_off, n_loops, vendor) = struct.unpack_from("<IIIIIIIII", b, 0)
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

    ctx["smpl_root"] = unity
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
        # split out the loop type (a little-endian u32) as its own editable field
        fields.append(_f(base + 4, 4, f"loop[{i}]_type", ltype, type_name))
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
    # bUnshiftedNote is an unsigned BYTE (0-127); chFineTune and chGain are
    # signed CHARs
    base, detune, gain = struct.unpack_from("<Bbb", b, 0)
    low_n, high_n, low_v, high_v = b[3], b[4], b[5], b[6]
    fields = [
        _f(0x00, 1, "base_note", base, midi_note_to_name(base) if base <= 127 else ""),
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
        note = f"id {cid}, play order {pos}, in '{fcc.decode('ascii', errors='replace')}'"
        fields.append(_f(base, 24, f"cue[{i}]", sample, note))
        if cstart or bstart:
            # nonzero only for block-compressed data: byte offset of the
            # enclosing chunk and of the block holding the sample
            fields.append(_f(base + 12, 8, f"cue[{i}]_block",
                             f"chunk_start {cstart}, block_start {bstart}",
                             "compressed-data addressing"))
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


def _parse_cart(b, ctx):
    """AES46 / RIFF Cart chunk: radio-automation metadata. A fixed 2048-byte
    layout (title, artist, cut id, category, start/end dates, producer app, level
    reference, 8 post-timers, url) followed by freeform tag text."""
    fields, warns = [], []
    if len(b) < 0x2AC:
        return "truncated", fields, [f"cart payload is {len(b)} bytes, header needs 2048"]

    def s(off, n):
        return _cstr(b, off, n)

    fields.append(_f(0x000, 4, "version", s(0, 4)))
    for off, n, name in ((0x004, 64, "title"), (0x044, 64, "artist"),
                         (0x084, 64, "cut_id"), (0x104, 64, "category"),
                         (0x144, 64, "classification"), (0x184, 64, "out_cue")):
        fields.append(_f(off, n, name, s(off, n)))
    fields.append(_f(0x1C4, 18, "start", (s(0x1C4, 10) + " " + s(0x1CE, 8)).strip()))
    fields.append(_f(0x1D6, 18, "end", (s(0x1D6, 10) + " " + s(0x1E0, 8)).strip()))
    fields.append(_f(0x1E8, 64, "producer_app", s(0x1E8, 64)))
    fields.append(_f(0x228, 64, "producer_version", s(0x228, 64)))
    fields.append(_f(0x2A8, 4, "level_reference", struct.unpack_from("<i", b, 0x2A8)[0]))
    for i in range(8):                                   # 8 post-timers: usage[4] + value
        o = 0x2AC + i * 8
        if o + 8 > len(b):
            break
        usage = _cstr(b, o, 4)
        if usage:
            fields.append(_f(o, 8, f"timer_{usage}", _u32(b, o + 4), "sample offset"))
    if len(b) >= 0x400:
        url = _cstr(b, 0x400, min(1024, len(b) - 0x400))
        if url:
            fields.append(_f(0x400, len(url), "url", url[:120]))
    if len(b) > 0x800:
        tag = b[0x800:].split(b"\x00")[0].decode("latin-1", "replace").strip()
        if tag:
            fields.append(_f(0x800, len(b) - 0x800, "tag_text", tag[:120]))
    title, artist = s(0x04, 64), s(0x44, 64)
    summary = f"Cart: {title or 'untitled'}" + (f" by {artist}" if artist else "")
    return summary, fields, warns


def _parse_ixml(b, ctx):
    """iXML: field-recorder XML metadata (project, scene, take, tape, note,
    circled, track list) written by Sound Devices / Zoom / Tascam and friends.
    Surfaces the common tags; the whole payload is the XML."""
    import re
    fields = []
    text = b.split(b"\x00")[0].decode("utf-8", "replace")

    def tag(name):
        m = re.search(rf"<{name}>(.*?)</{name}>", text, re.I | re.S)
        return m.group(1).strip() if m else None

    for name in ("IXML_VERSION", "PROJECT", "SCENE", "TAKE", "TAPE", "NOTE",
                 "CIRCLED", "FILE_SET_INDEX"):
        v = tag(name)
        if v:
            fields.append(_f(None, 0, name.lower(), v[:80]))
    tracks = len(re.findall(r"<TRACK>", text, re.I))
    if tracks:
        fields.append(_f(None, 0, "track_count", tracks))
    if not fields:
        fields.append(_f(0, len(b), "xml", text[:100].replace("\n", " ")))
    scene, take = tag("SCENE"), tag("TAKE")
    summary = "iXML" + (f": scene {scene}" if scene else "") + (f" take {take}" if take else "")
    return summary, fields, []


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
    "cart": _parse_cart,
    "iXML": _parse_ixml,
}


# ── walk ───────────────────────────────────────────────────────────


def inspect_wav(filepath, ctx=None):
    """Walk a WAV file and return (chunks, file_warnings).

    Each chunk is a dict: id, offset, size, summary, fields, warnings.
    A caller-supplied ``ctx`` dict is filled with the semantic values the
    per-chunk parsers accumulate (sample_rate, duration, acid_bpm,
    smpl_root, ...) -- the scan/index path reads those instead of running
    a second decoder over the same bytes.
    """
    file_size = os.path.getsize(filepath)
    if ctx is None:
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
