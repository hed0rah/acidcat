"""
RIFF/WAVE chunk parser.

Low-level binary parsing of WAV file chunks: ACID, SMPL, INST, FMT,
FACT, CUE, LIST/INFO, BEXT, and unknown chunks.
"""

import os
import struct
import wave
import binascii
from collections import namedtuple

from acidcat.util.midi import midi_note_to_name

# cap on payload bytes read per chunk (a forged size cannot force an unbounded
# allocation); the declared size is still reported in full.
PAYLOAD_CAP = 65536

# one traversed RIFF/WAVE region. size is the DECLARED chunk size (never
# clamped); payload is capped at PAYLOAD_CAP and may be short at EOF;
# payload_base is the absolute offset field offsets are measured from (offset+8).
Span = namedtuple("Span", "id offset payload_base payload size")


def parse_riff(filepath, enumerate_all=False):
    """
    Walk through WAV file and parse chunks.

    Args:
        filepath: Path to a WAV file.
        enumerate_all: If True, emit one (chunk_id, key, value) entry
            per parsed field (used by the ``chunks`` command).

    Returns:
        (results, meta, seen_order) where:
        - results: list of (chunk_id, key, value) tuples
        - meta: dict with ACID/SMPL summary fields
        - seen_order: ordered list of unique chunk IDs encountered
    """
    results = []
    meta = {
        "bpm": None,
        "acid_beats": None,
        "acid_one_shot": None,
        "acid_root_note": None,
        "smpl_root_key": None,
        "smpl_loop_start": None,
        "smpl_loop_end": None,
    }
    seen_order = []
    seen_set = set()

    file_size = os.path.getsize(filepath)

    with open(filepath, "rb") as f:
        riff_header = f.read(12)
        if len(riff_header) < 12 or riff_header[0:4] != b'RIFF':
            return results, meta, seen_order

        pos = 12
        while pos < file_size:
            f.seek(pos)
            header = f.read(8)
            if len(header) < 8:
                break
            chunk_id = header[0:4]
            try:
                chunk_size = struct.unpack("<I", header[4:8])[0]
            except struct.error:
                break

            cid_str = chunk_id.decode("ascii", errors="ignore")
            if cid_str and cid_str not in seen_set:
                seen_set.add(cid_str)
                seen_order.append(cid_str)

            # cap reads at 64 KB to bound malformed chunk_size against
            # OOM. legitimate LIST/cue chunks fit comfortably; pos
            # arithmetic below still uses chunk_size to skip the full
            # chunk on disk on the next iteration.
            chunk_data = f.read(min(chunk_size, 65536))

            # --- Known chunk parsing ---
            if chunk_id == b'acid':
                try:
                    # layout per libsndfile, field-verified against real
                    # ACIDized packs 2026-06-10: flags u32, root u16,
                    # q1 u16, q2 f32 (unknown, observed 0.0), num_beats
                    # u32, meter denom u16, meter numer u16, tempo f32.
                    # a previous unpack ("<IHHIII f") read num_beats from
                    # q2 and the meter from the real beats field, so every
                    # spec-conformant file reported acid_beats=0.
                    # unpack_from, not unpack: some taggers pad the chunk
                    # past 24 bytes, and an exact-length unpack raised on
                    # them, silently dropping BPM/beats.
                    if len(chunk_data) < 24:
                        raise ValueError(
                            f"acid chunk is {len(chunk_data)} bytes, need 24")
                    flags, root_note, _q1, _q2, beats, meter_den, meter_num, tempo = \
                        struct.unpack_from("<IHHfIHHf", chunk_data, 0)
                    meta["acid_root_note"] = root_note
                    meta["acid_beats"] = beats
                    meta["acid_one_shot"] = bool(flags & 0x01)
                    meta["bpm"] = round(tempo, 2)
                    if enumerate_all:
                        results.append(("acid", "bpm", meta["bpm"]))
                        results.append(("acid", "beats", beats))
                        results.append(("acid", "root_note", midi_note_to_name(root_note)))
                        results.append(("acid", "meter", f"{meter_num}/{meter_den}"))
                        results.append(("acid", "one_shot", bool(flags & 0x01)))
                        results.append(("acid", "flags", flags))
                except Exception as e:
                    if enumerate_all:
                        results.append(("acid", "error", str(e)))

            elif chunk_id == b'smpl':
                try:
                    (
                        manufacturer, product, sample_period, midi_unity_note,
                        midi_pitch_fraction, smpte_format, smpte_offset,
                        sample_loops, sampler_data
                    ) = struct.unpack("<IIIIIIIII", chunk_data[:36])
                    meta["smpl_root_key"] = midi_unity_note
                    if sample_loops > 0 and len(chunk_data) >= 36 + 24:
                        _, _, start, end, _, _ = struct.unpack("<IIIIII", chunk_data[36:60])
                        meta["smpl_loop_start"] = start
                        meta["smpl_loop_end"] = end
                    if enumerate_all:
                        results.append(("smpl", "root_key", midi_note_to_name(midi_unity_note)))
                        results.append(("smpl", "loops", sample_loops))
                        if meta["smpl_loop_start"] is not None:
                            results.append(("smpl", "loop_start", meta["smpl_loop_start"]))
                            results.append(("smpl", "loop_end", meta["smpl_loop_end"]))
                except Exception as e:
                    if enumerate_all:
                        results.append(("smpl", "error", str(e)))

            elif chunk_id == b'inst' and enumerate_all:
                try:
                    if len(chunk_data) >= 7:
                        base = chunk_data[0]
                        detune = struct.unpack("<b", chunk_data[1:2])[0]
                        gain = struct.unpack("<b", chunk_data[2:3])[0]
                        low_note, high_note = chunk_data[3], chunk_data[4]
                        low_vel, high_vel = chunk_data[5], chunk_data[6]
                        results.append(("inst", "base_note", midi_note_to_name(base)))
                        results.append(("inst", "detune_cents", detune))
                        results.append(("inst", "gain_db", gain))
                        results.append(("inst", "key_range",
                                        f"{midi_note_to_name(low_note)}-{midi_note_to_name(high_note)}"))
                        results.append(("inst", "vel_range", f"{low_vel}-{high_vel}"))
                    else:
                        results.append(("inst", "raw", binascii.hexlify(chunk_data).decode()))
                except Exception as e:
                    results.append(("inst", "error", str(e)))

            elif chunk_id == b'fmt ' and enumerate_all:
                try:
                    if len(chunk_data) >= 16:
                        wFormatTag, nChannels, nSamplesPerSec, nAvgBytesPerSec, nBlockAlign, wBitsPerSample = \
                            struct.unpack("<HHIIHH", chunk_data[:16])
                        results.append(("fmt ", "format_tag", wFormatTag))
                        results.append(("fmt ", "channels", nChannels))
                        results.append(("fmt ", "sample_rate", nSamplesPerSec))
                        results.append(("fmt ", "bits_per_sample", wBitsPerSample))
                        results.append(("fmt ", "block_align", nBlockAlign))
                    else:
                        results.append(("fmt ", "raw", binascii.hexlify(chunk_data).decode()))
                except Exception as e:
                    results.append(("fmt ", "error", str(e)))

            elif chunk_id == b'fact' and enumerate_all:
                try:
                    if len(chunk_data) >= 4:
                        sample_length = struct.unpack("<I", chunk_data[:4])[0]
                        results.append(("fact", "sample_length", sample_length))
                    else:
                        results.append(("fact", "raw", binascii.hexlify(chunk_data).decode()))
                except Exception as e:
                    results.append(("fact", "error", str(e)))

            elif chunk_id == b'cue ' and enumerate_all:
                try:
                    num_cues = struct.unpack("<I", chunk_data[:4])[0]
                    # Cap num_cues against the actual payload size. A
                    # malformed or malicious WAV with num_cues =
                    # 0xFFFFFFFF would otherwise iterate ~4 billion
                    # times before the inner len-check rejects every
                    # empty slice. Each cue record is exactly 24
                    # bytes, so (len(chunk_data) - 4) // 24 is the
                    # most we could ever read.
                    max_cues = max(0, (len(chunk_data) - 4) // 24)
                    safe_count = min(num_cues, max_cues)
                    for i in range(safe_count):
                        cue_base = 4 + i * 24
                        cue_data = chunk_data[cue_base: cue_base + 24]
                        if len(cue_data) == 24:
                            _, _, _, _, _, sample_offset = struct.unpack("<IIIIII", cue_data)
                            results.append(("cue ", f"marker_{i}", sample_offset))
                except Exception as e:
                    results.append(("cue ", "error", str(e)))

            elif chunk_id == b'LIST' and enumerate_all:
                try:
                    if len(chunk_data) >= 4:
                        list_type = chunk_data[:4].decode("ascii", errors="ignore")
                        results.append(("LIST", "type", list_type))
                        pos_in_list = 4
                        while pos_in_list + 8 <= len(chunk_data):
                            sub_id = chunk_data[pos_in_list:pos_in_list + 4].decode(
                                "ascii", errors="ignore"
                            )
                            sub_size = struct.unpack(
                                "<I", chunk_data[pos_in_list + 4:pos_in_list + 8]
                            )[0]
                            start = pos_in_list + 8
                            end = start + sub_size
                            if end > len(chunk_data):
                                break
                            sub_val = chunk_data[start:end].decode(
                                "ascii", errors="ignore"
                            ).rstrip("\x00").strip()
                            results.append(("LIST", sub_id, sub_val))
                            pos_in_list = end
                            if sub_size % 2 == 1:
                                pos_in_list += 1
                    else:
                        results.append(("LIST", "raw", binascii.hexlify(chunk_data[:32]).decode()))
                except Exception:
                    results.append(("LIST", "raw", binascii.hexlify(chunk_data[:32]).decode()))

            elif chunk_id == b'bext' and enumerate_all:
                try:
                    desc = chunk_data[0:256].decode("ascii", errors="ignore").rstrip("\x00").strip()
                    origin = chunk_data[256:288].decode("ascii", errors="ignore").rstrip("\x00").strip()
                    date = chunk_data[320:330].decode("ascii", errors="ignore").strip()
                    time = chunk_data[330:338].decode("ascii", errors="ignore").strip()
                    results.append(("bext", "description", desc))
                    results.append(("bext", "originator", origin))
                    results.append(("bext", "datetime", f"{date} {time}".strip()))
                except Exception:
                    results.append(("bext", "raw", binascii.hexlify(chunk_data[:32]).decode()))

            else:
                if enumerate_all:
                    hex_preview = binascii.hexlify(chunk_data[:16]).decode()
                    results.append((cid_str, "raw", hex_preview))

            # Word alignment
            pos += 8 + chunk_size
            if chunk_size % 2 == 1:
                pos += 1

    return results, meta, seen_order


def iter_chunks(filepath):
    """
    Yield (chunk_id_str, offset, size) for each chunk in a RIFF/WAVE file.

    Lightweight iterator -- doesn't parse chunk contents.
    """
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        hdr = f.read(12)
        if len(hdr) < 12 or hdr[0:4] != b"RIFF" or hdr[8:12] != b"WAVE":
            return
        pos = 12
        while pos + 8 <= size:
            f.seek(pos)
            ch = f.read(8)
            if len(ch) < 8:
                break
            cid = ch[0:4].decode("ascii", errors="ignore")
            try:
                csz = struct.unpack("<I", ch[4:8])[0]
            except struct.error:
                break
            yield (cid, pos, csz)
            pos += 8 + csz
            if csz % 2 == 1:
                pos += 1


def iter_spans(filepath):
    """Lenient RIFF/WAVE traversal, the single source both the walker and the
    grammar strategy consume. Returns ``(spans, warnings)``. Enumerates via
    ``iter_chunks`` so the chunk-walk arithmetic has exactly one home, and adds
    the payload read plus the traversal warnings (riff_size mismatch, chunk
    overrun) in the walker's exact wording. Degrades, never raises.
    """
    file_size = os.path.getsize(filepath)
    spans, warns = [], []
    with open(filepath, "rb") as f:
        hdr = f.read(12)
        if len(hdr) < 12:
            return [], [f"file is {len(hdr)} bytes; a RIFF header needs 12"]
        if hdr[0:4] != b"RIFF" or hdr[8:12] != b"WAVE":
            return [], ["not a RIFF/WAVE container"]
        riff_size = struct.unpack("<I", hdr[4:8])[0]
        if riff_size + 8 != file_size:
            warns.append(
                f"riff_size says {riff_size + 8:,} bytes, file is "
                f"{file_size:,} ({file_size - riff_size - 8:+,})"
            )
        for cid, offset, size in iter_chunks(filepath):
            avail = max(0, file_size - offset - 8)
            if size > avail:
                warns.append(
                    f"chunk {cid!r} at 0x{offset:08x} claims {size:,} bytes "
                    f"but only {avail:,} remain"
                )
            f.seek(offset + 8)
            payload = f.read(min(size, PAYLOAD_CAP))
            spans.append(Span(cid, offset, offset + 8, payload, size))
    return spans, warns


def smpl_root_or_none(meta):
    """Coerce the SMPL `smpl_root_key` field to None when it is the
    documented "unset" sentinel value 0 (MIDI note C-1, which no
    legitimate sample chunk actually uses as its root). Returns the
    integer MIDI note for any non-zero value, or None.

    Use this at every call site that downstreams `smpl_root_key` into
    a key/root display. Without it the scan CSV and any future caller
    will ship `C-1` for files whose SMPL chunk is present but unset.
    """
    val = meta.get("smpl_root_key") if hasattr(meta, "get") else meta
    return val if val else None


def acid_root_or_none(meta):
    """Companion to `smpl_root_or_none` for the ACID `acid_root_note`
    field. Same zero-as-sentinel convention.
    """
    val = meta.get("acid_root_note") if hasattr(meta, "get") else meta
    return val if val else None


def effective_acid_beats(meta, duration):
    """Vet the acid num_beats field against the one-shot flag and the
    file's actual duration.

    Field measurement (2026-06-11, 400 ACIDized files): with the
    one-shot bit clear, num_beats reconciles with duration*tempo/60
    in ~93% of files. With the bit set it is a coin flip: batch
    taggers leave boilerplate 8-beat/120-bpm values in true
    one-shots, while some vendors set the bit on real loops that
    carry accurate beat counts. So: trust beats when the flag is
    clear; when it is set, keep beats only if they reconcile with
    the actual duration within 15%.
    """
    beats = meta.get("acid_beats")
    if not beats:
        return None
    if not meta.get("acid_one_shot"):
        return beats
    bpm = meta.get("bpm")
    if bpm and duration:
        expected = beats / bpm * 60
        if abs(expected - duration) / duration < 0.15:
            return beats
    return None


def get_riff_info(filepath):
    """Return RIFF container size and type string, or None if not RIFF."""
    with open(filepath, "rb") as f:
        hdr = f.read(12)
        if len(hdr) < 12 or hdr[0:4] != b"RIFF":
            return None
        riff_size = struct.unpack("<I", hdr[4:8])[0]
        riff_type = hdr[8:12].decode("ascii", errors="ignore")
        return {"size": riff_size, "type": riff_type}


def get_duration(filepath):
    """
    Get WAV duration in seconds.
    Tries wave module first, then header-only fallback for non-PCM codecs.
    """
    try:
        with wave.open(filepath, 'rb') as wf:
            return round(wf.getnframes() / float(wf.getframerate()), 4)
    except Exception:
        pass
    return _duration_from_headers(filepath)


def _duration_from_headers(filepath):
    """
    Header-only duration calc for non-PCM / unsupported codecs.
    """
    try:
        size = os.path.getsize(filepath)
        with open(filepath, "rb") as f:
            hdr = f.read(12)
            if len(hdr) < 12 or hdr[0:4] != b'RIFF' or hdr[8:12] != b'WAVE':
                return None

            sample_rate = None
            channels = None
            bits_per_sample = None
            data_bytes = None
            fact_samples = None

            pos = 12
            while pos + 8 <= size:
                f.seek(pos)
                ch = f.read(8)
                if len(ch) < 8:
                    break
                cid = ch[0:4]
                csz = struct.unpack("<I", ch[4:8])[0]
                payload_off = pos + 8

                if cid == b'fmt ' and csz >= 16:
                    f.seek(payload_off)
                    fmt = f.read(16)
                    _, nChannels, nSamplesPerSec, _, _, wBitsPerSample = struct.unpack(
                        "<HHIIHH", fmt
                    )
                    sample_rate = nSamplesPerSec
                    channels = nChannels
                    bits_per_sample = wBitsPerSample
                elif cid == b'fact' and csz >= 4:
                    f.seek(payload_off)
                    fact = f.read(4)
                    fact_samples = struct.unpack("<I", fact)[0]
                elif cid == b'data':
                    data_bytes = csz

                pos += 8 + csz
                if csz % 2 == 1:
                    pos += 1

            if fact_samples and sample_rate:
                return round(fact_samples / float(sample_rate), 4)

            if (sample_rate and channels and bits_per_sample
                    and data_bytes is not None and bits_per_sample > 0):
                bytes_per_frame = channels * max(bits_per_sample // 8, 1)
                if bytes_per_frame > 0:
                    frames = data_bytes / float(bytes_per_frame)
                    return round(frames / float(sample_rate), 4)
    except Exception:
        return None
    return None
