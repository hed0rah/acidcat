"""
acidcat info -- single-file metadata dump.

The star command: ``acidcat file.wav`` dumps metadata like exiftool.
Supports WAV, AIFF, MIDI, Serum, MP3, FLAC, OGG, and M4A files.
"""

import os
import sys

from acidcat.core.riff import (
    smpl_root_or_none, acid_root_or_none, effective_acid_beats,
)
from acidcat.core.aiff import is_aiff
from acidcat.core.midi import is_midi
from acidcat.core.serum import is_serum_preset
from acidcat.core.tagged import is_tagged_format
from acidcat.core.detect import estimate_librosa_metadata
from acidcat.core.features import extract_audio_features
from acidcat.core.formats import output
from acidcat.util.midi import midi_note_to_name, midi_note_to_pitch_class
from acidcat.util.stdin import is_stdin_target, stdin_to_tempfile


def _vlog(args, msg):
    """Emit a diagnostic line to stderr when -v is set and -q is not.

    Keeps stdout clean so `acidcat info ... -f json | jq` stays pipe-friendly.
    """
    if getattr(args, "verbose", False) and not getattr(args, "quiet", False):
        print(msg, file=sys.stderr)


def register(subparsers):
    p = subparsers.add_parser("info", help="Show metadata for a single audio file.")
    p.add_argument("target", help="Path to an audio file (WAV, AIFF, MIDI, Serum preset).")
    p.add_argument("-f", "--format", default="table", choices=["table", "json", "csv"],
                   help="Output format (default: table).")
    p.add_argument("--deep", action="store_true",
                   help="Include librosa deep analysis (BPM/key detection + spectral features).")
    p.add_argument("-q", "--quiet", action="store_true", help="Suppress progress messages.")
    p.add_argument("-v", "--verbose", action="store_true", help="Show all chunk fields.")
    p.add_argument("-o", "--output", help="Write output to file instead of stdout.")
    p.set_defaults(func=run)


def _detect_format(filepath):
    """Detect file format by magic bytes, falling back to extension.

    Formats with a dedicated info builder (WAV/AIFF/MIDI/Serum/tagged) route to
    themselves; every other format the walkers structurally decode
    (Kurzweil/E-mu/Akai/RX2/FXP/tracker/SF2/MPC/... banks and presets) routes to
    "walker" for a walker-backed summary, so it is never mis-parsed as a
    headerless WAV."""
    if is_midi(filepath):
        return "midi"
    if is_aiff(filepath):
        return "aiff"
    if is_serum_preset(filepath):
        return "serum"
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".aif", ".aiff"):
        return "aiff"
    if ext in (".mid", ".midi"):
        return "midi"
    if ext.lower() == ".serumpreset":
        return "serum"
    if is_tagged_format(filepath):
        return "tagged"
    # any other structural format the walkers recognize gets a walker-backed
    # summary instead of a mis-parse; rf64 too (its walker is not the WAV one)
    from acidcat.core import sniff as sniffmod
    if sniffmod.sniff(filepath) not in (None, "wav"):
        return "walker"
    if _is_preset(filepath, ext):        # ext-only presets sniff may not catch
        return "walker"
    return "wav"


_PRESET_EXTS = (".bwpreset", ".bwclip", ".vital", ".nmsv", ".nabs", ".nksf",
                ".nki", ".ncw", ".nrkt", ".nfm8", ".nbkt")


