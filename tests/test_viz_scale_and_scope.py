"""A chart may rescale itself. It may not do so quietly.

Two complaints from actually using the TUI, both the same shape:

  The entropy plot is pinned to the ceiling on nearly every real file, because
  audio sits around 7.9 of a theoretical 8 and the axis is the full range.
  Everything worth seeing happens in the top two percent, where it cannot be.

  The byte histogram of a padded sampler bank is one spike at 0x00 and a flat
  line, because a bin 60x taller than the rest sets the axis for all of them.

The fix is a choice of vertical axis. The risk the fix carries is that a
rescaled chart is indistinguishable from an absolute one by eye -- an entropy
plot spanning 7.90 to 7.95 looks exactly like one spanning 0 to 8 -- so a chart
that rescales without saying so states a measurement it did not make. Every
test here is really about the caption.

Same for scope: these views used to be whole-file only, and a picture captioned
as the file that is really one chunk is the same lie in the horizontal.
"""

import asyncio
import struct

import pytest

from acidcat.core.forensics import viz
from acidcat.tui_theme import PALETTE, ramp_color

pytest.importorskip("textual")

from acidcat.tui_app.app import AcidcatTUI     # noqa: E402


def _run(scenario):
    asyncio.run(scenario())


# ── the axis primitive ────────────────────────────────────────────────

class TestScaleValues:
    def test_absolute_uses_the_axis_it_was_given(self):
        norm, lo, hi, _ = viz.scale_values([0, 4, 8], "absolute",
                                           floor=0.0, ceiling=8.0)
        assert norm == [0.0, 0.5, 1.0] and (lo, hi) == (0.0, 8.0)

    def test_auto_stretches_a_narrow_band_to_the_full_height(self):
        """The entropy complaint, in one assertion."""
        vals = [7.90, 7.92, 7.95]
        flat, *_ = viz.scale_values(vals, "absolute", floor=0.0, ceiling=8.0)
        assert max(flat) - min(flat) < 0.01, "precondition: absolute is flat here"
        norm, *_ = viz.scale_values(vals, "auto")
        assert norm == [0.0, pytest.approx(0.4), 1.0]

    def test_auto_names_the_window_it_chose(self):
        _n, _lo, _hi, label = viz.scale_values([7.90, 7.95], "auto")
        assert "7.9" in label, f"the caption must give the range away: {label!r}"

    def test_flat_data_is_reported_flat_not_full(self):
        """"everything is identical" and "everything is at maximum" are
        different findings and must not draw the same."""
        norm, _lo, _hi, label = viz.scale_values([5.0] * 4, "auto")
        assert norm == [0.0] * 4
        assert "flat" in label

    def test_log_lifts_the_rest_off_the_floor(self):
        """The histogram complaint: one bin 65x the others."""
        counts = [781] * 256
        counts[0] = 50786
        lin, *_ = viz.scale_values(counts, "absolute", floor=0.0)
        assert lin[1] < 0.02, "precondition: linear buries the other bins"
        log, *_ = viz.scale_values(counts, "log", floor=0.0)
        assert 0.5 < log[1] < 0.9 and log[0] == 1.0

    def test_clip_says_how_many_it_clipped(self):
        vals = [10] * 100 + [10000, 20000]
        _n, _lo, hi, label = viz.scale_values(vals, "clip", floor=0.0)
        assert hi < 10000
        assert "above" in label, f"silent clipping: {label!r}"

    def test_empty_input_is_not_a_crash(self):
        assert viz.scale_values([], "auto") == ([], 0.0, 0.0, "auto")

    def test_every_advertised_mode_is_implemented(self):
        """SCALES is what a caller iterates; a name in it that falls through to
        the absolute branch would be a mode that silently does nothing."""
        vals = [1, 5, 2, 900]
        seen = {}
        for m in viz.SCALES:
            norm, _lo, _hi, label = viz.scale_values(vals, m, floor=0.0)
            assert len(norm) == len(vals)
            assert all(0.0 <= v <= 1.0 for v in norm), m
            seen[m] = label
        assert len(set(seen.values())) == len(viz.SCALES), (
            f"two modes produced the same caption: {seen}")


