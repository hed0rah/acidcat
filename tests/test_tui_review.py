"""Review fixes after 2.0's TUI milestones: the tree keeps its height, the
info box never splits a phrase, the data inspector writes both byte orders
the same way, and no temp file's name reaches the screen."""

import asyncio

import pytest

pytest.importorskip("textual")

import seeds                                        # noqa: E402
from acidcat.tui_app.app import AcidcatTUI          # noqa: E402
from acidcat.tui_app.render import data_inspector, pack   # noqa: E402
from rich.text import Text                          # noqa: E402


def _run(scenario):
    return asyncio.run(scenario())


def _rows(text):
    return {l.split()[0]: l.split()[1:] for l in text.plain.splitlines()[2:]}


# ── the data inspector ─────────────────────────────────────────────────

def test_the_data_inspector_is_compact_by_default_and_full_on_request():
    raw = bytes(range(1, 9))
    assert list(_rows(data_inspector(0, raw, full=False))) == ["u16", "u32", "i32", "f32"]
    assert len(_rows(data_inspector(0, raw, full=True))) == 10


@pytest.mark.parametrize("raw", [bytes([0x44, 0xAC, 0, 0, 0, 0, 0, 0]),
                                 bytes([0xff] * 7 + [0x7f]),
                                 bytes([0x52, 0x49, 0x46, 0x46, 0xa4, 0, 0, 0])])
def test_both_byte_orders_are_written_the_same_way(raw):
    for name, (le, be) in _rows(data_inspector(0, raw)).items():
        if name.startswith("f"):
            continue
        assert le.startswith("0x") == be.startswith("0x"), (name, le, be)


def test_i_toggles_the_full_data_inspector(tmp_path):
    p = tmp_path / "seed.wav"
    p.write_bytes(seeds.build("wav"))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            before = app.query_one("#data").render().plain.count("\n")
            await pilot.press("i")
            await pilot.pause()
            after = app.query_one("#data").render().plain.count("\n")
            return before, after
    before, after = _run(scenario)
    assert (before, after) == (5, 11)


# ── the tree keeps its height ──────────────────────────────────────────

@pytest.mark.parametrize("size,rows", [((120, 36), 12), ((160, 44), 18)])
def test_the_tree_keeps_its_rows(tmp_path, size, rows):
    p = tmp_path / "seed.wav"
    p.write_bytes(seeds.build("wav"))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return app.query_one("#data").display, app.query_one("#tree").content_region.height
    shown, height = _run(scenario)
    assert shown and height >= rows


# ── the info box lays out whole phrases ────────────────────────────────

def test_pack_moves_a_piece_whole_to_the_next_line():
    t = pack([Text("seed.wav"), Text("RIFF/WAVE"), Text("172 bytes"),
              Text("2 chunks"), Text("[u back (1)]")], 30)
    lines = t.plain.split("\n")
    assert all(len(l) <= 30 for l in lines)
    for piece in ("172 bytes", "2 chunks", "[u back (1)]"):
        assert any(piece in l for l in lines), (piece, lines)


def test_pack_breaks_only_a_piece_wider_than_the_line():
    t = pack([Text("a very long format label that cannot fit")], 16)
    assert all(len(l) <= 16 for l in t.plain.split("\n"))
    assert t.plain.replace("\n  ", " ") == "a very long format label that cannot fit"


@pytest.mark.parametrize("size", [(120, 36), (160, 44)])
def test_no_phrase_in_the_info_box_splits(tmp_path, size):
    p = tmp_path / "tune.ym"
    p.write_bytes(seeds.SEEDS["ym"][0](packed="lh5"))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            from test_tui_caps import _select
            _select(app, "lh5")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            return (app.query_one("#title").render().plain,
                    app.query_one("#anom").render().plain,
                    app.query_one("#title").content_region.width)
    title, anom, width = _run(scenario)
    lines = title.split("\n")
    assert all(len(l) <= width for l in lines)
    for piece in ("bytes", "chunks", "[u back (1)]"):
        hit = [l for l in lines if piece in l]
        assert hit, (piece, lines)
    assert any("chunks" in l and l.split("chunks")[0].strip()[-1:].isdigit()
               for l in lines), lines
    assert "clean: no findings" in anom


# ── no temp name on screen ─────────────────────────────────────────────

def test_a_decoded_layer_is_named_like_the_breadcrumb(tmp_path):
    p = tmp_path / "space gun 1.ym"
    p.write_bytes(seeds.SEEDS["ym"][0](packed="lh5"))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            from test_tui_caps import _select
            _select(app, "lh5")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            return app.export_screenshot(), app.query_one("#inspect").render().plain
    svg, inspect = _run(scenario)
    assert inspect.splitlines()[0].startswith("space gun 1.ym > unpacked YM5")
    assert "acidcat_layer" not in svg and "tmp" not in inspect
