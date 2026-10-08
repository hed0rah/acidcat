"""Layers in the TUI (2.0, milestone 3): a packed file's image, opened in place.

A packed YM's LHA body is layer 1 of its Document. `enter` on the body opens
the layer as a view of its own: the breadcrumb names it, the tune's fields have
byte ranges there and light up in its bytes, and `u` comes back to the file.
The layer is read-only: it is the walk's decoding, not bytes the file holds.
"""

import asyncio
import os

import pytest

pytest.importorskip("textual")

import seeds                                        # noqa: E402
from acidcat.tui_app.app import AcidcatTUI          # noqa: E402


def _run(scenario):
    return asyncio.run(scenario())


def _find(node, prefix):
    for c in node.children:
        lbl = c.label.plain if hasattr(c.label, "plain") else str(c.label)
        if lbl.strip().startswith(prefix):
            return c
        hit = _find(c, prefix)
        if hit is not None:
            return hit
    return None


async def _select(app, pilot, prefix):
    node = _find(app.query_one("#tree").root, prefix)
    assert node is not None, prefix
    app._select_node(node)
    await pilot.pause()
    await pilot.pause()


@pytest.fixture
def packed(tmp_path):
    p = tmp_path / "tune.ym"
    p.write_bytes(seeds.SEEDS["ym"][0](packed="lh5"))
    return str(p)


def test_enter_opens_the_layer_and_the_frame_count_is_lit_in_it(packed):
    async def scenario():
        app = AcidcatTUI(packed)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await _select(app, pilot, "lh5")
            assert app._node_caps()["descend"]["layer"] == 1
            await pilot.press("enter")
            await pilot.pause()
            crumb = app._breadcrumb()
            await _select(app, pilot, "frames")
            status = app.query_one("#status").render().plain
            hexpane = app.query_one("#hex").render()
            return crumb, app.model.selection, status, hexpane
    crumb, sel, status, hexpane = _run(scenario)
    assert crumb == "tune.ym > unpacked YM5"
    assert sel == (0, 12, 4)                       # layer offsets
    assert "layer 1" in status and "unpacked YM5" in status
    # lit: the four bytes of the count carry the selection's background
    first = hexpane.plain.splitlines()[0]
    col = first.index("00 00 00 04")
    lit = [s for s in hexpane.spans if s.start <= col < s.end]
    assert any("on " in str(s.style) for s in lit), lit


def test_u_comes_back_to_the_file(packed):
    async def scenario():
        app = AcidcatTUI(packed)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await _select(app, pilot, "lh5")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
            await pilot.pause()
            return app._breadcrumb(), app._layer_view, app.src
    crumb, view, src = _run(scenario)
    assert crumb == "tune.ym" and view is None and src == packed


def test_a_layer_is_read_only(packed):
    before = open(packed, "rb").read()

    async def scenario():
        app = AcidcatTUI(packed)
        notes = []
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await _select(app, pilot, "lh5")
            await pilot.press("enter")
            await pilot.pause()
            await _select(app, pilot, "frames")
            app.notify = lambda m, **kw: notes.append(str(m))
            await pilot.press("e")
            await pilot.pause()
            return notes, app._edit_target
    notes, target = _run(scenario)
    assert target is None
    assert any("decoding of the file" in n for n in notes), notes
    assert open(packed, "rb").read() == before


def test_a_packed_field_says_where_its_bytes_are(packed):
    """In the file view a field of the packed tune has no byte range in the
    file; it says which layer has one and how to open it."""
    async def scenario():
        app = AcidcatTUI(packed)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await _select(app, pilot, "frames")
            return (app.query_one("#inspect").render().plain,
                    app.query_one("#hex").render().plain)
    inspect, hexpane = _run(scenario)
    assert "layer 1" in inspect and "enter on lh5 opens it" in inspect
    assert "layer 1" in hexpane


def test_the_decoded_file_is_removed_on_quit(packed):
    async def scenario():
        app = AcidcatTUI(packed)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await _select(app, pilot, "lh5")
            await pilot.press("enter")
            await pilot.pause()
            return app.src
    tmp = _run(scenario)
    assert os.path.basename(tmp).startswith("acidcat_layer_")
    assert not os.path.exists(tmp)


def test_enter_on_a_plain_chunk_does_not_descend(tmp_path):
    p = tmp_path / "seed.wav"
    p.write_bytes(seeds.build("wav"))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await _select(app, pilot, "fmt")
            await pilot.press("enter")
            await pilot.pause()
            return app._layer_view, len(app._stack)
    assert _run(scenario) == (None, 0)
