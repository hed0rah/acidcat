"""MIDI parser tests, focused on malformed-input safety."""

import struct

from acidcat.core.midi import parse_midi


def _build_smf(tracks):
    """Build a Standard MIDI File (format 1) with the given track bodies."""
    hdr = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), 480)
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
