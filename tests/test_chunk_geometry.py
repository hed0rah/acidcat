"""Every chunk has to say which bytes it occupies, in a way one rule can read.

There was no such rule. `_field_abs` documented the closest thing to one --
field offsets are relative to `payload_base`, else `offset + 8`
(core/infra/fieldcodec.py:39) -- and nine sites across six modules re-derived it
independently, spelled four different ways, two of them computing an end as
`base + size + 8` and one never consulting `payload_base` at all. Six consumers
guessing at the same geometry is not a convention, it is a coincidence, and the
coincidence had already broken.

It broke because one key was answering two questions. RIFF's `size` is the
PAYLOAD length; MP4's is the TOTAL box length, header included. Both are
reasonable and no single reader serves both, so a normalized chunk now carries
`extent` (every byte it occupies) and `payload` (the bytes inside), and says
whether anyone actually declared them. core/infra/geometry.py is the one reader.

Measured over 47 files and 275 chunks, before and after:

                     before   after
    declared            196     200
    defaulted            72      72
    bytes past EOF       28       3

The three that remain are the point of the distinction. A declared extent
running past the end of a deliberately truncated fixture is a finding about the
FILE, and all three are announced in the file's own numbers -- "claims 176,400
bytes but only 79,922 remain". Under a single `size` key that case and MP4's
systematic 8-byte overshoot looked identical from outside, which is how the
second one survived as long as it did.

A ratchet, not a gate: the known-defect set may shrink and must never grow. Its
own reach is asserted rather than implied, because a test that quietly stops
examining anything passes forever.

Those 47 files are `data/` on a development machine, where `data/test_formats/`
holds 48 specimens that are gitignored. A clone has 7 walkable files and 56
chunks, so the floor below is the CLONE's corpus, not the measurement above.
The first version of this file asserted the development numbers and passed on
the machine it was written on while failing on all five CI platforms -- the
ratchet reporting a local reading as a fact about the repo, which is the exact
defect class the rest of this file exists to catch. The floor rises only when
specimens are COMMITTED.
"""

import glob
import io
import os

import pytest

from acidcat.core.infra import geometry
from acidcat.core.walk import walk_file

_MAX = 8_000_000

# Anchored to the repo, not to os.getcwd(). A relative glob made the whole
# module a no-op when pytest ran from anywhere but the root: no matches, the
# fixture skips, and four assertions report success having examined nothing.
_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data")


# (walker label, chunk id) -> why this one is known-wrong. Shrink only.
#
# Empty, and it got here by shrinking rather than by starting that way. MP4 now
# states extent and payload separately, because an ISO box size counts its own
# header and the two differ by exactly that much. FLAC's `frames` chunk declares
# its own base rather than inheriting an eight-byte header it does not have. And
# a truncated FLAC reports the damage in the file's own numbers, the way RIFF
# always did -- the three chunks that still fail the arithmetic are damaged
# files being described correctly, which is the tool working.
KNOWN_DEFECTS = {}


def _corpus():
    """Real specimens from `data/`, a built one for every seeded format, and,
    when `ACIDCAT_HUNT_CORPUS` is set, a bounded slice of the real local corpus.

    The specimens are the better evidence and the seeds are the wider net, and
    the split matters because of where each exists. `data/test_formats/` is
    gitignored, so a development machine walks around thirty-six formats here
    and a CLONE walks five -- and the clone is where CI gates. An invariant that
    covers thirty-six locally and five on the runner is telling you about the
    machine that wrote it.

    `tests/seeds.py` is committed code, so a seeded format is examined
    everywhere. A seed is minimal and cannot show what a real file's oddities
    would, but for a geometry rule -- do these chunks describe bytes they own --
    a minimal valid file exercises the arithmetic exactly as well.
    """
    for path in sorted(glob.glob(os.path.join(_DATA, "**", "*.*"),
                                 recursive=True)):
        try:
            if os.path.getsize(path) > _MAX:
                continue
        except OSError:
            continue
        try:
            label, chunks, warns = walk_file(path, deep=False)
        except Exception:
            continue
        yield path, label, chunks, (warns or [])

    import tempfile

    import seeds as _seeds

    tmp = tempfile.mkdtemp(prefix="acidcat-geom-seeds-")
    for name, (build, ext, _sniffs) in sorted(_seeds.SEEDS.items()):
        path = os.path.join(tmp, name + ext)
        try:
            with io.open(path, "wb") as fh:
                fh.write(build())
            label, chunks, warns = walk_file(path, deep=False)
        except Exception:
            continue
        yield path, label, chunks, (warns or [])

    yield from _hunt()


