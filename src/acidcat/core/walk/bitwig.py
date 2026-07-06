"""Bitwig BtWg container structural walker.

Routed purely on the `BtWg` magic (core/sniff.py), so it handles every Bitwig
document that uses that container, not just presets and clips: .bwpreset,
.bwclip, .bwproject, .bwscene, .bwdevice, .bwmodule, .bwmodulator,
.bwremotecontrols. The header carries a version pair (e.g. 0003/0004). Reports
the header, metadata, note clips, and with deep mode the device tree and the
embedded-asset zip. Container primitives live in core/bitwig.py.

Two Bitwig types are NOT this container: .bwimpulse is a bare FLAC file (walked
by the flac walker), and .wt is the 'vawt' wavetable format (walked by wt.py).
The ZIP-based .multisample is not yet handled."""

import os
import struct

from acidcat.core import bitwig as bwmod
from acidcat.core.walk.base import _f
from acidcat.util.midi import midi_note_to_name

def _flac_audio_params(raw):
    """(channels, rate, seconds) from a FLAC STREAMINFO, or None."""
    if len(raw) < 42 or raw[:4] != b"fLaC":
        return None
    packed = struct.unpack_from(">Q", raw, 18)[0]  # STREAMINFO@8, packed field@+10
    rate = (packed >> 44) & 0xFFFFF
    ch = ((packed >> 41) & 0x07) + 1
    total = packed & 0xFFFFFFFFF
    return ch, rate, (total / rate if rate else 0)


