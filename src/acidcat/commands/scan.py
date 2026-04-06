"""
acidcat scan -- batch-scan a directory of WAV files.
"""

import csv
import os
import sys

from acidcat.core.riff import parse_riff, get_duration
from acidcat.core.formats import output
from acidcat.util.midi import midi_note_to_name
from acidcat.util.csv_helpers import safe_basename_for_csv


def register(subparsers):
    p = subparsers.add_parser("scan", help="Batch-scan a directory of WAV files.")
    p.add_argument("target", help="Directory containing WAV files.")
    p.add_argument("-o", "--output", help="Output CSV filename.")
    p.add_argument("-n", "--num", type=int, default=500, help="Max files to scan (default: 500).")
    p.add_argument("-q", "--quiet", action="store_true", help="Suppress console output.")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose output.")
    p.add_argument("-f", "--format", default="csv", choices=["table", "json", "csv"],
                   help="Output format (default: csv).")
    p.add_argument("--has", help="Filter: only files containing these chunk IDs (comma-separated).")
    p.add_argument("--fallback", action="store_true",
                   help="Estimate BPM/key with librosa if no metadata found.")
    p.add_argument("--features", action="store_true",
                   help="Extract 50+ audio features for ML analysis.")
    p.add_argument("--ml-ready", action="store_true",
                   help="Output normalized ML-ready features.")
    p.set_defaults(func=run)


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
    do_fallback = getattr(args, 'fallback', False)
    do_features = getattr(args, 'features', False)
    do_ml_ready = getattr(args, 'ml_ready', False)
    num = getattr(args, 'num', 500)

    rows = []
    count = 0

    for root, _, files in os.walk(directory):
        for file in files:
            if not file.lower().endswith(".wav"):
                continue
            filepath = os.path.join(root, file)
            _, meta, seen = parse_riff(filepath, enumerate_all=False)

            if wanted:
                upper_seen = {s.upper() for s in seen}
                if not (upper_seen & wanted):
                    continue

            duration = get_duration(filepath)

            expected = diff = None
            if meta["bpm"] and meta["acid_beats"] and meta["acid_beats"] > 0:
                expected = round((meta["acid_beats"] / meta["bpm"]) * 60, 4)
                diff = round(duration - expected, 4) if duration else None

            row = {
                "filename": filepath,
                "bpm": meta["bpm"],
                "acid_root_note": midi_note_to_name(meta["acid_root_note"]),
                "acid_beats": meta["acid_beats"],
                "smpl_root_key": midi_note_to_name(meta["smpl_root_key"]),
                "smpl_loop_start": meta["smpl_loop_start"],
                "smpl_loop_end": meta["smpl_loop_end"],
                "duration_sec": duration,
                "expected_duration": expected,
                "duration_diff": diff,
                "other_chunks": ",".join(
                    c for c in seen if c not in ("RIFF", "WAVE", "fmt ", "data", "acid", "smpl")
                ),
            }

            # Features
            if do_features or do_ml_ready:
                from acidcat.core.features import extract_audio_features
                if not quiet:
                    print(f"  [features] {os.path.basename(filepath)}...", file=sys.stderr)
                feats = extract_audio_features(filepath)
                if feats:
                    row.update(feats)

            # Fallback BPM/key
            if do_fallback:
                from acidcat.core.detect import estimate_librosa_metadata
                estimates = estimate_librosa_metadata(filepath)
                if estimates.get("estimated_bpm") is not None:
                    row["bpm"] = estimates["estimated_bpm"]
                if estimates.get("estimated_key") is not None:
                    row["smpl_root_key"] = estimates["estimated_key"]
                if estimates.get("duration_sec") is not None:
                    row["duration_sec"] = estimates["duration_sec"]

            if not quiet:
                bpm_str = row.get("bpm") or "-"
                print(f"  {os.path.basename(filepath):40s} BPM={bpm_str}", file=sys.stderr)

            rows.append(row)
            count += 1
            if count >= num:
                break
        if count >= num:
            break

    if not rows:
        if not quiet:
            print("acidcat scan: No WAV files found.", file=sys.stderr)
        return 0

    # Determine fieldnames
    base_fieldnames = [
        "filename", "bpm", "acid_root_note", "acid_beats", "smpl_root_key",
        "smpl_loop_start", "smpl_loop_end", "duration_sec",
        "expected_duration", "duration_diff", "other_chunks",
    ]
    if (do_features or do_ml_ready) and rows:
        all_keys = set()
        for r in rows:
            all_keys.update(r.keys())
        feature_keys = sorted(k for k in all_keys if k not in base_fieldnames)
        fieldnames = base_fieldnames + feature_keys
    else:
        fieldnames = base_fieldnames

    # Write output
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
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        if not quiet:
            print(f"\n[INFO] Wrote metadata for {len(rows)} files to {output_csv}",
                  file=sys.stderr)

    return 0
