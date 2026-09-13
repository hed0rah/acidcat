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
from collections import namedtuple as _namedtuple

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

    # ── read-only: present in files, with no writer behind them ───────
    "copyright": ((), TEXT, "the rights statement"),
    "encoder": ((), TEXT, "the encoder that produced the stream"),
    "disc": (("disc_number", "discnumber"), NUMBER,
             "which disc of a set"),
    "keywords": ((), TEXT, "search terms the writer attached"),
    "subject": ((), TEXT, "what the recording is of"),
    "technician": ((), TEXT, "who operated the equipment"),

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


# format id -> {canonical field: Bind(label, chunk, key)}
#
# `label` is what a person reads. `chunk` and `key` are where the value
# actually lives in a walked file, which is what lets reading use this table
# rather than a second one -- and a second one is the exact duplication this
# module exists to prevent.
#
# `key` is the READER's key specifically. The writers have their own maps and
# never consult this one, so a binding that borrows a writer's spelling reads
# nothing and looks fine: `track` was written as `tracknumber` here, which is
# what the writer takes, while the reader emits `track_number`.
#
# chunk is None for the tagged formats, whose values come from the tag reader
# rather than from a chunk walk.
# access: "rw" both ways, "r" readable only, "w" writable only.
#
# Not decoration. A field that writes and does not read back is either a bug
# or a fact about the format, and the two look identical until somebody says
# which. `track` was the bug -- the binding carried the writer's spelling of
# the key and the reader uses another. `albumartist` is the fact: it writes,
# and the tag reader does not surface it. `copyright` and `encoder` are the
# mirror image, readable with no writer behind them.
Bind = _namedtuple("Bind", "label chunk key access")
Bind.__new__.__defaults__ = ("rw",)


def readable(fmt, field):
    """Can this field be read back out of this format?"""
    bind = binding(fmt, field)
    return bool(bind) and "r" in bind.access


def writable(fmt, field):
    """Can this field be written into this format?"""
    bind = binding(fmt, field)
    return bool(bind) and "w" in bind.access

