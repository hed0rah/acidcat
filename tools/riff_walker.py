#!/usr/bin/env python3
"""
riff_walker.py

Show the RIFF/WAVE chunk layout for a single file: offsets and sizes in order.

Example:
  python riff_walker.py "D:\\Audio\\Loops\\SomeLoop.wav"
"""

import argparse
import os
import struct

def walk_file(filepath):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        hdr = f.read(12)
        if len(hdr) < 12 or hdr[0:4] != b"RIFF":
            print("[ERR] Not a RIFF file")
            return
        riff_size = struct.unpack("<I", hdr[4:8])[0]
        riff_type = hdr[8:12].decode("ascii", errors="ignore")
        print(f"[INFO] RIFF container size: {riff_size} bytes, type: {riff_type}")
        pos = 12
        idx = 0
        while pos + 8 <= size:
            f.seek(pos)
            ch = f.read(8)
            if len(ch) < 8:
                break
            cid = ch[0:4].decode("ascii", errors="ignore")
            csz = struct.unpack("<I", ch[4:8])[0]
            print(f"Chunk {cid:4s} @ {pos}, size={csz}")
            pos += 8 + csz
            if csz % 2 == 1:
                pos += 1
            idx += 1

def main():
    ap = argparse.ArgumentParser(description="Walk a RIFF/WAVE file and print chunk offsets and sizes.")
    ap.add_argument("file", help="WAV file path")
    args = ap.parse_args()
    walk_file(args.file)

if __name__ == "__main__":
    main()
