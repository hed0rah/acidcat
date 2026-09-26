"""ISO-BMFF MP4/M4A structural walker: decoded iTunes metadata and the
box tree, with the stsd sample entries and their codec-config boxes
(esds/alac/dOps) broken out. Box primitives live in core/mp4.py."""

import struct

from acidcat.core.formats import mp4 as mp4mod
from acidcat.core.infra.findings import defect
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import _f, _open, _size

_CODEC_NAMES = {"mp4a": "AAC", "alac": "Apple Lossless", "Opus": "Opus",
                "fLaC": "FLAC", "ac-3": "AC-3", "ec-3": "E-AC-3"}

# sample-entry 4ccs whose payload follows the AudioSampleEntry layout; video
# and text entries share the box shape but not the field offsets.
_AUDIO_ENTRY_4CC = {b"mp4a", b"alac", b"Opus", b"fLaC", b"ac-3", b"ec-3",
                    b"samr", b"sawb", b".mp3", b"lpcm", b"sowt", b"twos",
                    b"in24", b"in32", b"fl32", b"fl64", b"ulaw", b"alaw"}

# bytes read from the head to build the box tree. a moov that fits here is used in
# place; one that overruns it (or sits at EOF) is re-read in full so its udta/ilst
# metadata is not truncated. module-level so tests can shrink the window.
_HEAD_WINDOW = 8 * 1024 * 1024
_MOOV_CAP = 32 * 1024 * 1024


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
                    "payload_base": e["offset"] + e["hdr"],
                    # An ISO box size counts the header, so `size` is the extent
                    # and the payload is shorter by exactly that header. Saying
                    # only one of them let every reader that paired payload_base
                    # with size overshoot the box by 8.
                    "payload_len": e["size"] - e["hdr"],
                    "extent_len": e["size"]})
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
             "payload_base": boff + bhdr,
             "payload_len": bsize - bhdr, "extent_len": bsize}
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


_STCO_CAP = 256          # chunk-offset entries to annotate individually
# How far the media header and the sample table may disagree before it means
# something. Measured on real files: honest rounding lands around 25 ms.
_CLOCK_SLACK_S = 0.5
_CLOCK_SLACK = 0.02


def _stco_fields(data, b, file_size):
    """Decode an stco/co64 chunk-offset box into xref fields. Each entry is an
    absolute file offset to a run of sample data in mdat; annotate it so the
    TUI can follow it, and flag one that points past EOF (a truncated or
    re-muxed mdat leaves these dangling -- a forensic tell)."""
    base = b["offset"] + b["hdr"]
    wide = b["type"] == b"co64"
    step = 8 if wide else 4
    payload = data[base:b["offset"] + b["size"]]
    if len(payload) < 8:
        return "", [], []
    count = struct.unpack_from(">I", payload, 4)[0]
    avail = (len(payload) - 8) // step
    fields = [_f(0x00, 1, "version", payload[0]),
              _f(0x04, 4, "entry_count", f"{count:,}")]
    warns = []
    if count > avail:
        warns.append(defect("size.overrun",
                            f"declares {count:,} chunk offsets but payload holds {avail:,}"))
    dangling = 0
    shown = min(count, avail, _STCO_CAP)
    for i in range(min(count, avail)):
        o = 8 + i * step
        val = struct.unpack_from(">Q" if wide else ">I", payload, o)[0]
        if val >= file_size:
            dangling += 1
        if i < _STCO_CAP:
            fields.append(_f(o, step, f"chunk[{i}]", f"0x{val:08x}",
                             "past EOF" if val >= file_size else "-> sample data",
                             xref=val))
    note = f"{count:,} chunk offset(s)"
    if wide:
        note += ", 64-bit"
    if dangling:
        warns.append(defect("pointer.dangling", f"{dangling:,} chunk offset(s) point past EOF"))
        note += f", {dangling:,} dangling"
    if count > _STCO_CAP:
        note += f" (first {_STCO_CAP} annotated)"
    return note, fields, warns



