"""Two detectors the docs called proposed, and the fixtures that fire them.

A detector nobody has ever made fire is a claim, not a check. Before this,
`application_block` and `nonprintable_text` were referenced by no test at all,
and the two rules below did not exist despite being documented as coming.

Each test constructs the thing the rule exists to find, rather than asserting
on a hand-built finding dict -- a rule can only be trusted if a real file has
driven it.
"""

import json
import struct

import pytest

from acidcat.core.forensics import anomalies
from acidcat.core.walk import walk_file


def _scan(path):
    fmt, chunks, warns = walk_file(str(path))
    return fmt, anomalies.scan(str(path), fmt, chunks, warns)


def _rules(findings):
    return {f["rule"] for f in findings}


def _wav_bytes(pcm=b"\x00\x01" * 2000, extra=b""):
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
            + extra + b"data" + struct.pack("<I", len(pcm)) + pcm)
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ── embedded_standalone_media ────────────────────────────────────────────

def test_a_whole_wav_hidden_inside_a_wav_is_found(tmp_path):
    """The case no other rule catches. It is not past the declared end, so
    trailing_data cannot see it; it is not in a spec-ignorable region, so
    cavity_content cannot either. The container is intact and the sizes agree."""
    inner = _wav_bytes(pcm=b"\x7f\x80" * 500)
    junk = b"JUNK" + struct.pack("<I", len(inner)) + inner
    p = tmp_path / "nested.wav"
    p.write_bytes(_wav_bytes(extra=junk))

    _fmt, found = _scan(p)
    assert "embedded_standalone_media" in _rules(found)
    hit = [f for f in found if f["rule"] == "embedded_standalone_media"][0]
    assert "WAV" in hit["message"]
    assert hit["offset"] > 0


def test_an_ordinary_wav_is_not_flagged(tmp_path):
    p = tmp_path / "plain.wav"
    p.write_bytes(_wav_bytes())
    assert "embedded_standalone_media" not in _rules(_scan(p)[1])


def test_ogg_pages_are_not_mistaken_for_embedded_oggs():
    """Ogg stamps OggS on every page, so page 2 looks like an embedded Ogg.
    Six of six real Ogg and Opus files tripped this before the guard; the
    genuine case -- several logical bitstreams -- is ogg_multistream's job."""
    from conftest import CORPUS_OGG as src
    fmt, chunks, warns = walk_file(src)
    found = anomalies.scan(src, fmt, chunks, warns)
    assert "embedded_standalone_media" not in _rules(found)


def test_cover_art_is_not_flagged(tmp_path):
    """Images are excluded on purpose. Art inside a tag is ordinary, and a rule
    that fires on every tagged file teaches people to ignore it."""
    png = b"\x89PNG\r\n\x1a\n" + bytes(400)
    lst = b"LIST" + struct.pack("<I", 4 + 8 + len(png)) + b"INFO" + \
          b"ICMT" + struct.pack("<I", len(png)) + png
    p = tmp_path / "art.wav"
    p.write_bytes(_wav_bytes(extra=lst))
    assert "embedded_standalone_media" not in _rules(_scan(p)[1])


# ── json_unknown_key / json_trailing_data ────────────────────────────────

_VITAL = {"author": "x", "comments": "", "macro1": "M1", "macro2": "M2",
          "macro3": "M3", "macro4": "M4", "preset_name": "p",
          "preset_style": "Bass", "synth_version": "1.0.7",
          "settings": {"osc_1_level": 1.0}}


def _vital(path, extra=None, trailing=b""):
    doc = dict(_VITAL)
    if extra:
        doc.update(extra)
    path.write_bytes(json.dumps(doc).encode("utf-8") + trailing)
    return path


def test_a_clean_preset_is_clean(tmp_path):
    _fmt, found = _scan(_vital(tmp_path / "ok.vital"))
    assert not ({"json_unknown_key", "json_trailing_data"} & _rules(found))


def test_bytes_after_the_closing_brace_are_found(tmp_path):
    """There is no length field and no padding in a JSON preset, so the bytes
    after the object are one of only two places to put something."""
    payload = b"\x00SECRET-PAYLOAD" * 40
    _fmt, found = _scan(_vital(tmp_path / "tail.vital", trailing=payload))
    assert "json_trailing_data" in _rules(found)
    hit = [f for f in found if f["rule"] == "json_trailing_data"][0]
    assert hit["severity"] == "alert"
    assert f"{len(payload):,}" in hit["message"]


