"""Every bound key must be reachable, and must say something when it declines.

Two defects found by actually pressing keys, which no reviewer had done -- the
Wave 1 audit recorded that all three stances skipped the TUI:

  `tab` was unreachable. Textual binds tab to focus_next at the screen level and
  consumed it first, so hex-edit mode could not be entered by the key its own
  help screen documents. `shift+tab` had the same problem.

  The hex pane could not be scrolled at all. Focus starts on the tree and never
  left it, so arrows always drove the tree, while #hexwrap sat there holding up
  to _HEX_CAP bytes -- far more than one screen.

And three keys did nothing while saying nothing, which is indistinguishable
from being broken. That is the same defect class as a silent cap, in the
smallest possible form.
"""

import pathlib
import asyncio
import os
import shutil

import pytest

from acidcat.tui_app.app import AcidcatTUI

pytest.importorskip("textual")

from conftest import CORPUS_WAV as WAV
from conftest import until as _until


@pytest.fixture
def wav(tmp_path):
    if not os.path.isfile(WAV):
        pytest.skip("test corpus WAV not present")
    p = tmp_path / "t.wav"
    shutil.copyfile(WAV, p)
    return str(p)


def _run(scenario):
    asyncio.run(scenario())


def test_hex_edit_is_reachable_by_its_key(wav):
    """It was on `tab`, which Textual's focus_next ate before the binding -- and
    calling action_hex_focus() directly always worked, so every existing test
    passed. It is ctrl+e now: tab means move focus, and entering an edit mode
    should take a modifier."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            await pilot.press("down")
            # ctrl+e declines unless the cursor sits on a node with an editable
            # byte range, and declining leaves _hexedit None -- so a `down`
            # that had not landed yet produced a failure reading "ctrl+e did
            # not enter hex edit" when ctrl+e was never given anything to edit.
            assert await _until(pilot, lambda: bool(app._meta(app._cur_node))),                 "the cursor never reached an editable node"
            await pilot.press("ctrl+e")
            await _until(pilot, lambda: app._hexedit is not None)
            assert app._hexedit is not None, "ctrl+e did not enter hex edit"
    _run(scenario)


def test_arrows_move_the_hex_edit_cursor(wav):
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("ctrl+e")
            await pilot.pause()
            assert app._hexedit is not None
            for k in ("right", "right", "down"):
                await pilot.press(k)
            await pilot.pause()
            assert app._hexedit["cur"] > 0
    _run(scenario)


def test_the_hex_pane_can_be_focused_and_scrolled(wav):
    """It holds more than a screen and focus never reached it. 2.0: the pane
    is drawn to its own height and does not scroll; the arrows move its
    window through the file a row at a time."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            assert app._focused_pane() == "hexwrap", "tab did not move focus to the hex pane"
            first = app.query_one("#hex").render().plain.splitlines()[0]
            for _ in range(6):
                await pilot.press("down")
            await pilot.pause()
            now = app.query_one("#hex").render().plain.splitlines()[0]
            step = app._hex_width()
            assert int(now.split()[0], 16) == int(first.split()[0], 16) + 6 * step, (
                "arrows did not move the focused pane's window")
            assert app._focused_pane() == "hexwrap", "the arrows moved focus"
    _run(scenario)


def test_zoom_zooms_the_focused_pane_not_a_fixed_order(wav):
    """Reported: z walked its own sequence instead of zooming what you were
    looking at."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("z")             # tree has focus at start
            await pilot.pause()
            assert app._zoom == "zoom-tree"
            await pilot.press("z")             # toggles off
            await pilot.pause()
            assert app._zoom is None
            await pilot.press("tab")           # now the hex pane
            # z zooms whatever has focus, so pressing it before tab has moved
            # focus zooms the tree, and the assertion below then reads as a
            # zoom bug rather than as the missed keystroke it is.
            assert await _until(pilot,
                                lambda: app._focused_pane() == "hexwrap"),                 "tab never moved focus to the hex pane"
            await pilot.press("z")
            await _until(pilot, lambda: app._zoom is not None)
            assert app._zoom == "zoom-hex"
            assert app.query_one("#hexwrap").size.width >= 76
    _run(scenario)


def test_zoom_focuses_what_it_zoomed(wav):
    """Zooming a pane you then cannot drive is half a feature."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("z")
            await pilot.pause()
            assert app._focused_pane() == "hexwrap"
    _run(scenario)


