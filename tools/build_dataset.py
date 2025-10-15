#!/usr/bin/env python3
import sys, csv, math

def to_float(x):
    if x is None:
        return None
    s = str(x).strip()
    if s in ("", "None", "nan", "NaN"):
        return None
    try:
        v = float(s)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except:
        return None

def to_int(x):
    if x is None:
        return None
    s = str(x).strip()
    if s in ("", "None", "nan", "NaN"):
        return None
    try:
        return int(round(float(s)))
    except:
        return None

def main():
    if len(sys.argv) < 3:
        print("Usage: build_dataset.py <in_csv_from_filename_parser_or_acid_cat> <out_csv>")
        sys.exit(1)

    in_csv, out_csv = sys.argv[1], sys.argv[2]
    rows_out = []

    with open(in_csv, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            bpm_cat  = to_float(row.get("bpm"))
            bpm_name = to_float(row.get("filename_bpm"))
            bpm_filled = bpm_cat or bpm_name  # prefer ACID BPM, else filename BPM

            dur = to_float(row.get("duration_sec"))

            acid_beats = to_int(row.get("acid_beats"))
            beats_est = to_int((dur * bpm_filled / 60.0) if (dur and bpm_filled) else None)

            # choose final beats: trust ACID if present/nonzero, else estimate
            beats_final = acid_beats if (acid_beats and acid_beats > 0) else beats_est

            # expected duration from final beats/BPM
            expected_duration = None
            duration_diff = None
            if beats_final and bpm_filled:
                expected_duration = beats_final * 60.0 / bpm_filled
                if dur is not None:
                    duration_diff = round(dur - expected_duration, 4)

            row["bpm_filled"] = bpm_filled
            row["beats_est"] = beats_est
            row["beats_final"] = beats_final
            row["expected_duration_final"] = round(expected_duration, 4) if expected_duration else None
            row["duration_diff_final"] = duration_diff

            rows_out.append(row)

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        fieldnames = list(rows_out[0].keys()) if rows_out else []
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if rows_out:
            w.writeheader()
            w.writerows(rows_out)

    print(f"[INFO] wrote {len(rows_out)} rows to {out_csv}")

if __name__ == "__main__":
    main()
