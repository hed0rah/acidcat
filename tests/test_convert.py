"""tests for the DAW-clip -> MIDI writer and convert command."""

import struct
from acidcat.core.midi_write import notes_to_smf, _vlq


def _note_ons(smf):
    """(count, pitches) of note-on events in a type-0 SMF."""
    assert smf[:4] == b"MThd"
    pos = 8 + struct.unpack_from(">I", smf, 4)[0]
    assert smf[pos:pos + 4] == b"MTrk"
    tlen = struct.unpack_from(">I", smf, pos + 4)[0]
    i, end, st = pos + 8, pos + 8 + tlen, 0
    pitches = []

    def vlq(b, i):
        v = 0
        while True:
            c = b[i]; i += 1; v = (v << 7) | (c & 0x7F)
            if not c & 0x80:
                return v, i
    while i < end:
        _, i = vlq(smf, i)
        b = smf[i]
        if b & 0x80:
            st = b; i += 1
        ev = st & 0xF0
        if ev in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
            d1, d2 = smf[i], smf[i + 1]; i += 2
            if ev == 0x90 and d2 > 0:
                pitches.append(d1)
        elif ev in (0xC0, 0xD0):
            i += 1
        elif st == 0xFF:
            i += 1; ln, i = vlq(smf, i); i += ln
        else:
            i += 1
    return len(pitches), pitches


def test_vlq():
    assert _vlq(0) == b"\x00"
    assert _vlq(127) == b"\x7f"
    assert _vlq(128) == b"\x81\x00"


def test_notes_to_smf_roundtrip():
    notes = [{"pitch": 60, "start": 0.0, "duration": 1.0, "velocity": 100 / 127},
             {"pitch": 67, "start": 1.0, "duration": 0.5, "velocity": 80 / 127}]
    smf = notes_to_smf(notes, bpm=120, division=480)
    count, pitches = _note_ons(smf)
    assert count == 2 and sorted(pitches) == [60, 67]


def test_notes_to_smf_skips_bad_pitch():
    smf = notes_to_smf([{"pitch": None, "start": 0, "duration": 1}], bpm=120)
    count, _ = _note_ons(smf)
    assert count == 0
