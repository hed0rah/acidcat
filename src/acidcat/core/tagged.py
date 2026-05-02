"""tagged format metadata via mutagen.

Handles MP3 (ID3v2), FLAC (Vorbis Comment), OGG/Opus (Vorbis Comment),
and M4A/MP4 (iTunes atoms). Returns a normalized metadata dict regardless
of source format.

Requires: pip install acidcat[tags]
"""

import os


# extensions we handle
TAGGED_EXTENSIONS = {
    ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac",
}


_BOM = "﻿"


def _strip_bom(value):
    """Strip a leading UTF-8 BOM from a string. Some ID3v2.4 / Vorbis
    tags carry the BOM in the value, which would corrupt FTS matching
    on the affected rows. No-op for non-string values."""
    if isinstance(value, str):
        return value.lstrip(_BOM)
    return value


def is_tagged_format(filepath):
    """Check if the file extension is a tagged audio format."""
    ext = os.path.splitext(filepath)[1].lower()
    return ext in TAGGED_EXTENSIONS


def parse_tagged(filepath):
    """Extract metadata from a tagged audio file.

    Returns a dict with normalized keys:
        format, duration, sample_rate, channels, bitrate, bits_per_sample,
        title, artist, album, date, genre, comment, bpm, key,
        track_number, disc_number, encoder, copyright
    """
    try:
        import mutagen
    except ImportError:
        from acidcat.util.deps import require
        require("mutagen", group="tags")
        return None

    try:
        m = mutagen.File(filepath)
    except Exception:
        return None
    if m is None:
        return None

    rec = {}

    # audio info (available on all formats)
    info = m.info
    rec["duration"] = round(info.length, 4) if info.length else None
    rec["sample_rate"] = getattr(info, "sample_rate", None)
    rec["channels"] = getattr(info, "channels", None)
    rec["bitrate"] = getattr(info, "bitrate", None)
    rec["bits_per_sample"] = getattr(info, "bits_per_sample", None)

    # detect format type and extract tags
    fmt_name = type(m).__name__  # MP3, FLAC, OggVorbis, OggOpus, MP4, etc.

    if hasattr(m, "tags") and m.tags is not None:
        if fmt_name == "MP3":
            rec["format_type"] = "mp3"
            _extract_id3(m.tags, rec)
        elif fmt_name == "MP4":
            rec["format_type"] = "m4a"
            _extract_mp4(m.tags, rec)
        elif fmt_name in ("FLAC",):
            rec["format_type"] = "flac"
            _extract_vorbis(m.tags, rec)
            # flac has native info fields
            if rec["bits_per_sample"] is None:
                rec["bits_per_sample"] = getattr(info, "bits_per_sample", None)
        elif fmt_name == "OggOpus":
            rec["format_type"] = "opus"
            _extract_vorbis(m.tags, rec)
        elif fmt_name in ("OggVorbis", "OggFLAC"):
            rec["format_type"] = "ogg"
            _extract_vorbis(m.tags, rec)
        else:
            rec["format_type"] = fmt_name.lower()
            # try vorbis-style first, then id3
            if hasattr(m.tags, "keys"):
                try:
                    _extract_vorbis(m.tags, rec)
                except Exception:
                    pass

    if "format_type" not in rec:
        rec["format_type"] = fmt_name.lower()

    return rec


def _first_text(tags, key, default=None):
    """Get first text value from ID3 tag."""
    tag = tags.get(key)
    if tag is None:
        return default
    if hasattr(tag, "text") and tag.text:
        val = _strip_bom(str(tag.text[0]))
        return val if val else default
    return _strip_bom(str(tag)) if tag else default


