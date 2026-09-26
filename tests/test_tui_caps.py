"""The TUI decides by capabilities, not by format names (2.0, milestone 2).

What `p`, `X` and `e` do comes from the caps the walk's nodes carry
(core/infra/capabilities.py): a walker that declares a cap gets the behaviour
with no TUI edit, and nothing in tui_app/ chooses a behaviour by matching a
format id or a walker label. The field inspector says what a field is, and the
data inspector reads the bytes at the cursor every common way.
"""

import ast
import asyncio
import pathlib
import re

import pytest

pytest.importorskip("textual")

import seeds                                        # noqa: E402
from acidcat.core import walk as walkmod            # noqa: E402
from acidcat.tui_app.app import AcidcatTUI          # noqa: E402
from acidcat.tui_app.render import data_inspector   # noqa: E402

TUI = pathlib.Path(__file__).parent.parent / "src" / "acidcat" / "tui_app"


def _run(scenario):
    return asyncio.run(scenario())


# ── no format dispatch in tui_app ─────────────────────────────────────

def _deciding_literals():
    """(file, line, text) of string literals the TUI decides with: operands
    of comparisons and membership tests, arguments of startswith/endswith,
    and the constant tuples/lists/dicts assigned to names."""
    out = []
    for py in sorted(TUI.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        sites = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Compare):
                sites.append(n)
            elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr in ("startswith", "endswith")):
                sites.append(n)
            elif (isinstance(n, ast.Assign)
                  and isinstance(n.value, (ast.Tuple, ast.List, ast.Set, ast.Dict))):
                sites.append(n.value)
        seen = set()
        for site in sites:
            for c in ast.walk(site):
                if (isinstance(c, ast.Constant) and isinstance(c.value, str)
                        and id(c) not in seen):
                    seen.add(id(c))
                    out.append((py.name, c.lineno, c.value))
    return out


def test_the_enumerator_still_finds_literals():
    assert len(_deciding_literals()) > 100


def test_no_format_name_or_label_decides_anything_in_the_tui():
    """The TUI used to pick a player by matching "sid tune" in the walker's
    label, a decoder by substrings of format names, the audio chunk by its id,
    the tag editor by magic bytes and the disc browser by file extension.
    Each now reads a cap or asks core; this keeps it that way."""
    ids = {i for i in walkmod._WALKERS if len(i) > 2}     # "it", "au": English
    labels = {v[0].lower() for v in walkmod._WALKERS.values() if len(v[0]) > 3}
    word = re.compile(r"(?<![a-z0-9_])(" + "|".join(
        sorted(map(re.escape, ids), key=len, reverse=True)) + r")(?![a-z0-9_])")
    bad = []
    for fname, line, text in _deciding_literals():
        low = text.lower()
        hits = {m.group(1) for m in word.finditer(low)}
        hits |= {l for l in labels if l in low}
        # a piece of a label used as a needle ("sid tune", "spc700 sound
        # snapshot" was how the player was chosen): several words, and long
        # enough not to be ordinary vocabulary
        piece = low.strip()
        if " " in piece and len(piece) >= 6:
            hits |= {l for l in labels if piece in l}
        if hits:
            bad.append(f"{fname}:{line}: {text!r} names {sorted(hits)}")
    assert not bad, "the TUI decides by format name:\n  " + "\n  ".join(bad)


# ── a declared cap works with no TUI edit ──────────────────────────────

def test_a_walker_that_declares_render_plays_with_no_tui_edit(tmp_path, monkeypatch):
    """A WAV walker made to declare `render: sid` gets the SID engine on `p`:
    the TUI read the cap, not the format."""
    from acidcat.core.codecs import engines
    from acidcat.util import play
    label, walker = walkmod._WALKERS["wav"]

    def declaring(filepath, deep=False):
        chunks, warns = walker(filepath, deep)
        chunks[0]["caps"] = {"render": {"engine": "sid"}}
        return chunks, warns
    monkeypatch.setitem(walkmod._WALKERS, "wav", (label, declaring))
    monkeypatch.setattr(play, "have_audio", lambda: True)
    p = tmp_path / "seed.wav"
    p.write_bytes(seeds.build("wav"))

    async def scenario():
        app = AcidcatTUI(str(p))
        ran = []
        app._play_render = lambda engine: ran.append(engine)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
        return ran
    ran = _run(scenario)
    assert ran == [engines.get("sid")]


