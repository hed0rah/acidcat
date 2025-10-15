#!/usr/bin/env python3
import re
import sys
import csv
import os

KEYS = r"(?:[A-G](?:#|b)?)(?:maj|minor|min|m)?"
BPM_RE  = re.compile(r"(\d{2,3})\s*bpm", re.I)
KEY_RE  = re.compile(rf"(^|[^A-Za-z])({KEYS})([^A-Za-z]|$)", re.I)

def parse_name(name):
    bpm = None
    key = None
    m = BPM_RE.search(name)
    if m:
        bpm = int(m.group(1))
    mk = KEY_RE.search(name.replace("_"," ").replace("-"," "))
    if mk:
        key = mk.group(2).capitalize()
        key = key.replace("Minor","m").replace("Min","m").replace("Major","maj").replace("Maj","maj")
    return bpm, key

def main():
    if len(sys.argv) < 3:
        print("Usage: filename_parser.py <metadata_csv_from_v3> <output_csv>")
        sys.exit(1)
    in_csv, out_csv = sys.argv[1], sys.argv[2]
    rows = []
    with open(in_csv, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            base = os.path.basename(row["filename"])
            fbpm, fkey = parse_name(base)
            row["filename_bpm"] = fbpm
            row["filename_key"] = fkey
            rows.append(row)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            w.writeheader()
            w.writerows(rows)
    print(f"[INFO] wrote {len(rows)} rows to {out_csv}")

if __name__ == "__main__":
    main()
