"""Looking inside a byte range, at any depth, with the same question each time.

The engines are ALTERNATIVES, not contributors: the first rung that explains a
range wins it, and the others get their turn on the children one level down.
That ordering is not a preference, it is what keeps the hierarchy. `locate`
sweeps a whole range for audio signatures, so running it on a range a walker
already explained reports the innermost audio at its absolute offset and skips
every layer in between -- measured, on a blob holding a container holding two
Ogg files: two Oggs reported, the container never mentioned.

The recursion descends into the PAYLOAD, not the extent, and the difference is
load-bearing rather than pedantic. A chunk's extent begins with its own header,
so exploring the extent puts four bytes of tag where the next level's magic
should be and every rung fails to anchor. Measured on the fixture below: the
middle container is recognised through its payload and falls through to a bare
`locate` through its extent.
"""

import os
import struct

import pytest

from acidcat.core.forensics import explore
from acidcat.core.infra import geometry


def _wav(nsamples, fill):
    pcm = bytes([fill]) * nsamples
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _grid(magic, parts):
    body = b"".join(t + struct.pack("<I", len(p)) + p for t, p in parts)
    return magic + struct.pack("<I", len(body) + 4) + b"HDR0" + body


@pytest.fixture
def deep(tmp_path):
    """ARCH grid -> blob -> NEST grid -> sub1 -> RIFF/WAVE. Every level is
    independently recognisable, which is measured rather than assumed."""
    a, b = _wav(2000, 0x41), _wav(3000, 0x42)
    mid = _grid(b"NEST", [(b"sub1", a), (b"sub2", b), (b"note", b"inner")])
    outer = _grid(b"ARCH", [(b"blob", mid), (b"meta", b"outer"),
                            (b"pad0", b"\x00" * 64)])
    p = tmp_path / "deep.blob"
    p.write_bytes(outer)
    return str(p), outer, mid, a


def _chunk(res, cid):
    for c in res["chunks"]:
        if str(c.get("id")).strip() == cid:
            return c
    raise AssertionError(f"{cid!r} not among {[c.get('id') for c in res['chunks']]}")


class TestItGoesAllTheWayDown:
    def test_each_level_is_explained_by_its_own_walker(self, deep):
        path, outer, mid, a = deep
        top = explore.explore(path, 0, len(outer))
        assert top["engine"] == "walker"

        blob = _chunk(top, "blob")
        mid_res = explore.explore(path, *geometry.payload_of(blob))
        assert mid_res["engine"] == "walker", "the middle container was flattened"

        sub1 = _chunk(mid_res, "sub1")
        wav = explore.explore(path, *geometry.payload_of(sub1))
        assert wav["engine"] == "walker"
        assert wav["label"] == "RIFF/WAVE", (
            f"the innermost file lost its real walker: {wav['label']!r}")

    def test_the_innermost_fields_are_real(self, deep):
        """Reaching the bottom is not the job; reading it is. A generic chunk
        grid would give ids and sizes and nothing else."""
        path, outer, _mid, _a = deep
        top = explore.explore(path, 0, len(outer))
        mid = explore.explore(path, *geometry.payload_of(_chunk(top, "blob")))
        wav = explore.explore(path, *geometry.payload_of(_chunk(mid, "sub1")))
        fmt = _chunk(wav, "fmt")
        names = {f["name"]: f["value"] for f in fmt.get("fields") or []}
        assert names.get("sample_rate") == 44100, names

    def test_offsets_are_absolute_in_the_original_file(self, deep):
        """A child that reported its walker's view of a carved slice would
        point the hex pane at the wrong bytes -- silently, since both are
        valid offsets into something."""
        path, outer, mid, a = deep
        top = explore.explore(path, 0, len(outer))
        blob = _chunk(top, "blob")
        mid_res = explore.explore(path, *geometry.payload_of(blob))
        sub1 = _chunk(mid_res, "sub1")
        off, _len = geometry.payload_of(sub1)
        assert off == outer.index(a), (
            f"sub1's payload is at 0x{off:x}, the WAV is at "
            f"0x{outer.index(a):x}")
        assert open(path, "rb").read()[off:off + 4] == b"RIFF"


