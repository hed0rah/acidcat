"""Tests for filename/path-based key and BPM parsing."""

import pytest

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


class TestLibrosaKeyDetection:
    """B-4: `estimate_librosa_metadata` derives the detected key from
    `np.argmax(chroma_median)`, which only returns the strongest pitch
    class. That tells us nothing about major vs minor. The rest of the
    system (camelot.parse_key, find_compatible) treats the bare letter
    as major by convention, so a file actually in A minor ends up
    routed to A major neighbors.

    Short-term fix: return None for detected_key. The caller's
    filename-based key parser (which carries mode explicitly) wins
    instead of a wrong-mode guess.
    """

    def test_chroma_peak_at_a_does_not_emit_a(self, tmp_path, monkeypatch):
        """Mock the librosa pipeline so chroma_cqt returns a vector that
        peaks at pitch class A (index 9). Assert that detected_key is
        None (not "A"), so the caller can fall back to filename parsing.
        """
        librosa = pytest.importorskip("librosa")
        np = pytest.importorskip("numpy")

        # build a chroma matrix that median-aggregates to a peak at A
        chroma = np.zeros((12, 8))
        chroma[9, :] = 1.0  # A

        # 1 second of zeros at 22050 Hz is enough to bypass the
        # "len(y) < 256" early-return without doing any real DSP
        fake_y = np.zeros(22050, dtype=np.float32)
        fake_sr = 22050

        monkeypatch.setattr(librosa, "load",
                             lambda *_a, **_k: (fake_y, fake_sr))
        monkeypatch.setattr(librosa.feature, "chroma_cqt",
                             lambda *_a, **_k: chroma)
        # bypass tempo path: return a deterministic tempo so we focus
        # the assertion on the key path
        monkeypatch.setattr(librosa.onset, "onset_strength",
                             lambda *_a, **_k: np.ones(10))
        monkeypatch.setattr(librosa.beat, "tempo",
                             lambda *_a, **_k: np.array([120.0]))

        from acidcat.core.detect import estimate_librosa_metadata
        # path with no key hint, so filename parser returns None and we
        # see only the chroma-derived detected_key
        result = estimate_librosa_metadata(
            str(tmp_path / "anonymous_loop.wav")
        )
        assert result.get("detected_key") is None
        # estimated_key should also be None (filename had none, chroma
        # returned none)
        assert result.get("estimated_key") is None


def test_parse_bpm_existing():
    # sanity: existing bpm parser still works
    assert parse_bpm_from_filename("/loops/PL_Hypnotize_03_126_A#.wav") == 126
    assert parse_bpm_from_filename("/loops/kick.wav") is None


def test_parse_bpm_rejects_letter_adjacent_digits():
    # Regression: pack identifier prefixes like '91V' should NOT match as
    # BPM 91. The parser must fall through to the real tempo marker.
    assert parse_bpm_from_filename(
        "/packs/91V_SBH_126_drum_fill_build_up_dont_stop.wav"
    ) == 126
    assert parse_bpm_from_filename(
        "/packs/91V_SBH_130_drum_fill_build_up_engine.wav"
    ) == 130
    # other letter-adjacent forms that should be rejected
    assert parse_bpm_from_filename("/packs/V99kick.wav") is None
    # existing valid forms still work
    assert parse_bpm_from_filename("/packs/120bpm_loop.wav") == 120
    assert parse_bpm_from_filename("/packs/loop_140_BPM.wav") == 140
    assert parse_bpm_from_filename("/packs/_120_drum.wav") == 120
