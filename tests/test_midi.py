"""MIDI parser tests, focused on malformed-input safety."""

import struct

from acidcat.core.midi import parse_midi


def _build_smf(tracks, division=480):
    """Build a Standard MIDI File (format 1) with the given track bodies."""
    hdr = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), division)
    out = hdr
    for body in tracks:
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    return out


def test_parses_minimal_smf(tmp_path):
    """Sanity: a minimal one-track SMF with a tempo event parses cleanly."""
    # delta=0, FF 51 03 0B71B0 (500000us/beat = 120 BPM), then end-of-track
    track = (
        b"\x00\xFF\x51\x03\x07\xA1\x20"  # tempo 500000us
        b"\x00\xFF\x2F\x00"              # end of track
    )
    f = tmp_path / "ok.mid"
    f.write_bytes(_build_smf([track]))

    meta = parse_midi(str(f))
    assert meta["format"] == 1
    assert meta["tracks"] == 1
    assert meta["tempo_bpm"] == 120.0


def test_running_status_keeps_note_count_and_range(tmp_path):
    """B-1: running-status channel events (where the status byte is omitted
    and inherited from the previous channel event) must advance pos past
    both data bytes for 2-byte messages, not just one. Ableton, Logic, FL
    Studio and Reaper all emit running status for runs of consecutive
    note-on / note-off events.

    Track contents (one explicit note-on followed by two running-status
    note-ons, then EOT):

        delta=0, 0x90 0x3C 0x64  -- note on C3 (60) vel 100, status 0x90
        delta=0,      0x40 0x6E  -- running, note on E3 (64) vel 110
        delta=0,      0x43 0x78  -- running, note on G3 (67) vel 120
        delta=0, 0xFF 0x2F 0x00  -- end of track

    Expected after parse: 3 note-ons, lowest=60, highest=67. With the
    bug, pos advances one byte too few after each running-status event
    and the parser starts reading velocities as note numbers, so
    note_min collapses toward 0 and note_max blows past 67.
    """
    track = (
        b"\x00\x90\x3C\x64"  # note on C3 vel 100, status 0x90 captured
        b"\x00\x40\x6E"      # running: note on E3 vel 110
        b"\x00\x43\x78"      # running: note on G3 vel 120
        b"\x00\xFF\x2F\x00"  # end of track
    )
    f = tmp_path / "running_status.mid"
    f.write_bytes(_build_smf([track]))

    meta = parse_midi(str(f))
    assert meta["note_count"] == 3
    assert meta["note_min"] == 60
    assert meta["note_max"] == 67


def test_sysex_overlong_vlq_does_not_run_past_track(tmp_path):
    """F-06: a sysex event whose VLQ length points past the MTrk boundary
    must not advance pos into the next track's data. Parser should treat
    it as malformed and stop the inner loop, not silently scramble the
    next track's events.
    """
    # track 1: starts a sysex with an absurdly large length
    # delta=0, F0, then a 4-byte VLQ encoding a huge length (~70 MB)
    bad_track = b"\x00\xF0\xFF\xFF\xFF\x7F"  # F0 + VLQ for (1<<28)-1
    # track 2: a clean tempo + EOT. If the parser ran past track 1 into
    # track 2's body, it would mis-parse track 2 as sysex payload and we
    # would never see this tempo.
    good_track = (
        b"\x00\xFF\x51\x03\x06\xDD\xD0"  # tempo 450000us = ~133.33 BPM
        b"\x00\xFF\x2F\x00"
    )
    f = tmp_path / "sysex_overlong.mid"
    f.write_bytes(_build_smf([bad_track, good_track]))

    # the parse must not raise and must surface the legitimate tempo
    # from track 2 instead of getting lost in the malformed track 1.
    meta = parse_midi(str(f))
    assert meta["format"] == 1
    assert meta["tracks"] == 2
    assert meta["tempo_bpm"] is not None

