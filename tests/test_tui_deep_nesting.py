"""The tree goes as deep as the file does.

A reverse-engineering tool meets containers nobody has written a walker for,
holding containers nobody has written a walker for. The tree has to be able to
follow that down as far as it actually goes, because there is no telling what
the next source looks like.

Two separate defects sit behind "it stops at three levels", and only one of them
is about depth:

  It stops. Nothing below a region's chunks could be opened, so the tree bottomed
  out at a fixed level rather than at the bottom of the file.

  It FLATTENS. `locate()` sweeps a whole byte range for content signatures, so on
  a blob holding a container holding two audio files it reports the two audio
  files at their absolute offsets and the container they lived in never appears
  at all. That level is not merely unopened, it is unrepresented -- and a
  recursive explorer that reached for `locate()` first would fix the depth while
  keeping the flattening.

The fixtures below are built so that every level is independently recognisable,
which is measured, not assumed: `triage.generic_walk` claims the outer grid, the
middle grid, and the innermost RIFF each on their own bytes.
"""

import asyncio
import struct

import pytest

pytest.importorskip("textual")

from acidcat.tui_app.app import AcidcatTUI      # noqa: E402
from conftest import measured, quiet            # noqa: E402

# ten seconds and up per test: run in the full suite, skipped in the edit loop
pytestmark = pytest.mark.slow


def _run(scenario):
    asyncio.run(scenario())


def _wav(nsamples, fill):
    pcm = bytes([fill]) * nsamples
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _grid(magic, parts):
    """A tiling chunk grid: TAG + le32 size + payload, repeated.

    The shape `triage.generic_walk` recognises without knowing the format --
    which is the whole point, since the containers this tool meets in the wild
    are ones nobody wrote a walker for.
    """
    body = b"".join(t + struct.pack("<I", len(p)) + p for t, p in parts)
    return magic + struct.pack("<I", len(body) + 4) + b"HDR0" + body


@pytest.fixture
def deep_blob(tmp_path):
    """Three levels of real structure, each recognisable on its own bytes:

        ARCH grid
          blob -> NEST grid
                    sub1 -> RIFF/WAVE  (fmt , data, and their fields)
                    sub2 -> RIFF/WAVE
                    note
          meta
          pad0
    """
    a, b = _wav(2000, 0x41), _wav(3000, 0x42)
    mid = _grid(b"NEST", [(b"sub1", a), (b"sub2", b), (b"note", b"inner level")])
    outer = _grid(b"ARCH", [(b"blob", mid), (b"meta", b"outer level"),
                            (b"pad0", b"\x00" * 64)])
    p = tmp_path / "deep.blob"
    p.write_bytes(outer)
    return str(p), outer, mid, a


def _walk(node):
    yield node
    for c in node.children:
        yield from _walk(c)


def _covers(app, node, want):
    """Does this node's CONTENT start at `want`?

    A node's own range is its extent, which begins at the chunk header, while
    the container inside it begins after that header. Asking about the payload
    is asking the question these tests actually mean.
    """
    info = app._info(node)
    if info is None:
        return False
    poff, _plen = info.payload_range()
    return poff == want


def _depth(node, d=0):
    return max([d] + [_depth(c, d + 1) for c in node.children])


async def _names_when(pilot, app, tree, ok, tries=90, step=0.1):
    """Node names, once `ok` accepts them. Waiting lives in conftest.measured;
    this only says what is being measured."""
    return await measured(pilot,
                          lambda: [app._node_name(n) for n in _walk(tree.root)],
                          ok, tries, step)


async def _open_everything(app, pilot, rounds=8):
    """Expand every arrow the tree offers, until it stops offering new ones."""
    tree = app.query_one("#tree")
    tree.root.expand()
    await pilot.pause(0.2)
    for _ in range(90):                      # the root scan, if there is one
        if app._regions is not None and not app._scanning:
            break
        await pilot.pause(0.1)
    while len(app.screen_stack) > 1:
        app.screen_stack[-1].dismiss(None)
        await pilot.pause()
    for _ in range(rounds):
        opened = 0
        for n in list(_walk(tree.root)):
            if n.allow_expand and not n.is_expanded:
                n.expand()
                opened += 1
        # Expanding a node starts work on a worker, so a round that opened
        # nothing is not the same as a tree that has finished. Waiting a fixed
        # half second guessed at that; waiting for the workers themselves does
        # not have to guess.
        await quiet(pilot, app)
        if not opened:
            break
    return tree


