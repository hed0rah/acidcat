"""AIFF/AIFC structural walker: per-chunk field decoding for inspect.
The embedded 'ID3 ' chunk reuses the MP3 walker's ID3v2 frame decoder."""

import os
import struct

from acidcat.core import mp3 as mp3mod
from acidcat.core.aiff import (_AES_EMPHASIS, _AES_RATES,
                               _AIFC_KNOWN_COMPRESSION, _LOOP_MODES,
                               _parse_ieee_extended)
from acidcat.core.aiff import iter_chunks as iter_aiff_chunks
from acidcat.core.walk.base import _PAYLOAD_CAP, _bu16, _bu32, _dtext, _f
from acidcat.core.walk.mp3 import _id3v2_frames
from acidcat.util.midi import midi_note_to_name

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
    fields.append(_f(0x08, 10, "sample_rate", int(rate) if rate else 0, rate_note,
                     enc="float80", raw=int(rate) if rate else 0))
    ctx.update({"channels": ch, "frames": frames, "bits": bits, "rate": rate,
                "sample_rate": int(rate) if rate else 0,
                "duration": round(frames / rate, 4) if rate else None})
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
        name = _dtext(b[pos + 7:pos + 7 + name_len])
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
        mode_name = _LOOP_MODES.get(mode, f"unknown {mode}")
        # the loop mode is the first 2 bytes of the 6-byte field (a big-endian
        # int16); edit it by name as an enum bit-field over that word.
        fields.append(_f(off, 6, label, mode_name,
                         f"markers {begin}..{end}" if mode else "",
                         enc="bitsmap:0:2:0:16:aiff_loop_mode"))
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
    ctx["basc_beats"] = beats
    ctx["basc_root_key"] = root
    ctx["basc_scale"] = scale
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


def _mac_timestamp(ts):
    """Render a classic Mac timestamp (u32 seconds since 1904-01-01, the HFS
    epoch AIFF inherited from the Apple II era) as an ISO date, or '' for 0."""
    if not ts:
        return ""
    import datetime
    try:
        dt = datetime.datetime(1904, 1, 1) + datetime.timedelta(seconds=ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except OverflowError:
        return ""


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
        ts = struct.unpack_from(">I", b, pos)[0]
        marker = struct.unpack_from(">h", b, pos + 4)[0]
        count = _bu16(b, pos + 6)
        if pos + 8 + count > len(b):
            warns.append(f"comment[{i}] text overruns payload")
            break
        text = _dtext(b[pos + 8:pos + 8 + count]).strip()
        bits = []
        when = _mac_timestamp(ts)
        if when:
            bits.append(when)
        if marker:
            bits.append(f"marker {marker}")
        fields.append(_f(pos, 8 + count + (count & 1), f"comment[{i}]",
                         text[:60], ", ".join(bits)))
        pos += 8 + count + (count & 1)
        shown += 1
    return f"{shown} comment(s)", fields, warns


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
    # byte 0 packs four sub-fields; split them out as editable enum bit-fields
    fields.append(_f(0x00, 1, "aes_professional", pro,
                     enc="bitsmap:0:1:7:1:aes_pro"))
    fields.append(_f(0x00, 1, "aes_data_type", kind,
                     enc="bitsmap:0:1:6:1:aes_kind"))
    fields.append(_f(0x00, 1, "aes_emphasis", emphasis,
                     enc="bitsmap:0:1:3:3:aes_emphasis"))
    fields.append(_f(0x00, 1, "aes_sample_rate", rate,
                     enc="bitsmap:0:1:0:2:aes_rate"))
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


def _aiff_id3_fields(tag_bytes):
    """Decode an embedded ID3v2 tag (AIFF 'ID3 ' chunk) by reusing the MP3 ID3
    parser: the chunk payload is a complete ID3 tag, so write it to a temp file
    and run the same frame decoder. Returns [] if it is not a valid tag."""
    if tag_bytes[:3] != b"ID3":
        return []
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".id3")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(tag_bytes)
        hdr = mp3mod.read_id3v2(tmp)
        if not hdr:
            return []
        flds, _ = _id3v2_frames(tmp, hdr)
        return flds
    except Exception:
        return []
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def inspect_aiff(filepath, form_type, ctx=None):
    """Walk an AIFF/AIFC file and return (chunks, file_warnings).

    A caller-supplied ``ctx`` dict is filled with the semantic values the
    per-chunk parsers accumulate (channels, rate, frames, bits, duration,
    NAME/AUTH/copyright text, basc beats/root) so the scan path can read
    them instead of running a second decoder."""
    file_size = os.path.getsize(filepath)
    if ctx is None:
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
            # SSND's parser reads only its 8-byte header (offset + block_size);
            # the audio-byte count comes from size/avail, so cap that read small
            # instead of pulling up to 64 KB of audio for nothing.
            read_cap = 16 if cid == "SSND" else _PAYLOAD_CAP
            f.seek(offset + 8)
            payload = f.read(min(size, read_cap))

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
                elif cid == "FVER":
                    # AIFC format-version: a Mac timestamp; the spec froze it
                    # at 0xA2805140 (1990-05-23), the only value ever defined
                    if len(payload) >= 4:
                        ts = struct.unpack_from(">I", payload, 0)[0]
                        canon = ts == 0xA2805140
                        entry["fields"] = [_f(0x00, 4, "format_version",
                                              f"0x{ts:08X}",
                                              "AIFC Version 1 (1990-05-23)"
                                              if canon else
                                              _mac_timestamp(ts) or "unknown",
                                              enc=">I", raw=ts)]
                        entry["summary"] = ("AIFC Version 1" if canon
                                            else f"version 0x{ts:08X}")
                        if not canon:
                            entry["warnings"] = [
                                "format_version is not the canonical "
                                "0xA2805140; no other version was ever defined"]
                    else:
                        entry["summary"] = "truncated"
                        entry["warnings"] = ["FVER payload under 4 bytes"]
                elif cid == "AESD":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _aiff_aesd(payload)
                elif cid == "APPL":
                    entry["summary"], entry["fields"], entry["warnings"] = \
                        _aiff_appl(payload)
                elif cid in ("NAME", "AUTH", "(c) ", "ANNO"):
                    text = _dtext(payload).strip("\x00").strip()
                    entry["summary"] = text[:60]
                    entry["fields"] = [_f(0x00, size, "text", text[:200])]
                    ctx[{"NAME": "name", "AUTH": "author",
                         "(c) ": "copyright"}.get(cid, "annotation")] = text
                elif cid == "ID3 ":
                    entry["fields"] = _aiff_id3_fields(payload)
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
