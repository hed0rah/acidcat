"""tests for acidcat.commands.inspect."""

import struct
from types import SimpleNamespace

import pytest
from acidcat.commands.inspect import inspect_aiff, inspect_midi, inspect_wav, run


def _wav(tmp_path, *chunks, riff_size=None, name="t.wav"):
    body = b"WAVE" + b"".join(chunks)
    size = riff_size if riff_size is not None else len(body)
    p = tmp_path / name
    p.write_bytes(b"RIFF" + struct.pack("<I", size) + body)
    return str(p)


def _chunk(cid, payload):
    raw = cid + struct.pack("<I", len(payload)) + payload
    return raw + (b"\x00" if len(payload) % 2 else b"")


def _fmt(channels=1, rate=44100, bits=16):
    align = channels * bits // 8
    return _chunk(b"fmt ", struct.pack(
        "<HHIIHH", 1, channels, rate, rate * align, align, bits))


def _data(n_frames=441, align=2):
    return _chunk(b"data", b"\x00" * (n_frames * align))


def _acid(beats=8, tempo=120.0, root=60, flags=0x02):
    return _chunk(b"acid", struct.pack(
        "<IHHfIHHf", flags, root, 0x8000, 0.0, beats, 4, 4, tempo))


def _smpl(unity=60, loops=()):
    payload = struct.pack("<IIIIIiiII", 0, 0, 22675, unity, 0, 0, 0, len(loops), 0)
    for i, (start, end) in enumerate(loops):
        payload += struct.pack("<IIIIII", i, 0, start, end, 0, 0)
    return _chunk(b"smpl", payload)


class TestInspectWav:
    def test_chunk_table_and_summaries(self, tmp_path):
        path = _wav(tmp_path, _fmt(), _data(), _acid())
        chunks, warns = inspect_wav(path)
        ids = [c["id"] for c in chunks]
        assert ids == ["fmt ", "data", "acid"]
        assert "PCM 16-bit 1ch 44100 Hz" in chunks[0]["summary"]
        assert "8 beats" in chunks[2]["summary"]
        assert warns == []

    def test_acid_fields_decoded(self, tmp_path):
        path = _wav(tmp_path, _fmt(), _data(), _acid(beats=15, tempo=122.0))
        chunks, _ = inspect_wav(path)
        acid = next(c for c in chunks if c["id"] == "acid")
        by_name = {f["name"]: f["value"] for f in acid["fields"]}
        assert by_name["num_beats"] == 15
        assert by_name["tempo"] == 122.0
        assert by_name["meter_denominator"] == 4

    def test_riff_size_lie_is_flagged(self, tmp_path):
        path = _wav(tmp_path, _fmt(), _data(), riff_size=9999)
        _, warns = inspect_wav(path)
        assert any("riff_size" in w for w in warns)

    def test_smpl_loop_past_eof_is_flagged(self, tmp_path):
        # 441 frames of audio, loop end claims frame 100000
        path = _wav(tmp_path, _fmt(), _data(441), _smpl(loops=[(0, 100000)]))
        chunks, _ = inspect_wav(path)
        smpl = next(c for c in chunks if c["id"] == "smpl")
        assert any("past last frame" in w for w in smpl["warnings"])

    def test_acid_duration_drift_is_flagged(self, tmp_path):
        # 441 frames = 0.01 s, but acid claims 16 beats at 120 bpm = 8 s
        path = _wav(tmp_path, _fmt(), _data(441), _acid(beats=16, tempo=120.0))
        chunks, _ = inspect_wav(path)
        acid = next(c for c in chunks if c["id"] == "acid")
        assert any("drift" in w for w in acid["warnings"])

    def test_missing_fmt_is_flagged(self, tmp_path):
        path = _wav(tmp_path, _data())
        _, warns = inspect_wav(path)
        assert any("no fmt chunk" in w for w in warns)


RATE_44100 = bytes.fromhex("400eac440000000000000000")[:10]


def _aiff_chunk(cid, payload):
    raw = cid + struct.pack(">I", len(payload)) + payload
    if len(payload) % 2:
        raw += b"\x00"
    return raw


def _aiff(tmp_path, *chunks, form=b"AIFF", name="t.aiff"):
    body = form + b"".join(chunks)
    p = tmp_path / name
    p.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    return str(p)


def _comm(channels=1, frames=4, bits=16):
    return _aiff_chunk(b"COMM", struct.pack(">hIh", channels, frames, bits) + RATE_44100)


