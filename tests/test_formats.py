"""The format-dispatch invariant tests plus the `formats` command.

acidcat keys several independent dispatch tables (walkers, sample extractors,
convert, repair) on the format-id strings core/sniff.py returns. Nothing at
runtime checks that a table key is a real id, so a typo (`wiidisc` for `wii`)
would fail as a silent dict-miss. These tests make the sniff namespace
(sniff.KNOWN_FORMATS) authoritative and assert every table conforms to it -- the
enforcement the `formats` command only visualizes."""
import re
from pathlib import Path

import acidcat.core.infra.sniff as sniffmod
from acidcat.commands import formats
from acidcat.core.extract import samples
from acidcat.core.walk import _WALKERS


def test_known_formats_matches_sniff_source():
    """KNOWN_FORMATS must equal the ids sniff actually returns -- so the declared
    namespace can't drift from the code. Parse the return/else string literals out
    of sniff.py and compare (the KNOWN_FORMATS literal itself is not in a return
    position, so it isn't picked up)."""
    src = Path(sniffmod.__file__).read_text(encoding="utf-8")
    returned = set(re.findall(r'(?:return|else)\s+"([a-z0-9_-]+)"', src))
    assert returned == set(sniffmod.KNOWN_FORMATS)


def test_walker_keys_are_known_formats():
    unknown = set(_WALKERS) - sniffmod.KNOWN_FORMATS
    assert not unknown, f"walker keys not in sniff.KNOWN_FORMATS: {sorted(unknown)}"


def test_extractor_keys_are_known_formats():
    keys = set(samples._EXTRACTORS) | set(samples._PATH_EXTRACTORS)
    unknown = keys - sniffmod.KNOWN_FORMATS
    assert not unknown, f"extractor keys not in sniff.KNOWN_FORMATS: {sorted(unknown)}"


def test_convert_and_repair_sets_are_known_formats():
    unknown = (formats._CONVERT | formats._REPAIR) - sniffmod.KNOWN_FORMATS
    assert not unknown, f"convert/repair ids not in sniff.KNOWN_FORMATS: {sorted(unknown)}"


# a representative header per candidate container, so we can probe the live
# convert/repair dispatch instead of trusting the hand-lists. Includes negatives
# (formats that must NOT be repair/convert-capable) so an over-claim also fails.
_MAGIC = {
    "wav": b"RIFF\x00\x00\x00\x00WAVE", "rf64": b"RF64\x00\x00\x00\x00WAVE",
    "sf2": b"RIFF\x00\x00\x00\x00sfbk", "rmid": b"RIFF\x00\x00\x00\x00RMID",
    "akp": b"RIFF\x00\x00\x00\x00APRG", "aiff": b"FORM\x00\x00\x00\x00AIFF",
    "aifc": b"FORM\x00\x00\x00\x00AIFC", "8svx": b"FORM\x00\x00\x00\x008SVX",
    "smus": b"FORM\x00\x00\x00\x00SMUS", "e4b": b"FORM\x00\x00\x00\x00E4B0",
    "e5b": b"FORM\x00\x00\x00\x00E5B0", "flac": b"fLaC" + bytes(8),
    "mp4": b"\x00\x00\x00\x18ftypisom" + bytes(8),
    "au": b".snd" + bytes(20),
    "mp3": b"ID3\x03\x00" + bytes(20), "vital": b"{}" + bytes(10),   # negatives
}


def test_audio_container_table_is_single_source():
    # carve and locate both derive their audio-container constants from the one
    # table in sniff (Tier-2 dedup). Pin that they stay wired to it and consistent,
    # so nobody re-hardcodes a copy that can drift.
    from acidcat.commands import carve
    from acidcat.core.forensics import locate
    assert carve._EXT is sniffmod.AUDIO_CONTAINER_EXT
    assert locate._CONTAINER_MAGICS is sniffmod.AUDIO_CONTAINER_MAGICS
    assert set(locate._AUDIO_CONTAINER_FMTS) == set(sniffmod.AUDIO_CONTAINERS)
    assert set(sniffmod.AUDIO_CONTAINERS) <= sniffmod.KNOWN_FORMATS
    # the scan magics are exactly the distinct leading magics, de-duped in order
    assert sniffmod.AUDIO_CONTAINER_MAGICS == tuple(
        dict.fromkeys(m for m, _e in sniffmod.AUDIO_CONTAINERS.values()))


def test_repair_set_matches_live_dispatch():
    # derive the repair-capable set by probing constraints.repairer_for with a real
    # magic per format; it must equal _REPAIR exactly. Catches both the omission
    # class (flac was missing) and any over-claim.
    from acidcat.core.write import constraints
    derived = {fid for fid, m in _MAGIC.items() if constraints.repairer_for(m)}
    assert derived == formats._REPAIR


