"""SoundFont 2 parsing + sample extraction, on a synthetic ground-truth sfbk."""
import struct

import pytest

from acidcat.core import sf2


def _riff(form, *chunks):
    body = form + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _chunk(cid, payload):
    return cid + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")


def _list(ltype, *chunks):
    body = ltype + b"".join(chunks)
    return b"LIST" + struct.pack("<I", len(body)) + body


def _shdr_rec(name, start, end, ls, le, rate, pitch=60, stype=1):
    return (name.encode("latin-1").ljust(20, b"\x00")[:20]
            + struct.pack("<IIIII", start, end, ls, le, rate)
            + struct.pack("<BbHH", pitch, 0, 0, stype))


def _make_sf2(samples, smpl_frames):
    # smpl: `smpl_frames` 16-bit samples; each shdr indexes into it
    smpl = b"".join(struct.pack("<h", (i % 100) - 50) for i in range(smpl_frames))
    info = _list(b"INFO",
                 _chunk(b"ifil", struct.pack("<HH", 2, 1)),
                 _chunk(b"INAM", b"Test Font\x00"),
                 _chunk(b"IENG", b"acidcat\x00"))
    sdta = _list(b"sdta", _chunk(b"smpl", smpl))
    shdr = b"".join(_shdr_rec(*s) for s in samples) + _shdr_rec("EOS", 0, 0, 0, 0, 0)
    pdta = _list(b"pdta", _chunk(b"shdr", shdr))
    return _riff(b"sfbk", info, sdta, pdta)


def test_parse_info_and_samples():
    data = _make_sf2([("Kick", 0, 100, 10, 90, 44100),
                      ("Snare", 150, 400, 160, 390, 22050, 64, 4)], 500)
    r = sf2.parse_sf2(data)
    assert r["version"] == "2.1"
    assert r["info"]["name"] == "Test Font" and r["info"]["engineer"] == "acidcat"
    assert r["sample_count"] == 2
    k = r["samples"][0]
    assert k["name"] == "Kick" and k["rate"] == 44100 and k["start"] == 0 and k["end"] == 100
    assert r["samples"][1]["type"] == 4       # left channel


def test_sample_wav_carves_correct_pcm():
    data = _make_sf2([("Kick", 10, 60, 0, 0, 44100)], 500)
    r = sf2.parse_sf2(data)
    wav = sf2.sample_wav(data, r["smpl_offset"], r["samples"][0])
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    # the data chunk is exactly (end-start)*2 bytes and equals smpl[10*2:60*2]
    di = wav.find(b"data")
    dsize = struct.unpack_from("<I", wav, di + 4)[0]
    assert dsize == (60 - 10) * 2
    carved = wav[di + 8: di + 8 + dsize]
    smpl = data[r["smpl_offset"]: r["smpl_offset"] + r["smpl_size"]]
    assert carved == smpl[10 * 2: 60 * 2]
    # the WAV's fmt rate matches the sample
    tag, ch, rate, _br, _ba, bits = struct.unpack_from("<HHIIHH", wav, 20)
    assert tag == 1 and ch == 1 and rate == 44100 and bits == 16


def test_lying_shdr_index_skipped():
    # a header whose end index runs past the sample data must not carve garbage
    data = _make_sf2([("Good", 0, 50, 0, 0, 44100),
                      ("Bad", 0, 99999, 0, 0, 44100)], 200)
    r = sf2.parse_sf2(data)
    names = [s["name"] for s in r["samples"]]
    assert names == ["Good"]                  # the out-of-range one dropped


def test_not_sf2_raises():
    with pytest.raises(sf2.Sf2Error):
        sf2.parse_sf2(b"RIFF" + struct.pack("<I", 4) + b"WAVE")


def test_walker_and_sniff():
    from acidcat.core import sniff
    from acidcat.core.walk.sf2 import inspect_sf2
    import tempfile, os
    data = _make_sf2([("Kick", 0, 100, 0, 0, 44100),
                      ("Snare", 100, 300, 0, 0, 22050)], 400)
    assert sniff.sniff_bytes(data[:16]) == "sf2"
    p = os.path.join(tempfile.mkdtemp(), "t.sf2")
    with open(p, "wb") as f:
        f.write(data)
    chunks, warns = inspect_sf2(p)
    ids = [c["id"] for c in chunks]
    assert "sfbk" in ids and "smpl" in ids and "smp[0]" in ids
    smp0 = next(c for c in chunks if c["id"] == "smp[0]")
    assert "Kick" in smp0["summary"]
    # the sample's chunk offset is its real byte position in smpl (carveable)
    smpl = next(c for c in chunks if c["id"] == "smpl")
    assert smp0["offset"] == smpl["offset"] + 0


def test_convert_extracts_samples(tmp_path):
    from acidcat.commands import convert

    class A:
        def __init__(self, inp, out):
            self.input, self.output, self.division = inp, out, 480
            self.skip_existing = self.quiet = False
    data = _make_sf2([("Kick", 0, 100, 0, 0, 44100),
                      ("Snare/Rim", 100, 300, 0, 0, 22050)], 400)  # name has a /
    p = tmp_path / "f.sf2"
    p.write_bytes(data)
    outdir = str(tmp_path / "out")
    assert convert.run(A(str(p), outdir)) == 0
    import os
    wavs = sorted(os.listdir(outdir))
    assert len(wavs) == 2
    assert wavs[0].startswith("0000_Kick") and wavs[0].endswith(".wav")
    assert "/" not in wavs[1]                  # reserved char sanitized
    assert open(os.path.join(outdir, wavs[0]), "rb").read()[:4] == b"RIFF"


# ── SF3 (Ogg Vorbis samples) + unpadded-chunk robustness ───────────

def test_sf3_samples_are_ogg_ranges():
    # two compressed samples: type bit 0x10 set, start/end are byte offsets
    smpl_frames = 12                       # smpl_size = 24 bytes
    samples = [("kick", 0, 10, 0, 0, 44100, 60, 0x11),
               ("snare", 10, 24, 0, 0, 44100, 60, 0x11)]
    data = _make_sf2(samples, smpl_frames)
    info = sf2.parse_sf2(data)
    assert info["sf3"] is True
    assert info["sample_count"] == 2
    s0, s1 = info["samples"]
    assert s0["compressed"] and s0["byte_len"] == 10
    assert s1["byte_len"] == 14
    # the raw Ogg stream comes out as the exact smpl slice
    assert sf2.sample_bytes(data, s0) == data[s0["byte_off"]:s0["byte_off"] + 10]
    # a compressed sample cannot be emitted as a PCM WAV
    with pytest.raises(sf2.Sf2Error):
        sf2.sample_wav(data, info["smpl_offset"], s0)


def test_unpadded_odd_chunk_still_walks():
    # a writer (MuseScore SF3) that omits the RIFF pad byte after an odd chunk
    odd = b"aaa" + b"AAA"                   # 3-byte payload, odd
    a = b"AAAA" + struct.pack("<I", 3) + b"aaa"   # no pad byte
    b = b"BBBB" + struct.pack("<I", 4) + b"bbbb"
    blob = a + b
    got = list(sf2._iter_riff(blob, 0, len(blob)))
    ids = [cid for cid, _, _ in got]
    assert ids == [b"AAAA", b"BBBB"]
