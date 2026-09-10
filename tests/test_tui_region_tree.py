"""Located regions belong in the tree, because that is what they are.

They used to live behind a modal: a second grammar for the file's contents, in
a UI that already had one. The tree is the file, and a region found inside it is
a child of the file -- so as tree nodes they get the hex pane, the graphs, `p`
and the cursor for free, since a node with a byte range is all any of those ever
needed.

Two consequences follow:

  Opening no longer scans. A 187 MB archive ground for minutes before the UI
  answered, for a scan nobody had asked for. The root is expandable instead, and
  expanding it is the ask -- which is what expanding a node means everywhere
  else in this tree.

  The list stops being how you navigate and becomes how you act in bulk. That is
  the one thing a tree cannot express: selecting some regions and extracting
  exactly those.
"""

import asyncio
import struct

from conftest import until

import pytest

pytest.importorskip("textual")

from acidcat.tui_app.app import AcidcatTUI          # noqa: E402
from textual.widgets import DataTable              # noqa: E402
from acidcat.tui_app.screens import RegionsScreen   # noqa: E402


async def _expandable_child(pilot, node):
    """The first expandable child of `node`, once the tree actually has one.

    Five call sites did this behind a flat `pilot.pause(0.5)` and indexed the
    result unguarded, so when a background expansion had been dispatched and
    not yet delivered the list was empty and the test died on an IndexError --
    naming a duration that turned out to be short rather than the condition
    that never became true.

    Same defect 1.2.0 and 1.2.1 fixed at four other call sites. This file was
    missed; it still carries 77 flat pauses and imports no waiter at all.
    """
    got = []

    def ready():
        got[:] = [c for c in node.children if c.allow_expand]
        return bool(got)

    await until(pilot, ready)
    assert got, "no expandable child ever appeared under %r" % (node.label,)
    return got[0]


def _run(scenario):
    asyncio.run(scenario())


def _page(serial, seq, *, bos=False, eos=False, body=b"\x11" * 2000):
    htype = (0x02 if bos else 0) | (0x04 if eos else 0)
    segs, rest = [], len(body)
    while rest >= 255:
        segs.append(255)
        rest -= 255
    segs.append(rest)
    return (b"OggS" + bytes([0, htype]) + struct.pack("<q", seq * 1000)
            + struct.pack("<I", serial) + struct.pack("<I", seq)
            + struct.pack("<I", 0) + bytes([len(segs)]) + bytes(segs) + body)


def _stream(serial, pages=12):
    return b"".join(_page(serial, i, bos=(i == 0), eos=(i == pages - 1))
                    for i in range(pages))


@pytest.fixture
def blob(tmp_path):
    p = tmp_path / "archive.blob"
    p.write_bytes(b"HDR!" + b"\x00" * 4000
                  + _stream(101) + _stream(202) + _stream(303))
    return str(p)


async def _scan(app, pilot, want_list=False):
    if want_list:
        app.action_locate_regions()
    else:
        app.query_one("#tree").root.expand()
    await pilot.pause(0.2)
    for _ in range(80):
        if app._regions is not None and not app._scanning:
            break
        await pilot.pause(0.1)