def _ssnd(frames=4, channels=1, bits=16):
    return _aiff_chunk(b"SSND", struct.pack(">II", 0, 0)
                       + b"\x00" * (frames * channels * bits // 8))


def _mark(markers):
    payload = struct.pack(">H", len(markers))
    for mid, position, mname in markers:
        pstr = bytes([len(mname)]) + mname
        if (1 + len(mname)) % 2:
            pstr += b"\x00"
        payload += struct.pack(">hI", mid, position) + pstr
    return _aiff_chunk(b"MARK", payload)


def _inst(sustain=(1, 1, 2)):
    mode, begin, end = sustain
    return _aiff_chunk(b"INST", struct.pack(
        ">bbBBBBhhhhhhh", 60, 0, 0, 127, 0, 127, 0,
        mode, begin, end, 0, 0, 0))


class TestInspectAiff:
    def test_comm_decoded(self, tmp_path):
        path = _aiff(tmp_path, _comm(channels=2, frames=441), _ssnd(441, 2))
        chunks, warns = inspect_aiff(path, "AIFF")
        comm = next(c for c in chunks if c["id"] == "COMM")
        by_name = {f["name"]: f["value"] for f in comm["fields"]}
        assert by_name["num_channels"] == 2
        assert by_name["sample_rate"] == 44100
        assert warns == []

    def test_ssnd_frame_mismatch_flagged(self, tmp_path):
        # COMM declares 441 frames but SSND only holds 4
        path = _aiff(tmp_path, _comm(frames=441), _ssnd(frames=4))
        chunks, _ = inspect_aiff(path, "AIFF")
        ssnd = next(c for c in chunks if c["id"] == "SSND")
        assert any("COMM frames" in w for w in ssnd["warnings"])

    def test_markers_and_inst_loops(self, tmp_path):
        path = _aiff(tmp_path, _comm(),
                     _mark([(1, 0, b"start"), (2, 4, b"end")]),
                     _inst(sustain=(1, 1, 2)), _ssnd())
        chunks, warns = inspect_aiff(path, "AIFF")
        mark = next(c for c in chunks if c["id"] == "MARK")
        assert "2 marker(s)" in mark["summary"]
        assert warns == []

    def test_inst_loop_dangling_marker_flagged(self, tmp_path):
        path = _aiff(tmp_path, _comm(), _mark([(1, 0, b"a")]),
                     _inst(sustain=(1, 1, 9)), _ssnd())
        _, warns = inspect_aiff(path, "AIFF")
        assert any("marker id 9" in w for w in warns)


def _smf(tmp_path, tracks, division=480, ntrks=None, name="t.mid"):
    hdr = b"MThd" + struct.pack(">IHHH", 6, 1,
                                ntrks if ntrks is not None else len(tracks),
                                division)
    out = hdr
    for body in tracks:
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    p = tmp_path / name
    p.write_bytes(out)
    return str(p)


_TRACK = (
    b"\x00\xFF\x03\x04Bass"            # track name
    b"\x00\xFF\x51\x03\x07\xA1\x20"    # tempo 120
    b"\x00\x90\x3C\x64"                # note on C4
    b"\x00\x40\x6E"                    # running status note on E4
    b"\x00\xFF\x2F\x00"                # end of track
)


class TestInspectMidi:
    def test_header_and_track_stats(self, tmp_path):
        path = _smf(tmp_path, [_TRACK])
        chunks, warns = inspect_midi(path)
        assert [c["id"] for c in chunks] == ["MThd", "MTrk"]
        mthd = {f["name"]: f["value"] for f in chunks[0]["fields"]}
        assert mthd["division"] == 480
        trk = {f["name"]: f["value"] for f in chunks[1]["fields"]}
        assert trk["name"] == "Bass"
        assert trk["notes"] == 2
        assert warns == []

    def test_smpte_division_decoded(self, tmp_path):
        path = _smf(tmp_path, [_TRACK], division=0xE728)
        chunks, _ = inspect_midi(path)
        div = next(f for f in chunks[0]["fields"] if f["name"] == "division")
        assert "SMPTE" in div["note"]
        assert "25" in div["note"]

    def test_missing_eot_flagged(self, tmp_path):
        path = _smf(tmp_path, [b"\x00\x90\x3C\x64"])
        chunks, _ = inspect_midi(path)
        assert any("end-of-track" in w for w in chunks[1]["warnings"])

    def test_declared_tracks_missing_flagged(self, tmp_path):
        path = _smf(tmp_path, [_TRACK], ntrks=3)
        _, warns = inspect_midi(path)
        assert any("declares 3 tracks, found 1" in w for w in warns)

    def test_no_tempo_warns(self, tmp_path):
        path = _smf(tmp_path, [b"\x00\x90\x3C\x64\x00\xFF\x2F\x00"])
        _, warns = inspect_midi(path)
        assert any("120 bpm" in w for w in warns)


class TestRunCli:
    def _args(self, target, **kw):
        base = dict(target=target, show_hex=False, format="table",
                    quiet=False, verbose=False)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_table_output(self, tmp_path, capsys):
        path = _wav(tmp_path, _fmt(), _data(), _acid())
        assert run(self._args(path)) == 0
        out = capsys.readouterr().out
        assert "RIFF/WAVE" in out
        assert "acid @" in out
        assert "num_beats" in out

    def test_hex_column(self, tmp_path, capsys):
        path = _wav(tmp_path, _fmt(), _data())
        assert run(self._args(path, show_hex=True)) == 0
        out = capsys.readouterr().out
        assert "44 ac 00 00" in out  # 44100 little-endian

    def test_json_output(self, tmp_path, capsys):
        import json
        path = _wav(tmp_path, _fmt(), _data())
        assert run(self._args(path, format="json")) == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["format"] == "RIFF/WAVE"
        assert [c["id"] for c in doc["chunks"]] == ["fmt ", "data"]

    def test_not_riff_exits_1(self, tmp_path, capsys):
        p = tmp_path / "x.bin"
        p.write_bytes(b"\x00" * 64)
        assert run(self._args(str(p))) == 1

    def test_missing_file_exits_1(self):
        assert run(self._args("does/not/exist.wav")) == 1
