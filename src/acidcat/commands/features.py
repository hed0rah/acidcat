"""
acidcat features -- extract 50+ audio features for ML analysis.
"""

import os
import sys

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from acidcat.core.features import extract_audio_features
from acidcat.core.formats import output
from acidcat.util.csv_helpers import safe_basename_for_csv


def register(subparsers):
    p = subparsers.add_parser("features", help="Extract ML audio features from WAV files.")
    p.add_argument("target", help="WAV file or directory.")
    p.add_argument("-n", "--num", type=int, default=500, help="Max files to scan.")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--ml-ready", action="store_true",
                   help="Also output normalized (StandardScaler) version.")
    p.add_argument("-f", "--format", default="csv", choices=["table", "json", "csv"],
                   help="Output format (default: csv).")
    p.add_argument("-o", "--output", help="Output file path.")
    p.set_defaults(func=run)


def run(args):
    target = args.target
    quiet = getattr(args, 'quiet', False)
    ml_ready = getattr(args, 'ml_ready', False)
    fmt_name = getattr(args, 'format', 'csv')

    # Single file
    if os.path.isfile(target):
        feats = extract_audio_features(target)
        if feats is None:
            print(f"acidcat features: Could not extract features from {target}", file=sys.stderr)
            return 1
        feats["filename"] = os.path.basename(target)
        stream = sys.stdout
        if getattr(args, 'output', None):
            stream = open(args.output, 'w')
        output(feats, fmt=fmt_name, stream=stream)
        if stream is not sys.stdout:
            stream.close()
        return 0

    # Directory
    if not os.path.isdir(target):
        print(f"acidcat features: {target}: No such file or directory", file=sys.stderr)
        return 1

    num = getattr(args, 'num', 500)
    rows = []
    count = 0

    for root, _, files in os.walk(target):
        for fn in files:
            if not fn.lower().endswith(".wav"):
                continue
            filepath = os.path.join(root, fn)
            if not quiet:
                print(f"  [features] {fn}...", file=sys.stderr)
            feats = extract_audio_features(filepath)
            if feats:
                feats["filename"] = filepath
                rows.append(feats)
            count += 1
            if count >= num:
                break
        if count >= num:
            break

    if not rows:
        print("acidcat features: No features extracted.", file=sys.stderr)
        return 0

    default_base = os.path.basename(os.path.normpath(target))
    out_path = getattr(args, 'output', None) or safe_basename_for_csv(
        default_base + "_features.csv"
    )

    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False)
    if not quiet:
        print(f"\n[INFO] Wrote features for {len(rows)} files to {out_path}", file=sys.stderr)

    if ml_ready:
        ml_csv = out_path.replace('.csv', '_ml_ready.csv')
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if numeric_cols:
            scaler = StandardScaler()
            df_norm = df.copy()
            df_norm[numeric_cols] = scaler.fit_transform(df[numeric_cols])
            df_norm.to_csv(ml_csv, index=False)
            if not quiet:
                print(f"[INFO] Wrote ML-ready normalized features to {ml_csv}", file=sys.stderr)

    return 0