class TestColumnPeaks:
    def test_it_samples_the_way_the_chart_does(self):
        """Colour is applied per cell from this; if it disagreed with
        braille_line the picture and its colours would describe two different
        datasets. A spike must land in the cell that is drawn tall."""
        vals = [0.0] * 64
        vals[32] = 1.0
        peaks = viz.column_peaks(vals, 16)
        assert len(peaks) == 16
        hot = [i for i, v in enumerate(peaks) if v > 0.5]
        assert len(hot) == 1, f"the spike should occupy exactly one cell: {hot}"
        rows = viz.braille_line(vals, width=16, height=4, vmin=0.0, vmax=1.0)
        assert rows[0][hot[0]] != "⠀", (
            "the cell coloured hot is not the cell drawn tall")

    def test_degenerate_widths_do_not_raise(self):
        assert viz.column_peaks([], 8) == []
        assert viz.column_peaks([1, 2], 0) == []


class TestNoBarCanVanish:
    """A chart with more data than columns must not drop the tall bar.

    Found by the test above. braille_line point-sampled its input, so drawing
    256 histogram bins across 69 cells looked at 138 of them and never read the
    other 118. A file whose distribution spikes at one of those byte values --
    a padded bank, a fill byte, a single-byte XOR key -- rendered as a flat
    chart with nothing indicating a bar had been skipped. Silent omission in
    the view whose entire job is showing you the outlier.
    """

    def test_a_spike_at_any_byte_value_is_drawn(self):
        invisible = []
        for spike in range(256):
            counts = [10] * 256
            counts[spike] = 100000
            rows = viz.braille_line(counts, width=69, height=6, vmin=0, fill=True)
            if all(ch == "⠀" for ch in rows[0]):
                invisible.append(spike)
        assert not invisible, (
            f"{len(invisible)} byte values render invisible: {invisible[:8]}")

    def test_downsampling_reports_the_peak_not_a_sample(self):
        vals = [0] * 1000
        vals[500] = 42
        assert max(viz._sample_columns(vals, 20)) == 42

    def test_upsampling_still_interpolates_smoothly(self):
        """A four-point line must not become four steps."""
        cols = viz._sample_columns([0, 1, 2, 3], 16)
        assert len(cols) == 16 and cols[0] == 0 and cols[-1] == 3
        assert cols == sorted(cols), "an upsampled ramp should stay monotonic"

    def test_the_chart_and_its_colours_agree(self):
        """column_peaks feeds the colour; braille_line draws the bar. They read
        the same sampling now, and must keep doing so."""
        import random
        random.seed(2)
        vals = [random.random() for _ in range(500)]
        peaks = viz.column_peaks(vals, 40)
        rows = viz.braille_line(vals, width=40, height=8, vmin=0.0, vmax=1.0)
        tallest = peaks.index(max(peaks))
        assert rows[0][tallest] != "⠀", (
            "the cell coloured hottest is not the cell drawn tallest")


class TestRampColor:
    def test_the_ends_are_the_palette_ends(self):
        assert ramp_color(0.0).lower() == PALETTE[0].lower()
        assert ramp_color(1.0).lower() == PALETTE[-1].lower()

    def test_it_interpolates_rather_than_quantising(self):
        """Eight stops cannot show a magnitude; the point is the in-between."""
        shades = {ramp_color(i / 40) for i in range(41)}
        assert len(shades) > len(PALETTE) * 2

    def test_out_of_range_and_nan_are_clamped(self):
        assert ramp_color(-5) == ramp_color(0.0)
        assert ramp_color(99) == ramp_color(1.0)
        assert ramp_color(float("nan")) == ramp_color(0.0)


# ── byte ranges in the file-backed views ──────────────────────────────

@pytest.fixture
def two_halves(tmp_path):
    """Zeros then noise, so a range genuinely changes the answer."""
    import random
    random.seed(11)
    p = tmp_path / "halves.bin"
    p.write_bytes(b"\x00" * 8192
                  + bytes(random.randrange(256) for _ in range(8192)))
    return str(p)


