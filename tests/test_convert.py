"""tests for the DAW-clip -> MIDI writer and convert command."""

import io
import struct
import types
import wave

from acidcat.commands import convert
from acidcat.core.codecs import g711
from acidcat.core.write.midi_write import notes_to_smf, _vlq


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


# ---- Sun/NeXT .au -> WAV ----------------------------------------------------

def _au(encoding, rate, ch, body, data_size=None, data_offset=24):
    ds = len(body) if data_size is None else data_size
    return b".snd" + struct.pack(">IIIII", data_offset, ds, encoding, rate, ch) + body


def _au_args(inp, out):
    return types.SimpleNamespace(input=inp, output=out, force=False, to_pcm=False,
                                 codec=None, division=480, skip_existing=False,
                                 quiet=False)


def _wav_info(b):
    w = wave.open(io.BytesIO(b))
    return (w.getnchannels(), w.getsampwidth(), w.getframerate(),
            w.readframes(w.getnframes()))


def test_convert_au_mulaw_decodes_through_g711(tmp_path):
    body = bytes([0xFF, 0x80, 0x00, 0x7F])
    src = tmp_path / "a.au"; src.write_bytes(_au(1, 8000, 1, body))
    out = tmp_path / "a.wav"
    assert convert.run(_au_args(str(src), str(out))) == 0 and out.exists()
    ch, sw, fr, frames = _wav_info(out.read_bytes())
    assert (ch, sw, fr) == (1, 2, 8000)
    assert frames == g711.decode_ulaw(body)


def test_convert_au_linear16_byteswaps_to_le(tmp_path):
    body = struct.pack(">2h", 0x0102, 0x7FFF)          # big-endian samples
    src = tmp_path / "b.au"; src.write_bytes(_au(3, 22050, 1, body))
    out = tmp_path / "b.wav"
    assert convert.run(_au_args(str(src), str(out))) == 0
    _ch, _sw, _fr, frames = _wav_info(out.read_bytes())
    assert struct.unpack("<2h", frames) == (0x0102, 0x7FFF)


def test_convert_au_streaming_sentinel_takes_rest_of_file(tmp_path):
    body = b"\x7f" * 10
    src = tmp_path / "c.au"; src.write_bytes(_au(1, 8000, 1, body, data_size=0xFFFFFFFF))
    out = tmp_path / "c.wav"
    assert convert.run(_au_args(str(src), str(out))) == 0
    _ch, _sw, _fr, frames = _wav_info(out.read_bytes())
    assert len(frames) == 20                           # 10 samples, 16-bit


def test_convert_au_unsupported_encoding_is_refused(tmp_path):
    # no converter for this encoding is could-not-run (2), as the contract
    # says of a file a verb has no converter for; it said 1
    src = tmp_path / "d.au"; src.write_bytes(_au(6, 44100, 1, b"\x00" * 8))  # 32-bit float
    out = tmp_path / "d.wav"
    assert convert.run(_au_args(str(src), str(out))) == 2
    assert not out.exists()


def test_convert_to_an_unwritable_output_is_2_not_a_traceback(tmp_path, capsys):
    src = tmp_path / "e.au"; src.write_bytes(_au(1, 8000, 1, b"\xff" * 64))
    out = tmp_path / "taken.wav"
    out.mkdir()                                  # a folder where the file goes
    assert convert.run(_au_args(str(src), str(out))) == 2
    err = capsys.readouterr().err
    assert "taken.wav" in err and "Traceback" not in err
