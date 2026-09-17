"""Every bound key does something, and says so when it cannot.

These are the checks that would have caught the last three rounds of TUI bugs
before they were found by hand. The pattern each time was the same: an action
existed, a test called it directly and passed, and the *key* never reached it --
or reached it in a state where it silently did nothing.

So this drives real key presses through the real app and asserts on observable
state, never on the action methods.
"""

import asyncio
import os
import shutil

import pytest

from acidcat.tui_app.app import AcidcatTUI

# ten seconds and up per test: run in the full suite, skipped in the edit loop
pytestmark = pytest.mark.slow


def _run_capture(coro_factory):
    """asyncio.run for a scenario that returns a value.

    The module's `_run` discards the return, and three tests already repeat the
    notify-capture idiom by hand; this keeps the new ones from being a fourth
    copy.
    """
    return asyncio.run(coro_factory())


def _run(coro_factory):
    asyncio.run(coro_factory())


@pytest.fixture
def wav(tmp_path):
    from conftest import CORPUS_WAV as src
    p = tmp_path / "t.wav"
    shutil.copyfile(src, p)
    return str(p)


# keys that only make sense mid-scan, or that end the session
_EXEMPT = {"q", "escape", "space", "enter"}

# Keys held dormant by check_action until a particular pane has focus. The
# sweep below presses every key from ONE state -- tree focused, hex view -- so
# a key that is deliberately asleep there reads as inert, which is what this
# file is built to flag.
#
# Exempting them is only legitimate because two things are checked instead, and
# both are asserted below rather than asserted here in prose:
# test_the_gated_keys_are_actually_gated proves they are asleep in the swept
# state on purpose, and tests/test_viz_scale_and_scope.py's
# TestArrowsOnAFocusedGraph presses each one in the state that arms it. An
# exemption pointing at another test is a redirect; one pointing at nothing is
# a hole.
#
# up/down are NOT here. They are gated for the graph too, but in the swept
# state they fall through to the tree cursor, so the sweep still bites on them.
# The region actions go the same way: check_action turns them off when nothing
# has been located, so in the swept state they are not bound rather than
# bound-and-silent.
#
# Each key names the file that presses it, because "exercised somewhere else"
# is only a redirect if the somewhere is checked. It was one shared filename
# when every gated key lived in one place; the region keys are armed by a
# different state and belong to a different file.
_GATED = {
    "left": "test_viz_scale_and_scope.py",
    "right": "test_viz_scale_and_scope.py",
    "space": "test_tui_one_vocabulary.py",
    "A": "test_tui_one_vocabulary.py",
    "X": "test_tui_one_vocabulary.py",
    "E": "test_tui_one_vocabulary.py",
}


def _bound_keys():
    keys = []
    for b in AcidcatTUI.BINDINGS:
        k = b[0] if isinstance(b, tuple) else b.key
        if k not in _EXEMPT and k not in _GATED:
            keys.append(k)
    return keys


@pytest.fixture(scope="module")
def keysweep(request):
    """Press every bound key once, in a fresh app each time, and record what it
    did. Module-scoped because it is the expensive part of this file -- one app
    per key rather than one per key per assertion.
    """
    from conftest import CORPUS_WAV as src
    import tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, "t.wav")
    shutil.copyfile(src, path)

    async def sweep():
        out = {}
        for key in _bound_keys():
            app = AcidcatTUI(path)
            async with app.run_test(size=(140, 44)) as pilot:
                await pilot.pause()
                await pilot.press("down")          # land on a real chunk
                await pilot.pause()
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                before = _snapshot(app)
                await pilot.press(key)
                await pilot.pause()
                changed = _snapshot(app) != before
                opened = len(app.screen_stack) > 1
                escaped = None
                if opened:
                    await pilot.press("escape")
                    await pilot.pause()
                    escaped = len(app.screen_stack) == 1
                out[key] = {"changed": changed, "spoke": bool(notes),
                            "modal": opened, "escaped": escaped}
        return out

    return asyncio.run(sweep())


def test_no_bound_key_is_silently_inert(keysweep):
    """A key that changes nothing and says nothing is indistinguishable from a
    broken build. That is exactly how `tab` shipped unreachable: Textual's
    focus_next consumed it, every test passed because they called the action
    directly, and pressing it did nothing at all."""
    inert = [k for k, r in keysweep.items() if not r["changed"] and not r["spoke"]]
    assert not inert, f"keys that did nothing and said nothing: {inert}"


