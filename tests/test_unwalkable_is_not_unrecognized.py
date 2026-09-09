"""What `walk` says about a file `sniff` already named.

Three formats are sniffable and deliberately have no walker: a ROM or a disc
image is not a chunk tree, and what acidcat recovers from one is its samples,
which is `extract`'s job. The rejection said "not a recognized audio or preset
file" for all of them -- false about the tool's own state, since it HAD
recognized the file, and it sends the owner of an N64 ROM away from the verb
that would have worked.
"""
import struct

import pytest

from acidcat.core.infra import sniff as sniffmod
from acidcat.core.walk import _EXTRACT_ONLY, _WALKERS, walk_file
from acidcat.core.walk.base import Unsupported


def _n64_rom(tmp_path):
    p = tmp_path / "game.z64"
    p.write_bytes(b"\x80\x37\x12\x40" + b"\x00" * 4092)
    return str(p)


def test_the_list_is_exactly_the_sniffable_formats_with_no_walker():
    """Stated rather than inferred, and asserted so it cannot drift. A format
    that gains a walker must leave this list; one that is added to the sniffer
    without a walker must join it or explain itself here."""
    sniffable = set(sniffmod.KNOWN_FORMATS) - {"id3-wrapped"}   # a sentinel
    unwalked = sniffable - set(_WALKERS)
    assert unwalked == set(_EXTRACT_ONLY), (
        f"sniffable formats with no walker: {sorted(unwalked)}; "
        f"_EXTRACT_ONLY lists {sorted(_EXTRACT_ONLY)}")


def test_a_rom_is_told_it_is_recognized(tmp_path):
    path = _n64_rom(tmp_path)
    assert sniffmod.sniff(path) == "n64rom"
    with pytest.raises(Unsupported) as e:
        walk_file(path)
    msg = str(e.value)
    assert "recognized" in msg and "not a recognized" not in msg
    assert "extract" in msg, msg


def test_genuinely_unknown_bytes_still_say_unrecognized(tmp_path):
    """The control. Without it this passes for a build that tells everyone
    their file is recognized."""
    p = tmp_path / "x.bin"
    # not a chunk grid either, or generic triage would claim it
    p.write_bytes(bytes(range(256)) * 4)
    assert sniffmod.sniff(str(p)) is None
    with pytest.raises(Unsupported) as e:
        walk_file(str(p))
    assert "not a recognized" in str(e.value)


def test_every_extract_only_format_names_extract(tmp_path):
    """Each entry has to carry the pointer, or the message is only fixed for
    whichever one someone happened to test."""
    for fmt, phrase in _EXTRACT_ONLY.items():
        assert phrase and not phrase.endswith("."), fmt
        assert fmt in sniffmod.KNOWN_FORMATS, fmt
        assert fmt not in _WALKERS, fmt
