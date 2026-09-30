"""Tests for core.ump (Universal MIDI Packet) and the .midi2 clip-file walker.
The byte layouts are the spec's own worked examples: a MIDI 2.0 Note On is
0x40903C00 0x80000000, DCTPQ carries a 16-bit ticks-per-quarter, and a clip is
an 8-byte SMF2CLIP magic then a self-delimiting big-endian UMP stream."""
import struct

from acidcat.core.formats import ump


def _w(*words):
    return b"".join(struct.pack(">I", x) for x in words)


def _dcs(ticks):
    return _w(0x00400000 | (ticks & 0xFFFFF))          # Delta Clockstamp utility message


def _clip():
    """The spec's minimal clip plus a tempo, a 4/4 time-sig, and a note pair."""
    return (b"SMF2CLIP"
            + _dcs(0) + _w(0x003001E0)                 # DCTPQ 480
            + _dcs(0) + _w(0xD0100000, 0x02FAF080, 0, 0)   # Set Tempo 120 BPM
            + _dcs(0) + _w(0xD0100001, 0x04020800, 0, 0)   # 4/4, 8 32nds per quarter
            #                                              (M2-104-UM 7.5.4)
            + _dcs(0) + _w(0xF0200000, 0, 0, 0)        # Start of Clip
            + _dcs(0) + _w(0x40903C00, 0x80000000)     # Note On note 60 vel 0x8000
            + _dcs(480) + _w(0x40803C00, 0)            # Note Off a quarter later
            + _dcs(0) + _w(0xF0210000, 0, 0, 0))       # End of Clip


def test_ump_note_on():
    _, _, m = next(ump.iter_ump(bytes.fromhex("40903C0080000000")))
    assert m["kind"] == "note_on" and m["group"] == 0 and m["channel"] == 0
    assert m["note"] == 60 and m["velocity"] == 0x8000 and m["attr_type"] == 0


def test_ump_utility_and_stream():
    assert ump.decode((0x003001E0,)) == {"mt": 0, "kind": "dctpq", "value": 480}
    assert ump.decode((0x00400000,))["ticks"] == 0
    assert ump.decode((0x00400000 | 480,))["ticks"] == 480
    assert ump.decode((0xF0200000, 0, 0, 0))["kind"] == "start_of_clip"
    assert ump.decode((0xF0210000, 0, 0, 0))["kind"] == "end_of_clip"
    t = ump.decode((0xD0100000, 0x02FAF080, 0, 0))     # Set Tempo -> 120 BPM
    assert t["kind"] == "set_tempo" and round(t["bpm"]) == 120


def test_ump_stream_statuses_follow_the_spec_table():
    # M2-104-UM v1.1.2 Appendix F. 0x05 and 0x10 were once swapped here.
    want = {0x00: "endpoint_discovery", 0x01: "endpoint_info", 0x02: "device_identity",
            0x03: "endpoint_name", 0x04: "product_instance_id",
            0x05: "stream_config_request", 0x06: "stream_config_notification",
            0x10: "function_block_discovery", 0x11: "function_block_info",
            0x12: "function_block_name", 0x20: "start_of_clip", 0x21: "end_of_clip"}
    for status, kind in want.items():
        assert ump.decode((0xF0000000 | status << 16, 0, 0, 0))["kind"] == kind


def test_ump_poly_pressure_per_note_management_and_mixed_data_set():
    m = ump.decode((0x40A03C00, 0x12345678))
    assert (m["kind"], m["note"], m["data"]) == ("poly_pressure", 60, 0x12345678)
    m = ump.decode((0x40F03C03, 0))
    assert (m["kind"], m["note"], m["options"]) == ("per_note_management", 60, 3)
    assert ump.decode((0x50800000, 0, 0, 0))["status"] == "mixed_data_set_header"
    assert ump.decode((0x50900000, 0, 0, 0))["status"] == "mixed_data_set_payload"
    # SysEx7 has no Mixed Data Set: the same nibble is not a known status there
    assert ump.decode((0x30800000, 0))["status"] == "?"


def _clip_with(body):
    return (b"SMF2CLIP" + _dcs(0) + _w(0x003001E0) + _dcs(0) + _w(0xF0200000, 0, 0, 0)
            + body + _dcs(0) + _w(0xF0210000, 0, 0, 0))


def _walk_bytes(tmp_path, data):
    from acidcat.core.walk import walk_file
    p = tmp_path / "c.midi2"
    p.write_bytes(data)
    _l, chunks, warns = walk_file(str(p))
    codes = [getattr(w, "code", None) for w in list(warns)
             + [w for c in chunks for w in c.get("warnings", [])]]
    return chunks, codes


