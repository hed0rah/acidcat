"""2.0 milestone 4: the byte strip, the responsive data inspector, the open
dialog's memory, and no pane drawing past its edge at any size."""

import asyncio
import json
import os

import pytest
from rich.cells import cell_len

pytest.importorskip("textual")

import seeds                                        # noqa: E402
from acidcat.tui_app import state                   # noqa: E402
from acidcat.tui_app.app import AcidcatTUI          # noqa: E402
from acidcat.tui_app.render import byte_strip       # noqa: E402


def _run(scenario):
    return asyncio.run(scenario())


def _node(off, length, head=0, kind="chunk"):
    return {"kind": kind, "extent": {"off": off, "len": length},
            "payload": {"off": off + head, "len": length - head}}


# ── the strip ──────────────────────────────────────────────────────────

def test_the_strip_is_exactly_as_wide_as_asked():
    nodes = [_node(0, 100, 8), _node(100, 900, 8)]
    for w in (1, 7, 80, 200):
        assert byte_strip(nodes, 1000, w).cell_len == w


def test_the_strip_is_proportional():
    t = byte_strip([_node(0, 250), _node(250, 750)], 1000, 40)
    spans = [(s.start, s.end, str(s.style)) for s in t.spans]
    first = {st for a, b, st in spans if b <= 10}
    last = {st for a, b, st in spans if a >= 10}
    assert len(first) == 1 and len(last) == 1 and first != last


def test_header_bytes_draw_as_header_and_gaps_as_gaps():
    t = byte_strip([_node(0, 80, 40), _node(80, 20, kind="unwalked")], 100, 10)
    assert t.plain == "▌▌▌▌████··"


def test_a_small_chunk_between_big_ones_keeps_its_cell():
    """Ten bytes in a cell of 500: the cell where it starts is its header,
    not the neighbour that covers the cell's middle."""
    from acidcat.tui_app.render import PALETTE
    nodes = [_node(0, 5000, 8), _node(5000, 10, 8), _node(5100, 4900, 8)]
    t = byte_strip(nodes, 10000, 20)
    assert t.plain[10] == "▌"
    cell = [s for s in t.spans if s.start == 10][0]
    assert str(cell.style) == PALETTE[1]


def test_the_selection_is_lit():
    t = byte_strip([_node(0, 100)], 100, 10, selection=(50, 10))
    assert t.plain[5] == "▓" and t.plain[4] == "█"


def test_an_empty_layer_draws_a_row_of_gap():
    assert byte_strip([], 0, 6).plain == "······"


# ── the data inspector goes where it fits ──────────────────────────────

@pytest.mark.parametrize("size,shown", [((120, 36), True), ((80, 30), False),
                                        ((160, 30), False)])
def test_the_data_inspector_shows_only_where_it_fits(tmp_path, size, shown):
    p = tmp_path / "seed.wav"
    p.write_bytes(seeds.build("wav"))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return app.query_one("#data").display
    assert _run(scenario) is shown


# ── nothing draws past its edge ────────────────────────────────────────

_PANES = ("#hex", "#inspect", "#data", "#strip", "#status")


@pytest.mark.parametrize("size", [(80, 30), (100, 30), (120, 36), (160, 44)])
@pytest.mark.parametrize("pick", ["sample_rate", "~ data", None])
def test_no_pane_runs_past_its_edge(tmp_path, size, pick):
    """The narrow golden shot, as a check: every line of every pane fits the
    width the pane actually has, on the root, a chunk and a field."""
    from test_tui_caps import _select
    p = tmp_path / "seed.wav"
    p.write_bytes(seeds.build("wav"))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            if pick:
                _select(app, pick)
            await pilot.pause()
            await pilot.pause()
            out = []
            for sel in _PANES:
                w = app.query_one(sel)
                if not w.display:
                    continue
                room = w.content_region.width
                for line in w.render().plain.split("\n"):
                    if cell_len(line) > room:
                        out.append(f"{sel}: {cell_len(line)} > {room}: {line!r}")
            return out
    assert _run(scenario) == []


# ── the open dialog remembers ──────────────────────────────────────────

def test_state_survives_a_mangled_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ACIDCAT_HOME", str(tmp_path / "home"))
    os.makedirs(tmp_path / "home")
    (tmp_path / "home" / "tui.json").write_text("{not json")
    assert state.load() == {"last_dir": None, "recent": []}
    (tmp_path / "home" / "tui.json").write_text("[1, 2]")
    assert state.load() == {"last_dir": None, "recent": []}


def test_recent_files_are_newest_first_unique_capped_and_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("ACIDCAT_HOME", str(tmp_path / "home"))
    files = []
    for i in range(state.RECENT_MAX + 3):
        f = tmp_path / f"f{i}.wav"
        f.write_bytes(b"x")
        files.append(str(f))
        state.remember(str(f))
    state.remember(files[-3])
    os.remove(files[-1])
    st = state.load()
    assert st["recent"][0] == files[-3]
    assert files[-1] not in st["recent"]
    assert len(st["recent"]) == len(set(st["recent"])) <= state.RECENT_MAX
    assert st["last_dir"] == str(tmp_path)
    raw = json.loads((tmp_path / "home" / "tui.json").read_text())
    assert set(raw) == {"last_dir", "recent"}


def test_an_unwritable_home_is_left_alone(tmp_path, monkeypatch):
    blocker = tmp_path / "home"
    blocker.write_text("a file where the directory should be")
    monkeypatch.setenv("ACIDCAT_HOME", str(blocker))
    state.remember(str(tmp_path / "x.wav"))          # does not raise
    assert state.load()["recent"] == []


def test_the_dialog_starts_in_the_last_directory_lists_recent_and_hides_dots(
        tmp_path, monkeypatch):
    from acidcat.tui_app.screens import BrowseScreen
    monkeypatch.setenv("ACIDCAT_HOME", str(tmp_path / "home"))
    here, there = tmp_path / "here", tmp_path / "there"
    here.mkdir()
    there.mkdir()
    (there / ".git").mkdir()
    (there / ".hidden.wav").write_bytes(b"x")
    (there / "song.wav").write_bytes(seeds.build("wav"))
    opened = here / "seed.wav"
    opened.write_bytes(seeds.build("wav"))
    state.remember(str(there / "song.wav"))

    async def scenario():
        app = AcidcatTUI(str(opened))          # remembered on open
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            # the last directory is now `here`: opening moved it
            await pilot.press("o")
            await pilot.pause(0.3)
            scr = app.screen
            assert isinstance(scr, BrowseScreen)
            start, recent = str(scr.start), list(scr.recent)
            tree = scr.query_one("#dtree")
            await pilot.pause(0.3)
            names = [str(c.label) for c in tree.root.children]
            scr.dismiss(None)
            await pilot.pause()
            app._browse = lambda: None
            # and in `there`, the dot folders stay hidden
            app.push_screen(BrowseScreen(str(there), recent))
            await pilot.pause(0.3)
            tree = app.screen.query_one("#dtree")
            await pilot.pause(0.3)
            there_names = [str(c.label) for c in tree.root.children]
            # picking a recent file opens it
            picked = []
            app.screen.dismiss = picked.append
            ol = app.screen.query_one("#recent")
            ol.focus()
            await pilot.pause()
            ol.highlighted = 1
            await pilot.press("enter")
            await pilot.pause()
            return start, recent, names, there_names, picked
    start, recent, names, there_names, picked = _run(scenario)
    assert start == str(here)
    assert recent == [str(opened), str(there / "song.wav")]
    assert "seed.wav" in names
    assert there_names == ["song.wav"]
    assert picked == [str(there / "song.wav")]