def _extract_id3(tags, rec):
    """Extract ID3v2 tags (MP3)."""
    rec["title"] = _first_text(tags, "TIT2")
    rec["artist"] = _first_text(tags, "TPE1")
    rec["album"] = _first_text(tags, "TALB")
    rec["date"] = _first_text(tags, "TDRC") or _first_text(tags, "TYER")
    rec["genre"] = _first_text(tags, "TCON")
    rec["track_number"] = _first_text(tags, "TRCK")
    rec["disc_number"] = _first_text(tags, "TPOS")
    rec["encoder"] = _first_text(tags, "TSSE") or _first_text(tags, "TENC")
    rec["copyright"] = _first_text(tags, "TCOP")
    rec["publisher"] = _first_text(tags, "TPUB")

    # BPM -- TBPM frame
    rec["bpm"] = _first_text(tags, "TBPM")
    if rec["bpm"]:
        try:
            rec["bpm"] = float(rec["bpm"])
            if rec["bpm"] == int(rec["bpm"]):
                rec["bpm"] = int(rec["bpm"])
        except ValueError:
            pass

    # key -- TKEY frame (e.g. "Am", "Cmaj", "F#m")
    rec["key"] = _first_text(tags, "TKEY")

    # comment -- COMM frame (can have multiple, take first non-empty)
    for key in tags:
        if key.startswith("COMM"):
            val = tags[key]
            if hasattr(val, "text") and val.text:
                text = str(val.text[0]).strip()
                if text:
                    rec["comment"] = text
                    break

    # TXXX user-defined frames (common in producer tools)
    for key in tags:
        if key.startswith("TXXX:"):
            frame_desc = key.split(":", 1)[1] if ":" in key else ""
            val = _first_text(tags, key)
            if not val:
                continue
            desc_lower = frame_desc.lower()
            if desc_lower == "bpm" and not rec.get("bpm"):
                try:
                    rec["bpm"] = float(val)
                except ValueError:
                    pass
            elif desc_lower in ("key", "initialkey", "initial key") and not rec.get("key"):
                rec["key"] = val


def _extract_vorbis(tags, rec):
    """Extract Vorbis Comment tags (FLAC, OGG, Opus)."""
    def first(key):
        vals = tags.get(key)
        if vals and isinstance(vals, list) and vals[0]:
            return _strip_bom(vals[0])
        return None

    rec["title"] = first("title") or first("TITLE")
    rec["artist"] = first("artist") or first("ARTIST")
    rec["album"] = first("album") or first("ALBUM")
    rec["date"] = first("date") or first("DATE") or first("year") or first("YEAR")
    rec["genre"] = first("genre") or first("GENRE")
    rec["comment"] = first("comment") or first("COMMENT") or first("description") or first("DESCRIPTION")
    rec["track_number"] = first("tracknumber") or first("TRACKNUMBER")
    rec["disc_number"] = first("discnumber") or first("DISCNUMBER")
    rec["encoder"] = first("encoder") or first("ENCODER")
    rec["copyright"] = first("copyright") or first("COPYRIGHT")

    # BPM
    bpm_str = first("bpm") or first("BPM") or first("TEMPO") or first("tempo")
    if bpm_str:
        try:
            bpm = float(bpm_str)
            rec["bpm"] = int(bpm) if bpm == int(bpm) else bpm
        except ValueError:
            rec["bpm"] = bpm_str

    # key
    rec["key"] = (first("key") or first("KEY") or first("INITIALKEY")
                  or first("initialkey") or first("initial_key"))


def _extract_mp4(tags, rec):
    """Extract iTunes/MP4 atoms."""
    def first(key):
        vals = tags.get(key)
        if vals and isinstance(vals, list):
            val = vals[0]
            return _strip_bom(str(val)) if val else None
        return None

    rec["title"] = first("\xa9nam")
    rec["artist"] = first("\xa9ART")
    rec["album"] = first("\xa9alb")
    rec["date"] = first("\xa9day")
    rec["genre"] = first("\xa9gen")
    rec["comment"] = first("\xa9cmt")
    rec["encoder"] = first("\xa9too")
    rec["copyright"] = first("cprt")

    # track number -- stored as (track, total) tuple
    trkn = tags.get("trkn")
    if trkn and isinstance(trkn, list) and trkn[0]:
        t = trkn[0]
        if isinstance(t, tuple):
            rec["track_number"] = f"{t[0]}/{t[1]}" if t[1] else str(t[0])
        else:
            rec["track_number"] = str(t)

    # disc number
    disk = tags.get("disk")
    if disk and isinstance(disk, list) and disk[0]:
        d = disk[0]
        if isinstance(d, tuple):
            rec["disc_number"] = f"{d[0]}/{d[1]}" if d[1] else str(d[0])
        else:
            rec["disc_number"] = str(d)

    # BPM -- tmpo atom (integer)
    tmpo = tags.get("tmpo")
    if tmpo and isinstance(tmpo, list) and tmpo[0]:
        try:
            rec["bpm"] = int(tmpo[0])
        except (ValueError, TypeError):
            pass

    # key -- no standard atom, some tools use a freeform key
    # check common locations
    for key_name in ["----:com.apple.iTunes:initialkey",
                     "----:com.apple.iTunes:KEY",
                     "----:com.apple.iTunes:key"]:
        val = tags.get(key_name)
        if val and isinstance(val, list) and val[0]:
            raw = val[0]
            if isinstance(raw, bytes):
                rec["key"] = raw.decode("utf-8", errors="replace")
            else:
                rec["key"] = str(raw)
            break
