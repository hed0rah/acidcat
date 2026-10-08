"""The TUI is a keyboard tool, and the loudness guard has to actually fire.

Three regressions, all reported from real use:

  Playing a parent node produced noise with no warning. The guard tested
  `is_audio is False`, and a format whose PCM could not be located returned
  None -- so every compressed format skipped the prompt entirely.

  The prompt itself was answerable only with a mouse. Its sole binding was
  escape, so "yes" had no key at all, on a dialog guarding a hazard.

  The unsaved-changes prompt had the same shape: save and discard were
  mouse-only, on the one dialog standing between a user and a lost edit.
"""

import struct

import pytest

from acidcat.tui_app.app import AcidcatTUI
from acidcat.tui_app.screens import ConfirmScreen, YesNoScreen


class _PlayProbe:
    """action_play without standing up a Textual app: record what it pushes."""
    _audio_span = AcidcatTUI._audio_span
    _region_is_audio = AcidcatTUI._region_is_audio
    _chunk_name_at = AcidcatTUI._chunk_name_at
    action_play = AcidcatTUI.action_play
    _decodable = AcidcatTUI._decodable
    # Play acts on the PAYLOAD now, which it asks the selected node for and
    # falls back to _cur_region when there is no node. These tests drive it at
    # the _cur_region level, so the fallback is the path under test -- borrow
    # the real resolver rather than teaching the app to tolerate a fake.
    _act_range = AcidcatTUI._act_range
    _info = AcidcatTUI._info
    _cur_node = None
    # Play asks whether the SELECTED BYTES decode before it reinterprets them
    # as PCM; the probe's work path does not exist, so they read as nothing
    # and never decode -- exercising the guard rather than the decoder.
    _play_temp = staticmethod(lambda *a, **kw: None)

    # Default to a format the player CANNOT decode. These tests are about the
    # guard that stops raw bytes being reinterpreted as PCM, and a decodable
    # format now takes a different path entirely -- it is handed to the decoder,
    # which produces music rather than the loud noise the guard exists to
    # prevent. See test_a_decodable_format_is_played_not_reinterpreted.
    def __init__(self, chunks, fmt="Serum preset", region=(0, 4096)):
        from acidcat.core.infra import capabilities
        from acidcat.tui_app.model import DocumentModel
        self.chunks = chunks
        self.fmt = fmt
        self.work = "unused-by-these-tests"
        # 2.0: play decides by the file's caps, which the model holds; the
        # same caps the app's model computes from the walk
        self.model = DocumentModel()
        self.model.chunk_caps = capabilities.caps(None, fmt, chunks)
        self._cur_region = (region[0], region[1], None)
        self.pushed = []
        self.played = []
        self.notes = []

    def push_screen(self, screen, callback=None):
        self.pushed.append(screen)

    def notify(self, msg, **kw):
        self.notes.append(msg)

    def _do_play(self, off, length):
        self.played.append((off, length))


@pytest.fixture(autouse=True)
def _pretend_audio_exists(monkeypatch):
    from acidcat.util import play
    monkeypatch.setattr(play, "have_audio", lambda: True)


def test_action_play_warns_when_no_pcm_was_located():
    """The reported bug, at the call site that had it.

    A compressed file has no chunk in AUDIO_SAMPLE_IDS, so the span is None.
    None is not False, so the old guard let it through and played noise.
    """
    p = _PlayProbe(chunks=[{"id": "frames", "offset": 45, "size": 3761}])
    p.action_play()
    assert p.played == [], "played without asking"
    assert len(p.pushed) == 1
    assert isinstance(p.pushed[0], YesNoScreen)
    assert "no raw PCM" in p.pushed[0].prompt


def test_action_play_warns_on_a_header_region():
    pcm = b"\x00\x01" * 2000
    chunks = [{"id": "fmt ", "offset": 12, "size": 16},
              {"id": "data", "offset": 36, "size": len(pcm), "payload_base": 44}]
    p = _PlayProbe(chunks, fmt="RIFF/WAVE", region=(12, 16))
    p.action_play()
    assert p.played == []
    assert "not the audio payload" in p.pushed[0].prompt


def test_action_play_does_not_nag_on_the_real_payload():
    pcm = b"\x00\x01" * 2000
    chunks = [{"id": "data", "offset": 36, "size": len(pcm), "payload_base": 44}]
    p = _PlayProbe(chunks, fmt="RIFF/WAVE", region=(44, len(pcm)))
    p.action_play()
    assert p.pushed == [], "prompted on the actual audio"
    assert p.played == [(44, len(pcm))]