def test_the_gated_keys_are_actually_gated(wav):
    """Justify _GATED from the code instead of from belief.

    If a key in that set stops being conditional -- someone drops the
    check_action clause, or rebinds it to something unconditional -- it silently
    leaves the sweep above with nothing checking it. This fails at that moment
    and says to shorten the set.
    """
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            actions = {}
            for b in AcidcatTUI.BINDINGS:
                key = b[0] if isinstance(b, tuple) else b.key
                if key in _GATED:
                    actions[key] = b[1] if isinstance(b, tuple) else b.action
            assert set(actions) == set(_GATED), (
                f"_GATED names keys that are not bound: "
                f"{set(_GATED) - set(actions)}")
            for key, action in actions.items():
                assert app.check_action(action, ()) is False, (
                    f"{key} is live in the swept state, so exempting it from "
                    f"the inert sweep leaves it unchecked")
    _run(scenario)


def test_every_gated_key_is_exercised_somewhere_else(wav):
    """The redirect half: the exemption names a file, so that file must press
    them. Cheap to check, and it fails if those tests are ever deleted."""
    import pathlib
    import re
    here = pathlib.Path(__file__).parent
    for key, filename in _GATED.items():
        other = here / filename
        assert other.is_file(), f"{key} redirects to {filename}, which is gone"
        text = other.read_text(encoding="utf-8")
        # Both spellings count, because both press the key. `press_until`
        # exists for keys a loaded runner can drop: it presses, waits for the
        # state, and presses again, so it exercises the binding at least as
        # hard as a bare `pilot.press`. Matching only the literal made this
        # guard fail the moment a flaky keypress was made robust -- which would
        # have taught the next person to weaken the exemption instead, and a
        # guard that punishes the fix is worse than no guard.
        #
        # A regex rather than `in`, because the call wraps across lines. The
        # first attempt was "press_until appears somewhere AND the key appears
        # somewhere", which passed for a key pressed nowhere at all: the name
        # occurs in docstrings and neighbouring tests, so the two halves met by
        # accident and the guard stopped guarding.
        pressed = bool(
            re.search(r'press\(\s*"%s"\s*\)' % re.escape(key), text)
            or re.search(r'press_until\(\s*pilot,\s*"%s"' % re.escape(key), text))
        assert pressed, (
            f"{key} is exempted here and pressed nowhere else "
            f"({filename} does not press it)")


def _expanded(tree):
    """How much of the tree is unfolded -- what `a` and `c` change, and the one
    piece of state a naive snapshot misses, which makes those two keys look
    inert when they are not."""
    n = 0
    stack = [tree.root]
    while stack:
        node = stack.pop()
        if getattr(node, "is_expanded", False):
            n += 1
            stack.extend(node.children)
    return n


def _snapshot(app):
    bar = app.query_one("#editbar")
    tree = app.query_one("#tree")
    return (
        len(app.screen_stack),
        app._view,
        app._zoom,
        getattr(app.focused, "id", None),
        app._hexedit is not None,
        app._edit_target is not None,
        app._prompt,
        app.dirty,
        tree.cursor_line,
        _expanded(tree),
        "hidden" not in bar.classes,
        # Panning the tree sideways moves nothing else, so a snapshot without
        # it reports ctrl+left/right as inert on a tree they genuinely scroll.
        # The x axis only: vertical scroll follows the cursor, so including it
        # would let any key that merely moves the cursor look like it did
        # something here -- which is the thing this snapshot exists to catch.
        tree.scroll_offset.x,
    )


def test_every_modal_closes_on_escape(keysweep):
    """A modal you cannot leave is a hang. It also silently disables every
    single-letter binding underneath it (check_action), so a stuck modal reads
    as "the whole app stopped responding to keys" -- which is exactly how a
    working `z` can look broken."""
    stuck = [k for k, r in keysweep.items() if r["modal"] and not r["escaped"]]
    assert not stuck, f"modals that escape does not close: {stuck}"