# The real corpus, opt in. Seeds make the invariants gate in a clone; they
# cannot show what a real file's oddities would. `ACIDCAT_HUNT_CORPUS` names
# one or more roots (os.pathsep-separated) of real specimens: copyrighted,
# local, never committed. Every file under them is sniffed, and the first N
# per format walked. The floor in the ratchet is NOT raised by this:
# it stays what a clone reaches, so a run on a machine with the corpus is
# stronger evidence, never a stricter assertion.
_HUNT_PER_FORMAT = 40      # files walked per format id; ACIDCAT_HUNT_PER_FORMAT overrides
_HUNT_SCAN_CAP = 400_000   # files sniffed before the scan stops, so a huge tree stays bounded
_HUNT_SECONDS = 600

# (path, exception) for every real file that made a walker RAISE. With
# ACIDCAT_WALKER_RAISE=1 (conftest sets it) a walker bug surfaces here rather
# than being demoted to a warning. A corpus sweep that quietly skipped a
# crashing walker would be the green-run-that-checked-nothing defect again.
HUNT_RAISED = []
# how many real files the sweep actually walked. Without this, a corpus
# variable pointing somewhere empty produces an empty HUNT_RAISED and
# reads exactly like a clean run over fifty thousand files.
HUNT_WALKED = []


def _hunt():
    roots = os.environ.get("ACIDCAT_HUNT_CORPUS")
    if not roots:
        return
    import time

    from acidcat.core.infra import sniff as _sniff

    per = int(os.environ.get("ACIDCAT_HUNT_PER_FORMAT") or _HUNT_PER_FORMAT)
    taken, scanned, t0 = {}, 0, time.time()
    for root in roots.split(os.pathsep):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()                            # deterministic order
            for name in sorted(filenames):
                scanned += 1
                if scanned > _HUNT_SCAN_CAP or time.time() - t0 > _HUNT_SECONDS:
                    return
                path = os.path.join(dirpath, name)
                try:
                    if not 12 <= os.path.getsize(path) <= _MAX:
                        continue
                    fid = _sniff.sniff(path)
                except Exception:
                    continue
                if not fid or taken.get(fid, 0) >= per:
                    continue
                try:
                    label, chunks, warns = walk_file(path, deep=False)
                except Exception as exc:               # noqa: BLE001 (recorded, asserted below)
                    HUNT_RAISED.append((path, repr(exc)))
                    continue
                taken[fid] = taken.get(fid, 0) + 1
                HUNT_WALKED.append(fid)
                yield path, label, chunks, (warns or [])


def _overshoots(chunks, fsize):
    """Chunks whose bytes leave the file, read through the one accessor.

    Through `geometry.payload_of` rather than by re-deriving the rule here: a
    test that carries its own tenth copy of the thing it is policing would keep
    passing after the copy it polices was fixed.
    """
    out = []
    for c in chunks:
        if c.get("geometry") == geometry.UNPOSITIONED:
            continue
        off = c.get("offset")
        if not isinstance(off, int):
            continue
        base, n = geometry.payload_of(c)
        eoff, elen = geometry.extent_of(c)
        if base + n > fsize or eoff + elen > fsize:
            out.append((str(c.get("id")), base, n,
                        c.get("geometry") or "unnormalized"))
    return out



def _collisions(chunks):
    """Sibling pairs whose extents overlap without one enclosing the other.

    Enclosure is the reason this is not simply "do the ranges intersect". Several
    walkers emit a container and the chunks inside it in one flat list, so a
    parent spanning its children is the normal shape and reads as an overlap to
    any test that only compares adjacent spans. A first pass written that way
    flagged eighteen formats and was wrong about seventeen.

    What is left after filtering enclosure is two chunks each claiming bytes the
    other also claims, at different starts -- which no correct walker does,
    because the bytes belong to one of them.

    Read through `geometry.extent_of` rather than re-deriving offset+size here,
    for the reason the module docstring gives: a test carrying its own copy of
    the rule keeps passing after the copy it polices is fixed.
    """
    spans = []
    for c in chunks:
        if c.get("geometry") == geometry.UNPOSITIONED:
            continue
        off, ext = geometry.extent_of(c)
        if isinstance(off, int) and isinstance(ext, int) and ext >= 0:
            spans.append((off, off + ext, str(c.get("id"))))
    spans.sort()
    out = []
    for i, a in enumerate(spans):
        for b in spans[i + 1:]:
            if b[0] >= a[1]:
                break                      # sorted: nothing further can touch a
            if a[0] <= b[0] and b[1] <= a[1]:
                continue                   # a encloses b: nesting, not collision
            if b[0] <= a[0] and a[1] <= b[1]:
                continue                   # b encloses a
            out.append((a[2], a[0], a[1], b[2], b[0], b[1]))
    return out