def _is_preset(filepath, ext):
    """Synth/DAW preset containers that `inspect` decodes but `info` (which is
    WAV/tag centric) does not. Detected by magic where reliable, else extension.
    Prevents a preset from being silently mis-parsed as a headerless WAV."""
    if ext in _PRESET_EXTS:
        return True
    try:
        with open(filepath, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    return (head[:4] == b"BtWg" or head[12:16] == b"hsin" or head[:4] == b"-in-"
            or (head[:4] == b"RIFF" and head[8:12] == b"NIKS"))


def _info_wav(filepath, args):
    """Build info record for a WAV/RIFF file, from the inspect walker's ctx
    (the single WAV decoder since the 2026-07 unification)."""
    from acidcat.core.walk.wav import inspect_wav

    ctx = {}
    chunks, _warns = inspect_wav(filepath, ctx=ctx)
    seen = list(dict.fromkeys(c["id"] for c in chunks))
    duration = round(ctx["duration"], 4) if ctx.get("duration") else None
    bpm = ctx.get("acid_bpm")

    # SMPL/ACID root_note of 0 is the default-unset sentinel (MIDI C-1),
    # not a legitimate musical root. Treat as missing.
    smpl_root = smpl_root_or_none(ctx.get("smpl_root"))
    acid_root = acid_root_or_none(ctx.get("acid_root"))

    _vlog(args, "[detect] fmt=wav")

    rec = {}
    rec["File"] = os.path.basename(filepath)

    if ctx.get("format_tag") is not None:
        tag = ctx["format_tag"]
        codec = "PCM" if tag == 1 else f"tag={tag}"
        ch = ctx.get("channels")
        ch_label = "mono" if ch == 1 else "stereo" if ch == 2 else f"{ch}ch"
        rec["Format"] = (f"WAV {codec} {ctx.get('sample_rate')}Hz "
                         f"{ctx.get('bits')}-bit {ch_label}")

    if duration is not None:
        rec["Duration"] = f"{duration}s"

    if bpm is not None:
        rec["BPM"] = bpm
        beats = effective_acid_beats(
            {"acid_beats": ctx.get("acid_beats"),
             "acid_one_shot": ctx.get("acid_one_shot"), "bpm": bpm}, duration)
        if beats:
            rec["Beats"] = beats
        if acid_root is not None:
            rec["ACID Root"] = midi_note_to_name(acid_root)
        if beats and bpm:
            expected = round((beats / bpm) * 60, 4)
            rec["Expected Duration"] = f"{expected}s"
            if duration:
                diff = round(duration - expected, 4)
                rec["Duration Diff"] = f"{diff}s"
    else:
        rec["BPM"] = "-"

    if smpl_root is not None:
        rec["Key"] = f"{midi_note_to_pitch_class(smpl_root)} (from SMPL)"
        _vlog(args, f"[key] smpl_root={smpl_root} -> {midi_note_to_pitch_class(smpl_root)}")
    elif acid_root is not None:
        rec["Key"] = f"{midi_note_to_pitch_class(acid_root)} (from ACID)"
        _vlog(args, f"[key] acid_root={acid_root} -> {midi_note_to_pitch_class(acid_root)}")
    else:
        rec["Key"] = "-"
        _vlog(args, "[key] no SMPL/ACID root, unset")

    rec["ACID"] = "yes" if bpm is not None else "no"

    if smpl_root is not None:
        smpl_parts = [f"root={midi_note_to_name(smpl_root)}"]
        if ctx.get("smpl_loop_start") is not None:
            smpl_parts.append(f"loop={ctx['smpl_loop_start']}-{ctx['smpl_loop_end']}")
        else:
            smpl_parts.append("loops=0")
        rec["SMPL"] = " ".join(smpl_parts)
    else:
        rec["SMPL"] = "no"

    rec["Chunks"] = ", ".join(seen) if seen else "(none)"

    # deep analysis (only for audio files)
    if getattr(args, 'deep', False):
        _add_deep_analysis(filepath, rec, args)

    return rec


def _info_aiff(filepath, args):
    """Build info record for an AIFF/AIFC file, from the inspect walker."""
    from acidcat.core.walk.aiff import inspect_aiff

    _vlog(args, "[detect] fmt=aiff")
    with open(filepath, "rb") as f:
        form = "AIFC" if f.read(12)[8:12] == b"AIFC" else "AIFF"
    ctx = {}
    chunks, _warns = inspect_aiff(filepath, form, ctx=ctx)
    seen = [c["id"] for c in chunks]
    # the legacy parser labeled AIFF's compression "none" and AIFC's "aifc";
    # the walker records the real AIFC compression 4cc (e.g. "sowt") in ctx
    compression = ctx.get("compression")

    rec = {}
    rec["File"] = os.path.basename(filepath)

    fmt_parts = ["AIFF"]
    if form == "AIFC":
        fmt_parts[0] = "AIFC"
        if compression and compression not in ("none", "NONE"):
            fmt_parts.append(compression)
    if ctx.get("sample_rate"):
        fmt_parts.append(f"{ctx['sample_rate']}Hz")
    if ctx.get("bits"):
        fmt_parts.append(f"{ctx['bits']}-bit")
    if ctx.get("channels"):
        ch = ctx["channels"]
        ch_label = "mono" if ch == 1 else "stereo" if ch == 2 else f"{ch}ch"
        fmt_parts.append(ch_label)
    rec["Format"] = " ".join(fmt_parts)

    if ctx.get("duration") is not None:
        rec["Duration"] = f"{ctx['duration']}s"
    if ctx.get("frames") is not None:
        rec["Frames"] = ctx["frames"]
    if ctx.get("name"):
        rec["Name"] = ctx["name"]
    if ctx.get("author"):
        rec["Author"] = ctx["author"]
    if ctx.get("copyright"):
        rec["Copyright"] = ctx["copyright"]

    rec["Chunks"] = ", ".join(seen) if seen else "(none)"

    if getattr(args, 'deep', False):
        _add_deep_analysis(filepath, rec, args)

    return rec


def _info_midi(filepath, args):
    """Build info record for a MIDI file, from the inspect walker."""
    from acidcat.core.walk.midi import inspect_midi

    _vlog(args, "[detect] fmt=midi")
    meta = {}
    inspect_midi(filepath, ctx=meta)

    rec = {}
    rec["File"] = os.path.basename(filepath)
    rec["Format"] = f"MIDI type {meta['format']}" if meta.get("format") is not None else "MIDI"

    if meta.get("tracks") is not None:
        rec["Tracks"] = meta["tracks"]

    if meta.get("division") is not None:
        division = meta["division"]
        if division & 0x8000:
            # SMPTE division: high byte is a negative two's-complement
            # frame rate, low byte is ticks per frame. rendering the
            # raw word as ticks/beat (e.g. "59176 ticks/beat") is
            # nonsense; -29 means 29.97 drop-frame.
            fps = 256 - ((division >> 8) & 0xFF)
            tpf = division & 0xFF
            shown = 29.97 if fps == 29 else fps
            rec["Division"] = f"SMPTE {shown} fps, {tpf} ticks/frame"
        else:
            rec["Division"] = f"{division} ticks/beat"

    if meta.get("tempo_bpm") is not None:
        rec["BPM"] = meta["tempo_bpm"]

    if meta.get("time_sig"):
        rec["Time Sig"] = meta["time_sig"]

    if meta.get("key_sig"):
        rec["Key"] = meta["key_sig"]

    if meta.get("track_names"):
        rec["Track Names"] = ", ".join(meta["track_names"])

    if meta.get("copyright"):
        rec["Copyright"] = meta["copyright"]

    if meta.get("note_count", 0) > 0:
        rec["Notes"] = meta["note_count"]
        if meta.get("note_min") is not None and meta.get("note_max") is not None:
            rec["Note Range"] = f"{midi_note_to_name(meta['note_min'])}-{midi_note_to_name(meta['note_max'])}"

    if meta.get("channels_used"):
        rec["Channels"] = ", ".join(str(c) for c in meta["channels_used"])

    if meta.get("duration") is not None:
        rec["Duration"] = f"{meta['duration']}s"
    elif meta.get("duration_ticks", 0) > 0:
        rec["Duration"] = f"{meta['duration_ticks']} ticks"

    return rec


def _info_walker(filepath, args):
    """Quick summary for a structural format the walkers decode but `info` has
    no dedicated builder for (Kurzweil/E-mu/Akai/RX2/FXP/tracker/SF2/MPC/... banks
    and synth presets). Shows the walker's top-level label + summary and points
    to `inspect` for the full byte-level decode, instead of mis-parsing the file
    as a headerless WAV."""
    from acidcat.core.walk import walk_file, Unsupported

    _vlog(args, "[detect] fmt=walker (structural)")
    rec = {"File": os.path.basename(filepath)}
    try:
        label, chunks, warns = walk_file(filepath)
    except Unsupported as e:
        rec["Format"] = "unrecognized structural format"
        rec["Note"] = str(e)
        return rec
    rec["Format"] = label
    if chunks and chunks[0].get("summary"):
        rec["Summary"] = chunks[0]["summary"]
    rec["Regions"] = len(chunks)
    if warns:
        rec["Warnings"] = len(warns)
    rec["Inspect"] = (f"use `acidcat inspect {os.path.basename(filepath)}` "
                      "for the full structural decode")
    return rec


def _info_serum(filepath, args):
    """Build info record for a Serum preset, from the inspect walker."""
    from acidcat.core.walk.serum import inspect_serum

    _vlog(args, "[detect] fmt=serum")
    meta = {}
    inspect_serum(filepath, ctx=meta)

    rec = {}
    rec["File"] = os.path.basename(filepath)
    rec["Format"] = "Serum Preset"

    if meta.get("presetName"):
        rec["Preset"] = meta["presetName"]
    if meta.get("presetAuthor"):
        rec["Author"] = meta["presetAuthor"]
    if meta.get("presetDescription"):
        rec["Description"] = meta["presetDescription"]
    if meta.get("product"):
        rec["Product"] = meta["product"]
    if meta.get("productVersion"):
        rec["Version"] = meta["productVersion"]
    if meta.get("tags"):
        tags = meta["tags"]
        if isinstance(tags, list):
            rec["Tags"] = ", ".join(tags)
        else:
            rec["Tags"] = str(tags)
    if meta.get("vendor"):
        rec["Vendor"] = meta["vendor"]
    if meta.get("fileType"):
        rec["File Type"] = meta["fileType"]

    return rec


def _info_tagged(filepath, args):
    """Build info record for tagged audio (MP3, FLAC, OGG, M4A)."""
    from acidcat.core.tagged import parse_tagged

    _vlog(args, "[detect] fmt=tagged (via mutagen)")
    meta = parse_tagged(filepath)
    if meta is None:
        return {"File": os.path.basename(filepath), "Format": "unknown (mutagen failed)"}

    rec = {}
    rec["File"] = os.path.basename(filepath)

    # format line
    fmt_type = meta.get("format_type", "unknown")
    fmt_parts = [fmt_type.upper()]
    if meta.get("sample_rate"):
        fmt_parts.append(f"{meta['sample_rate']}Hz")
    if meta.get("bits_per_sample"):
        fmt_parts.append(f"{meta['bits_per_sample']}-bit")
    elif meta.get("bitrate"):
        fmt_parts.append(f"{meta['bitrate'] // 1000}kbps")
    if meta.get("channels"):
        ch = meta["channels"]
        ch_label = "mono" if ch == 1 else "stereo" if ch == 2 else f"{ch}ch"
        fmt_parts.append(ch_label)
    rec["Format"] = " ".join(fmt_parts)

    if meta.get("duration") is not None:
        rec["Duration"] = f"{meta['duration']}s"

    if meta.get("title"):
        rec["Title"] = meta["title"]
    if meta.get("artist"):
        rec["Artist"] = meta["artist"]
    if meta.get("album"):
        rec["Album"] = meta["album"]

    if meta.get("bpm"):
        rec["BPM"] = meta["bpm"]
    else:
        rec["BPM"] = "-"

    if meta.get("key"):
        rec["Key"] = meta["key"]
    else:
        rec["Key"] = "-"

    if meta.get("genre"):
        rec["Genre"] = meta["genre"]
    if meta.get("date"):
        rec["Date"] = meta["date"]
    if meta.get("comment"):
        rec["Comment"] = meta["comment"]
    if meta.get("track_number"):
        rec["Track"] = meta["track_number"]
    if meta.get("disc_number"):
        rec["Disc"] = meta["disc_number"]
    if meta.get("encoder"):
        rec["Encoder"] = meta["encoder"]
    if meta.get("copyright"):
        rec["Copyright"] = meta["copyright"]
    if meta.get("publisher"):
        rec["Publisher"] = meta["publisher"]

    # deep analysis works on any audio format librosa can load
    if getattr(args, 'deep', False):
        _add_deep_analysis(filepath, rec, args)

    return rec


def _add_deep_analysis(filepath, rec, args):
    """Append librosa deep analysis fields to an existing record."""
    import time as _time

    if not getattr(args, 'quiet', False):
        print("  [deep] Running librosa analysis...", file=sys.stderr)

    t0 = _time.perf_counter()
    estimates = estimate_librosa_metadata(filepath)
    _vlog(args, f"[librosa] estimate_librosa_metadata: "
                f"{(_time.perf_counter() - t0) * 1000:.0f}ms")
    if estimates:
        if estimates.get("estimated_bpm") and rec.get("BPM") in (None, "-"):
            bpm_val = estimates["estimated_bpm"]
            src = estimates.get("bpm_source", "")
            rec["BPM"] = f"{bpm_val} ({src})" if src else bpm_val
        if estimates.get("estimated_key") and rec.get("Key") in (None, "-"):
            key_val = estimates["estimated_key"]
            src = estimates.get("key_source", "")
            rec["Key"] = f"{key_val} ({src})" if src else key_val

    t1 = _time.perf_counter()
    feats = extract_audio_features(filepath)
    _vlog(args, f"[librosa] extract_audio_features: "
                f"{(_time.perf_counter() - t1) * 1000:.0f}ms")
    if feats:
        rec["Spectral Centroid"] = f"{float(feats.get('spectral_centroid_mean', 0)):.1f} Hz"
        rec["RMS Energy"] = f"{float(feats.get('rms_mean', 0)):.6f}"
        rec["Zero Crossing Rate"] = f"{float(feats.get('zcr_mean', 0)):.4f}"
        rec["Tempo (librosa)"] = f"{float(feats.get('tempo_librosa', 0)):.1f}"
        rec["Beat Count"] = int(feats.get('beat_count', 0))


def run(args):
    filepath = args.target
    tmp_path = None

    # handle stdin: "acidcat -" or piped input
    if is_stdin_target(filepath):
        tmp_path = stdin_to_tempfile()
        if tmp_path is None:
            print("acidcat: no data on stdin", file=sys.stderr)
            return 1
        filepath = tmp_path

    if not os.path.isfile(filepath):
        print(f"acidcat: {filepath}: No such file", file=sys.stderr)
        return 1

    try:
        fmt_type = _detect_format(filepath)

        if fmt_type == "aiff":
            rec = _info_aiff(filepath, args)
        elif fmt_type == "midi":
            rec = _info_midi(filepath, args)
        elif fmt_type == "serum":
            rec = _info_serum(filepath, args)
        elif fmt_type == "tagged":
            rec = _info_tagged(filepath, args)
        elif fmt_type == "walker":
            rec = _info_walker(filepath, args)
        else:
            rec = _info_wav(filepath, args)

        # when reading from stdin, show <stdin> instead of tempfile name
        if tmp_path:
            rec["File"] = "<stdin>"

        # output
        stream = sys.stdout
        if getattr(args, 'output', None):
            stream = open(args.output, 'w', encoding='utf-8')

        fmt_name = getattr(args, 'format', 'table')
        output(rec, fmt=fmt_name, stream=stream)

        if stream is not sys.stdout:
            stream.close()

        return 0
    finally:
        if tmp_path:
            os.unlink(tmp_path)
