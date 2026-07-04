"""Ogg structural walker: page census, codec identity, and the
Vorbis/Opus comment header. Page primitives live in core/ogg.py."""

import os

from acidcat.core import ogg as oggmod
from acidcat.core.walk.base import _f

def inspect_ogg(filepath):
    """Structural view of an Ogg stream: page count/codec and the Vorbis/Opus
    comment header (vendor + tags). The audio packets are opaque."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(file_size, 16 * 1024 * 1024))
    pages = list(oggmod.iter_pages(data))
    ch = oggmod.comment_header(data)
    ident = oggmod.identification(data)
    codec = ch[0] if ch else (ident[0] if ident else "unknown")
    serial = pages[0]["serial"] if pages else 0
    fields = [_f(0x00, 4, "codec", codec),
              _f(None, 0, "pages", len(pages)),
              _f(None, 0, "bitstream_serial", serial)]
    rate_txt = ""
    if ident and ident[1]:
        chn, sr = ident[1].get("channels"), ident[1].get("sample_rate")
        if chn is not None:
            fields.append(_f(None, 0, "channels", chn))
        if sr:
            fields.append(_f(None, 0, "sample_rate", sr))
            rate_txt = f", {chn}ch {sr} Hz"
        # duration from the last page's granule position (a running sample count).
        # opus always counts granules at 48 kHz regardless of the decode rate.
        gran_rate = 48000 if "opus" in codec.lower() else sr
        last_gran = max((p["granule"] for p in pages if p.get("granule", -1) >= 0),
                        default=0)
        if gran_rate and last_gran > 0:
            duration = last_gran / gran_rate
            fields.append(_f(None, 0, "duration", f"{duration:.3f} s"))
            rate_txt += f", {duration:.3f} s"
    chunks = [{"id": "OggS", "offset": 0, "size": file_size,
               "summary": f"Ogg {codec}, {len(pages)} page(s){rate_txt}",
               "fields": fields, "warnings": [], "payload_base": 0}]
    if ch and ch[2]:
        _, vendor, tags = ch
        fields = []
        if vendor:
            fields.append(_f(None, 0, "vendor", vendor[:200]))
        for k, v in list(tags.items())[:200]:
            fields.append(_f(None, 0, k, str(v)[:200]))
        if len(tags) > 200:
            fields.append(_f(None, 0, "...", f"{len(tags) - 200} more comments"))
        chunks.append({"id": "comments", "offset": 0, "size": 0,
                       "summary": f"{len(tags)} Vorbis comment(s)",
                       "fields": fields, "warnings": []})
    return chunks, []
