"""The forensics panel has to be readable without a mouse.

#idbox is six rows: a filename line, a legend line, and room for about four
findings. Everything past that was below the fold, and the only two panes in
the tab cycle were the tree and the hex dump -- so on a file with a dozen
findings, the list existed and could not be read from the keyboard. Over ssh,
which is where this tool gets used, that is the whole list.

Its twin: `f` cycles findings and moves a `>` marker down the panel, but the
panel never scrolled, so past the fourth finding the marker moved somewhere
invisible. Pressing f repeatedly showed a frozen box while the tree and hex
pane jumped around, which reads as f being broken.
"""

import asyncio
import struct

import pytest

from acidcat.tui_app.app import AcidcatTUI

pytest.importorskip("textual")


def _run(scenario):
    asyncio.run(scenario())


@pytest.fixture
def noisy(tmp_path):
    """A WAV that scans dirty enough to overflow the panel.

    A correct RIFF followed by every magic the polyglot scan knows: one
    trailing_data notice plus one alert per magic. Built rather than borrowed
    so the finding count is a property of the file and not of a corpus that
    may not be present.
    """
    from acidcat.core.forensics import anomalies

    body = b"WAVE"
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    body += b"fmt " + struct.pack("<I", 16) + fmt
    body += b"data" + struct.pack("<I", 64) + b"\x00" * 64
    data = b"RIFF" + struct.pack("<I", len(body)) + body
    tail = b"".join(magic + b"\x00" * 24 for magic, _ in anomalies._MAGICS)
    p = tmp_path / "noisy.wav"
    p.write_bytes(data + tail)
    return str(p)


def test_the_findings_panel_is_in_the_tab_cycle(noisy):
    async def scenario():
        app = AcidcatTUI(noisy)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            seen = set()
            for _ in range(len(app._PANES)):
                await pilot.press("tab")
                await pilot.pause()
                seen.add(app._focused_pane())
            assert "idbox" in seen, (
                f"tab never reached the forensics panel; visited {sorted(seen)}")
    _run(scenario)


def test_the_findings_panel_scrolls_when_focused(noisy):
    async def scenario():
        app = AcidcatTUI(noisy)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            assert len(app.findings) > 4, (
                f"fixture only produced {len(app.findings)} findings; it must "
                f"overflow the six-row box for this test to mean anything")
            box = app.query_one("#idbox")
            assert box.max_scroll_y > 0, "the panel should hold more than it shows"

            for _ in range(len(app._PANES)):
                if app._focused_pane() == "idbox":
                    break
                await pilot.press("tab")
                await pilot.pause()
            assert app._focused_pane() == "idbox"

            for _ in range(4):
                await pilot.press("down")
            await pilot.pause()
            assert box.scroll_offset.y > 0, "arrows did not scroll the panel"
    _run(scenario)


def test_pressing_f_keeps_the_marked_finding_on_screen(noisy):
    async def scenario():
        app = AcidcatTUI(noisy)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            n = len(app.findings)
            assert n > 4
            box = app.query_one("#idbox")
            for _ in range(n - 1):            # walk to the last finding
                await pilot.press("f")
            await pilot.pause()
            assert app._finding_idx == n - 2 or app._finding_idx >= 4
            assert box.scroll_offset.y > 0, (
                "the panel never scrolled, so the > marker is off screen")
    _run(scenario)


def test_zoom_declines_on_the_panel_instead_of_raising(noisy):
    """#idbox has no zoom class. Before it joined the tab cycle that was
    unreachable; now z can land on it, and a KeyError would kill the app."""
    async def scenario():
        app = AcidcatTUI(noisy)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            for _ in range(len(app._PANES)):
                if app._focused_pane() == "idbox":
                    break
                await pilot.press("tab")
                await pilot.pause()
            assert app._focused_pane() == "idbox"
            await pilot.press("z")
            await pilot.pause()
            assert app._zoom is None, "the panel should not have zoomed"
            assert app.is_running, "z crashed the app"
    _run(scenario)


def test_a_finding_with_no_offset_neither_crashes_the_panel_nor_the_jump(noisy):
    """The anomaly runner reports a check that could not run as a finding
    with offset None: it is about the whole file. Under parallel test load
    a MemoryError produced one, and the panel died formatting None as 08x.
    The jump key must not crash on it either."""
    async def scenario():
        app = AcidcatTUI(noisy)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            app.findings = [{"severity": "warn", "offset": None, "rule": "check_failed",
                             "message": "the x check could not run (MemoryError)"}] \
                + list(app.findings)
            app._finding_idx = -1
            app._render_anomalies()
            await pilot.press("f")
            await pilot.pause()
            assert app._finding_idx == 0
            await pilot.press("f")
            await pilot.pause()
            assert app._finding_idx == 1
    _run(scenario)
