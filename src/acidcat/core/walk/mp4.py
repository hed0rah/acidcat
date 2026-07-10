"""ISO-BMFF MP4/M4A structural walker: decoded iTunes metadata and the
box tree, with the stsd sample entries and their codec-config boxes
(esds/alac/dOps) broken out. Box primitives live in core/mp4.py."""

import os
import struct

from acidcat.core import mp4 as mp4mod
from acidcat.core.walk.base import _f

_CODEC_NAMES = {"mp4a": "AAC", "alac": "Apple Lossless", "Opus": "Opus",
                "fLaC": "FLAC", "ac-3": "AC-3", "ec-3": "E-AC-3"}

# sample-entry 4ccs whose payload follows the AudioSampleEntry layout; video
# and text entries share the box shape but not the field offsets.
_AUDIO_ENTRY_4CC = {b"mp4a", b"alac", b"Opus", b"fLaC", b"ac-3", b"ec-3",
                    b"samr", b"sawb", b".mp3", b"lpcm", b"sowt", b"twos",
                    b"in24", b"in32", b"fl32", b"fl64", b"ulaw", b"alaw"}


def _aac_profile(moov_data):
    """The exact codec name from the first audio entry's esds, when it is an
    MPEG-4 audio stream: 'AAC LC', 'SBR (HE-AAC)', ... or None."""
    for e in mp4mod.sample_entries(moov_data):
        for btype, boff, bhdr, bsize in e["children"]:
            if btype != b"esds":
                continue
            info = mp4mod.parse_esds(moov_data[boff + bhdr + 4:boff + bsize])
            if not info:
                return None
            oti = info.get("object_type_indication")
            if oti == 0x40 and info.get("dsi"):
                asc = mp4mod.parse_audio_specific_config(info["dsi"])
                if asc:
                    return mp4mod._AAC_OBJECT_TYPES.get(
                        asc["object_type"], f"MPEG-4 audio type {asc['object_type']}")
            return mp4mod._ESDS_OTI.get(oti)
    return None


def _entry_chunks(data, stsd_box):
    """Chunk dicts for the sample entries under one stsd box and their
    codec-config children, indented to sit under it in the tree."""
    out = []
    lo, hi = stsd_box["offset"], stsd_box["offset"] + stsd_box["size"]
    for e in mp4mod.sample_entries(data):
        if not (lo < e["offset"] < hi):
            continue
        codec = e["codec"].decode("latin-1", errors="replace")
        indent = ". " * e["depth"]
        fields = []
        is_audio = e["codec"] in _AUDIO_ENTRY_4CC
        ver = e.get("version")
        summary = f"{indent}{codec}  sample entry"
        if is_audio and ver is not None:
            fields.append(_f(0x08, 2, "entry_version", ver,
                             "QuickTime v2 layout" if ver == 2 else
                             ("QuickTime v1 (adds 16 compression bytes)"
                              if ver == 1 else "")))
            if ver == 2:
                fields.append(_f(0x20, 8, "sample_rate", e.get("sample_rate"),
                                 "Hz, float64"))
                fields.append(_f(0x28, 4, "channels", e.get("channels")))
                fields.append(_f(0x30, 4, "sample_size", e.get("sample_size"),
                                 "bits"))
            else:
                fields.append(_f(0x10, 2, "channels", e.get("channels")))
                fields.append(_f(0x12, 2, "sample_size", e.get("sample_size"),
                                 "bits"))
                fields.append(_f(0x18, 4, "sample_rate", e.get("sample_rate"),
                                 "Hz, 16.16 fixed"))
            if e.get("channels") is not None:
                summary = (f"{indent}{codec}  {e['channels']}ch "
                           f"{e.get('sample_rate')} Hz, "
                           f"{e.get('sample_size')}-bit")
        out.append({"id": codec[:8], "offset": e["offset"], "size": e["size"],
                    "summary": summary, "fields": fields, "warnings": [],
                    "payload_base": e["offset"] + e["hdr"]})
        for btype, boff, bhdr, bsize in e["children"]:
            out.append(_config_chunk(data, btype, boff, bhdr, bsize,
                                     e["depth"] + 1))
    return out


