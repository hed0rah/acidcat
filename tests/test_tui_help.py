"""The help is generated from the bindings (2.0, milestone 4).

Every key the app binds has a row in AcidcatTUI.HELP and every row names an
action something binds, so a rebound or added key cannot leave the help
wrong. The rows are grouped by the area they act on.
"""

import asyncio

import pytest

pytest.importorskip("textual")

import seeds                                 # noqa: E402
from acidcat.tui_app.app import AcidcatTUI   # noqa: E402


def _actions(bindings):
    return {b[1] if isinstance(b, tuple) else b.action for b in bindings}


def test_every_binding_has_a_help_row_and_every_row_a_binding():
    from textual.widgets import Tree
    bound = _actions(AcidcatTUI.BINDINGS)
    rows = {a for _area, acts, _t in AcidcatTUI.HELP for a in acts}
    tree_rows = {a for _area, acts, _t in AcidcatTUI.TREE_HELP for a in acts}
    assert bound - rows == set(), "bound with no help row"
    assert rows - bound == set(), "help row for an action nothing binds"
    assert tree_rows <= _actions(Tree.BINDINGS)


def test_every_row_sits_in_a_known_area_and_the_areas_come_in_order():
    areas = [a for a, _r in AcidcatTUI._help_sections()]
    assert areas == [a for a in AcidcatTUI.HELP_AREAS if a in areas]
    assert {a for a, _x, _t in AcidcatTUI.HELP} <= set(AcidcatTUI.HELP_AREAS)


def test_a_rebound_key_shows_up_in_the_help(monkeypatch):
    from textual.binding import Binding
    rebound = [Binding("k", "zoom", "zoom") if (not isinstance(b, tuple)
               and b.action == "zoom") or (isinstance(b, tuple) and b[1] == "zoom")
               else b for b in AcidcatTUI.BINDINGS]
    monkeypatch.setattr(AcidcatTUI, "BINDINGS", rebound)
    rows = dict(r for _a, rs in AcidcatTUI._help_sections() for r in rs)
    assert rows.get("k", "").startswith("give the focused pane")
    assert "z" not in rows


def test_the_help_screen_shows_the_areas(tmp_path):
    p = tmp_path / "seed.wav"
    p.write_bytes(seeds.build("wav"))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()
            return "\n".join(w.render().plain for w in app.screen.query("Static"))
    text = asyncio.run(scenario())
    for area in AcidcatTUI.HELP_AREAS:
        assert f"\n{area}\n" in text
    assert "pgdn / pgup" in text and "ctrl+z/r" in text and "shift+left/right" in text