def test_a_render_cap_with_no_engine_says_so(tmp_path, monkeypatch):
    from acidcat.util import play
    label, walker = walkmod._WALKERS["wav"]

    def declaring(filepath, deep=False):
        chunks, warns = walker(filepath, deep)
        chunks[0]["caps"] = {"render": {"engine": "ym"}}
        return chunks, warns
    monkeypatch.setitem(walkmod._WALKERS, "wav", (label, declaring))
    monkeypatch.setattr(play, "have_audio", lambda: True)
    p = tmp_path / "seed.wav"
    p.write_bytes(seeds.build("wav"))

    async def scenario():
        app = AcidcatTUI(str(p))
        notes = []
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            app.notify = lambda m, **kw: notes.append(str(m))
            await pilot.press("p")
            await pilot.pause()
        return notes
    assert any("no player for ym" in n for n in _run(scenario))


# ── e and the root ─────────────────────────────────────────────────────

def test_e_on_a_file_with_a_tag_editor_opens_it(tmp_path):
    from acidcat.tui_app.screens import EditScreen
    p = tmp_path / "seed.wav"
    p.write_bytes(seeds.build("wav"))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            assert app._node_caps().get("edit", {}).get("profile") == "wav"
            await pilot.press("e")
            await pilot.pause()
            return [type(s) for s in app.screen_stack]
    assert EditScreen in _run(scenario)


def test_the_root_offers_the_file_caps_and_a_chunk_does_not(tmp_path):
    p = tmp_path / "seed.sid"
    p.write_bytes(seeds.build("sid"))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            root = app._node_caps()
            header = app.query_one("#tree").root.children[0]
            return root, app._node_caps(header), app.query_one("#status").render().plain
    root, header, status = _run(scenario)
    assert root["render"]["engine"] == "sid"
    assert "render" not in header
    assert "render" in status


# ── the inspectors ─────────────────────────────────────────────────────

def test_the_data_inspector_reads_both_byte_orders():
    t = data_inspector(0x18, bytes([0x44, 0xAC, 0, 0, 0, 0, 0, 0])).plain
    rows = {l.split()[0]: l.split()[1:] for l in t.splitlines()[2:]}
    assert rows["u16"] == ["44100", "17580"]
    assert rows["u32"] == ["44100", "1152122880"]
    assert rows["i16"] == ["-21436", "17580"]


def test_the_data_inspector_leaves_a_short_read_blank():
    t = data_inspector(0, b"\x01\x02").plain
    rows = {l.split()[0]: l.split()[1:] for l in t.splitlines()[2:]}
    assert rows["u16"] == ["513", "258"]
    assert rows["u32"] == [] and rows["f64"] == []


def test_a_cut_number_says_it_was_cut():
    t = data_inspector(0, bytes([0xff] * 7 + [0x7f])).plain
    u64 = [l for l in t.splitlines() if l.startswith("u64")][0]
    assert "…" in u64 or "0x7fffffffffffffff" in u64


def _select(app, prefix):
    def find(node):
        for c in node.children:
            lbl = c.label.plain if hasattr(c.label, "plain") else str(c.label)
            if lbl.strip().startswith(prefix):
                return c
            hit = find(c)
            if hit is not None:
                return hit
        return None
    node = find(app.query_one("#tree").root)
    assert node is not None, prefix
    app._select_node(node)
    return node


def test_the_field_inspector_names_type_value_and_pointer(tmp_path):
    p = tmp_path / "seed.sid"
    p.write_bytes(seeds.build("sid"))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            _select(app, "dataOffset")
            await pilot.pause()
            await pilot.pause()
            return app.query_one("#inspect").render().plain
    text = _run(scenario)
    assert "u16be" in text and "(enc)" in text
    assert "value" in text and "124" in text
    assert "points" in text and "0x0000007c" in text and "enter follows" in text


def test_enter_on_a_pointer_follows_it(tmp_path):
    p = tmp_path / "seed.sid"
    p.write_bytes(seeds.build("sid"))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            _select(app, "dataOffset")
            await pilot.pause()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            off, length, _a = app._cur_region
            return off, length
    off, length = _run(scenario)
    assert off <= 0x7C < off + max(length, 1)