class TestItReachesTheBottom:
    def test_the_innermost_container_is_reachable(self, deep_blob):
        """Two whole levels of container sit between the file and this WAV."""
        path, outer, _mid, a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                want = outer.index(a)
                hit = [n for n in _walk(tree.root)
                       if _covers(app, n, want)]
                assert hit, (
                    f"no node covers the inner WAV at 0x{want:08x}; deepest "
                    f"level reached was {_depth(tree.root)}")
        _run(scenario)

    def test_its_fields_are_reachable(self, deep_blob):
        """Reaching the container is not the job. Reading it is."""
        path, _outer, _mid, _a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                names = await _names_when(
                    pilot, app, tree,
                    lambda ns: any("sample_rate" in n or "44100" in n for n in ns))
                assert any("sample_rate" in n or "44100" in n for n in names), (
                    "the innermost WAV's fields were never rendered")
        _run(scenario)

    def test_the_tree_is_deeper_than_the_old_ceiling(self, deep_blob):
        """Four edges: file > blob > sub1 > fmt > sample_rate.

        Not five. `NEST` is triage's header pseudo-chunk, a SIBLING of sub1
        rather than a level above it, so the grid inside `blob` costs one level
        and not two. Counting it twice was my arithmetic, and a test that
        asserts a number nobody has checked against the tree is how a wrong
        number becomes a requirement.

        The ceiling this replaces was three, and it was a property of the code
        rather than of any file: file > region > chunk > field, and no further
        for anything.
        """
        path, _outer, _mid, _a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                got = _depth(tree.root)
                assert got >= 4, (
                    f"reached {got} levels; the old ceiling was 3 and this "
                    f"file goes further")
                # depth FROM the root, not the depth of the subtree below --
                # by the latter the root always wins, which is how this first
                # asked whether the filename contained an equals sign.
                def _from_root(n):
                    d = 0
                    while n.parent is not None:
                        d += 1
                        n = n.parent
                    return d
                deepest = max(_walk(tree.root), key=_from_root)
                assert "=" in app._node_name(deepest), (
                    "the deepest thing in the tree should be a decoded field, "
                    f"not {app._node_name(deepest)!r}")
        _run(scenario)


class TestItDoesNotFlatten:
    def test_the_middle_container_is_represented(self, deep_blob):
        """The defect that is not about depth: a signature sweep finds the
        innermost recognisable things and throws away what they sat inside.
        The middle grid must exist as a level, not be skipped over."""
        path, outer, mid, _a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                want = outer.index(mid)
                hit = [n for n in _walk(tree.root)
                       if _covers(app, n, want)]
                assert hit, (
                    f"nothing represents the middle container at 0x{want:08x} "
                    f"-- its contents were lifted out and it vanished")
        _run(scenario)

    def test_the_inner_wav_is_a_descendant_of_the_middle_container(self, deep_blob):
        """Being present is not enough: it has to be present in the right
        place. A flat list that happens to contain both is what we are
        replacing."""
        path, outer, mid, a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                mid_off, wav_off = outer.index(mid), outer.index(a)
                mids = [n for n in _walk(tree.root)
                        if _covers(app, n, mid_off)]
                assert mids, "no middle container to be a descendant of"
                under = [n for n in _walk(mids[0])
                         if _covers(app, n, wav_off)]
                assert under, "the inner WAV is not under the container it is in"
        _run(scenario)


