"""The metadata field ledger is pinned against the live writer.

A declaration table that nothing checks is a comment. This file is the check:
every field the ledger claims a format can hold must actually be accepted by
`edit_metadata`, and every field the writer accepts must be in the ledger.

Same shape as tests/test_cap_announcements.py and the `_EDIT` pin in
tests/test_formats.py, for the same reason: a claim that stops being true
should fail the suite rather than quietly mislead whoever reads it next.
"""

import struct

import pytest

from acidcat.core import metadata as M
from acidcat.core.write import edit_aiff, edit_riff, edits
from acidcat.core.write.edits import EditError, edit_metadata


# ── the vocabulary itself ───────────────────────────────────────────

def test_every_alias_resolves_to_a_canonical_name():
    for alias, name in M.ALIASES.items():
        assert name in M.CANONICAL, f"{alias!r} resolves to unknown {name!r}"


def test_no_alias_shadows_a_canonical_name():
    """An alias that is also a canonical field would make one of the two
    unreachable, and which one would depend on dict order."""
    for name, (aliases, _kind, _doc) in M.CANONICAL.items():
        for alias in aliases:
            assert alias not in M.CANONICAL, \
                f"{alias!r} is both an alias of {name!r} and canonical"


def test_canonical_lookup_is_case_and_space_insensitive():
    assert M.canonical("Title") == "title"
    assert M.canonical("  tempo ") == "bpm"
    assert M.canonical("preset_name") == "title"
    assert M.canonical("nonsense") is None


def test_every_field_has_a_definition():
    """A name with no meaning beside it is the thing this module exists to
    stop: it is how `creator` and `author` became two fields."""
    for name, (_aliases, kind, doc) in M.CANONICAL.items():
        assert doc and len(doc) > 8, f"{name!r} has no definition"
        assert kind in (M.TEXT, M.NUMBER, M.NOTE, M.DATE, M.TIME,
                        M.KEY), (name, kind)


# ── the bindings, against the live writers ──────────────────────────

def _minimal(fmt):
    """The smallest real file of each kind the writer will accept."""
    if fmt in ("wav",):
        f = b"fmt " + struct.pack("<I", 16) + struct.pack(
            "<HHIIHH", 1, 1, 44100, 88200, 2, 16)
        d = b"data" + struct.pack("<I", 4) + bytes(4)
        body = b"WAVE" + f + d
        return b"RIFF" + struct.pack("<I", len(body)) + body
    if fmt in ("aiff", "aifc"):
        comm = b"COMM" + struct.pack(">I", 18) + struct.pack(">hIh", 1, 4, 16) \
            + bytes.fromhex("400eac440000000000000000")[:10]
        ssnd = b"SSND" + struct.pack(">I", 16) + struct.pack(">II", 0, 0) + bytes(8)
        body = b"AIFF" + comm + ssnd
        return b"FORM" + struct.pack(">I", len(body)) + body
    return None


_LIVE_MAPS = {
    "wav": set(edit_riff._INFO_TAGS) | set(edit_riff._BEXT_FIELDS)
           | set(edit_riff._ACID_FIELDS) | set(edit_riff._SMPL_FIELDS),
    "aiff": set(edit_aiff._AIFF_TEXT),
    "tagged": set(edits._EASY_FIELDS),
    "vital": set(edits._VITAL_FIELDS),
    "bitwig": set(edits._BITWIG_FIELDS),
}


@pytest.mark.parametrize("fmt", sorted(_LIVE_MAPS))
def test_every_bound_field_is_accepted_by_the_writer(fmt):
    """The ledger must not claim a field the writer would reject.

    Checked against the writer's own field map rather than by writing a file,
    because the map IS the acceptance test inside edit_metadata -- and going
    through the map catches a claim about a format whose files are awkward to
    synthesise here.
    """
    live = _LIVE_MAPS[fmt]
    for field in M.fields_for(fmt, "w"):
        # the CANONICAL spelling specifically, not merely some alias:
        # edit_metadata folds every known spelling to it before dispatch, so a
        # writer that takes only its own alias would reject the very name the
        # ledger hands it.
        assert field in live, (
            f"the ledger says {fmt} holds {field!r} and the writer accepts "
            f"only {sorted(set(M.CANONICAL[field][0]) & live)}")


