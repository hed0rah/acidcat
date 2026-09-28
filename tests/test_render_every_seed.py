"""Every render path, on every seed, finishes without an internal error.

1.8.6 shipped `acidcat inspect` crashing on every SoundFont: the SF2 walker
added a preset and instrument tree whose nodes have no byte position, and the
table, --full and od all formatted `offset` as a number. The walk tests passed;
nothing ran the renderers over a walk that holds an unplaced node. This does,
for every format, in every mode the CLI offers for one file."""
import pytest

import seeds
from acidcat.cli import main

MODES = (["inspect"], ["inspect", "-q"], ["inspect", "--hex"], ["inspect", "--json"],
         ["inspect", "--full"], ["inspect", "--pretty"], ["od"])


@pytest.mark.parametrize("fmt", sorted(seeds.SEEDS))
def test_every_mode_renders_the_seed(fmt, tmp_path, capsys):
    path = tmp_path / ("seed" + seeds.suffix(fmt))
    path.write_bytes(seeds.build(fmt))
    for mode in MODES:
        try:
            rc = main([*mode, str(path)])
        except SystemExit as e:
            rc = e.code
        out = capsys.readouterr()
        assert rc in (0, 1, None), f"{' '.join(mode)} exited {rc}: {out.err.strip()[-300:]}"
        assert "internal error" not in out.err, f"{' '.join(mode)}: {out.err.strip()[-300:]}"


def test_the_seed_set_includes_an_unplaced_node(tmp_path):
    """The control: without a seed whose walk holds a node with no offset, the
    test above could not have caught the SoundFont crash."""
    from acidcat.core.walk import walk_file
    path = tmp_path / ("probe" + seeds.suffix("sf2"))
    path.write_bytes(seeds.build("sf2"))
    _l, chunks, _w = walk_file(str(path))
    assert any(c.get("offset") is None for c in chunks)
