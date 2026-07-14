"""Vital preset walker: metadata, plus flagging the JSON side-channels a tolerant
loader accepts -- an unknown top-level key, and bytes trailing the JSON value."""
import json

from acidcat.core import vital as vitalmod
from acidcat.core.walk import vital


def _preset(**extra):
    obj = {"synth_version": "1.5.5", "preset_name": "x", "author": "me",
           "settings": {"osc_1_on": 1.0}}
    obj.update(extra)
    return json.dumps(obj).encode()


def _write(tmp_path, data):
    p = tmp_path / "p.vital"
    p.write_bytes(data)
    return str(p)


def test_parse_vital_span_tolerates_trailing():
    obj, end = vitalmod.parse_vital_span(_preset() + b"JUNK")
    assert obj is not None and obj["synth_version"] == "1.5.5"
    assert end == len(_preset())                       # span ends before the junk
    # non-Vital JSON and non-JSON are still rejected
    assert vitalmod.parse_vital_span(b'{"a": 1}') == (None, 0)
    assert vitalmod.parse_vital_span(b"not json") == (None, 0)


def test_unknown_top_level_key_flagged(tmp_path):
    chunks, _w = vital.inspect_vital(_write(tmp_path, _preset(_cavity="SECRET")))
    warns = chunks[0]["warnings"]
    assert any("unvalidated top-level key" in w and "_cavity" in w for w in warns)


def test_trailing_data_warns_not_raises(tmp_path):
    # text and binary trailing both parse (no Unsupported) and warn
    for tail in (b"TRAILINGJUNK", bytes(range(256))):
        chunks, _w = vital.inspect_vital(_write(tmp_path, _preset() + tail))
        assert chunks[0]["id"] == "vital"
        assert any("trailing data" in w for w in chunks[0]["warnings"])


def test_clean_preset_no_side_channel_warnings(tmp_path):
    # a normal preset (standard keys, a trailing newline) is silent
    data = _preset(macro1=0.5, preset_style="Bass") + b"\n"
    chunks, _w = vital.inspect_vital(_write(tmp_path, data))
    assert chunks[0]["warnings"] == []
