"""An unknown file must not be a dead end in the TUI.

Two refusals left a user with nothing to do:

  A file no walker claims yields `unsupported`, an empty chunk list and a tree
  with a single root node. `inspect --force` on the CLI shows what each walker
  makes of it, which is the actual way into an unknown container -- and the TUI
  had no equivalent at all.

  Forensics refuses on anything over 64 MB, gated on the same flag that makes
  the file read-only. That is a resource decision, not a verdict, and it was
  stated as a flat refusal with no way to overrule it.

`F` answers whichever applies. Both stay hypotheses: a forced parse is a lead,
never an identification, because a walker parses at fixed offsets whether or
not the header is really its format.
"""

import asyncio
import struct

import pytest

pytest.importorskip("textual")

from acidcat.tui_app.app import AcidcatTUI          # noqa: E402
# One definition of the poller, in conftest. This file is where the
# lesson was learned; two other suites then made the same timing
# assumption, so the helper moved rather than being copied a third time.
from conftest import until as _until                # noqa: E402
from acidcat.tui_app.screens import ForcedScreen    # noqa: E402


def _run(scenario):
    asyncio.run(scenario())


@pytest.fixture
def unknown(tmp_path):
    """A blob no walker claims, with enough structure that several will still
    make something of it when forced."""
    p = tmp_path / "mystery.bin"
    p.write_bytes(b"TMOD\x0b2025.12.3.0" + bytes(range(256)) * 40)
    return str(p)


async def _settle(app, pilot):
    for _ in range(60):
        if not app._scanning:
            break
        await pilot.pause(0.1)
    while len(app.screen_stack) > 1:
        app.screen_stack[-1].dismiss(None)
        await pilot.pause()


def _forced(app):
    from acidcat.tui_app.screens import ForcedScreen as _FS
    return [s for s in app.screen_stack if isinstance(s, _FS)]


class TestForcedParse:
    def test_an_unknown_file_walks_as_unsupported_with_no_tree(self, unknown):
        """The precondition. Without it the rest of this file tests nothing."""
        async def scenario():
            app = AcidcatTUI(unknown)
            async with app.run_test(size=(150, 44)) as pilot:
                await _settle(app, pilot)
                assert app.fmt in ("unsupported", "walk failed")
                assert app.chunks == []
                assert app._unparsed()
        _run(scenario)

    def test_F_offers_the_candidates(self, unknown):
        async def scenario():
            app = AcidcatTUI(unknown)
            async with app.run_test(size=(150, 44)) as pilot:
                await _settle(app, pilot)
                app.action_force_parse()
                await _until(pilot, lambda: _forced(app))
                screens = _forced(app)
                assert screens, "F offered nothing on an unparsed file"
                assert screens[0].rows, "the candidate table was empty"
        _run(scenario)

    def test_choosing_a_candidate_rewalks_with_it(self, unknown):
        async def scenario():
            app = AcidcatTUI(unknown)
            async with app.run_test(size=(150, 44)) as pilot:
                await _settle(app, pilot)
                app.action_force_parse()
                await _until(pilot, lambda: _forced(app))
                screen = _forced(app)[0]
                pick = screen.rows[0]["format"]
                screen.dismiss(pick)
                await _until(pilot, lambda: app._fmt_override == pick)
                assert app._fmt_override == pick
                assert app.chunks, "the forced walk produced no chunks"
        _run(scenario)

    def test_the_forced_parse_is_labelled_as_one(self, unknown):
        """A forced parse invents structure readily. The view must never read
        as an identification."""
        async def scenario():
            app = AcidcatTUI(unknown)
            async with app.run_test(size=(150, 44)) as pilot:
                await _settle(app, pilot)
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                app._on_forced("wav")
                await pilot.pause(0.3)
                assert any("hypothesis" in n for n in notes), notes
        _run(scenario)

    def test_cancelling_changes_nothing(self, unknown):
        async def scenario():
            app = AcidcatTUI(unknown)
            async with app.run_test(size=(150, 44)) as pilot:
                await _settle(app, pilot)
                app._on_forced(None)
                await pilot.pause()
                assert app._fmt_override is None
        _run(scenario)

    def test_F_declines_on_a_file_that_parsed(self, tmp_path):
        """Forcing a walker onto a file a walker already claimed would replace
        a real answer with a guess."""
        body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
                + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
                + b"data" + struct.pack("<I", 8) + b"\x00" * 8)
        p = tmp_path / "real.wav"
        p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

        async def scenario():
            app = AcidcatTUI(str(p))
            async with app.run_test(size=(150, 44)) as pilot:
                await _settle(app, pilot)
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                app.action_force_parse()
                # A background round trip, waited on by its condition rather
                # than by a duration. The assert stays: `until` returns a
                # bool, so a timeout would otherwise read as a pass.
                await pilot.pause(0.1)
                await _until(pilot, lambda: not [s for s in app.screen_stack if isinstance(s, ForcedScreen)])
                assert not [s for s in app.screen_stack
                            if isinstance(s, ForcedScreen)]
                assert any("forces a walker only" in n for n in notes), notes
        _run(scenario)

    def test_the_override_belongs_to_the_view(self, unknown):
        """It is frame state: descending must not inherit a walker forced on
        the parent, and coming back must not lose it."""
        async def scenario():
            app = AcidcatTUI(unknown)
            async with app.run_test(size=(150, 44)) as pilot:
                await _settle(app, pilot)
                assert "_fmt_override" in app._FRAME_ATTRS
                app._fmt_override = "wav"
                app._regions = [{"kind": "carve", "format": None, "offset": 4,
                                 "end": 600, "length": 596}]
                app._blob_src = app.src
                app._descend(0)
                await pilot.pause()
                assert app._fmt_override is None, "inherited the parent's walker"
                app.action_nav_back()
                await pilot.pause()
                assert app._fmt_override == "wav", "lost the parent's walker"
        _run(scenario)


