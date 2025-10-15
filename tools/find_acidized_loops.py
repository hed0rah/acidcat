"""
find_acidized_loops.py

Quick scanner that walks a directory tree and prints paths of WAV files
that contain an 'acid' RIFF chunk.
"""

import argparse
import os
import struct

def has_acid_chunk(filepath):
    """Return True if file contains a valid 'acid' RIFF chunk."""
    try:
        with open(filepath, 'rb') as f:
            riff = f.read(12)  # RIFF header (12 bytes)
            if not riff.startswith(b'RIFF') or riff[8:12] != b'WAVE':
                return False

            # Iterate through chunks
            while True:
                header = f.read(8)
                if len(header) < 8:
                    break
                chunk_id, chunk_size = struct.unpack('<4sI', header)
                if chunk_id == b'acid':
                    return True
                f.seek(chunk_size, os.SEEK_CUR)

        return False
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser(description="Find WAV files with ACID metadata.")
    parser.add_argument("directory", help="Root directory to search.")
    parser.add_argument("-o", "--output", help="Optional text file to save results.")
    args = parser.parse_args()

    acidized_files = []

    for root, _, files in os.walk(args.directory):
        for file in files:
            if file.lower().endswith(".wav"):
                path = os.path.join(root, file)
                if has_acid_chunk(path):
                    acidized_files.append(path)
                    print(f"[ACID] {path}")

    print(f"\n[INFO] Found {len(acidized_files)} acidized WAV file(s).")

    if args.output:
        with open(args.output, "w") as out:
            for path in acidized_files:
                out.write(path + "\n")
        print(f"[INFO] Wrote list to {args.output}")

if __name__ == "__main__":
    main()
