"""
acidcat info -- single-file metadata dump.

The star command: ``acidcat file.wav`` dumps metadata like exiftool.
Supports WAV, AIFF, MIDI, and Serum preset files.
"""

import os
import sys

from acidcat.core.riff import parse_riff, get_duration, get_fmt_info
from acidcat.core.aiff import is_aiff, parse_aiff
from acidcat.core.midi import is_midi, parse_midi
from acidcat.core.serum import is_serum_preset, parse_serum_preset
from acidcat.core.detect import estimate_librosa_metadata
from acidcat.core.features import extract_audio_features
from acidcat.core.formats import output
from acidcat.util.midi import midi_note_to_name


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
    """Detect file format by magic bytes, falling back to extension."""
    if is_midi(filepath):
        return "midi"
    if is_aiff(filepath):
        return "aiff"
    if is_serum_preset(filepath):
        return "serum"
    # default: try as WAV/RIFF
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".aif", ".aiff"):
        return "aiff"
    if ext in (".mid", ".midi"):
        return "midi"
    if ext.lower() == ".serumpreset":
        return "serum"
    return "wav"


def _info_wav(filepath, args):
    """Build info record for a WAV/RIFF file."""
    _, meta, seen = parse_riff(filepath, enumerate_all=False)
    duration = get_duration(filepath)
    fmt = get_fmt_info(filepath)

    rec = {}
    rec["File"] = os.path.basename(filepath)

    if fmt:
        codec = "PCM" if fmt["format_tag"] == 1 else f"tag={fmt['format_tag']}"
        ch = fmt["channels"]
        ch_label = "mono" if ch == 1 else "stereo" if ch == 2 else f"{ch}ch"
        rec["Format"] = f"WAV {codec} {fmt['sample_rate']}Hz {fmt['bits_per_sample']}-bit {ch_label}"

    if duration is not None:
        rec["Duration"] = f"{duration}s"

    if meta["bpm"] is not None:
        rec["BPM"] = meta["bpm"]
        if meta["acid_beats"]:
            rec["Beats"] = meta["acid_beats"]
        if meta["acid_root_note"] is not None:
            rec["ACID Root"] = midi_note_to_name(meta["acid_root_note"])
        if meta["acid_beats"] and meta["bpm"]:
            expected = round((meta["acid_beats"] / meta["bpm"]) * 60, 4)
            rec["Expected Duration"] = f"{expected}s"
            if duration:
                diff = round(duration - expected, 4)
                rec["Duration Diff"] = f"{diff}s"
    else:
        rec["BPM"] = "-"

    if meta["smpl_root_key"] is not None:
        rec["Key"] = f"{midi_note_to_name(meta['smpl_root_key'])} (from SMPL)"
    elif meta["acid_root_note"] is not None:
        rec["Key"] = f"{midi_note_to_name(meta['acid_root_note'])} (from ACID)"
    else:
        rec["Key"] = "-"

    rec["ACID"] = "yes" if meta["bpm"] is not None else "no"

    has_smpl = meta["smpl_root_key"] is not None
    if has_smpl:
        smpl_parts = [f"root={midi_note_to_name(meta['smpl_root_key'])}"]
        if meta["smpl_loop_start"] is not None:
            smpl_parts.append(f"loop={meta['smpl_loop_start']}-{meta['smpl_loop_end']}")
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
    """Build info record for an AIFF/AIFC file."""
    _, meta, seen = parse_aiff(filepath, enumerate_all=False)

    rec = {}
    rec["File"] = os.path.basename(filepath)

    # format line
    fmt_parts = ["AIFF"]
    if meta.get("compression") and meta["compression"] not in ("none", "NONE"):
        fmt_parts[0] = "AIFC"
        fmt_parts.append(meta["compression"])
    if meta.get("sample_rate"):
        fmt_parts.append(f"{meta['sample_rate']}Hz")
    if meta.get("bits_per_sample"):
        fmt_parts.append(f"{meta['bits_per_sample']}-bit")
    if meta.get("channels"):
        ch = meta["channels"]
        ch_label = "mono" if ch == 1 else "stereo" if ch == 2 else f"{ch}ch"
        fmt_parts.append(ch_label)
    rec["Format"] = " ".join(fmt_parts)

    if meta.get("duration_sec") is not None:
        rec["Duration"] = f"{meta['duration_sec']}s"

    if meta.get("num_frames") is not None:
        rec["Frames"] = meta["num_frames"]

    if meta.get("name"):
        rec["Name"] = meta["name"]
    if meta.get("author"):
        rec["Author"] = meta["author"]
    if meta.get("copyright"):
        rec["Copyright"] = meta["copyright"]

    rec["Chunks"] = ", ".join(seen) if seen else "(none)"

    if getattr(args, 'deep', False):
        _add_deep_analysis(filepath, rec, args)

    return rec


