"""Library indexing engine: walk a directory tree, extract per-file
metadata, and upsert it into the SQLite index. Shared by the `index`
command and the MCP server, so it lives in core (not commands) to keep the
dependency direction right (mcp -> core, never mcp -> commands)."""

import json
import os
import sys
import time

from acidcat.core import index as idx
from acidcat.core import paths as acidpaths
from acidcat.core import registry as reg
from acidcat.core.riff import (
    parse_riff, get_duration, get_fmt_info,
    smpl_root_or_none, acid_root_or_none, effective_acid_beats,
)
from acidcat.core.aiff import is_aiff, parse_aiff
from acidcat.core.midi import is_midi, parse_midi
from acidcat.core.mp3 import decode_frame_header
from acidcat.core.serum import is_serum_preset, parse_serum_preset
from acidcat.core.tagged import is_tagged_format




# synth/DAW preset formats whose metadata we index (device, product, creator,
# category, preset name, tags)
PRESET_EXTENSIONS = {
    ".bwpreset", ".bwclip", ".bwmodulator",       # Bitwig
    ".nmsv", ".nabs", ".nki", ".nkm", ".ksd", ".nksf",  # Native Instruments
    ".vital",                                       # Vital
}

INDEXABLE_EXTENSIONS = {
    ".wav", ".aif", ".aiff",
    ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac",
    ".mid", ".midi",
    ".serumpreset",
} | PRESET_EXTENSIONS

# OS sidecar / metadata junk that shows up in copied libraries.
JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini"}


def _is_junk(name):
    """True for files we never want in the index (AppleDouble, OS metadata)."""
    if name.startswith("._"):
        return True
    return name.lower() in JUNK_FILES


_COMMIT_EVERY_N_FILES = 100


def walk_and_upsert(conn, scan_root, do_features=False, do_deep=False,
                     quiet=False, force=False):
    walk_start = time.time()
    added = updated = skipped = failed = 0
    seen_paths = 0
    since_commit = 0

    for root, _, files in os.walk(scan_root, followlinks=False):
        for name in files:
            if _is_junk(name):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in INDEXABLE_EXTENSIONS:
                continue

            filepath = os.path.join(root, name)
            norm = idx.normalize_path(filepath)
            try:
                st = os.stat(filepath)
            except OSError:
                continue

            existing = idx.get_sample_stat(conn, norm)
            # force bypasses the mtime+size short-circuit: the files
            # did not change but the parser may have. annotations are
            # safe either way; they live in separate tables by path.
            if existing is not None and not force:
                old_mtime, old_size = existing
                if old_mtime == st.st_mtime and old_size == st.st_size:
                    idx.touch_last_seen(conn, norm, walk_start)
                    skipped += 1
                    seen_paths += 1
                    since_commit += 1
                    if since_commit >= _COMMIT_EVERY_N_FILES:
                        conn.commit()
                        since_commit = 0
                    continue

            row = _extract_for_index(
                filepath, scan_root=scan_root,
                mtime=st.st_mtime, size=st.st_size,
                walk_start=walk_start,
                do_deep=do_deep,
                quiet=quiet,
            )
            if row is None:
                failed += 1
                if not quiet:
                    print(f"  [skip] {name}: parse failed", file=sys.stderr)
                continue

            row_tags = row.pop("_tags", None)

            if existing is None:
                added += 1
            else:
                updated += 1
            idx.upsert_sample(conn, row)

            if row_tags:
                idx.upsert_tags(conn, row["path"], row_tags, replace=True)

            if do_features:
                _extract_and_store_features(conn, filepath, row["path"], quiet=quiet)

            seen_paths += 1
            since_commit += 1
            if since_commit >= _COMMIT_EVERY_N_FILES:
                conn.commit()
                since_commit = 0
            if not quiet:
                bpm = row.get("bpm") or "-"
                key = row.get("key") or "-"
                print(f"  {name:40s} bpm={bpm} key={key}", file=sys.stderr)

    # make the walk durable before pruning: a failure inside
    # prune_missing (symlink loop, permission error) must not roll
    # back the trailing batch of upserts.
    conn.commit()
    pruned = idx.prune_missing(conn, scan_root, walk_start)
    idx.record_scan_root(conn, scan_root, seen_paths, walk_start)
    conn.commit()

    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "pruned": pruned,
    }


