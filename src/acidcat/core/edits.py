"""Metadata editors for acidcat's write capability.

Each editor takes the file bytes and a `changes` dict {field: value} (value None
clears the field) and returns (new_bytes, applied) where applied is a list of
(field, old, new) for the dry-run diff. Field names are the same ones acidcat
displays, so editing is WYSIWYG. An unsupported field raises EditError rather
than silently doing nothing.

WAV/RIFF editing lives in edit_riff.py (spec-sensitive); this module covers the
JSON and tag-library formats where correctness is straightforward.
"""

import json
import os
import tempfile


class EditError(ValueError):
    """A requested edit cannot be applied (unsupported field, wrong format, ...)."""


# ── Vital (bare JSON) ──────────────────────────────────────────────

_VITAL_FIELDS = {
    "name": "preset_name", "preset_name": "preset_name", "title": "preset_name",
    "author": "author", "creator": "author", "artist": "author",
    "comment": "comments", "comments": "comments", "description": "comments",
    "style": "preset_style", "category": "preset_style",
}


def edit_vital(data, changes):
    """Edit a Vital preset's top-level metadata. Re-serializing preserves the
    full synth state (every other key is left untouched)."""
    obj = json.loads(data)
    if not isinstance(obj, dict) or "synth_version" not in obj:
        raise EditError("not a Vital preset")
    applied = []
    for field, value in changes.items():
        key = _VITAL_FIELDS.get(field.lower())
        if key is None:
            raise EditError(f"Vital preset has no editable field {field!r}")
        old = obj.get(key)
        obj[key] = "" if value is None else str(value)
        applied.append((field, old, obj[key]))
    return json.dumps(obj).encode("utf-8"), applied


# ── tagged audio (mp3/flac/ogg/m4a via mutagen) ────────────────────

# field -> mutagen "easy" key (the normalized cross-format interface)
_EASY_FIELDS = {
    "title": "title", "name": "title",
    "artist": "artist", "creator": "artist",
    "albumartist": "albumartist",
    "album": "album",
    "genre": "genre",
    "comment": "comment", "description": "comment",
    "date": "date", "year": "date",
    "bpm": "bpm",
    "track": "tracknumber", "tracknumber": "tracknumber",
}
_easyid3_ready = False


def _register_easyid3_comment():
    """Teach mutagen's EasyID3 to read/write a plain comment (COMM), which it
    does not expose by default."""
    global _easyid3_ready
    if _easyid3_ready:
        return
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import COMM
    if "comment" not in EasyID3.valid_keys:
        def _get(id3, _):
            return [c.text[0] for c in id3.getall("COMM")
                    if c.desc == "" and c.text]

        def _set(id3, _, value):
            id3.delall("COMM")
            id3.add(COMM(encoding=3, lang="eng", desc="", text=value))

        def _del(id3, _):
            id3.delall("COMM")
        EasyID3.RegisterKey("comment", _get, _set, _del)
    _easyid3_ready = True


def edit_tagged(data, suffix, changes):
    """Edit tags on an mp3/flac/ogg/opus/m4a via mutagen (which owns the on-disk
    tag spec). Round-trips through a temp file so the caller still gets bytes."""
    try:
        import mutagen
    except ImportError:
        raise EditError("editing tagged audio needs mutagen (pip install mutagen)")
    try:
        _register_easyid3_comment()
    except Exception:
        pass
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        audio = mutagen.File(tmp, easy=True)
        if audio is None:
            raise EditError("mutagen could not read this audio file")
        applied = []
        for field, value in changes.items():
            key = _EASY_FIELDS.get(field.lower())
            if key is None:
                raise EditError(f"tagged audio has no editable field {field!r}")
            old = audio.get(key)
            old = old[0] if isinstance(old, list) and old else old
            try:
                if value is None or value == "":
                    audio.pop(key, None)
                else:
                    audio[key] = [str(value)]
            except (KeyError, ValueError, TypeError):
                raise EditError(
                    f"{suffix.lstrip('.') or 'this format'} cannot store "
                    f"field {field!r}")
            applied.append((field, old, value))
        audio.save()
        with open(tmp, "rb") as r:
            return r.read(), applied
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