def _info_midi(filepath, args):
    """Build info record for a MIDI file."""
    meta = parse_midi(filepath)

    rec = {}
    rec["File"] = os.path.basename(filepath)
    rec["Format"] = f"MIDI type {meta['format']}" if meta["format"] is not None else "MIDI"

    if meta["tracks"] is not None:
        rec["Tracks"] = meta["tracks"]

    if meta["division"] is not None:
        rec["Division"] = f"{meta['division']} ticks/beat"

    if meta["tempo_bpm"] is not None:
        rec["BPM"] = meta["tempo_bpm"]

    if meta["time_sig"]:
        rec["Time Sig"] = meta["time_sig"]

    if meta["key_sig"]:
        rec["Key"] = meta["key_sig"]

    if meta["track_names"]:
        rec["Track Names"] = ", ".join(meta["track_names"])

    if meta.get("copyright"):
        rec["Copyright"] = meta["copyright"]

    if meta["note_count"] > 0:
        rec["Notes"] = meta["note_count"]
        if meta["note_min"] is not None and meta["note_max"] is not None:
            rec["Note Range"] = f"{midi_note_to_name(meta['note_min'])}-{midi_note_to_name(meta['note_max'])}"

    if meta["channels_used"]:
        rec["Channels"] = ", ".join(str(c) for c in meta["channels_used"])

    if meta.get("duration_sec") is not None:
        rec["Duration"] = f"{meta['duration_sec']}s"
    elif meta["duration_ticks"] > 0:
        rec["Duration"] = f"{meta['duration_ticks']} ticks"

    return rec


def _info_serum(filepath, args):
    """Build info record for a Serum preset."""
    meta = parse_serum_preset(filepath)

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


def _add_deep_analysis(filepath, rec, args):
    """Append librosa deep analysis fields to an existing record."""
    if not getattr(args, 'quiet', False):
        print("  [deep] Running librosa analysis...", file=sys.stderr)

    estimates = estimate_librosa_metadata(filepath)
    if estimates:
        if estimates.get("estimated_bpm") and rec.get("BPM") in (None, "-"):
            bpm_val = estimates["estimated_bpm"]
            src = estimates.get("bpm_source", "")
            rec["BPM"] = f"{bpm_val} ({src})" if src else bpm_val
        if estimates.get("estimated_key") and rec.get("Key") in (None, "-"):
            key_val = estimates["estimated_key"]
            src = estimates.get("key_source", "")
            rec["Key"] = f"{key_val} ({src})" if src else key_val

    feats = extract_audio_features(filepath)
    if feats:
        rec["Spectral Centroid"] = f"{float(feats.get('spectral_centroid_mean', 0)):.1f} Hz"
        rec["RMS Energy"] = f"{float(feats.get('rms_mean', 0)):.6f}"
        rec["Zero Crossing Rate"] = f"{float(feats.get('zcr_mean', 0)):.4f}"
        rec["Tempo (librosa)"] = f"{float(feats.get('tempo_librosa', 0)):.1f}"
        rec["Beat Count"] = int(feats.get('beat_count', 0))


def run(args):
    filepath = args.target
    if not os.path.isfile(filepath):
        print(f"acidcat: {filepath}: No such file", file=sys.stderr)
        return 1

    fmt_type = _detect_format(filepath)

    if fmt_type == "aiff":
        rec = _info_aiff(filepath, args)
    elif fmt_type == "midi":
        rec = _info_midi(filepath, args)
    elif fmt_type == "serum":
        rec = _info_serum(filepath, args)
    else:
        rec = _info_wav(filepath, args)

    # output
    stream = sys.stdout
    if getattr(args, 'output', None):
        stream = open(args.output, 'w')

    fmt_name = getattr(args, 'format', 'table')
    output(rec, fmt=fmt_name, stream=stream)

    if stream is not sys.stdout:
        stream.close()

    return 0
