"""
acidcat inspect -- readelf-style structural dump for audio files.

Walks the container chunk by chunk and prints a structural table, a
decoded field breakdown per known chunk (with byte offsets), and any
spec violations it noticed along the way. `--hex` adds the raw bytes
next to each decoded field. `--frames` adds a per-element deep dump
(every MPEG frame for MP3, every event for MIDI). `--color` syntax-
highlights the table (auto/always/never, respects NO_COLOR). `-f json`
emits the same structure for machines.

Supports WAV/RIFF, RF64, AIFF/AIFC, Standard MIDI Files, Xfer Serum
presets, MP3 (ID3v2 + MPEG frames + Xing/LAME), and FLAC.
"""

import json
import os
import struct
import sys

from acidcat.core import mp3 as mp3mod
from acidcat.core import bitwig as bwmod
from acidcat.core import vital as vitalmod
from acidcat.core import ncw as ncwmod
from acidcat.core import mp4 as mp4mod
from acidcat.core import ni as nimod
from acidcat.core import ogg as oggmod
from acidcat.core import anomalies as anomaliesmod
from acidcat.core import lsb as lsbmod
from acidcat.core.walk.base import (
    _FRAME_LISTING_CAP, _ID3_READ_CAP, _PAYLOAD_CAP,
    _bu16, _bu32, _dtext, _f,
)
from acidcat.core.walk.aiff import inspect_aiff
from acidcat.core.walk.flac import inspect_flac
from acidcat.core.walk.midi import inspect_midi
from acidcat.core.walk.mp3 import inspect_mp3
from acidcat.core.walk.ogg import inspect_ogg
from acidcat.core.walk.rf64 import inspect_rf64
from acidcat.core.walk.wav import inspect_wav
from acidcat.util.midi import midi_note_to_name

# --full emits raw region bytes for chunks that have decoded fields; cap the
# hex so a huge header (embedded art) cannot bloat the dump without bound.
_FULL_RAW_CAP = 8192


def register(subparsers):
    p = subparsers.add_parser(
        "inspect",
        help="readelf-style structural dump of a WAV, AIFF, MIDI, MP3, or FLAC file.",
    )
    p.add_argument("targets", nargs="+", metavar="target",
                   help="One or more WAV, RF64, AIFF, MIDI, Serum, MP3, or FLAC "
                        "files. With more than one, each is printed under a "
                        "'File:' banner; JSON output becomes NDJSON (one record "
                        "per line).")
    p.add_argument("--hex", action="store_true", dest="show_hex",
                   help="Show raw bytes next to each decoded field.")
    p.add_argument("-f", "--format", default="table", choices=["table", "json"],
                   help="Output format (default: table).")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Chunk table only, no per-chunk field detail.")
    p.add_argument("--pretty", action="store_true",
                   help="Human-friendly view of the decoded tags and metadata "
                        "(no byte offsets), ideal for presets and tagged files.")
    p.add_argument("-F", "--frames", action="store_true",
                   help="Per-element deep dump: every MPEG frame (MP3) or "
                        "MIDI event. No effect on formats without per-element "
                        "structure (WAV, AIFF, FLAC).")
    p.add_argument("--only", metavar="IDS",
                   help="Show only these chunk ids (comma-separated, e.g. "
                        "'fmt,bext'). Case-insensitive, matched against the "
                        "displayed id. Compose with --hex to hexdump one chunk.")
    p.add_argument("--exclude", metavar="IDS",
                   help="Hide these chunk ids (comma-separated). Applied after "
                        "--only.")
    p.add_argument("--full", action="store_true",
                   help="Emit a self-contained structural dump (implies -f json): "
                        "each chunk with its raw region bytes and every field's "
                        "absolute byte offset, so build_explorer.py can render a "
                        "standalone HTML explorer for the file.")
    p.add_argument("--anomalies", action="store_true",
                   help="Forensic scan: flag trailing data past the container, "
                        "appended-format magic (polyglots), structural size "
                        "mismatches, and control bytes smuggled into text fields.")
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                   help="Colorize table output: auto (default, when stdout is a "
                        "TTY), always, or never. Respects the NO_COLOR env var.")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


# ── serum walk ─────────────────────────────────────────────────────


