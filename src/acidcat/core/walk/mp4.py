"""ISO-BMFF MP4/M4A structural walker: decoded iTunes metadata and the
box tree. Box primitives live in core/mp4.py."""

import os

from acidcat.core import mp4 as mp4mod
from acidcat.core.walk.base import _f

def inspect_mp4(filepath):
    """Structural view of an ISO-BMFF MP4/M4A file: the decoded metadata (from
    udta > meta > ilst and the movie duration) followed by the box tree."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(file_size, 8 * 1024 * 1024))  # box tree from the head
        # metadata lives in moov; non-faststart files (most Apple/ffmpeg output)
        # put moov at EOF, past the head window. locate and read just moov then.
        moov_data = data
        if not any(b["type"] == b"moov" for b in mp4mod.iter_boxes(data)) \
                and file_size > len(data):
            moff, msz = mp4mod.find_moov(filepath, file_size)
            if moff is not None:
                f.seek(moff)
                moov_data = f.read(min(msz, 32 * 1024 * 1024))
    chunks, warns = [], []

    ts, dur = mp4mod.movie_timescale_duration(moov_data)
    dur_s = dur / ts if ts and dur else None
    ainfo = mp4mod.audio_info(moov_data)
    meta = mp4mod.parse_ilst(moov_data)
    mfields = []
    if ainfo:
        codec, ch, rate = ainfo
        codec_names = {"mp4a": "AAC", "alac": "Apple Lossless", "Opus": "Opus",
                       "fLaC": "FLAC", "ac-3": "AC-3", "ec-3": "E-AC-3"}
        desc = codec_names.get(codec, codec)
        if ch:
            desc += f", {ch}ch"
            if rate:
                desc += f" {rate} Hz"
        mfields.append(_f(None, 0, "codec", desc))
    if dur_s:
        mfields.append(_f(None, 0, "duration", f"{dur_s:.3f} s"))
    for label in ("title", "artist", "album_artist", "album", "year", "genre",
                  "bpm", "composer", "encoder", "comment", "track", "disc",
                  "cover_art", "compilation"):
        if label in meta:
            mfields.append(_f(None, 0, label, str(meta[label])[:200]))
    if mfields:
        title = meta.get("title", "")
        chunks.append({"id": "tags", "offset": 0, "size": 0,
                       "summary": f"'{title}'" if title else "iTunes metadata",
                       "fields": mfields, "warnings": []})

    for b in mp4mod.iter_boxes(data, file_size=file_size):
        t = b["type"].decode("latin-1", errors="replace")
        summary = ". " * b["depth"] + t
        fields = []
        if b["truncated"]:
            warns.append(f"box {t!r} at 0x{b['offset']:08x} overruns its parent")
            summary += " (overruns parent)"
        elif b.get("beyond_cap"):
            # a valid box (e.g. a large mdat) whose contents run past the read
            # window: not an error, just not fully read.
            summary += " (content beyond read window)"
        elif b["type"] == b"ftyp" and b["depth"] == 0:
            brand = data[b["offset"] + b["hdr"]:b["offset"] + b["hdr"] + 4]
            summary += f"  major brand {brand.decode('latin-1', errors='replace')}"
            fields.append(_f(0x00, 4, "major_brand",
                             brand.decode("latin-1", errors="replace")))
        chunks.append({"id": t[:8], "offset": b["offset"], "size": b["size"],
                       "summary": summary, "fields": fields, "warnings": [],
                       "payload_base": b["offset"] + b["hdr"]})
    return chunks, warns
