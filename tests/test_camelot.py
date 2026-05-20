"""Tests for the Camelot key helper."""

from acidcat.core import camelot


def test_parse_simple_major():
    assert camelot.parse_key("C") == (0, 0)
    assert camelot.parse_key("c") == (0, 0)
    assert camelot.parse_key("C major") == (0, 0)
    assert camelot.parse_key("C maj") == (0, 0)


def test_parse_simple_minor():
    assert camelot.parse_key("Am") == (9, 1)
    assert camelot.parse_key("A minor") == (9, 1)
    assert camelot.parse_key("a min") == (9, 1)
    assert camelot.parse_key("F#m") == (6, 1)


def test_parse_enharmonic():
    assert camelot.parse_key("C#") == camelot.parse_key("Db")
    assert camelot.parse_key("Eb") == camelot.parse_key("D#")


def test_parse_camelot_code():
    assert camelot.parse_key("8A") == (9, 1)  # Am
    assert camelot.parse_key("8B") == (0, 0)  # C


def test_parse_midi_note_like():
    # C4 style strings (stripped octave)
    assert camelot.parse_key("C4") == (0, 0)
    assert camelot.parse_key("F#3") == (6, 0)


def test_parse_capital_m_means_major():
    """B-3: Beatport, Mixed In Key, Serato and Rekordbox tag major keys
    with a trailing capital M (e.g. `CM`, `DM`). The current parser
    lowercases the suffix before the major/minor branch, so `CM` ->
    `cm` -> matches the minor literal `m`. find_compatible then
    returns harmonically wrong neighbors.

    All of these must resolve to major (mode = 0).
    """
    assert camelot.parse_key("CM") == (0, 0)
    assert camelot.parse_key("DM") == (2, 0)
    assert camelot.parse_key("EM") == (4, 0)
    assert camelot.parse_key("FM") == (5, 0)
    assert camelot.parse_key("GM") == (7, 0)
    # capital M with sharp/flat carries through
    assert camelot.parse_key("F#M") == (6, 0)
    assert camelot.parse_key("BbM") == (10, 0)
    # round-trips through Camelot too
    assert camelot.key_to_camelot("CM") == "8B"
    assert camelot.key_to_camelot("DM") == "10B"


def test_parse_invalid():
    assert camelot.parse_key("") is None
    assert camelot.parse_key(None) is None
    assert camelot.parse_key("ZZZ") is None


def test_key_to_camelot():
    assert camelot.key_to_camelot("Am") == "8A"
    assert camelot.key_to_camelot("C") == "8B"
    assert camelot.key_to_camelot("Fm") == "4A"
    assert camelot.key_to_camelot("Eb") == "5B"


def test_neighbors_cover_all_four():
    n = camelot.camelot_neighbors("8A")
    # same, relative, perfect fourth, perfect fifth
    assert n == ["8A", "8B", "7A", "9A"]


def test_neighbors_wrap():
    assert camelot.camelot_neighbors("1A") == ["1A", "1B", "12A", "2A"]
    assert camelot.camelot_neighbors("12B") == ["12B", "12A", "11B", "1B"]


def test_compatible_keys_am():
    compat = camelot.compatible_keys("Am")
    # Am(8A) -> 8A, 8B, 7A, 9A = Am, C, Dm, Em
    assert compat == {"Am", "C", "Dm", "Em"}


def test_compatible_keys_unparseable():
    assert camelot.compatible_keys("xyz") == set()
    assert camelot.compatible_keys(None) == set()


def test_enharmonic_spellings_roundtrip():
    # B and Cb are enharmonically the same pitch
    assert "Cb" in camelot.enharmonic_spellings("B")
    assert "B" in camelot.enharmonic_spellings("Cb")

    # C# and Db are enharmonic
    assert "Db" in camelot.enharmonic_spellings("C#")
    assert "C#" in camelot.enharmonic_spellings("Db")

    # Minor keys preserve suffix
    assert "Cbm" in camelot.enharmonic_spellings("Bm")
    assert "Bm" in camelot.enharmonic_spellings("Cbm")


def test_enharmonic_spellings_unparseable():
    assert camelot.enharmonic_spellings("xyz") == set()
    assert camelot.enharmonic_spellings(None) == set()