class TestTheMiddleLevelKeepsItsOwnStructure:
    """Reaching the bottom is not proof the middle survived.

    Recursing into a chunk's EXTENT instead of its payload still reaches the
    innermost WAVs and their fields -- the extent begins with the chunk header,
    no rung can anchor on it, and `locate` sweeps the range and finds the audio
    anyway. So the WAVs are present, and still descendants of the right node,
    and every earlier test in this file passes.

    What is gone is the layer in between: `sub1`, `sub2` and `note` are
    replaced by two flat `wav` regions, and the grid that held them is never
    named. That is the flattening, one level further in, and it needs asking
    about directly because its symptoms are invisible from the leaves.
    """

    def _names(self, app, tree):
        return [app._node_name(n) for n in _walk(tree.root)]

    def test_the_middle_grids_own_chunks_are_named(self, deep_blob):
        path, _outer, _mid, _a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                names = self._names(app, tree)
                for want in ("sub1", "sub2", "note"):
                    assert any(want in n for n in names), (
                        f"{want!r} is missing: the middle grid was swept "
                        f"instead of walked")
        _run(scenario)

    def test_the_inner_file_was_reached_through_structure_not_a_sweep(self, deep_blob):
        """A chunk means a walker named it. A located region means we found it
        by looking for audio, which is the fallback and says nothing about
        what contained it."""
        path, outer, _mid, a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                hits = [n for n in _walk(tree.root) if _covers(app, n, outer.index(a))]
                assert hits, "the inner WAV is not in the tree at all"
                kinds = {app._info(n).kind for n in hits}
                assert "chunk" in kinds, (
                    f"the inner WAV was only ever found by sweeping: {kinds}")
        _run(scenario)

    def test_no_level_is_explained_by_a_sweep_when_a_walker_could(self, deep_blob):
        """Every level of this fixture is independently walkable, so a located
        region anywhere in the tree means a rung was skipped."""
        path, _outer, _mid, _a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                swept = [app._node_name(n) for n in _walk(tree.root)
                         if app._info(n) is not None
                         and app._info(n).kind == "region"]
                assert not swept, (
                    f"these were found by sweeping in a file where every level "
                    f"walks: {swept}")
        _run(scenario)


class TestItStopsWhenThereIsNothingThere:
    def test_a_node_with_nothing_inside_says_so(self, deep_blob):
        """An arrow that opens onto silence is worse than no arrow. Expanding
        something unrecognisable has to produce a statement, not an empty
        branch."""
        path, _outer, _mid, _a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                empty = [n for n in _walk(tree.root)
                         if n.is_expanded and not n.children]
                assert not empty, (
                    "expanded nodes with no children at all: "
                    f"{[app._node_name(n) for n in empty][:5]}")
        _run(scenario)

    def test_it_terminates(self, deep_blob):
        """A chunk whose byte range equals its parent's would re-walk itself
        forever. Opening everything repeatedly has to reach a fixed point."""
        path, _outer, _mid, _a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot, rounds=12)
                before = len(list(_walk(tree.root)))
                for n in list(_walk(tree.root)):
                    if n.allow_expand and not n.is_expanded:
                        n.expand()
                await pilot.pause(0.6)
                after = len(list(_walk(tree.root)))
                assert after == before, (
                    f"the tree was still growing after it settled: "
                    f"{before} -> {after} nodes")
        _run(scenario)