def inspect_serum(filepath):
    """Structural view of an Xfer Serum preset: XferJson magic, the
    JSON metadata block, then opaque wavetable/modulation data."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        raw = f.read(min(file_size, 4 * 1024 * 1024))
    chunks = []
    file_warns = []

    chunks.append({"id": "magc", "offset": 0, "size": 8,
                   "summary": "XferJson signature",
                   "fields": [_f(0x00, 8, "magic", "XferJson")],
                   "warnings": [], "payload_base": 0})

    json_start = raw.find(b"{")
    if json_start < 0:
        file_warns.append("no JSON block after the magic")
        return chunks, file_warns

    text = raw[json_start:].decode("utf-8", errors="replace")
    # RecursionError: the json scanner recurses per nesting level, so a
    # forged preset with thousands of nested objects blows the stack.
    try:
        parsed, end = json.JSONDecoder().raw_decode(text)
    except (ValueError, RecursionError) as e:
        file_warns.append(f"JSON block does not parse: {e.__class__.__name__}: {e}")
        return chunks, file_warns

    fields = []
    for key in ("fileType", "presetName", "presetAuthor",
                "presetDescription", "product", "productVersion",
                "tags", "vendor", "version"):
        if key in parsed:
            val = parsed[key]
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            fields.append(_f(None, 0, key, str(val)[:80]))
    name = parsed.get("presetName") or "unnamed"
    # raw_decode's end is a CHARACTER offset into the decoded text; the
    # blob boundary is a BYTE offset, so re-encode the parsed region to
    # measure it. exact for valid UTF-8 (which valid JSON is); off only
    # when the JSON region itself held invalid bytes, where any offset
    # is best-effort.
    end_bytes = len(text[:end].encode("utf-8"))
    chunks.append({"id": "json", "offset": json_start, "size": end_bytes,
                   "summary": f"'{name}' metadata, {len(parsed)} keys",
                   "fields": fields, "warnings": []})

    blob_off = json_start + end_bytes
    chunks.append({"id": "blob", "offset": blob_off,
                   "size": file_size - blob_off,
                   "summary": f"wavetable/modulation data, "
                              f"{file_size - blob_off:,} bytes (opaque)",
                   "fields": [], "warnings": []})
    return chunks, file_warns


# ── bitwig walk ────────────────────────────────────────────────────


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


# ── vital walk ─────────────────────────────────────────────────────


def inspect_vital(filepath, deep=False):
    """Structural view of a Vital preset (bare JSON): the top-level metadata,
    and with deep (--verbose or --frames) the full synth structure, active
    oscillators + wavetables, LFO inventory, effects chain, and the modulation
    matrix."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(file_size, 32 * 1024 * 1024))
    # a fast bytes search for the marker before the full JSON parse: an
    # arbitrary large JSON file that merely starts with '{' is rejected without
    # paying for json.loads. the substring may sit anywhere (some presets emit
    # the big 'settings' object before synth_version).
    if b'"synth_version"' not in data:
        raise _Unsupported("not a Vital preset (no synth_version marker)")
    obj = vitalmod.parse_vital(data)
    if obj is None:
        raise _Unsupported("not a Vital preset (JSON did not parse or lacks "
                           "the synth_version key)")
    fields = []
    for k in vitalmod.META_KEYS:
        v = obj.get(k)
        if v is not None and not isinstance(v, (dict, list)):
            fields.append(_f(None, 0, k, str(v)[:200]))
    settings = obj.get("settings")
    nkeys = len(settings) if isinstance(settings, dict) else 0
    name = obj.get("preset_name") or "unnamed"
    chunks = [{"id": "vital", "offset": 0, "size": file_size,
               "summary": f"'{name}' by {obj.get('author', '?')}, "
                          f"{nkeys} settings keys",
               "fields": fields, "warnings": []}]
    if deep:
        st = vitalmod.deep_structure(obj)
        engine = []
        if st.get("oscillators"):
            wt = ", ".join(st["wavetables"]) if st.get("wavetables") else ""
            engine.append(_f(None, 0, "oscillators",
                             ", ".join(st["oscillators"]), wt))
        if st.get("lfos"):
            engine.append(_f(None, 0, "lfos",
                             f"{len(st['lfos'])}: " + ", ".join(st["lfos"])))
        if st.get("effects"):
            engine.append(_f(None, 0, "effects chain",
                             " > ".join(st["effects"])))
        if engine:
            chunks.append({"id": "engine", "offset": 0, "size": 0,
                           "summary": "active synth structure",
                           "fields": engine, "warnings": []})
        mods = st.get("modulations") or []
        if mods:
            mfields = []
            for src, dst, amt in mods:
                note = f"amount {amt:g}" if isinstance(amt, (int, float)) else ""
                mfields.append(_f(None, 0, src, f"-> {dst}", note))
            chunks.append({"id": "modulation", "offset": 0, "size": 0,
                           "summary": f"{len(mods)} wired modulations "
                                      "(source -> destination)",
                           "fields": mfields, "warnings": []})
    return chunks, []


