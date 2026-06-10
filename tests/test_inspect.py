"""tests for acidcat.commands.inspect."""

import struct
from types import SimpleNamespace

import pytest
from acidcat.commands.inspect import inspect_wav, run


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