class TestBigRangesAreTheUsersCall:
    """Opening a node reads its bytes. On a 187 MB archive a tree that read
    eagerly would grind through the file one keypress at a time, so past a size
    the read is offered rather than performed.

    The offer is the arrow on the line, not a key to be discovered: the node
    says what it would cost, and expanding it is consent. A refusal that named
    no number would be indistinguishable from an empty branch.
    """

    @pytest.fixture
    def big(self, tmp_path):
        n = 6 * 1024 * 1024
        body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
                + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
                + b"data" + struct.pack("<I", n) + b"A" * n)
        wav = b"RIFF" + struct.pack("<I", len(body)) + body
        grid = b"".join(t + struct.pack("<I", len(x)) + x
                        for t, x in ((b"big0", wav), (b"tiny", b"x" * 32)))
        p = tmp_path / "gate.blob"
        p.write_bytes(b"BIGC" + struct.pack("<I", len(grid) + 4) + b"HDR0" + grid)
        return str(p)

    async def _first_child(self, app, pilot):
        tree = app.query_one("#tree")
        tree.root.expand()
        await pilot.pause(0.3)
        for _ in range(90):
            if app._regions is not None and not app._scanning:
                break
            await pilot.pause(0.1)
        while len(app.screen_stack) > 1:
            app.screen_stack[-1].dismiss(None)
            await pilot.pause()
        kids = [c for c in tree.root.children if app._meta(c) is not None]
        assert kids, "nothing under the file to open"
        return kids[0]

    def test_a_big_payload_is_offered_rather_than_read(self, big):
        async def scenario():
            app = AcidcatTUI(big)
            async with app.run_test(size=(170, 50)) as pilot:
                await pilot.pause(0.3)
                node = await self._first_child(app, pilot)
                app._explore_node(node)
                names = [app._node_name(c) for c in node.children]
                assert len(names) == 1, names
                assert "look inside" in names[0], names
                assert "MB" in names[0], "the offer did not say what it costs"
        _run(scenario)

    def test_accepting_the_offer_reads_it(self, big):
        async def scenario():
            app = AcidcatTUI(big)
            async with app.run_test(size=(170, 50)) as pilot:
                await pilot.pause(0.3)
                node = await self._first_child(app, pilot)
                app._explore_node(node)
                ask = node.children[0]
                assert ask.allow_expand, "the offer cannot be accepted"
                app._explore_node(ask)
                # The read happens on a worker, so the answer arrives later.
                # Waiting for it is the point: a test that inspected straight
                # away would be asserting that the UI had NOT been blocked.
                for _ in range(200):
                    ids = [app._node_name(c) for c in ask.children]
                    if any("fmt" in x for x in ids):
                        break
                    await pilot.pause(0.05)
                ids = [app._node_name(c) for c in ask.children]
                assert any("fmt" in x for x in ids), ids
                assert any("data" in x for x in ids), ids
                assert not any("looking inside" in x for x in ids), (
                    "the placeholder outlived the answer")
        _run(scenario)

    def test_a_small_payload_is_just_read(self, deep_blob):
        """Asking about every branch would be ceremony where the read is
        imperceptible."""
        path, _outer, _mid, _a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                # "MB to read" and not "look inside": the latter is also a
                # substring of "nothing to look inside", so the first version of
                # this matched a refusal and called it an offer.
                offers = [n for n in _walk(tree.root)
                          if "MB to read" in app._node_name(n)]
                assert not offers, (
                    f"asked permission for a small read: "
                    f"{[app._node_name(n) for n in offers]}")
        _run(scenario)


class TestTheUiDoesNotBlockWhileItReads:
    """A walker resyncing through a few megabytes takes as long as it takes.
    Doing that inside the expand handler freezes the app with no way to quit,
    which reads as a hang rather than as work.
    """

    def test_expanding_returns_before_the_answer_does(self, deep_blob):
        path, _outer, _mid, _a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                await pilot.pause(0.3)
                tree = app.query_one("#tree")
                tree.root.expand()
                await pilot.pause(0.3)
                # An arrow is not the same as somewhere to go -- the header
                # pseudo-chunk has fields and eight bytes of payload, so it has
                # an arrow and nothing to walk. Ask for one that can actually
                # be explored, which is what this test is about.
                node = [c for c in tree.root.children
                        if app._info(c) and app._info(c).kind == "chunk"
                        and app._info(c).can_explore][0]
                app._explore_node(node)
                # Synchronously after the call: something is on screen, and it
                # is not the answer yet.
                names = [app._node_name(c) for c in node.children]
                assert names, "expanding showed nothing at all"
                assert any("looking inside" in n for n in names), names
        _run(scenario)

    def test_the_replay_path_stays_synchronous(self, deep_blob):
        """A rebuild reopens what was open by walking each path and building
        the lazy levels as it goes. It cannot wait for a worker -- Textual posts
        expansion rather than calling it, so there is no point in that walk
        where the next level could be awaited."""
        path, _outer, _mid, _a = deep_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                await pilot.pause(0.3)
                tree = app.query_one("#tree")
                tree.root.expand()
                await pilot.pause(0.3)
                node = [c for c in tree.root.children
                        if app._info(c) and app._info(c).kind == "chunk"
                        and app._info(c).can_explore][0]
                app._explore_node(node, background=False)
                names = [app._node_name(c) for c in node.children]
                assert names and not any("looking inside" in n for n in names), (
                    f"the replay path deferred its answer: {names}")
        _run(scenario)


