"""The Vorbis comment header is reported whenever it exists.

It was reported only when it held at least one TAG, and most Ogg files in the
wild hold none. What they do hold is the header's vendor string -- the
encoder's own name for itself, down to the build date -- and gating on the tag
count threw away the one provenance fact the header was written to carry. In a
1,200-file walk the header was present in 1,176 files and reported in 18.
"""

import struct

import pytest

from acidcat.core.walk.ogg import inspect_ogg


def _page(serial, seq, body, *, bos=False, eos=False, granule=0):
    """One well-formed Ogg page, with a segment table so the walker can find
    the next page rather than searching for the next magic."""
    htype = (0x02 if bos else 0) | (0x04 if eos else 0)
    segs, rest = [], len(body)
    while rest >= 255:
        segs.append(255)
        rest -= 255
    segs.append(rest)
    return (b"OggS" + bytes([0, htype])
            + struct.pack("<q", granule)
            + struct.pack("<I", serial)
            + struct.pack("<I", seq)
            + struct.pack("<I", 0)               # crc, unchecked by the walker
            + bytes([len(segs)]) + bytes(segs) + body)


def _ident():
    """A Vorbis identification header: 2 channels at 48 kHz."""
    return (b"\x01vorbis" + struct.pack("<I", 0) + bytes([2])
            + struct.pack("<I", 48000)
            + struct.pack("<iii", 0, 128000, 0)
            + bytes([0xB8]) + b"\x01")


def _comments(vendor=b"Xiph.Org libVorbis I 20200704", tags=()):
    out = b"\x03vorbis" + struct.pack("<I", len(vendor)) + vendor
    out += struct.pack("<I", len(tags))
    for t in tags:
        out += struct.pack("<I", len(t)) + t
    return out + b"\x01"                          # framing bit


def _ogg(tmp_path, comment_packet, name="t.ogg"):
    data = (_page(1, 0, _ident(), bos=True)
            + _page(1, 1, comment_packet)
            + _page(1, 2, b"\x00" * 64, eos=True, granule=48000))
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _named(chunks, cid):
    return next((c for c in chunks if c["id"] == cid), None)


def test_a_header_with_no_tags_still_reports_its_vendor(tmp_path):
    """The bug. An encoder that writes its name and no tags is the ordinary
    case, and the whole chunk was being dropped."""
    chunks, _w = inspect_ogg(_ogg(tmp_path, _comments()))
    comments = _named(chunks, "comments")
    assert comments is not None, "the comment header was dropped"
    vendor = next(f for f in comments["fields"] if f["name"] == "vendor")
    assert vendor["value"] == "Xiph.Org libVorbis I 20200704"
    assert "no comments" in comments["summary"]
    assert "libVorbis" in comments["summary"]


def test_tags_are_still_read(tmp_path):
    """The control: adding tags must not cost the vendor, and the tags have to
    keep arriving as fields."""
    chunks, _w = inspect_ogg(_ogg(tmp_path, _comments(
        tags=(b"ARTIST=Example", b"TITLE=A Track"))))
    comments = _named(chunks, "comments")
    f = {x["name"]: x["value"] for x in comments["fields"]}
    assert f["vendor"] == "Xiph.Org libVorbis I 20200704"
    assert f["ARTIST"] == "Example"
    assert f["TITLE"] == "A Track"
    assert comments["summary"].startswith("2 Vorbis comment(s)")


def test_a_header_with_neither_vendor_nor_tags_is_not_invented(tmp_path):
    """The other control. An empty header carries nothing, and a chunk with no
    fields and no summary is noise rather than a finding."""
    chunks, _w = inspect_ogg(_ogg(tmp_path, _comments(vendor=b"")))
    assert _named(chunks, "comments") is None


def test_the_comment_listing_announces_its_own_bound(tmp_path, monkeypatch):
    from acidcat.core.walk import ogg as woggm

    monkeypatch.setattr(woggm, "_TAG_LIST_CAP", 3)
    tags = tuple(f"T{i}=v{i}".encode("ascii") for i in range(10))
    chunks, warns = inspect_ogg(_ogg(tmp_path, _comments(tags=tags)))
    comments = _named(chunks, "comments")
    assert len(comments["fields"]) == 1 + 3          # vendor + the bound
    assert any("listing the first 3 of 10" in w for w in comments["warnings"])
    # and it reaches the file level, where a caller reading only those would
    # otherwise be told the listing was complete
    assert any("listing the first 3 of 10" in w for w in warns)
