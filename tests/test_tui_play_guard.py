"""The TUI must not blast noise at you for pressing a key.

Every field node in the chunk tree is a selectable, playable region, and almost
none of them are audio. Pressing play on a header, a tag or a text field
reinterpreted the bytes as PCM and produced a burst of full-scale noise at
whatever volume the user happened to be on -- a real hazard with headphones, and
trivially easy to hit by arrowing through the tree.

Two halves: the audio chunk is marked in the tree so you can see it before
pressing anything, and playing anything else asks first.

Structural, not statistical: inside a walked container the chunk id IS the
answer, and no heuristic beats it.
"""

import math
import struct

import pytest

from acidcat.core.walk import walk_file
from acidcat.tui_app.app import AcidcatTUI


def _wav(path, frames=2000):
    pcm = b"".join(struct.pack("<h", int(9000 * math.sin(i / 30.0)))
                   for i in range(frames))
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


class _Probe:
    """The three methods under test, bound without standing up a Textual app --
    they are pure functions of `self.chunks` and the caps the model holds for
    them (2.0: the audio chunk is the one with an `audio` cap)."""
    _audio_span = AcidcatTUI._audio_span
    _region_is_audio = AcidcatTUI._region_is_audio
    _chunk_name_at = AcidcatTUI._chunk_name_at

    def __init__(self, chunks, label="RIFF/WAVE"):
        from acidcat.core.infra import capabilities
        from acidcat.tui_app.model import DocumentModel
        self.chunks = chunks
        self.model = DocumentModel()
        self.model.chunk_caps = capabilities.caps(None, label, chunks)


@pytest.fixture
def probe(tmp_path):
    _, chunks, _ = walk_file(_wav(tmp_path / "t.wav"))
    return _Probe(chunks)


def test_the_audio_span_is_the_data_payload(probe):
    lo, hi = probe._audio_span()
    data = [c for c in probe.chunks if str(c["id"]).strip() == "data"][0]
    assert lo == data.get("payload_base", data["offset"] + 8)
    assert hi - lo == data["size"]


def test_inside_the_payload_is_audio(probe):
    lo, _ = probe._audio_span()
    assert probe._region_is_audio(lo + 100, 500) is True


@pytest.mark.parametrize("what,off,length", [
    ("the RIFF header", 0, 12),
    ("the fmt chunk", 12, 16),
])
def test_headers_are_not_audio(probe, what, off, length):
    assert probe._region_is_audio(off, length) is False, what


def test_a_selection_starting_slightly_early_still_counts(probe):
    """Most, not all. Demanding containment would nag on every ordinary drag
    that clips a few bytes of the chunk header."""
    lo, _ = probe._audio_span()
    assert probe._region_is_audio(lo - 4, 500) is True


def test_a_mostly_outside_selection_does_not_count(probe):
    lo, _ = probe._audio_span()
    assert probe._region_is_audio(lo - 400, 500) is False


def test_no_located_pcm_reports_unknown_not_safe():
    """None means "no walker located raw PCM", which is NOT the same as safe.

    This used to be read as a pass: action_play tested `is_audio is False`, so
    None fell straight through to playback with no prompt. AUDIO_SAMPLE_IDS is
    {data, SSND, BODY}, so EVERY compressed format -- mp3, flac, ogg, opus, the
    tracker family -- returned None and played full-scale noise silently. The
    caller must warn on None; see test_action_play_warns_when_no_pcm_was_located.
    """
    assert _Probe([])._region_is_audio(0, 100) is None


def test_a_parent_region_that_opens_on_a_header_is_not_audio(probe):
    """The reported bug: selecting the parent node plays the header first.

    Playback starts AT the region offset, so a span covering the whole file
    overlaps the audio by well over half and still opens with noise. The old
    test was "is most of this audio"; the right one is "does it START in the
    audio", because that is what you hear.
    """
    lo, hi = probe._audio_span()
    whole_file = probe._region_is_audio(0, hi)
    assert whole_file is False, "a region starting at byte 0 opens with the RIFF header"


def test_the_data_chunk_node_still_plays_without_nagging(probe):
    """The tolerance that has to survive: the tree's data node starts at the
    chunk header, 8 bytes before the payload, and that is genuinely audio."""
    data = [c for c in probe.chunks if str(c["id"]).strip() == "data"][0]
    assert probe._region_is_audio(data["offset"], data["size"]) is True


def test_the_prompt_names_the_chunk(probe):
    """"'fmt' is not the audio payload" is actionable; "this region" is not."""
    assert probe._chunk_name_at(12) == "'fmt'"
    lo, _ = probe._audio_span()
    assert probe._chunk_name_at(lo + 10) == "'data'"


def test_aiff_and_8svx_payloads_are_recognised():
    """The set is shared with the rest of the tool, so it must not be WAV-only."""
    from acidcat.core.infra.sniff import AUDIO_SAMPLE_IDS
    assert {"data", "SSND", "BODY"} <= AUDIO_SAMPLE_IDS
    # smpl describes the audio rather than being it
    assert "smpl" not in AUDIO_SAMPLE_IDS
