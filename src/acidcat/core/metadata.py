"""The metadata field ledger: one vocabulary, and where each field lives.

acidcat can edit metadata in thirteen formats, and until this module existed
each of them had its own field names. The same idea was `title` in one map,
`name` in another and `preset_name` in a third; `artist`, `creator` and
`author` were three spellings of one thing. They overlapped BY CONVENTION,
which works until somebody adds a format and calls it `performer`.

This is the same shape as the cap ledger and the seed registry, and for the
same reason: a declaration table plus a test that pins it against the live
dispatch, so a claim that stops being true fails the suite rather than quietly
misleading a reader.

Two things live here.

CANONICAL names the fields and folds the aliases into them. It answers "what
is this field" once, rather than once per format.

BINDINGS says, per format, which canonical fields it can hold AND WHERE THEY
GO. That second half is the part worth having: it is the difference between
"WAV supports comment" and "WAV puts comment in LIST/INFO ICMT", and it is
what makes the lossiness of a cross-format move computable rather than
something a human has to know.
"""

# canonical name -> (aliases, kind, what it means)
#
# The aliases are not decoration: they are the spellings already accepted by
# the format writers, collected here so that accepting them stays a decision
# rather than an accident of which map a caller landed in.
#
# The KIND is what a value has to be. It was not in the first draft of this
# table and the pin test found the hole immediately: writing "probe" into
# `bpm` raised ValueError out of struct.pack, because a tempo is a float in
# the acid chunk and a string in a Vorbis comment. A cross-format move cannot
# be correct without knowing which -- "120" and 120.0 are the same tempo and
# not the same bytes.
TEXT, NUMBER, NOTE, DATE, TIME = "text", "number", "note", "date", "time"
# KEY is not free text. The WAV writer turns a key name into a MIDI note and
# refuses anything it cannot parse -- "unrecognized key 'probe'" -- while a
# Vorbis comment stores whatever string it is handed. Same field, two
# contracts, and the pin test found it by trying to write the wrong thing.
KEY = "key"

CANONICAL = {
    # ── descriptive, and common to nearly everything ─────────────────
    "title": (("name", "preset_name"), TEXT, "the piece's name"),
    "artist": (("creator", "author", "performer"), TEXT, "who made it"),
    "album": ((), TEXT, "the release it belongs to"),
    "albumartist": ((), TEXT, "the release's artist, where it differs from the track's"),
    "comment": (("comments",), TEXT, "free text with no defined meaning"),
    "description": (("bext_description",), TEXT, "free text describing the content"),
    "genre": ((), TEXT, "the style"),
    "date": (("year",), DATE, "when it was created"),
    "track": (("tracknumber",), NUMBER, "position within the release"),

    # ── musical, and the reason this tool is not exiftool ─────────────
    "bpm": (("tempo",), NUMBER, "tempo in beats per minute"),
    "key": (("initialkey",), KEY, "musical key, as a note name"),
    "root_note": (("root", "unity_note"), NOTE,
                  "the MIDI note a sampler should play the file back at"),

    # ── production and broadcast ──────────────────────────────────────
    "engineer": ((), TEXT, "who recorded or mixed it"),
    "software": ((), TEXT, "the tool that wrote the file"),
    "originator": ((), TEXT, "the broadcast originator (bext)"),
    "originator_reference": (("reference",), TEXT,
                             "the originator's unique reference (bext)"),
    "origination_date": (("date_recorded",), DATE, "when the recording was made (bext)"),
    "origination_time": (("time_recorded",), TIME, "time of day of the recording (bext)"),

    # ── preset and instrument ─────────────────────────────────────────
    "category": (("style",), TEXT, "what kind of sound or device it is"),
    "preset_category": ((), TEXT,
                        "how a preset is filed, where that differs from the "
                        "device's own category"),
    "tags": ((), TEXT, "free-form labels"),
    "device": ((), TEXT, "the device a preset is for"),

    # ── container-specific ────────────────────────────────────────────
    "annotation": ((), TEXT, "AIFF's free annotation chunk"),
}

# Every alias, mapped back to its canonical name. Built once rather than
# searched, because a lookup that scans is a lookup somebody will skip.
ALIASES = {alias: name
           for name, (aliases, _kind, _doc) in CANONICAL.items()
           for alias in aliases}
ALIASES.update({name: name for name in CANONICAL})


