"""Library indexing engine: walk a directory tree, extract per-file
metadata, and upsert it into the SQLite index. Shared by the `index`
command and the MCP server, so it lives in core (not commands) to keep the
dependency direction right (mcp -> core, never mcp -> commands)."""

import json
import os
import sqlite3
import sys
import time

from acidcat.core import index as idx
from acidcat.core import paths as acidpaths
from acidcat.core import registry as reg
from acidcat.core.riff import (
    smpl_root_or_none, acid_root_or_none, effective_acid_beats,
)
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
    # ensure the query-layer expression indexes exist (existing DBs never get
    # them via _apply_schema, which returns early at the current version).
    try:
        idx.ensure_query_indexes(conn)
    except sqlite3.DatabaseError:
        pass
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

    # refresh planner statistics after a bulk change so the query layer's
    # index choices are stats-driven, not guesses across the many indexes.
    try:
        conn.execute("PRAGMA optimize")
    except sqlite3.DatabaseError:
        pass

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
    """WAV row extraction, driven by the inspect walker (the single WAV
    decoder since the 2026-07-10 unification): one walk fills a semantic
    ctx dict; the legacy core/riff parse is no longer run here."""
    from acidcat.util.midi import midi_note_to_pitch_class
    from acidcat.core.detect import parse_key_from_path, parse_bpm_from_filename
    from acidcat.core.walk.wav import inspect_wav

    ctx = {}
    chunks, _warns = inspect_wav(filepath, ctx=ctx)
    # rounded to match the retired core/riff.get_duration, so migrated rows
    # compare equal to previously indexed ones
    duration = round(ctx["duration"], 4) if ctx.get("duration") else None

    row["format"] = "wav"
    row["duration"] = duration
    row["bpm"] = ctx.get("acid_bpm")

    # SMPL/ACID root_note = 0 (MIDI C-1) is the documented "unset" value.
    # Treat it as missing so we can fall back to filename parsing.
    smpl = smpl_root_or_none(ctx.get("smpl_root"))
    acid = acid_root_or_none(ctx.get("acid_root"))

    # key stores pitch class only (no octave); full MIDI int lives in root_note.
    row["key"] = midi_note_to_pitch_class(smpl) or midi_note_to_pitch_class(acid)
    row["acid_beats"] = effective_acid_beats(
        {"acid_beats": ctx.get("acid_beats"),
         "acid_one_shot": ctx.get("acid_one_shot"),
         "bpm": ctx.get("acid_bpm")}, duration)
    row["root_note"] = smpl or acid
    # unique ids in first-seen order (a file can carry two LIST chunks;
    # the legacy path listed each id once and queries substring-match)
    row["chunks"] = ",".join(dict.fromkeys(
        c["id"] for c in chunks
        if c["id"] not in ("RIFF", "WAVE", "fmt ", "data")
    )) or None

    if ctx.get("sample_rate"):
        row["sample_rate"] = ctx.get("sample_rate")
        row["channels"] = ctx.get("channels")
        row["bits_per_sample"] = ctx.get("bits")

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
    """AIFF/AIFC row extraction, driven by the inspect walker (the single
    AIFF decoder since the 2026-07-10 unification)."""
    from acidcat.core.detect import parse_key_from_path, parse_bpm_from_filename
    from acidcat.util.midi import midi_note_to_pitch_class
    from acidcat.core.walk.aiff import inspect_aiff

    with open(filepath, "rb") as f:
        form = "AIFC" if f.read(12)[8:12] == b"AIFC" else "AIFF"
    ctx = {}
    chunks, _warns = inspect_aiff(filepath, form, ctx=ctx)
    row["format"] = "aiff"
    row["duration"] = ctx.get("duration")
    row["sample_rate"] = ctx.get("sample_rate")
    row["channels"] = ctx.get("channels")
    row["bits_per_sample"] = ctx.get("bits")
    row["title"] = ctx.get("name")
    row["artist"] = ctx.get("author")
    row["comment"] = ctx.get("copyright")
    row["chunks"] = ",".join(dict.fromkeys(c["id"] for c in chunks)) or None

    # Apple Loops carry beat count and root key in the basc chunk.
    # tempo is derived, not stored: the loops are tempo-flexible and
    # beats / duration * 60 recovers the recording tempo (matched the
    # filename bpm on 103/103 surveyed loops). root is a MIDI note;
    # the scale enum is unverified, so only the pitch class is used,
    # same convention as the WAV smpl root.
    if row.get("bpm") is None and ctx.get("basc_beats") and row["duration"]:
        row["bpm"] = round(ctx["basc_beats"] / row["duration"] * 60, 2)
    if row.get("key") is None and ctx.get("basc_root_key"):
        row["key"] = midi_note_to_pitch_class(ctx["basc_root_key"])

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
    """MIDI row extraction, driven by the inspect walker (the single MIDI
    decoder since the 2026-07-10 unification): one walk fills a semantic
    ctx dict. Key names come from the shared key_signature_name resolver, so
    scan and inspect can no longer disagree on the key."""
    from acidcat.core.walk.midi import inspect_midi

    ctx = {}
    inspect_midi(filepath, ctx=ctx)
    row["format"] = "midi"
    row["duration"] = ctx.get("duration")
    row["bpm"] = ctx.get("tempo_bpm")
    row["key"] = ctx.get("key_sig")
    if ctx.get("track_name"):
        row["title"] = ctx["track_name"]
    if ctx.get("copyright"):
        row["comment"] = ctx["copyright"]
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


