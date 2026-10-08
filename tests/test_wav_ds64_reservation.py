"""The 28-byte JUNK a WAV writer puts first, to become RF64 past 4 GB.

It was read as padding, and padding that is not zero was reported as
"the tail of something overwritten in place". Across 6,000 real WAVs every
non-zero one was a writer filling it on purpose: the ds64 sizes themselves
(sometimes stale, the file having grown after), a 28-character quote from
Ableton Live, or the RIFF size written over such a quote by a later tool."""
import struct

from acidcat.core.walk import walk_file

FMT = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
DATA = b"\x00\x01" * 32


def _wav(tmp_path, junk, tail=b""):
    body = (b"WAVE" + b"JUNK" + struct.pack("<I", len(junk)) + junk
            + b"fmt " + struct.pack("<I", 16) + FMT
            + b"data" + struct.pack("<I", len(DATA)) + DATA + tail)
    p = tmp_path / "r.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p), len(body)


def _junk(tmp_path, build):
    _p, riff = _wav(tmp_path, b"\x00" * 28)
    path, _r = _wav(tmp_path, build(riff))
    _l, chunks, _w = walk_file(path)
    return chunks[0]


def test_ds64_sizes_filled_in_are_named_not_flagged(tmp_path):
    j = _junk(tmp_path, lambda riff: struct.pack("<QQQI", riff, len(DATA), 32, 0))
    assert j["summary"] == "RF64 reservation (ds64), filled in with this file's sizes"
    assert not j["warnings"]
    assert {f["name"]: f["value"] for f in j["fields"]}["data_size"] == len(DATA)


def test_stale_ds64_sizes_say_the_file_grew(tmp_path):
    j = _junk(tmp_path, lambda riff: struct.pack("<QQQI", riff - 10, len(DATA), 32, 0))
    assert "filled in when the file was" in j["summary"]
    assert [w.code for w in j["warnings"]] == ["convention.noted"]


def test_a_quote_is_text_not_damage(tmp_path):
    j = _junk(tmp_path, lambda riff: b"Why r u using a hex editor? ")
    assert "holding text" in j["summary"] and "hex editor" in j["summary"]
    assert [w.code for w in j["warnings"]] == ["convention.noted"]


def test_riff_size_over_a_quote(tmp_path):
    j = _junk(tmp_path, lambda riff: struct.pack("<Q", riff) + b"rg is our ontology  ")
    assert "written over a text" in j["summary"]
    assert [w.code for w in j["warnings"]] == ["convention.noted"]


def test_other_bytes_are_still_flagged(tmp_path):
    """The control: a non-zero reservation that is none of the three is still
    flagged, as padding.nonzero: a suspicion (info), not damage."""
    j = _junk(tmp_path, lambda riff: bytes(range(1, 29)))
    assert [w.code for w in j["warnings"]] == ["padding.nonzero"]
    assert [w.kind for w in j["warnings"]] == ["info"]


def _audit(path, *extra):
    import subprocess
    import sys
    return subprocess.run([sys.executable, "-m", "acidcat", "audit", *extra, path],
                          capture_output=True, text=True)


def test_a_chunk_deleted_in_place_as_junk_is_named_and_not_damage(tmp_path):
    """A writer that renamed an old LIST/INFO to JUNK left a healthy file:
    audit reports it and exits 0 (it said damage, exit 1)."""
    j = _junk(tmp_path, lambda riff: b"INFOIPRD" + struct.pack("<I", 10)
              + b"Tape TR66\x00" + b"\x00" * 6)
    assert "a former LIST/INFO body" in str(j["warnings"][0])
    path, _r = _wav(tmp_path, b"INFOIPRD" + struct.pack("<I", 10)
                    + b"Tape TR66\x00" + b"\x00" * 6)
    r = _audit(path)
    assert r.returncode == 0, r.stdout
    assert "former LIST/INFO" in r.stdout


def test_a_walker_note_is_reported_at_its_registered_severity(tmp_path):
    """Every defect printed as warn whatever the registry said: a notice-level
    code now prints as notice."""
    import json
    from acidcat.core.infra.findings import REGISTRY
    path, _r = _wav(tmp_path, bytes(range(1, 29)))
    rows = json.loads(_audit(path, "--json").stdout)[0]["forensics"]
    pad = next(f for f in rows if f["code"] == "padding.nonzero")
    assert pad["severity"] == REGISTRY["padding.nonzero"][1] == "notice"


def test_the_quote_names_ableton_live_as_the_writer(tmp_path):
    from acidcat.core.forensics import provenance
    path, _r = _wav(tmp_path, b"The sleeper must awaken     ")
    label, chunks, _w = walk_file(path)
    with open(path, "rb") as fh:
        tools = [s["tool"] for s in provenance.identify(label, chunks, fh.read())]
    assert "Ableton Live" in tools
