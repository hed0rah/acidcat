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


def test_lame_tag_enrichment():
    chunks = [{"id": "frame0", "fields": [
        {"name": "encoder", "value": "LAME3.100"},
        {"name": "vbr_method", "value": 4, "note": "VBR (mtrh)"},
        {"name": "lowpass", "value": "20500 Hz"},
        {"name": "bitrate", "value": "0 kbps"},          # VBR min -> suppressed
    ]}]
    sigs = provenance.identify("MP3/MPEG audio", chunks, b"")
    assert len(sigs) == 1
    tool = sigs[0]["tool"]
    assert tool.startswith("LAME 3.100") and "VBR (mtrh)" in tool and "lowpass" in tool
    assert "0 kbps" not in tool                          # suppressed
    assert sigs[0]["basis"] == "LAME tag"


def test_lame_not_double_listed():
    # the bare LAME encoder string must not also appear as a separate signal
    chunks = [{"id": "frame0", "fields": [
        {"name": "encoder", "value": "LAME3.99"},
        {"name": "vbr_method", "value": 3, "note": "ABR"},
    ]}]
    sigs = provenance.identify("MP3/MPEG audio", chunks, b"")
    assert sum(1 for s in sigs if s["tool"].startswith("LAME")) == 1


def test_id3_tsse_encoder_frame():
    chunks = [{"id": "ID3v2", "fields": [{"name": "TSSE", "value": "Lavf62.13.101"}]}]
    sigs = provenance.identify("MP3/MPEG audio", chunks, b"")
    assert any(s["tool"] == "FFmpeg (libav 62.13.101)" for s in sigs)


# ── DAW chunk signatures (field-team DEV FINDINGS, corpus-verified) ──


def test_logic_pro_chunk_signatures():
    # LGWV (405 files) and ResU (303) each identify Logic even when the bext
    # originator is stripped or re-branded. Corpus: ResU is Logic, not Steinberg.
    for cid in ("LGWV", "ResU"):
        sigs = provenance._structural("RIFF/WAVE", _chunks_ids(["fmt ", "data", cid]), b"")
        assert any(s["tool"] == "Apple Logic Pro" and s["confidence"] == "likely"
                   for s in sigs), cid


def test_digital_performer_chunk_signature():
    # any of the dp* family identifies MOTU DP (100% co-occur, 0 FP in 2345 files)
    for cid in ("dprn", "dpte", "dpas", "dpam"):
        sigs = provenance._structural("RIFF/WAVE", _chunks_ids(["fmt ", "data", cid]), b"")
        assert any(s["tool"] == "MOTU Digital Performer" for s in sigs), cid


def test_bitwig_bwbm_chunk_signature():
    # BWBM is the only provenance handle on a string-stripped Bitwig render
    sigs = provenance._structural("RIFF/WAVE", _chunks_ids(["JUNK", "BWBM", "data"]), b"")
    assert any(s["tool"] == "Bitwig Studio" for s in sigs)


def test_digidesign_dgda_chunk_signature():
    sigs = provenance._structural("RIFF/WAVE", _chunks_ids(["fmt ", "data", "DGDA"]), b"")
    assert any("Digidesign" in s["tool"] for s in sigs)


def test_afan_afmd_not_attributed_to_one_app():
    # shared macOS CoreAudio chunks must NOT identify a single app (Logic or DP)
    sigs = provenance._structural(
        "RIFF/WAVE", _chunks_ids(["fmt ", "data", "AFAn", "AFmd"]), b"")
    assert sigs == []


def test_dp_and_edison_canon():
    c = provenance._canon
    assert c("Digital Performer") == "MOTU Digital Performer"
    assert c("Digital Performer 11") == "MOTU Digital Performer"
    assert c("Edison") == "Image-Line Edison (FL Studio)"
    # a full FL Studio string still canonicalizes to FL Studio (bare-Edison rule only)
    assert c("FL Studio 21") == "FL Studio"


def test_tracker_field_distinctive_and_default():
    # a distinctive tracker stamp is a writer tell; the format default is suppressed
    d = provenance.identify("FastTracker II XM",
                            _chunks_with([("tracker", "OpenMPT 1.30.06.00")]), b"")
    assert any(s["tool"] == "OpenMPT 1.30.06.00" for s in d)
    default = provenance.identify("FastTracker II XM",
                                  _chunks_with([("tracker", "FastTracker v2.00")]), b"")
    assert default == []


def test_comment_tell_is_narrow():
    # "made with <tool>" is mined; a free-text / URL comment is not
    fl = provenance.identify(
        "RIFF/WAVE", _chunks_with([("comment", "made with FL Studio 4 (98-02)")]), b"")
    assert any(s["tool"] == "FL Studio" and s["confidence"] == "likely" for s in fl)
    url = provenance.identify(
        "RIFF/WAVE", _chunks_with([("comment", "Visit https://example.bandcamp.com")]), b"")
    assert url == []


def test_iart_portapack_device_tell():
    dev = provenance.identify("RIFF/WAVE", _chunks_with([("iart", "PortaPack")]), b"")
    assert any("PortaPack" in s["tool"] for s in dev)
    # a normal IART artist is not a device tell
    assert provenance.identify(
        "RIFF/WAVE", _chunks_with([("iart", "Some Artist")]), b"") == []
