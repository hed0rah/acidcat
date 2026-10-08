"""Contract values before JSON v1 freezes (review R6).

(a) `value` is never a formatted number: an int-valued display string is
    the int, except in a field typed as text.
(b) Every derived field says what it came from (`derived_from`): a walker's
    own sources, a positioned twin it repeats, or its node, which
    `typing.derived_by_node` counts.
(c) An all-zero gap inside a payload is `padding`, not `unwalked`.
(d) MP3 frame rows are snake_case with a real `at.len`.
"""

import re
import struct

import pytest

import acidcat
import seeds
from acidcat.core.infra import contract

_NUM = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)$")


def _docs():
    for fmt in sorted(seeds.SEEDS):
        try:
            yield fmt, acidcat.open(seeds.build(fmt), forensics=False).to_json()
        except Exception:
            continue


def test_no_value_is_a_formatted_number():
    bad = [f"{fmt}:{n['id']}#{f['key']}={f['value']!r}"
           for fmt, d in _docs() for n in contract.iter_nodes(d) for f in n["fields"]
           if isinstance(f["value"], str) and f["type"] not in ("ascii", "fourcc")
           and _NUM.match(f["value"].strip())]
    assert not bad, bad[:10]


def test_a_thousands_display_becomes_its_int():
    f = contract._field({"name": "frames", "value": "8,755", "off": None, "len": 0},
                        None, b"", False, "frames")
    assert (f["value"], f["display"], f["type"]) == (8755, "8,755", "derived")


def test_text_that_reads_as_a_number_stays_text():
    data = b"2024"
    f = contract._field({"name": "year", "value": "2024", "off": 0, "len": 4},
                        0, data, False, "year")
    assert f["type"] == "fourcc" and f["value"] == "2024"


def test_every_derived_field_says_what_it_came_from():
    for fmt, d in _docs():
        ids = {n["id"] for n in contract.iter_nodes(d)}
        by_node = 0
        for n in contract.iter_nodes(d):
            for f in n["fields"]:
                if f["type"] != "derived":
                    continue
                src = f.get("derived_from")
                assert src, f"{fmt}:{n['id']}#{f['key']} has no derived_from"
                for s in src:
                    node = s.split("#", 1)[0]
                    assert node in ids, f"{fmt}: {s} names no node"
                if src == [n["id"]]:
                    by_node += 1
        assert d["typing"]["derived_by_node"] == by_node, fmt


def test_a_copy_names_its_positioned_twin():
    d = acidcat.open(seeds.build("adg"), forensics=False)
    f = d.field("Ableton#MajorVersion")
    assert f.raw["derived_from"] == ["Ableton/xml#MajorVersion"]


def test_a_walker_can_name_its_sources():
    from acidcat.core.walk.base import _f
    fl = _f(None, 0, "duration", 1.5, derived_from=["frames", "RIFF/fmt_#sample_rate"])
    chunk = {"id": "data", "offset": 0, "extent_len": 8, "payload_base": 8,
             "payload_len": 0, "geometry": "declared", "summary": "",
             "fields": [_f(None, 0, "frames", 3), fl]}
    d = contract.document("x", "X", [chunk], [], bytes(8))
    got = d["nodes"][0]["fields"][1]["derived_from"]
    assert got == ["data#frames", "RIFF/fmt_#sample_rate"]


def _gapped(fill):
    """A 32-byte container (8-byte header) holding one 8-byte chunk at 8 and
    `fill` from 16 to 32 that no node describes."""
    data = bytes(8) + b"abcdefgh" + fill
    chunks = [{"id": "BOX ", "offset": 0, "extent_len": 32, "payload_base": 8,
               "payload_len": 24, "geometry": "declared", "summary": "", "fields": []},
              {"id": "part", "offset": 8, "extent_len": 8, "payload_base": 8,
               "payload_len": 8, "geometry": "declared", "summary": "", "fields": []}]
    return contract.document("x", "X", chunks, [], data)


def test_an_all_zero_gap_inside_a_payload_is_padding():
    d = _gapped(bytes(16))
    gaps = [(n["kind"], n["id"], n["extent"]["off"], n["extent"]["len"])
            for n in contract.iter_nodes(d) if n["kind"] in ("padding", "unwalked")]
    assert gaps == [("padding", "BOX_/padding", 16, 16)]
    assert d["typing"]["nodes_unwalked"] == 0


def test_a_nonzero_gap_stays_unwalked():
    d = _gapped(bytes(15) + b"\x01")
    assert [n["kind"] for n in contract.iter_nodes(d)][-1] == "unwalked"
    assert d["typing"]["nodes_unwalked"] == 1


def test_mp3_rows_are_snake_case_and_placed():
    d = acidcat.open(seeds.build("mp3"), forensics=False,
                     limits=acidcat.Limits(decode=True))
    rows = next(n.rows for n in d.walk() if n.rows)["items"]
    assert set(rows[0]) == {"index", "bitrate_kbps", "sample_rate", "mode",
                            "bytes", "at"}
    assert rows[1]["at"] == {"layer": 0, "off": rows[0]["bytes"], "len": rows[1]["bytes"]}