def test_convert_set_matches_live_dispatch():
    # convert.run() branches on these predicates; probe them the same way.
    from acidcat.core.formats import sf2 as sf2mod, svx as svxmod
    from acidcat.core.codecs import ncw as ncwmod
    from acidcat.core.formats import bitwig as bwmod
    from acidcat.core.walk import au as aumod
    def convertible(fid, m):
        return (m[:4] == ncwmod.MAGIC or svxmod.is_8svx(m) or sf2mod.is_sf2(m)
                or m[:4] == bwmod.MAGIC or (m[:4] == b"RIFF" and m[8:12] == b"WAVE")
                or aumod.parse_header(m) is not None)
    probe = dict(_MAGIC, ncw=ncwmod.MAGIC + bytes(8), bitwig=bwmod.MAGIC + bytes(8))
    derived = {fid for fid, m in probe.items() if convertible(fid, m)}
    assert derived == formats._CONVERT


def test_extractable_equals_registries():
    # the public EXTRACTABLE set must be exactly the two extractor tables
    assert samples.EXTRACTABLE == frozenset(samples._EXTRACTORS) | frozenset(samples._PATH_EXTRACTORS)


# ---- the command itself -----------------------------------------------------

def test_matrix_covers_every_capability_id():
    ids = {r["id"] for r in formats._matrix()}
    assert set(_WALKERS) <= ids and set(samples.EXTRACTABLE) <= ids
    assert formats._CONVERT <= ids and formats._REPAIR <= ids


def test_matrix_flags_are_correct():
    rows = {r["id"]: r for r in formats._matrix()}
    # sf2 is the one format supported by all four capabilities
    assert rows["sf2"]["inspect"] and rows["sf2"]["extract"]
    assert rows["sf2"]["convert"] and rows["sf2"]["repair"]
    # a tracker module inspects + extracts but does not convert or repair
    assert rows["it"]["inspect"] and rows["it"]["extract"]
    assert not rows["it"]["convert"] and not rows["it"]["repair"]


def test_every_row_has_a_label():
    assert all(r["label"] for r in formats._matrix())


def test_command_runs_table_json_tsv(capsys):
    class A:
        format = None
    for shape in ("table", "json", "tsv"):
        A.output_format = shape
        assert formats.run(A) == 0
    out = capsys.readouterr().out
    assert out                                          # produced output for the last shape


def test_command_single_format_and_miss(capsys):
    class A:
        format = "sf2"; output_format = "table"
    assert formats.run(A) == 0
    A.format = "nope-not-real"
    assert formats.run(A) == 1


def test_edit_set_matches_live_dispatch(tmp_path):
    """`acidcat write` is the one verb that changes a file, and the capability
    matrix did not have a column for it until 1.6.1. The tool could edit
    metadata in thirteen formats and the command whose whole job is answering
    "what can acidcat do with format X" did not mention editing at all.

    Derived the same way as convert and repair: feed edit_metadata a real magic
    per format and see which ones it refuses. Catches the omission class and
    the over-claim equally.
    """
    from acidcat.core.write.edits import EditError, edit_metadata

    probe = dict(_MAGIC,
                 ogg=b"OggS" + bytes(24),
                 vital=b'{"synth_version":"1.0"}' + bytes(8),
                 bitwig=b"BtWg" + bytes(20),
                 ni=b"RIFF\x00\x00\x00\x00NIKS" + bytes(8))
    derived = set()
    for fid, magic in probe.items():
        p = tmp_path / f"{fid}.bin"
        p.write_bytes(magic + bytes(64))
        try:
            edit_metadata(str(p), {})
        except EditError as e:
            # EditError covers two different answers and only one of them
            # means "unsupported": the dispatch raises it for a file type it
            # has no editor for, and an editor raises it for content it does
            # have but cannot use. A 12-byte RIFF/WAVE stub with no fmt chunk
            # is the second kind, and reading it as the first would have made
            # this test agree with a matrix that claimed nothing.
            if "no metadata editor for this file type" in str(e):
                continue
        except Exception:
            pass                           # has one; it just disliked the stub
        derived.add(fid)
    # every format the dispatch accepts must be claimed, and nothing else
    assert derived == formats._EDIT & set(probe), (
        f"claimed but not dispatched: {formats._EDIT & set(probe) - derived}; "
        f"dispatched but not claimed: {derived - formats._EDIT}")


def test_edit_set_are_known_formats():
    unknown = formats._EDIT - sniffmod.KNOWN_FORMATS
    assert not unknown, unknown