class TestByteRanges:
    def test_entropy_over_a_range_reads_that_range(self, two_halves):
        lo, _s, _sm = viz.file_entropy(two_halves, windows=4, start=0, end=8192)
        hi, _s, _sm = viz.file_entropy(two_halves, windows=4, start=8192)
        assert max(lo) < 0.1, "the zero half should be flat at zero"
        assert min(hi) > 7.0, "the noise half should be near 8"

    def test_the_reported_size_is_the_range_not_the_file(self, two_halves):
        _v, size, _s = viz.file_entropy(two_halves, windows=4, start=8192)
        assert size == 8192

    def test_the_default_is_still_the_whole_file(self, two_halves):
        a = viz.file_entropy(two_halves, windows=8)
        b = viz.file_entropy(two_halves, windows=8, start=0, end=None)
        assert a == b

    def test_hilbert_over_a_range_maps_that_range(self, two_halves):
        grid, _side, _s = viz.hilbert_from_file(two_halves, order=3,
                                                start=0, end=8192)
        cells = [c for row in grid for c in row if c is not None]
        assert cells and max(cells) == 0, "the zero half should map to zeros"

    def test_a_range_past_the_end_is_clamped_not_crashed(self, two_halves):
        vals, size, _s = viz.file_entropy(two_halves, windows=4,
                                          start=999999, end=1000000)
        assert vals == [] and size == 0

    def test_a_reversed_range_yields_nothing(self, two_halves):
        _v, size, _s = viz.file_entropy(two_halves, windows=4,
                                        start=8000, end=10)
        assert size == 0


# ── the keys, and what the caption admits to ──────────────────────────

@pytest.fixture
def wav(tmp_path):
    import random
    random.seed(5)
    audio = bytes(random.randrange(256) for _ in range(40000))
    body = b"WAVE"
    body += b"fmt " + struct.pack("<I", 16) + struct.pack(
        "<HHIIHH", 1, 2, 44100, 176400, 4, 16)
    body += b"data" + struct.pack("<I", len(audio)) + audio
    p = tmp_path / "t.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p)


def _caption(app, mode):
    return app._viz_render(mode).plain.splitlines()[0]