@pytest.mark.parametrize("fmt", sorted(_LIVE_MAPS))
def test_every_field_the_writer_accepts_is_in_the_ledger(fmt):
    """The other direction, and the one that catches a new format quietly
    inventing a name. A writer field with no canonical entry is a field
    nothing else can ever map to."""
    unknown = {f for f in _LIVE_MAPS[fmt] if M.canonical(f) is None}
    assert not unknown, (
        f"{fmt} accepts {sorted(unknown)}, which the ledger does not name. "
        f"Add them to CANONICAL, as an alias if they mean something it "
        f"already has.")


# a value of the right shape per kind. The first draft used "probe" for
# everything and struct.pack refused it for bpm, which is how the kind column
# got added to the ledger in the first place.
_PROBE = {M.TEXT: "probe", M.NUMBER: "120", M.NOTE: "60",
          M.DATE: "2026-09-12", M.TIME: "12:00:00", M.KEY: "Am"}


def test_a_bound_field_really_writes(tmp_path):
    """The map check above is a proxy. This is the real thing, on the two
    containers that can be built in a line: set every bound field, read the
    file back, and require the writer to have accepted it."""
    for fmt, name in (("wav", "a.wav"), ("aiff", "a.aif")):
        raw = _minimal(fmt)
        for field in M.fields_for(fmt, "w"):
            p = tmp_path / name
            p.write_bytes(raw)
            probe = _PROBE[M.kind_of(field)]
            try:
                res = edit_metadata(str(p), {field: probe})
            except EditError as e:
                pytest.fail(f"{fmt} rejected bound field {field!r}: {e}")
            assert res.applied, f"{fmt} accepted {field!r} and changed nothing"


# ── every editable container, against the ledger ────────────────────
#
# The pins above check a writer's field MAP. That is a proxy, and it missed
# three containers sharing one row: `ni` borrowed Bitwig's row, so `formats
# --fields ni` offered tags, device and category to three editors that take
# none of them, and the tagged row offered `key` to an mp4 whose easy
# interface had no key at all. The map said yes; the file said no.
#
# This is the real check: one specimen per container kind behind every
# format `formats` lists as editable, every field the ledger calls writable
# set through the one front door, and every canonical field it does not
# call writable refused as a bad --set.

def _bitwig_full():
    """A Bitwig preset holding every field the editor can change: it edits a
    field in place, so a specimen without one cannot take it at all."""
    def field(key, val):
        return (struct.pack(">I", len(key)) + key + b"\x08"
                + struct.pack(">I", len(val)) + val)
    return b"BtWg0003000200" + b"".join(
        field(k, b"seed") for k in (b"creator", b"comment", b"tags",
                                    b"device_name", b"device_category",
                                    b"preset_category"))


def _fixture(name):
    from conftest import SMALL_FIXTURES
    import os
    with open(os.path.join(SMALL_FIXTURES, name), "rb") as fh:
        return fh.read()


def _specimens():
    import seeds
    return {
        "wav": [("a.wav", lambda: _minimal("wav"))],
        "aiff": [("a.aif", lambda: _minimal("aiff"))],
        "aifc": [("a.aifc", seeds.aifc)],
        "flac": [("a.flac", seeds.flac)],
        "mp3": [("a.mp3", seeds.mp3)],
        # the ogg and mp4 seeds are walker seeds mutagen will not open; the
        # committed fixtures are real files it does
        "ogg": [("a.ogg", lambda: _fixture("tone.ogg")),
                ("a.opus", lambda: _fixture("tone.opus"))],
        "mp4": [("a.m4a", lambda: _fixture("tone.m4a"))],
        "vital": [("a.vital", seeds.vital)],
        "bitwig": [("a.bwpreset", _bitwig_full)],
        "ni": [("a.nksf", seeds.ni), ("a.nmsv", seeds.ni_hsin),
               ("a.ksd", seeds.ni_ksd)],
    }


def _specimen_cases():
    return [(fmt, name) for fmt, specs in sorted(_specimens().items())
            for name, _build in specs]


def _build(fmt, name):
    return dict(_specimens()[fmt])[name]()


def test_every_editable_format_has_a_specimen():
    """A format `formats` calls editable with no specimen here is a format
    whose ledger row nothing holds to its editor."""
    from acidcat.commands import formats
    assert set(_specimens()) == formats._EDIT


@pytest.mark.parametrize("fmt,name", _specimen_cases())
def test_every_writable_field_is_taken_by_every_container(fmt, name):
    pytest.importorskip("mutagen")
    data = _build(fmt, name)
    refused = {}
    for field in M.fields_for(fmt, "w"):
        try:
            res = edits.edit_metadata_data(data, name,
                                           {field: _PROBE[M.kind_of(field)]})
        except EditError as e:
            refused[field] = str(e)
            continue
        assert res.applied, f"{name} took {field!r} and changed nothing"
    assert not refused, (
        f"`formats --fields {fmt}` calls these writable and the {name} "
        f"editor refuses them: {refused}")