def _refuses_as_root(path):
    norm = acidpaths.normalize(path)
    if norm == acidpaths.normalize(os.path.expanduser("~")):
        return True
    # platform root (e.g. /, C:/)
    if norm == os.path.dirname(norm):
        return True
    return False


def _count_audio_in_subtree(directory, max_depth=99):
    """Count files matching INDEXABLE_EXTENSIONS in `directory` up to
    max_depth levels deep. Skips junk files (._*, .DS_Store, etc.) and
    hidden directories (basename starting with '.').
    """
    count = 0
    base_depth = directory.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(directory, followlinks=False):
        depth = root.rstrip(os.sep).count(os.sep) - base_depth
        if depth > max_depth:
            dirs[:] = []
            continue
        # prune hidden dirs in-place so os.walk skips them
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if _is_junk(name):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in INDEXABLE_EXTENSIONS:
                count += 1
    return count


def _discover_candidates(root, registered_roots, min_samples, max_depth,
                          current_depth=0):
    """Walk `root` looking for subdirectories that qualify as libraries.

    A directory qualifies if its subtree (within max_depth) contains at
    least min_samples audio files AND it is not already registered AND it
    is not nested under one of the candidates we are about to return.

    Returns a sorted list of normalized absolute paths.
    """
    if current_depth >= max_depth:
        return []
    norm_root = acidpaths.normalize(root)
    if acidpaths.compare_path(norm_root) in registered_roots:
        # already a library: don't recurse, the caller's dedup handles it
        return []

    candidates = []
    try:
        children = sorted(os.listdir(root))
    except OSError:
        return []

    for child in children:
        if child.startswith("."):
            continue
        child_path = os.path.join(root, child)
        if not os.path.isdir(child_path):
            continue
        if os.path.islink(child_path):
            # don't follow symlinks: they often point at parent dirs and
            # would create infinite walks or duplicate registrations.
            continue
        norm_child = acidpaths.normalize(child_path)
        if acidpaths.compare_path(norm_child) in registered_roots:
            continue
        # check overlap with already-chosen candidates in this run so we
        # do not propose nested libraries
        if any(norm_child.startswith(c + "/") for c in candidates):
            continue

        count = _count_audio_in_subtree(child_path, max_depth=max_depth)
        if count >= min_samples:
            candidates.append(norm_child)
        else:
            # this child didn't qualify on its own; recurse one level
            # deeper to see if any of its grandchildren do
            sub = _discover_candidates(
                child_path, registered_roots, min_samples,
                max_depth, current_depth=current_depth + 1,
            )
            candidates.extend(sub)

    return candidates


def _resolve_unique_label(rconn, base_label, parent_basename, used_labels,
                          root=None):
    """Pick an unused label, preferring `base_label`. If taken, append the
    parent dir name. If still taken, append a short hash that includes
    the candidate root path so two unrelated roots that both default to
    the same base_label do not collide on the fallback. Mutates
    `used_labels`.
    """
    if base_label and base_label not in used_labels:
        existing = reg.get_library(rconn, base_label)
        if existing is None:
            used_labels.add(base_label)
            return base_label
    # try parent-disambiguated
    if parent_basename:
        candidate = f"{base_label}_{parent_basename}"
        if candidate not in used_labels and reg.get_library(rconn, candidate) is None:
            used_labels.add(candidate)
            return candidate
    # final fallback: hash suffix that incorporates the root path so
    # two distinct roots defaulting to base_label="library" do not
    # collide on a deterministic same-input hash.
    import hashlib
    seed = f"{base_label or 'lib'}|{root or ''}"
    h = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:6]
    candidate = f"{base_label}_{h}"
    used_labels.add(candidate)
    return candidate
