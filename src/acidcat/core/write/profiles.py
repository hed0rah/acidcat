"""Metadata edit profiles: which tag fields each format's editor offers.

Mirrors what the write engine accepts (commands/write.py `_edit`), so a form
built from a profile only offers fields a save can apply. Moved here from the
TUI (tui_app/render.py) in 2.0: which format gets which editor is a fact about
formats, and the TUI decides nothing by format name (it reads the `edit` cap).
"""

import os


# editable-field profiles, mirroring what the write engine accepts per format.
# (field, label) -- field is the --set name commands.write understands.
_WAV_FIELDS = [("title", "title"), ("artist", "artist"), ("album", "album"),
               ("genre", "genre"), ("comment", "comment"), ("date", "date"),
               ("bpm", "bpm"), ("key", "key"),
               ("root_note", "root note (C3 or 60)")]
_AIFF_FIELDS = [("title", "title"), ("artist", "artist"), ("comment", "comment")]
_TAGGED_FIELDS = [("title", "title"), ("artist", "artist"), ("album", "album"),
                  ("genre", "genre"), ("comment", "comment"), ("date", "date"),
                  ("bpm", "bpm"), ("key", "key")]
_VITAL_FIELDS = [("name", "preset name"), ("author", "author"),
                 ("comment", "comments")]


def edit_profile(path):
    """Return (profile_name, [(field, label), ...]) for the file's format, or
    None where the write engine has no editor (or editing is disabled, e.g.
    Bitwig/NI). Routing mirrors commands.write._edit so the form only offers
    fields a save can actually apply."""
    with open(path, "rb") as f:
        head = f.read(16)
    return profile_for(head, path)


def profile_for(head, name):
    """`edit_profile` from the file's first 16 bytes and its name, so a file
    in memory is decided exactly as the same file on disk."""
    ext = os.path.splitext(name or "")[1].lower()
    if ext == ".vital" or head[:1] == b"{":
        return ("Vital", _VITAL_FIELDS)
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ("WAV", _WAV_FIELDS)
    # Bitwig / NI preset writing is disabled in the engine; do not offer it.
    if (head[:4] == b"BtWg" or head[12:16] == b"hsin" or head[:4] == b"-in-"
            or (head[:4] == b"RIFF" and head[8:12] == b"NIKS")):
        return None
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return ("AIFF", _AIFF_FIELDS)
    tagged = (head[:4] == b"fLaC" or head[:3] == b"ID3" or head[:4] == b"OggS"
              or head[4:8] == b"ftyp"
              or ext in (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4"))
    if tagged:
        return ("tagged", _TAGGED_FIELDS)
    return None



# tagged-audio text fields the write engine (mutagen) can set, keyed by the
# walker's field name: ID3 frame ids (mp3) and Vorbis comment keys (flac/ogg).
_ID3_TEXT = {"TIT2": "title", "TPE1": "artist", "TALB": "album", "TCON": "genre",
             "COMM": "comment", "TDRC": "date", "TYER": "date", "TBPM": "bpm",
             "TKEY": "key", "TRCK": "track"}
_VORBIS_TEXT = {"TITLE": "title", "ARTIST": "artist", "ALBUM": "album",
                "GENRE": "genre", "COMMENT": "comment", "DESCRIPTION": "comment",
                "DATE": "date", "BPM": "bpm", "KEY": "key", "INITIALKEY": "key",
                "TRACKNUMBER": "track"}


def text_field_for(profile, field_name):
    """If `field_name` (a walker field name) is a variable-length text field the
    write engine can edit, return the engine field name to route it through;
    else None. These must NOT be same-length byte-patched -- a longer title
    shifts the file -- so the editor re-serializes via the metadata engine."""
    if profile == "WAV":
        from acidcat.core.write.edit_riff import _INFO_TAGS
        rev = {v.decode("latin1").strip(): k for k, v in _INFO_TAGS.items()}
        return rev.get(field_name)
    if profile == "AIFF":
        from acidcat.core.write.edit_aiff import _AIFF_TEXT
        rev = {v.decode("latin1").strip(): k for k, v in _AIFF_TEXT.items()}
        return rev.get(field_name)
    if profile == "tagged":
        n = field_name.strip()
        return _ID3_TEXT.get(n) or _VORBIS_TEXT.get(n.upper())
    return None