def _sniff_format(filepath):
    """Peek the first 12 bytes of a file and return one of:
    'midi', 'aiff', 'wav', 'serum', 'flac', 'ogg', 'mp3', 'mp4', or None.

    Robust against double-suffixed filenames (e.g. foo.aiff.wav from a
    bad batch convert): we trust the magic bytes over the extension.
    Returns None if the file is unreadable or unrecognized; callers
    can fall back to extension-based dispatch in that case.
    """
    try:
        with open(filepath, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    if len(head) < 4:
        return None
    # synth/DAW presets (content-sniffed): Bitwig, NI hsin/ksd/nksf
    if head[0:4] == b"BtWg" or head[0:4] == b"-in-" \
            or head[12:16] == b"hsin" \
            or (head[0:4] == b"RIFF" and head[8:12] == b"NIKS"):
        return "preset"
    if head[0:4] == b"MThd":
        return "midi"
    if head[0:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head[0:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[0:8] == b"XferJson":
        return "serum"
    if head[0:4] == b"fLaC":
        return "flac"
    if head[0:4] == b"OggS":
        return "ogg"
    if head[0:3] == b"ID3":
        return "mp3"
    # MP3 sync frame (no ID3 tag): 11 set sync bits, then let the frame
    # header decoder vet version/layer/bitrate/rate. the old fixed byte
    # list only matched MPEG 1/2 Layer III, so Layer I/II and MPEG 2.5
    # files sniffed as None and were misindexed; decode_frame_header
    # also rejects ADTS AAC (reserved layer bits).
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0 \
            and decode_frame_header(head[0:4]) is not None:
        return "mp3"
    if head[4:8] == b"ftyp":
        return "mp4"
    return None


def _extract_for_index(filepath, scan_root, mtime, size, walk_start,
                       do_deep=False, quiet=True):
    """Dispatch to the right parser and return a sample row dict.

    Magic-byte sniff has priority over extension so a double-suffixed
    file (foo.aiff.wav) routes by its actual content, not by the
    trailing suffix. Returns None if the file could not be parsed at
    all. Includes a synthetic '_tags' list for formats that carry
    their own tags (e.g. Serum presets).
    """
    norm = idx.normalize_path(filepath)
    ext = os.path.splitext(filepath)[1].lower()
    sniffed = _sniff_format(filepath)

    row = {
        "path": norm,
        "scan_root": scan_root,
        "mtime": mtime,
        "size": size,
        "indexed_at": walk_start,
        "last_seen_at": walk_start,
    }

    try:
        if sniffed == "midi" or (sniffed is None and ext in (".mid", ".midi")):
            return _from_midi(filepath, row)
        if sniffed == "aiff" or (sniffed is None and ext in (".aif", ".aiff")):
            return _from_aiff(filepath, row)
        if sniffed == "serum" or (sniffed is None and ext == ".serumpreset"):
            return _from_serum(filepath, row)
        if sniffed == "preset" or (sniffed is None and ext in PRESET_EXTENSIONS):
            return _from_preset(filepath, row)
        if sniffed in ("flac", "ogg", "mp3", "mp4"):
            return _from_tagged(filepath, row, do_deep=do_deep)
        if sniffed is None and is_tagged_format(filepath) and ext != ".wav":
            return _from_tagged(filepath, row, do_deep=do_deep)
        # default: WAV/RIFF (sniffed == "wav" or sniffed is None and ext is .wav or unknown)
        return _from_wav(filepath, row, do_deep=do_deep)
    except Exception as e:
        # swallowing is deliberate (a bad file must not kill the walk),
        # but silence is not: without the class+message a programming
        # bug looks identical to a genuinely corrupt file.
        if not quiet:
            print(f"  [skip] {os.path.basename(filepath)}: "
                  f"{e.__class__.__name__}: {e}", file=sys.stderr)
        return None


def _from_wav(filepath, row, do_deep=False):
    from acidcat.util.midi import midi_note_to_pitch_class
    from acidcat.core.detect import parse_key_from_path, parse_bpm_from_filename

    _, meta, seen = parse_riff(filepath, enumerate_all=False)
    duration = get_duration(filepath)
    fmt = get_fmt_info(filepath)

    row["format"] = "wav"
    row["duration"] = duration
    row["bpm"] = meta.get("bpm")

    # SMPL/ACID root_note = 0 (MIDI C-1) is the default "unset" value.
    # Treat it as missing so we can fall back to filename parsing.
    smpl = smpl_root_or_none(meta)
    acid = acid_root_or_none(meta)

    # key stores pitch class only (no octave); full MIDI int lives in root_note.
    row["key"] = midi_note_to_pitch_class(smpl) or midi_note_to_pitch_class(acid)
    row["acid_beats"] = effective_acid_beats(meta, duration)
    row["root_note"] = smpl or acid
    row["chunks"] = ",".join(
        c for c in seen if c not in ("RIFF", "WAVE", "fmt ", "data")
    ) or None

    if fmt:
        row["sample_rate"] = fmt.get("sample_rate")
        row["channels"] = fmt.get("channels")
        row["bits_per_sample"] = fmt.get("bits_per_sample")

    if row["key"] is None:
        row["key"] = parse_key_from_path(filepath)
    if row["bpm"] is None:
        fname_bpm = parse_bpm_from_filename(filepath)
        if fname_bpm is not None:
            row["bpm"] = float(fname_bpm)

    if do_deep and row["bpm"] is None:
        _fill_from_librosa(filepath, row)

    return row


def _from_aiff(filepath, row, do_deep=False):
    from acidcat.core.detect import parse_key_from_path, parse_bpm_from_filename
    from acidcat.util.midi import midi_note_to_pitch_class

    _, meta, seen = parse_aiff(filepath, enumerate_all=False)
    row["format"] = "aiff"
    row["duration"] = meta.get("duration_sec")
    row["sample_rate"] = meta.get("sample_rate")
    row["channels"] = meta.get("channels")
    row["bits_per_sample"] = meta.get("bits_per_sample")
    row["title"] = meta.get("name")
    row["artist"] = meta.get("author")
    row["comment"] = meta.get("copyright")
    row["chunks"] = ",".join(seen) if seen else None

    # Apple Loops carry beat count and root key in the basc chunk.
    # tempo is derived, not stored: the loops are tempo-flexible and
    # beats / duration * 60 recovers the recording tempo (matched the
    # filename bpm on 103/103 surveyed loops). root is a MIDI note;
    # the scale enum is unverified, so only the pitch class is used,
    # same convention as the WAV smpl root.
    if row.get("bpm") is None and meta.get("basc_beats") and row["duration"]:
        row["bpm"] = round(meta["basc_beats"] / row["duration"] * 60, 2)
    if row.get("key") is None and meta.get("basc_root_key"):
        row["key"] = midi_note_to_pitch_class(meta["basc_root_key"])

    # otherwise fall back to filename/folder tokens.
    if row.get("key") is None:
        row["key"] = parse_key_from_path(filepath)
    if row.get("bpm") is None:
        fname_bpm = parse_bpm_from_filename(filepath)
        if fname_bpm is not None:
            row["bpm"] = float(fname_bpm)

    if do_deep and row.get("bpm") is None:
        _fill_from_librosa(filepath, row)
    return row


def _from_midi(filepath, row):
    meta = parse_midi(filepath)
    row["format"] = "midi"
    row["duration"] = meta.get("duration_sec")
    row["bpm"] = meta.get("tempo_bpm")
    row["key"] = meta.get("key_sig")
    if meta.get("track_names"):
        row["title"] = meta["track_names"][0]
    if meta.get("copyright"):
        row["comment"] = meta["copyright"]
    return row


def _from_serum(filepath, row):
    meta = parse_serum_preset(filepath)
    row["format"] = "serum"
    if meta.get("presetName"):
        row["title"] = meta["presetName"]
    if meta.get("presetAuthor"):
        row["artist"] = meta["presetAuthor"]
    if meta.get("presetDescription"):
        row["comment"] = meta["presetDescription"]
    tags = meta.get("tags")
    if isinstance(tags, list):
        row["_tags"] = tags
    elif isinstance(tags, str) and tags:
        row["_tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    return row


def _from_preset(filepath, row):
    """Index a synth/DAW preset's metadata (Bitwig, Native Instruments, Vital).
    Reads a bounded prefix (all preset metadata lives near the start), normalizes
    via core.preset_meta, and maps into the preset columns + tags."""
    from acidcat.core import preset_meta
    with open(filepath, "rb") as f:
        data = f.read(min(row["size"], 8 * 1024 * 1024))
    meta = preset_meta.extract(data)
    if not meta:
        return None
    ext = os.path.splitext(filepath)[1].lower().lstrip(".")
    row["format"] = ext or "preset"
    row["preset_name"] = meta.get("preset_name")
    row["device"] = meta.get("device")
    row["product"] = meta.get("product")
    row["creator"] = meta.get("creator")
    row["category"] = meta.get("category")
    # mirror into the common columns so existing text/title views still work
    row["title"] = meta.get("preset_name")
    row["artist"] = meta.get("creator")
    row["comment"] = meta.get("description")
    if meta.get("tags"):
        row["_tags"] = meta["tags"]
    return row


def _from_tagged(filepath, row, do_deep=False):
    from acidcat.core.tagged import parse_tagged
    from acidcat.core.detect import parse_key_from_path, parse_bpm_from_filename

    meta = parse_tagged(filepath)
    if meta is None:
        return None
    row["format"] = meta.get("format_type") or "tagged"
    row["duration"] = meta.get("duration")
    row["sample_rate"] = meta.get("sample_rate")
    row["channels"] = meta.get("channels")
    row["bits_per_sample"] = meta.get("bits_per_sample")
    row["title"] = meta.get("title")
    row["artist"] = meta.get("artist")
    row["album"] = meta.get("album")
    row["genre"] = meta.get("genre")
    row["comment"] = meta.get("comment")
    row["bpm"] = _coerce_bpm(meta.get("bpm"))
    row["key"] = meta.get("key")

    # genre frames feed the tags table the same way serum preset tags
    # do, so `query --tag house` works against tagged-format
    # libraries. multi-genre strings split on the common separators.
    genre = meta.get("genre")
    if genre:
        parts = [g.strip() for g in
                 genre.replace(";", ",").replace("/", ",").split(",")]
        row["_tags"] = [g for g in parts if g]

    if row["key"] is None:
        row["key"] = parse_key_from_path(filepath)
    if row["bpm"] is None:
        fname_bpm = parse_bpm_from_filename(filepath)
        if fname_bpm is not None:
            row["bpm"] = float(fname_bpm)

    if do_deep and row["bpm"] is None:
        _fill_from_librosa(filepath, row)
    return row


def _coerce_bpm(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fill_from_librosa(filepath, row):
    try:
        from acidcat.core.detect import estimate_librosa_metadata
    except ImportError:
        return
    est = estimate_librosa_metadata(filepath) or {}
    if est.get("estimated_bpm") and row.get("bpm") is None:
        bpm = est["estimated_bpm"]
        if isinstance(bpm, (int, float)):
            row["bpm"] = float(bpm)
    if est.get("estimated_key") and row.get("key") is None:
        row["key"] = est["estimated_key"]
    if est.get("duration_sec") and row.get("duration") is None:
        row["duration"] = est["duration_sec"]


def _extract_and_store_features(conn, filepath, path_key, quiet=False):
    try:
        from acidcat.core.features import extract_audio_features
    except ImportError:
        if not quiet:
            print("  [features] librosa not installed; skipping", file=sys.stderr)
        return
    feats = extract_audio_features(filepath)
    if feats is None:
        return
    idx.upsert_features(conn, path_key, feats, version=1)