class TestAChunkWithFieldsCanStillBeOpened:
    """"Has children" is not "has been looked inside", and confusing the two
    made a whole class of chunk permanently unopenable.

    The top level gets its fields when the tree is built, so a guard on children
    returned immediately for every chunk that had any -- a WAV `data` chunk
    holding a nested container could never be explored, and nothing in the tree
    hinted that a question had been skipped. It answered by not asking.

    The fix has its own hazard, which is why both live here: once the guard
    stops being "has children", the ROOT qualifies too, and its contents are
    exactly what the tree was just built from. Exploring it again walks the
    whole file a second time and hangs a duplicate of every top-level chunk
    under the first.
    """

    @pytest.fixture
    def nested_in_data(self, tmp_path):
        pcm = b"B" * 1000
        inner = b"RIFF" + struct.pack("<I", 4 + 24 + 8 + len(pcm)) + (
            b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 2, 22050, 88200, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
        body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
                + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
                + b"data" + struct.pack("<I", len(inner)) + inner)
        p = tmp_path / "nested_in_data.wav"
        p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
        return str(p)

    def test_a_container_inside_a_data_chunk_is_found(self, nested_in_data):
        async def scenario():
            app = AcidcatTUI(nested_in_data)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                names = await _names_when(
                    pilot, app, tree, lambda ns: any("22050" in n for n in ns))
                assert any("22050" in n for n in names), (
                    "the WAV inside the data chunk was never opened: " +
                    str(names))
        _run(scenario)

    def test_the_chunks_own_fields_survive_being_explored(self, nested_in_data):
        """Exploring adds to what the walker said; it does not replace it."""
        async def scenario():
            app = AcidcatTUI(nested_in_data)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                names = await _names_when(
                    pilot, app, tree, lambda ns: any("frames" in n for n in ns))
                assert any("frames" in n for n in names), names
        _run(scenario)

    def test_nothing_is_listed_twice(self, nested_in_data):
        """The root's children are what the tree was built from. Walking the
        file again to 'explore' it duplicates every one of them."""
        async def scenario():
            app = AcidcatTUI(nested_in_data)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot)
                top = [app._node_name(c) for c in tree.root.children]
                assert len(top) == len(set(top)), (
                    f"the top level was built twice: {top}")
        _run(scenario)

    def test_the_root_is_not_re_walked(self, nested_in_data):
        async def scenario():
            app = AcidcatTUI(nested_in_data)
            async with app.run_test(size=(170, 50)) as pilot:
                await pilot.pause(0.4)
                root = app.query_one("#tree").root
                assert app._info(root).explored is True, (
                    "the root does not know its children are already its "
                    "contents")
        _run(scenario)


