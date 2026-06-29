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

    def test_overrun_data_size_not_trusted(self, tmp_path):
        # ANI bug class (CVE-2007-0038): a chunk declares far more than the
        # file holds. inspect must lint the overrun AND derive frames/duration
        # from the bytes actually present, never from the lying size field.
        big_data = b"data" + struct.pack("<I", 0x7FFFFFFF) + b"\x00" * 8
        path = _wav(tmp_path, _fmt(), big_data)
        chunks, warns = inspect_wav(path)
        assert any("only 8 remain" in w for w in warns)
        data = next(c for c in chunks if c["id"] == "data")
        assert "declared" in data["summary"]
        frames = next(f for f in data["fields"] if f["name"] == "frames")
        assert frames["value"] == 4  # 8 bytes present / 2-byte align, not ~1e9

    def test_adpcm_duration_uses_fact_not_block_align(self, tmp_path):
        # ADPCM block_align is a block size (many samples/block), so
        # bytes/align gives a near-zero bogus duration; the fact chunk's
        # sample count is authoritative and must win.
        fmt = _chunk(b"fmt ", struct.pack(
            "<HHIIHH", 0x0011, 1, 44100, 11100, 1024, 4))
        fact = _chunk(b"fact", struct.pack("<I", 44100))  # 1.0 s of samples
        data = _chunk(b"data", b"\x00" * 2048)            # only 2 blocks
        path = _wav(tmp_path, fmt, fact, data)
        chunks, _ = inspect_wav(path)
        d = next(c for c in chunks if c["id"] == "data")
        frames = next(f for f in d["fields"] if f["name"] == "frames")
        assert frames["value"] == 44100
        assert "1.000 s" in d["summary"]

    def test_adpcm_avg_bytes_not_linted(self, tmp_path):
        # avg_bytes_per_sec == sample_rate*block_align is a PCM-only identity.
        # ADPCM (tag 0x0011) legitimately breaks it; the lint must stay quiet.
        fmt = _chunk(b"fmt ", struct.pack(
            "<HHIIHH", 0x0011, 2, 44100, 11100, 1024, 4))
        path = _wav(tmp_path, fmt, _data())
        _, warns = inspect_wav(path)
        assert not any("avg_bytes_per_sec" in w for w in warns)


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

    def test_overrun_ssnd_size_not_trusted(self, tmp_path):
        # SSND declares 0x7fffffff bytes; the file holds 8. payload size must
        # be derived from the bytes present, not the lying chunk size.
        big_ssnd = (b"SSND" + struct.pack(">I", 0x7FFFFFFF)
                    + struct.pack(">II", 0, 0) + b"\x00" * 8)
        path = _aiff(tmp_path, _comm(frames=4), big_ssnd)
        chunks, warns = inspect_aiff(path, "AIFF")
        assert any("remain" in w for w in warns)
        ssnd = next(c for c in chunks if c["id"] == "SSND")
        assert "overruns" in ssnd["summary"]
        assert "2,147,483" not in ssnd["summary"]  # not the declared 2GB


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

    def test_deep_event_listing(self, tmp_path):
        path = _smf(tmp_path, [_TRACK])
        chunks, _ = inspect_midi(path, deep=True)
        trk = next(c for c in chunks if c["id"] == "MTrk")
        assert "rows" in trk
        kinds = [r["event"] for r in trk["rows"]]
        assert "meta track name" in kinds
        assert "meta tempo" in kinds
        assert "note on" in kinds
        assert "meta end of track" in kinds
        note_on = next(r for r in trk["rows"] if r["event"] == "note on")
        assert "C4" in note_on["detail"]

    def test_default_midi_has_no_rows(self, tmp_path):
        path = _smf(tmp_path, [_TRACK])
        chunks, _ = inspect_midi(path)
        assert all("rows" not in c for c in chunks)