BINDINGS = {
    "wav": {
        "title": Bind("LIST/INFO INAM", "LIST", "INAM"),
        "artist": Bind("LIST/INFO IART", "LIST", "IART"),
        "album": Bind("LIST/INFO IPRD", "LIST", "IPRD"),
        "comment": Bind("LIST/INFO ICMT", "LIST", "ICMT"),
        "genre": Bind("LIST/INFO IGNR", "LIST", "IGNR"),
        "date": Bind("LIST/INFO ICRD", "LIST", "ICRD"),
        "track": Bind("LIST/INFO ITRK", "LIST", "ITRK"),
        "engineer": Bind("LIST/INFO IENG", "LIST", "IENG"),
        "software": Bind("LIST/INFO ISFT", "LIST", "ISFT"),
        "description": Bind("bext description", "bext", "description"),
        "originator": Bind("bext originator", "bext", "originator"),
        "originator_reference": Bind("bext originator reference", "bext", "originator_reference"),
        "origination_date": Bind("bext origination date", "bext", "origination_date"),
        "origination_time": Bind("bext origination time", "bext", "origination_time"),
        # WAV has a SECOND home for a tempo -- LIST/INFO IBPM, which Bitwig
        # writes -- and this binds only the acid chunk. Measured before
        # deciding whether that matters: IBPM appears in 0 of 80,005 files
        # here, against 472 with an acid chunk in the first 20,000. A field
        # with two homes needs a precedence rule, and building one for a
        # tag nothing writes would be machinery in search of a file.
        "bpm": Bind("acid tempo", "acid", "tempo"),
        "key": Bind("acid root note", "acid", "root_note"),
        "root_note": Bind("smpl unity note", "smpl", "midi_unity_note"),
        # LIST/INFO tags the walker names and no writer sets
        "copyright": Bind("LIST/INFO ICOP", "LIST", "ICOP", "r"),
        "keywords": Bind("LIST/INFO IKEY", "LIST", "IKEY", "r"),
        "subject": Bind("LIST/INFO ISBJ", "LIST", "ISBJ", "r"),
        "technician": Bind("LIST/INFO ITCH", "LIST", "ITCH", "r"),
    },
    # AIFF has THREE text chunks and more than three ideas to put in them, so
    # comment, description and annotation all land in ANNO. Setting one
    # replaces another, and that is a fact about the format rather than a bug
    # in the writer -- which is exactly why it is written down.
    "aiff": {
        "title": Bind("NAME", "NAME", "text"),
        "artist": Bind("AUTH", "AUTH", "text"),
        "comment": Bind("ANNO", "ANNO", "text"),
        "description": Bind("ANNO", "ANNO", "text"),
        "annotation": Bind("ANNO", "ANNO", "text"),
    },
    "tagged": {
        "title": Bind("TITLE", None, "title"),
        "artist": Bind("ARTIST", None, "artist"),
        "album": Bind("ALBUM", None, "album"),
        # written, and the tag reader does not surface it
        "albumartist": Bind("ALBUMARTIST", None, "albumartist", "w"),
        "comment": Bind("COMMENT", None, "comment"),
        "description": Bind("COMMENT", None, "comment"),
        "genre": Bind("GENRE", None, "genre"),
        "date": Bind("DATE", None, "date"),
        "track": Bind("TRACKNUMBER", None, "track_number"),
        "bpm": Bind("BPM", None, "bpm"),
        "key": Bind("KEY", None, "key"),
        # readable, with no writer behind them
        "copyright": Bind("COPYRIGHT", None, "copyright", "r"),
        "encoder": Bind("ENCODER", None, "encoder", "r"),
        "disc": Bind("DISCNUMBER", None, "disc_number", "r"),
    },
    "vital": {
        "title": Bind("preset_name", None, "preset_name"),
        "artist": Bind("author", None, "author"),
        "comment": Bind("comments", None, "comments"),
        "description": Bind("comments", None, "comments"),
        "category": Bind("preset_style", None, "preset_style"),
    },
    # Bitwig distinguishes the DEVICE from the PRESET and has a category for
    # each, so `category` and `preset_category` are separate fields here
    # rather than one with an alias. Its `name` is the device's, not the
    # preset's, so there is no title binding: claiming one would put a preset
    # title into a device name.
    "bitwig": {
        "artist": Bind("creator", None, "creator"),
        "comment": Bind("comment", None, "comment"),
        "description": Bind("comment", None, "comment"),
        "tags": Bind("tags", None, "tags"),
        "device": Bind("device_name", None, "device_name"),
        "category": Bind("device_category", None, "device_category"),
        "preset_category": Bind("preset_category", None, "preset_category"),
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


def fields_for(fmt, access=None):
    """The canonical fields a format can hold.

    `access` filters to "r" or "w"; without it, every field either way.
    """
    binds = BINDINGS.get(fmt, {})
    if access:
        return tuple(sorted(f for f, b in binds.items() if access in b.access))
    return tuple(sorted(binds))


def where(fmt, field):
    """Where a canonical field lands in a format, as text, or None."""
    bind = binding(fmt, field)
    return bind.label if bind else None


def binding(fmt, field):
    """The full Bind for a field in a format, or None."""
    name = canonical(field)
    return BINDINGS.get(fmt, {}).get(name) if name else None


def collisions(fmt):
    """Canonical fields that share one destination in this format.

    Setting either replaces the other. A cross-format move that lands two
    distinct fields on one target loses one of them, and this is what says so
    before the move rather than after.
    """
    seen = {}
    for field, bind in BINDINGS.get(fmt, {}).items():
        seen.setdefault(bind.label, []).append(field)
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


def read_metadata(path, fmt=None):
    """Read a file's metadata as canonical fields.

    The counterpart to `edit_metadata`, and deliberately built on the SAME
    bindings: a separate read table would be free to drift, and the asymmetry
    it produced is what this module exists to end. Before this, the writer
    handled thirteen formats and the tag reader handled eight -- different
    eight -- so WAV metadata was reachable only by walking the file, and a
    caller who tried the obvious reader got nothing and no explanation.

    Returns {canonical field: value} for what is present. Absent fields are
    absent rather than None, so a caller can tell "not set" from "set empty".
    Never raises: an unreadable file is an empty answer, which is the same
    courtesy `read_tags` already extends.
    """
    from acidcat.core.infra.sniff import sniff

    fmt = fmt or sniff(path)
    binds = BINDINGS.get(fmt or "", {})
    if not binds:
        return {}

    # the tagged formats answer through the tag reader; everything else is
    # read out of a chunk walk, because that is where its metadata lives
    if all(b.chunk is None for b in binds.values()):
        try:
            from acidcat.core.tagged import read_tags
            tags = read_tags(path) or {}
        except Exception:                                  # noqa: BLE001
            return {}
        out = {}
        for field, bind in binds.items():
            if "r" not in bind.access:
                continue
            value = tags.get(bind.key)
            if value not in (None, ""):
                out.setdefault(field, value)
            # a collision means two canonical fields read the same place; the
            # first one wins so `comment` beats `description` deterministically
        return out

    try:
        from acidcat.core.walk import walk_file
        _label, chunks, _warns = walk_file(path)
    except Exception:                                      # noqa: BLE001
        return {}
    by_id = {}
    for entry in chunks:
        by_id.setdefault(entry["id"].strip(), entry)

    out = {}
    for field, bind in binds.items():
        if bind.chunk is None or "r" not in bind.access:
            continue
        entry = by_id.get(bind.chunk.strip())
        if entry is None:
            continue
        for fld in entry.get("fields", ()):
            if fld["name"] == bind.key:
                value = fld["value"]
                if _is_set(field, value):
                    out.setdefault(field, value)
                break
    return out


def _is_set(field, value):
    """Is this value a value, or the format's way of saying "nothing here"?

    Chunks are fixed-size, so an unset field is not absent -- it is zero. The
    acid chunk carries a root note of 0 whether the key is unset or is C-1,
    and reporting the first as a key would graft a "0" into the next file as
    though someone had chosen it.

    The cost of this rule is one unreportable value per field: a genuine C-1
    root note reads as unset. That is the right trade at the bottom of the
    MIDI range, and it is stated here rather than discovered later.
    """
    if value in (None, ""):
        return False
    if kind_of(field) in (NOTE, KEY) and value in (0, "0"):
        return False
    return True
