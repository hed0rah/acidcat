"""
acidcat scan -- batch-scan a directory of audio files.
"""

import csv
import os
import sys

from acidcat.core.riff import parse_riff, get_duration
from acidcat.core.aiff import is_aiff, parse_aiff
from acidcat.core.tagged import is_tagged_format
from acidcat.core.formats import output
from acidcat.util.midi import midi_note_to_name
from acidcat.util.csv_helpers import safe_basename_for_csv


# extensions to pick up during directory walk
AUDIO_EXTENSIONS = {
    ".wav", ".aif", ".aiff",
    ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac",
}


def register(subparsers):
    p = subparsers.add_parser("scan", help="Batch-scan a directory of audio files.")
    p.add_argument("target", help="Directory containing audio files.")
    p.add_argument("-o", "--output", help="Output CSV filename.")
    p.add_argument("-n", "--num", type=int, default=500, help="Max files to scan (default: 500).")
    p.add_argument("-q", "--quiet", action="store_true", help="Suppress console output.")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose output.")
    p.add_argument("-f", "--format", default="csv", choices=["table", "json", "csv"],
                   help="Output format (default: csv).")
    p.add_argument("--has", help="Filter: only WAV files containing these chunk IDs (comma-separated).")
    p.add_argument("--fallback", action="store_true",
                   help="Estimate BPM/key with librosa if no metadata found.")
    p.add_argument("--features", action="store_true",
                   help="Extract 50+ audio features for ML analysis.")
    p.add_argument("--ml-ready", action="store_true",
                   help="Output normalized ML-ready features.")
    p.set_defaults(func=run)


def _scan_wav(filepath):
    """Extract metadata row from a WAV file."""
    _, meta, seen = parse_riff(filepath, enumerate_all=False)
    duration = get_duration(filepath)

    expected = diff = None
    if meta["bpm"] and meta["acid_beats"] and meta["acid_beats"] > 0:
        expected = round((meta["acid_beats"] / meta["bpm"]) * 60, 4)
        diff = round(duration - expected, 4) if duration else None

    return {
        "filename": filepath,
        "format": "wav",
        "bpm": meta["bpm"],
        "key": midi_note_to_name(meta["smpl_root_key"]) or midi_note_to_name(meta["acid_root_note"]),
        "duration_sec": duration,
        "title": None,
        "artist": None,
        "acid_beats": meta["acid_beats"],
        "expected_duration": expected,
        "duration_diff": diff,
        "chunks": ",".join(c for c in seen if c not in ("RIFF", "WAVE", "fmt ", "data")),
    }, seen


def _scan_aiff(filepath):
    """Extract metadata row from an AIFF file."""
    _, meta, seen = parse_aiff(filepath, enumerate_all=False)

    return {
        "filename": filepath,
        "format": "aiff",
        "bpm": None,
        "key": None,
        "duration_sec": meta.get("duration_sec"),
        "title": meta.get("name"),
        "artist": meta.get("author"),
        "acid_beats": None,
        "expected_duration": None,
        "duration_diff": None,
        "chunks": ",".join(seen),
    }, seen


def _scan_tagged(filepath):
    """Extract metadata row from a tagged format (MP3, FLAC, OGG, M4A)."""
    from acidcat.core.tagged import parse_tagged

    meta = parse_tagged(filepath)
    if meta is None:
        return None, []

    return {
        "filename": filepath,
        "format": meta.get("format_type", "unknown"),
        "bpm": meta.get("bpm"),
        "key": meta.get("key"),
        "duration_sec": meta.get("duration"),
        "title": meta.get("title"),
        "artist": meta.get("artist"),
        "acid_beats": None,
        "expected_duration": None,
        "duration_diff": None,
        "chunks": None,
    }, []