class TestOpeningDoesNotScan:
    def test_no_scan_starts_on_its_own(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                assert app._regions is None
                assert not app._scanning, "started a scan nobody asked for"
        _run(scenario)

    def test_the_root_offers_itself_for_expansion(self, blob):
        """Otherwise there is nothing on screen to suggest the file has
        contents, and no obvious way to ask."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                assert app.query_one("#tree").root.allow_expand is True
        _run(scenario)

    def test_expanding_the_root_runs_the_scan(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.4)
                await _scan(app, pilot)
                assert app._regions and len(app._regions) == 3
        _run(scenario)

    def test_a_walked_file_is_not_offered_a_scan(self, tmp_path):
        """`_scannable` is about containers no walker claims. A WAV has real
        chunks and expanding it must show those, not start a locate sweep."""
        n = 2000
        body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
                + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
                + b"data" + struct.pack("<I", n) + b"\x00" * n)
        p = tmp_path / "real.wav"
        p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

        async def scenario():
            app = AcidcatTUI(str(p))
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                assert app._scannable() is False
                assert not app._scanning
        _run(scenario)


class TestAScanAnnouncesItself:
    """A scan can run for minutes on a large file. Starting one without saying
    so leaves the key that started it looking broken for exactly that long.

    The announcement lived at one of the two callers, so expanding the file
    said it and `l` did not. It belongs to the scan, not to a way of asking
    for one.
    """

    def test_the_key_says_it_started_one(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                app.action_locate_regions()
                # A background round trip, waited on by its condition
                # rather than by a duration. The assert stays: `until`
                # returns a bool, so a timeout would read as a pass.
                await pilot.pause(0.1)
                await until(pilot, lambda: any('scanning' in n for n in notes))
                assert any("scanning" in n for n in notes), notes
        _run(scenario)

    def test_expanding_the_file_says_it_too(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                app.query_one("#tree").root.expand()
                await pilot.pause(0.3)
                said = [n for n in notes if "scanning" in n]
                assert said, notes
                assert len(said) == 1, f"announced twice: {said}"
        _run(scenario)

    def test_a_cached_list_does_not_claim_to_be_scanning(self, blob):
        """`l` after a scan is instant. Saying "scanning" over an instant
        answer is the same lie in the other direction."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                while len(app.screen_stack) > 1:
                    app.screen_stack[-1].dismiss(None)
                    await pilot.pause()
                notes = []
                app.notify = lambda m, **kw: notes.append(str(m))
                app.action_locate_regions()
                # A background round trip, waited on by its condition
                # rather than by a duration. The assert stays: `until`
                # returns a bool, so a timeout would read as a pass.
                await pilot.pause(0.1)
                await until(pilot, lambda: not [n for n in notes if 'scanning' in n])
                assert not [n for n in notes if "scanning" in n], notes
        _run(scenario)


class TestRegionsAreTreeNodes:
    def _region_nodes(self, app):
        return [c for c in app.query_one("#tree").root.children
                if (app._info(c) is not None and app._info(c).region is not None)]

    def test_the_scan_puts_them_under_the_file(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                assert len(self._region_nodes(app)) == 3
        _run(scenario)

    def test_the_scan_does_not_open_the_list(self, blob):
        """Expanding asked for the tree to be filled, not for a modal."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                assert not [s for s in app.screen_stack
                            if isinstance(s, RegionsScreen)]
        _run(scenario)

    def test_l_still_opens_the_list_without_rescanning(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                cached = app._regions
                app.action_locate_regions()
                # A background round trip, waited on by its condition
                # rather than by a duration. The assert stays: `until`
                # returns a bool, so a timeout would read as a pass.
                await pilot.pause(0.1)
                await until(pilot, lambda: [s for s in app.screen_stack if isinstance(s, RegionsScreen)])
                assert [s for s in app.screen_stack
                        if isinstance(s, RegionsScreen)]
                assert app._regions is cached, "l rescanned instead of reusing"
        _run(scenario)

    def test_a_region_node_carries_its_byte_range(self, blob):
        """This is what makes the hex pane, the graphs and `p` work on it with
        no special cases: it is an ordinary node."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                for node, r in zip(self._region_nodes(app), app._regions):
                    off, length, _accent = app._meta(node)
                    assert off == r["offset"]
                    assert length == r["length"]
        _run(scenario)

    def test_the_label_names_the_sniffed_format(self, blob):
        """Sniffing every region is what lets a node say `ogg` rather than
        `region`, and decides which are worth offering to expand."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                labels = [app._node_name(n) for n in self._region_nodes(app)]
                assert all("ogg" in x for x in labels), labels
        _run(scenario)


class TestExpandingARegionWalksIt:
    def test_it_hangs_the_chunks_under_the_region(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                node = [c for c in app.query_one("#tree").root.children
                        if (app._info(c) is not None and app._info(c).region is not None)][0]
                assert node.allow_expand is True
                assert not node.children, "walked before being asked"
                node.expand()
                await pilot.pause(0.5)
                assert node.children, "expanding did not walk the region"
        _run(scenario)

    def test_chunk_offsets_are_rebased_onto_the_parent(self, blob):
        """The walker sees a carved temp, so its offsets start at zero. Every
        other part of the UI reads the file that is open, so a child node
        carrying the temp's offsets would point the hex pane at the wrong
        bytes -- silently, since both are valid offsets."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                node = [c for c in app.query_one("#tree").root.children
                        if (app._info(c) is not None and app._info(c).region is not None)][0]
                base = app._regions[app._info(node).region]["offset"]
                assert base > 0, "fixture must not put region 0 at offset 0"
                node.expand()
                await pilot.pause(0.5)
                kids = [c for c in node.children if (app._meta(c) is not None)]
                assert kids
                for c in kids:
                    off, _len, _a = app._meta(c)
                    assert off >= base, (
                        f"child at 0x{off:08x} is below its region at "
                        f"0x{base:08x} -- offsets were not rebased")
        _run(scenario)

    def test_expanding_twice_does_not_double_the_children(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                node = [c for c in app.query_one("#tree").root.children
                        if (app._info(c) is not None and app._info(c).region is not None)][0]
                node.expand()
                await pilot.pause(0.5)
                first = len(node.children)
                node.collapse()
                node.expand()
                await pilot.pause(0.5)
                assert len(node.children) == first
        _run(scenario)


class TestTheTreeGoesAllTheWayDown:
    """The tree dead-ended two levels in: file > region > chunk, and a chunk
    would not open even when the walker had returned fields for it.

    The cause was one argument. `add_leaf` is `add(..., allow_expand=False)`,
    so the lazy region path had declared every chunk a leaf before asking
    whether it had anything under it -- and it never rendered the fields it was
    already being handed. Measured on this fixture the shallow walk returns 8
    fields across 2 chunks, all of them dropped.
    """

    def _chunks_of_a_region(self, app):
        node = [c for c in app.query_one("#tree").root.children
                if (app._info(c) is not None and app._info(c).region is not None)][0]
        node.expand()
        return node

    def test_a_chunk_with_fields_can_be_opened(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                region = self._chunks_of_a_region(app)
                await pilot.pause(0.5)
                openable = [c for c in region.children if c.allow_expand]
                assert openable, (
                    "level 3 unreachable: every chunk under the region was "
                    "declared a leaf")
        _run(scenario)

    def test_opening_it_shows_the_fields(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                region = self._chunks_of_a_region(app)
                await pilot.pause(0.5)
                chunk = await _expandable_child(pilot, region)
                chunk.expand()
                await pilot.pause(0.2)
                names = [app._node_name(f) for f in chunk.children]
                assert names, "expandable, but nothing under it"
                assert any("=" in n for n in names), names
        _run(scenario)

    def test_the_fields_carry_a_byte_range_in_the_parent_space(self, blob):
        """A field node is only worth having if the hex pane can follow it, and
        that means the region's base, not the carved temp's zero."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                region = self._chunks_of_a_region(app)
                await pilot.pause(0.5)
                base = app._regions[app._info(region).region]["offset"]
                chunk = await _expandable_child(pilot, region)
                chunk.expand()
                await pilot.pause(0.2)
                located = [app._meta(f) for f in chunk.children
                           if (app._meta(f) is not None)
                           and app._meta(f)[0] is not None]
                assert located, "no field node knew where it was"
                for off, _ln, _a in located:
                    assert off >= base, (
                        f"field at 0x{off:08x} sits below its region at "
                        f"0x{base:08x}")
        _run(scenario)

    def test_a_chunk_with_nothing_under_it_stays_shut(self, blob):
        """An arrow that opens onto nothing is a worse lie than no arrow."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                region = self._chunks_of_a_region(app)
                await pilot.pause(0.5)
                for c in region.children:
                    if not c.allow_expand:
                        continue
                    c.expand()
                    await pilot.pause(0.2)
                    assert c.children, (
                        f"{app._node_name(c)!r} claims children and has none")
        _run(scenario)


def _every(node):
    yield node
    for c in node.children:
        yield from _every(c)


class TestARebuildKeepsYourPlaceAtDepth:
    """Editing, undoing or saving rebuilds the tree, and the rebuild puts back
    what was open. It only ever looked one level down for what that was.

    That limitation was invisible for a while, and measurably so: nothing below
    the top level could be OPEN, because fields and rows are leaves and the
    lazily walked chunks under a region carried no key to be recorded by. Both
    halves are gone now -- a region's chunks expand, and they have paths -- so
    the one-level walk finally has something to lose, and does not.

    A path also has to survive being replayed through a level that does not
    exist yet: the region's chunks are walked on demand, so reopening has to
    rebuild that level before the path's next component can resolve.
    """

    def _open_to_depth(self, app, pilot):
        tree = app.query_one("#tree")
        region = [c for c in tree.root.children
                  if app._info(c) is not None and app._info(c).region is not None][0]
        region.expand()
        return region

    def test_a_chunk_inside_a_region_is_still_open_afterwards(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 50)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                region = self._open_to_depth(app, pilot)
                await pilot.pause(0.5)
                chunk = await _expandable_child(pilot, region)
                chunk.expand()
                await pilot.pause(0.3)
                want = app._info(chunk).path
                assert want is not None, "a lazily walked chunk got no path"

                app._load()
                await pilot.pause(0.5)
                again = [n for n in _every(app.query_one("#tree").root)
                         if app._info(n) is not None
                         and app._info(n).path == want]
                assert again, "the chunk did not survive the rebuild at all"
                assert again[0].is_expanded, "the rebuild collapsed it"
                assert again[0].children, "reopened, but its fields are gone"
        _run(scenario)

    def test_the_region_under_it_was_re_walked_to_get_there(self, blob):
        """The level in between is lazy, so replaying the path had to rebuild
        it. If reopening only looked things up, the walk would stop at the
        region and the chunk would never be found."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 50)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                region = self._open_to_depth(app, pilot)
                await pilot.pause(0.5)
                chunk = await _expandable_child(pilot, region)
                chunk.expand()
                await pilot.pause(0.3)
                app._load()
                await pilot.pause(0.5)
                root = app.query_one("#tree").root
                rnodes = [c for c in root.children
                          if app._info(c) is not None
                          and app._info(c).region is not None]
                assert rnodes and rnodes[0].children, (
                    "the region came back empty, so nothing below it could be "
                    "restored")
        _run(scenario)

    def test_a_path_that_no_longer_resolves_lands_on_its_deepest_ancestor(self, blob):
        """An edit can change what is there. Dropping the user at the root
        because the last component went missing is worse than stopping at the
        level that still exists."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 50)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                region = self._open_to_depth(app, pilot)
                await pilot.pause(0.5)
                rpath = app._info(region).path
                landed = app._reopen(rpath + (("chunk", "NOPE", 99),))
                assert app._info(landed).path == rpath, (
                    "a broken tail should stop at the last good level")
        _run(scenario)

    def test_the_open_set_is_gathered_from_the_whole_tree(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 50)) as pilot:
                await pilot.pause(0.3)
                await _scan(app, pilot)
                region = self._open_to_depth(app, pilot)
                await pilot.pause(0.5)
                chunk = await _expandable_child(pilot, region)
                chunk.expand()
                await pilot.pause(0.3)
                paths = app._open_paths(app.query_one("#tree").root)
                assert app._info(chunk).path in paths, (
                    "an open node below the top level was not recorded")
                assert paths == sorted(paths, key=len), (
                    "parents must be replayed before their children")
        _run(scenario)


class TestTheHelpColumnsToo:
    """The same collision, found in the help screen while documenting the fix
    for it: the key column padded to 16 and `shift+left/right` is 16 wide.
    Nothing that pads without a separator is safe from its own widest entry.
    """

    def test_the_widest_key_is_not_glued_to_its_description(self, blob):
        from textual.widgets import Static

        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(150, 44)) as pilot:
                await pilot.pause(0.3)
                app.action_help()
                await pilot.pause(0.3)
                text = app.screen_stack[-1].query_one(Static).content.plain
                assert "shift+left/right" in text, "the key is undocumented"
                line = [x for x in text.splitlines()
                        if "shift+left/right" in x][0]
                after = line.split("shift+left/right", 1)[1]
                assert after.startswith("  "), repr(line)
        _run(scenario)


class TestTheLabelColumns:
    """`comments` is exactly eight characters, and the id was padded to eight
    with no separator after it, so it rendered as `comments0x0000bb31`. The pad
    was doing the separator's job, which works right up until an id is as wide
    as the pad.
    """

    def test_an_id_that_fills_the_pad_still_has_a_gap_after_it(self):
        lbl = AcidcatTUI._chunk_label(
            AcidcatTUI, {"id": "comments", "size": 12}, 0xbb31, 8).plain
        assert "comments0x" not in lbl, lbl
        assert "comments  0x0000bb31" in lbl, lbl

    def test_the_column_widens_for_the_widest_id_in_the_group(self):
        chunks = [{"id": "OggS"}, {"id": "comments"}]
        assert AcidcatTUI._id_width(chunks) == 8
        starts = [AcidcatTUI._chunk_label(AcidcatTUI, c, 0, 8).plain.index("0x")
                  for c in chunks]
        assert len(set(starts)) == 1, "offsets did not line up"

    def test_a_very_long_id_is_capped_rather_than_shoving_the_row_across(self):
        wide = [{"id": "x" * 40}]
        assert AcidcatTUI._id_width(wide) == 12

    def test_a_shortened_id_says_it_was_shortened(self):
        """The column is capped, so a long id gets cut. An id printed whole
        when it is not whole names a chunk that is not in the file."""
        lbl = AcidcatTUI._chunk_label(
            AcidcatTUI, {"id": "SampleParameters", "size": 4}, 0, 12).plain
        assert "SampleParameters" not in lbl
        assert "…" in lbl, lbl

    def test_an_id_that_fits_is_left_alone(self):
        lbl = AcidcatTUI._chunk_label(
            AcidcatTUI, {"id": "fmt ", "size": 16}, 0, 6).plain
        assert "…" not in lbl
        assert "fmt" in lbl

    def test_each_chunk_keeps_its_own_colour(self):
        """The id is drawn in the accent that chunk's bytes are highlighted in,
        which is the only thing tying a tree row to a stripe in the hex pane.
        Sharing one builder must not flatten them to a single colour."""
        a = AcidcatTUI._chunk_label(AcidcatTUI, {"id": "fmt ", "size": 1}, 0, 6,
                                    False, "#ff0000")
        b = AcidcatTUI._chunk_label(AcidcatTUI, {"id": "fmt ", "size": 1}, 0, 6,
                                    False, "#00ff00")
        assert a.spans[1].style != b.spans[1].style

    def test_both_tree_builders_use_it(self):
        """There were two label builders, one padding to 6 and one to 8, and
        the collision was fixed in neither until they became one. Each of them
        has to go through the shared one, or the next fix lands in half the
        tree again.

        `_attach_children` is the second one now: every level below the top is
        built by it, whatever engine found the children."""
        import inspect as _i
        for fn in (AcidcatTUI._load, AcidcatTUI._attach_children):
            src = _i.getsource(fn)
            assert "self._chunk_label(" in src, fn.__name__
            assert "lbl.append(f\"{cid" not in src, (
                f"{fn.__name__} still formats an id column of its own")


class TestSelectingForExtraction:
    """The one job a tree cannot do: mark some regions and extract exactly
    those."""

    async def _list(self, app, pilot):
        """Open the region list and wait for it to actually be there.

        Waiting for the SCAN is not enough: pushing the screen and mounting its
        table are separate steps, and a fixed pause that covers both on an idle
        machine does not cover them under a full-suite load. This failed once in
        2,689 tests, in the full run only, on a NoMatches for the table -- which
        is a race dressed as a flake.
        """
        from textual.widgets import DataTable
        app.action_locate_regions()
        await pilot.pause(0.2)
        for _ in range(80):
            if app._regions is not None and not app._scanning:
                break
            await pilot.pause(0.1)
        for _ in range(80):
            screens = [x for x in app.screen_stack
                       if isinstance(x, RegionsScreen)]
            if screens:
                try:
                    screens[-1].query_one("#regtable", DataTable)
                    return screens[-1]
                except Exception:
                    pass
            await pilot.pause(0.05)
        raise AssertionError("the region list never mounted its table")

    def _screen(self, app):
        # the LIVE one: toggling re-pushes, so the first in the stack is stale
        return [s for s in app.screen_stack if isinstance(s, RegionsScreen)][-1]

    def test_space_marks_and_unmarks(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                s = await self._list(app, pilot)
                assert app._region_sel == set()
                s.action_toggle_sel()
                # A mark dismisses the list and the app reopens it, so the
                # wait is for that round trip rather than for a duration. The
                # assert stays: `until` returns a bool, so without it a
                # timeout would read as a pass.
                await pilot.pause(0.1)
                await until(pilot, lambda: app._region_sel == {0})
                assert app._region_sel == {0}

                # The cursor ADVANCES after a mark, so the list can be walked
                # by holding space. Toggling again therefore marks the NEXT
                # row -- come back to row 0 to unmark it.
                scr = self._screen(app)
                scr.query_one("#regtable", DataTable).move_cursor(row=0)
                scr.action_toggle_sel()
                await pilot.pause(0.1)
                await until(pilot, lambda: app._region_sel == set())
                assert app._region_sel == set()
        _run(scenario)

    def test_a_selects_all_then_none(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                s = await self._list(app, pilot)
                s.action_select_all()
                await pilot.pause(0.1)
                await until(pilot, lambda: app._region_sel == {0, 1, 2})
                assert app._region_sel == {0, 1, 2}
                self._screen(app).action_select_all()
                await pilot.pause(0.1)
                await until(pilot, lambda: app._region_sel == set())
                assert app._region_sel == set()
        _run(scenario)

    def test_extract_takes_exactly_the_marked_ones(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await self._list(app, pilot)
                got = {}
                app._extract = lambda regs: got.__setitem__(
                    "offsets", [r["offset"] for r in regs])
                app._region_sel = {0, 2}
                app._show_regions(app._regions)
                await pilot.pause(0.3)
                self._screen(app).action_extract()
                # A background round trip, waited on by its condition
                # rather than by a duration. The assert stays: `until`
                # returns a bool, so a timeout would read as a pass.
                await pilot.pause(0.1)
                await until(pilot, lambda: got.get('offsets') is not None)
                assert got["offsets"] == [app._regions[0]["offset"],
                                          app._regions[2]["offset"]]
        _run(scenario)

    def test_extract_falls_back_to_the_cursor_when_nothing_is_marked(self, blob):
        """Selection must not become mandatory for the single-region case."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await self._list(app, pilot)
                got = {}
                app._extract = lambda regs: got.__setitem__("n", len(regs))
                self._screen(app).action_extract()
                # A background round trip, waited on by its condition
                # rather than by a duration. The assert stays: `until`
                # returns a bool, so a timeout would read as a pass.
                await pilot.pause(0.1)
                await until(pilot, lambda: got.get('n') == 1)
                assert got["n"] == 1
        _run(scenario)

    def test_extract_all_ignores_the_selection(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await self._list(app, pilot)
                got = {}
                app._extract = lambda regs: got.__setitem__("n", len(regs))
                app._region_sel = {1}
                app._show_regions(app._regions)
                await pilot.pause(0.3)
                self._screen(app).action_extract_all()
                # A background round trip, waited on by its condition
                # rather than by a duration. The assert stays: `until`
                # returns a bool, so a timeout would read as a pass.
                await pilot.pause(0.1)
                await until(pilot, lambda: got.get('n') == 3)
                assert got["n"] == 3
        _run(scenario)

    def test_the_selection_survives_the_list_being_rebuilt(self, blob):
        """It is re-pushed on every toggle and whenever a scan lands. A
        selection that did not survive that would be worse than none."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await self._list(app, pilot)
                app._region_sel = {1, 2}
                app._show_regions(app._regions)
                await pilot.pause(0.3)
                assert self._screen(app).selected == {1, 2}
        _run(scenario)

    def test_the_selection_belongs_to_the_view(self, blob):
        assert "_region_sel" in AcidcatTUI._FRAME_ATTRS


class TestTheListReopensWhereYouLeftIt:
    """Marking re-pushes the screen, so a cursor it does not carry is a cursor
    it cannot return to. Marking five regions in a list of 266 meant scrolling
    from the top five times -- in the feature the list exists for.

    The screen always computed the right row and even advanced it by one after
    a mark, so that holding space walks the list. The app was throwing it away.
    """

    async def _list(self, app, pilot):
        from textual.widgets import DataTable as _DT
        app.action_locate_regions()
        await pilot.pause(0.2)
        for _ in range(80):
            if app._regions is not None and not app._scanning:
                break
            await pilot.pause(0.1)
        for _ in range(80):
            screens = [x for x in app.screen_stack
                       if isinstance(x, RegionsScreen)]
            if screens:
                try:
                    screens[-1].query_one("#regtable", _DT)
                    return screens[-1]
                except Exception:
                    pass
            await pilot.pause(0.05)
        raise AssertionError("the region list never mounted")

    def _row(self, app):
        scr = [x for x in app.screen_stack if isinstance(x, RegionsScreen)][-1]
        return scr.query_one("#regtable", DataTable).cursor_row

    def test_marking_leaves_the_cursor_on_the_next_row(self, blob):
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                s = await self._list(app, pilot)
                s.action_toggle_sel()
                # Keep the pause AND wait for the row. The pause lets the app
                # drain; the wait is what makes a slow runner report the row it
                # reached instead of the moment it was asked. Dropping either
                # one breaks this differently -- and `_row` raises IndexError
                # while the screen is still coming up, so it cannot be the
                # condition until after the pause.
                await pilot.pause(0.1)
                await until(pilot, lambda: self._row(app) == 1)
                assert self._row(app) == 1, (
                    "the cursor went back to the top after a mark")
        _run(scenario)

    def test_holding_space_walks_the_list(self, blob):
        """Three marks in a row should mark three different regions."""
        async def scenario():
            app = AcidcatTUI(blob)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(0.3)
                await self._list(app, pilot)
                for n in range(3):
                    # A mark DISMISSES the list and the app reopens it one row
                    # down, so this loop is three dismiss/reopen cycles rather
                    # than three keystrokes on one screen. A flat pause races
                    # that cycle: the next toggle can land on a screen that has
                    # not been handed the new selection yet, and because the
                    # toggle is a symmetric difference the run then ends with
                    # ONE region marked rather than three. Waiting for the app
                    # to have recorded the mark is waiting for the cycle.
                    #
                    # Found on ubuntu 3.10 in CI, green on the other four and
                    # green here at pause(0.0), which is why the duration was
                    # never the thing to tune.
                    scr = [x for x in app.screen_stack
                           if isinstance(x, RegionsScreen)][-1]
                    scr.action_toggle_sel()
                    await pilot.pause(0.1)
                    # the cursor stops at the last row, so the third mark
                    # leaves it where it was instead of past the end
                    want_row = min(n + 1, 2)
                    await until(pilot, lambda n=n, r=want_row: (
                        len(app._region_sel) == n + 1
                        and self._row(app) == r))
                    # `until` returns a bool rather than raising, so the
                    # assertions are what make a timeout a failure instead of
                    # a silent pass -- the same mistake one layer up.
                    assert len(app._region_sel) == n + 1, (
                        "mark %d never reached the app: %s"
                        % (n, app._region_sel))
                    assert self._row(app) == want_row, (
                        "the reopened list sits on row %d, not %d"
                        % (self._row(app), want_row))
                assert app._region_sel == {0, 1, 2}, app._region_sel
        _run(scenario)

    def test_the_cursor_belongs_to_the_view(self, blob):
        assert "_region_cursor" in AcidcatTUI._FRAME_ATTRS


class TestTheWaiterItself:
    """`_expandable_child` cannot be proved against the flake it fixes.

    The flake only appears under full-suite load; on an idle machine the
    children are already present before the helper is called, so removing the
    wait entirely still passes. That makes the usual mutation check vacuous and
    is worth saying rather than papering over -- the fix rests on the mechanism
    being right, so the mechanism is what gets tested.
    """

    def test_it_waits_for_a_child_that_is_not_there_yet(self):
        """A node whose children arrive late must still be handled. This is the
        real condition; a flat pause only happens to cover it when the machine
        is fast enough."""
        class LateNode:
            label = "late"

            def __init__(self, after):
                self._after = after
                self.polls = 0

            @property
            def children(self):
                self.polls += 1
                if self.polls <= self._after:
                    return []
                return [_Expandable()]

        class _Expandable:
            allow_expand = True

        node = LateNode(after=3)

        async def scenario():
            pilot = _FakePilot()
            got = await _expandable_child(pilot, node)
            assert got.allow_expand
            assert node.polls > 3, (
                "the helper returned before the children existed, so it is not "
                "waiting on the condition at all")

        asyncio.run(scenario())

    def test_it_reports_the_condition_when_it_never_becomes_true(self):
        """The failure a flat pause could not produce: a message naming what
        never happened rather than a duration that turned out to be short."""
        class NeverNode:
            label = "never"
            children = []

        async def scenario():
            pilot = _FakePilot()
            with pytest.raises(AssertionError, match="expandable child"):
                await _expandable_child(pilot, NeverNode())

        asyncio.run(scenario())


class _FakePilot:
    """Enough pilot for the waiter: `until` only awaits `pause`."""

    async def pause(self, delay=None):
        await asyncio.sleep(0)
