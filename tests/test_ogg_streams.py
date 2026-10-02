"""`locate` finds Ogg streams, not Ogg pages.

Ogg stamps `OggS` on every page, and the signature sweep took each hit as its
own container. A single song therefore reported as hundreds of regions, and
`carve --batch` wrote hundreds of individually unplayable fragments -- on the
most ordinary input there is, a plain .ogg file on disk.

Inside a scan segment the failure inverted and got worse. `_resolve_container_
ends` ended each page-region exactly where the next began, so the TUI's
`_merge_boundary`, which coalesces exactly-adjacent regions of the same kind and
format, swallowed every page in a segment into one region. A 187 MB archive
holding 64 songs reported 22 regions whose offsets sat on 16 MB boundaries: they
were scan segments, not songs. Extracted, each played in VLC (which handles
chained Ogg) and errored elsewhere.

The file says where a stream ends. header_type bit 2 is end-of-stream and the
serial at +14 says which logical stream a page belongs to. Grouping is by
PHYSICAL stream rather than by serial because a multiplexed file interleaves
several serials over the same bytes -- their ranges overlap, so carving one out
by byte range is not a thing that can be done.
"""

import struct

import pytest

from acidcat.core.forensics import locate as L


def _page(serial, seq, *, bos=False, eos=False, body=b"\x00" * 64):
    """One well-formed Ogg page. Segment table, so a walker can find the next
    page without searching for the next magic."""
    htype = (0x02 if bos else 0) | (0x04 if eos else 0)
    segs, rest = [], len(body)
    while rest >= 255:
        segs.append(255)
        rest -= 255
    segs.append(rest)
    hdr = (b"OggS" + bytes([0, htype])
           + struct.pack("<q", seq * 1000)      # granule
           + struct.pack("<I", serial)
           + struct.pack("<I", seq)
           + struct.pack("<I", 0)               # crc, unchecked here
           + bytes([len(segs)]) + bytes(segs))
    return hdr + body


def _stream(serial, pages=4):
    out = b""
    for i in range(pages):
        out += _page(serial, i, bos=(i == 0), eos=(i == pages - 1))
    return out


class TestOneSongIsOneRegion:
    def test_a_single_stream_is_one_region(self):
        """The ordinary case, and the one that was most broken."""
        data = _stream(7777, pages=8)
        rs = [r for r in L.locate(data, mode="strict") if r["format"] == "ogg"]
        assert len(rs) == 1, f"{len(rs)} regions for one song"
        assert (rs[0]["offset"], rs[0]["end"]) == (0, len(data))

    def test_page_count_does_not_change_the_region_count(self):
        for pages in (2, 5, 40, 200):
            data = _stream(1234, pages=pages)
            rs = [r for r in L.locate(data, mode="strict")
                  if r["format"] == "ogg"]
            assert len(rs) == 1, f"{pages} pages produced {len(rs)} regions"

    def test_the_region_carries_its_serial(self):
        rs = [r for r in L.locate(_stream(4242), mode="strict")
              if r["format"] == "ogg"]
        assert rs[0]["stream_serials"] == (4242,)

    def test_a_complete_stream_is_not_marked_streaming(self):
        """`streaming_extent` means the end was inferred. An EOS page is the
        format declaring it, which is stronger."""
        rs = [r for r in L.locate(_stream(1), mode="strict")
              if r["format"] == "ogg"]
        assert rs[0]["streaming_extent"] is False


class TestChainedStreams:
    def test_songs_butted_together_stay_separate(self):
        """48 of the 64 streams in the real archive had a zero-byte gap, which
        is exactly the case adjacency cannot tell from a continuation."""
        data = _stream(11) + _stream(22) + _stream(33)
        rs = [r for r in L.locate(data, mode="strict") if r["format"] == "ogg"]
        assert len(rs) == 3, f"{len(rs)} regions for 3 concatenated songs"
        assert [r["stream_serials"] for r in rs] == [(11,), (22,), (33,)]

    def test_the_regions_tile_the_file_exactly(self):
        """Every byte accounted for, no overlap: what carve depends on."""
        parts = [_stream(s) for s in (5, 6, 7)]
        data = b"".join(parts)
        rs = [r for r in L.locate(data, mode="strict") if r["format"] == "ogg"]
        assert [(r["offset"], r["length"]) for r in rs] == [
            (0, len(parts[0])),
            (len(parts[0]), len(parts[1])),
            (len(parts[0]) + len(parts[1]), len(parts[2])),
        ]

    def test_a_gap_between_songs_is_resynced_past(self):
        """Streams are not always butted together -- in the real archive they
        sit 1,116 bytes apart. A walk that stopped at the first non-page found
        the first song and missed every one after it."""
        data = _stream(11) + b"\x00" * 1116 + _stream(22)
        rs = [r for r in L.locate(data, mode="strict") if r["format"] == "ogg"]
        assert len(rs) == 2, f"{len(rs)} regions across a padded gap"
        assert [r["stream_serials"] for r in rs] == [(11,), (22,)]