@pytest.mark.parametrize("key,expect", [
    ("full_stop", "nothing is playing"),
    # `u` is navigation now, not "show the region list": it goes back to the
    # view you descended from. At the top of the trail there is nowhere to go.
    ("u", "nothing to go back to"),
    ("U", "nothing to go forward to"),
    ("ctrl+t", "no field is being edited"),
])
def test_a_key_that_declines_says_why(wav, key, expect):
    """These three changed nothing and printed nothing. A key that is silent
    when it refuses is indistinguishable from one that is broken -- which is
    precisely how the unreachable tab presented."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            notes = []
            app.notify = lambda m, **kw: notes.append(str(m))
            await pilot.press(key)
            await pilot.pause()
            assert any(expect in n for n in notes), f"{key} said {notes}"
    _run(scenario)


def test_stopping_playback_internally_stays_quiet(wav):
    """The three internal callers are cleanup before a new sound, not a user
    pressing '.', so they must not narrate."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            notes = []
            app.notify = lambda m, **kw: notes.append(str(m))
            app.action_stop_play(quiet=True)
            await pilot.pause()
            assert not [n for n in notes if "nothing is playing" in n]
    _run(scenario)


def test_expand_and_collapse_actually_move_the_tree(wav):
    """Guarding against a harness blind spot: a sweep that only watches scalar
    app state calls these dead, because what they change is the tree."""
    def expanded(app):
        n = 0
        def walk(node):
            nonlocal n
            if node.is_expanded:
                n += 1
            for c in node.children:
                walk(c)
        walk(app.query_one("#tree").root)
        return n

    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            base = expanded(app)
            await pilot.press("a")
            await pilot.pause()
            opened = expanded(app)
            assert opened > base
            await pilot.press("c")
            await pilot.pause()
            assert expanded(app) < opened
    _run(scenario)


def test_strip_asks_before_it_touches_anything(wav):
    """`s` used to discard every tag on the keypress. Every other unmodified
    key here reads; a mistyped `s` reaching for something else should not be
    a destructive edit."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            before = pathlib.Path(app.work).read_bytes()
            await pilot.press("s")
            await pilot.pause()
            assert len(app.screen_stack) > 1, "s did not ask"
            assert not app.dirty, "s edited before the answer"
            await pilot.press("n")
            await pilot.pause()
            assert not app.dirty, "declining still edited"
            assert pathlib.Path(app.work).read_bytes() == before
    _run(scenario)


def test_answering_yes_still_strips(wav):
    """The confirmation must not have broken the feature it guards."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert app.dirty, "confirming did not strip"
    _run(scenario)


def test_tab_does_not_focus_a_pane_hidden_by_zoom(wav):
    """Zoomed into the hex view, tab used to focus the tree -- which the zoom
    hides. The cursor moved, the hex jumped to a field the user had not picked,
    and nothing on screen explained it. Navigating blind reads as "I cannot
    change fields at all"."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("tab")
            await pilot.press("z")
            await pilot.pause()
            assert app._zoom == "zoom-hex"
            tree = app.query_one("#tree")
            before = tree.cursor_line
            await pilot.press("tab")
            await pilot.pause()
            assert app._focused_pane() == "hexwrap", "tab escaped into a hidden pane"
            await pilot.press("down")
            await pilot.pause()
            assert tree.cursor_line == before, "the hidden tree moved unseen"
    _run(scenario)


def test_tab_still_cycles_once_the_zoom_is_off(wav):
    """The skip must not break the ordinary case.

    Asserted as a full lap rather than a fixed sequence: the cycle gained the
    forensics panel (see test_tui_findings_panel.py) and will gain more, and a
    test that pins the exact next pane fails on every such addition while
    proving nothing about the property that matters -- that len(_PANES)
    presses return you to where you started, having visited each pane once.
    """
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.pause()
            start = app._focused_pane()
            assert start == "tree"
            seen = []
            for _ in range(len(app._PANES)):
                await pilot.press("tab")
                await pilot.pause()
                seen.append(app._focused_pane())
            assert seen[-1] == start, f"tab did not come back around: {seen}"
            assert sorted(seen) == sorted(app._PANES), (
                f"a lap missed or repeated a pane: {seen}")
    _run(scenario)


def test_the_map_key_declines_on_an_unwalked_file(tmp_path):
    """`m` is a shown footer binding and early-returned when nothing parsed.

    So on an unrecognised blob it changed nothing and said nothing, which is
    indistinguishable from a broken build -- on exactly the files a person
    opens this tool for. It could not join the table above because every
    fixture there is a valid WAV with chunks.
    """
    from acidcat.tui_app.app import AcidcatTUI
    p = tmp_path / "mystery.bin"
    p.write_bytes(bytes(range(256)) * 8)          # no magic any walker claims

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            assert not app.chunks, "specimen was walked, so this proves nothing"
            notes = []
            app.notify = lambda m, **kw: notes.append(str(m))
            await pilot.press("m")
            await pilot.pause()
            assert any("no byte map" in n for n in notes), notes
            assert len(app.screen_stack) == 1, "a map screen opened anyway"
    _run(scenario)
