"""The TUI's model, without a terminal and without Textual.

`tui_app/model.py` holds everything the screen shows that is not a widget:
the Document, the layer, the selection, the window onto the layer, undo. These
tests drive it directly; the first one proves they need no Textual at all.
"""

import os
import subprocess
import sys

import pytest

import seeds
from acidcat.core.walk import walk_file
from acidcat.tui_app.model import DocumentModel


def _model(tmp_path, name="seed.wav", raw=None):
    p = tmp_path / name
    p.write_bytes(raw if raw is not None else seeds.build("wav"))
    m = DocumentModel(str(p))
    if raw is not None and name.endswith(".bin"):
        m.load(None, "raw bytes", [], [])        # no walker; a Document still
    else:
        m.load(None, *walk_file(str(p)))
    return m


def test_the_model_imports_and_runs_without_textual(tmp_path):
    """With Textual made unimportable, the model still loads a file, selects
    and draws a window: it depends on nothing the terminal needs."""
    p = tmp_path / "seed.wav"
    p.write_bytes(seeds.build("wav"))
    code = (
        "import sys; sys.modules['textual'] = None\n"
        "from acidcat.core.walk import walk_file\n"
        "from acidcat.tui_app.model import DocumentModel\n"
        f"m = DocumentModel({str(p)!r})\n"
        f"m.load(None, *walk_file({str(p)!r}))\n"
        "m.select(12, 4)\n"
        "start, raw = m.window(16, 4)\n"
        "assert m.document is not None and raw\n"
        "assert 'textual' not in [k for k, v in sys.modules.items() if v]\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
    assert r.returncode == 0, r.stderr


def test_it_builds_the_document_from_the_walk_it_is_given(tmp_path):
    m = _model(tmp_path)
    assert m.document["contract"] == 1
    assert m.layer_ids() == [0]
    assert m.layer_length() == os.path.getsize(m.path)


class TestTheWindow:
    def test_a_selection_that_fits_sits_a_third_of_the_way_down(self, tmp_path):
        m = _model(tmp_path, "a.bin", bytes(range(256)) * 16)
        m.select(0x400, 4)
        start, raw = m.window(16, 12)
        row = (0x400 - start) // 16
        assert row == (12 - 1) // 3
        assert raw == m.read(start, 12 * 16)

    def test_it_never_starts_before_the_layer_or_runs_past_it(self, tmp_path):
        m = _model(tmp_path, "a.bin", bytes(1000))
        m.select(0, 4)
        assert m.window(16, 12)[0] == 0
        m.select(990, 4)
        start, raw = m.window(16, 12)
        assert start + len(raw) == 1000
        assert len(raw) == 12 * 16 - (12 * 16 - (1000 - start))

    def test_paging_moves_a_screenful_and_stops_at_the_ends(self, tmp_path):
        m = _model(tmp_path, "a.bin", bytes(16 * 100))
        m.select(0, 1)
        assert m.page(-1, 16, 10) is None
        assert m.page(1, 16, 10) == 160
        assert m.page(1, 16, 10) == 320
        for _ in range(20):
            m.page(1, 16, 10)
        assert m.page(1, 16, 10) is None
        assert m.window(16, 10)[0] == 16 * 90

    def test_a_new_selection_brings_the_window_back(self, tmp_path):
        m = _model(tmp_path, "a.bin", bytes(16 * 100))
        m.select(0, 1)
        m.page(1, 16, 10)
        m.select(0, 1)                       # the same selection keeps its page
        assert m.top is not None
        m.select(800, 2)
        assert m.top is None and m.window(16, 10)[0] <= 800

    def test_show_offset_puts_a_byte_in_view(self, tmp_path):
        m = _model(tmp_path, "a.bin", bytes(16 * 100))
        m.show_offset(1000, 16, 10)
        start, raw = m.window(16, 10)
        assert start <= 1000 < start + len(raw)


def test_the_status_names_the_layer_the_selection_and_the_node(tmp_path):
    m = _model(tmp_path)
    fmt = m.document["nodes"][0]
    for n in m.document["nodes"]:
        if n["name"] == "RIFF":
            fmt = n
    m.select(fmt["extent"]["off"], fmt["extent"]["len"])
    st = m.status()
    assert st["layer"] == "file" and st["layer_id"] == 0
    assert st["offset"] == fmt["extent"]["off"]
    assert st["node"] == fmt["id"]
    assert st["actions"] == sorted(fmt["caps"])


def test_spans_are_the_node_fields_in_this_layer(tmp_path):
    m = _model(tmp_path)
    node = next(n for n in m.document["nodes"] if n["fields"])
    spans = m.spans(node)
    assert spans and all(isinstance(o, int) and n > 0 for o, n in spans)


class TestUndo:
    def test_record_take_and_redo(self, tmp_path):
        m = _model(tmp_path)
        m.record(4, b"\x00", b"\x01")
        m.record(8, b"\x02", b"\x03")
        assert m.take_undo() == (8, b"\x02", b"\x03")
        assert m.take_redo() == (8, b"\x02", b"\x03")
        assert m.take_redo() is None
        m.take_undo()
        m.record(0, b"a", b"b")              # a fresh edit forgets redo
        assert m.redo == []

    def test_the_history_is_capped_and_keeps_the_newest(self, tmp_path, monkeypatch):
        from acidcat.tui_app import model as tm
        monkeypatch.setattr(tm, "_UNDO_BYTES_CAP", 1)
        m = _model(tmp_path)
        m.record(0, b"aaaa", b"bbbb")
        m.record(4, b"cccc", b"dddd")
        assert m.undo == [(4, b"cccc", b"dddd")]


def test_a_decoded_layer_is_read_from_the_document(tmp_path):
    """A packed YM's tune is layer 1; the model reads it through the
    registry, and its window is in the layer's own offsets."""
    raw = seeds.SEEDS["ym"][0](packed="lh5")
    m = _model(tmp_path, name="tune.ym", raw=raw)
    assert m.layer_ids() == [0, 1]
    m.layer = 1
    assert m.read(0, 4) == b"YM5!"
    assert m.layer_length() == len(seeds.SEEDS["ym"][0]())
    m.select(12, 4, layer=1)
    assert m.status()["layer"].startswith("unpacked")