def _keys(screen_cls):
    out = set()
    for b in screen_cls.BINDINGS:
        out.add(b[0] if isinstance(b, tuple) else b.key)
    return out


def test_the_loudness_prompt_answers_to_the_keyboard():
    assert {"y", "n", "escape"} <= _keys(YesNoScreen)


def test_the_loudness_prompt_actions_resolve():
    """A binding pointing at a missing action is a silent no-op in Textual."""
    for action in ("action_yes", "action_cancel"):
        assert callable(getattr(YesNoScreen, action, None)), action


def test_the_unsaved_changes_prompt_answers_to_the_keyboard():
    assert {"s", "d", "c", "escape"} <= _keys(ConfirmScreen)
    for action in ("action_save", "action_discard", "action_cancel"):
        assert callable(getattr(ConfirmScreen, action, None)), action


def test_the_footer_carries_the_common_keys():
    """Every binding used to show, so the footer overflowed and edit fell off
    the end -- the one a user reaches for most on a field."""
    shown = {}
    for b in AcidcatTUI.BINDINGS:
        if isinstance(b, tuple):
            shown[b[0]] = b[2]
        elif getattr(b, "show", True):
            shown[b.key] = b.description
    assert "e" in shown, "edit field is missing from the footer"
    assert {"e", "p", "slash", "g", "m", "question_mark", "q"} <= set(shown)
    # a footer that lists everything lists nothing
    assert len(shown) <= 14, f"footer shows {len(shown)} keys, too many to read"


def test_the_row_cap_is_reachable_not_just_counted():
    """A capped row is a part of the file you cannot walk to.

    The tree lists 400 per-element rows per chunk and then prints "... N more
    rows". An MP3 of a few seconds has 609 frames, so 209 of them were counted
    and unreachable. `+` raises the budget for that chunk.
    """
    from acidcat.tui_app.render import _ROW_CAP

    class _Probe:
        action_more_rows = AcidcatTUI.action_more_rows

        def __init__(self):
            self.chunks = [{"id": "frames", "rows": [{"n": i} for i in range(609)]}]
            self._rowbudget = {}
            self.notes = []
            self.loaded = 0
            # A node carries its own record now, so the "... more rows" marker
            # lives on the node instead of in a dict keyed on its id.
            self._node = type("N", (), {"data": None})()

        @staticmethod
        def _info(node):
            return getattr(node, "data", None)

        def query_one(self, sel):
            probe = self
            return type("T", (), {"cursor_node": probe._node})()

        def notify(self, msg, **kw):
            self.notes.append(msg)

        def _load(self):
            self.loaded += 1

    from acidcat.tui_app.app import NodeInfo
    p = _Probe()
    p._node.data = NodeInfo(0, 0, "#fff", kind="note", morerows=0)
    p.action_more_rows()
    assert p._rowbudget[0] == _ROW_CAP * 2
    assert p.loaded == 1
    assert "609" in p.notes[-1]

    # and it refuses politely when the cursor is not on a "more rows" line
    q = _Probe()
    q.action_more_rows()
    assert q._rowbudget == {} and q.loaded == 0
    assert "more rows" in q.notes[-1]


def test_a_decodable_format_is_played_not_reinterpreted():
    """An Ogg or an MP3 has no raw PCM anywhere in it, so there is no `data`
    node to point `p` at and hunting the tree for one cannot succeed. Warning
    about noise was the best answer available while the only option was
    reinterpreting bytes; handing the file to a decoder is a better one, and it
    needs no selection at all."""
    p = _PlayProbe(chunks=[{"id": "frames", "offset": 45, "size": 3761}],
                   fmt="Ogg")
    assert p._decodable() is True
    p2 = _PlayProbe(chunks=[{"id": "frames", "offset": 45, "size": 3761}],
                    fmt="MP3/MPEG audio")
    assert p2._decodable() is True


def test_a_pcm_format_is_not_treated_as_decodable():
    """A WAV's bytes ARE the audio; reinterpreting them is the right path and
    must not be diverted."""
    pcm = bytes([0, 1]) * 2000
    chunks = [{"id": "fmt ", "offset": 12, "size": 16},
              {"id": "data", "offset": 44, "size": len(pcm),
               "payload_base": 44}]
    p = _PlayProbe(chunks=chunks, fmt="RIFF/WAVE", region=(44, len(pcm)))
    assert p._decodable() is False
