"""RIFF/WAVE structural walker: per-chunk field decoding for inspect."""

import struct

from acidcat.core.formats.riff import iter_chunks
from acidcat.core.infra.findings import defect
from acidcat.core.infra.vocab import (WAVE_FORMAT_TAGS as _FORMAT_TAGS,
                                WAV_SPEAKER_POSITIONS as _SPEAKER_POSITIONS,
                                KSDATAFORMAT_TAIL as _KSDATAFORMAT_TAIL)
from acidcat.core.infra.limits import hit
from acidcat.core.primitives.notes import is_coverage
from acidcat.core.walk.apple import _parse_apple_meta, _parse_resu
from acidcat.core.walk.base import (
    VENDOR_CHUNKS, _PAYLOAD_CAP, _dtext, _f, _u16, _u32, _cstr, _flag_names,
    parse_opaque, parse_padding, _open, _size,
)
from acidcat.util.midi import midi_note_to_name

_ACID_FLAGS = (
    (0x01, "one-shot"),
    (0x02, "root set"),
    (0x04, "stretch"),
    (0x08, "disk-based"),
)

_LOOP_TYPES = {0: "forward", 1: "ping-pong", 2: "reverse"}

# WAVEFORMATEXTENSIBLE dwChannelMask bit positions, low bit first.
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

    if tag == 1 and ch and bits and align != ch * ((bits + 7) // 8):
        warns.append(f"block_align {align} != channels*ceil(bits/8) = {ch * ((bits + 7) // 8)}")
    if tag == 1 and rate and align and avg != rate * align:
        warns.append(f"avg_bytes_per_sec {avg} != sample_rate*block_align = {rate * align}")
    # physically implausible but structurally valid values -- a crafted-file
    # tell. Bounds are deliberately generous (real audio never trips them):
    # 8-channel surround and 384 kHz masters are fine; 255 channels or a 1 Hz
    # rate are not.
    if rate and not (1000 <= rate <= 768000):
        warns.append(f"sample_rate {rate} Hz is outside any plausible range")
    if ch > 64:
        warns.append(f"{ch} channels is implausibly high")

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


# A writer streaming to a pipe cannot know its length, so it writes a placeholder
# and the reader is expected to read to EOF. These are not damage, and reporting
# them as a size mismatch says the same thing about a streamed file as about a
# truncated one. 0xFFFFFFFF is ffmpeg's, 0x7FFFF000 is SoX's, and 0 is written by
# several. A declared size of 0 is the dangerous one: taken literally it reports
# a file full of audio as empty.
_STREAM_SENTINELS = {
    0: "zero, the most common placeholder",
    0xFFFFFFFF: "the 32-bit maximum",
    0x7FFFF000: "the largest sample-aligned 31-bit value",
}


def _parse_data(b, ctx, size, avail=None):
    fields, warns = [], []
    align = ctx.get("block_align")
    rate = ctx.get("sample_rate")
    # a declared size larger than the bytes actually present is a lie we
    # already lint at the file level; never derive frames/duration from it.
    # a sentinel means "I did not know", so the bytes present ARE the payload and
    # there is nothing to reconcile. Checked before the overrun test, which would
    # otherwise report a streamed file as a size mismatch.
    streaming = size in _STREAM_SENTINELS and avail is not None and (
        size == 0 and avail > 0 or size > avail)
    overrun = (not streaming) and avail is not None and size > avail
    eff = avail if (overrun or streaming) else size
    fact = ctx.get("fact_samples")
    # bytes / block_align is the frame count only for uncompressed audio.
    # block-compressed formats (ADPCM) pack many samples per block, so trust
    # the fact chunk's sample count when present. on an overrun we still
    # derive from the bytes actually present, never a declared count.
    frames = None
    if overrun or streaming:
        if align:
            frames = eff // align
    elif fact is not None:
        frames = fact
    elif align:
        frames = eff // align
    if frames is not None:
        ctx["frames"] = frames
    if streaming:
        summary = (f"audio payload, {eff:,} bytes, size field is a streaming "
                   f"placeholder ({_STREAM_SENTINELS[size]})")
        warns.append("the data size is a streaming placeholder, so the payload "
                     "runs to the end of the file; this is how a writer records "
                     "audio it cannot measure in advance, not damage")
    elif overrun:
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
    if size == 0 and not streaming:
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
        if i == 0:                       # first loop, for the scan/info row
            ctx["smpl_loop_start"] = start
            ctx["smpl_loop_end"] = end
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
        # the marker's sample-frame position resolves to a byte offset inside the
        # data chunk (data_off + frame*block_align), an xref the TUI can follow.
        # only for uncompressed PCM, where dwSampleOffset counts frames not bytes.
        data_off = ctx.get("data_off")
        align = ctx.get("block_align")
        xref = None
        if data_off is not None and align:
            target = data_off + sample * align
            if sample * align <= ctx.get("data_bytes", 0):
                note += f", @ 0x{target:08x}"
                xref = target
        fields.append(_f(base, 24, f"cue[{i}]", sample,
                         note + " (sample frame)", xref=xref))
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
            warns.append(defect("size.overrun", f"sub-chunk {sub_id!r} overruns LIST payload"))
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


def _parse_clm(b, ctx):
    """Steinberg/Xfer wavetable marker: ASCII, and the frame size is the point.

    A Serum wavetable is a plain WAV whose samples are N frames of a fixed
    length laid end to end. Nothing in fmt or data says so -- without this
    chunk the file is one long single-cycle-looking waveform, and with it the
    frame size is stated, so the frame COUNT is arithmetic rather than a guess.

    The payload is text: "<!>" then the frame size, then eight hex digits of
    flags, then a vendor string. Parsed by splitting rather than by a fixed
    layout, because the only part with a documented position is the "<!>".
    """
    fields, warns = [], []
    text = _dtext(b).strip("\x00").strip()
    if not text:
        return "empty", fields, ["clm chunk carries no text"]
    fields.append(_f(0x00, len(b), "marker", text[:160]))
    frame = None
    if text.startswith("<!>"):
        parts = text[3:].split()
        if parts and parts[0].isdigit():
            frame = int(parts[0])
            fields.append(_f(None, 0, "frame_size", f"{frame:,}",
                             "samples per wavetable frame"))
        if len(parts) > 1 and len(parts[1]) == 8:
            # eight hex digits whose meaning is not documented anywhere
            # available here. Reported as the bytes they are rather than
            # decoded into flags nobody has verified.
            fields.append(_f(None, 0, "flags", parts[1], "undecoded"))
    else:
        warns.append("clm text does not open with the '<!>' marker")

    # the frame COUNT, which is what a reader actually wants, and only
    # derivable once the data chunk's length is known
    frames = ctx.get("frames")
    summary = "wavetable marker"
    if frame and frames:
        n, rem = divmod(frames, frame)
        summary = f"wavetable, {n:,} frames of {frame:,} samples"
        fields.append(_f(None, 0, "frames", f"{n:,}",
                         "data length divided by the frame size"))
        if rem:
            warns.append(defect(
                "length.misaligned",
                f"{frames:,} sample frames is not a whole number of "
                f"{frame:,}-sample wavetable frames ({rem:,} trail)"))
    elif frame:
        summary = f"wavetable, {frame:,}-sample frames"
    if "xfer" in text.lower():
        fields.append(_f(None, 0, "writer", "Xfer Records (Serum)"))
    return summary, fields, warns


# strc: the ACID/Sony slice table. A 28-byte header then fixed-width records,
# and the stride is DERIVED rather than assumed: sixteen of seventeen real
# files measured use 32-byte records and one older writer does not, so a
# hardcoded stride reports the odd one out as garbage.
_STRC_HEADER = 28
_STRC_POS_OFF = 8          # the ascending sample position inside a record


def _parse_strc(b, ctx):
    """ACID stretch/slice markers: where the beats are.

    Verified against a 126 BPM loop: sixteen markers 21,000 samples apart at
    44.1 kHz is 0.4762 s, which is 126.00 BPM exactly, and 16 x 21,000 is the
    whole data chunk. They are beat positions, not arbitrary offsets.

    Only the header and the position column are decoded. The rest of each
    record is left as bytes: nothing available here documents it, and a walker
    that names a field it has not confirmed is the more expensive kind of
    wrong.
    """
    fields, warns = [], []
    if len(b) < _STRC_HEADER:
        return "truncated", fields, [
            f"strc payload is {len(b)} bytes, the header alone is {_STRC_HEADER}"]
    hdr_size, count = struct.unpack_from("<II", b, 0)
    fields.append(_f(0x00, 4, "header_size", hdr_size))
    fields.append(_f(0x04, 4, "slices", count))
    if hdr_size != _STRC_HEADER:
        warns.append(f"strc header declares {hdr_size} bytes, not the "
                     f"{_STRC_HEADER} every measured file uses")

    body = len(b) - _STRC_HEADER
    if count <= 0:
        return "no slices", fields, warns
    stride, rem = divmod(body, count)
    if rem or stride < 12:
        warns.append(f"{body:,} bytes of slice records does not divide into "
                     f"{count} whole records; positions not read")
        return f"{count} slice(s), record layout unreadable", fields, warns
    fields.append(_f(None, 0, "record_size", stride, "derived, not assumed"))

    positions = []
    for i in range(count):
        o = _STRC_HEADER + i * stride
        if o + _STRC_POS_OFF + 4 > len(b):
            break
        positions.append(struct.unpack_from("<I", b, o + _STRC_POS_OFF)[0])
    if positions and positions != sorted(positions):
        warns.append("slice positions are not ascending; the record layout "
                     "may differ in this file and they are reported as read")

    for i, pos in enumerate(positions[:_STRC_SLICE_CAP]):
        fields.append(_f(_STRC_HEADER + i * stride + _STRC_POS_OFF, 4,
                         f"slice[{i}]", f"{pos:,}", "sample position"))
    if len(positions) > _STRC_SLICE_CAP:
        warns.append(hit("list_rows", _STRC_SLICE_CAP, len(positions),
                         f"listing the first {_STRC_SLICE_CAP} of "
                         f"{len(positions):,} slice positions"))

    # the implied tempo, when the markers are evenly spaced. Stated only when
    # they ARE even: an uneven set is a transient map rather than a beat grid,
    # and a tempo derived from it would be a number that means nothing.
    summary = f"{count} slice marker(s)"
    rate = ctx.get("sample_rate")
    if len(positions) > 2 and rate:
        gaps = {b - a for a, b in zip(positions, positions[1:])}
        if len(gaps) == 1:
            gap = gaps.pop()
            if gap > 0:
                bpm = 60.0 / (gap / float(rate))
                fields.append(_f(None, 0, "implied_tempo", f"{bpm:.2f} BPM",
                                 f"{gap:,} samples between evenly spaced markers"))
                summary += f", evenly spaced -- {bpm:.2f} BPM"
    return summary, fields, warns


def _parse_riff_id3(b, ctx):
    """An ID3v2 tag inside a RIFF chunk.

    The same tag acidcat reads at the front of an MP3 and inside an AIFF
    chunk, through the same reader: `formats.mp3.id3v2_from_bytes`. There were
    two readers before, and they disagreed -- a tag whose size is written
    little-endian read correctly here and not in AIFF.

    Registered under BOTH spellings in _PARSERS. Chunk ids are matched exactly,
    and registering only the lowercase one left 25 files in a real library
    reporting an uppercase `ID3 ` chunk as unparsed bytes, each carrying a TBPM
    frame that was simply dropped.
    """
    from acidcat.core.formats import mp3 as mp3mod
    fields = []
    header, frames, warns = mp3mod.id3v2_from_bytes(b)
    if header is None:
        return "not an ID3v2 tag", fields, ["id3 chunk does not open with 'ID3'"]
    fields.append(_f(0x00, 3, "magic", "ID3"))
    fields.append(_f(0x03, 2, "version",
                     f"2.{header['major']}.{header['revision']}"))
    fields.append(_f(0x05, 1, "flags", f"0x{header['flags']:02x}"))
    fields.append(_f(0x06, 4, "tag_size", f"{header['size']:,}",
                     header["size_note"], enc="synchsafe", raw=header["size"]))
    for fid, text in frames[:_ID3_FRAME_CAP]:
        fields.append(_f(None, 0, fid, str(text)[:160]))
    if len(frames) > _ID3_FRAME_CAP:
        warns = list(warns) + [hit(
            "list_rows", _ID3_FRAME_CAP, len(frames),
            f"listing the first {_ID3_FRAME_CAP} of {len(frames)} ID3 frames")]
    n = len(frames)
    return (f"ID3v2.{header['major']} tag, {header['size']:,} bytes"
            + (f", {n} frame(s)" if n else "")), fields, list(warns)


# Listing bounds. A slice table can be hundreds of markers and an ID3 tag
# dozens of frames; a listing that silently stops is worse than one that
# says it did.
_STRC_SLICE_CAP = 64
_ID3_FRAME_CAP = 40
# An XMP packet is a whole catalogue record; a real one holds a dozen or two
# properties. The bound is a listing bound, not a parse bound.
_XMP_PROPERTY_CAP = 40
# Peak records are one per channel. 64 is far above any real file and
# bounds a crafted chunk rather than a normal one.
_PEAK_CHANNEL_CAP = 64
# A playlist is a handful of segments. The bound is for a crafted one.
_PLST_SEGMENT_CAP = 64
# A trigger list record, fixed. 4 + count * 24 is the whole payload in all 30
# specimens measured, across four different payload sizes.
_TLST_RECORD = 24
# The most triggers to list individually. The declared count is always
# reported, so a shortened listing never reads as the whole list. The largest
# real one measured has eight.
_TLST_RECORD_CAP = 64
# MIDI's Note On status byte, which is what the trigger word's third byte is.
_MIDI_NOTE_ON = 0x90
# Every SAUR measured is exactly this long.
_SAUR_SIZE = 32
# And every chrp.
_CHRP_SIZE = 12


# Windows clipboard format ids, for the DISP chunk's first word. Only the few
# that occur in audio files: DISP was meant for any clipboard payload and in
# practice carries a title or a thumbnail.
_CF = {1: "CF_TEXT", 2: "CF_BITMAP", 3: "CF_METAFILEPICT", 6: "CF_TIFF",
       7: "CF_OEMTEXT", 8: "CF_DIB", 9: "CF_PALETTE", 13: "CF_UNICODETEXT",
       14: "CF_ENHMETAFILE", 17: "CF_DIBV5"}


def _parse_disp(b, ctx):
    """RIFF DISP: a clipboard payload, usually a title or a thumbnail.

    The first u32 is a Windows clipboard format id and the rest is that
    format's own bytes, so what follows cannot be read without reading it
    first. A DISP treated as text prints a bitmap header as mojibake.
    """
    fields, warns = [], []
    if len(b) < 4:
        return "truncated", fields, ["DISP payload is shorter than its format id"]
    cf = _u32(b, 0)
    name = _CF.get(cf, f"clipboard format {cf}")
    fields.append(_f(0x00, 4, "clipboard_format", cf, name, enc="<I", raw=cf))
    body = b[4:]
    if cf in (1, 7):                       # CF_TEXT / CF_OEMTEXT
        text = _dtext(body).strip("\x00").strip()
        fields.append(_f(0x04, len(body), "text", text[:200]))
        return f"{name}: {text[:60]}" if text else name, fields, warns
    if cf == 13 and len(body) >= 2:        # CF_UNICODETEXT
        text = body.decode("utf-16-le", "replace").split("\x00")[0]
        fields.append(_f(0x04, len(body), "text", text[:200]))
        return f"{name}: {text[:60]}", fields, warns
    if cf in (8, 17) and len(body) >= 16:  # CF_DIB / CF_DIBV5
        # a BITMAPINFOHEADER: its own size, then a signed width and height
        hdr, width, height = struct.unpack_from("<Iii", body, 0)
        planes, bits = struct.unpack_from("<HH", body, 12)
        fields.append(_f(0x04, 4, "dib_header_size", hdr))
        fields.append(_f(0x08, 4, "width", width))
        fields.append(_f(0x0C, 4, "height", height,
                         "negative means the rows are top-down"))
        fields.append(_f(0x12, 2, "bits_per_pixel", bits))
        if planes != 1:
            warns.append(f"DIB declares {planes} colour planes; the format "
                         f"allows only 1")
        return (f"{name}, {width}x{abs(height)} at {bits} bpp"), fields, warns
    return f"{name}, {len(body):,} bytes", fields, warns

def _parse_padding(b, ctx):
    """JUNK / FLLR / PAD, through the reader every container shares."""
    return parse_padding(b)

def _parse_cset(b, ctx):
    """RIFF CSET: the code page the text chunks in this file are written in.

    Worth reading because everything else that holds text -- INFO, (c), DISP --
    is bytes until something says how to decode them, and without CSET a reader
    is assuming. Rare in practice, which is why the assumption usually holds.
    """
    fields, warns = [], []
    if len(b) < 8:
        return "truncated", fields, [
            f"CSET payload is {len(b)} bytes, the spec fixes it at 8"]
    page, country, lang, dialect = struct.unpack_from("<HHHH", b, 0)
    fields.append(_f(0x00, 2, "code_page", page,
                     "0 means the system default", enc="<H", raw=page))
    fields.append(_f(0x02, 2, "country_code", country))
    fields.append(_f(0x04, 2, "language", lang))
    fields.append(_f(0x06, 2, "dialect", dialect))
    return (f"code page {page}" if page else "system default code page"), fields, warns


def _parse_copyright(b, ctx):
    """The top-level '(c) ' chunk: a copyright string, outside any INFO list."""
    fields, warns = [], []
    text = _dtext(b).strip("\x00").strip()
    fields.append(_f(0x00, len(b), "copyright", text[:200]))
    return (text[:70] if text else "empty"), fields, warns


# Apple's typedstream: the archive format NSArchiver wrote before
# NSKeyedArchiver, still emitted by Logic and Final Cut into AFAn/AFmd. It
# opens with a version byte and the literal "streamtyped".

def _parse_xmp(b, _ctx):
    """`_PMX`: an XMP packet, which is RDF/XML.

    The same packet an MP4 carries in a `uuid` box and a JPEG in an APP1
    segment, so the reader is shared. A sound library writes its whole
    catalogue record here -- description, publisher, artist, genre -- and the
    chunk was being reported as unparsed bytes with an XML document inside it.
    """
    from acidcat.core.formats import xmp as xmpmod
    fields, warns = [], []
    if not xmpmod.is_xmp(b):
        return (f"unrecognized, {len(b):,} bytes"), fields, [
            "_PMX does not open as an XMP packet"]
    props, pw = xmpmod.parse_xmp(b)
    warns.extend(pw)
    for name, value in props[:_XMP_PROPERTY_CAP]:
        fields.append(_f(None, 0, name, value))
    if len(props) > _XMP_PROPERTY_CAP:
        warns.append(hit("list_rows", _XMP_PROPERTY_CAP, len(props),
                         f"listing the first {_XMP_PROPERTY_CAP} of "
                         f"{len(props)} XMP properties"))
    named = dict(props)
    bits = [named[k] for k in ("xmp:CreatorTool", "dc:publisher", "xmpDM:artist")
            if named.get(k)]
    summary = f"XMP packet, {len(b):,} bytes"
    if bits:
        summary += " -- " + ", ".join(bits[:2])
    return summary, fields, warns


def _parse_minf(b, _ctx):
    """`minf`: sixteen bytes, and the first eight are a Windows FILETIME.

    Nothing in the chunk says so. The reading is shown rather than asserted,
    and it is offered because it is the one that produces sane answers: read as
    FILETIME the field lands in the years the files were made, and read any
    other common way it does not.
    """
    fields, warns = [], []
    if len(b) < 16:
        return "truncated", fields, [
            f"minf payload is {len(b)} bytes, the structure is 16"]
    stamp, flag = struct.unpack_from("<QI", b, 0)
    fields.append(_f(0x00, 8, "timestamp", f"0x{stamp:016x}",
                     _filetime(stamp), enc="<Q", raw=stamp))
    fields.append(_f(0x08, 4, "flag", flag, "1 or 2 in every file measured; "
                                            "meaning unknown"))
    date = _filetime(stamp)
    return (f"{_AVID_CHUNKS['minf']}, {date}" if date
            else f"{_AVID_CHUNKS['minf']}, {len(b):,} bytes"), fields, warns


def _filetime(v):
    """A Windows FILETIME: 100-nanosecond ticks since 1601-01-01 UTC."""
    import datetime
    if not 0 < v < (1 << 63):
        return ""
    try:
        d = (datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
             + datetime.timedelta(microseconds=v // 10))
    except (OverflowError, ValueError):
        return ""
    if not 1980 <= d.year <= 2100:
        return ""
    return f"reads as a Windows FILETIME: {d.date().isoformat()}"


# Chunks Pro Tools writes. Named rather than decoded: there is no published
# layout for any of them, and a field map guessed from one vendor's files is a
# guess that reads like a fact. What IS certain is which tool wrote the file,
# and that is worth more than four bytes of speculation.
_AVID_CHUNKS = {
    "umid": "Avid/Pro Tools material identifier",
    "minf": "Avid/Pro Tools media info",
    "regn": "Avid/Pro Tools region table",
    "elm1": "Avid/Pro Tools element data",
    "elmo": "Avid/Pro Tools element data",
    "DGDA": "Digidesign analysis data",
}


# The table lives in walk/base.py: these ids turn up in more than one
# container, and AIFF reads the same set.
_AVID_CHUNKS = VENDOR_CHUNKS          # the name tests and the cap ledger use


def _vendor_parser(what):
    def parse(b, _ctx):
        return parse_opaque(b, what)
    return parse



def _peak_date(v):
    """The PEAK timestamp, which the format defines as Unix seconds."""
    import datetime
    if not 0 < v < (1 << 31):
        return ""
    try:
        d = datetime.datetime.fromtimestamp(v, datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return d.date().isoformat()


def _parse_peak(b, ctx):
    """`PEAK`: each channel's loudest sample and where it is.

    A version, a Unix timestamp, then one record per channel holding a float
    peak and the frame it occurs on. Little-endian, unlike the CAF chunk of the
    same name, which is big-endian with a 64-bit frame -- the same idea written
    twice by different people, which is why they are read by different code.

    Worth reading rather than skipping for the reason the WAV anatomy page
    already gives: it lets a reader normalise and draw a waveform without
    scanning the samples, and it settles the non-unit-float question, because a
    writer that normalises to 2^23 rather than 1.0 says so here.
    """
    fields, warns = [], []
    if len(b) < 8:
        return "truncated", fields, [
            f"PEAK payload is {len(b)} bytes, the header alone is 8"]
    version, stamp = struct.unpack_from("<II", b, 0)
    fields.append(_f(0x00, 4, "version", version,
                     "" if version == 1 else "only version 1 is defined"))
    # the PEAK spec DEFINES this as seconds since 1970, so it is stated rather
    # than offered as a reading -- unlike the Apple chunks, where the same
    # four bytes are a guess that happens to produce sane years
    fields.append(_f(0x04, 4, "timestamp", stamp, _peak_date(stamp),
                     enc="<I", raw=stamp))
    if version != 1:
        warns.append(f"PEAK declares version {version}; only 1 is defined")

    chans = ctx.get("channels") or 0
    have = (len(b) - 8) // 8
    if chans and have != chans:
        warns.append(f"{have} peak record(s) for {chans} channel(s)")
    loudest = 0.0
    for i in range(min(have, _PEAK_CHANNEL_CAP)):
        value, frame = struct.unpack_from("<fI", b, 8 + i * 8)
        loudest = max(loudest, abs(value))
        note = f"{frame / ctx['sample_rate']:.3f} s" if ctx.get("sample_rate") else ""
        fields.append(_f(8 + i * 8, 8, f"peak[{i}]",
                         f"{value:.6f} at frame {frame:,}", note))
    if have > _PEAK_CHANNEL_CAP:
        warns.append(hit("list_rows", _PEAK_CHANNEL_CAP, have,
                         f"listing the first {_PEAK_CHANNEL_CAP} of "
                         f"{have} peak records"))
    # a peak past unit scale is not damage: float WAV is allowed past 0 dBFS,
    # and a file normalised to 2^23 rather than 1.0 shows up here as a huge
    # number rather than as a clipped one. Stated, never corrected.
    if loudest > 1.0:
        fields.append(_f(None, 0, "full_scale", f"{loudest:.6f}",
                         "past unit scale: legal in float, and the tell for a "
                         "writer that normalises to 2^23 instead of 1.0"))
    return (f"{have} channel peak(s), loudest {loudest:.6f}"
            if have else "no peak records"), fields, warns


def _parse_fake(b, _ctx):
    """`Fake`: a placeholder, and it says so in its own name.

    Measured on 30 specimens from unrelated libraries: two bytes in 29 of
    them and four in one, and the content is either NULs or spaces. Nothing
    is encoded in it. It is reported rather than decoded because there is
    nothing to decode, and because a reader who meets a chunk called `Fake`
    deserves to be told it is exactly what it looks like.
    """
    blank = not b.strip(b"\x00 ")
    return ("%d blank byte(s)" % len(b) if blank
            else "%d byte(s), not blank" % len(b)), \
        [_f(0x00, len(b), "bytes", b[:8].hex(" ") or "(empty)",
            "NULs or spaces in every specimen measured" if blank
            else "this one is NOT blank, which no specimen measured was")], []


def _parse_chrp(b, _ctx):
    """`chrp`: twelve bytes, and all thirty specimens measured are zero.

    It travels with `muma`, which does carry per-file values, so this is
    probably a companion record its writer never fills in. Reported rather
    than decoded, because zero bytes decode to nothing.
    """
    blank = not b.strip(b"\x00")
    warns = []
    if len(b) != _CHRP_SIZE:
        warns.append("chrp is %d bytes; every specimen measured is %d"
                     % (len(b), _CHRP_SIZE))
    return ("%d zero byte(s)" % len(b) if blank
            else "%d byte(s), not zero" % len(b)),         [_f(0x00, len(b), "bytes", b[:12].hex(" ") or "(empty)",
            "zero in every specimen measured" if blank
            else "NOT zero; every specimen measured is")], warns


def _parse_saur(b, _ctx):
    """`SAUR`: a fixed 32-byte version string, NUL-padded.

    All 30 specimens are byte-identical and read `1.1.0.0`, across two
    unrelated libraries. So it stamps the writer's version and carries nothing
    about the file it is in.
    """
    if not b:
        return "empty", [], ["SAUR payload is empty"]
    text = b.split(b"\x00", 1)[0].decode("latin-1", "replace")
    warns = []
    if len(b) != _SAUR_SIZE:
        warns.append("SAUR is %d bytes; every specimen measured is %d"
                     % (len(b), _SAUR_SIZE))
    return ("version %s" % text if text else "no version string"), \
        [_f(0x00, len(text), "version", text,
            "a writer's stamp; the same in every specimen measured")], warns


def _parse_cdif(b, _ctx):
    """`CDif`: 68 bytes that are the same 68 bytes in every file.

    The first word is the chunk's own size and the second is 1; the remaining
    sixty bytes are zero. Thirty specimens from unrelated libraries are
    byte-identical, so whatever it was meant to hold, nothing is putting
    anything in it.
    """
    fields, warns = [], []
    if len(b) < 8:
        return "truncated", fields, ["CDif payload is under 8 bytes"]
    declared, kind = _u32(b, 0), _u32(b, 4)
    fields.append(_f(0x00, 4, "size", declared,
                     "the chunk's own size, repeated"))
    fields.append(_f(0x04, 4, "value", kind))
    if declared != len(b):
        warns.append("CDif declares %d bytes and its payload is %d"
                     % (declared, len(b)))
    rest = b[8:]
    if rest.strip(b"\x00"):
        # worth saying: it would be the first specimen carrying anything
        fields.append(_f(0x08, len(rest), "rest", rest[:16].hex(" "),
                         "NOT zero; every specimen measured is"))
    else:
        fields.append(_f(0x08, len(rest), "rest", "%d zero bytes" % len(rest),
                         "zero in every specimen measured"))
    return "%d bytes, constant in every specimen measured" % len(b), \
        fields, warns


def _parse_tlst(b, ctx):
    """`tlst`: a trigger list. Which chunk to play, and what plays it.

    A count and then fixed 24-byte records, verified on 30 specimens across
    four payload sizes (28, 52, 100 and 196 bytes) where 4 + count * 24 is the
    whole payload every time. Every record in every specimen names `cue `, so
    a trigger targets a cue point.

    Two things are deliberately NOT claimed.

    The second word is not the cue point's ID. It reads 0 where the file's own
    `cue ` chunk calls the point 1, in all thirty specimens, so whatever it
    selects, it is not by the id `cue ` assigns. It is reported as the two
    16-bit halves it visibly is, and named `selector`, because that is as far
    as the evidence goes.

    The fourth word is offered rather than asserted. It reads `FF nn 90 00`,
    where 0x90 is MIDI's Note On and nn took 60, 61, 62 and 63 across the
    specimens -- consecutive notes from middle C, which is what a trigger list
    is for. The reading is shown next to the raw bytes so a reader can judge
    it.
    """
    fields, warns = [], []
    if len(b) < 4:
        return "truncated", fields, ["tlst payload is under 4 bytes"]
    count = _u32(b, 0)
    capacity = (len(b) - 4) // _TLST_RECORD
    fields.append(_f(0x00, 4, "triggers", count))
    if count != capacity:
        warns.append("tlst declares %d trigger(s) and its payload holds %d"
                     % (count, capacity))
    for i in range(min(count, capacity, _TLST_RECORD_CAP)):
        at = 4 + i * _TLST_RECORD
        target = b[at:at + 4].decode("latin-1", "replace")
        low, high = _u16(b, at + 4), _u16(b, at + 6)
        kind = _u32(b, at + 8)
        trigger = b[at + 12:at + 16]
        note = ""
        if len(trigger) == 4 and trigger[2] == _MIDI_NOTE_ON:
            note = "reads as MIDI note %d on note-on" % trigger[1]
        fields.append(_f(at, _TLST_RECORD, "trigger[%d]" % i,
                         "%s, selector %d/%d" % (target, low, high),
                         (note + ", " if note else "")
                         + "raw %s" % trigger.hex(" ")))
        if kind != 1:
            warns.append("trigger[%d] has kind %d; every specimen measured "
                         "has 1" % (i, kind))
        if target.strip() != "cue":
            warns.append("trigger[%d] targets %r; every specimen measured "
                         "targets 'cue '" % (i, target))
    if count > _TLST_RECORD_CAP:
        warns.append(hit("list_rows", _TLST_RECORD_CAP, count,
                         "listing the first %d of %d triggers"
                         % (_TLST_RECORD_CAP, count)))
    return ("%d trigger(s)" % count if count else "no triggers"), fields, warns


def _parse_plst(b, ctx):
    """`plst`: the playlist. Which cue points to play, how long, how often.

    This is the spec's OWN loop machinery -- cue points plus a playlist
    carrying a repeat count -- and it lost. The WAV anatomy page has the
    census: across one production library `plst` appeared 48 times and `smpl`
    154,697. The sanctioned system was beaten by the sampler chunk.

    Read anyway, because 48 files is 48 files and because a segment naming a
    cue point that does not exist is a broken playlist that nothing else will
    tell you about.
    """
    fields, warns = [], []
    if len(b) < 4:
        return "truncated", fields, ["plst payload is under 4 bytes"]
    declared = _u32(b, 0)
    capacity = max(0, (len(b) - 4) // 12)
    fields.append(_f(0x00, 4, "segments", declared))
    if declared > capacity:
        warns.append(f"declares {declared} segments but the payload holds "
                     f"{capacity}")
    rate = ctx.get("sample_rate")
    wanted = []
    for i in range(min(declared, capacity, _PLST_SEGMENT_CAP)):
        base = 4 + i * 12
        cue_id, length, loops = struct.unpack_from("<III", b, base)
        wanted.append(cue_id)
        note = f"cue {cue_id}"
        if rate:
            note += f", {length / rate:.3f} s"
        if loops != 1:
            note += f", {loops}x"
        fields.append(_f(base, 12, f"segment[{i}]",
                         f"{length:,} frames", note))
        if loops == 0:
            warns.append(f"segment[{i}] plays {loops} times, which plays it "
                         f"not at all")
    if declared > _PLST_SEGMENT_CAP:
        warns.append(hit("list_rows", _PLST_SEGMENT_CAP, declared,
                         f"listing the first {_PLST_SEGMENT_CAP} of "
                         f"{declared} segments"))
    # the cue ids this playlist needs, for the cross-check after the walk --
    # plst is written BEFORE cue in the files measured, so the check cannot
    # happen here
    ctx.setdefault("plst_cue_ids", []).extend(wanted)
    return (f"{declared} segment(s)" if declared else "empty playlist"), \
        fields, warns


_PARSERS = {
    "fmt ": _parse_fmt,
    "fact": _parse_fact,
    "acid": _parse_acid,
    "smpl": _parse_smpl,
    "inst": _parse_inst,
    "cue ": _parse_cue,
    "plst": _parse_plst,
    "tlst": _parse_tlst,
    "Fake": _parse_fake,
    "SAUR": _parse_saur,
    "CDif": _parse_cdif,
    "chrp": _parse_chrp,
    "LIST": _parse_list,
    "bext": _parse_bext,
    "BWBM": _parse_bwbm,
    "cart": _parse_cart,
    "iXML": _parse_ixml,
    # measured on a real library before being written: clm in 43 files,
    # strc in the loop material, id3 in 8 -- all three previously walked
    # as "unparsed, first bytes: ..."
    "clm ": _parse_clm,
    "strc": _parse_strc,
    # BOTH spellings. Chunk ids are matched exactly, and registering only
    # the lowercase one left 25 files in a real library reporting an ID3
    # tag as unparsed bytes -- each carrying a TBPM frame, a tempo, that
    # was simply dropped.
    "id3 ": _parse_riff_id3,
    "ID3 ": _parse_riff_id3,
    "DISP": _parse_disp,
    "ResU": _parse_resu,
    # padding, named rather than dumped as hex -- and checked, because
    # padding that is not zero is a chunk overwritten in place.
    #
    # Chunk ids are matched EXACTLY, so every spelling a writer uses has to be
    # here. A census of 867,703 files found two that were not: lowercase `junk`
    # in 12,881 of them and `filr` in 15,639, both all-zero in every specimen
    # examined. Twenty-eight thousand files were getting no overwritten-data
    # check because of two table entries.
    #
    # This is the third time this exact bug has turned up -- `id3 ` versus
    # `ID3 ` was the first -- which is why tests/test_riff.py now pins the
    # padding ids against a census histogram rather than trusting the list.
    "JUNK": _parse_padding,
    "junk": _parse_padding,
    "FLLR": _parse_padding,
    "filr": _parse_padding,
    "PAD ": _parse_padding,
    "pad ": _parse_padding,
    "PEAK": _parse_peak,
    "CSET": _parse_cset,
    "(c) ": _parse_copyright,
    "AFAn": _parse_apple_meta,
    "AFmd": _parse_apple_meta,
    "_PMX": _parse_xmp,
    "XMP ": _parse_xmp,
    "minf": _parse_minf,
}
# every vendor chunk but minf, which has a field worth reading
_PARSERS.update({cid: _vendor_parser(what)
                 for cid, what in VENDOR_CHUNKS.items()
                 if cid not in _PARSERS})


# ── walk ───────────────────────────────────────────────────────────


def inspect_wav(filepath, ctx=None):
    """Walk a WAV file and return (chunks, file_warnings).

    Each chunk is a dict: id, offset, size, summary, fields, warnings.
    A caller-supplied ``ctx`` dict is filled with the semantic values the
    per-chunk parsers accumulate (sample_rate, duration, acid_bpm,
    smpl_root, ...) -- the scan/index path reads those instead of running
    a second decoder over the same bytes.
    """
    file_size = _size(filepath)
    if ctx is None:
        ctx = {}
    chunks = []
    file_warns = []
    seen = []

    with _open(filepath) as f:
        hdr = f.read(12)
        if len(hdr) < 12:
            # a sub-header file (empty/truncated) reaches here via the info
            # command's bare-path routing; degrade to a warning, not struct.error
            return chunks, [f"file is {len(hdr)} bytes; a RIFF header needs 12"]
        riff_size = struct.unpack("<I", hdr[4:8])[0]
        if riff_size + 8 != file_size:
            file_warns.append(
                f"riff_size says {riff_size + 8:,} bytes, file is {file_size:,} "
                f"({file_size - riff_size - 8:+,})"
            )

        for cid, offset, size in iter_chunks(filepath):
            seen.append(cid)
            avail = max(0, file_size - offset - 8)
            if size > avail and not (cid == "data" and size in _STREAM_SENTINELS):
                file_warns.append(defect(
                    "size.overrun",
                    f"chunk {cid!r} at 0x{offset:08x} claims {size:,} bytes "
                    f"but only {avail:,} remain")
                )
            parser = _PARSERS.get(cid)
            # _parse_data derives frames/duration from size + ctx and never reads
            # the payload, so skip the (up to 64 KB) read + transient alloc there
            if cid == "data":
                payload = b""
            else:
                f.seek(offset + 8)
                payload = f.read(min(size, _PAYLOAD_CAP))

            entry = {"id": cid, "offset": offset, "size": size,
                     "summary": "", "fields": [], "warnings": []}
            if cid == "data":
                # remember the data chunk's absolute payload offset so a later
                # cue chunk can resolve its sample-frame markers to byte offsets
                ctx["data_off"] = offset + 8
                ctx["data_bytes"] = min(size, avail)
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
            # "we stopped listing" is a fact about the WALK, not about this
            # chunk, so it belongs where a caller reads walk-level caveats too.
            # Promoted by the note's KIND, never by matching its text: that
            # wording being load-bearing across a module boundary is the defect
            # primitives.notes exists to prevent.
            file_warns.extend(w for w in entry["warnings"] if is_coverage(w))
            chunks.append(entry)

    if "fmt " not in seen:
        file_warns.append("no fmt chunk: not decodable as audio")
    if "data" not in seen:
        file_warns.append("no data chunk: no audio payload")
    if "fmt " in seen and "data" in seen and seen.index("fmt ") > seen.index("data"):
        file_warns.append("fmt appears after data, violating the one RIFF ordering rule")

    # A playlist segment names a cue point by id, and `cue ` is what defines
    # those ids. plst is written BEFORE cue in the files measured, so the
    # check waits until the walk is done and ctx holds both.
    wanted = ctx.get("plst_cue_ids") or []
    if wanted:
        defined = set()
        for entry in chunks:
            if entry.get("id") != "cue ":
                continue
            for fld in entry.get("fields") or []:
                note = fld.get("note") or ""
                if fld["name"].startswith("cue[") and note.startswith("id "):
                    try:
                        defined.add(int(note[3:].split(",")[0]))
                    except ValueError:
                        pass
        missing = sorted({c for c in wanted if c not in defined})
        if missing and defined:
            file_warns.append(
                f"plst names cue point(s) {missing} that cue does not define")
        elif missing and not defined:
            file_warns.append(
                f"plst names cue point(s) {missing} and there is no cue chunk")

    # The wavetable FRAME COUNT needs both the frame size and the data length,
    # and clm is written before data in every file measured -- so at the moment
    # clm is parsed the count cannot be known. Filled in here, where ctx is
    # complete, rather than by making the chunk loop two passes for one field.
    frames = ctx.get("frames")
    if frames:
        for entry in chunks:
            if entry.get("id") != "clm ":
                continue
            size = next((f["value"] for f in entry.get("fields") or []
                         if f["name"] == "frame_size"), None)
            try:
                size = int(str(size).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if size <= 0:
                continue
            n, rem = divmod(frames, size)
            entry["fields"].append(_f(None, 0, "frames", f"{n:,}",
                                      "data length divided by the frame size"))
            entry["summary"] = f"wavetable, {n:,} frames of {size:,} samples"
            if rem:
                entry["warnings"].append(defect(
                    "length.misaligned",
                    f"{frames:,} sample frames is not a whole number of "
                    f"{size:,}-sample wavetable frames ({rem:,} trail)"))

    return chunks, file_warns