@pytest.fixture(scope="module")
def walked():
    got = list(_corpus())
    if not got:
        pytest.skip("no walkable corpus in data/")
    return got


class TestTheGeometryIsReadableByOneRule:
    def test_no_new_chunk_leaves_the_file_unannounced(self, walked):
        """A payload range past EOF is either damage the walker reports, or a
        walker that cannot describe its own chunks. Nothing else."""
        new = {}
        for path, label, chunks, warns in walked:
            fsize = os.path.getsize(path)
            for cid, base, size, kind in _overshoots(chunks, fsize):
                if (label, cid) in KNOWN_DEFECTS:
                    continue
                # Damage the walker already called out in the file's own numbers
                # is the tool working, not failing.
                if any(str(size) in w or "remain" in w or "claims" in w
                       for w in warns):
                    continue
                new.setdefault((label, cid), (os.path.basename(path), base,
                                              size, fsize, kind))
        assert not new, (
            "chunks whose payload range leaves the file, with no warning and "
            "no known-defect entry:\n" + "\n".join(
                f"  {lab} {cid!r} in {fn}: {kind} payload {b}+{s} > {fs}"
                for (lab, cid), (fn, b, s, fs, kind) in sorted(new.items())))

    def test_the_known_defects_are_still_real(self, walked):
        """A known-failures list that outlives the failure silently re-freezes
        a bug that was already fixed."""
        still = set()
        for path, label, chunks, _warns in walked:
            fsize = os.path.getsize(path)
            for cid, _b, _s, _k in _overshoots(chunks, fsize):
                still.add((label, cid))
        stale = sorted(k for k in KNOWN_DEFECTS if k not in still)
        assert not stale, (
            f"these no longer overshoot and should leave KNOWN_DEFECTS: {stale}")

    def test_a_declared_payload_base_sits_inside_its_own_chunk(self, walked):
        """payload_base is where the contents begin, so it cannot precede the
        chunk. A walker that puts it earlier is describing something else."""
        bad = []
        for path, label, chunks, _warns in walked:
            for c in chunks:
                off, pb = c.get("offset"), c.get("payload_base")
                if not isinstance(off, int) or pb is None:
                    continue
                if pb < off:
                    bad.append((label, str(c.get("id")), off, pb,
                                os.path.basename(path)))
        assert not bad, f"payload_base before its chunk: {bad[:6]}"


class TestTheRatchetSaysWhatItCovers:
    def test_it_is_measuring_a_real_corpus(self, walked):
        """A ratchet that examines nothing passes forever. The floor is the
        COMMITTED corpus, so it holds in a clone as well as on a machine with
        the gitignored specimens; it exists so a corpus that quietly shrinks --
        a moved fixture, a walker that starts raising -- fails here instead of
        turning every assertion above into a no-op."""
        chunks = sum(len(c) for _p, _l, c, _w in walked)
        assert len(walked) >= 33, f"only {len(walked)} files walked"
        assert chunks >= 110, f"only {chunks} chunks examined"

    def test_it_says_how_far_it_reaches(self, walked):
        """Stated rather than implied. A clone's data/ exercises five formats;
        the repo has roughly thirty walkers, so a clean run here is evidence
        about those five and silence about the rest. The gap is corpus, not
        method: every format COMMITTED to data/ widens this automatically, and
        a development machine carrying data/test_formats/ already covers more
        than this asserts."""
        labels = {lab for _p, lab, _c, _w in walked}
        # The floor is what a CLONE reaches: five formats from the committed
        # fixtures, plus one per seeded format. It rises when a specimen is
        # COMMITTED or a seed is ADDED, and both are things someone did on
        # purpose -- never when a gitignored directory happens to be present.
        assert len(labels) >= 26, sorted(labels)
        assert {"RIFF/WAVE", "FLAC"} <= labels, sorted(labels)


