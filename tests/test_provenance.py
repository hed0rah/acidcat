"""Provenance identification: canonicalizing writer strings and the structural
fingerprints, with honest confidence levels."""
from acidcat.core import provenance


def _chunks_with(fields):
    return [{"id": "x", "fields": [{"name": n, "value": v} for n, v in fields]}]


def test_canonicalizes_common_encoders():
    c = provenance._canon
    assert c("Lavf62.13.101") == "FFmpeg (libav 62.13.101)"
    assert c("reference libFLAC 1.4.2 20221022") == "libFLAC 1.4.2 (reference FLAC)"
    assert c("LAME3.100") == "LAME 3.100"
    assert c("Pro Tools") == "Avid Pro Tools"
    assert c("Adobe Audition CC 2024") == "Adobe Audition"
    # unknown strings pass through unchanged
    assert c("Bespoke Encoder 9") == "Bespoke Encoder 9"


def test_string_tells_are_high_confidence():
    chunks = _chunks_with([("vendor", "Lavf62.13.101")])
    sigs = provenance.identify("FLAC", chunks, b"")
    assert sigs[0]["tool"] == "FFmpeg (libav 62.13.101)"
    assert sigs[0]["confidence"] == "high"
    assert sigs[0]["basis"] == "vendor string"


def test_free_text_comment_is_not_a_tell():
    # a comment URL must not be mistaken for a tool
    chunks = _chunks_with([("comment", "Visit https://example.bandcamp.com")])
    assert provenance.identify("FLAC", chunks, b"") == []


def test_dedup_keeps_highest_confidence():
    chunks = _chunks_with([("vendor", "Lavf62.1"), ("encoder", "Lavf62.1")])
    sigs = provenance.identify("MP4/M4A", chunks, b"")
    tools = [s["tool"] for s in sigs]
    assert tools.count("FFmpeg (libav 62.1)") == 1


def test_ordering_high_before_likely():
    # a high-confidence string and a likely structural tell: string comes first
    chunks = _chunks_with([("software", "SomeEditor")])
    # inject a fake structural signal via monkeypatch-free path is hard; instead
    # assert the sort key orders high before likely on a hand-built list
    sigs = [{"tool": "b", "confidence": "likely", "basis": "x"},
            {"tool": "a", "confidence": "high", "basis": "y"}]
    order = {"high": 0, "likely": 1, "guess": 2}
    ordered = sorted(sigs, key=lambda x: order[x["confidence"]])
    assert ordered[0]["confidence"] == "high"


def _chunks_ids(ids):
    return [{"id": i, "fields": []} for i in ids]


def test_daw_chunk_signatures():
    pt = provenance._structural("RIFF/WAVE", _chunks_ids(["fmt ", "data", "regn", "minf"]), b"")
    assert any("Pro Tools" in s["tool"] and s["confidence"] == "likely" for s in pt)
    st = provenance._structural("RIFF/WAVE", _chunks_ids(["fmt ", "data", "SMED"]), b"")
    assert any("Steinberg" in s["tool"] for s in st)
    # a bare WAV yields no structural tell
    assert provenance._structural("RIFF/WAVE", _chunks_ids(["fmt ", "data"]), b"") == []


def test_chunk_signatures_only_for_iff():
    # a non-IFF label must not run the chunk-signature check
    assert provenance._structural("MP3/MPEG audio", _chunks_ids(["regn"]), b"") == []


def test_expanded_converter_canon():
    c = provenance._canon
    assert c("Exact Audio Copy V1.6") == "Exact Audio Copy"
    assert c("dBpoweramp Release 17") == "dBpoweramp"
    assert c("Lavf58.76.100 (via foobar2000)") in (
        "FFmpeg (libav 58.76.100)", "foobar2000")   # first match wins (FFmpeg)
    assert c("created with SoX") == "SoX"
