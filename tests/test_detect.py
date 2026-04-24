"""Tests for filename/path-based key and BPM parsing."""

from acidcat.core.detect import (
    parse_bare_key_token,
    parse_key_from_path,
    parse_key_from_filename,
    parse_bpm_from_filename,
)


def test_bare_token_major():
    assert parse_bare_key_token("A") == "A"
    assert parse_bare_key_token("C#") == "C#"
    assert parse_bare_key_token("Db") == "C#"  # flat -> sharp normalization
    assert parse_bare_key_token("Bb") == "A#"


def test_bare_token_cb_fb_enharmonic():
    assert parse_bare_key_token("Cb") == "B"
    assert parse_bare_key_token("Fb") == "E"
    assert parse_bare_key_token("Cbm") == "Bm"
    assert parse_bare_key_token("Fbm") == "Em"


def test_bare_token_minor():
    assert parse_bare_key_token("Am") == "Am"
    assert parse_bare_key_token("F#m") == "F#m"
    assert parse_bare_key_token("Ebm") == "D#m"


def test_bare_token_rejects_non_keys():
    assert parse_bare_key_token("Analog") is None
    assert parse_bare_key_token("Hypnotize") is None
    assert parse_bare_key_token("Break") is None
    assert parse_bare_key_token("") is None
    assert parse_bare_key_token("127") is None
    assert parse_bare_key_token("A#m7") is None  # not a whole key token


def test_path_filename_bare_key():
    assert parse_key_from_path("/loops/PL_Hypnotize_03_126_A#.wav") == "A#"
    assert parse_key_from_path("/loops/kick_120_Am.wav") == "Am"


def test_path_parent_folder_bare_key():
    # key in parent folder, not filename
    path = "/samples/PL_Hypnotize_03_126_A#/Drums/kick.wav"
    assert parse_key_from_path(path) == "A#"


def test_path_grandparent_folder():
    # key two levels up; max_parent_depth=2 by default
    path = "/packs/PL_Hypnotize_03_126_A#/Drums/Raw/kick.wav"
    assert parse_key_from_path(path) == "A#"


def test_path_suffix_style_matches_existing():
    # existing parse_key_from_filename handles 'Am' with suffix styles too
    assert parse_key_from_path("/loops/pad_in_A minor.wav") == "Am"
    assert parse_key_from_path("/loops/pad_in_C_major.wav") == "C"


def test_path_no_key_returns_none():
    assert parse_key_from_path("/loops/Analog_Break_fx.wav") is None
    assert parse_key_from_path("/loops/kick.wav") is None


def test_parse_bpm_existing():
    # sanity: existing bpm parser still works
    assert parse_bpm_from_filename("/loops/PL_Hypnotize_03_126_A#.wav") == 126
    assert parse_bpm_from_filename("/loops/kick.wav") is None