def _box_fields(payload, kind):
    """Decoded fields for one box, or [] for a box we only name.

    Offsets are relative to the box PAYLOAD, which is what payload_base means
    everywhere in acidcat -- a box's fields are not at absolute positions and
    reporting them that way is the recurring bug this convention exists for.
    """
    if kind == b"mvhd":
        h = mp4mod.parse_mvhd(payload)
        if not h:
            return []
        out = [_f(0x00, 1, "version", h["version"]),
               _f(None, 0, "timescale", "%s / s" % f"{h['timescale']:,}",
                  "the clock the MOVIE is counted in"),
               _f(None, 0, "duration", f"{h['duration']:,}",
                  "%.3f s" % h["seconds"] if h["seconds"] else "")]
        if h["rate"] != 1.0:
            out.append(_f(None, 0, "rate", h["rate"], "1.0 is normal speed"))
        out.append(_f(None, 0, "volume", h["volume"], "1.0 is full"))
        for label in ("created", "modified"):
            if h[label]:
                out.append(_f(None, 0, label, _stamp(h[label]),
                              "counted from 1904, not 1970"))
        return out

    if kind == b"tkhd":
        h = mp4mod.parse_tkhd(payload)
        if not h:
            return []
        state = [n for n, on in (("enabled", h["enabled"]),
                                 ("in movie", h["in_movie"]),
                                 ("in preview", h["in_preview"])) if on]
        out = [_f(None, 0, "track_id", h["track_id"]),
               _f(None, 0, "flags", "0x%06X" % h["flags"],
                  ", ".join(state) if state else "not played"),
               _f(None, 0, "duration", f"{h['duration']:,}",
                  "in the movie timescale, not the media's")]
        if h["volume"]:
            out.append(_f(None, 0, "volume", h["volume"]))
        if h["width"] or h["height"]:
            out.append(_f(None, 0, "size", "%g x %g" % (h["width"], h["height"]),
                          "a visual track"))
        return out

    if kind == b"mdhd":
        h = mp4mod.parse_mdhd(payload)
        if not h:
            return []
        out = [_f(None, 0, "timescale", f"{h['timescale']:,}",
                  "usually the audio sample rate; NOT the movie's clock"),
               _f(None, 0, "duration", f"{h['duration']:,}",
                  "%.3f s" % h["seconds"] if h["seconds"] else "")]
        if h["language"]:
            out.append(_f(None, 0, "language", h["language"], "ISO 639-2/T"))
        return out

    if kind == b"hdlr":
        h = mp4mod.parse_hdlr(payload)
        if not h:
            return []
        out = [_f(0x08, 4, "handler", h["handler"], h["means"])]
        if h["name"]:
            out.append(_f(0x18, len(h["name"]), "name", h["name"]))
        return out

    if kind == b"elst":
        h = mp4mod.parse_elst(payload)
        if not h:
            return []
        out = [_f(0x04, 4, "entries", h["count"])]
        for i, e in enumerate(h["entries"]):
            note = "empty edit, inserts silence" if e["media_time"] < 0 else \
                "starts %s into the media" % f"{e['media_time']:,}"
            if e["rate"] != 1:
                note += ", rate %d" % e["rate"]
            out.append(_f(None, 0, "edit[%d]" % i,
                          "%s for %s" % (e["media_time"], f"{e['duration']:,}"),
                          note))
        if h["count"] > len(h["entries"]):
            out.append(_f(None, 0, "...", "%d more"
                          % (h["count"] - len(h["entries"]))))
        return out

    if kind == b"stts":
        h = mp4mod.parse_stts(payload)
        if not h:
            return []
        out = [_f(0x04, 4, "entries", h["count"]),
               _f(None, 0, "samples", f"{h['samples']:,}",
                  "summed from the table"),
               _f(None, 0, "duration", f"{h['duration']:,}",
                  "in the media timescale")]
        if h["constant"] and h["delta"]:
            out.append(_f(None, 0, "frame", h["delta"],
                          "every sample the same length"))
        return out

    if kind == b"stsz":
        h = mp4mod.parse_stsz(payload)
        if not h:
            return []
        out = [_f(0x04, 4, "uniform_size", h["uniform"],
                  "every sample this size" if h["uniform"]
                  else "0 means the table below"),
               _f(0x08, 4, "samples", f"{h['count']:,}")]
        if h["bytes"]:
            out.append(_f(None, 0, "media_bytes", f"{h['bytes']:,}",
                          "summed; largest sample %s" % f"{h['largest']:,}"))
        return out

    if kind == b"smhd":
        h = mp4mod.parse_smhd(payload)
        if not h:
            return []
        return [_f(0x04, 2, "balance", h["balance"], "0 is centred")]

    if kind == b"dref":
        h = mp4mod.parse_dref(payload)
        if not h:
            return []
        return [_f(0x04, 4, "entries", h["count"]),
                _f(None, 0, "self_contained", h["self_contained"],
                   "the media is in this file"
                   if h["self_contained"] else
                   "at least one reference points at ANOTHER file")]
    return []