class TestInspectRf64:
    def _rf64(self, tmp_path, data_bytes=8, sentinel_ok=True):
        from acidcat.commands.inspect import inspect_rf64  # noqa: F401
        fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
        fmt_chunk = b"fmt " + struct.pack("<I", 16) + fmt
        data_chunk = b"data" + struct.pack("<I", 0xFFFFFFFF) + b"\x00" * data_bytes
        body_after_hdr = b"WAVE"
        # ds64 sizes: riff_size = file - 8, computed after assembly
        ds64_payload = struct.pack("<QQQI", 0, data_bytes, data_bytes // 2, 0)
        ds64_chunk = b"ds64" + struct.pack("<I", len(ds64_payload)) + ds64_payload
        body = body_after_hdr + ds64_chunk + fmt_chunk + data_chunk
        riff_size = len(body)
        ds64_payload = struct.pack("<QQQI", riff_size, data_bytes,
                                   data_bytes // 2, 0)
        ds64_chunk = b"ds64" + struct.pack("<I", len(ds64_payload)) + ds64_payload
        body = body_after_hdr + ds64_chunk + fmt_chunk + data_chunk
        hdr_size = 0xFFFFFFFF if sentinel_ok else 123
        p = tmp_path / "t.rf64"
        p.write_bytes(b"RF64" + struct.pack("<I", hdr_size) + body)
        return str(p)

    def test_ds64_resolves_data_size(self, tmp_path):
        from acidcat.commands.inspect import inspect_rf64
        path = self._rf64(tmp_path)
        chunks, warns = inspect_rf64(path)
        ids = [c["id"] for c in chunks]
        assert ids == ["ds64", "fmt ", "data"]
        data = chunks[-1]
        assert data["size"] == 8
        assert warns == []

    def test_header_sentinel_violation_flagged(self, tmp_path):
        from acidcat.commands.inspect import inspect_rf64
        path = self._rf64(tmp_path, sentinel_ok=False)
        _, warns = inspect_rf64(path)
        assert any("sentinel" in w for w in warns)


class TestInspectSerum:
    def test_json_and_blob(self, tmp_path):
        from acidcat.commands.inspect import inspect_serum
        meta = b'{"presetName": "Growl X", "presetAuthor": "u", "tags": "bass, growl"}'
        p = tmp_path / "g.serumpreset"
        p.write_bytes(b"XferJson" + meta + b"\x01\x02" * 64)
        chunks, warns = inspect_serum(str(p))
        ids = [c["id"] for c in chunks]
        assert ids == ["magc", "json", "blob"]
        assert "Growl X" in chunks[1]["summary"]
        assert chunks[2]["size"] == 128
        assert warns == []

    def test_missing_json_flagged(self, tmp_path):
        from acidcat.commands.inspect import inspect_serum
        p = tmp_path / "bad.serumpreset"
        p.write_bytes(b"XferJson" + b"\x00" * 32)
        _, warns = inspect_serum(str(p))
        assert any("JSON" in w for w in warns)


def _flac_block(btype, payload, last=False):
    head = bytes([(0x80 if last else 0) | btype]) + struct.pack(">I", len(payload))[1:]
    return head + payload


def _streaminfo(rate=44100, channels=2, bits=16, total=441):
    packed = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | total
    return struct.pack(">HH", 4096, 4096) + b"\x00\x00\x0e" + b"\x00\x33\xa8" \
        + struct.pack(">Q", packed) + b"\xab" * 16


def _vorbis_comment(vendor=b"acidcat-test", comments=(b"ARTIST=u", b"TITLE=t")):
    out = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments))
    for c in comments:
        out += struct.pack("<I", len(c)) + c
    return out


def _flac(tmp_path, *blocks, name="t.flac"):
    p = tmp_path / name
    p.write_bytes(b"fLaC" + b"".join(blocks))
    return str(p)