class TestScaleKey:
    def test_S_cycles_and_the_caption_names_the_axis(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                seen = []
                for _ in range(len(app._VIZ_SCALES["entropy"])):
                    seen.append(_caption(app, "entropy"))
                    await pilot.press("S")
                    await pilot.pause()
                assert all("scale " in c for c in seen), seen
                assert len(set(seen)) == len(seen), (
                    f"a scale change left the caption identical: {seen}")
        _run(scenario)

    def test_S_declines_on_a_view_with_no_axis(self, wav):
        """hex and hilbert have no magnitude axis. A key that appears to work
        and changes nothing is the defect this whole file is about."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "hilbert"
                before = dict(app._viz_scale)
                await pilot.press("S")
                await pilot.pause()
                assert app._viz_scale == before
                assert app._scale_for("hilbert") is None
        _run(scenario)

    def test_the_default_axis_is_the_untransformed_one(self, wav):
        """A first-run picture must not be secretly stretched."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                assert app._scale_for("entropy") == "absolute"
                assert app._scale_for("histogram") == "absolute"
                assert "0-8" in _caption(app, "entropy")
        _run(scenario)


class TestScopeKey:
    def test_r_scopes_the_graph_to_the_selected_node(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                assert "whole file" in _caption(app, "entropy")
                for _ in range(2):
                    await pilot.press("down")
                await pilot.press("r")
                await pilot.pause()
                lo, hi, _label = app._viz_range()
                assert (lo, hi) != (0, app.fsize), "scope did not narrow"
                cap = _caption(app, "entropy")
                assert "whole file" not in cap and f"0x{lo:08x}" in cap
        _run(scenario)

    def test_r_toggles_back(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                await pilot.press("down")
                await pilot.press("r")
                await pilot.press("r")
                await pilot.pause()
                assert app._viz_range() == (0, app.fsize, "whole file")
        _run(scenario)

    def test_a_selection_with_no_bytes_says_it_fell_back(self, wav):
        """Silently showing the whole file under a caption that claims a region
        is worse than not scoping at all."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                app._viz_scope = "region"
                app._cur_node = None
                lo, hi, label = app._viz_range()
                assert (lo, hi) == (0, app.fsize)
                assert "whole file" in label and "no bytes" in label
        _run(scenario)

    def test_r_declines_in_the_hex_view(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                assert app._view == "hex"
                await pilot.press("r")
                await pilot.pause()
                assert app._viz_scope == "file"
        _run(scenario)

    def test_r_is_still_a_toggle(self, wav):
        """The arrows are directional; the letter key stays a toggle, because
        that is what it already was for anyone who learned it."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                await pilot.press("down")           # a node with bytes
                await pilot.press("r")
                await pilot.pause()
                assert app._viz_scope == "region"
                await pilot.press("r")
                await pilot.pause()
                assert app._viz_scope == "file"
        _run(scenario)

    def test_the_container_marker_is_dropped_when_scoped(self, wav):
        """It answers "where does the FILE end", which is meaningless once the
        x axis is one chunk -- and it would be drawn at a wrong column."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                for _ in range(2):
                    await pilot.press("down")
                await pilot.press("r")
                await pilot.pause()
                assert "container ends at" not in app._viz_render("entropy").plain
        _run(scenario)


class TestArrowsOnAFocusedGraph:
    """Arrows drive the graph they are pointed at.

    Mapped to the axis they move along: up/down is the vertical axis, so it is
    the scale; left/right is the horizontal extent, so it is how much of the
    file. Left widens and right narrows rather than both toggling -- a toggle
    on two opposed keys means neither one tells you which way you are going.

    The whole risk of this is theft. These keys belong to the tree and to the
    hex dump's scrolling, and both must keep them.
    """

    async def _on_graph(self, pilot, app, view="entropy"):
        """Focus the byte pane with a graph in it, the only state that arms
        the arrows."""
        app._view = view
        while app._focused_pane() != "hexwrap":
            await pilot.press("tab")
            await pilot.pause()
        app._paint_bytes()
        await pilot.pause()

    def test_up_and_down_change_the_scale(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await self._on_graph(pilot, app)
                assert app._scale_for("entropy") == "absolute"
                await pilot.press("up")
                await pilot.pause()
                assert app._scale_for("entropy") == "auto"
        _run(scenario)

    def test_down_reverses_up(self, wav):
        """Not another forward step: an axis you can only cycle one way makes
        you walk the whole list to undo a keypress."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await self._on_graph(pilot, app, view="histogram")
                assert len(app._VIZ_SCALES["histogram"]) == 3
                await pilot.press("up")
                await pilot.press("up")
                await pilot.pause()
                two_up = app._scale_for("histogram")
                await pilot.press("down")
                await pilot.pause()
                assert app._scale_for("histogram") != two_up
                await pilot.press("down")
                await pilot.pause()
                assert app._scale_for("histogram") == "absolute"
        _run(scenario)

    def test_down_from_the_first_scale_wraps_to_the_last(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await self._on_graph(pilot, app, view="histogram")
                await pilot.press("down")
                await pilot.pause()
                assert app._scale_for("histogram") == "clip"
        _run(scenario)

    def test_left_and_right_move_the_selection(self, wav):
        """Up and down are spent on the scale, so without this, focusing a
        graph freezes what you are looking at."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await self._on_graph(pilot, app)
                tree = app.query_one("#tree")
                start = tree.cursor_line
                await pilot.press("right")
                await pilot.pause()
                assert tree.cursor_line == start + 1
                await pilot.press("left")
                await pilot.pause()
                assert tree.cursor_line == start
        _run(scenario)

    def test_it_moves_the_same_cursor_the_tree_moves(self, wav):
        """One cursor, so the two panes cannot disagree about the selection."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await self._on_graph(pilot, app)
                await pilot.press("right")
                await pilot.pause()
                tree = app.query_one("#tree")
                assert app._cur_node is tree.cursor_node
        _run(scenario)

    def test_the_end_of_the_tree_says_so(self, wav):
        """A key that stops working without a word reads as a broken build."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await self._on_graph(pilot, app)
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                for _ in range(80):
                    await pilot.press("left")
                await pilot.pause()
                assert any("start of the tree" in n for n in notes), notes
        _run(scenario)

    def test_the_tree_keeps_its_arrows(self, wav):
        """The graph is on screen; focus is not on it. Arrows must move the
        cursor, or the tree becomes undrivable whenever a chart is shown."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                app._paint_bytes()
                await pilot.pause()
                assert app._focused_pane() == "tree"
                tree = app.query_one("#tree")
                before = tree.cursor_line
                await pilot.press("down")
                await pilot.pause()
                assert tree.cursor_line != before, "the tree lost its arrows"
                assert app._scale_for("entropy") == "absolute", (
                    "the graph stole a keypress meant for the tree")
        _run(scenario)

    def test_the_hex_dump_keeps_its_arrows(self, wav):
        """The file holds far more than a screen, and the arrows are how you
        read on. 2.0: the pane is drawn to its height and does not scroll, so
        the arrows move its window a row at a time."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                assert app._view == "hex"
                await pilot.press("tab")
                await pilot.pause()
                assert app._focused_pane() == "hexwrap"

                def first():
                    return int(app.query_one("#hex").render().plain.split()[0], 16)
                at = first()
                for _ in range(6):
                    await pilot.press("down")
                await pilot.pause()
                assert first() == at + 6 * app._hex_width(), (
                    "arrows stopped moving the dump")
        _run(scenario)

    def test_arrows_are_dormant_until_the_graph_has_focus(self, wav):
        """check_action is the gate; assert on it directly so a failure names
        the cause rather than a downstream symptom."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                for action in app._VIZ_ARROWS:
                    assert app.check_action(action, ()) is False, action
                await self._on_graph(pilot, app)
                for action in app._VIZ_ARROWS:
                    assert app.check_action(action, ()) is True, action
        _run(scenario)

    def test_up_on_the_hilbert_map_declines(self, wav):
        """It has no magnitude axis. Silently doing nothing is the defect."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await self._on_graph(pilot, app, view="hilbert")
                await pilot.press("up")
                await pilot.pause()
                assert app._viz_scale == {}
        _run(scenario)

    def test_the_chart_says_the_arrows_exist(self, wav):
        """A key nobody can discover is a key nobody has."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                for view in ("entropy", "histogram"):
                    app._view = view
                    assert "arrows" in app._viz_render(view).plain, view
        _run(scenario)


class TestRegionGraphFollowsTheSelection:
    """A region-scoped graph tracks the cursor as it moves.

    Without this, `r` took a snapshot: the caption named a chunk, you moved off
    it, and the picture stayed. A stale chart under a live caption is worse than
    either an honest whole-file view or no scoping at all, because nothing on
    screen says the two disagree.
    """

    def _ranges(self, app):
        return app._viz_range()[:2]

    def test_moving_the_tree_redraws_a_region_graph(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                await pilot.press("down")
                await pilot.press("r")
                await pilot.pause()
                first = app._viz_drawn
                assert first is not None
                await pilot.press("down")
                await pilot.pause()
                assert app._viz_drawn != first, (
                    "the chart did not follow the cursor off the old chunk")
                assert app._viz_drawn == self._ranges(app), (
                    "what is drawn and what the caption describes disagree")
        _run(scenario)

    def test_the_caption_follows_too(self, wav):
        """The picture and its label move together or the label is a lie."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                await pilot.press("down")
                await pilot.press("r")
                await pilot.pause()
                before = app._viz_render("entropy").plain.splitlines()[0]
                await pilot.press("down")
                await pilot.pause()
                after = app._viz_render("entropy").plain.splitlines()[0]
                assert before != after
                lo, _hi, _l = app._viz_range()
                assert f"0x{lo:08x}" in after
        _run(scenario)

    def test_a_whole_file_graph_does_not_redraw_on_every_keystroke(self, wav):
        """Scoped to the file, the selection cannot change the picture, so
        following it would be pure cost on the largest files."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                app._paint_bytes()
                await pilot.pause()
                calls = []
                real = app._paint_bytes
                app._paint_bytes = lambda: (calls.append(1), real())[1]
                # cleared so the scope check is the only thing standing between
                # a keystroke and a repaint; otherwise the range guard alone
                # would carry this test and the scope check could be deleted
                # without anything noticing.
                app._viz_drawn = None
                for _ in range(4):
                    await pilot.press("down")
                await pilot.pause()
                assert not calls, f"repainted {len(calls)} times for nothing"
        _run(scenario)

    def test_the_hex_view_is_untouched_by_all_this(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                assert app._view == "hex"
                app._viz_scope = "region"
                await pilot.press("down")
                await pilot.pause()
                assert app._viz_drawn is None
        _run(scenario)

    def test_moving_onto_a_node_with_no_bytes_still_redraws(self, wav):
        """The case that put this on the highlight event rather than in _show.

        _show returns early for a node with no range of its own, so a follow
        living there would leave the chart on the previous chunk while the
        selection had moved off it.

        The state is built rather than hunted: a WAV's tree happens to give
        every node a range, so a version of this that walked the tree hoping to
        find one passed without ever reaching the branch -- which is how it
        first shipped. Dropping the entry is the same condition the code reads.
        """
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                await pilot.press("down")
                await pilot.press("r")
                await pilot.pause()
                assert app._viz_scope == "region"
                scoped = app._viz_drawn
                assert scoped is not None and scoped != (0, app.fsize)

                await pilot.press("down")          # learn the next node
                await pilot.pause()
                target = app._cur_node
                await pilot.press("up")
                await pilot.pause()
                assert app._meta(target) is not None, (
                    "precondition: the node needs a range to remove")
                target.data = None                 # take its byte range away

                await pilot.press("down")          # onto it, now range-less
                await pilot.pause()
                assert app._viz_drawn == (0, app.fsize), (
                    "the chart stayed on the old chunk after the selection "
                    "left it")
                assert "no bytes" in app._viz_range()[2]
        _run(scenario)

    def test_the_repaint_is_skipped_when_the_range_is_unchanged(self, wav):
        """Holding an arrow through fields that share a parent's range should
        not redraw the same picture over and over."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                await pilot.press("down")
                await pilot.press("r")
                await pilot.pause()
                calls = []
                real = app._paint_bytes
                app._paint_bytes = lambda: (calls.append(1), real())[1]
                app._follow_selection()
                app._follow_selection()
                assert not calls, "redrew a range already on screen"
        _run(scenario)


class TestSmallRegionsDoNotLie:
    """Entropy over few bytes is a depressed number, and must not be shown as
    a plain one.

    Shannon entropy over n bytes cannot exceed log2(n): with 256 symbols and
    fewer than 256 draws, most values cannot appear at all. Two ways that bit
    when the graphs learned to scope:

      A 16-byte fmt chunk got one window per byte. Entropy of a single byte is
      0 by definition, so the header printed "min 0.00 mean 0.00 max 0.00",
      which reads as a region of one repeated byte.

      A 900-byte region drawn at 16 bytes per window tops out at 4.0 bits, so
      uniformly random data reported "max 4.09" against an axis labelled 0-8
      and read as structured.

    Neither is a wrong calculation. Both are correct numbers presented without
    the one fact that makes them interpretable.
    """

    def _head(self, app, n=3):
        return "\n".join(app._viz_render("entropy").plain.splitlines()[:n])

    async def _scoped(self, pilot, app, span):
        """Point the graph at a region of exactly `span` bytes."""
        app._view = "entropy"
        await pilot.press("down")
        await pilot.pause()
        app._bind_node(app._cur_node, 36, span, "#08F9DF", index=False)
        app._viz_scope = "region"

    def test_a_region_too_small_to_plot_says_so_instead_of_plotting_zero(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await self._scoped(pilot, app, 16)
                out = self._head(app)
                assert "too few" in out
                assert "0.00" not in out, (
                    "a flat line at zero reads as one repeated byte")
        _run(scenario)

    def test_a_coarse_region_states_the_ceiling_it_cannot_pass(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await self._scoped(pilot, app, 900)
                out = self._head(app)
                assert "cannot exceed" in out, out
                assert "4.0 bits" in out, out
        _run(scenario)

    def test_it_says_which_limit_you_are_looking_at(self, wav):
        """Pane-limited and data-limited are different situations: one is fixed
        by a wider terminal, the other cannot be fixed at all."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await self._scoped(pilot, app, 400)
                assert "set by the region not the pane" in self._head(app)
                app._bind_node(app._cur_node, 36, 40000, "#08F9DF", index=False)
                assert "set by the region not the pane" not in self._head(app)
        _run(scenario)

    def test_a_region_with_room_to_measure_stays_quiet(self, wav):
        """The hedge must not fire when the measurement is sound, or it stops
        being read."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await self._scoped(pilot, app, 40000)
                out = self._head(app)
                assert "cannot exceed" not in out and "too few" not in out, out
                assert "bits/byte" in out
        _run(scenario)

    def test_the_window_count_never_exceeds_what_the_bytes_support(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                for span in (32, 100, 900, 5000, 100000):
                    windows, _bound = app._entropy_windows(span)
                    assert windows >= 1
                    assert span // windows >= app._ENTROPY_MIN_WINDOW, (
                        f"{span} bytes split into {windows} windows")
        _run(scenario)


class TestEnteringAGraphScopesToTheSelection:
    """Hex is still what a file opens on -- bytes first. But once you ask for a
    shape, the shape of the chunk you are standing on is almost always the
    question: a 40-byte header is one column of a whole-file plot."""

    def test_leaving_hex_for_a_graph_scopes_to_the_region(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                assert app._view == "hex" and app._viz_scope == "file"
                await pilot.press("b")
                await pilot.pause()
                assert app._view != "hex"
                assert app._viz_scope == "region"
        _run(scenario)

    def test_it_only_applies_on_the_way_out_of_hex(self, wav):
        """Cycling between graphs must not keep re-imposing the default and
        undoing a scope the user chose."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await pilot.press("b")               # hex -> entropy, scopes
                await pilot.press("r")               # user widens it
                await pilot.pause()
                assert app._viz_scope == "file"
                await pilot.press("b")               # entropy -> hilbert
                await pilot.pause()
                assert app._viz_scope == "file", "the default overrode a choice"
        _run(scenario)

    def test_the_scope_is_still_togglable(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                await pilot.press("b")
                await pilot.pause()
                assert app._viz_scope == "region"
                await pilot.press("r")
                await pilot.pause()
                assert app._viz_scope == "file"
        _run(scenario)


class TestTheRegionSparkline:
    """A shape column in the region list, so you can tell compressed audio from
    padding before spending a descend on it. Off by default: it costs a read per
    row, which is nothing on 22 rows and real on 4,000."""

    def _screen(self, src):
        from acidcat.tui_app.screens import RegionsScreen
        return RegionsScreen([], "x", blob_src=src)

    def test_it_is_off_by_default(self):
        from acidcat.tui_app.screens import RegionsScreen
        assert RegionsScreen([], "x").show_shape is False

    def test_high_entropy_and_low_entropy_look_different(self, tmp_path):
        import random
        random.seed(4)
        p = tmp_path / "mixed.bin"
        p.write_bytes(bytes(random.randrange(256) for _ in range(40000))
                      + b"\x00" * 40000)
        rs = self._screen(str(p))
        hot = rs._shape({"offset": 0, "end": 40000})
        cold = rs._shape({"offset": 40000, "end": 80000})
        assert hot and cold and hot != cold
        assert hot.strip("@") == "", f"random data should read as full: {hot!r}"
        assert cold.strip() == "", f"zero padding should read as empty: {cold!r}"

    def test_it_declines_quietly_without_a_source(self):
        """The screen is constructible without one (older call sites, tests),
        and a missing source must not raise inside compose."""
        from acidcat.tui_app.screens import RegionsScreen
        assert RegionsScreen([], "x")._shape({"offset": 0, "end": 10}) == ""

    def test_an_unreadable_range_does_not_raise(self, tmp_path):
        p = tmp_path / "tiny.bin"
        p.write_bytes(b"\x00" * 8)
        assert self._screen(str(p))._shape({"offset": 999, "end": 9999}) == ""
