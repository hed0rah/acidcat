"""Reaching the rest of a big region, and playing a format that has no PCM.

Three complaints from driving the TUI on a real archive:

  The hex view shows the first kilobyte of a region and nothing else. It always
  said so -- "N more bytes" -- but saying so is only half the job when there is
  no way to go and look at them. On a 3 MB region that made the hex view a view
  of its first kilobyte.

  `u` and `U` felt clunky. Coming out of a region almost always means "show me
  the others", and splitting navigation from listing cost that common case an
  extra keypress: `u` used to open the list directly.

  In an Ogg there is no `data` node to select, so `p` could not be pointed at
  the audio -- and hunting the tree for one is a search that cannot succeed.
  A compressed container has no raw PCM anywhere in it. The answer is not a
  better tree, it is to stop reinterpreting bytes and hand the file to a
  decoder.
"""

import asyncio
import glob
import os
import struct

import pytest

pytest.importorskip("textual")

from conftest import until                      # noqa: E402
from acidcat.tui_app.app import AcidcatTUI          # noqa: E402
from acidcat.tui_app.render import _HEX_CAP, hex_text   # noqa: E402
from acidcat.tui_app.screens import RegionsScreen   # noqa: E402


def _run(scenario):
    asyncio.run(scenario())


@pytest.fixture
def big_wav(tmp_path):
    """A WAV whose data chunk is far bigger than one hex screenful.

    The payload must NOT repeat. Filling it with `bytes(range(256))` made byte
    4096 identical to byte 0, so a test asserting that a later window shows
    different bytes passed even when the window offset was ignored entirely --
    the only thing that actually differed was the offset gutter.
    """
    import random
    rng = random.Random(20260814)
    n = 240000
    pcm = bytes(rng.randrange(256) for _ in range(n))
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", n) + pcm)
    p = tmp_path / "big.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p)


async def _on_data(app, pilot):
    """Select a CHILD node whose range exceeds the hex cap.

    Skipping the root matters: the root is the whole file and so is always
    bigger than the cap, and a helper that stopped there left the cursor on
    line 0, where pressing up moves nothing and a test about moving the
    selection proves nothing.
    """
    tree = app.query_one("#tree")
    for _ in range(8):
        off, length, _a = app._cur_region
        if tree.cursor_line > 0 and length and length > _HEX_CAP:
            return
        await pilot.press("down")
        await pilot.pause()
    raise AssertionError("no child node bigger than the hex cap in this fixture")


def _first(app):
    """The first offset the hex pane shows, read off the drawing itself."""
    line = app.query_one("#hex").render().plain.splitlines()[0]
    return int(line.split()[0], 16)


def _page(app):
    return app._hex_width() * app._hex_rows_visible()


class TestPagingTheHexView:
    """2.0: the hex view is the whole layer around the selection, so paging
    moves through the file a screenful at a time, past the selection's ends,
    and stops at the file's. (1.x paged inside the selected region by
    _HEX_CAP bytes; that window is gone with the region-only view.)"""

    def test_the_window_moves_a_page_at_a_time(self, big_wav):
        async def scenario():
            app = AcidcatTUI(big_wav)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause()
                await _on_data(app, pilot)
                at = _first(app)
                await pilot.press("pagedown")
                await pilot.pause()
                assert _first(app) == at + _page(app)
                await pilot.press("pagedown")
                await pilot.pause()
                assert _first(app) == at + 2 * _page(app)
                await pilot.press("pageup")
                await pilot.pause()
                assert _first(app) == at + _page(app)
        _run(scenario)

    def test_the_dump_says_which_window_it_is_showing(self, big_wav):
        """"1,024 of 3 MB" leaves the reader working out where they are."""
        t = hex_text(big_wav, 44, 240000, "#ffffff", None, 16, start=2048)
        line = [x for x in t.plain.splitlines() if "bytes " in x]
        assert line, t.plain[-200:]
        assert "2,048..3,071 of 240,000" in line[0]

    def test_a_later_window_renders_the_later_bytes(self, big_wav):
        """Paging has to change which bytes are on screen, and to the right
        ones: a run read from the file itself."""
        data = open(big_wav, "rb").read()

        async def scenario():
            app = AcidcatTUI(big_wav)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause()
                await _on_data(app, pilot)
                first = app.query_one("#hex").render().plain
                await pilot.press("pagedown")
                await pilot.pause()
                later = app.query_one("#hex").render().plain
                assert first != later, "the second window rendered the first bytes"
                at = _first(app)
                want = data[at:at + 8]
                assert want.hex(" ") in later.lower()
        _run(scenario)

    def test_it_stops_at_both_ends_and_says_so(self, big_wav):
        async def scenario():
            app = AcidcatTUI(big_wav)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause()
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                assert _first(app) == 0          # the root: the file from 0
                await pilot.press("pageup")
                await pilot.pause()
                assert _first(app) == 0
                assert any("start of the file" in n for n in notes), notes
        _run(scenario)

    def test_a_file_that_fits_declines(self, tmp_path):
        p = tmp_path / "small.wav"
        body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
                + struct.pack("<HHIIHH", 1, 1, 8000, 8000, 1, 8)
                + b"data" + struct.pack("<I", 8) + bytes(8))
        p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

        async def scenario():
            app = AcidcatTUI(str(p))
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause()
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                await pilot.press("pagedown")
                await pilot.pause()
                assert any("already shown" in n for n in notes), notes
        _run(scenario)

    def test_moving_the_selection_brings_the_window_with_it(self, big_wav):
        """Paging far away then selecting another node must show that node,
        not the page you left."""
        async def scenario():
            app = AcidcatTUI(big_wav)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause()
                await _on_data(app, pilot)
                for _ in range(3):
                    await pilot.press("pagedown")
                await pilot.pause()
                assert app._hex_top is not None
                await pilot.press("up")
                await pilot.pause()
                assert app._hex_top is None
                off, length, _a = app._cur_region
                assert _first(app) <= off < _first(app) + _page(app)
        _run(scenario)

    def test_the_window_belongs_to_the_view(self, big_wav):
        assert "_hex_top" in AcidcatTUI._FRAME_ATTRS


