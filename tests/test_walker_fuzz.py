"""Every walker we can seed, against bytes designed to break it.

A tool whose entire subject is malformed input should not be surprised by
malformed input. The contract at the `walk_file` boundary is already written
down in its own docstring -- degrade with warnings, never raise -- and the
enforcement is there too, so the only thing missing was ever asking.

There was a differential fuzzer before this, and it was good, and it covered
exactly one of 52 registered walkers. Not because anyone chose WAV: because
constructing a valid input was reinvented in 56 test files, 46 of them building
a `WAVE` header by hand, so widening the sweep meant writing another builder
every time. `seeds.py` is that ceiling removed -- the sweep below is the same
loop it always was, over as many formats as we can synthesize.

Deterministic and hermetic: seeded RNG, no external corpus, nothing downloaded.
A failure here reproduces on any machine with the seed printed in the message.
"""

import random

import pytest

from acidcat.core.infra import sniff as sniffmod
from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported

import seeds

FORMATS = sorted(seeds.SEEDS)


def _write(tmp_path, fmt, data, i=0):
    p = tmp_path / f"{fmt}_{i}{seeds.suffix(fmt)}"
    p.write_bytes(data)
    return str(p)


def _mutate(data, rng):
    """The four things that break a parser, in the proportions they occur.

    Bit flips find field-level assumptions; truncation finds reads past the end;
    scribbling a size or count field finds the arithmetic; appended junk finds
    the loop that trusts a declared end. Deliberately not random bytes from
    scratch -- that tests the reject path and never reaches the parser.
    """
    b = bytearray(data)
    kind = rng.randint(0, 3)
    if kind == 0 and b:
        b[rng.randrange(len(b))] ^= 1 << rng.randrange(8)
    elif kind == 1 and len(b) > 4:
        b = b[:rng.randrange(1, len(b))]
    elif kind == 2 and len(b) >= 8:
        b[rng.randrange(4, min(len(b), 64))] = rng.randrange(256)
    else:
        b += bytes(rng.randrange(256) for _ in range(rng.randrange(1, 32)))
    return bytes(b)


class TestTheSeedsAreWhatTheyClaim:
    """A sweep seeded with bytes that walk as something else tests that other
    thing, thoroughly and by accident, and reports the coverage of the format
    it meant to test."""

    @pytest.mark.parametrize("fmt", FORMATS)
    def test_the_sniffer_agrees(self, tmp_path, fmt):
        got = sniffmod.sniff(_write(tmp_path, fmt, seeds.build(fmt)))
        assert got == seeds.sniffs_as(fmt), (
            f"{fmt} seed sniffs as {got!r}, so the sweep below would be "
            f"fuzzing {got!r} under the name {fmt!r}")

    @pytest.mark.parametrize("fmt", FORMATS)
    def test_a_clean_seed_walks_without_complaint(self, tmp_path, fmt):
        """If the seed does not parse, every mutation of it is testing the
        error path and none of them reach the code we care about."""
        label, chunks, _warns = walk_file(_write(tmp_path, fmt, seeds.build(fmt)))
        assert chunks, f"{fmt} seed produced no chunks at all ({label})"


class TestMutationsDegradeRatherThanCrash:
    @pytest.mark.parametrize("fmt", FORMATS)
    def test_the_walker_never_raises_a_stray_exception(self, tmp_path, fmt):
        """The boundary's own contract, applied to every format we can seed.

        `Unsupported` is a legitimate answer -- a mutation can destroy the magic
        -- and so is a degraded walk with warnings. A struct.error or an
        IndexError is a parser meeting a shape it did not consider.
        """
        rng = random.Random(20260815)
        base = seeds.build(fmt)
        for i in range(400):
            m = _mutate(base, rng)
            path = _write(tmp_path, fmt, m, i % 8)
            try:
                walk_file(path)
            except Unsupported:
                pass
            except Exception as e:                      # noqa: BLE001
                pytest.fail(
                    f"{fmt}: {e.__class__.__name__}: {e}\n"
                    f"  iteration {i}, seed 20260815, {len(m):,} bytes\n"
                    f"  head: {m[:32].hex(' ')}")

    @pytest.mark.parametrize("fmt", FORMATS)
    def test_deep_walks_survive_the_same_bytes(self, tmp_path, fmt):
        """`deep=True` is a different amount of parsing, not the same parsing
        done twice: it decodes rows a shallow walk never looks at."""
        rng = random.Random(20260816)
        base = seeds.build(fmt)
        for i in range(150):
            m = _mutate(base, rng)
            path = _write(tmp_path, fmt, m, i % 8)
            try:
                walk_file(path, deep=True)
            except Unsupported:
                pass
            except Exception as e:                      # noqa: BLE001
                pytest.fail(
                    f"{fmt} (deep): {e.__class__.__name__}: {e}\n"
                    f"  iteration {i}, seed 20260816, {len(m):,} bytes\n"
                    f"  head: {m[:32].hex(' ')}")