class TestForensicsOverride:
    @pytest.fixture
    def big(self, tmp_path):
        from acidcat.tui_app.render import _LARGE_FILE
        n = _LARGE_FILE + (1 << 20)
        p = tmp_path / "big.wav"
        body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
                + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
                + b"data" + struct.pack("<I", n))
        with open(p, "wb") as f:
            f.write(b"RIFF" + struct.pack("<I", len(body) + n) + body)
            block = b"\x00" * (1 << 20)
            for _ in range(n >> 20):
                f.write(block)
        return str(p)

    def test_the_refusal_says_it_can_be_overruled(self, big):
        """Announcing a bound that bit is half the job; saying what to do about
        it is the other half."""
        async def scenario():
            app = AcidcatTUI(big)
            async with app.run_test(size=(150, 44)) as pilot:
                await _settle(app, pilot)
                assert app._readonly
                assert app.findings == []
                assert "press F" in (app.scan_note or ""), app.scan_note
        _run(scenario)

    def test_F_runs_the_scan_anyway(self, big):
        async def scenario():
            app = AcidcatTUI(big)
            async with app.run_test(size=(150, 44)) as pilot:
                await _settle(app, pilot)
                app.action_force_parse()
                await _until(pilot, lambda: app._force_scan is True)
                assert app._force_scan is True
                assert app.scan_note is None, (
                    f"still refusing after an override: {app.scan_note}")
        _run(scenario)

    def test_the_override_does_not_make_the_file_writable(self, big):
        """Two different decisions rode on one flag. Scanning is a read; it
        must not unlock in-place edits of a file with no working copy."""
        async def scenario():
            app = AcidcatTUI(big)
            async with app.run_test(size=(150, 44)) as pilot:
                await _settle(app, pilot)
                app.action_force_parse()
                await _until(pilot, lambda: app._force_scan is True)
                assert app._readonly is True
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                assert app._apply_to_work(b"nope") is False
                assert any("read-only" in n for n in notes), notes
        _run(scenario)


class TestForcingDoesNotFreezeTheApp:
    """`F` tries every walker in turn and each one that gets far enough reads
    the file. Measured at 16.5 seconds on a 187 MB archive -- sixteen seconds
    of frozen app with no way to quit, which is the same freeze `_explore_into`
    runs on a worker to avoid, in a different place.
    """

    def test_it_returns_before_the_answer_does(self, unknown):
        async def scenario():
            app = AcidcatTUI(unknown)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                app.action_force_parse()
                # synchronously after the call: it has said something and has
                # NOT pushed the result, because the result does not exist yet
                assert any("one moment" in n or "trying every walker" in n
                           for n in notes), notes
                assert not [s for s in app.screen_stack
                            if isinstance(s, ForcedScreen)], (
                    "the candidate table appeared synchronously, so the walk "
                    "ran on the UI thread")
        _run(scenario)

    def test_the_answer_still_arrives(self, unknown):
        """Not blocking is only half of it."""
        async def scenario():
            app = AcidcatTUI(unknown)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                app.action_force_parse()
                for _ in range(100):
                    if [s for s in app.screen_stack
                            if isinstance(s, ForcedScreen)]:
                        break
                    await pilot.pause(0.1)
                assert [s for s in app.screen_stack
                        if isinstance(s, ForcedScreen)], "no answer ever came"
        _run(scenario)

    def test_an_answer_for_a_view_that_is_gone_is_dropped(self, unknown):
        async def scenario():
            app = AcidcatTUI(unknown)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                stale = app._generation
                app._load()
                await pilot.pause(0.2)
                app._forced_landed(stale, [{"format": "wav", "chunks": 1,
                                            "note": "", "complaint": ""}], None)
                await pilot.pause(0.2)
                assert not [s for s in app.screen_stack
                            if isinstance(s, ForcedScreen)], (
                    "a stale forced-parse result opened a screen over the "
                    "current view")
        _run(scenario)