def test_smpte_division_duration(tmp_path):
    """division with bit 15 set is SMPTE timing: the high byte is a
    negative two's-complement frame rate, the low byte is ticks per
    frame. wall time is ticks / (fps * tpf) and tempo does not enter
    into it. 0xE728 is -25 fps at 40 ticks/frame = 1000 ticks/second,
    so 2000 ticks must report 2.0 seconds. the ppqn formula fed the
    raw division (59176) in and produced near-zero durations.
    """
    track = (
        b"\x00\xFF\x51\x03\x07\xA1\x20"  # tempo 120 bpm, must not matter
        b"\x8F\x50\xFF\x2F\x00"          # delta 2000 ticks, end of track
    )
    f = tmp_path / "smpte.mid"
    f.write_bytes(_build_smf([track], division=0xE728))

    meta = parse_midi(str(f))
    assert meta["duration_sec"] == 2.0


def test_meta_event_cancels_running_status(tmp_path):
    """SMF 1.0: sysex and meta events cancel running status. a data
    byte that follows a meta event without a fresh status byte is
    malformed input and must not be decoded as a phantom note through
    the stale status.
    """
    track = (
        b"\x00\x90\x3C\x64"    # note on C4, establishes status 0x90
        b"\x00\xFF\x01\x01A"   # meta text event cancels running status
        b"\x00\x3E\x64"        # malformed: data bytes with no status
        b"\x00\xFF\x2F\x00"    # end of track
    )
    f = tmp_path / "rs_cancel.mid"
    f.write_bytes(_build_smf([track]))

    meta = parse_midi(str(f))
    assert meta["note_count"] == 1
    assert meta["note_max"] == 60


def _keysig_track(sf, mi):
    """Track with one key-signature meta event, then end-of-track."""
    return (b"\x00\xFF\x59\x02" + struct.pack(">bB", sf, mi)
            + b"\x00\xFF\x2F\x00")


def test_key_signature_major(tmp_path):
    """Major keys name the signature's major root directly."""
    cases = {0: "C", 1: "G", 4: "E", 7: "C#", -1: "F", -3: "Eb", -7: "Cb"}
    for sf, want in cases.items():
        p = tmp_path / f"k{sf}.mid"
        p.write_bytes(_build_smf([_keysig_track(sf, 0)]))
        assert parse_midi(str(p))["key_sig"] == want, f"sf={sf}"


def test_key_signature_minor_is_relative_minor(tmp_path):
    """mi=1 names the RELATIVE minor (major root + 9 semitones), not the
    major root with an 'm' suffix. sf=0 mi=1 is A minor, not C minor."""
    cases = {0: "Am", 1: "Em", 2: "Bm", 3: "F#m", 7: "A#m",
             -1: "Dm", -3: "Cm", -5: "Bbm", -7: "Abm"}
    for sf, want in cases.items():
        p = tmp_path / f"k{sf}.mid"
        p.write_bytes(_build_smf([_keysig_track(sf, 1)]))
        assert parse_midi(str(p))["key_sig"] == want, f"sf={sf}"


def test_walker_key_sig_matches_parser(tmp_path):
    """The inspect walker now resolves the same real key name as the legacy
    parser (they used to disagree: the walker showed '+2 sharps'). Both call
    the shared key_signature_name resolver."""
    from acidcat.core.walk.midi import inspect_midi
    for sf, mi, want in [(2, 0, "D"), (0, 1, "Am"), (-3, 0, "Eb"), (7, 1, "A#m")]:
        p = tmp_path / f"k{sf}_{mi}.mid"
        p.write_bytes(_build_smf([_keysig_track(sf, mi)]))
        chunks, _ = inspect_midi(str(p))
        trk = next(c for c in chunks if c["id"] == "MTrk")
        ks = next(f for f in trk["fields"] if f["name"] == "key_sig")
        assert ks["value"] == want == parse_midi(str(p))["key_sig"]
        # the raw signature rides the deep event listing's detail
        chunks_deep, _ = inspect_midi(str(p), deep=True)
        trk_deep = next(c for c in chunks_deep if c["id"] == "MTrk")
        ksig_row = next(r for r in trk_deep["rows"] if "key" in r["event"])
        assert "sharps" in ksig_row["detail"] or "flats" in ksig_row["detail"]