def canonical(field):
    """The canonical name for a field spelling, or None if it is not one."""
    return ALIASES.get(field.lower().strip()) if field else None


# format id -> {canonical field: where it goes}
#
# The right-hand side is a human-readable location, not a key the code uses --
# the writers already know where to put things. Its job is to let a reader ask
# "where does this end up" and to make a collision visible, which is how the
# AIFF row below earns its keep.
BINDINGS = {
    "wav": {
        "title": "LIST/INFO INAM", "artist": "LIST/INFO IART",
        "album": "LIST/INFO IPRD", "comment": "LIST/INFO ICMT",
        "genre": "LIST/INFO IGNR", "date": "LIST/INFO ICRD",
        "track": "LIST/INFO ITRK", "engineer": "LIST/INFO IENG",
        "software": "LIST/INFO ISFT",
        "description": "bext description",
        "originator": "bext originator",
        "originator_reference": "bext originator reference",
        "origination_date": "bext origination date",
        "origination_time": "bext origination time",
        "bpm": "acid tempo", "key": "acid root note",
        "root_note": "smpl unity note",
    },
    # AIFF has THREE text chunks and more than three ideas to put in them, so
    # comment, description and annotation all land in ANNO. Setting one
    # replaces another, and that is a fact about the format rather than a bug
    # in the writer -- which is exactly why it is written down.
    "aiff": {
        "title": "NAME", "artist": "AUTH",
        "comment": "ANNO", "description": "ANNO", "annotation": "ANNO",
    },
    "tagged": {
        "title": "TITLE", "artist": "ARTIST", "album": "ALBUM",
        "albumartist": "ALBUMARTIST", "comment": "COMMENT",
        "description": "COMMENT", "genre": "GENRE", "date": "DATE",
        "track": "TRACKNUMBER", "bpm": "BPM", "key": "KEY",
    },
    "vital": {
        "title": "preset_name", "artist": "author", "comment": "comments",
        "description": "comments", "category": "preset_style",
    },
    # Bitwig distinguishes the DEVICE from the PRESET and has a category for
    # each, so `category` and `preset_category` are separate fields here
    # rather than one with an alias. Its `name` is the device's, not the
    # preset's, so there is no title binding: claiming one would put a preset
    # title into a device name.
    "bitwig": {
        "artist": "creator", "comment": "comment", "description": "comment",
        "tags": "tags", "device": "device_name",
        "category": "device_category", "preset_category": "preset_category",
    },
}

# The tagged writer serves one vocabulary to six containers, so they share a
# row rather than repeating it five times. Kept explicit so a reader asking
# about `.flac` is not told to go and look up what "tagged" means.
TAGGED_FORMATS = ("flac", "mp3", "ogg", "opus", "mp4", "m4a")
for _fmt in TAGGED_FORMATS:
    BINDINGS[_fmt] = BINDINGS["tagged"]
BINDINGS["aifc"] = BINDINGS["aiff"]
BINDINGS["ni"] = BINDINGS["bitwig"]


def fields_for(fmt):
    """The canonical fields a format can hold, or () if it cannot be edited."""
    return tuple(sorted(BINDINGS.get(fmt, {})))


def where(fmt, field):
    """Where a canonical field lands in a format, or None."""
    name = canonical(field)
    return BINDINGS.get(fmt, {}).get(name) if name else None


def collisions(fmt):
    """Canonical fields that share one destination in this format.

    Setting either replaces the other. A cross-format move that lands two
    distinct fields on one target loses one of them, and this is what says so
    before the move rather than after.
    """
    seen = {}
    for field, target in BINDINGS.get(fmt, {}).items():
        seen.setdefault(target, []).append(field)
    return {t: sorted(f) for t, f in seen.items() if len(f) > 1}


def lost_moving(src, dst):
    """Canonical fields `src` can hold that `dst` cannot.

    The lossiness of a cross-format graft, computed rather than remembered.
    """
    return tuple(sorted(set(BINDINGS.get(src, {})) - set(BINDINGS.get(dst, {}))))


def kind_of(field):
    """What a value for this field has to be: text, number, note, date, time.

    A tempo is a float in a WAV acid chunk and a string in a Vorbis comment;
    a key is a parsed note name in one and an arbitrary string in the other.
    Moving between them is a conversion, and a move that does not know that is
    a move that raises out of struct.pack.
    """
    name = canonical(field)
    return CANONICAL[name][1] if name else None