def _config_chunk(data, btype, boff, bhdr, bsize, depth):
    """A chunk dict for one codec-config box inside a sample entry."""
    t = btype.decode("latin-1", errors="replace")
    indent = ". " * depth
    entry = {"id": t[:8], "offset": boff, "size": bsize,
             "summary": indent + t, "fields": [], "warnings": [],
             "payload_base": boff + bhdr}
    payload = data[boff + bhdr:boff + bsize]
    flds = entry["fields"]
    if btype == b"esds":
        info = mp4mod.parse_esds(payload[4:]) or {}
        oti = info.get("object_type_indication")
        if oti is not None:
            flds.append(_f(None, 0, "object_type", f"0x{oti:02x}",
                           mp4mod._ESDS_OTI.get(oti, "unknown")))
        if info.get("max_bitrate"):
            flds.append(_f(None, 0, "max_bitrate",
                           f"{info['max_bitrate'] / 1000:.0f} kbps"))
        if info.get("avg_bitrate"):
            flds.append(_f(None, 0, "avg_bitrate",
                           f"{info['avg_bitrate'] / 1000:.0f} kbps"))
        asc = (mp4mod.parse_audio_specific_config(info["dsi"])
               if oti == 0x40 and info.get("dsi") else None)
        if asc:
            name = mp4mod._AAC_OBJECT_TYPES.get(
                asc["object_type"], f"type {asc['object_type']}")
            flds.append(_f(None, 0, "aac_object_type", asc["object_type"], name))
            if asc.get("sample_rate"):
                flds.append(_f(None, 0, "asc_sample_rate", asc["sample_rate"],
                               "Hz"))
            flds.append(_f(None, 0, "channel_config", asc["channels"],
                           "0 = in-band PCE" if asc["channels"] == 0 else
                           ("8 channels (7.1)" if asc["channels"] == 7 else "")))
            if asc.get("ext_sample_rate"):
                flds.append(_f(None, 0, "sbr_output_rate",
                               asc["ext_sample_rate"], "Hz after SBR"))
            entry["summary"] += f"  {name}"
        elif oti is not None:
            entry["summary"] += f"  {mp4mod._ESDS_OTI.get(oti, hex(oti))}"
    elif btype == b"alac" and len(payload) >= 28:
        c = mp4mod.parse_alac_cookie(payload[4:])
        if c:
            flds.append(_f(0x04, 4, "frame_length", c["frame_length"],
                           "samples per packet"))
            flds.append(_f(0x09, 1, "bit_depth", c["bit_depth"]))
            flds.append(_f(0x0A, 3, "rice_params",
                           f"pb {c['pb']}, mb {c['mb']}, kb {c['kb']}",
                           "entropy-coder tuning"))
            flds.append(_f(0x0D, 1, "channels", c["channels"]))
            flds.append(_f(0x10, 4, "max_frame_bytes", f"{c['max_frame_bytes']:,}"))
            flds.append(_f(0x14, 4, "avg_bitrate",
                           f"{c['avg_bitrate'] / 1000:.0f} kbps"
                           if c["avg_bitrate"] else "0 (unset)"))
            flds.append(_f(0x18, 4, "sample_rate", c["sample_rate"], "Hz"))
            entry["summary"] += (f"  {c['bit_depth']}-bit magic cookie")
    elif btype == b"dOps":
        c = mp4mod.parse_dops(payload)
        if c:
            flds.append(_f(0x00, 1, "version", c["version"]))
            flds.append(_f(0x01, 1, "channels", c["channels"]))
            flds.append(_f(0x02, 2, "pre_skip", c["pre_skip"],
                           "priming samples at 48 kHz"))
            flds.append(_f(0x04, 4, "input_sample_rate",
                           c["input_sample_rate"], "informational"))
            flds.append(_f(0x08, 2, "output_gain",
                           f"{c['output_gain_db']:+.2f} dB", "Q7.8 fixed"))
            flds.append(_f(0x0A, 1, "mapping_family", c["mapping_family"],
                           "RTP order" if c["mapping_family"] == 0 else
                           ("Vorbis order" if c["mapping_family"] == 1 else "")))
            entry["summary"] += "  Opus config (big-endian, unlike OpusHead)"
    elif btype == b"frma" and len(payload) >= 4:
        orig = payload[:4].decode("latin-1", errors="replace")
        flds.append(_f(0x00, 4, "original_format", orig))
        entry["summary"] += f"  original format '{orig}'"
    elif btype == b"btrt" and len(payload) >= 12:
        buf, maxr, avgr = struct.unpack_from(">III", payload, 0)
        flds.append(_f(0x00, 4, "buffer_size", f"{buf:,}", "bytes"))
        flds.append(_f(0x04, 4, "max_bitrate", f"{maxr / 1000:.0f} kbps"))
        flds.append(_f(0x08, 4, "avg_bitrate", f"{avgr / 1000:.0f} kbps"))
    elif btype == b"wave":
        entry["summary"] += "  QuickTime codec-config wrapper"
    else:
        entry["summary"] += f"  codec configuration, {max(bsize - bhdr, 0):,} bytes"
    return entry


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
        desc = _CODEC_NAMES.get(codec, codec)
        # the esds names the exact profile (AAC LC vs HE-AAC ...); use it
        # when present -- works even when moov sits at EOF
        prof = _aac_profile(moov_data)
        if prof:
            desc = prof
        if ch:
            desc += f", {ch}ch"
            if rate:
                desc += f" {rate} Hz"
        mfields.append(_f(None, 0, "codec", desc))
    if dur_s:
        mfields.append(_f(None, 0, "duration", f"{dur_s:.3f} s"))
    fixed = ("title", "artist", "album_artist", "album", "year", "genre",
             "bpm", "composer", "encoder", "comment", "track", "disc",
             "cover_art", "compilation")
    for label in fixed:
        if label in meta:
            mfields.append(_f(None, 0, label, str(meta[label])[:200]))
    # freeform ---- atoms (Serato / MusicBrainz / iTunes normalization keys)
    extras = [k for k in meta if k not in fixed]
    for k in extras[:24]:
        mfields.append(_f(None, 0, k[:60], str(meta[k])[:200], "freeform"))
    if len(extras) > 24:
        mfields.append(_f(None, 0, "...", f"{len(extras) - 24} more freeform tags"))
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
        if b["type"] == b"stsd" and not b["truncated"] \
                and not b.get("beyond_cap"):
            chunks.extend(_entry_chunks(data, b))
    return chunks, warns
