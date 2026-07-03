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

    def test_ssnd_offset_exceeding_payload_flagged(self, tmp_path):
        # an SSND offset larger than the chunk payload degrades to 0 bytes;
        # it must be flagged, not silently swallowed.
        ssnd = _aiff_chunk(b"SSND", struct.pack(">II", 0xFFFFFFF0, 0) + b"\x00" * 8)
        path = _aiff(tmp_path, _comm(), ssnd)
        chunks, _ = inspect_aiff(path, "AIFF")
        s = next(c for c in chunks if c["id"] == "SSND")
        assert any("offset" in w and "exceeds" in w for w in s["warnings"])

    def test_comm_frames_exceeding_file_flagged(self, tmp_path):
        # num_sample_frames that implies more audio than the whole file holds
        # makes the duration untrustworthy; flag it on the COMM chunk.
        path = _aiff(tmp_path, _comm(frames=0xFFFFFFFF), _ssnd())
        chunks, _ = inspect_aiff(path, "AIFF")
        comm = next(c for c in chunks if c["id"] == "COMM")
        assert any("implies more audio" in w for w in comm["warnings"])

    def test_markers_and_inst_loops(self, tmp_path):
        path = _aiff(tmp_path, _comm(),
                     _mark([(1, 0, b"start"), (2, 4, b"end")]),
                     _inst(sustain=(1, 1, 2)), _ssnd())
        chunks, warns = inspect_aiff(path, "AIFF")
        mark = next(c for c in chunks if c["id"] == "MARK")
        assert "2 marker(s)" in mark["summary"]
        assert warns == []

    def test_aifc_compressed_duration_is_approximate(self, tmp_path):
        # AIFC ima4: num_sample_frames is a packet count, so frames/rate is
        # not the real duration. it must be labeled approximate and warned.
        comm = _aiff_chunk(b"COMM", struct.pack(">hIh", 1, 83, 16)
                           + RATE_44100 + b"ima4" + b"\x00")
        path = _aiff(tmp_path, comm, _ssnd(), form=b"AIFC")
        chunks, warns = inspect_aiff(path, "AIFC")
        comm_c = next(c for c in chunks if c["id"] == "COMM")
        assert "approx" in comm_c["summary"]
        assert any("approximate" in w for w in comm_c["warnings"])

    def test_aifc_pcm_duration_is_exact(self, tmp_path):
        # sowt/NONE are uncompressed: duration stays exact, no warning.
        comm = _aiff_chunk(b"COMM", struct.pack(">hIh", 1, 44100, 16)
                           + RATE_44100 + b"sowt" + b"\x00")
        path = _aiff(tmp_path, comm, _ssnd(), form=b"AIFC")
        chunks, _ = inspect_aiff(path, "AIFC")
        comm_c = next(c for c in chunks if c["id"] == "COMM")
        assert "approx" not in comm_c["summary"]
        assert "1.000 s" in comm_c["summary"]

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
    b"\x00\x90\x3C\x64"                # note on C3
    b"\x00\x40\x6E"                    # running status note on E3
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
        assert "C3" in note_on["detail"]  # MIDI 60 = C3 (DAW convention)

    def test_default_midi_has_no_rows(self, tmp_path):
        path = _smf(tmp_path, [_TRACK])
        chunks, _ = inspect_midi(path)
        assert all("rows" not in c for c in chunks)

    def test_whole_file_read_is_capped(self, tmp_path, monkeypatch):
        # a forged multi-GB .mid must not be slurped whole (DoS). cap
        # shrunk for the test; the header still parses and the missing
        # tail surfaces as warnings instead of an OOM.
        import acidcat.core.midi as midimod
        monkeypatch.setattr(midimod, "MAX_SMF_BYTES", 64)
        big = _smf(tmp_path, [_TRACK * 20], name="big.mid")
        chunks, warns = inspect_midi(big)
        assert chunks[0]["id"] == "MThd"
        assert any("cap" in w for w in warns)

    def test_mthd_length_below_six(self, tmp_path):
        # hdr_len - 6 went negative for a sub-spec MThd length and, under
        # --hex, reached the renderer as read(negative) (the whole file).
        # no field may carry a negative length; the lie gets a warning.
        p = tmp_path / "short_hdr.mid"
        p.write_bytes(b"MThd" + struct.pack(">I", 2)
                      + struct.pack(">HHH", 0, 1, 480)
                      + b"MTrk" + struct.pack(">I", len(_TRACK)) + _TRACK)
        chunks, _ = inspect_midi(str(p))
        mthd = chunks[0]
        assert all(f["len"] >= 0 for f in mthd["fields"])
        assert any("spec minimum is 6" in w for w in mthd["warnings"])

    def test_mthd_length_below_six_hex_render(self, tmp_path, capsys):
        p = tmp_path / "short_hdr2.mid"
        p.write_bytes(b"MThd" + struct.pack(">I", 2)
                      + struct.pack(">HHH", 0, 1, 480)
                      + b"MTrk" + struct.pack(">I", len(_TRACK)) + _TRACK)
        args = SimpleNamespace(target=str(p), show_hex=True, format="table",
                               quiet=False, verbose=False)
        assert run(args) == 0  # must not blow up rendering hex


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

    def test_ds64_override_table_resolves_nondata_sentinel(self, tmp_path):
        # a non-data chunk carrying the sentinel is resolved through the
        # ds64 override table, not broken on.
        from acidcat.commands.inspect import inspect_rf64
        fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
        fmt_chunk = b"fmt " + struct.pack("<I", 16) + fmt
        bigx = b"bigx" + struct.pack("<I", 0xFFFFFFFF) + b"\x00" * 16
        data_chunk = b"data" + struct.pack("<I", 0xFFFFFFFF) + b"\x00" * 8
        # ds64: riff/data/samples + a 1-entry table overriding bigx = 16
        ds = (struct.pack("<QQQI", 0, 8, 4, 1) + b"bigx" + struct.pack("<Q", 16))
        ds64_chunk = b"ds64" + struct.pack("<I", len(ds)) + ds
        body = b"WAVE" + ds64_chunk + fmt_chunk + bigx + data_chunk
        p = tmp_path / "tbl.rf64"
        p.write_bytes(b"RF64" + struct.pack("<I", 0xFFFFFFFF) + body)
        chunks, warns = inspect_rf64(str(p))
        ids = [c["id"] for c in chunks]
        assert ids == ["ds64", "fmt ", "bigx", "data"]
        assert next(c["size"] for c in chunks if c["id"] == "bigx") == 16
        assert not any("no override" in w for w in warns)

    def test_fact_sentinel_resolved_via_ds64(self, tmp_path):
        # an RF64 fact chunk stores 0xFFFFFFFF; the real sample count
        # lives in ds64. duration must derive from the ds64 count, not
        # the sentinel (which read as ~97,000 s for a 1 s file).
        from acidcat.commands.inspect import inspect_rf64
        fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
        fmt_chunk = b"fmt " + struct.pack("<I", 16) + fmt
        data_bytes = 88200  # 1.0 s of 16-bit mono at 44100 Hz
        fact_chunk = b"fact" + struct.pack("<I", 4) + struct.pack("<I", 0xFFFFFFFF)
        data_chunk = b"data" + struct.pack("<I", 0xFFFFFFFF) + b"\x00" * data_bytes
        ds64 = struct.pack("<QQQI", 0, data_bytes, 44100, 0)
        ds64_chunk = b"ds64" + struct.pack("<I", len(ds64)) + ds64
        body = b"WAVE" + ds64_chunk + fmt_chunk + fact_chunk + data_chunk
        ds64 = struct.pack("<QQQI", len(body), data_bytes, 44100, 0)
        ds64_chunk = b"ds64" + struct.pack("<I", len(ds64)) + ds64
        body = b"WAVE" + ds64_chunk + fmt_chunk + fact_chunk + data_chunk
        p = tmp_path / "fact.rf64"
        p.write_bytes(b"RF64" + struct.pack("<I", 0xFFFFFFFF) + body)
        chunks, warns = inspect_rf64(str(p))
        fact = next(c for c in chunks if c["id"] == "fact")
        assert "44,100 samples" in fact["summary"]
        data = next(c for c in chunks if c["id"] == "data")
        assert "1.000 s" in data["summary"]
        assert warns == []

    def test_ds64_data_size_beyond_file_linted(self, tmp_path):
        # a ds64 claiming exabytes of data cannot be honest about a
        # small file; lint it at the source, not just at the data chunk.
        from acidcat.commands.inspect import inspect_rf64
        fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
        fmt_chunk = b"fmt " + struct.pack("<I", 16) + fmt
        data_chunk = b"data" + struct.pack("<I", 0xFFFFFFFF) + b"\x00" * 8
        ds64 = struct.pack("<QQQI", 100, 2 ** 63, 4, 0)
        ds64_chunk = b"ds64" + struct.pack("<I", len(ds64)) + ds64
        body = b"WAVE" + ds64_chunk + fmt_chunk + data_chunk
        p = tmp_path / "lie.rf64"
        p.write_bytes(b"RF64" + struct.pack("<I", 0xFFFFFFFF) + body)
        chunks, _ = inspect_rf64(str(p))
        d = next(c for c in chunks if c["id"] == "ds64")
        assert any("exceeds the whole file" in w for w in d["warnings"])

    def test_fact_sentinel_without_ds64_not_trusted(self, tmp_path):
        # a plain RIFF/WAVE with a 0xFFFFFFFF fact has no ds64 to
        # resolve through; the sentinel must not become a sample count
        # (27 hours at 44.1 kHz).
        path = _wav(tmp_path, _fmt(),
                    _chunk(b"fact", struct.pack("<I", 0xFFFFFFFF)),
                    _data(441))
        chunks, _ = inspect_wav(path)
        fact = next(c for c in chunks if c["id"] == "fact")
        assert any("ds64" in w for w in fact["warnings"])
        data = next(c for c in chunks if c["id"] == "data")
        frames = next(f for f in data["fields"] if f["name"] == "frames")
        assert frames["value"] == 441  # from data bytes, not the sentinel


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

    def test_deeply_nested_json_no_crash(self, tmp_path):
        # the json scanner recurses per nesting level; a forged preset
        # with thousands of nested objects raised RecursionError past
        # the ValueError-only handler and crashed the command.
        from acidcat.commands.inspect import inspect_serum
        p = tmp_path / "deep.serumpreset"
        p.write_bytes(b"XferJson{" + b'"k":{' * 5000)
        chunks, warns = inspect_serum(str(p))  # must not raise
        assert any("JSON" in w for w in warns)

    def test_deeply_nested_json_core_parser_no_crash(self, tmp_path):
        # same guard in the core parser used by info/index
        from acidcat.core.serum import parse_serum_preset
        p = tmp_path / "deep2.serumpreset"
        p.write_bytes(b"XferJson{" + b'"k":{' * 5000)
        assert parse_serum_preset(str(p)) == {}  # must not raise

    def test_multibyte_utf8_blob_boundary(self, tmp_path):
        # raw_decode returns a CHARACTER offset; using it as a byte
        # offset shifted the blob chunk left by one byte per multibyte
        # UTF-8 character in the JSON metadata.
        from acidcat.commands.inspect import inspect_serum
        meta = '{"presetName": "Gröwl ééé"}'.encode("utf-8")
        p = tmp_path / "umlaut.serumpreset"
        p.write_bytes(b"XferJson" + meta + b"\x01" * 64)
        chunks, warns = inspect_serum(str(p))
        blob = next(c for c in chunks if c["id"] == "blob")
        assert blob["offset"] == 8 + len(meta)
        assert blob["size"] == 64
        jsn = next(c for c in chunks if c["id"] == "json")
        assert jsn["size"] == len(meta)
        assert warns == []


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

    def test_picture_forged_mime_length(self, tmp_path):
        from acidcat.commands.inspect import inspect_flac
        # PICTURE with a mime length claiming 4 GB: must warn and stop,
        # not decode the rest of the block as a garbage mime string.
        pic = struct.pack(">I", 3) + struct.pack(">I", 0xFFFFFFFF) + b"\x00" * 64
        path = _flac(tmp_path, _flac_block(0, _streaminfo()),
                     _flac_block(6, pic, last=True))
        chunks, _ = inspect_flac(path)
        p = next(c for c in chunks if c["id"] == "PICTURE")
        assert p["summary"] == "truncated"
        assert any("mime_type length" in w for w in p["warnings"])

    def test_picture_forged_description_length(self, tmp_path):
        from acidcat.commands.inspect import inspect_flac
        pic = (struct.pack(">I", 3)
               + struct.pack(">I", 9) + b"image/png"
               + struct.pack(">I", 0xFFFFFF00) + b"\x00" * 64)
        path = _flac(tmp_path, _flac_block(0, _streaminfo()),
                     _flac_block(6, pic, last=True))
        chunks, _ = inspect_flac(path)
        p = next(c for c in chunks if c["id"] == "PICTURE")
        assert p["summary"] == "truncated"
        assert any("description length" in w for w in p["warnings"])

    def test_picture_valid_still_decodes(self, tmp_path):
        from acidcat.commands.inspect import inspect_flac
        img = b"\x89PNG\r\n"
        pic = (struct.pack(">I", 3)
               + struct.pack(">I", 9) + b"image/png"
               + struct.pack(">I", 5) + b"cover"
               + struct.pack(">IIIII", 32, 32, 24, 0, len(img)) + img)
        path = _flac(tmp_path, _flac_block(0, _streaminfo()),
                     _flac_block(6, pic, last=True))
        chunks, _ = inspect_flac(path)
        p = next(c for c in chunks if c["id"] == "PICTURE")
        assert "image/png" in p["summary"]
        assert "32x32" in p["summary"]


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

    def test_id3v22_text_frames_decode(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3

        def v22(fid, text):  # v2.2: 3-char id + 3-byte size
            payload = b"\x00" + text.encode("latin-1")
            return fid + struct.pack(">I", len(payload))[1:] + payload
        tag = _id3v2(v22(b"TT2", "Song"), v22(b"TP1", "Band"), major=2)
        p = tmp_path / "t.mp3"
        p.write_bytes(tag + _MP3_FRAME)
        chunks, _ = inspect_mp3(str(p))
        id3 = next(c for c in chunks if c["id"] == "ID3v2")
        vals = {f["name"]: (f["value"], f["note"]) for f in id3["fields"]}
        assert vals.get("TT2") == ("Song", "title")
        assert vals.get("TP1") == ("Band", "artist")

    def test_id3v2_extended_header_skipped(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3

        def v23(fid, text):
            payload = b"\x00" + text.encode("latin-1")
            return fid + struct.pack(">I", len(payload)) + b"\x00\x00" + payload
        ext = struct.pack(">I", 6) + b"\x00" * 6  # v2.3 ext header: size(excl)=6
        tag = _id3v2(ext + v23(b"TIT2", "Hi"), major=3)
        tag = tag[:5] + bytes([0x40]) + tag[6:]  # set extended-header flag
        p = tmp_path / "t.mp3"
        p.write_bytes(tag + _MP3_FRAME)
        chunks, _ = inspect_mp3(str(p))
        id3 = next(c for c in chunks if c["id"] == "ID3v2")
        names = [f["name"] for f in id3["fields"]]
        assert "extended_header" in names
        assert any(f["name"] == "TIT2" for f in id3["fields"])  # frame past it still read

    def test_id3v2_unsync_warns(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3

        def v23(fid, payload):
            return fid + struct.pack(">I", len(payload)) + b"\x00\x00" + payload
        tag = _id3v2(v23(b"TIT2", b"\x00\xff\x00A"), major=3)
        tag = tag[:5] + bytes([0x80]) + tag[6:]  # set unsync flag
        p = tmp_path / "t.mp3"
        p.write_bytes(tag + _MP3_FRAME)
        chunks, _ = inspect_mp3(str(p))
        id3 = next(c for c in chunks if c["id"] == "ID3v2")
        assert any("unsynchronised" in w for w in id3["warnings"])

    def test_mp3_vbri_header_parsed(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3
        # inject a VBRI header at the fixed offset 36 into a valid frame
        vbri = b"VBRI" + struct.pack(">HHH", 1, 0, 100) + struct.pack(">II", 5000, 42)
        frame = bytearray(_MP3_FRAME)
        frame[36:36 + len(vbri)] = vbri
        p = tmp_path / "t.mp3"
        p.write_bytes(bytes(frame) * 2)
        chunks, _ = inspect_mp3(str(p))
        f0 = next(c for c in chunks if c["id"] == "frame0")
        d = {f["name"]: f["value"] for f in f0["fields"]}
        assert d.get("vbr_tag") == "VBRI"
        assert d.get("frame_count") == "42"
        frames = next(c for c in chunks if c["id"] == "frames")
        assert any(f["name"] == "vbr" and f["value"] is True
                   for f in frames["fields"])

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

    def test_xing_frame_count_divergence_flagged(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3
        # forge a Xing header (offset 21 for MPEG1 mono) declaring 9999
        # frames while only 3 are actually present.
        fr = bytearray(_MP3_FRAME)
        fr[21:25] = b"Xing"
        fr[25:29] = struct.pack(">I", 0x01)      # frames flag
        fr[29:33] = struct.pack(">I", 9999)      # bogus frame count
        p = tmp_path / "vbr.mp3"
        p.write_bytes(bytes(fr) + _MP3_FRAME * 2)
        chunks, _ = inspect_mp3(str(p))
        frames = next(c for c in chunks if c["id"] == "frames")
        assert any("diverges" in w for w in frames["warnings"])

    def test_info_tag_is_cbr(self, tmp_path):
        # is_vbr_header was true for both Xing and Info; an Info tag is
        # LAME's CBR marker and must not force the VBR label.
        from acidcat.commands.inspect import inspect_mp3
        fr = bytearray(_MP3_FRAME)
        fr[21:25] = b"Info"
        fr[25:29] = struct.pack(">I", 0x01)      # frames flag
        fr[29:33] = struct.pack(">I", 3)         # accurate frame count
        p = tmp_path / "cbr.mp3"
        p.write_bytes(bytes(fr) + _MP3_FRAME * 2)
        chunks, _ = inspect_mp3(str(p))
        frames = next(c for c in chunks if c["id"] == "frames")
        assert "CBR" in frames["summary"]
        vbr = next(f for f in frames["fields"] if f["name"] == "vbr")
        assert vbr["value"] is False

    def test_xing_tag_forces_vbr_even_with_uniform_bitrates(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3
        fr = bytearray(_MP3_FRAME)
        fr[21:25] = b"Xing"
        fr[25:29] = struct.pack(">I", 0x01)
        fr[29:33] = struct.pack(">I", 3)
        p = tmp_path / "vbr2.mp3"
        p.write_bytes(bytes(fr) + _MP3_FRAME * 2)
        chunks, _ = inspect_mp3(str(p))
        frames = next(c for c in chunks if c["id"] == "frames")
        assert "VBR" in frames["summary"]

    def test_truncated_xing_header_no_crash(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp3
        # a first frame that declares a Xing tag with the frames flag set but
        # ends right after the flags: the frame_count read must not run past
        # the buffer (previously an uncaught struct.error crashed inspect).
        p = tmp_path / "x.mp3"
        p.write_bytes(b"\xff\xfb\x90\xc0" + b"\x00" * 17 + b"Xing" + struct.pack(">I", 1))
        chunks, warns = inspect_mp3(str(p))  # must not raise
        allw = warns + [w for c in chunks for w in c.get("warnings", [])]
        assert any("truncated" in w.lower() for w in allw)

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

    def test_id3_wrapped_wav_not_dispatched_as_mp3(self, tmp_path, capsys):
        # an ID3v2 tag prepended to a RIFF/WAVE must not be claimed as MP3.
        wav = b"RIFF" + struct.pack("<I", 4 + len(_fmt()) + len(_data(4))) \
            + b"WAVE" + _fmt() + _data(4)
        p = tmp_path / "x.wav"
        p.write_bytes(_id3v2(_id3_text_frame(b"TIT2", "x")) + wav)
        assert run(self._args(str(p))) == 1
        assert "not" in capsys.readouterr().err.lower()

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

    # ── multiple targets ────────────────────────────────────────────

    def _multi(self, targets, **kw):
        base = dict(targets=list(targets), show_hex=False, format="table",
                    quiet=False, verbose=False)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_two_files_get_readelf_banner(self, tmp_path, capsys):
        a = _wav(tmp_path, _fmt(), _data(), name="a.wav")
        b = _wav(tmp_path, _fmt(), _data(), name="b.wav")
        assert run(self._multi([a, b])) == 0
        out = capsys.readouterr().out
        assert out.count("File: ") == 2
        assert f"File: {a}" in out and f"File: {b}" in out

    def test_single_file_has_no_banner(self, tmp_path, capsys):
        a = _wav(tmp_path, _fmt(), _data(), name="a.wav")
        assert run(self._multi([a])) == 0
        assert "File: " not in capsys.readouterr().out

    def test_multi_json_is_ndjson(self, tmp_path, capsys):
        import json
        a = _wav(tmp_path, _fmt(), _data(), name="a.wav")
        b = _wav(tmp_path, _fmt(), _data(), name="b.wav")
        assert run(self._multi([a, b], format="json")) == 0
        lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
        assert len(lines) == 2
        docs = [json.loads(l) for l in lines]  # each line parses on its own
        assert [d["format"] for d in docs] == ["RIFF/WAVE", "RIFF/WAVE"]

    def test_missing_among_present_keeps_going_exit_1(self, tmp_path, capsys):
        a = _wav(tmp_path, _fmt(), _data(), name="a.wav")
        assert run(self._multi([a, str(tmp_path / "gone.wav")])) == 1
        out = capsys.readouterr().out
        assert "RIFF/WAVE" in out  # the good file still rendered

    # ── --hex reads the right bytes across formats (payload_base) ────

    def test_hex_flac_reads_magic_not_offset_8(self, tmp_path, capsys):
        p = _flac(tmp_path, _flac_block(0, _streaminfo(), last=True))
        assert run(self._args(p, show_hex=True)) == 0
        out = capsys.readouterr().out
        # the magic field must show the fLaC bytes, not 8 bytes into the file
        assert "66 4c 61 43" in out

    def test_hex_mp3_id3_reads_absolute_offsets(self, tmp_path, capsys):
        p = tmp_path / "t.mp3"
        p.write_bytes(_id3v2(_id3_text_frame(b"TIT2", "Hi")) + _MP3_FRAME)
        assert run(self._args(str(p), show_hex=True)) == 0
        out = capsys.readouterr().out
        # the TIT2 frame id must appear in the hex column (proves the ID3
        # tag's absolute field offsets are not double-counted by +8)
        assert "54 49 54 32" in out

    # ── --only / --exclude chunk selection ──────────────────────────

    def test_only_filters_to_named_chunk(self, tmp_path, capsys):
        p = _wav(tmp_path, _fmt(), _data(), _acid())
        assert run(self._args(p, only="fmt")) == 0
        out = capsys.readouterr().out
        assert "showing 1 of 3 chunks" in out
        assert "PCM" in out and "acid @" not in out

    def test_only_is_case_and_space_insensitive(self, tmp_path, capsys):
        p = _wav(tmp_path, _fmt(), _data())
        assert run(self._args(p, only="FMT")) == 0  # matches the "fmt " id
        assert "showing 1 of 2 chunks" in capsys.readouterr().out

    def test_exclude_drops_chunks(self, tmp_path, capsys):
        p = _wav(tmp_path, _fmt(), _data(), _acid())
        assert run(self._args(p, exclude="data,acid")) == 0
        out = capsys.readouterr().out
        assert "showing 1 of 3 chunks" in out
        assert "acid @" not in out

    def test_only_applies_to_ndjson(self, tmp_path, capsys):
        import json
        p = _wav(tmp_path, _fmt(), _data(), _acid())
        assert run(self._args(p, only="acid", format="json")) == 0
        doc = json.loads(capsys.readouterr().out)
        assert [c["id"] for c in doc["chunks"]] == ["acid"]
        assert "_idx" not in doc["chunks"][0]  # helper key stays internal


class TestParseFmtExtensible:
    def test_extensible_channel_mask_and_guid(self):
        from acidcat.commands.inspect import _parse_fmt, _KSDATAFORMAT_TAIL
        sub = struct.pack("<H", 1) + _KSDATAFORMAT_TAIL  # PCM subtype
        b = (struct.pack("<HHIIHH", 0xFFFE, 6, 48000, 48000 * 12, 12, 16)
             + struct.pack("<HH", 22, 16) + struct.pack("<I", 0x3F) + sub)
        _, fields, warns = _parse_fmt(b, {})
        d = {f["name"]: (f["value"], f["note"]) for f in fields}
        assert d["channel_mask"] == ("0x3f", "FL, FR, FC, LFE, BL, BR")
        assert d["sub_format"] == ("PCM", "KSDATAFORMAT_SUBTYPE")
        assert warns == []

    def test_extensible_nonstandard_guid_warns(self):
        from acidcat.commands.inspect import _parse_fmt
        b = (struct.pack("<HHIIHH", 0xFFFE, 2, 44100, 44100 * 4, 4, 16)
             + struct.pack("<HH", 22, 16) + struct.pack("<I", 0x03)
             + struct.pack("<H", 1) + b"\x00" * 14)  # wrong GUID tail
        _, _, warns = _parse_fmt(b, {})
        assert any("KSDATAFORMAT" in w for w in warns)

    def test_nonextensible_extended_shows_cbsize(self):
        from acidcat.commands.inspect import _parse_fmt
        b = struct.pack("<HHIIHH", 3, 2, 44100, 44100 * 8, 8, 32) + struct.pack("<H", 0)
        _, fields, _ = _parse_fmt(b, {})
        assert any(f["name"] == "cb_size" for f in fields)


class TestParseBext:
    def _bext(self, version, umid=b"", loud=None, hist=b""):
        b = (b"Desc".ljust(256, b"\x00") + b"Orig".ljust(32, b"\x00")
             + b"Ref".ljust(32, b"\x00") + b"2026-07-02" + b"11-30-00"
             + struct.pack("<II", 44100, 0) + struct.pack("<H", version))
        if version >= 2:
            loud = loud or [0, 0, 0, 0, 0]
            b += umid.ljust(64, b"\x00") + b"".join(
                struct.pack("<h", x) for x in loud) + b"\x00" * 180
        elif version >= 1:
            b += umid.ljust(64, b"\x00") + b"\x00" * 190
        else:
            b += b"\x00" * 254
        return b + hist

    def test_v2_umid_loudness_and_history(self):
        from acidcat.commands.inspect import _parse_bext
        b = self._bext(2, umid=b"\x01\x02\x03",
                       loud=[-2265, 500, -150, 0x7FFF, -1000],
                       hist=b"A=PCM,F=48000,W=24,M=stereo\r\n\x00\x00")
        _, fields, _ = _parse_bext(b, {"sample_rate": 44100})
        d = {f["name"]: f["value"] for f in fields}
        assert d["loudness_value"] == "-22.65 LUFS"
        assert d["max_momentary"] == "unset"      # 0x7fff sentinel
        assert d["umid"].startswith("010203")
        assert d["coding_history"].startswith("A=PCM")

    def test_v0_has_no_umid_or_loudness(self):
        from acidcat.commands.inspect import _parse_bext
        _, fields, _ = _parse_bext(self._bext(0), {})
        assert not any(f["name"] in ("umid", "loudness_value") for f in fields)

    def test_v1_all_zero_umid(self):
        from acidcat.commands.inspect import _parse_bext
        _, fields, _ = _parse_bext(self._bext(1), {})
        assert next(f["value"] for f in fields if f["name"] == "umid") == "0 (no UMID)"


class TestFlacCuesheet:
    def test_cuesheet_tracks_and_leadout(self):
        from acidcat.commands.inspect import _flac_cuesheet
        b = b"1234567890123".ljust(128, b"\x00") + struct.pack(">Q", 88200)
        b += bytes([0x80]) + b"\x00" * 258 + bytes([2])  # is-CD, reserved, 2 tracks
        b += (struct.pack(">Q", 0) + bytes([1]) + b"USRC12300001".ljust(12, b"\x00")
              + bytes([0]) + b"\x00" * 13 + bytes([1]))          # track 1, 1 index
        b += struct.pack(">Q", 0) + bytes([1]) + b"\x00" * 3     # its index point
        b += (struct.pack(">Q", 441000) + bytes([170]) + b"\x00" * 12
              + bytes([0]) + b"\x00" * 13 + bytes([0]))          # lead-out
        s, fields, warns = _flac_cuesheet(b)
        assert "CD-DA" in s and "2 track" in s
        tracks = [f for f in fields if f["name"].startswith("track")]
        assert "ISRC USRC12300001" in tracks[0]["note"]
        assert "lead-out" in tracks[1]["note"]
        assert warns == []

    def test_cuesheet_truncated(self):
        from acidcat.commands.inspect import _flac_cuesheet
        s, _, w = _flac_cuesheet(b"\x00" * 100)
        assert s == "truncated" and w


class TestAiffExtraChunks:
    def test_comt_marker_linked_comment(self):
        from acidcat.commands.inspect import _aiff_comt
        b = struct.pack(">H", 1) + struct.pack(">IhH", 0, 3, 5) + b"hello" + b"\x00"
        s, fields, warns = _aiff_comt(b)
        c = next(f for f in fields if f["name"] == "comment[0]")
        assert c["value"] == "hello" and c["note"] == "marker 3"
        assert warns == []

    def test_aesd_channel_status_byte0(self):
        from acidcat.commands.inspect import _aiff_aesd
        s, fields, _ = _aiff_aesd(bytes([0x81]) + b"\x00" * 23)  # pro, 44.1k
        assert "professional" in fields[0]["note"] and "44100" in fields[0]["note"]

    def test_appl_pdos_pstring(self):
        from acidcat.commands.inspect import _aiff_appl
        s, fields, _ = _aiff_appl(b"pdos" + bytes([4]) + b"MyAp" + b"\x00")
        assert any(f["name"] == "name" and f["value"] == "MyAp" for f in fields)


class TestMidiSmpteOffset:
    def test_smpte_offset_decoded(self, tmp_path):
        smpte = bytes([0, 0xFF, 0x54, 0x05, 0x61, 0, 0, 0, 0])  # 30fps, hour 1
        trk = smpte + b"\x00\xFF\x2F\x00"
        data = (b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
                + b"MTrk" + struct.pack(">I", len(trk)) + trk)
        p = tmp_path / "t.mid"
        p.write_bytes(data)
        chunks, _ = inspect_midi(str(p), deep=True)
        mtrk = next(c for c in chunks if c["id"] == "MTrk")
        row = next(r for r in mtrk["rows"] if "smpte" in r["event"])
        assert row["detail"] == "01:00:00:00.00 @ 30 fps"


class TestId3v1AndLame:
    def test_id3v11_full_fields_and_genre(self):
        from acidcat.commands.inspect import _id3v1_fields
        tag = (b"TAG" + b"Title".ljust(30, b"\x00") + b"Artist".ljust(30, b"\x00")
               + b"Album".ljust(30, b"\x00") + b"2020" + b"Comment".ljust(28, b"\x00")
               + b"\x00" + bytes([7]) + bytes([34]))  # v1.1 track 7, genre 34
        fields, title = _id3v1_fields(tag)
        d = {f["name"]: (f["value"], f["note"]) for f in fields}
        assert title == "Title"
        assert d["album"][0] == "Album" and d["year"][0] == "2020"
        assert d["track"][0] == 7
        assert d["genre"] == (34, "Acid")

    def test_id3v10_has_no_track(self):
        from acidcat.commands.inspect import _id3v1_fields
        tag = (b"TAG" + b"T".ljust(30, b"\x00") + b"A".ljust(30, b"\x00")
               + b"Al".ljust(30, b"\x00") + b"2020" + b"C".ljust(30, b"\x00")
               + bytes([13]))  # full comment, genre 13
        fields, _ = _id3v1_fields(tag)
        assert not any(f["name"] == "track" for f in fields)
        assert next(f["note"] for f in fields if f["name"] == "genre") == "Pop"

    def test_lame_replaygain_decode(self):
        from acidcat.commands.inspect import _lame_replaygain
        # name=1 (radio), sign=1 (neg), magnitude 60 -> -6.0 dB
        word = (1 << 13) | (1 << 9) | 60
        assert _lame_replaygain(word) == "-6.0 dB (radio)"
        assert _lame_replaygain(0) is None


class TestInspectFull:
    def _args(self, target, **kw):
        base = dict(target=target, show_hex=False, format="table", quiet=False,
                    verbose=False, full=True)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_full_emits_json_with_raw_and_abs(self, tmp_path, capsys):
        import json
        p = _wav(tmp_path, _fmt(channels=2), _data())
        assert run(self._args(p)) == 0          # --full implies json even w/ format=table
        d = json.loads(capsys.readouterr().out)
        assert d["full"] is True
        fmt = next(c for c in d["chunks"] if c["id"] == "fmt ")
        assert "raw" in fmt and "raw_base" in fmt and "payload_base" in fmt
        # a field's absolute offset must map into the raw region bytes
        sr = next(f for f in fmt["fields"] if f["name"] == "sample_rate")
        raw = bytes.fromhex(fmt["raw"])
        pos = sr["abs"] - fmt["raw_base"]
        assert int.from_bytes(raw[pos:pos + sr["len"]], "little") == 44100

    def test_full_raw_capped(self, tmp_path, capsys):
        import json
        from acidcat.commands.inspect import _FULL_RAW_CAP
        # a data chunk larger than the cap must not dump unbounded hex
        p = _wav(tmp_path, _fmt(), _data(n_frames=_FULL_RAW_CAP, align=2))
        assert run(self._args(p)) == 0
        d = json.loads(capsys.readouterr().out)
        data = next(c for c in d["chunks"] if c["id"] == "data")
        if "raw" in data:
            assert len(bytes.fromhex(data["raw"])) <= _FULL_RAW_CAP


class TestBitwigBWBM:
    def test_bwbm_beats_duration_bpm(self):
        import struct as _s
        from acidcat.commands.inspect import _parse_bwbm
        payload = (_s.pack("<I", 1) + _s.pack("<I", 2) + b"\x00" * 16
                   + _s.pack("<d", 6.0) + _s.pack("<d", 2.5714285714))
        summary, fields, warns = _parse_bwbm(payload, {})
        d = {f["name"]: f["value"] for f in fields}
        assert d["version"] == 1
        assert d["beats"] == 6.0
        assert "2.5714" in d["duration"]
        assert d["derived_bpm"] == 140.0  # 6 / 2.5714 * 60
        assert "140" in summary and warns == []

    def test_bwbm_truncated(self):
        from acidcat.commands.inspect import _parse_bwbm
        s, _, w = _parse_bwbm(b"\x00" * 20, {})
        assert s == "truncated" and w


class TestBitwigWalker:
    def _bw(self, *pairs):
        import struct as _s
        def tok(b): return _s.pack(">I", len(b)) + b
        def meta(k, v): return tok(k) + b"\x08" + _s.pack(">I", len(v)) + v
        body = b"".join(meta(k, v) for k, v in pairs)
        return b"BtWg" + b"0003000200" + body

    def test_parse_meta_extracts_string_fields(self):
        from acidcat.core.bitwig import parse_meta
        data = self._bw((b"device_name", b"Polysynth"), (b"tags", b"bass wide"),
                        (b"comment", b"secret msg"))
        m = parse_meta(data)
        assert m["device_name"] == "Polysynth"
        assert m["tags"] == "bass wide"
        assert m["comment"] == "secret msg"

    def test_inspect_bitwig_surfaces_description(self, tmp_path):
        from acidcat.commands.inspect import inspect_bitwig
        p = tmp_path / "t.bwpreset"
        p.write_bytes(self._bw((b"device_name", b"Convolution"),
                               (b"comment", b"wussssuppppp")))
        chunks, warns = inspect_bitwig(str(p))
        meta = next(c for c in chunks if c["id"] == "meta")
        vals = {f["name"]: f["value"] for f in meta["fields"]}
        assert vals["device"] == "Convolution"
        assert vals["description"] == "wussssuppppp"

    def test_bitwig_hostile_length_ignored(self):
        # a forged u32 length must not read past the buffer
        from acidcat.core.bitwig import parse_meta
        data = b"BtWg" + b"0003000200" + b"\xff\xff\xff\xff" + b"junk"
        assert parse_meta(data) == {}  # no crash, nothing decoded


class TestVitalWalker:
    def test_parse_vital_metadata(self):
        import json
        from acidcat.core.vital import parse_vital
        data = json.dumps({"synth_version": "1.0.7", "preset_name": "Test",
                           "author": "Me", "settings": {"a": 1}}).encode()
        obj = parse_vital(data)
        assert obj["preset_name"] == "Test" and obj["author"] == "Me"

    def test_non_vital_json_rejected(self):
        from acidcat.core.vital import parse_vital
        assert parse_vital(b'{"hello":"world"}') is None   # lacks Vital keys
        assert parse_vital(b'not json') is None

    def test_inspect_vital_surfaces_name(self, tmp_path):
        import json
        from acidcat.commands.inspect import inspect_vital
        p = tmp_path / "t.vital"
        p.write_bytes(json.dumps({"synth_version": "1.0", "preset_name": "P",
                                  "author": "A", "settings": {}}).encode())
        chunks, _ = inspect_vital(str(p))
        vals = {f["name"]: f["value"] for f in chunks[0]["fields"]}
        assert vals["preset_name"] == "P" and vals["author"] == "A"


class TestNcwWalker:
    def _ncw(self, ch=2, bits=24, rate=48000, n=44100):
        import struct as _s
        from acidcat.core.ncw import MAGIC
        return (MAGIC + b"\x31\x01\x00\x00" + _s.pack("<HHII", ch, bits, rate, n)
                + b"\x00" * 40)

    def test_parse_ncw_header(self):
        from acidcat.core.ncw import parse_header
        h = parse_header(self._ncw())
        assert h == {"channels": 2, "bits": 24, "sample_rate": 48000,
                     "num_samples": 44100}

    def test_ncw_bad_params_rejected(self):
        from acidcat.core.ncw import parse_header, MAGIC
        import struct as _s
        # bits=7 is invalid -> not trusted as NCW
        bad = MAGIC + b"\x00" * 4 + _s.pack("<HHII", 2, 7, 48000, 100) + b"\x00" * 40
        assert parse_header(bad) is None
        assert parse_header(b"nope" + b"\x00" * 40) is None


class TestMp4Walker:
    def _box(self, t, payload):
        import struct as _s
        return _s.pack(">I", 8 + len(payload)) + t + payload

    def _m4a_with_title(self, title):
        import struct as _s
        data_box = self._box(b"data", _s.pack(">II", 1, 0) + title.encode())
        nam = self._box(b"\xa9nam", data_box)
        ilst = self._box(b"ilst", nam)
        meta = self._box(b"meta", b"\x00\x00\x00\x00" + ilst)  # FullBox prefix
        udta = self._box(b"udta", meta)
        moov = self._box(b"moov", udta)
        ftyp = self._box(b"ftyp", b"M4A \x00\x00\x00\x00")
        return ftyp + moov

    def test_iter_boxes_tree_and_ilst(self):
        from acidcat.core.mp4 import iter_boxes, parse_ilst, is_mp4
        data = self._m4a_with_title("Hello")
        assert is_mp4(data)
        types = [b["type"] for b in iter_boxes(data)]
        assert b"ftyp" in types and b"moov" in types and b"ilst" in types
        assert parse_ilst(data) == {"title": "Hello"}

    def test_truncated_box_flagged_not_crash(self):
        import struct as _s
        from acidcat.core.mp4 import iter_boxes
        # a box claiming more than the buffer holds
        data = _s.pack(">I", 999999) + b"moov" + b"\x00" * 4
        boxes = list(iter_boxes(data))
        assert boxes and boxes[0]["truncated"]

    def test_inspect_mp4_surfaces_title(self, tmp_path):
        from acidcat.commands.inspect import inspect_mp4
        p = tmp_path / "t.m4a"
        p.write_bytes(self._m4a_with_title("Song"))
        chunks, _ = inspect_mp4(str(p))
        tags = next(c for c in chunks if c["id"] == "tags")
        assert any(f["value"] == "Song" for f in tags["fields"])


class TestPrettyMode:
    def test_pretty_bitwig_no_byte_offsets(self, tmp_path, capsys):
        from types import SimpleNamespace
        def tok(b): return struct.pack(">I", len(b)) + b
        def meta(k, v): return tok(k) + b"\x08" + struct.pack(">I", len(v)) + v
        p = tmp_path / "t.bwpreset"
        p.write_bytes(b"BtWg0003000200" + meta(b"device_name", b"Conv")
                      + meta(b"tags", b"reverb wide"))
        args = SimpleNamespace(target=str(p), format="table", show_hex=False,
                               quiet=False, verbose=False, pretty=True,
                               color="never")
        assert run(args) == 0
        out = capsys.readouterr().out
        assert "Conv" in out and "reverb wide" in out
        assert "+0x" not in out  # pretty view drops byte offsets


class TestReviewHardening:
    """regression tests for the pre-0.11.0 adversarial-review findings."""
    def _box(self, t, p):
        return struct.pack(">I", 8 + len(p)) + t + p

    def test_mp4_tmpo_int_bomb_gated(self):
        # a hostile type-21 'data' box with a huge payload must not become a
        # bignum (which str() would crash on Python 3.11+).
        from acidcat.core.mp4 import _decode_data_box
        big = self._box(b"data", struct.pack(">II", 21, 0) + b"\x00" * 2048)
        v = _decode_data_box(big, 0, len(big))
        assert not isinstance(v, int)

    def test_mp4_ilst_ancestor_aware(self):
        # a a9nam box that is NOT inside an ilst must not be read as a tag.
        from acidcat.core.mp4 import parse_ilst
        nam = self._box(b"\xa9nam",
                        self._box(b"data", struct.pack(">II", 1, 0) + b"Fake"))
        moov = self._box(b"moov", self._box(b"trak", self._box(b"mdia", nam)))
        assert parse_ilst(moov) == {}

    def test_vital_requires_synth_version(self):
        from acidcat.core.vital import parse_vital
        assert parse_vital(b'{"settings":{}}') is None       # too generic
        assert parse_vital(b'{"synth_version":"1"}') is not None

    def test_ncw_absurd_num_samples_rejected(self):
        from acidcat.core.ncw import parse_header, MAGIC
        bad = (MAGIC + b"\x00" * 4
               + struct.pack("<HHII", 2, 24, 48000, 0xFFFFFFFF) + b"\x00" * 40)
        assert parse_header(bad) is None


class TestNiHsinWalker:
    def _hsin(self, name, product, version):
        import struct as _s
        def p16(s): return _s.pack("<I", len(s)) + s.encode("utf-16-le")
        body = bytearray(0x30)
        body[0x0C:0x10] = b"hsin"
        body += p16(version) + p16(name) + p16(product)
        _s.pack_into("<Q", body, 0, len(body))
        return bytes(body)

    def test_parse_hsin_metadata(self):
        from acidcat.core.ni import parse_hsin
        m = parse_hsin(self._hsin("MyPreset", "Massive", "2.0.1"))
        assert m == {"product": "Massive", "version": "2.0.1", "name": "MyPreset"}

    def test_non_hsin_rejected(self):
        from acidcat.core.ni import parse_hsin
        assert parse_hsin(b"not an hsin file at all" * 4) is None

    def test_inspect_ni_surfaces_name(self, tmp_path):
        from acidcat.commands.inspect import inspect_ni
        p = tmp_path / "t.nmsv"
        p.write_bytes(self._hsin("Bass01", "Massive", "1.5"))
        chunks, _ = inspect_ni(str(p))
        vals = {f["name"]: f["value"] for f in chunks[0]["fields"]}
        assert vals["product"] == "Massive" and vals["name"] == "Bass01"


class TestNiKsdWalker:
    def test_parse_ksd_xml_metadata(self):
        import zlib
        from acidcat.core.ni import parse_ksd
        xml = (b'<?xml version="1.0"?><NI_DOC_HEADER><doc_name>Waltz</doc_name>'
               b'<info><commonAttr><Author>me</Author><Bankname>Bank1</Bankname>'
               b'</commonAttr><Plugins><Plugin>FM8</Plugin></Plugins></info>'
               b'</NI_DOC_HEADER>')
        data = b"-in-" + b"\x00" * 16 + zlib.compress(xml)
        m = parse_ksd(data)
        assert m["name"] == "Waltz" and m["author"] == "me"
        assert m["bank"] == "Bank1" and m["plugin"] == "FM8"

    def test_ksd_decompression_bomb_bounded(self):
        import zlib
        from acidcat.core.ni import _safe_inflate
        bomb = zlib.compress(b"\x00" * (50 * 1024 * 1024))  # 50 MB inflated
        assert _safe_inflate(bomb, maxlen=1024 * 1024) is None  # capped, refused

    def test_non_ksd_rejected(self):
        from acidcat.core.ni import parse_ksd
        assert parse_ksd(b"RIFF____WAVE") is None


class TestNiNksfWalker:
    def test_parse_nksf_msgpack(self):
        import struct as _s
        from acidcat.core.ni import parse_nksf
        mp = (b"\x83" + b"\xa4name" + b"\xa4Bass" + b"\xa6vendor" + b"\xa2NI"
              + b"\xa9bankchain" + b"\x92\xa9Massive X\xa0")  # map3, incl bankchain array
        nisi = b"NISI" + _s.pack("<I", 4 + len(mp)) + _s.pack("<I", 1) + mp
        riff = b"RIFF" + _s.pack("<I", 4 + len(nisi)) + b"NIKS" + nisi
        m = parse_nksf(riff)
        assert m["name"] == "Bass" and m["vendor"] == "NI"
        assert m["bank"] == "Massive X"

    def test_nksf_bad_msgpack_no_crash(self):
        import struct as _s
        from acidcat.core.ni import parse_nksf
        mp = b"\xc1\xc1\xc1"  # 0xc1 is a reserved/unsupported type
        nisi = b"NISI" + _s.pack("<I", 4 + len(mp)) + _s.pack("<I", 1) + mp
        riff = b"RIFF" + _s.pack("<I", 4 + len(nisi)) + b"NIKS" + nisi
        assert parse_nksf(riff) is None  # degrades, no crash

    def test_msgpack_depth_capped(self):
        from acidcat.core.ni import _mp_decode
        deep = b"\x91" * 100  # 100 nested 1-element arrays
        v, _ = _mp_decode(deep)  # must not RecursionError
        assert v is None or isinstance(v, list)


class TestBitwigDeep:
    def _tok(self, s):
        import struct as _s
        b = s.encode() if isinstance(s, str) else s
        return _s.pack(">I", len(b)) + b

    def test_parse_structure_device_tree(self):
        from acidcat.core.bitwig import parse_structure
        data = (b"BtWg0003000200" + self._tok("Filter+") + self._tok("CONTENTS")
                + self._tok("CUTOFF") + self._tok("Reverb") + self._tok("CONTENTS"))
        assert parse_structure(data) == ["Filter+", "Reverb"]

    def test_list_assets_unzips(self):
        import io, zipfile
        from acidcat.core.bitwig import list_assets
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("impulses/x.bwimpulse", b"fLaC" + b"\x00" * 100)
        data = b"BtWg0003000200" + b"\x00" * 8 + buf.getvalue()
        assets = list_assets(data)
        assert len(assets) == 1 and assets[0][0] == "impulses/x.bwimpulse"
        assert assets[0][2][:4] == b"fLaC"

    def test_list_assets_caps_bomb(self):
        import io, zipfile
        from acidcat.core.bitwig import list_assets
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("big", b"\x00" * (200 * 1024))
        data = b"BtWg" + b"\x00" * 8 + buf.getvalue()
        assets = list_assets(data, prefix=1024)  # only inflate a 1 KB prefix
        assert assets[0][2] is not None and len(assets[0][2]) <= 1024

    def test_no_zip_no_assets(self):
        from acidcat.core.bitwig import list_assets
        assert list_assets(b"BtWg0003000200no zip here") == []


class TestNiFastLZ:
    def test_fastlz_literal_and_match(self):
        from acidcat.core.ni import fastlz_decompress
        # 04=literal run of 5 ("ABCDE"); 20 04=match len 3 at offset 4 -> "ABC"
        assert fastlz_decompress(b"\x04ABCDE\x20\x04") == b"ABCDEABC"

    def test_fastlz_empty(self):
        from acidcat.core.ni import fastlz_decompress
        assert fastlz_decompress(b"") == b""

    def test_fastlz_bomb_capped(self):
        from acidcat.core.ni import fastlz_decompress
        # literal "A", then a 259-byte match from offset 0: refused at the cap
        assert fastlz_decompress(b"\x00A\xe0\xfa\x00", max_out=8) is None

    def test_decompress_subtree_none_on_garbage(self):
        from acidcat.core.ni import decompress_subtree
        assert decompress_subtree(b"\x00" * 200) is None


class TestBitwigReferences:
    def test_parse_references_counts(self):
        import struct as _s
        from acidcat.core.bitwig import parse_references
        data = (b"BtWg0003000200" + b"referenced_module_ids" + b"\x19"
                + _s.pack(">I", 3) + b"referenced_device_ids" + b"\x19"
                + _s.pack(">I", 5))
        refs = parse_references(data)
        assert refs["referenced_modules"] == 3
        assert refs["referenced_devices"] == 5

    def test_parse_connections_paths(self):
        import struct as _s
        from acidcat.core.bitwig import parse_connections

        def tok(s):
            return _s.pack(">I", len(s)) + s.encode()
        data = (b"BtWg0003000200" + tok("CONTENTS/MODULES/4/CONTENTS/CUTOFF")
                + tok("plain string") + tok("CONTENTS/MODULES/4/CONTENTS/CUTOFF"))
        conns = parse_connections(data)
        assert conns == ["CONTENTS/MODULES/4/CONTENTS/CUTOFF"]  # deduped


class TestBitwigTree:
    def _tok(self, s):
        import struct as _s
        return _s.pack(">I", len(s)) + s.encode()

    def test_parse_tree_builds_hierarchy(self):
        from acidcat.core.bitwig import parse_tree, flatten_tree
        data = (b"BtWg0003000200"
                + self._tok("CONTENTS/MODULES/8/CONTENTS/OUT")
                + self._tok("CONTENTS/MODULES/8/CONTENTS/LEVEL_1")
                + self._tok("CONTENTS/MODULES/2/CONTENTS/TIME")
                + self._tok("application/bitwig-preset"))  # noise: excluded
        tree = parse_tree(data)
        assert set(tree) == {"CONTENTS"}  # MIME path filtered out
        rows = flatten_tree(tree)
        segs = [seg for _, seg, _ in rows]
        assert "MODULES" in segs and "8" in segs
        leaves = {seg for _, seg, leaf in rows if leaf}
        assert leaves == {"OUT", "LEVEL_1", "TIME"}  # params are the leaves

    def test_flatten_tree_numeric_sort(self):
        from acidcat.core.bitwig import parse_tree, flatten_tree
        data = (b"BtWg0003000200"
                + self._tok("CONTENTS/MODULES/10/CONTENTS/X")
                + self._tok("CONTENTS/MODULES/2/CONTENTS/X"))
        rows = flatten_tree(parse_tree(data))
        idxs = [seg for _, seg, _ in rows if seg.isdigit()]
        assert idxs == ["2", "10"]  # numeric, not lexical


class TestBitwigNumeric:
    def test_parse_numeric_f64(self):
        import struct as _s
        from acidcat.core.bitwig import parse_numeric
        # bpm key (length-prefixed) + type 0x07 + f64 BE 140.0
        data = (b"BtWg0003000200" + _s.pack(">I", 3) + b"bpm" + b"\x07"
                + _s.pack(">d", 140.0) + _s.pack(">I", 11) + b"beat_length"
                + b"\x07" + _s.pack(">d", 16.0))
        nums = parse_numeric(data)
        assert nums["bpm"] == 140.0 and nums["beat_length"] == 16.0

    def test_parse_numeric_substring_safe(self):
        import struct as _s
        from acidcat.core.bitwig import parse_numeric
        # 'bpm' appearing inside another word must not match (length-prefixed)
        data = b"BtWg0003000200somebpmword" + _s.pack(">d", 999.0)
        assert "bpm" not in parse_numeric(data)


class TestBitwigParameters:
    def test_parse_parameters_named_f64(self):
        import struct as _s
        from acidcat.core.bitwig import parse_parameters
        data = (b"BtWg0003000200"
                + _s.pack(">I", 10) + b"GLIDE_TIME" + _s.pack(">I", 0x136)
                + b"\x07" + _s.pack(">d", 1.0)
                + _s.pack(">I", 6) + b"F1FREQ" + _s.pack(">I", 0x136)
                + b"\x07" + _s.pack(">d", 142.1))
        params = dict(parse_parameters(data))
        assert params["GLIDE_TIME"] == 1.0
        assert abs(params["F1FREQ"] - 142.1) < 0.001


class TestVitalDeep:
    def test_deep_structure(self):
        from acidcat.core.vital import deep_structure
        obj = {"synth_version": "1", "settings": {
            "osc_1_on": 1.0, "osc_2_on": 0.0, "osc_3_on": 1.0,
            "wavetables": [{"name": "Saw"}, {"name": "Sine"}],
            "lfos": [{"name": "Sin"}, {"name": "Tri"}],
            "reverb_on": 1.0, "delay_on": 0.0, "distortion_on": 1.0,
            "modulations": [{"source": "lfo_1", "destination": "osc_1_level"},
                            {"source": "", "destination": ""}],
            "modulation_1_amount": 0.5,
        }}
        st = deep_structure(obj)
        assert st["oscillators"] == ["osc_1", "osc_3"]
        assert st["wavetables"] == ["Saw", "Sine"]
        assert st["effects"] == ["distortion", "reverb"]
        assert st["modulations"] == [("lfo_1", "osc_1_level", 0.5)]

    def test_deep_structure_no_settings(self):
        from acidcat.core.vital import deep_structure
        assert deep_structure({"synth_version": "1"}) == {}


class TestBitwigNotes:
    def _note_clip(self, pitch, pos, dur, vel01):
        def f(idb, val):
            return idb + b"\x07" + struct.pack(">d", val)
        rec = (f(b"\x00\x00\x02\xaf", pos) + f(b"\x00\x00\x00\x26", dur)
               + f(b"\x00\x00\x2d\xfc", 1.0) + f(b"\x00\x00\x00\xef", vel01))
        footer = b"\x00\x00\x00\xee\x01" + bytes([pitch])
        return b"BtWg0003000200" + rec + footer

    def test_parse_notes(self):
        from acidcat.core.bitwig import parse_notes
        notes = parse_notes(self._note_clip(60, 2.0, 0.5, 100 / 127))
        assert len(notes) == 1
        n = notes[0]
        assert n["pitch"] == 60 and n["start"] == 2.0 and n["duration"] == 0.5
        assert round(n["velocity"] * 127) == 100

    def test_parse_notes_empty_without_lanes(self):
        from acidcat.core.bitwig import parse_notes
        assert parse_notes(b"BtWg0003000200 no note lanes here") == []


class TestAiffEmbeddedID3:
    def _id3v23(self, frames):
        body = b""
        for fid, text in frames:
            p = b"\x03" + text.encode("utf-8")  # utf-8 encoding byte
            body += fid + struct.pack(">I", len(p)) + b"\x00\x00" + p
        n = len(body)
        ss = bytes([(n >> 21) & 0x7f, (n >> 14) & 0x7f, (n >> 7) & 0x7f, n & 0x7f])
        return b"ID3\x03\x00\x00" + ss + body

    def test_aiff_decodes_embedded_id3(self):
        from acidcat.commands.inspect import _aiff_id3_fields
        tag = self._id3v23([(b"TPE1", "아버지"), (b"TIT2", "untitled")])
        fields = _aiff_id3_fields(tag)
        vals = {f["value"] for f in fields}
        assert "아버지" in vals and "untitled" in vals

    def test_aiff_id3_ignores_non_id3(self):
        from acidcat.commands.inspect import _aiff_id3_fields
        assert _aiff_id3_fields(b"not an id3 tag") == []


class TestOggWalker:
    def _vc(self, vendor, tags):
        b = struct.pack("<I", len(vendor)) + vendor.encode("utf-8")
        b += struct.pack("<I", len(tags))
        for k, v in tags.items():
            c = f"{k}={v}".encode("utf-8")
            b += struct.pack("<I", len(c)) + c
        return b

    def _ogg(self, packets):
        seg_table, body = [], b""
        for p in packets:
            body += p
            rem = len(p)
            while rem >= 255:
                seg_table.append(255); rem -= 255
            seg_table.append(rem)
        hdr = (b"OggS\x00\x02" + b"\x00" * 8 + struct.pack("<I", 999)
               + b"\x00" * 4 + b"\x00" * 4 + bytes([len(seg_table)]) + bytes(seg_table))
        return hdr + body

    def test_ogg_vorbis_comments(self):
        from acidcat.core import ogg
        p1 = b"\x01vorbis" + b"\x00" * 20
        p2 = b"\x03vorbis" + self._vc("libVorbis", {"ARTIST": "아버지", "TITLE": "x"})
        codec, vendor, tags = ogg.comment_header(self._ogg([p1, p2]))
        assert codec == "Vorbis" and vendor == "libVorbis"
        assert tags["ARTIST"] == "아버지" and tags["TITLE"] == "x"

    def test_ogg_identification(self):
        from acidcat.core import ogg
        ident = b"vorbis" + struct.pack("<I", 0) + bytes([2]) + struct.pack("<I", 44100)
        p2 = b"vorbis" + self._vc("v", {})
        codec, params = ogg.identification(self._ogg([ident, p2]))
        assert codec == "Vorbis"
        assert params["channels"] == 2 and params["sample_rate"] == 44100

    def test_ogg_malformed_no_crash(self):
        from acidcat.core import ogg
        for bad in (b"OggS", b"OggS" + b"\xff" * 60, b"OggS\x00\x02" + b"\x00" * 30):
            list(ogg.iter_pages(bad))
            ogg.comment_header(bad)  # must not raise


class TestUnicodeMetadata:
    """Regression: inspect must not mangle non-Latin text metadata to U+FFFD
    (the ascii/errors='replace' bug that turned Korean/CJK/etc. into '?')."""

    def test_dtext_utf8_and_latin1_fallback(self):
        from acidcat.commands.inspect import _dtext
        assert _dtext("아버지".encode("utf-8")) == "아버지"
        assert _dtext("⣎⡇ꉪლ".encode("utf-8")) == "⣎⡇ꉪლ"
        assert _dtext(b"caf\xe9") == "café"          # invalid utf-8 -> latin-1, no raise
        assert "�" not in _dtext("아버지".encode("utf-8"))

    def test_wav_info_utf8_roundtrips_through_parse(self):
        from acidcat.commands.inspect import _parse_list
        val = "아버지".encode("utf-8")
        info = b"INFO" + b"INAM" + struct.pack("<I", len(val)) + val
        _, fields, _ = _parse_list(info, {})
        assert any(f["value"] == "아버지" for f in fields)
        assert not any("�" in str(f["value"]) for f in fields)
