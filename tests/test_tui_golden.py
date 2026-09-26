"""Golden screenshots of the TUI: the review for anything visual.

Each state is driven headless at a fixed terminal size and exported with
Textual's own `export_screenshot()` (an SVG, no extra dependency), then
compared with `tests/golden/tui/<name>.svg`. Nobody reviewing a change can see
a terminal, so these files are what a person looks at; a change that moves a
pixel fails here and the new SVG is written beside the test's temp dir.

Regenerate with ACIDCAT_WRITE_GOLDEN=1 after an intended change, and look at
every SVG that changed before committing it.

The drawing depends on Textual's renderer, so the files are pinned to the
Textual major.minor they were written with (tests/golden/tui/TEXTUAL); on
another version the comparison is skipped with both versions named, rather
than failing on a renderer change nobody made here.
"""

import asyncio
import os
import pathlib
import struct

import pytest

textual = pytest.importorskip("textual")

import seeds                                   # noqa: E402
from acidcat import tui_theme                  # noqa: E402
from acidcat.tui_app.app import AcidcatTUI     # noqa: E402

GOLDEN = pathlib.Path(__file__).parent / "golden" / "tui"
WRITE = os.environ.get("ACIDCAT_WRITE_GOLDEN") == "1"


def _textual_version():
    from importlib.metadata import version
    return ".".join(version("textual").split(".")[:2])


def _wav_with_tail(tmp_path):
    """A WAV with a zip appended past its RIFF end: two findings."""
    raw = seeds.build("wav") + b"PK\x03\x04" + bytes(40)
    return raw


# name -> (file name, bytes, (cols, rows), steps)
# A step is a key to press, or ("select", label prefix) to land the tree on
# the first node whose label starts with it (children of expanded nodes
# included), which is what goto/search do.
STATES = {
    "wav-open": ("seed.wav", lambda t: seeds.build("wav"), (120, 36), []),
    "wav-chunk": ("seed.wav", lambda t: seeds.build("wav"), (120, 36),
                  [("select", "fmt")]),
    "wav-field": ("seed.wav", lambda t: seeds.build("wav"), (120, 36),
                  [("select", "sample_rate")]),
    "wav-field-wide": ("seed.wav", lambda t: seeds.build("wav"), (160, 44),
                       [("select", "sample_rate")]),
    "wav-field-narrow": ("seed.wav", lambda t: seeds.build("wav"), (80, 30),
                         [("select", "sample_rate")]),
    "wav-findings": ("tail.wav", _wav_with_tail, (120, 36), []),
    "ym-packed": ("tune.ym", lambda t: seeds.SEEDS["ym"][0](packed="lh5"),
                  (120, 36), [("select", "lh5")]),
    "midi-track": ("seed.mid", lambda t: seeds.build("midi"), (120, 36),
                   [("select", "MTrk")]),
    "hex-focused-scrolled": ("seed.wav", lambda t: seeds.build("wav"), (120, 36),
                             ["tab", "down", "down", "down"]),
    "entropy-view": ("seed.wav", lambda t: seeds.build("wav"), (120, 36),
                     ["b"]),
    "hex-zoomed": ("seed.wav", lambda t: seeds.build("wav"), (120, 36),
                   [("select", "~ data"), "tab", "z"]),
    "help": ("seed.wav", lambda t: seeds.build("wav"), (120, 36),
             ["question_mark"]),
    # 2.0 M2: a pointer field says where it points; the root offers its caps
    "pointer-field": ("seed.sid", lambda t: seeds.build("sid"), (120, 36),
                      [("select", "dataOffset")]),
    "sid-root": ("seed.sid", lambda t: seeds.build("sid"), (120, 36), []),
    # 2.0 M3: a packed field says where its bytes are; enter opens the layer
    "ym-packed-field": ("tune.ym", lambda t: seeds.SEEDS["ym"][0](packed="lh5"),
                        (120, 36), [("select", "lh5"), "right", ("select", "frames")]),
    "ym-layer-frames": ("tune.ym", lambda t: seeds.SEEDS["ym"][0](packed="lh5"),
                        (120, 36), [("select", "lh5"), "enter", ("select", "frames")]),
}


def _label(node):
    lbl = node.label
    return (lbl.plain if hasattr(lbl, "plain") else str(lbl)).strip()


def _find(node, prefix):
    for c in node.children:
        if _label(c).startswith(prefix):
            return c
        hit = _find(c, prefix)
        if hit is not None:
            return hit
    return None


async def _drive(app, pilot, steps):
    await pilot.pause()
    for step in steps:
        if isinstance(step, tuple) and step[0] == "select":
            node = _find(app.query_one("#tree").root, step[1])
            assert node is not None, f"no tree node starts with {step[1]!r}"
            app._select_node(node)
        else:
            await pilot.press(step)
        await pilot.pause()
    await pilot.pause()
    await pilot.pause()


def _shot(tmp_path, name):
    fname, build, size, steps = STATES[name]
    p = tmp_path / fname
    p.write_bytes(build(tmp_path))

    async def scenario():
        app = AcidcatTUI(str(p))
        async with app.run_test(size=size) as pilot:
            await _drive(app, pilot, steps)
            return app.export_screenshot()
    return asyncio.run(scenario())


def _norm(svg):
    return svg.replace("\r\n", "\n")


@pytest.mark.parametrize("name", sorted(STATES))
def test_the_screen_matches_its_golden_shot(tmp_path, name):
    if tui_theme.ACTIVE_THEME != "brand":
        pytest.skip(f"golden shots are drawn in the brand theme, not "
                    f"{tui_theme.ACTIVE_THEME} (ACIDCAT_THEME is set)")
    pinned = (GOLDEN / "TEXTUAL").read_text(encoding="utf-8").strip() if (GOLDEN / "TEXTUAL").exists() else None
    if WRITE:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        (GOLDEN / "TEXTUAL").write_text(_textual_version() + "\n", encoding="utf-8")
        (GOLDEN / f"{name}.svg").write_text(_shot(tmp_path, name), encoding="utf-8",
                                            newline="\n")
        return
    if pinned != _textual_version():
        pytest.skip(f"golden shots were drawn with Textual {pinned}; this is "
                    f"{_textual_version()}. Regenerate with "
                    f"ACIDCAT_WRITE_GOLDEN=1 and review them")
    want = GOLDEN / f"{name}.svg"
    assert want.exists(), f"no golden shot {want}; write it with ACIDCAT_WRITE_GOLDEN=1"
    got = _shot(tmp_path, name)
    if _norm(got) != _norm(want.read_text(encoding="utf-8")):
        out = tmp_path / f"{name}.actual.svg"
        out.write_text(got, encoding="utf-8", newline="\n")
        pytest.fail(f"{name}: the screen changed; compare {out} with {want}")


def test_every_golden_shot_has_a_state():
    """The other direction: a shot whose state was removed leaves the repo."""
    names = {p.stem for p in GOLDEN.glob("*.svg")}
    assert names == set(STATES), (sorted(names - set(STATES)),
                                  sorted(set(STATES) - names))