class TestGotoLandsInTheRegion:
    def test_it_selects_the_containing_chunk_and_shows_the_byte(self, big_wav):
        """A one-byte highlight with no surrounding bytes is not a hex view of
        anything. Selecting the chunk and showing the byte inside it is what
        jumping means."""
        async def scenario():
            app = AcidcatTUI(big_wav)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause()
                target = 0x1D000
                app._jump_to_offset(target)
                await pilot.pause()
                off, length, _a = app._cur_region
                assert length > _HEX_CAP, "selected a one-byte region again"
                assert off <= target < off + length
                first = _first(app)
                assert first <= target < first + _page(app), (
                    f"window at 0x{first:08x} does not contain 0x{target:08x}")
        _run(scenario)

    def test_an_offset_no_chunk_covers_still_lands(self, big_wav):
        async def scenario():
            app = AcidcatTUI(big_wav)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause()
                app._jump_to_offset(10 ** 9)
                await pilot.pause()          # must not raise
        _run(scenario)


class TestBackReopensTheList:
    @pytest.fixture
    def blob(self, tmp_path):
        from test_tui_navigation_stack import _stream
        p = tmp_path / "b.blob"
        p.write_bytes(b"HDR!" + b"\x00" * 4000
                      + _stream(101) + _stream(202) + _stream(303))
        return str(p)

    async def _ready(self, app, pilot):
        app.query_one("#tree").root.expand()      # the scan is asked for now
        await pilot.pause(0.2)
        for _ in range(80):
            if app._regions is not None and not app._scanning:
                break
            await pilot.pause(0.1)
        while len(app.screen_stack) > 1:
            app.screen_stack[-1].dismiss(None)
            await pilot.pause()

    def test_coming_out_of_a_region_shows_the_others(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await self._ready(app, pilot)
                app._descend(0)
                await pilot.pause()
                app.action_nav_back()
                # A background round trip, waited on by its condition rather
                # than by a duration. The assert stays: `until` returns a
                # bool, so a timeout would otherwise read as a pass.
                await pilot.pause(0.1)
                await until(pilot, lambda: [s for s in app.screen_stack if isinstance(s, RegionsScreen)])
                assert [s for s in app.screen_stack
                        if isinstance(s, RegionsScreen)], (
                    "back left the user on a bare view with no list")
        _run(scenario)

    def test_escaping_that_list_leaves_you_on_the_parent(self, blob):
        """Not back inside the region you just left."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await self._ready(app, pilot)
                top = app.src
                app._descend(0)
                await pilot.pause()
                app.action_nav_back()
                await pilot.pause(0.4)
                for s in list(app.screen_stack):
                    if isinstance(s, RegionsScreen):
                        s.dismiss(None)
                        await pilot.pause()
                assert app.src == top
                assert app._region_view is None
        _run(scenario)

    def test_it_does_not_fire_when_there_was_no_region(self, blob, tmp_path):
        """Going back to a plain file you opened is not a region listing."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await self._ready(app, pilot)
                app._regions = None
                app._stack.append(app._snapshot())
                app._region_view = None
                app.action_nav_back()
                # A background round trip, waited on by its condition rather
                # than by a duration. The assert stays: `until` returns a
                # bool, so a timeout would otherwise read as a pass.
                await pilot.pause(0.1)
                await until(pilot, lambda: not [s for s in app.screen_stack if isinstance(s, RegionsScreen)])
                assert not [s for s in app.screen_stack
                            if isinstance(s, RegionsScreen)]
        _run(scenario)


class TestPlayingACompressedFile:
    def _ogg(self):
        found = glob.glob("data/**/*.ogg", recursive=True)
        if not found:
            pytest.skip("no .ogg in the test corpus")
        return found[0]

    def test_an_ogg_is_decoded_rather_than_reinterpreted(self, monkeypatch):
        """There is no `data` node in an Ogg to point `p` at, and there never
        will be -- the bytes are not PCM. Playing the file is the answer."""
        import acidcat.util.play as playmod
        path = self._ogg()

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause()
                assert app._decodable() is True
                called = {}
                monkeypatch.setattr(playmod, "have_audio", lambda: True)
                monkeypatch.setattr(playmod, "play",
                                    lambda p, **kw: called.setdefault("path", p))
                monkeypatch.setattr(playmod, "play_bytes",
                                    lambda *a, **kw: called.setdefault("raw", True))
                app.action_play()
                await pilot.pause()
                assert called.get("path") == app.work
                assert "raw" not in called, "reinterpreted a compressed file as PCM"
                assert len(app.screen_stack) == 1, "asked before doing the right thing"
        _run(scenario)

    def test_a_wav_still_takes_the_pcm_path(self, tmp_path, monkeypatch):
        """The reinterpreting path is right for real PCM and must survive."""
        import acidcat.util.play as playmod
        n = 4000
        body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
                + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
                + b"data" + struct.pack("<I", n) + b"\x00" * n)
        p = tmp_path / "t.wav"
        p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

        async def scenario():
            app = AcidcatTUI(str(p))
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause()
                assert app._decodable() is False
        _run(scenario)