def test_midi2_reports_the_opening_tempo_and_counts_changes(tmp_path):
    # a real tempo track (Holst's Mars) changes tempo 94 times; the clip opens
    # at the FIRST one, which is what the summary must say
    body = (_dcs(0) + _w(0xD0100000, 0x02FAF080, 0, 0)           # 120 BPM
            + _dcs(480) + _w(0xD0100000, 0x0BEBC200, 0, 0))      # 30 BPM
    chunks, codes = _walk_bytes(tmp_path, _clip_with(body))
    assert "120 BPM (+1 changes)" in chunks[1]["summary"]
    assert not any(f["name"] == "duration" for f in chunks[1]["fields"])
    assert not codes


def test_midi2_joins_text_split_across_packets(tmp_path):
    # 12 text bytes per UMP: form 1 starts, 2 continues, 3 ends
    def part(form, text):
        return _w(0xD0000000 | form << 22 | 1 << 20 | 0x0103) + text.ljust(12, b"\x00")
    body = (_dcs(0) + part(1, b"acidcat MIDI") + _dcs(0) + part(2, b" 2.0 feature")
            + _dcs(0) + part(3, b" tour"))
    chunks, codes = _walk_bytes(tmp_path, _clip_with(body))
    meta = [f["value"] for f in chunks[1]["fields"] if f["name"] == "meta"]
    assert meta == ["acidcat MIDI 2.0 feature tour"] and not codes


def test_midi2_flags_sysex_out_of_order(tmp_path):
    cont = _w(0x30220000, 0)                                     # SysEx7 continue, no start
    chunks, codes = _walk_bytes(tmp_path, _clip_with(_dcs(0) + cont))
    assert "chunk.order" in codes
    ok = _w(0x30120000, 0) + _dcs(0) + _w(0x30320000, 0)         # start then end
    chunks, codes = _walk_bytes(tmp_path, _clip_with(_dcs(0) + ok))
    assert not codes


def test_ump_self_delimiting_walk():
    # every message length comes only from the MT nibble; a mixed stream stays aligned
    kinds = [m["kind"] for _, _, m in ump.iter_ump(_clip()[8:])]
    assert kinds == ["delta_clockstamp", "dctpq", "delta_clockstamp", "set_tempo",
                     "delta_clockstamp", "set_time_signature", "delta_clockstamp",
                     "start_of_clip", "delta_clockstamp", "note_on",
                     "delta_clockstamp", "note_off", "delta_clockstamp", "end_of_clip"]


def test_ump_truncated_tail_stops_clean():
    # a 2-word MT 0x4 packet with only 1 word present must not be yielded
    out = list(ump.iter_ump(_w(0x40903C00)))           # only word 1 of a note-on
    assert out == []


def test_midi2_walker(tmp_path):
    from acidcat.core.infra import sniff
    from acidcat.core.walk import walk_file

    f = tmp_path / "clip.midi2"
    f.write_bytes(_clip())
    assert sniff.sniff(str(f)) == "midi2"

    label, chunks, warns = walk_file(str(f))
    assert "MIDI Clip File" in label and warns == []
    header, clip = chunks
    assert header["id"] == "SMF2CLIP" and header["fields"][0]["value"] == "SMF2CLIP"
    vals = {fld["name"]: fld["value"] for fld in clip["fields"]}
    assert vals["resolution"] == "480 ticks/quarter"
    assert vals["time_signature"] == "4/4"
    assert vals["duration"] == "0.50 s"                # 480 ticks @ 480 TPQ, 120 BPM = 1 quarter
    assert "120 BPM" in clip["summary"]


def test_midi2_deep_rows(tmp_path):
    from acidcat.core.walk import walk_file
    f = tmp_path / "clip.midi2"
    f.write_bytes(_clip())
    _, chunks, _ = walk_file(str(f), deep=True)
    rows = chunks[1]["rows"]
    notes = [r for r in rows if r["event"] in ("note_on", "note_off")]
    assert len(notes) == 2
    assert notes[0]["tick"] == 0 and notes[1]["tick"] == 480   # note off a quarter later


def test_midi2_warns_on_missing_markers(tmp_path):
    from acidcat.core.walk import walk_file
    # magic + a lone note, no DCTPQ, no start/end of clip
    f = tmp_path / "bad.midi2"
    f.write_bytes(b"SMF2CLIP" + _w(0x40903C00, 0x80000000))
    _, chunks, _ = walk_file(str(f))
    w = " ".join(chunks[1]["warnings"])
    assert "DCTPQ" in w and "Start of Clip" in w and "End of Clip" in w
