"""Native Instruments preset structural walker: hsin containers
(Massive/Absynth/Kontakt), NKS .nksf, and the older zlib-XML .ksd.
Container parsing lives in core/ni.py."""

import os

from acidcat.core import ni as nimod
from acidcat.core.walk.base import Unsupported as _Unsupported
from acidcat.core.walk.base import _f

def inspect_ni(filepath, deep=False):
    """Structural view of a Native Instruments preset: the readable metadata.
    Handles the hsin container (Massive .nmsv, Absynth .nabs, modern Kontakt
    .nki) and the older zlib-XML .ksd (Absynth/KORE). With deep (--verbose or
    --frames) it also FastLZ-decompresses the hsin subtree to report the inner
    preset-state container."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(file_size, 16 * 1024 * 1024))
    if nimod.is_ni_ksd(data):
        meta, kind = nimod.parse_ksd(data), "ksd"
    elif nimod.is_ni_nksf(data):
        meta, kind = nimod.parse_nksf(data), "nksf"
    else:
        meta, kind = nimod.parse_hsin(data), "hsin"
    if not meta:
        raise _Unsupported("not a recognized Native Instruments preset")
    order = ["name", "product", "plugin", "author", "vendor", "bank", "comment",
             "description", "device_type", "version", "tempo", "genre", "key"]
    fields = [_f(None, 0, k, str(meta[k])) for k in order if meta.get(k)]
    for k in meta:
        if k not in order:
            fields.append(_f(None, 0, k, str(meta[k])))
    prod = meta.get("product") or meta.get("plugin") or "NI"
    summary = f"{prod} preset '{meta.get('name', '(unnamed)')}'"
    chunks = [{"id": kind, "offset": 0, "size": file_size, "summary": summary,
               "fields": fields, "warnings": [], "payload_base": 0}]
    if deep and kind == "hsin":
        inner = nimod.decompress_subtree(data)
        if inner is not None:
            nested = nimod.is_ni_hsin(inner)
            chunks.append({"id": "payload", "offset": 0, "size": 0,
                           "summary": "FastLZ-compressed preset state",
                           "fields": [_f(None, 0, "decompressed_size",
                                         f"{len(inner):,} bytes"),
                                      _f(None, 0, "inner_container",
                                         "nested hsin (synth parameter state)"
                                         if nested else "opaque")],
                           "warnings": []})
    return chunks, []