class TestWhatWeCanFuzzIsStated:
    """The sweep covers what it can seed, which used to be one walker in 52.

    This class asked for years only that the gap be NAMED, on the grounds that
    an unstated number reads as complete. It said in as many words that if the
    gap ever closed it should say so differently. It has closed, so it does.
    """

    def test_every_registered_walker_is_reachable_from_a_seed(self, tmp_path):
        """The gap is zero, and this is what keeps it there.

        A walker with no seed is a walker no mutation ever reaches: it was
        registered, it was covered by whatever its own test module asserted,
        and it was absent from every contract test in this file. That is how
        the sweep spent its first life covering exactly one of 52 formats.

        Adding a walker now means adding a seed, and this is where forgetting
        is caught. It is deliberately stated as a set difference rather than a
        count, so the failure names the format instead of a number.
        """
        from acidcat.core.walk import _WALKERS
        registered = {label for label, _fn in _WALKERS.values()}
        reached = set()
        for fmt in FORMATS:
            try:
                reached.add(walk_file(_write(tmp_path, fmt, seeds.build(fmt)))[0])
            except Unsupported:
                continue
        assert registered <= reached, (
            "registered walkers no seed reaches, so nothing in this file "
            "fuzzes them: " + ", ".join(sorted(registered - reached)))

    def test_every_seed_is_reachable_through_the_public_boundary(self, tmp_path):
        """A seed only the sniffer likes is not a fuzzing target."""
        for fmt in FORMATS:
            try:
                walk_file(_write(tmp_path, fmt, seeds.build(fmt)))
            except Unsupported:
                pytest.fail(f"{fmt} seed cannot be walked at all")


class TestWalkBytesIsTheHarnessEntryPoint:
    """Fuzzing was a chore before it was a call.

    Every walker takes a path, so each harness wrote its own carve-and-delete,
    and each new target meant writing it again. That, not any judgement about
    formats, is why the differential fuzzer covered one of 52.
    """

    def test_it_agrees_with_walking_the_same_bytes_on_disk(self, tmp_path):
        from acidcat.core.walk import walk_bytes
        for fmt in FORMATS:
            data = seeds.build(fmt)
            a = walk_bytes(data, suffix=seeds.suffix(fmt))
            b = walk_file(_write(tmp_path, fmt, data))
            assert a[0] == b[0], fmt
            assert [c.get("id") for c in a[1]] == [c.get("id") for c in b[1]], fmt

    def test_it_leaves_no_temp_behind(self, tmp_path):
        from acidcat.core.walk import walk_bytes
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        for fmt in FORMATS:
            try:
                walk_bytes(seeds.build(fmt), suffix=seeds.suffix(fmt),
                           scratch_dir=str(scratch))
            except Unsupported:
                pass
        assert list(scratch.iterdir()) == [], list(scratch.iterdir())

    def test_a_walker_that_raises_still_cleans_up(self, tmp_path):
        """The delete has to survive the failure, or a fuzz run leaves one file
        per crash exactly when it is finding the most crashes."""
        from acidcat.core.walk import walk_bytes
        scratch = tmp_path / "s2"
        scratch.mkdir()
        try:
            walk_bytes(b"\x00" * 64, suffix=".bin", scratch_dir=str(scratch))
        except Unsupported:
            pass
        assert list(scratch.iterdir()) == []


def test_every_walker_survives_tiny_forced_input():
    """fmt_override's docstring promises a forced walker "degrades to warnings
    like any other walk". No seed required, so this reaches a walker through
    the forced path rather than through its magic -- which is a different entry
    point, not a wider one, now that every walker has a seed. The audit found four
    walkers that broke the promise on sub-header input: voc (reachable from
    the natural sniff: a file that is exactly the 20-byte magic), rf64 and
    aiff (12-byte header unpacked unguarded), and asd (domain AbletonError
    escaping). Conftest sets ACIDCAT_WALKER_RAISE=1, so any raise fails here.
    """
    from acidcat.core.walk import _WALKERS, walk_bytes
    tiny = (b"", b"\xaa" * 4, b"\x00" * 11, b"Creative Voice File\x1a",
            bytes(range(32)))
    for fmt in sorted(_WALKERS):
        for blob in tiny:
            try:
                walk_bytes(blob, fmt_override=fmt)
                walk_bytes(blob, deep=True, fmt_override=fmt)
            except Unsupported:
                pass