class TestTheHuntCorpusWalks:
    def test_no_real_file_made_a_walker_raise(self, walked):
        """Only meaningful with ACIDCAT_HUNT_CORPUS set; empty otherwise.

        A walker bug on a real specimen re-raises under ACIDCAT_WALKER_RAISE
        and lands in HUNT_RAISED instead of being skipped. This is the sweep the
        audit asked for: the walk path is fuzzed with seeds, but only a real
        corpus holds the wrong format wearing the right extension."""
        del walked                                   # forces the corpus to have run
        assert not HUNT_RAISED, (
            f"{len(HUNT_RAISED)} real file(s) made a walker raise:\n" + "\n".join(
                f"  {os.path.basename(p)}: {e}" for p, e in HUNT_RAISED[:10]))

    def test_the_sweep_actually_reached_real_files(self):
        """The control, and the reason the assertion above means anything.

        `ACIDCAT_HUNT_CORPUS` pointed at a path that does not exist, or at an
        empty directory, or at a tree this process cannot read, produces an
        empty HUNT_RAISED -- which is indistinguishable from a clean sweep over
        fifty thousand files. Both were verified to pass before this existed.

        So when the variable is set, the sweep has to show it walked something.
        When it is not set, there is nothing to check and saying so is honest;
        the seeds and fixtures are the floor, and they are asserted elsewhere."""
        if not os.environ.get("ACIDCAT_HUNT_CORPUS"):
            pytest.skip("ACIDCAT_HUNT_CORPUS not set; seeds and fixtures are the floor")
        assert HUNT_WALKED, (
            "ACIDCAT_HUNT_CORPUS is set but the sweep walked no file at all. "
            "The path is wrong, unreadable, or holds nothing this tool reads, "
            "and every hunt assertion above passed on an empty set.")
        assert len(set(HUNT_WALKED)) >= 3, (
            "the sweep reached only %d format(s) (%s); a corpus that narrow is "
            "not evidence about the fleet"
            % (len(set(HUNT_WALKED)), sorted(set(HUNT_WALKED))))


class TestFieldsLandInsideTheirChunk:
    def test_no_positioned_field_escapes_its_payload(self, walked):
        """The check that would have caught the generic_walk header defect on
        the day it was written, rather than on the day someone measured it."""
        from acidcat.core.infra.fieldcodec import _field_abs
        bad = []
        for path, label, chunks, warns in walked:
            fsize = os.path.getsize(path)
            for c in chunks:
                off, size = c.get("offset"), c.get("size")
                if not isinstance(off, int) or not isinstance(size, int):
                    continue
                base, size = geometry.payload_of(c)
                if base + size > fsize:        # already covered above
                    continue
                for fl in c.get("fields") or []:
                    if fl.get("off") is None:
                        continue
                    at = _field_abs(c, fl)
                    end = at + (fl.get("len") or 0)
                    if at < base or end > base + size:
                        bad.append((label, str(c.get("id")), fl["name"],
                                    at, base, size, os.path.basename(path)))
        assert not bad, (
            "fields outside the chunk that declares them:\n" + "\n".join(
                f"  {lab} {cid!r}.{nm!r} at 0x{at:x}, payload 0x{b:x}+{s}"
                f" ({fn})" for lab, cid, nm, at, b, s, fn in bad[:8]))


# (walker label, chunk id) -> the sibling it collides with. Shrink only.
#
# Empty, and it got here the same way KNOWN_DEFECTS did -- by the two entries
# being fixed rather than recorded. Both were one defect: a chunk that never declared `payload_base`,
# so `geometry.normalize` substituted `offset + DEFAULT_HEADER` -- the RIFF
# convention of an eight-byte chunk header -- for a format that has no such
# header. The extent then runs exactly eight bytes past the chunk's own size,
# into whatever starts next.
#
# It is invisible from every angle except this one. Each chunk's `size` is
# correct, `is_trustworthy` returns True for both members of the pair, and
# nothing leaves the file, so the overshoot test above sees nothing either.
KNOWN_COLLISIONS = set()