@pytest.mark.parametrize("fmt,name", _specimen_cases())
def test_a_field_the_format_cannot_hold_is_a_bad_value(fmt, name):
    """The other half of agreeing: a canonical field the ledger does not
    offer is a --set the file cannot take (exit 2), not an edit refused
    (exit 1)."""
    pytest.importorskip("mutagen")
    data = _build(fmt, name)
    wrong = {}
    for field in sorted(set(M.CANONICAL) - set(M.fields_for(fmt, "w"))):
        try:
            edits.edit_metadata_data(data, name,
                                     {field: _PROBE[M.kind_of(field)]})
        except edits.BadValue:
            continue
        except EditError as e:
            wrong[field] = f"{type(e).__name__}: {e}"
            continue
        wrong[field] = "accepted"
    assert not wrong, (
        f"{name}: fields the ledger does not offer for {fmt}, and not "
        f"refused as a bad --set: {wrong}")


def test_ni_has_its_own_row():
    """NI's name is the preset's, so it binds title, which Bitwig's row
    (whose name is the device's) deliberately does not."""
    assert M.fields_for("ni", "w") == ("artist", "comment", "description",
                                       "title")
    assert "title" not in M.fields_for("bitwig")


def test_mp4_key_is_the_freeform_atom_the_reader_reads(tmp_path):
    """iTunes has no key atom. The writer uses the freeform one the MP4 tag
    reader already looks in first, so a key set is a key shown."""
    pytest.importorskip("mutagen")
    from acidcat.core.tagged import read_tags
    res = edits.edit_metadata_data(_fixture("tone.m4a"), "a.m4a",
                                   {"key": "Am"})
    p = tmp_path / "k.m4a"
    p.write_bytes(res.data)
    assert read_tags(str(p)).get("key") == "Am"
    assert b"initialkey" in res.data


# ── what the table buys ─────────────────────────────────────────────

def test_aiff_collisions_are_declared():
    """AIFF has three text chunks and more ideas than that to put in them, so
    comment, description and annotation all land in ANNO. Setting one replaces
    another. That is a fact about the format, and the ledger states it rather
    than letting a caller discover it."""
    got = M.collisions("aiff")
    assert got.get("ANNO") == ["annotation", "comment", "description"], got


def test_wav_has_no_collisions():
    """The control. WAV has a chunk per idea, so nothing shares a destination
    -- and a future edit that makes two fields collide will fail here."""
    assert M.collisions("wav") == {}


def test_moving_from_wav_to_flac_reports_what_is_lost():
    """The lossiness of a cross-format move, computed instead of remembered.
    WAV carries broadcast and sampler fields that a Vorbis comment has no
    place for."""
    lost = M.lost_moving("wav", "flac")
    assert "originator" in lost and "root_note" in lost
    # and the ordinary descriptive fields survive
    assert "title" not in lost and "artist" not in lost


def test_a_tagged_format_answers_for_itself():
    """A reader asking about .flac should not be told to go and look up what
    "tagged" means."""
    assert M.fields_for("flac") == M.fields_for("mp3")
    assert M.where("flac", "title") == "TITLE"
    assert M.where("mp3", "tempo") == "BPM"          # via the alias


def test_an_unknown_format_holds_nothing():
    assert M.fields_for("sf2") == ()
    assert M.where("sf2", "title") is None


# ── reading through the same bindings ───────────────────────────────

def test_read_metadata_round_trips_a_wav(tmp_path):
    """The counterpart to edit_metadata, built on the SAME bindings. A
    separate read table would be free to drift, and the asymmetry it produced
    is what this module exists to end: the writer handled thirteen formats and
    the tag reader eight, a different eight, so WAV metadata was reachable
    only by walking the file."""
    p = tmp_path / "rt.wav"
    p.write_bytes(_minimal("wav"))
    written = {"title": "Razor Boy", "artist": "Steely Dan", "genre": "Rock",
               "tempo": "126", "root_note": "60", "originator": "acidcat"}
    for field, value in written.items():
        res = edit_metadata(str(p), {field: value})
        p.write_bytes(res.data)

    got = M.read_metadata(str(p))
    assert got["title"] == "Razor Boy"
    assert got["artist"] == "Steely Dan"
    assert got["genre"] == "Rock"
    assert got["originator"] == "acidcat"       # bext, not LIST/INFO
    assert float(got["bpm"]) == 126.0           # acid, and `tempo` folded to it
    assert int(got["root_note"]) == 60          # smpl