def test_zoom_works_from_either_pane_and_toggles_back(wav):
    """z is the key the whole layout depends on, and it is focus-sensitive --
    which makes it exactly the kind of thing that breaks quietly."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            start = app.query_one("#tree").region.width

            await pilot.press("z")                  # tree is focused
            await pilot.pause()
            assert app._zoom == "zoom-tree"
            assert app.query_one("#tree").region.width > start
            await pilot.press("z")
            await pilot.pause()
            assert app._zoom is None
            assert app.query_one("#tree").region.width == start

            await pilot.press("tab")                # hex pane
            await pilot.press("z")
            await pilot.pause()
            assert app._zoom == "zoom-hex"
            assert app.query_one("#hexwrap").region.width > start
            await pilot.press("z")
            await pilot.pause()
            assert app._zoom is None
    _run(scenario)


def test_a_zoomed_pane_actually_fills_the_screen(wav):
    """Zoom that leaves the other column occupying space is not zoom."""
    async def scenario():
        app = AcidcatTUI(wav)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            await pilot.press("z")
            await pilot.pause()
            assert app.query_one("#right").region.width == 0
            assert app.query_one("#left").region.width == 140
            await pilot.press("z")
            await pilot.press("tab")
            await pilot.press("z")
            await pilot.pause()
            assert app.query_one("#left").region.width == 0
            assert app.query_one("#right").region.width == 140
    _run(scenario)


def test_the_footer_only_advertises_keys_that_work(keysweep):
    """The footer is the contract. A key shown there that does nothing is
    worse than one that is not shown at all."""
    shown = []
    for b in AcidcatTUI.BINDINGS:
        if isinstance(b, tuple):
            shown.append(b[0])
        elif getattr(b, "show", True):
            shown.append(b.key)
    broken = [k for k in shown
              if k in keysweep
              and not keysweep[k]["changed"] and not keysweep[k]["spoke"]]
    assert not broken, f"footer advertises inert keys: {broken}"


# ── mounting must not scale with a hostile chunk count ─────────────

def _null_tailed_wav(path, tail_bytes):
    """A valid WAV with a run of nulls appended.

    The deep walker reads each 8-byte run of zeros as a zero-size chunk, so
    this is the cheapest way to make a file with an enormous chunk count. It is
    not contrived: a truncated or zero-padded file from a failed transfer looks
    exactly like this, and opening damaged files is the tool's purpose.
    """
    import struct
    pcm = b"\x00\x01" * 200
    body = (b"WAVE" + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body
                     + bytes(tail_bytes))
    return path


def test_a_huge_chunk_count_does_not_freeze_the_mount(tmp_path):
    """_ROW_CAP bounded rows WITHIN a chunk; nothing bounded the chunks.

    One Tree widget per chunk, built synchronously in on_mount: 65,538 chunks
    took 11.5 s, 262,146 took 46 s with nothing painted -- so `q` was not
    available -- and 8.5 million never finished. `inspect` renders the same
    file in 3.4 s, which is what makes this the TUI's bug and not the walker's.
    """
    import time
    from acidcat.tui_app.app import AcidcatTUI
    p = _null_tailed_wav(tmp_path / "tail.wav", 2 * 1024 * 1024)

    async def go():
        app = AcidcatTUI(str(p))
        t0 = time.perf_counter()
        async with app.run_test(size=(120, 40)) as pilot:
            elapsed = time.perf_counter() - t0
            nodes = len(app.query_one("#tree").root.children)
            await pilot.press("q")
        return elapsed, nodes

    elapsed, nodes = asyncio.run(go())
    # The node count is the real assertion. The bug was an unbounded chunk
    # count -- one Tree widget per chunk -- and `nodes <= 2001` catches that
    # deterministically on any machine.
    assert nodes <= 2001, f"{nodes} top-level nodes; the chunk cap is not applied"
    # The clock is only a hang detector. It was 10 s, which is a plausible
    # mount time on a loaded shared runner rather than a broken one: Windows CI
    # took 15.9 s and failed a green build. Raising a budget every time it
    # trips is how a floor stops meaning anything, so this is deliberately far
    # out of reach of a slow machine and still nowhere near the 46 s that the
    # unbounded version needed for 262,146 chunks.
    assert elapsed < 60.0, f"mount took {elapsed:.1f}s on a 2 MB file; that is a hang, not a slow runner"


def test_the_hidden_chunks_are_counted_and_reachable(tmp_path):
    """A silently shortened tree makes a truncated file look complete, which is
    worse than the freeze. The cap has to name what it hid, and `+` must reach
    past it -- the same contract the row cap already honours."""
    from acidcat.tui_app.app import AcidcatTUI
    p = _null_tailed_wav(tmp_path / "tail.wav", 512 * 1024)

    async def go():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=(120, 40)) as pilot:
            tree = app.query_one("#tree")
            labels = [str(c.label) for c in tree.root.children]
            named = any("more chunks" in x for x in labels)
            before = len(tree.root.children)
            hits = [c for c in tree.root.children if "more chunks" in str(c.label)]
            if hits:
                tree.move_cursor(hits[0])
                app.action_more_rows()
                await pilot.pause()
            after = len(app.query_one("#tree").root.children)
            await pilot.press("q")
        return named, before, after

    named, before, after = asyncio.run(go())
    assert named, "the hidden chunks are not named, so the tree looks complete"
    assert after > before, f"+ did not extend the chunk budget ({before} -> {after})"


# ── a cap must never be reported as the answer ──────────────────────

def _repeated_byte_wav(path, n=100_000, value=0x41):
    """A WAV whose payload is one byte repeated, so the match count is known."""
    import struct
    pcm = bytes([value]) * n
    body = (b"WAVE" + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 44100, 1, 8)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


def test_byte_search_reports_the_true_match_count(tmp_path):
    """The loop stopped at a bare literal 4096 and the notify printed
    len(hits), so 100,000 matches reported as "4096 match(es)" and n/N wrapped
    at 4096 with the rest of the file unreachable and unmentioned.

    This was the only cap in the app that was neither named nor disclosed.
    """
    from acidcat.tui_app.app import AcidcatTUI
    from acidcat.tui_app.render import _SEARCH_CAP
    p = _repeated_byte_wav(tmp_path / "many.wav")

    async def scenario():
        app = AcidcatTUI(p)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            notes = []
            app.notify = lambda m, **kw: notes.append(str(m))
            app._run_search("0x41")
            await pilot.pause()
            state = app._search
            await pilot.press("q")
        return notes, state

    notes, state = _run_capture(scenario)
    assert state["total"] > _SEARCH_CAP, "specimen no longer exceeds the cap"
    assert len(state["hits"]) == _SEARCH_CAP
    said = " ".join(notes)
    assert f"{state['total']:,} match(es)" in said, said
    assert "reachable" in said, said


def test_a_search_under_the_cap_says_nothing_extra(tmp_path):
    """The hedge must not fire when nothing was hidden."""
    from acidcat.tui_app.app import AcidcatTUI
    p = _repeated_byte_wav(tmp_path / "few.wav", n=10)

    async def scenario():
        app = AcidcatTUI(p)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            notes = []
            app.notify = lambda m, **kw: notes.append(str(m))
            app._run_search("0x41")
            await pilot.pause()
            await pilot.press("q")
        return notes, None

    notes, _ = _run_capture(scenario)
    assert notes and "reachable" not in " ".join(notes), notes


def test_pending_changes_counts_every_region(tmp_path):
    """_pending_changes stopped at _DIFF_CAP + 1 so the screen could print
    ".. 1 more regions", which made 201 the largest number it could ever
    report. 1,000 changed regions rendered as "201 region(s)" on the one screen
    a person consults before overwriting their file.
    """
    from acidcat.tui_app.app import AcidcatTUI
    from acidcat.tui_app.render import _DIFF_CAP
    src = _repeated_byte_wav(tmp_path / "edit.wav", n=8000, value=0x00)

    async def scenario():
        app = AcidcatTUI(src)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            # 1,000 isolated single-byte changes in the working copy
            with open(app.work, "rb") as f:
                buf = bytearray(f.read())
            start = len(buf) - 6000
            for k in range(1000):
                buf[start + k * 4] ^= 0xFF
            with open(app.work, "wb") as f:
                f.write(bytes(buf))
            out = app._pending_changes()
            await pilot.press("q")
        return out, None

    (regions, _sl, _wl, total), _ = _run_capture(scenario)
    assert total == 1000, f"reported {total} regions, planted 1000"
    assert len(regions) == _DIFF_CAP, "the list should still be capped"


def test_the_diff_screen_prints_the_true_region_count(monkeypatch):
    """The other half of the pending-changes fix: the app now computes a true
    total, and the screen has to render it rather than len(the capped list).

    Composed directly instead of driven, because the count is a property of the
    text and not of the interaction.
    """
    from acidcat.tui_app import screens as S
    from acidcat.tui_app.render import _DIFF_CAP

    class _Ctx:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(S, "Vertical", lambda **kw: _Ctx())
    monkeypatch.setattr(S, "Static", lambda t: t)
    regions = [(i * 4, b"\x00\x00", b"\x01\x01") for i in range(_DIFF_CAP)]

    out = list(S.DiffScreen(regions, 8000, 8000, total=1000).compose())[0]
    txt = out.plain if hasattr(out, "plain") else str(out)
    assert "1,000 region(s)" in txt, txt.splitlines()[:2]
    assert f"listing the first {_DIFF_CAP:,}" in txt
    assert "800 more regions" in txt

    # and no hedge when the cap did not bite
    out = list(S.DiffScreen(regions, 8000, 8000, total=_DIFF_CAP).compose())[0]
    txt = out.plain if hasattr(out, "plain") else str(out)
    assert "listing the first" not in txt
    assert "more regions" not in txt