class TestSiblingsDoNotClaimTheSameBytes:
    def test_no_new_chunk_overlaps_one_it_does_not_enclose(self, walked):
        """A byte belongs to one chunk. Two chunks claiming it means at least
        one is describing bytes it does not own, and a reader following either
        lands somewhere the walker did not intend.

        Found on `.au`, whose 24-byte header inherited an 8-byte chunk header it
        does not have and ran into its own audio. The same substitution is why
        the two entries above collide, and why this test exists at the contract
        rather than in one walker's own file."""
        new = {}
        for path, label, chunks, _warns in walked:
            for aid, a0, a1, bid, b0, b1 in _collisions(chunks):
                if (label, aid) in KNOWN_COLLISIONS:
                    continue
                new.setdefault((label, aid), (os.path.basename(path), a0, a1,
                                              bid, b0, b1))
        assert not new, (
            "chunks overlapping a sibling they do not enclose:\n" + "\n".join(
                f"  {lab} {cid!r} in {fn}: {a0}..{a1} into {bid!r} {b0}..{b1}"
                for (lab, cid), (fn, a0, a1, bid, b0, b1) in sorted(new.items())))

    def test_the_known_collisions_are_still_real(self, walked):
        """The same ratchet the overshoot list gets. A known-failure entry that
        outlives its failure re-freezes a fixed bug in silence."""
        still = set()
        for _path, label, chunks, _warns in walked:
            for aid, _a0, _a1, _bid, _b0, _b1 in _collisions(chunks):
                still.add((label, aid))
        stale = sorted(k for k in KNOWN_COLLISIONS if k not in still)
        assert not stale, (
            f"these no longer collide and should leave KNOWN_COLLISIONS: {stale}")

    def test_enclosure_is_not_counted_as_a_collision(self, walked):
        """The control, and the reason the helper is not a one-line intersect.

        Container walkers emit a parent and its children in one list. If
        enclosure counted, this suite would fail on every one of them and the
        real defect would be invisible inside the noise -- which is exactly what
        the first draft of the sweep did."""
        nested = 0
        for _path, _label, chunks, _warns in walked:
            spans = []
            for c in chunks:
                if c.get("geometry") == geometry.UNPOSITIONED:
                    continue
                off, ext = geometry.extent_of(c)
                if isinstance(off, int) and isinstance(ext, int):
                    spans.append((off, off + ext))
            for i, a in enumerate(spans):
                for b in spans[i + 1:]:
                    if a[0] <= b[0] and b[1] <= a[1] and a != b:
                        nested += 1
        assert nested > 0, (
            "no nested chunk anywhere in the corpus, so the enclosure filter "
            "is untested and this suite is not proving what it claims")


class TestPointerFieldsPointSomewhereReal:
    def test_no_xref_lands_outside_the_file(self, walked):
        """A field marked `xref` is a POINTER: the TUI follows it and the
        forensics layer resolves it. An offset past the end is a walker
        publishing a destination it never checked, and the reader finds out by
        arriving nowhere.

        Clean across the corpus when this was written, which is the reason to
        pin it now rather than after the first one appears. Eleven walkers emit
        xrefs, so the surface is wide enough to regress quietly."""
        bad = []
        for path, label, chunks, warns in walked:
            fsize = os.path.getsize(path)
            for c in chunks:
                cid = str(c.get("id"))
                for fl in c.get("fields") or []:
                    x = fl.get("xref")
                    if isinstance(x, int) and not (0 <= x <= fsize):
                        # a dangling pointer the walker already reported, by
                        # chunk id or by the pointer's own value, is damage
                        # described correctly, the same rule as the overshoot test
                        if any(("EOF" in w or "past" in w)
                               and (cid in w or f"0x{x:08x}" in w) for w in warns):
                            continue
                        bad.append((label, cid, fl.get("name"), x,
                                    fsize, os.path.basename(path)))
        assert not bad, (
            "pointer fields aimed outside their file:\n" + "\n".join(
                f"  {lab} {cid!r}.{nm!r} -> {x} in a {fs}-byte file ({fn})"
                for lab, cid, nm, x, fs, fn in bad[:8]))

    def test_some_walker_actually_emits_an_xref(self, walked):
        """The control. Without it the test above passes on a corpus where no
        walker publishes a pointer at all, which is silence rather than
        evidence."""
        n = sum(1 for _p, _l, chunks, _w in walked for c in chunks
                for fl in (c.get("fields") or [])
                if isinstance(fl.get("xref"), int))
        assert n > 0, "no xref anywhere in the corpus, so nothing was checked"