def run(args):
    directory = args.target
    if not os.path.isdir(directory):
        print(f"acidcat scan: {directory}: Not a directory", file=sys.stderr)
        return 1

    default_base = os.path.basename(os.path.normpath(directory))
    output_csv = safe_basename_for_csv(
        getattr(args, 'output', None) or (default_base + "_metadata.csv")
    )

    wanted = None
    has_val = getattr(args, 'has', None)
    if has_val:
        wanted = set(w.strip().upper() for w in has_val.split(",") if w.strip())

    quiet = getattr(args, 'quiet', False)
    verbose = getattr(args, 'verbose', False) and not quiet
    do_fallback = getattr(args, 'fallback', False)
    do_features = getattr(args, 'features', False)
    do_ml_ready = getattr(args, 'ml_ready', False)
    num = getattr(args, 'num', 500)

    def _vlog(msg):
        if verbose:
            print(msg, file=sys.stderr)

    _vlog(f"[scan] dir={directory} num={num} fallback={do_fallback} "
          f"features={do_features}")

    rows = []
    count = 0

    for root, _, files in os.walk(directory):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue

            filepath = os.path.join(root, file)

            # dispatch by format
            try:
                if ext in (".wav",):
                    row, seen = _scan_wav(filepath)
                elif ext in (".aif", ".aiff") or is_aiff(filepath):
                    row, seen = _scan_aiff(filepath)
                elif is_tagged_format(filepath):
                    row, seen = _scan_tagged(filepath)
                    if row is None:
                        continue
                else:
                    continue
            except Exception as e:
                if not quiet:
                    print(f"  [skip] {file}: {e}", file=sys.stderr)
                continue

            # chunk filter (only applies to chunk-based formats)
            if wanted and seen:
                upper_seen = {s.upper() for s in seen}
                if not (upper_seen & wanted):
                    continue

            # optional ML features
            if do_features or do_ml_ready:
                from acidcat.core.features import extract_audio_features
                if not quiet:
                    print(f"  [features] {os.path.basename(filepath)}...", file=sys.stderr)
                feats = extract_audio_features(filepath)
                if feats:
                    row.update(feats)

            # fallback BPM/key via librosa
            if do_fallback and not row.get("bpm"):
                from acidcat.core.detect import estimate_librosa_metadata
                estimates = estimate_librosa_metadata(filepath)
                if estimates.get("estimated_bpm") is not None:
                    row["bpm"] = estimates["estimated_bpm"]
                if estimates.get("estimated_key") is not None:
                    row["key"] = estimates["estimated_key"]
                if estimates.get("duration_sec") is not None and not row.get("duration_sec"):
                    row["duration_sec"] = estimates["duration_sec"]

            if not quiet:
                bpm_str = row.get("bpm") or "-"
                print(f"  {os.path.basename(filepath):40s} BPM={bpm_str}", file=sys.stderr)
            if verbose:
                _vlog(f"    format={row.get('format')} "
                      f"key={row.get('key') or '-'} "
                      f"dur={row.get('duration_sec') or '-'}")

            rows.append(row)
            count += 1
            if count >= num:
                break
        if count >= num:
            break

    if not rows:
        if not quiet:
            print("acidcat scan: No audio files found.", file=sys.stderr)
        return 0

    # fieldnames: core set, then any extras from features
    base_fieldnames = [
        "filename", "format", "bpm", "key", "duration_sec",
        "title", "artist",
        "acid_beats", "expected_duration", "duration_diff", "chunks",
    ]
    if (do_features or do_ml_ready) and rows:
        all_keys = set()
        for r in rows:
            all_keys.update(r.keys())
        feature_keys = sorted(k for k in all_keys if k not in base_fieldnames)
        fieldnames = base_fieldnames + feature_keys
    else:
        fieldnames = base_fieldnames

    # output
    if do_ml_ready:
        import numpy as np
        import pandas as pd
        from sklearn.preprocessing import StandardScaler

        df = pd.DataFrame(rows)
        df.to_csv(output_csv, index=False)

        ml_csv = output_csv.replace('.csv', '_ml_ready.csv')
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if numeric_cols:
            scaler = StandardScaler()
            df_norm = df.copy()
            df_norm[numeric_cols] = scaler.fit_transform(df[numeric_cols])
            df_norm.to_csv(ml_csv, index=False)
            if not quiet:
                print(f"\n[INFO] Wrote raw features for {len(rows)} files to {output_csv}",
                      file=sys.stderr)
                print(f"[INFO] Wrote ML-ready normalized features to {ml_csv}", file=sys.stderr)
        else:
            if not quiet:
                print(f"\n[INFO] Wrote metadata for {len(rows)} files to {output_csv}",
                      file=sys.stderr)
    else:
        with open(output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        if not quiet:
            print(f"\n[INFO] Wrote metadata for {len(rows)} files to {output_csv}",
                  file=sys.stderr)

    return 0
