"""SoundFont 2 (.sf2) structural walker: the sfbk metadata, the sample-data
and structure chunks, and the named sample list. Sample carving lives in
core/sf2.py; `acidcat convert font.sf2` extracts the samples to WAV."""

import os

from acidcat.core import sf2 as sf2mod
from acidcat.core.walk.base import Unsupported, _PAYLOAD_CAP, _f

_SAMPLE_LIST_CAP = 400          # named samples to list in inspect
_SF2_CAP = 512 * 1024 * 1024    # cap the whole-file read; a forged sfbk must not OOM


def inspect_sf2(filepath):
    """Structural view of an SF2: version + INFO metadata, the sdta/pdta chunk
    sizes, and the named sample list (each with rate, duration, loop)."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        # validate the sfbk magic before slurping, so a huge non-SoundFont is
        # rejected without being read into memory
        if not sf2mod.is_sf2(f.read(12)):
            raise Unsupported("not a RIFF/sfbk SoundFont")
        f.seek(0)
        # parse_sf2 indexes by absolute offset so the whole file is read, but
        # capped: a forged sfbk must not amplify into unbounded memory
        data = f.read(min(file_size, _SF2_CAP))
    try:
        info = sf2mod.parse_sf2(data)
    except sf2mod.Sf2Error as e:
        raise Unsupported(str(e))

    sf3 = info.get("sf3")
    ver_label = "SoundFont 3 (Ogg Vorbis)" if sf3 else "SoundFont 2"
    chunks = []
    meta = [_f(0x08, 4, "form", "sfbk")]
    if info["version"]:
        meta.append(_f(None, 0, "version", info["version"]))
    for key in ("name", "author", "product", "engineer", "software", "comment",
                "copyright", "date", "sound_engine"):
        if info["info"].get(key):
            meta.append(_f(None, 0, key, info["info"][key][:200]))
    meta.append(_f(None, 0, "samples", info["sample_count"]))
    if sf3:
        meta.append(_f(None, 0, "compression", "Ogg Vorbis (SF3)"))
    title = info["info"].get("name", "")
    chunks.append({"id": "sfbk", "offset": 0, "size": file_size,
                   "summary": f"{ver_label}{' ' + info['version'] if info['version'] else ''}"
                              f", {info['sample_count']} samples"
                              + (f" -- '{title}'" if title else ""),
                   "fields": meta, "warnings": [], "payload_base": 0})

    smpl_mb = info["smpl_size"] / (1024 * 1024)
    body_desc = f"{smpl_mb:.1f} MB of Ogg Vorbis streams" if sf3 \
        else f"{smpl_mb:.1f} MB of 16-bit PCM"
    smpl_fields = [_f(None, 0, "sample_bytes", f"{info['smpl_size']:,}")]
    if not sf3:
        smpl_fields.append(_f(None, 0, "sample_frames", f"{info['smpl_size'] // 2:,}"))
    chunks.append({"id": "smpl", "offset": info["smpl_offset"], "size": info["smpl_size"],
                   "summary": f"sample data, {info['smpl_size']:,} bytes ({body_desc})",
                   "fields": smpl_fields,
                   "warnings": [], "payload_base": info["smpl_offset"]})

    warns = []
    if file_size > _SF2_CAP:
        warns.append(f"file exceeds {_SF2_CAP >> 20} MB; parsed the first "
                     f"{_SF2_CAP >> 20} MB (samples near the end may be missing)")
    for i, s in enumerate(info["samples"][:_SAMPLE_LIST_CAP]):
        looped = "looped" if s["loop_end"] > s["loop_start"] else "one-shot"
        stype = {1: "mono", 2: "right", 4: "left", 8: "linked"}.get(s["type"], f"type {s['type']}")
        # the sample's real byte range in smpl, so the hex pane shows its bytes
        # and `carve --offset` extracts it (SF2: 16-bit PCM; SF3: an Ogg stream)
        byte_off, byte_len = s["byte_off"], s["byte_len"]
        if s.get("compressed"):
            summary = (f"{s['name']}  {s['rate']} Hz, {stype}, {looped}, "
                       f"Ogg Vorbis, {byte_len:,} bytes")
            rng = _f(None, 0, "ogg_range", f"0x{byte_off:08x}..0x{byte_off + byte_len:08x}",
                     "byte offsets into smpl")
        else:
            dur = (s["end"] - s["start"]) / s["rate"] if s["rate"] else 0
            summary = f"{s['name']}  {s['rate']} Hz, {dur:.2f}s, {stype}, {looped}"
            rng = _f(None, 0, "range", f"{s['start']}..{s['end']}", "sample indices into smpl")
        chunks.append({"id": f"smp[{i}]", "offset": byte_off, "size": byte_len,
                       "summary": summary,
                       "fields": [_f(None, 0, "name", s["name"]),
                                  _f(None, 0, "sample_rate", s["rate"], "Hz"),
                                  rng,
                                  _f(None, 0, "loop", f"{s['loop_start']}..{s['loop_end']}"),
                                  _f(None, 0, "root_key", s["pitch"])],
                       "warnings": [], "payload_base": byte_off})
    if info["sample_count"] > _SAMPLE_LIST_CAP:
        warns.append(f"listing the first {_SAMPLE_LIST_CAP} of "
                     f"{info['sample_count']:,} samples")
    return chunks, warns