def test_an_unknown_top_level_key_is_found(tmp_path):
    _fmt, found = _scan(_vital(tmp_path / "extra.vital",
                               extra={"__carrier": "payload"}))
    assert "json_unknown_key" in _rules(found)
    assert "__carrier" in " ".join(f["message"] for f in found)


def test_a_brace_inside_a_string_does_not_end_the_object(tmp_path):
    """Brace counting has to respect strings, or a preset whose comment
    contains a brace reports every byte after it as trailing data."""
    _fmt, found = _scan(_vital(tmp_path / "brace.vital",
                               extra={"comments": 'a } brace { in text'}))
    assert "json_trailing_data" not in _rules(found)


def test_a_value_that_looks_like_a_key_is_not_reported(tmp_path):
    """Only strings followed by a colon at depth 1 are keys."""
    _fmt, found = _scan(_vital(tmp_path / "val.vital",
                               extra={"comments": "preset_name"}))
    assert "json_unknown_key" not in _rules(found)


# ── the detector that shipped with no test at all ────────────────────────

def test_application_block_fires_on_a_flac_APPLICATION_block(tmp_path):
    """Shipped since it was written, referenced by no test. FLAC's APPLICATION
    block is freeform by spec, which is exactly what makes it a carrier."""
    from conftest import CORPUS_FLAC as src
    raw = open(src, "rb").read()
    payload = b"APPL" + b"\xde\xad\xbe\xef" * 64
    block = bytes([2]) + len(payload).to_bytes(3, "big") + payload   # type 2
    p = tmp_path / "app.flac"
    p.write_bytes(raw[:4] + block + raw[4:])

    _fmt, found = _scan(p)
    assert "application_block" in _rules(found), "the rule has never fired"
    hit = [f for f in found if f["rule"] == "application_block"][0]
    assert str(len(payload)) in hit["message"].replace(",", "")


def test_the_scan_is_windowed_not_slurped(tmp_path):
    """It read 16 MB into memory, which pushed audit's peak past the bound its
    memory test enforces -- and truncated at the cap without saying so, which
    is the defect this rule exists to help find.

    Planting the container past the old cap proves both: it is found, and the
    peak stays flat.
    """
    import tracemalloc

    inner = _wav_bytes(pcm=b"\x33\x44" * 300)
    p = tmp_path / "far.wav"
    filler = b"\x00" * (18 * 1024 * 1024)          # past the old 16 MB read
    junk = b"JUNK" + struct.pack("<I", len(filler) + len(inner)) + filler + inner
    p.write_bytes(_wav_bytes(extra=junk))

    tracemalloc.start()
    _fmt, found = _scan(p)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert "embedded_standalone_media" in _rules(found), "stopped at the old cap"
    assert peak < 8 * 1024 * 1024, f"peaked at {peak:,} bytes on a 18 MB file"


def test_a_container_straddling_a_window_boundary_is_still_found(tmp_path):
    """The overlap has to be as long as the magic plus the form bytes after it,
    or a match split across two reads is invisible."""
    inner = _wav_bytes(pcm=b"\x11\x22" * 200)
    for pad in (1 << 20, (1 << 20) - 2, (1 << 20) - 6, (1 << 20) - 11):
        filler = b"\x00" * pad
        junk = b"JUNK" + struct.pack("<I", len(filler) + len(inner)) + filler + inner
        p = tmp_path / f"edge{pad}.wav"
        p.write_bytes(_wav_bytes(extra=junk))
        assert "embedded_standalone_media" in _rules(_scan(p)[1]), f"missed at pad={pad}"


# ── nonprintable_text: where the finding says it is ──────────────────────

def test_a_nonprintable_text_finding_points_at_the_text(tmp_path):
    """The finding's offset is absolute, like every other finding's. It used
    to be the field's `off`, which is relative to its chunk's payload, so it
    pointed near the start of the file rather than at the tag."""
    text = b"ab\x01\x02cd\x00"
    info = b"INFO" + b"INAM" + struct.pack("<I", len(text)) + text
    extra = b"LIST" + struct.pack("<I", len(info)) + info
    p = tmp_path / "ctl.wav"
    data = _wav_bytes(extra=extra)
    p.write_bytes(data)
    _, findings = _scan(p)
    hits = [f for f in findings if f["rule"] == "nonprintable_text"]
    assert hits, "the rule did not fire"
    at = hits[0]["offset"]
    # the INAM sub-chunk: its id, size, then the text
    assert data[at:at + 4] == b"INAM", (at, data[at:at + 16])
    assert data[at + 8:at + 8 + len(text)] == text