class TestMultiplexedStreams:
    def test_interleaved_serials_are_one_physical_stream(self):
        """Video plus audio share the same bytes. Their ranges overlap, so they
        cannot be separate regions -- one region carrying both serials is the
        only answer a byte range can give."""
        data = _page(1, 0, bos=True) + _page(2, 0, bos=True)
        for i in range(1, 4):
            data += _page(1, i) + _page(2, i)
        data += _page(1, 4, eos=True) + _page(2, 4, eos=True)
        rs = [r for r in L.locate(data, mode="strict") if r["format"] == "ogg"]
        assert len(rs) == 1, f"{len(rs)} regions for one multiplexed file"
        assert rs[0]["stream_serials"] == (1, 2)
        assert (rs[0]["offset"], rs[0]["end"]) == (0, len(data))

    def test_it_closes_only_when_every_serial_has_ended(self):
        """Closing on the first EOS would cut the file in half."""
        data = (_page(1, 0, bos=True) + _page(2, 0, bos=True)
                + _page(1, 1, eos=True)          # one ends early
                + _page(2, 1) + _page(2, 2, eos=True))
        rs = [r for r in L.locate(data, mode="strict") if r["format"] == "ogg"]
        assert len(rs) == 1
        assert rs[0]["end"] == len(data)


class TestTruncation:
    def test_a_stream_with_no_eos_is_marked_incomplete(self):
        """A scan segment ends mid-stream constantly. An inferred end must not
        be presented as a declared one."""
        data = _page(9, 0, bos=True) + _page(9, 1) + _page(9, 2)
        rs = [r for r in L.locate(data, mode="strict") if r["format"] == "ogg"]
        assert len(rs) == 1
        assert rs[0]["streaming_extent"] is True
        assert rs[0]["end"] == len(data)

    def test_a_fragment_with_no_bos_is_still_found(self):
        """Carved mid-stream, or the tail half of a split scan segment. It has
        no beginning, and reporting nothing at all would be worse."""
        data = _page(9, 5) + _page(9, 6, eos=True)
        rs = [r for r in L.locate(data, mode="strict") if r["format"] == "ogg"]
        assert len(rs) == 1
        assert rs[0]["offset"] == 0

    def test_a_torn_final_page_does_not_lose_the_stream(self):
        full = _stream(3, pages=4)
        rs = [r for r in L.locate(full[:-20], mode="strict")
              if r["format"] == "ogg"]
        assert len(rs) == 1
        assert rs[0]["streaming_extent"] is True


class TestTheSegmentedMerge:
    """The TUI scans in 16 MB segments and heals regions split across the edge.
    That heal is what turned every song in a segment into one region."""

    def _merge(self, regions):
        from acidcat.tui_app.app import AcidcatTUI
        return AcidcatTUI._merge_boundary(regions)

    def _ogg(self, off, end, serials, incomplete):
        return {"kind": "container", "format": "ogg", "offset": off, "end": end,
                "length": end - off, "stream_serials": serials,
                "streaming_extent": incomplete, "confidence": 0.9}

    def test_two_adjacent_songs_do_not_merge(self):
        pytest.importorskip("textual")
        out = self._merge([self._ogg(0, 100, (11,), False),
                           self._ogg(100, 200, (22,), False)])
        assert len(out) == 2, "two songs were merged into one region"

    def test_one_song_split_across_a_segment_edge_does_merge(self):
        """The behaviour the heal exists for: same serial, and the first half
        is marked incomplete because the segment ran out."""
        pytest.importorskip("textual")
        out = self._merge([self._ogg(0, 100, (11,), True),
                           self._ogg(100, 200, (11,), False)])
        assert len(out) == 1
        assert (out[0]["offset"], out[0]["end"]) == (0, 200)
        assert out[0]["length"] == 200

    def test_a_completed_stream_never_absorbs_what_follows(self):
        """Same serial can recur across a chained file. A stream that already
        saw its EOS is finished, whatever comes next."""
        pytest.importorskip("textual")
        out = self._merge([self._ogg(0, 100, (11,), False),
                           self._ogg(100, 200, (11,), False)])
        assert len(out) == 2

    def test_non_ogg_regions_still_heal_by_adjacency(self):
        """The blob-splitting case the function was written for is untouched."""
        pytest.importorskip("textual")
        blob = lambda o, e: {"kind": "blob", "format": None, "offset": o,
                             "end": e, "length": e - o, "confidence": 0.5}
        out = self._merge([blob(0, 100), blob(100, 200)])
        assert len(out) == 1 and out[0]["end"] == 200


class TestStreamCountsAgree:
    """Bug hunt 2026-10-02: the walker counted every serial and every page,
    the anomaly rule counted only serials that had a BOS page, and the pages
    field counted all streams beside a note saying it described the first."""

    def _walk(self, tmp_path, data):
        from acidcat.core.walk.ogg import inspect_ogg
        p = tmp_path / "multi.ogg"
        p.write_bytes(data)
        return p, inspect_ogg(str(p))

    def test_pages_counts_the_first_stream(self, tmp_path):
        _p, (chunks, _w) = self._walk(tmp_path, _stream(1, pages=3) + _stream(2, pages=10))
        pages = next(f for f in chunks[0]["fields"] if f["name"] == "pages")
        assert pages["value"] == 3 and "13" in pages["note"]

    def test_a_stream_without_a_bos_page_is_still_counted(self, tmp_path):
        from acidcat.core.forensics import anomalies
        cont = b"".join(_page(2, i) for i in range(1, 4))      # no BOS page
        p, (_c, warns) = self._walk(tmp_path, _stream(1) + cont)
        assert any("2 logical bitstreams" in str(w) for w in warns)
        findings = anomalies.scan(str(p))
        hit = [f for f in findings if f["rule"] == "ogg_multistream"]
        assert hit and "2 logical bitstreams" in hit[0]["message"]
        assert "continuation" in hit[0]["message"]