class TestThePayloadIsTheWalkTarget:
    def test_the_extent_does_not_re_enter(self, deep):
        """The extent starts with the chunk's own header, so the next level's
        magic is not at offset 0 and nothing can anchor on it. This is the
        measurement that makes two ranges worth carrying."""
        path, outer, _mid, _a = deep
        top = explore.explore(path, 0, len(outer))
        blob = _chunk(top, "blob")
        by_payload = explore.explore(path, *geometry.payload_of(blob))
        by_extent = explore.explore(path, *geometry.extent_of(blob))
        assert by_payload["engine"] == "walker"
        assert by_extent["engine"] != "walker" or by_extent["label"] != by_payload["label"]


class TestItRefusesToLoop:
    def test_a_child_the_size_of_its_parent_is_not_explorable(self):
        """A walk that hands back its own input would re-walk it forever."""
        assert explore.explorable((0, 1000), (0, 1000), 0) is False

    def test_a_child_that_overflows_its_parent_is_not_explorable(self):
        assert explore.explorable((900, 500), (0, 1000), 0) is False
        assert explore.overflows((900, 500), (0, 1000)) is True

    def test_a_proper_subrange_is(self):
        assert explore.explorable((100, 500), (0, 1000), 0) is True
        assert explore.overflows((100, 500), (0, 1000)) is False

    def test_something_too_small_to_hold_a_container_is_not_offered(self):
        assert explore.explorable((0, 4), (0, 1000), 0) is False

    def test_depth_has_a_backstop(self):
        assert explore.explorable((0, 500), (0, 1000), explore._MAX_DEPTH) is False
        assert explore.explorable((0, 500), (0, 1000), explore._MAX_DEPTH - 1) is True


class TestItAnswersHonestlyWhenThereIsNothing:
    def test_unrecognisable_bytes_produce_no_engine_and_no_children(self, tmp_path):
        # Random, not a repeating ramp. A ramp is periodic, and periodic is
        # precisely what a frame-sync detector is looking for -- the first
        # version of this fixture had `locate` confidently finding a stream in
        # it, which says more about the fixture than about the engine.
        import random
        rng = random.Random(20260815)
        p = tmp_path / "noise.bin"
        p.write_bytes(bytes(rng.randrange(256) for _ in range(4096)))
        res = explore.explore(str(p), 0, 4096)
        assert res["engine"] is None
        assert not res["chunks"] and not res["regions"]

    def test_a_range_past_the_end_says_what_it_actually_read(self, tmp_path):
        """Not "nothing here": the range claimed more bytes than exist, and
        that is a fact about the file worth surfacing."""
        p = tmp_path / "short.bin"
        p.write_bytes(b"\x00" * 100)
        res = explore.explore(str(p), 0, 5000)
        assert res["partial"] is True
        assert "100" in res["note"] and "5,000" in res["note"]

    def test_an_empty_range_is_not_an_error(self):
        assert explore.explore("/nonexistent", 0, 0)["engine"] is None

    def test_an_unreadable_source_says_so_rather_than_raising(self):
        res = explore.explore(os.path.join("no", "such", "file"), 0, 10)
        assert res["engine"] is None
        assert res["warnings"], "a failed read reported nothing at all"


class TestItLeavesNothingBehind:
    def test_the_carved_slice_is_deleted(self, deep, tmp_path):
        """One temp per explore, deleted after the walk. A deep exploration of
        a large image would otherwise leave a carved copy of every level it
        touched on disk until the app closed."""
        path, outer, _mid, _a = deep
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        for _ in range(3):
            explore.explore(path, 0, len(outer), scratch_dir=str(scratch))
        assert os.listdir(scratch) == [], os.listdir(scratch)


def test_the_verb_builds_from_the_positioned_record(tmp_path, monkeypatch):
    """2.0: `explore` reads inspect's positioned (`full`) record in process.
    It used to run `acidcat inspect --full`, which is an alias for --json in
    2.0 and would hand the page the wrong record."""
    import seeds
    from acidcat import explorer
    from acidcat.cli import main
    p = tmp_path / "a.wav"
    p.write_bytes(seeds.build("wav"))
    seen = []
    real = explorer.build
    monkeypatch.setattr(explorer, "build", lambda r: seen.append(r) or real(r))
    assert main(["explore", str(p), "-o", str(tmp_path / "a.html")]) == 0
    assert seen and seen[0]["full"] is True