class TestAChunkThatCoversItsParentDoesNotRecurse:
    """The loop a real archive produced, and the reason it slipped through.

    An Ogg region walks to a single `OggS` chunk covering exactly the bytes the
    region does. Its payload is therefore the region again, so exploring it
    re-walks the same range and produces the same chunk, forever: on screen,
    `OggS 0x03ea251d 2,419,671b` inside an identical `OggS 0x03ea251d
    2,419,671b`, as deep as anyone cared to open.

    The guard against exactly this existed and was correct. It was only ever
    consulted to decide whether to draw an ARROW, and that decisionshort-circuited
    on `bool(chunk["fields"])` -- an OggS chunk has six. So the arrow appeared
    because there were fields to show, and expanding then walked the bytes with
    nothing checking whether it should.

    An arrow means "there is something under this". It does not mean "these
    bytes may be walked". Two questions, two answers.
    """

    @pytest.fixture
    def ogg_blob(self, tmp_path):
        import glob
        found = glob.glob("data/**/*.ogg", recursive=True)
        if not found:
            pytest.skip("no .ogg in the test corpus")
        raw = open(found[0], "rb").read()
        p = tmp_path / "one.blob"
        p.write_bytes(b"HDR!" + bytes(4096) + raw)
        return str(p), 4100, len(raw)

    def test_the_tree_settles_instead_of_growing(self, ogg_blob):
        path, _off, _n = ogg_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot, rounds=10)
                first = len(list(_walk(tree.root)))
                for _ in range(4):
                    for n in list(_walk(tree.root)):
                        if n.allow_expand and not n.is_expanded:
                            n.expand()
                    await quiet(pilot, app)
                after = len(list(_walk(tree.root)))
                assert after == first, (
                    "the tree was still growing after it should have settled")
        _run(scenario)

    def test_no_chunk_contains_a_chunk_over_the_same_bytes(self, ogg_blob):
        """The shape as the user sees it: identical offset, identical size, one
        inside the other, repeating.

        Stated between chunks rather than between any two nodes, because a
        REGION and the single chunk a walker finds in it legitimately cover the
        same bytes -- the region IS the Ogg. What cannot happen is that chunk
        containing another chunk over the same range, because the only way to
        produce one is to walk the same bytes again.
        """
        path, _off, _n = ogg_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot, rounds=10)

                def check(node, chunk_ancestors):
                    info = app._info(node)
                    here = list(chunk_ancestors)
                    if info is not None and info.kind == "chunk"                             and info.off is not None:
                        mine = (info.off, info.length)
                        assert mine not in here, (
                            f"{app._node_name(node).strip()[:44]!r} sits inside "
                            f"a chunk over the same bytes "
                            f"(0x{info.off:08x}+{info.length:,})")
                        here.append(mine)
                    for c in node.children:
                        check(c, here)

                check(tree.root, [])
        _run(scenario)

    def test_a_chunk_covering_its_parent_still_shows_its_fields(self, ogg_blob):
        """The fix must not cost the fields. They are the reason the arrow was
        right in the first place."""
        path, _off, _n = ogg_blob

        async def scenario():
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot, rounds=10)
                names = await _names_when(
                    pilot, app, tree,
                    lambda ns: any("codec" in x for x in ns)
                    and any("bitstream_serial" in x for x in ns))
                assert any("codec" in x for x in names), names[:12]
                assert any("bitstream_serial" in x for x in names), names[:12]
        _run(scenario)


class TestTheTopLevelHasTheSameOpinion:
    """The verdict is computed by the lazy builder, and `_load` builds the top
    level. So the first fix covered a chunk found INSIDE a region and left the
    identical shape untouched one level up.

    An Ogg opened directly walks to a single OggS chunk covering the whole file.
    With no opinion recorded, the top level defaulted to yes, and expanding that
    chunk walked the file again and hung a copy of itself underneath. It stopped
    after one level only because the copy was built by the lazy path, which does
    compute the verdict -- so the bug was one duplicated level rather than an
    endless one, which is a worse way to be wrong: quiet enough to keep.
    """

    def _paths(self):
        import glob
        out = []
        for pat in ("*.ogg", "*.flac"):
            hits = glob.glob(f"data/**/{pat}", recursive=True)
            if hits:
                out.append(hits[0])
        if not out:
            pytest.skip("no ogg or flac in the test corpus")
        return out

    def test_no_top_level_chunk_contains_a_copy_of_itself(self):
        async def scenario(path):
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot, rounds=8)
                seen = {}
                for n in _walk(tree.root):
                    info = app._info(n)
                    if info is None or info.kind != "chunk" or info.off is None:
                        continue
                    key = (info.off, info.length)
                    seen[key] = seen.get(key, 0) + 1
                dupes = {k: v for k, v in seen.items() if v > 1}
                assert not dupes, (
                    f"{path}: chunk ranges built more than once: {dupes}")
        for p in self._paths():
            _run(lambda p=p: scenario(p))

    def test_a_file_that_is_one_chunk_still_shows_its_fields(self):
        """The whole reason the arrow was right: there IS something under it."""
        async def scenario(path):
            app = AcidcatTUI(path)
            async with app.run_test(size=(170, 50)) as pilot:
                tree = await _open_everything(app, pilot, rounds=8)
                names = [app._node_name(n) for n in _walk(tree.root)]
                assert any("=" in n for n in names), names[:10]
        for p in self._paths():
            _run(lambda p=p: scenario(p))
