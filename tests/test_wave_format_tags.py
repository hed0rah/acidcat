"""The `fmt ` format tag, named rather than reported as a number.

acidcat named nine of Microsoft's 265 tags, so a FLAC stream muxed into RIFF
came back as `unknown 0xf1ac` from a tool whose whole subject is telling you
what bytes are. The table is still not the whole registry -- most of it is
codecs for hardware nobody has owned since 1998 -- but it now covers what a
real audio corpus contains.

Two readers share it and both are tested here, because the second is the one
easy to miss: the plain tag in `fmt `, and the SUB-format inside a
WAVEFORMATEXTENSIBLE GUID, where a file says 0xFFFE and puts the real answer
fourteen bytes further in.
"""
import struct

import pytest

from acidcat.core.infra.vocab import WAVE_FORMAT_TAGS
from acidcat.core.walk import walk_file

_KSDATAFORMAT_TAIL = bytes.fromhex("000000001000800000aa00389b71")


def _rc(cid, payload):
    return (cid + struct.pack("<I", len(payload)) + payload
            + (b"\x00" if len(payload) % 2 else b""))


def _wav(tag=1, ch=2, bits=16, rate=44100, ext=None):
    align = ch * bits // 8
    fmt = struct.pack("<HHIIHH", tag, ch, rate, rate * align, align, bits)
    if ext is not None:
        fmt += struct.pack("<H", len(ext)) + ext
    body = b"WAVE" + _rc(b"fmt ", fmt) + _rc(b"data", b"\x00" * (64 * align))
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _extensible(sub_tag, bits=16, ch=2):
    """A WAVEFORMATEXTENSIBLE whose sub-format GUID carries `sub_tag`."""
    ext = (struct.pack("<HI", bits, 0x3)
           + struct.pack("<H", sub_tag) + _KSDATAFORMAT_TAIL)
    return _wav(tag=0xFFFE, bits=bits, ch=ch, ext=ext)


def _write(tmp_path, data, name="t.wav"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _field(chunks, name):
    for c in chunks:
        for f in c.get("fields") or []:
            if f["name"] == name:
                return f
    return None


@pytest.mark.parametrize("tag,label", [
    (0xF1AC, "FLAC"),
    (0x0161, "WMA v2"),
    (0x0163, "WMA Lossless"),
    (0x674F, "Ogg Vorbis mode 1"),
    (0x0050, "MPEG Layer I/II"),
    (0x0055, "MPEG Layer III"),
    (0x0092, "Dolby AC-3 over S/PDIF"),
    (0xA106, "MPEG-4 AAC"),
    (0x0031, "GSM 6.10"),
    (0x0001, "PCM"),
])
def test_a_named_tag_reaches_the_field_table(tmp_path, tag, label):
    _l, chunks, _w = walk_file(_write(tmp_path, _wav(tag=tag)))
    f = _field(chunks, "format_tag")
    assert f is not None
    assert f["note"] == label, f
    # the VALUE stays the raw hex: consumers key on the number, and lsb.py
    # reads this string, so naming a tag must not change what it reads
    assert f["value"] == f"0x{tag:04x}"


def test_an_unregistered_tag_still_says_unknown(tmp_path):
    """The control. Without it this file passes for a table that claims to
    name everything."""
    _l, chunks, _w = walk_file(_write(tmp_path, _wav(tag=0x1234)))
    assert _field(chunks, "format_tag")["note"] == "unknown 0x1234"


def test_the_extensible_sub_format_is_named_too(tmp_path):
    """A file that says 0xFFFE has put the real answer in the sub-format GUID.
    This reader is separate from the one above and shares the table."""
    _l, chunks, _w = walk_file(_write(tmp_path, _extensible(0xF1AC)))
    sub = _field(chunks, "sub_format")
    assert sub is not None, [f["name"] for c in chunks for f in c["fields"]]
    assert "FLAC" in str(sub["value"]) or "FLAC" in str(sub["note"])


def test_the_ac3_collision_names_both(tmp_path):
    """0x2000 is Fast Multimedia's DVM in mmreg.h and is what AC-3-in-WAV is
    written with. Printing one and not the other would be picking a side about
    two bytes that genuinely mean both."""
    _l, chunks, _w = walk_file(_write(tmp_path, _wav(tag=0x2000)))
    note = _field(chunks, "format_tag")["note"]
    assert "DVM" in note and "AC-3" in note


def test_info_and_inspect_agree_about_the_same_bytes(tmp_path):
    """`info` spelled its own codec name -- "PCM", else a bare `tag=61868` --
    so the verb a person runs first said less about the same two bytes than
    the one they run second. Both read the shared table now."""
    import argparse

    from acidcat.commands.info import _info_wav
    path = _write(tmp_path, _wav(tag=0xF1AC))
    rec = _info_wav(path, argparse.Namespace(verbose=False))
    assert "FLAC" in rec["Format"], rec["Format"]

    plain = _info_wav(_write(tmp_path, _wav(tag=1), "p.wav"),
                      argparse.Namespace(verbose=False))
    assert "PCM" in plain["Format"], plain["Format"]


def test_the_format_doc_lists_exactly_what_the_code_names():
    """docs/formats is canon, and a spec table that drifts from the tool is
    worse than no table: a reader consults it precisely to learn what the tool
    will say. Adding a tag now has to touch both, which is the point."""
    import io
    import os
    import re

    doc_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "docs", "formats", "riff_wav.md")
    if not os.path.isfile(doc_path):
        pytest.skip("docs/formats/riff_wav.md is not in this checkout")
    doc = io.open(doc_path, encoding="utf-8").read()
    start = doc.index("### Format Tags")
    section = doc[start:doc.index("For extensible format", start)]
    listed = {int(m, 16) for m in re.findall(r"`0x([0-9A-Fa-f]{4})`", section)}

    missing = sorted(f"0x{t:04X}" for t in set(WAVE_FORMAT_TAGS) - listed)
    extra = sorted(f"0x{t:04X}" for t in listed - set(WAVE_FORMAT_TAGS))
    assert not missing, f"named in code, absent from the doc table: {missing}"
    assert not extra, f"in the doc table, not named in code: {extra}"


def test_no_tag_is_named_twice_or_left_blank():
    """A value->label table with an empty label renders as a blank cell, which
    reads as 'no tag' rather than 'unnamed tag'."""
    assert all(isinstance(v, str) and v.strip() for v in WAVE_FORMAT_TAGS.values())
    assert len(set(WAVE_FORMAT_TAGS)) == len(WAVE_FORMAT_TAGS)


def test_a_12_bit_pcm_block_is_two_bytes_a_sample(tmp_path):
    """PCM pads a sample to whole bytes, so 12-bit stereo aligns on 4. The
    check computed channels * bits // 8 = 3 and warned on every such file."""
    fmt = struct.pack("<HHIIHH", 1, 2, 44100, 44100 * 4, 4, 12)
    body = b"WAVE" + _rc(b"fmt ", fmt) + _rc(b"data", b"\x00" * 64)
    _l, chunks, _w = walk_file(_write(tmp_path, b"RIFF" + struct.pack("<I", len(body)) + body))
    fmt_chunk = next(c for c in chunks if c["id"].strip() == "fmt")
    assert not any("block_align" in w for w in fmt_chunk["warnings"])