def _stamp(unix_time):
    """A box timestamp, rendered. Out-of-range values are shown raw rather than
    raising: the field is only as trustworthy as the writer."""
    import datetime
    try:
        return datetime.datetime.utcfromtimestamp(unix_time).strftime(
            "%Y-%m-%d %H:%M:%S UTC")
    except (OSError, OverflowError, ValueError):
        return str(unix_time)



def _capped_note(payload, kind):
    """A coverage note when a bounded listing actually bit, and nothing when
    it did not. The bound is on the LISTING, never on the declared count,
    which is always reported."""
    if kind == b"elst":
        h = mp4mod.parse_elst(payload)
        if h and h["capped"]:
            return hit("list_rows", mp4mod._ELST_ENTRY_CAP, h['count'],
                       "listing the first %d of %d edit-list entries"
                       % (mp4mod._ELST_ENTRY_CAP, h["count"]))
    elif kind == b"dref":
        h = mp4mod.parse_dref(payload)
        if h and h["capped"]:
            return hit("list_rows", mp4mod._DREF_ENTRY_CAP, h['count'],
                       "examined the first %d of %d data references, so "
                       "whether the media is all in this file is not "
                       "settled" % (mp4mod._DREF_ENTRY_CAP, h["count"]))
    return None


def inspect_mp4(filepath):
    """Structural view of an ISO-BMFF MP4/M4A file: the decoded metadata (from
    udta > meta > ilst and the movie duration) followed by the box tree."""
    file_size = _size(filepath)
    with _open(filepath) as f:
        data = f.read(min(file_size, _HEAD_WINDOW))  # box tree from the head
        # metadata lives in moov. use the head window when the whole moov fits in
        # it; re-read the full moov when it sits at EOF (non-faststart output) OR
        # when it starts in the head but overruns it (large faststart files) --
        # otherwise its udta/ilst atoms at the tail of moov get truncated off.
        moov_data = data
        moov_box = next((b for b in mp4mod.iter_boxes(data)
                         if b["type"] == b"moov"), None)
        # `size` on a malformed box is what REMAINS, not what it declared --
        # so a moov whose tail sits past the head window is `truncated` and
        # its clamped size no longer sticks out. Asking the flag is the
        # question that was meant all along; the arithmetic only worked
        # while a bad size was passed through as an extent.
        overruns = moov_box is not None and (
            moov_box["truncated"]
            or moov_box["offset"] + moov_box["size"] > len(data))
        if (moov_box is None or overruns) and file_size > len(data):
            moff, msz = mp4mod.find_moov(filepath, file_size)
            if moff is not None:
                f.seek(moff)
                moov_data = f.read(min(msz, _MOOV_CAP))
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
        mfields.append(_f(None, 0, "duration", f"{dur_s:.3f} s",
                          "the MOVIE, which runs as long as its longest "
                          "track; a player showing only the audio may say "
                          "less"))
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

    clocks = {}
    for b in mp4mod.iter_boxes(data, file_size=file_size):
        t = b["type"].decode("latin-1", errors="replace")
        summary = ". " * b["depth"] + t
        fields = []
        if b["truncated"]:
            warns.append(defect(
                "size.overrun",
                f"box {t!r} at 0x{b['offset']:08x} declares "
                f"{b.get('declared', 0):,} bytes, which overruns its "
                f"parent; {b['size']:,} bytes remain"))
            summary += " (overruns parent)"
            fields.append(_f(0x00, 4, "declared_size", f"{b.get('declared', 0):,}",
                             "larger than what is left; not followed"))
        elif b.get("beyond_cap"):
            # a valid box (e.g. a large mdat) whose contents run past the read
            # window: not an error, just not fully read.
            summary += " (content beyond read window)"
        elif b["type"] == b"ftyp" and b["depth"] == 0:
            brand = data[b["offset"] + b["hdr"]:b["offset"] + b["hdr"] + 4]
            summary += f"  major brand {brand.decode('latin-1', errors='replace')}"
            fields.append(_f(0x00, 4, "major_brand",
                             brand.decode("latin-1", errors="replace")))
        box_warns_early = []
        if not b["truncated"] and not b.get("beyond_cap"):
            base = b["offset"] + b["hdr"]
            payload = data[base:b["offset"] + b["size"]]
            decoded = _box_fields(payload, b["type"])
            if decoded:
                fields.extend(decoded)
                clocks.setdefault(b["type"], []).append((base, payload))
            note = _capped_note(payload, b["type"])
            if note:
                box_warns_early.append(note)
                warns.append(note)
        if b["type"] in (b"stco", b"co64") and not b["truncated"] \
                and not b.get("beyond_cap"):
            note, sfields, box_warns_early2 = _stco_fields(data, b, file_size)
            box_warns_early.extend(box_warns_early2)
            if note:
                summary += f"  {note}"
            fields.extend(sfields)
        chunks.append({"id": t[:8], "offset": b["offset"], "size": b["size"],
                       "summary": summary, "fields": fields,
                       "warnings": box_warns_early,
                       "payload_base": b["offset"] + b["hdr"],
                       "payload_len": b["size"] - b["hdr"],
                       "extent_len": b["size"]})
        if b["type"] == b"stsd" and not b["truncated"] \
                and not b.get("beyond_cap"):
            chunks.extend(_entry_chunks(data, b))
    warns.extend(_clock_disagreements(clocks))
    return chunks, warns