def test_an_unset_chunk_field_is_not_reported_as_set(tmp_path):
    """Chunks are fixed-size, so an unset field is not absent -- it is zero.
    The acid chunk carries a root note of 0 whether the key is unset or is
    C-1, and reporting the first would graft a "0" into the next file as
    though someone had chosen it."""
    p = tmp_path / "u.wav"
    p.write_bytes(_minimal("wav"))
    res = edit_metadata(str(p), {"bpm": "120"})   # writes acid, leaves key unset
    p.write_bytes(res.data)
    got = M.read_metadata(str(p))
    assert "bpm" in got
    assert "key" not in got, got


def test_reading_an_unsupported_format_is_empty_not_an_error(tmp_path):
    """A courtesy read, the same one read_tags already extends."""
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x00" * 64)
    assert M.read_metadata(str(p)) == {}


def test_read_and_write_agree_about_which_formats_they_serve():
    """The asymmetry, pinned. Anything the ledger says is writable must also
    be readable, because both now consult the same table."""
    for fmt, binds in M.BINDINGS.items():
        assert binds, fmt
        # every binding is complete enough to read through
        for field, bind in binds.items():
            assert bind.label and bind.key, (fmt, field)


# ── access modes, and the round trip that found them ────────────────

def test_the_reader_key_is_the_readers_not_the_writers():
    """`Bind.key` is consulted only by read_metadata -- the writers have their
    own maps and never look at this table. A binding that borrows a writer's
    spelling therefore reads nothing and looks perfectly fine.

    `track` was exactly that: stored here as `tracknumber`, which is what the
    writer accepts, while the tag reader emits `track_number`. It wrote, it
    never read back, and nothing noticed."""
    assert M.binding("flac", "track").key == "track_number"


@pytest.mark.parametrize("fmt", ["wav", "aiff"])
def test_every_writable_field_reads_back(fmt, tmp_path):
    """The pin that makes a divergence impossible to ship quietly.

    Write each writable field, read it back through the ledger, and require it
    to appear. Three fields disagreed the first time this ran, and each was a
    different problem: a wrong reader key, a field the reader cannot see, and
    fields with no writer at all. None of them were visible without this.
    """
    raw = _minimal(fmt)
    unreadable = []
    for field in M.fields_for(fmt, "w"):
        if not M.readable(fmt, field):
            continue                       # declared write-only; see below
        p = tmp_path / f"rt.{fmt}"
        p.write_bytes(raw)
        res = edit_metadata(str(p), {field: _PROBE[M.kind_of(field)]})
        p.write_bytes(res.data)
        if field not in M.read_metadata(str(p), fmt=fmt):
            unreadable.append(field)
    assert not unreadable, (
        f"{fmt} writes {unreadable} and cannot read them back. Either the "
        f"binding's reader key is wrong, or the field is genuinely write-only "
        f"and should say access='w'.")


def test_write_only_and_read_only_are_declared_not_accidental():
    """The three states have to be distinguishable, because a field that
    writes and does not read back is either a bug or a fact about the format
    and they look identical until somebody says which."""
    # written, and the tag reader does not surface it
    assert M.writable("flac", "albumartist")
    assert not M.readable("flac", "albumartist")
    # readable, with no writer behind them
    for field in ("copyright", "encoder", "disc"):
        assert M.readable("flac", field)
        assert not M.writable("flac", field)
    # and the ordinary case is still both
    assert M.readable("flac", "title") and M.writable("flac", "title")


def test_a_read_only_field_is_not_claimed_as_writable():
    """`--fields` and any caller filtering on writability must not offer a
    field the writer will refuse."""
    for fmt in ("flac", "wav"):
        for field in M.fields_for(fmt, "w"):
            assert M.writable(fmt, field), (fmt, field)


def test_read_only_fields_are_excluded_from_the_writer_pin():
    """The earlier pin requires every bound field to be accepted by the
    writer. Read-only fields are bound and deliberately are NOT, so the pin
    has to know the difference or adding one breaks the suite."""
    live = _LIVE_MAPS["wav"]
    for field in M.fields_for("wav", "r"):
        if not M.writable("wav", field):
            assert field not in live, (
                f"{field!r} is declared read-only and the writer accepts it")