# ── ncw walk ───────────────────────────────────────────────────────


def inspect_ncw(filepath):
    """Structural view of an NI Compressed Wave (.ncw) file: the audio
    parameters from the header. The compressed blocks are opaque."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        head = f.read(64)
    hdr = ncwmod.parse_header(head)
    if hdr is None:
        raise _Unsupported("not a valid NCW header")
    dur = hdr["num_samples"] / hdr["sample_rate"] if hdr["sample_rate"] else 0
    fields = [
        _f(0x08, 2, "channels", hdr["channels"]),
        _f(0x0A, 2, "bits_per_sample", hdr["bits"]),
        _f(0x0C, 4, "sample_rate", hdr["sample_rate"], "Hz"),
        _f(0x10, 4, "num_samples", hdr["num_samples"],
           f"{dur:.3f} s" if dur else ""),
    ]
    chunks = [{"id": "NCW", "offset": 0, "size": file_size,
               "summary": f"NI Compressed Wave, {hdr['bits']}-bit "
                          f"{hdr['channels']}ch {hdr['sample_rate']} Hz, "
                          f"{dur:.3f} s (compressed audio opaque)",
               "fields": fields, "warnings": [], "payload_base": 0}]
    return chunks, []


# ── mp4 walk ───────────────────────────────────────────────────────


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
            desc += f", {ch}ch {rate} Hz"
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


# ── native instruments walk ────────────────────────────────────────


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


# ── rendering ──────────────────────────────────────────────────────


def _hex_bytes(filepath, offset, length, cap=8):
    with open(filepath, "rb") as f:
        f.seek(offset)
        raw = f.read(min(length, cap))
    s = raw.hex(" ")
    return s + " .." if length > cap else s


# ── color ──────────────────────────────────────────────────────────
# small, meaningful palette: structure (cyan), value (green), positional
# metadata (dim), warning (red). codes are zero-width, so callers pad to
# the column width first and paint the padded string.

_ANSI = {
    # bright-black (a real palette slot the terminal theme defines) rather
    # than faint (\033[2m): terminals render faint by blending the fg toward
    # the background, which turns muddy on any non-black background. 90 stays
    # legible against whatever background the user's theme actually uses.
    "dim": "\033[90m",
    "id": "\033[1;36m",     # bold cyan: chunk ids, format label, anchors
    "val": "\033[32m",      # green: decoded field values
    "warn": "\033[1;31m",   # bold red: warnings
}
_RESET = "\033[0m"


def _color_enabled(args):
    # explicit always/never win; NO_COLOR governs auto only.
    mode = getattr(args, "color", "auto")
    if mode == "never":
        return False
    if mode == "always":
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


class _Paint:
    def __init__(self, on):
        self.on = on

    def __call__(self, role, text):
        text = str(text)
        return f"{_ANSI[role]}{text}{_RESET}" if self.on else text


def _render_rows(rows, paint):
    """Print a per-element listing as a compact dynamic-column table."""
    if not rows:
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    header = "    " + "  ".join(f"{c:<{widths[c]}}" for c in cols)
    print(paint("dim", header))
    for r in rows:
        print("    " + "  ".join(f"{str(r.get(c, '')):<{widths[c]}}" for c in cols))


def _human_size(n):
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or unit == "TB":
            return f"{int(x)} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024


def _render_pretty(filepath, fmt_label, chunks, file_warns, args):
    """A clean, human-friendly view of the decoded tags/metadata: section per
    chunk, aligned key/value, no byte offsets. Made for presets and tagged
    files (Bitwig, Vital, Serum, MP4 tags, WAV/FLAC/MP3 metadata)."""
    p = _Paint(_color_enabled(args))
    size = os.path.getsize(filepath)
    print(p("id", os.path.basename(filepath)))
    print(p("dim", f"{fmt_label}, {_human_size(size)}"))
    for c in chunks:
        fields = [f for f in c["fields"]
                  if f["value"] not in (None, "") and str(f["value"]).strip()]
        if not fields:
            continue
        print()
        head = c["id"].strip()
        meta = f"  {p('dim', c['summary'])}" if c.get("summary") else ""
        print(p("id", head) + meta)
        w = max(len(f["name"]) for f in fields)
        for f in fields:
            key = p("dim", f"{f['name']:<{w}}")
            note = f"  {p('dim', '(' + str(f['note']) + ')')}" if f["note"] else ""
            print(f"  {key}  {p('val', f['value'])}{note}")
    all_warns = list(file_warns) + [w for c in chunks for w in c["warnings"]]
    if all_warns:
        print()
        print(p("warn", "warnings:"))
        for w in all_warns:
            print(p("warn", f"  ! {w}"))
    return 0


def _render_anomalies(findings, args):
    """Print the forensic findings from `--anomalies` under the main dump."""
    p = _Paint(_color_enabled(args))
    role = {"alert": "warn", "warn": "warn", "notice": "dim"}
    print()
    if not findings:
        print(p("dim", "  anomalies: none"))
        return
    print(p("id", f"  anomalies ({len(findings)}):"))
    for f in findings:
        sev = f["severity"]
        tag = p(role.get(sev, "dim"), f"[{sev:6}]")
        off = p("dim", f"0x{f['offset']:08x}")
        print(f"    {tag} {off}  {f['rule']:16} {f['message']}")


def _render_table(filepath, fmt_label, chunks, file_warns, args, total=None):
    file_size = os.path.getsize(filepath)
    p = _Paint(_color_enabled(args))
    if total is not None and total != len(chunks):
        count = f"showing {len(chunks)} of {total} chunks"
    else:
        count = f"{len(chunks)} chunks"
    print(f"{os.path.basename(filepath)}: {p('id', fmt_label)}, {file_size:,} bytes, "
          f"{count}")
    print()
    print(p("dim", f"  {'idx':<5} {'id':<5} {'offset':<11} {'size':<11} summary"))
    for i, c in enumerate(chunks):
        idx = p("dim", f"[{c.get('_idx', i):>2}]")
        cid = p("id", f"{c['id']:<5}")
        off = p("dim", f"0x{c['offset']:08x}")
        print(f"  {idx}  {cid} {off}  {c['size']:<11,} {c['summary']}")

    if not args.quiet:
        for c in chunks:
            if not c["fields"] and not c.get("rows"):
                continue
            print()
            hdr_id = p("id", c["id"].strip())
            hdr_meta = p("dim", f"@ 0x{c['offset']:08x} ({c['size']} bytes)")
            print(f"{hdr_id} {hdr_meta}")
            for fl in c["fields"]:
                note = p("dim", f"  {fl['note']}") if fl["note"] else ""
                # derived stats (midi track facts) carry no byte offset
                off_col = f"+0x{fl['off']:04x}" if fl["off"] is not None else "      "
                off_col = p("dim", off_col)
                val = p("val", f"{fl['value']!s:<14}")
                if args.show_hex and fl["off"] is not None:
                    # field offsets are measured from the chunk's payload base.
                    # RIFF/AIFF/RF64/MThd all have an 8-byte id+size header, so
                    # that is the default; formats with a different header (FLAC
                    # blocks: 4 bytes) or whose fields are already absolute (MP3
                    # ID3 tags, MPEG frames, the FLAC/Serum magic) set their own.
                    base = c.get("payload_base")
                    if base is None:
                        base = c["offset"] + 8
                    hx = _hex_bytes(filepath, base + fl["off"], fl["len"])
                    print(f"  {off_col}  {p('dim', f'{hx:<26}')} "
                          f"{fl['name']:<22} {val}{note}")
                else:
                    print(f"  {off_col}  {fl['name']:<22} {val}{note}")
            if c.get("rows"):
                _render_rows(c["rows"], p)

    if getattr(args, "frames", False) and not any(c.get("rows") for c in chunks):
        print()
        print(p("dim", f"  (--frames: {fmt_label} has no per-element structure to dump)"))

    all_warns = list(file_warns)
    all_warns += [f"{c['id'].strip()}: {w}" for c in chunks for w in c["warnings"]]
    if all_warns:
        print()
        print(p("warn", "warnings:"))
        for w in all_warns:
            print(p("warn", f"  ! {w}"))
    return 0


def _id3_tagged_mp3(filepath):
    """A file starting with an ID3v2 tag is MP3 only if the tag does not
    merely wrap a different known container. Some tools prepend an ID3 tag
    to a WAV/AIFF/FLAC; that is not an MP3 and must not be claimed as one."""
    hdr = mp3mod.read_id3v2(filepath)
    if not hdr:
        return True  # "ID3" magic but an unreadable header; treat as an MP3 attempt
    with open(filepath, "rb") as f:
        f.seek(hdr["total"])
        nxt = f.read(4)
    return nxt not in (b"RIFF", b"RF64", b"FORM", b"fLaC", b"MThd")


def _parse_id_list(val):
    """A comma-separated chunk-id list into a normalized set (or None)."""
    if not val:
        return None
    return {x.strip().casefold() for x in val.split(",") if x.strip()}


def _select_chunks(chunks, only, exclude):
    """Filter chunks by --only/--exclude, tagging each survivor with its
    original index so the table keeps truthful [n] and file positions."""
    out = []
    for i, c in enumerate(chunks):
        cid = c["id"].strip().casefold()
        if only is not None and cid not in only:
            continue
        if exclude is not None and cid in exclude:
            continue
        c = dict(c)
        c["_idx"] = i
        out.append(c)
    return out


class _Unsupported(Exception):
    """A file inspect cannot structurally decode; message is user-facing."""


def _walk_file(filepath, deep):
    """Sniff the magic and dispatch to the format walker.

    Returns (fmt_label, chunks, file_warns); raises _Unsupported for a
    file inspect does not decode."""
    with open(filepath, "rb") as f:
        magic = f.read(16)
    if len(magic) >= 12 and magic[:4] == b"RIFF" and magic[8:12] == b"WAVE":
        return ("RIFF/WAVE", *inspect_wav(filepath))
    if len(magic) >= 12 and magic[:4] == b"FORM" and magic[8:12] in (b"AIFF", b"AIFC"):
        form_type = magic[8:12].decode("ascii")
        return (f"IFF/{form_type}", *inspect_aiff(filepath, form_type))
    if len(magic) >= 14 and magic[:4] == b"MThd":
        return ("Standard MIDI File", *inspect_midi(filepath, deep=deep))
    if len(magic) >= 12 and magic[:4] == b"RF64" and magic[8:12] == b"WAVE":
        return ("RF64/WAVE", *inspect_rf64(filepath))
    if magic[:8] == b"XferJson":
        return ("Xfer Serum preset", *inspect_serum(filepath))
    if magic[:4] == b"BtWg":
        return ("Bitwig preset", *inspect_bitwig(filepath, deep=deep))
    if magic[:4] == ncwmod.MAGIC:
        return ("NI Compressed Wave", *inspect_ncw(filepath))
    if magic[:1] == b"{":
        return ("Vital preset", *inspect_vital(filepath, deep=deep))
    if magic[4:8] == b"ftyp":
        return ("MP4/M4A", *inspect_mp4(filepath))
    if magic[12:16] == b"hsin" or magic[:4] == b"-in-" \
            or (magic[:4] == b"RIFF" and magic[8:12] == b"NIKS"):
        return ("Native Instruments preset", *inspect_ni(filepath, deep=deep))
    if magic[:4] == b"fLaC":
        return ("FLAC", *inspect_flac(filepath))
    if magic[:4] == b"OggS":
        return ("Ogg", *inspect_ogg(filepath))
    if magic[:3] == b"ID3" and not _id3_tagged_mp3(filepath):
        raise _Unsupported("ID3 tag wraps a non-MP3 container; not supported")
    if magic[:3] == b"ID3" or (len(magic) >= 4
                               and mp3mod.decode_frame_header(magic[:4]) is not None):
        return ("MP3/MPEG audio", *inspect_mp3(filepath, deep=deep))
    raise _Unsupported("not a WAV, RF64, AIFF, MIDI, Serum, Bitwig, Vital, NCW, "
                       "MP4/M4A, MP3, or FLAC")


def _full_chunk(chunk, filepath):
    """Enrich a chunk for --full into a self-contained record: its absolute
    payload base, the raw region bytes as hex (capped), and every field's
    absolute byte offset. build_explorer.py needs nothing but this JSON."""
    c = {k: v for k, v in chunk.items() if k != "_idx"}
    pb = chunk.get("payload_base", chunk["offset"] + 8)
    c["payload_base"] = pb
    fields = []
    for f in chunk["fields"]:
        f2 = dict(f)
        # absolute file offset, so a field maps to raw[abs - offset]
        f2["abs"] = pb + f["off"] if f["off"] is not None else None
        fields.append(f2)
    c["fields"] = fields
    # only carry raw bytes for chunks that actually have positioned fields;
    # audio-data regions are huge and have nothing to highlight.
    if any(f["off"] is not None for f in chunk["fields"]):
        n = min(chunk["size"], _FULL_RAW_CAP)
        with open(filepath, "rb") as fh:
            fh.seek(chunk["offset"])
            raw = fh.read(n)
        c["raw"] = raw.hex()
        c["raw_base"] = chunk["offset"]
        if chunk["size"] > _FULL_RAW_CAP:
            c["raw_truncated"] = chunk["size"] - _FULL_RAW_CAP
    return c


def run(args):
    # accept either the multi-file `targets` or the legacy single `target`
    targets = getattr(args, "targets", None)
    if not targets:
        one = getattr(args, "target", None)
        targets = [one] if one else []
    if not targets:
        print("acidcat inspect: no target file given", file=sys.stderr)
        return 1

    deep = getattr(args, "frames", False) or getattr(args, "verbose", False)
    full = getattr(args, "full", False)
    as_json = args.format == "json" or full  # --full is a JSON dump
    multi = len(targets) > 1
    only = _parse_id_list(getattr(args, "only", None))
    exclude = _parse_id_list(getattr(args, "exclude", None))
    exit_code = 0

    try:
        for filepath in targets:
            if not os.path.isfile(filepath):
                print(f"acidcat inspect: {filepath}: No such file", file=sys.stderr)
                exit_code = 1
                continue
            try:
                fmt_label, chunks, file_warns = _walk_file(filepath, deep)
            except _Unsupported as e:
                print(f"acidcat inspect: {filepath}: {e}", file=sys.stderr)
                exit_code = 1
                continue
            except Exception as e:  # a walker bug must not sink the whole run
                print(f"acidcat inspect: {filepath}: {e.__class__.__name__}: {e}",
                      file=sys.stderr)
                exit_code = 1
                continue

            total = len(chunks)
            shown = _select_chunks(chunks, only, exclude)
            findings = (anomaliesmod.scan(filepath, fmt_label, chunks, file_warns)
                        if getattr(args, "anomalies", False) else None)
            lsb_info = None
            if getattr(args, "anomalies", False) or full:
                try:
                    lsb_info = lsbmod.analyze(filepath, fmt_label, chunks)
                except Exception:
                    lsb_info = None
            if findings is not None and lsb_info and lsb_info["uniform_high"]:
                findings.append({
                    "severity": "notice", "offset": lsb_info["region"][0],
                    "rule": "lsb_entropy",
                    "message": f"uniformly high LSB entropy (min {lsb_info['min']}, "
                               f"mean {lsb_info['mean']}): consistent with LSB "
                               f"steganography, but also with a noisy/dithered/"
                               f"high-bit-depth recording"})
                findings.sort(key=lambda x: (
                    -{"alert": 3, "warn": 2, "notice": 1}.get(x["severity"], 0),
                    x["offset"]))

            if as_json:
                # NDJSON: one compact record per file per line, so the stream
                # pipes cleanly into jq -c and other line-oriented tools.
                if full:
                    out_chunks = [_full_chunk(c, filepath) for c in shown]
                else:
                    out_chunks = [{k: v for k, v in c.items() if k != "_idx"}
                                  for c in shown]
                sys.stdout.write(json.dumps({
                    "file": filepath,
                    "format": fmt_label,
                    "size": os.path.getsize(filepath),
                    "full": full,
                    "chunks": out_chunks,
                    "warnings": file_warns,
                    **({"anomalies": findings} if findings is not None else {}),
                    **({"lsb": lsb_info} if lsb_info else {}),
                }) + "\n")
            else:
                pretty = getattr(args, "pretty", False)
                if multi and not pretty:
                    print(f"\nFile: {filepath}")  # readelf-style per-file banner
                elif multi:
                    print()  # separate files; --pretty prints its own name header
                if pretty:
                    _render_pretty(filepath, fmt_label, shown, file_warns, args)
                else:
                    _render_table(filepath, fmt_label, shown, file_warns, args, total)
                if findings is not None:
                    _render_anomalies(findings, args)
    except BrokenPipeError:
        # a downstream pager or `head` closed the pipe: exit quietly the way
        # cat and grep do, without a traceback.
        try:
            sys.stdout.close()
        except Exception:
            pass
        return exit_code

    return exit_code