class TestInspectFlac:
    def test_streaminfo_and_comments(self, tmp_path):
        from acidcat.commands.inspect import inspect_flac
        path = _flac(tmp_path,
                     _flac_block(0, _streaminfo(channels=2, total=88200)),
                     _flac_block(4, _vorbis_comment(), last=True),
                     b"\xff" * 100)  # opaque audio frames
        chunks, warns = inspect_flac(path)
        ids = [c["id"] for c in chunks]
        assert ids == ["fLaC", "STREAMINFO", "VORBIS_COMMENT", "frames"]
        si = {f["name"]: f["value"] for f in chunks[1]["fields"]}
        assert si["sample_rate"] == 44100
        assert si["channels"] == 2
        assert si["total_samples"] == 88200
        vc = {f["name"]: f["value"] for f in chunks[2]["fields"]}
        assert vc["ARTIST"] == "u"
        assert vc["TITLE"] == "t"
        assert warns == []

    def test_first_block_not_streaminfo_flagged(self, tmp_path):
        from acidcat.commands.inspect import inspect_flac
        path = _flac(tmp_path, _flac_block(4, _vorbis_comment(), last=True))
        _, warns = inspect_flac(path)
        assert any("not STREAMINFO" in w for w in warns)

    def test_missing_last_flag_flagged(self, tmp_path):
        from acidcat.commands.inspect import inspect_flac
        path = _flac(tmp_path, _flac_block(0, _streaminfo()))
        _, warns = inspect_flac(path)
        assert any("last-metadata-block" in w for w in warns)

    def test_metadata_block_overrun_flagged(self, tmp_path):
        from acidcat.commands.inspect import inspect_flac
        # a PADDING block declares 8192 bytes but only 8 are present.
        bogus = bytes([0x80 | 1]) + struct.pack(">I", 8192)[1:] + b"\x00" * 8
        path = _flac(tmp_path, _flac_block(0, _streaminfo()), bogus)
        chunks, _ = inspect_flac(path)
        pad = next(c for c in chunks if c["id"] == "PADDING")
        assert any("overruns the file" in w for w in pad["warnings"])


# MPEG 1 Layer III, 128 kbps, 44100 Hz, mono: 417-byte frames
_MP3_FRAME = b"\xff\xfb\x90\xc0" + b"\x00" * 413


def _id3v2(*frames, major=3):
    body = b"".join(frames)
    # synchsafe size
    n = len(body)
    size = bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])
    return b"ID3" + bytes([major, 0, 0]) + size + body


def _id3_text_frame(fid, text):
    payload = b"\x00" + text.encode("latin-1")
    return fid + struct.pack(">I", len(payload)) + b"\x00\x00" + payload


