"""One answer to "which files should this verb look at".

The bug this replaces: eight commands each walked directories with their own
extension list, so one directory of `a.flac b.mp3 c.aiff d.wav` was seen as 4,
3, 1 or 0 files depending on which verb you asked -- silently.
"""

import os

import pytest

from acidcat.util import targets as T


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "a.flac").write_bytes(b"fLaC")
    (tmp_path / "b.mp3").write_bytes(b"\xff\xfb")
    (tmp_path / "c.aiff").write_bytes(b"FORM")
    (tmp_path / "d.wav").write_bytes(b"RIFF")
    (tmp_path / "notes.txt").write_text("not audio")
    (tmp_path / "cover.jpg").write_bytes(b"\xff\xd8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "e.wav").write_bytes(b"RIFF")
    return tmp_path


def test_a_directory_yields_every_audio_file_it_holds(tree):
    files, _ = T.expand([str(tree)])
    names = sorted(os.path.basename(f) for f in files)
    assert names == ["a.flac", "b.mp3", "c.aiff", "d.wav", "e.wav"]


def test_what_the_walk_passed_over_is_counted(tree):
    """A silent filter is indistinguishable from an empty directory. That is
    the whole defect: `detect DIR` skipped every FLAC and said nothing."""
    _, skipped = T.expand([str(tree)])
    assert skipped == 2, "notes.txt and cover.jpg should be counted, not dropped"
    assert "2" in T.skip_note(skipped)


def test_no_note_when_nothing_was_skipped(tmp_path):
    """The line exists to qualify a result. Printing it always would be noise."""
    (tmp_path / "a.wav").write_bytes(b"RIFF")
    _, skipped = T.expand([str(tmp_path)])
    assert skipped == 0 and T.skip_note(skipped) is None


def test_an_explicitly_named_file_is_never_filtered(tree):
    """`detect a.flac` already worked while `detect DIR` skipped the same file.
    If you name it, you asked for it -- grep does not refuse on extension."""
    odd = tree / "notes.txt"
    files, skipped = T.expand([str(odd)])
    assert files == [str(odd)]
    assert skipped == 0


def test_a_narrower_accept_set_still_takes_named_files(tree):
    """convert wants .ncw from a walk, but must still convert the file you
    point it at."""
    files, _ = T.expand([str(tree / "b.mp3")], accept={".ncw"})
    assert len(files) == 1


def test_recursion_can_be_turned_off(tree):
    deep, _ = T.expand([str(tree)])
    flat, _ = T.expand([str(tree)], recurse=False)
    assert any(os.path.basename(f) == "e.wav" for f in deep)
    assert not any(os.path.basename(f) == "e.wav" for f in flat)


def test_duplicates_collapse(tree):
    """A file named twice, or named and also inside a listed directory, is one
    file. Reporting it twice would double every count downstream."""
    p = str(tree / "d.wav")
    files, _ = T.expand([p, p, str(tree)])
    assert sum(1 for f in files if os.path.basename(f) == "d.wav") == 1


def test_dash_passes_through(tmp_path):
    """stdin handling belongs to the caller; expand must not swallow it."""
    files, _ = T.expand(["-"])
    assert files == ["-"]


def test_order_is_stable(tree):
    """Reproducible output means a diff between two runs is a real change."""
    assert T.expand([str(tree)])[0] == T.expand([str(tree)])[0]


def test_a_predicate_works_as_accept(tree):
    files, skipped = T.expand([str(tree)], accept=lambda p: p.endswith(".wav"))
    assert sorted(os.path.basename(f) for f in files) == ["d.wav", "e.wav"]
    # 7 files in the tree, 2 kept: a.flac b.mp3 c.aiff notes.txt cover.jpg
    assert skipped == 5


def test_the_extension_set_covers_what_the_old_lists_did(tree):
    """The union has to be a superset of every list it replaces, or the merge
    quietly loses formats -- which is the bug, reintroduced."""
    # validate's _EXTS is gone: it moved onto the shared expander, which is
    # the outcome this test exists to drive. The remaining lists stay checked.
    from acidcat.commands.scan import AUDIO_EXTENSIONS
    from acidcat.commands.info import _PRESET_EXTS
    for name, old in (("scan", AUDIO_EXTENSIONS), ("info", _PRESET_EXTS)):
        missing = {e.lower() for e in old} - T.KNOWN_EXTS
        assert not missing, f"{name} accepted {sorted(missing)}, the shared set does not"


def test_the_formats_acidcat_walks_are_mostly_covered():
    """A weak check on purpose: not every format id maps to one extension
    (raw/headerless ones have none, some share). It catches a wholesale gap,
    not every individual miss."""
    from acidcat.core.infra import sniff
    assert len(T.KNOWN_EXTS) >= len(sniff.KNOWN_FORMATS) * 0.8, (
        f"{len(T.KNOWN_EXTS)} extensions for {len(sniff.KNOWN_FORMATS)} formats "
        "-- the shared set has fallen behind the walkers")


# ── the structural rule ─────────────────────────────────────────────

def test_no_command_walks_directories_on_its_own():
    """Every directory walk goes through util.targets.

    Eight commands each had their own os.walk and their own extension list, so
    one directory of `a.flac b.mp3 c.aiff d.wav` was seen as 4, 3, 1 or 0 files
    depending which verb you asked -- and none of them said so. A ninth copy is
    how that comes back.

    If this fails: use targets.expand(), or add the command here with a reason.
    """
    import pathlib
    import re
    # A known-set assertion, the shape test_hex_grid_invariant already uses
    # here. detect, features and survey are converted; these are the ones left,
    # and the point of pinning the set is that a sixth cannot appear quietly.
    # (index.py was in this list on assumption -- the reverse check below
    # caught that it never walked directories at all.)
    known = {
        "classify.py",  # not yet converted -- already sees every format
        "convert.py",   # not yet converted -- .ncw only, deliberately
        "scan.py",      # not yet converted -- has its own 11-extension set
        "shape.py",     # not yet converted -- already sees every format
    }
    found = set()
    for p in sorted(pathlib.Path("src/acidcat/commands").glob("*.py")):
        src = p.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bos\.walk\s*\(", src):
            found.add(p.name)
    new = found - known
    assert not new, (
        f"a new directory walk appeared in {sorted(new)}. Use "
        "util.targets.expand() so every verb agrees on what a directory holds, "
        "and so skipped files are reported rather than dropped.")
    gone = known - found
    assert not gone, (
        f"{sorted(gone)} no longer walks directories -- remove it from the "
        "known set so the list keeps shrinking honestly.")


# extensions that name far more files than ours: walked only when named
_NOT_IN_A_WALK = {".m", ".cu8"}


def test_a_directory_walk_takes_every_format_a_walker_reads():
    """A folder of REX loops, Serum presets, EXS/SFZ and Live Packs walked as
    empty: 49 of 99 walker formats had an extension the walk did not know,
    while the same files named directly read fine."""
    import seeds
    missing = sorted({seeds.suffix(f).lower() for f in seeds.SEEDS}
                     - T.KNOWN_EXTS - _NOT_IN_A_WALK)
    assert missing == []


def test_the_formats_a_sample_library_holds_are_walked(tmp_path):
    names = ["loop.rx2", "loop.rex", "bass.SerumPreset", "kit.exs", "kit.sfz",
             "pack.alp", "group.mxgrp", "inst.sxt", "bank.labx", "tune.sid"]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    files, skipped = T.expand([str(tmp_path)])
    assert sorted(os.path.basename(f) for f in files) == sorted(names)
    assert skipped == 0