def _clock_disagreements(clocks):
    """The same duration is written in three places on three different clocks.

    mvhd counts in the movie timescale, mdhd in the media's (usually the sample
    rate), and stts is the sum of the actual sample durations. They describe
    the same audio, so converting them to seconds and comparing is a check on
    the file that needs nothing outside it -- and a file whose clocks disagree
    is one where a player and a tagger will report different lengths.
    """
    out = []
    mdhd = [mp4mod.parse_mdhd(p) for _at, p in clocks.get(b"mdhd", [])]
    stts = [mp4mod.parse_stts(p) for _at, p in clocks.get(b"stts", [])]
    # NOT compared: the media duration against the movie duration. A track
    # whose media outlasts the movie looks wrong and is not -- an edit list
    # trims it, and all six such tracks in the files measured had one. That
    # check was written, fired six times, and every hit was legitimate, so it
    # was removed rather than left as a caveat nobody reads.
    for i, (m, t) in enumerate(zip(mdhd, stts)):
        if not (m and t and m["timescale"] and t["duration"] and m["seconds"]):
            continue
        summed = t["duration"] / m["timescale"]
        # Sub-frame rounding between the two is normal and was measured at
        # around 25 ms. The threshold sits well above that so the note means
        # "these disagree about the content", not "these disagree in the last
        # decimal": the one real hit was a media declared three seconds longer
        # than its own sample table accounts for.
        if abs(summed - m["seconds"]) > max(_CLOCK_SLACK_S,
                                            m["seconds"] * _CLOCK_SLACK):
            out.append("track %d declares %.3f s of media and its sample table "
                       "accounts for %.3f s" % (i + 1, m["seconds"], summed))
    return out