def test_midi_scan_row_from_walker(tmp_path):
    """The scan row comes from the walker's ctx since the unification; a
    tempo + key + track name + copyright track round-trips into the row."""
    from acidcat.core.indexing import _from_midi
    track = (b"\x00\xFF\x51\x03\x07\xA1\x20"      # tempo 120 bpm
             b"\x00\xFF\x03\x04Bass"              # track name
             b"\x00\xFF\x02\x03(c)"               # copyright
             b"\x00\xFF\x59\x02\x00\x01"          # key sig: A minor
             b"\x87\x40\x90\x3C\x64"              # note at tick 960 (2 beats)
             b"\x00\xFF\x2F\x00")
    p = tmp_path / "loop.mid"
    p.write_bytes(_build_smf([track], division=480))
    row = _from_midi(str(p), {})
    assert row["format"] == "midi"
    assert row["bpm"] == 120.0
    assert row["duration"] == 1.0                 # 960 ticks / 480 at 120 bpm
    assert row["key"] == "Am"
    assert row["title"] == "Bass"
    assert row["comment"] == "(c)"


def test_midi_scan_duration_null_without_tempo(tmp_path):
    """A PPQ file with no tempo event stores no scan duration (a default-120
    estimate would be a wrong number to filter on); the inspect view still
    shows the labeled estimate."""
    from acidcat.core.indexing import _from_midi
    from acidcat.core.walk.midi import inspect_midi
    track = b"\x87\x40\x90\x3C\x64\x00\xFF\x2F\x00"   # note, no tempo
    p = tmp_path / "notempo.mid"
    p.write_bytes(_build_smf([track], division=480))
    assert _from_midi(str(p), {})["duration"] is None
    chunks, _ = inspect_midi(str(p))
    dur = next(f for f in chunks[0]["fields"] if f["name"] == "duration")
    assert "default" in dur["note"]                   # labeled in the view


def test_whole_file_read_is_capped(tmp_path, monkeypatch):
    """a forged multi-GB .mid must not be slurped whole (DoS). the cap
    is shrunk for the test; header metadata still parses."""
    import acidcat.core.midi as midimod
    monkeypatch.setattr(midimod, "MAX_SMF_BYTES", 64)
    track = (b"\x00\xFF\x51\x03\x07\xA1\x20" + b"\x00\x90\x3C\x64" * 100
             + b"\x00\xFF\x2F\x00")
    p = tmp_path / "big.mid"
    p.write_bytes(_build_smf([track]))
    meta = parse_midi(str(p))  # must not read past the cap or raise
    assert meta["format"] == 1
    assert meta["tempo_bpm"] == 120.0


def test_inspect_midi_short_mthd_degrades(tmp_path):
    """a file cut off inside the MThd header (reachable via a truncated RMID
    data chunk or a direct call) degrades to a warning, not a struct.error."""
    from acidcat.core.walk.midi import inspect_midi
    f = tmp_path / "cut.mid"
    f.write_bytes(b"MThd\x00\x00\x00\x06")   # 8 bytes; a full header needs 14
    chunks, warns = inspect_midi(str(f))
    assert chunks == []
    assert any("MThd" in w for w in warns)


def test_sysex_noncommercial_id_flagged_as_cavity(tmp_path):
    """A SysEx event with the 0x7D non-commercial manufacturer id (no synth acts
    on it) is a payload-cavity tell; the walker warns."""
    from acidcat.core.walk.midi import inspect_midi
    body = bytes([0x7D]) + b"hidden-payload-bytes-nobody-plays"
    sysex = bytes([0x00, 0xF0]) + bytes([len(body) + 1]) + body + bytes([0xF7])
    track = (bytes([0x00, 0x90, 60, 64, 0x60, 0x80, 60, 0])
             + sysex + bytes([0x00, 0xFF, 0x2F, 0x00]))
    f = tmp_path / "sysex.mid"
    f.write_bytes(_build_smf([track]))
    chunks, warns = inspect_midi(str(f))
    all_warns = list(warns) + [w for c in chunks for w in c.get("warnings", [])]
    assert any("non-commercial" in w and "cavity" in w for w in all_warns)
