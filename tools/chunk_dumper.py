#!/usr/bin/env python3
"""
chunk_dumper.py

Dump specific chunk(s) from a WAV file, printing offset/size and a hex preview.
Optionally write the raw chunk payload bytes to files.

Examples:
  # Print ACID chunk info with a short hex
  python chunk_dumper.py "D:\\Audio\\Loops\\Loop.wav" acid

  # Dump multiple chunks and write raw payloads to ./out/
  python chunk_dumper.py "D:\\Audio\\Loops\\Loop.wav" acid smpl LIST -o out -b 128
"""

import argparse
import os
import struct
import binascii

def iter_chunks(filepath):
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
            csz = struct.unpack("<I", ch[4:8])[0]
            payload_off = pos + 8
            yield (cid, payload_off, csz)
            pos += 8 + csz
            if csz % 2 == 1:
                pos += 1

def main():
    ap = argparse.ArgumentParser(description="Dump specific RIFF chunks from a WAV file.")
    ap.add_argument("file", help="WAV file path")
    ap.add_argument("chunks", nargs="+", help="Chunk IDs to dump (e.g. acid smpl LIST). Case-insensitive.")
    ap.add_argument("-b", "--bytes", type=int, default=64, help="Hex preview length (bytes)")
    ap.add_argument("-o", "--outdir", help="If set, write raw chunk payloads to this directory")
    args = ap.parse_args()

    wanted = {c.upper() for c in args.chunks}
    base = os.path.basename(args.file)
    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)

    found_any = False
    try:
        for cid, off, sz in iter_chunks(args.file):
            if cid.upper() not in wanted:
                continue
            found_any = True
            with open(args.file, "rb") as f:
                f.seek(off)
                payload = f.read(sz)
            preview = binascii.hexlify(payload[:args.bytes]).decode()
            print(f"[FOUND] Chunk {cid} @ {off}, size={sz} bytes")
            print("Hex preview:", " ".join(preview[i:i+2] for i in range(0, len(preview), 2)))
            if args.outdir:
                # Write raw payload (without the 8-byte header)
                outname = f"{os.path.splitext(base)[0]}_{cid}_{off}.bin"
                outpath = os.path.join(args.outdir, outname)
                with open(outpath, "wb") as g:
                    g.write(payload)
                print(f"[WRITE] {outpath}")
            print()
    except FileNotFoundError:
        print(f"[ERR] File not found: {args.file}")
        return

    if not found_any:
        print("[MISS] None of the requested chunks were found.")

if __name__ == "__main__":
    main()