class TestInspectMp3:
    def test_frames_counted_cbr(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3
        p = tmp_path / "t.mp3"
        p.write_bytes(_MP3_FRAME * 3)
        chunks, warns = inspect_mp3(str(p))
        ids = [c["id"] for c in chunks]
        assert ids == ["frame0", "frames"]
        f0 = {f["name"]: f["value"] for f in chunks[0]["fields"]}
        assert f0["bitrate"] == 128
        assert f0["sample_rate"] == 44100
        frames = next(c for c in chunks if c["id"] == "frames")
        fc = {f["name"]: f["value"] for f in frames["fields"]}
        assert fc["frame_count"] == "3"
        assert "CBR" in frames["summary"]
        assert warns == []

    def test_id3v2_frames_decoded(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3
        tag = _id3v2(_id3_text_frame(b"TIT2", "My Title"),
                     _id3_text_frame(b"TPE1", "Some Artist"))
        p = tmp_path / "t.mp3"
        p.write_bytes(tag + _MP3_FRAME * 2)
        chunks, warns = inspect_mp3(str(p))
        assert chunks[0]["id"] == "ID3v2"
        by_name = {f["name"]: f["value"] for f in chunks[0]["fields"]}
        assert by_name["TIT2"] == "My Title"
        assert by_name["TPE1"] == "Some Artist"
        assert warns == []

    def test_id3v1_trailer_detected(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3
        v1 = b"TAG" + b"My Title".ljust(30, b"\x00") \
            + b"My Artist".ljust(30, b"\x00") + b"\x00" * 65
        p = tmp_path / "t.mp3"
        p.write_bytes(_MP3_FRAME * 2 + v1)
        chunks, _ = inspect_mp3(str(p))
        assert chunks[-1]["id"] == "ID3v1"
        assert "My Title" in chunks[-1]["summary"]

    def test_no_frame_flagged(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3
        p = tmp_path / "t.mp3"
        p.write_bytes(_id3v2(_id3_text_frame(b"TIT2", "x")) + b"\x00" * 200)
        _, warns = inspect_mp3(str(p))
        assert any("no valid MPEG" in w for w in warns)

    def test_deep_frame_listing(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3
        p = tmp_path / "t.mp3"
        p.write_bytes(_MP3_FRAME * 3)
        chunks, _ = inspect_mp3(str(p), deep=True)
        frames = next(c for c in chunks if c["id"] == "frames")
        assert "rows" in frames
        assert len(frames["rows"]) == 3
        assert frames["rows"][0]["kbps"] == 128
        assert frames["rows"][0]["offset"] == "0x00000000"

    def test_default_has_no_rows(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3
        p = tmp_path / "t.mp3"
        p.write_bytes(_MP3_FRAME * 2)
        chunks, _ = inspect_mp3(str(p))
        assert all("rows" not in c for c in chunks)


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

    def test_flac_dispatch(self, tmp_path, capsys):
        path = _flac(tmp_path, _flac_block(0, _streaminfo(), last=True))
        assert run(self._args(path)) == 0
        out = capsys.readouterr().out
        assert "FLAC" in out
        assert "STREAMINFO" in out

    def test_mp3_dispatch(self, tmp_path, capsys):
        p = tmp_path / "t.mp3"
        p.write_bytes(_MP3_FRAME * 2)
        assert run(self._args(str(p))) == 0
        out = capsys.readouterr().out
        assert "MP3/MPEG audio" in out

    def test_adts_aac_not_dispatched_as_mp3(self, tmp_path, capsys):
        # raw ADTS AAC: sync 0xFFF but layer bits 00. the old 11-bit-sync
        # gate misread it as MP3; now it must be cleanly rejected.
        aac = b"\xff\xf1\x50\x80" + b"\x00" * 380
        p = tmp_path / "t.aac"
        p.write_bytes(aac * 4)
        assert run(self._args(str(p))) == 1
        assert "not a" in capsys.readouterr().err

    def test_frames_flag_renders_rows(self, tmp_path, capsys):
        p = tmp_path / "t.mp3"
        p.write_bytes(_MP3_FRAME * 3)
        assert run(self._args(str(p), frames=True)) == 0
        out = capsys.readouterr().out
        assert "kbps" in out and "mode" in out  # per-frame column header

    def test_frames_noop_note_on_wav(self, tmp_path, capsys):
        path = _wav(tmp_path, _fmt(), _data())
        assert run(self._args(path, frames=True)) == 0
        out = capsys.readouterr().out
        assert "no per-element structure" in out

    def test_not_riff_exits_1(self, tmp_path, capsys):
        p = tmp_path / "x.bin"
        p.write_bytes(b"\x00" * 64)
        assert run(self._args(str(p))) == 1

    def test_missing_file_exits_1(self):
        assert run(self._args("does/not/exist.wav")) == 1

    def test_color_always_emits_ansi(self, tmp_path, capsys):
        p = tmp_path / "t.mp3"
        p.write_bytes(_MP3_FRAME * 2)
        assert run(self._args(str(p), color="always")) == 0
        assert "\x1b[" in capsys.readouterr().out

    def test_color_never_is_plain(self, tmp_path, capsys):
        p = tmp_path / "t.mp3"
        p.write_bytes(_MP3_FRAME * 2)
        assert run(self._args(str(p), color="never")) == 0
        assert "\x1b[" not in capsys.readouterr().out
