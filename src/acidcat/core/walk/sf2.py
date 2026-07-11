"""SoundFont 2 (.sf2) structural walker: the sfbk metadata, the sample-data
and structure chunks, and the named sample list. Sample carving lives in
core/sf2.py; `acidcat convert font.sf2` extracts the samples to WAV."""

import os

from acidcat.core import sf2 as sf2mod
from acidcat.core.walk.base import Unsupported, _PAYLOAD_CAP, _f

_SAMPLE_LIST_CAP = 400          # named samples to list in inspect


def inspect_sf2(filepath):
    """Structural view of an SF2: version + INFO metadata, the sdta/pdta chunk
    sizes, and the named sample list (each with rate, duration, loop)."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        # the header + pdta/shdr table are near the ends; the giant smpl blob in
        # between is not needed to enumerate structure, but parse_sf2 indexes by
        # absolute offset, so read the whole file (bounded by the format's use)
        data = f.read()
    if not sf2mod.is_sf2(data):
        raise Unsupported("not a RIFF/sfbk SoundFont")
    try:
        info = sf2mod.parse_sf2(data)
    except sf2mod.Sf2Error as e:
        raise Unsupported(str(e))

    chunks = []
    meta = [_f(0x08, 4, "form", "sfbk")]
    if info["version"]:
        meta.append(_f(None, 0, "version", info["version"]))
    for key in ("name", "author", "product", "engineer", "software", "comment",
                "copyright", "date", "sound_engine"):
        if info["info"].get(key):
            meta.append(_f(None, 0, key, info["info"][key][:200]))
    meta.append(_f(None, 0, "samples", info["sample_count"]))
    title = info["info"].get("name", "")
    chunks.append({"id": "sfbk", "offset": 0, "size": file_size,
                   "summary": f"SoundFont 2{' ' + info['version'] if info['version'] else ''}"
                              f", {info['sample_count']} samples"
                              + (f" -- '{title}'" if title else ""),
                   "fields": meta, "warnings": [], "payload_base": 0})

    smpl_mb = info["smpl_size"] / (1024 * 1024)
    chunks.append({"id": "smpl", "offset": info["smpl_offset"], "size": info["smpl_size"],
                   "summary": f"sample data, {info['smpl_size']:,} bytes "
                              f"({smpl_mb:.1f} MB of 16-bit PCM)",
                   "fields": [_f(None, 0, "sample_bytes", f"{info['smpl_size']:,}"),
                              _f(None, 0, "sample_frames", f"{info['smpl_size'] // 2:,}")],
                   "warnings": [], "payload_base": info["smpl_offset"]})

    warns = []
    smpl_off = info["smpl_offset"]
    for i, s in enumerate(info["samples"][:_SAMPLE_LIST_CAP]):
        dur = (s["end"] - s["start"]) / s["rate"] if s["rate"] else 0
        looped = "looped" if s["loop_end"] > s["loop_start"] else "one-shot"
        stype = {1: "mono", 2: "right", 4: "left", 8: "linked"}.get(s["type"], f"type {s['type']}")
        # the sample's real byte range in smpl, so the hex pane shows its PCM and
        # `carve --offset` can extract it (16-bit samples -> *2)
        byte_off = smpl_off + s["start"] * 2
        byte_len = (s["end"] - s["start"]) * 2
        chunks.append({"id": f"smp[{i}]", "offset": byte_off, "size": byte_len,
                       "summary": f"{s['name']}  {s['rate']} Hz, {dur:.2f}s, "
                                  f"{stype}, {looped}",
                       "fields": [_f(None, 0, "name", s["name"]),
                                  _f(None, 0, "sample_rate", s["rate"], "Hz"),
                                  _f(None, 0, "duration", f"{dur:.3f} s"),
                                  _f(None, 0, "range", f"{s['start']}..{s['end']}",
                                     "sample indices into smpl"),
                                  _f(None, 0, "loop", f"{s['loop_start']}..{s['loop_end']}"),
                                  _f(None, 0, "root_key", s["pitch"])],
                       "warnings": [], "payload_base": byte_off})
    if info["sample_count"] > _SAMPLE_LIST_CAP:
        warns.append(f"listing the first {_SAMPLE_LIST_CAP} of "
                     f"{info['sample_count']:,} samples")
    return chunks, warns