def _wav_audio_params(raw):
    """(channels, rate, seconds) from a WAV's fmt/data chunks, or None."""
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None
    i, ch, rate, bits, datasz = 12, 0, 0, 0, 0
    while i + 8 <= len(raw):
        cid = raw[i:i + 4]
        sz = struct.unpack_from("<I", raw, i + 4)[0]
        if cid == b"fmt " and i + 24 <= len(raw):
            ch = struct.unpack_from("<H", raw, i + 10)[0]
            rate = struct.unpack_from("<I", raw, i + 12)[0]
            bits = struct.unpack_from("<H", raw, i + 22)[0]
        elif cid == b"data":
            datasz = sz
        i += 8 + sz + (sz & 1)
    if not rate:
        return None
    frame = ch * max(1, bits // 8)
    return ch, rate, (datasz / (rate * frame) if frame else 0)


def _summarize_embedded(raw):
    """One-line format identity of an embedded asset's bytes."""
    if not raw:
        return "unreadable / too large"
    if raw[:4] == b"fLaC":
        p = _flac_audio_params(raw)
        return f"FLAC, {p[0]}ch {p[1]} Hz, {p[2]:.2f} s" if p else "FLAC"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        p = _wav_audio_params(raw)
        return f"WAV, {p[0]}ch {p[1]} Hz, {p[2]:.2f} s" if p else "WAV"
    if raw[:4] == b"OggS":
        return "OGG"
    if raw[:4] == b"BtWg":
        return "Bitwig preset (nested)"
    if raw[:2] == b"PK":
        return "zip"
    return f"{len(raw):,} bytes (opaque)"


def inspect_bitwig(filepath, deep=False):
    """Structural view of a Bitwig BtWg container (.bwpreset/.bwclip): header,
    metadata block, and a note for the embedded-asset zip. With deep (--verbose
    or --frames) it also deconstructs the device/module tree and unzips and
    identifies every embedded asset."""
    file_size = os.path.getsize(filepath)
    # read the whole preset (bounded) so the embedded-asset zip, which can sit
    # past the first few MB, is found. the meta scan is bounded internally.
    with open(filepath, "rb") as f:
        data = f.read(min(file_size, 64 * 1024 * 1024))
    chunks, file_warns = [], []

    ver = bwmod.read_header(data)
    chunks.append({"id": "BtWg", "offset": 0, "size": 14,
                   "summary": f"Bitwig container, format {ver}",
                   "fields": [_f(0x00, 4, "magic", "BtWg"),
                              _f(0x04, 10, "version", ver)],
                   "warnings": [], "payload_base": 0})

    meta = bwmod.parse_meta(data)
    fields = [_f(None, 0, label, meta[key][:200])
              for key, label in bwmod._META_FIELDS if key in meta]
    nums = bwmod.parse_numeric(data)
    if "bpm" in nums:
        fields.append(_f(None, 0, "bpm", f"{nums['bpm']:g}"))
    if "beat_length" in nums:
        beats = nums["beat_length"]
        note = f"{beats / 4:g} bars at 4/4" if beats else ""
        fields.append(_f(None, 0, "beat_length", f"{beats:g} beats", note))
    for label, count in bwmod.parse_references(data).items():
        fields.append(_f(None, 0, label, count))
    if meta.get("device_name"):
        summary = f"{meta['device_name']} ({meta.get('device_category', '?')})"
    elif meta.get("type", "").endswith("note-clip"):
        bpm = nums.get("bpm")
        summary = "note clip" + (f", {bpm:g} bpm" if bpm else "")
    elif meta:
        summary = meta.get("type", "Bitwig data")
    else:
        summary = "no meta block decoded"
        file_warns.append("BtWg meta block not decoded")
    chunks.append({"id": "meta", "offset": 0, "size": 0,
                   "summary": summary, "fields": fields, "warnings": []})

    if meta.get("type", "").endswith("note-clip"):
        notes = bwmod.parse_notes(data)
        pitches = [n["pitch"] for n in notes if n["pitch"] is not None]
        if pitches:
            lo, hi = min(pitches), max(pitches)
            rng = f"{midi_note_to_name(lo)}-{midi_note_to_name(hi)}"
            nfields = [_f(None, 0, "note count", len(notes)),
                       _f(None, 0, "pitch range", rng)]
            if deep:
                _inf = float("inf")

                def _fin(x):  # finite value or 0 (a crafted clip can carry NaN/Inf)
                    return x if isinstance(x, (int, float)) and -_inf < x < _inf else 0
                for n in notes[:500]:
                    nm = (midi_note_to_name(n["pitch"])
                          if n["pitch"] is not None else "?")
                    vel = round(_fin(n["velocity"]) * 127)
                    start, dur = _fin(n["start"]), _fin(n["duration"])
                    nfields.append(_f(None, 0, f"{nm} @ {start:g}",
                                      f"dur {dur:g}, vel {vel}"))
                if len(notes) > 500:
                    nfields.append(_f(None, 0, "...",
                                      f"{len(notes) - 500} more notes"))
            chunks.append({"id": "notes", "offset": 0, "size": 0,
                           "summary": f"{len(notes)} notes, {rng}",
                           "fields": nfields, "warnings": []})

    if deep:
        modules = bwmod.parse_structure(data)
        if modules:
            mfields = [_f(None, 0, f"module {i + 1}", m)
                       for i, m in enumerate(modules)]
            chunks.append({"id": "modules", "offset": 0, "size": 0,
                           "summary": f"{len(modules)} devices/modules in the "
                                      "chain (pre-order)",
                           "fields": mfields, "warnings": []})
        params = bwmod.parse_parameters(data)
        if params:
            def _fmt(v):
                return f"{v:g}"
            pfields = [_f(None, 0, name, _fmt(val)) for name, val in params]
            chunks.append({"id": "parameters", "offset": 0, "size": 0,
                           "summary": f"{len(params)} device parameters "
                                      "(raw internal units)",
                           "fields": pfields, "warnings": []})
        rows = bwmod.flatten_tree(bwmod.parse_tree(data))
        if rows:
            leaves = sum(1 for _, _, leaf in rows if leaf)
            tfields = [_f(None, 0, ("  " * d) + seg, "param" if leaf else "+")
                       for d, seg, leaf in rows]
            chunks.append({"id": "tree", "offset": 0, "size": 0,
                           "summary": f"addressable structure tree "
                                      f"({len(rows)} nodes, {leaves} wired "
                                      f"parameters, from Grid paths)",
                           "fields": tfields, "warnings": []})

    zoff = data.find(b"PK\x03\x04")
    if zoff >= 0:
        afields, asum = [], ("embedded asset zip (deflate); unzip for the "
                             "referenced samples/impulses")
        if deep:
            assets = bwmod.list_assets(data)
            afields = [_f(None, 0, name, f"{size:,} bytes, "
                          f"{_summarize_embedded(raw)}")
                       for name, size, raw in assets]
            asum = f"{len(assets)} embedded file(s) (deflate zip)"
        chunks.append({"id": "assets", "offset": zoff,
                       "size": file_size - zoff, "summary": asum,
                       "fields": afields, "warnings": []})
    return chunks, file_warns
