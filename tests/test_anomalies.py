"""Tests for `inspect --anomalies` (core.anomalies)."""
import io
import struct
import zipfile

from acidcat.core import anomalies
from acidcat.core.walk import walk_file


def _chunk(cid, p):
    return cid + struct.pack("<I", len(p)) + p + (b"\x00" if len(p) % 2 else b"")


def _wav(*chunks):
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


_FMT = _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16))


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _scan(path):
    fmt, chunks, warns = walk_file(path, deep=False)
    return anomalies.scan(path, fmt, chunks, warns)


def test_clean_wav_has_no_anomalies(tmp_path):
    path = _write(tmp_path, "clean.wav", _wav(_FMT, _chunk(b"data", b"\x00" * 32)))
    findings = _scan(path)
    assert not any(f["rule"] in ("polyglot", "trailing_data") for f in findings)


def test_wav_zip_polyglot_flagged(tmp_path):
    wav = _wav(_FMT, _chunk(b"data", b"\x00" * 32))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("hidden.txt", b"payload")
    path = _write(tmp_path, "poly.wav", wav + buf.getvalue())
    findings = _scan(path)
    rules = {f["rule"] for f in findings}
    assert "polyglot" in rules and "trailing_data" in rules
    assert any(f["severity"] == "alert" and "ZIP" in f["message"] for f in findings)


def test_trailing_junk_flagged_without_polyglot(tmp_path):
    wav = _wav(_FMT, _chunk(b"data", b"\x00" * 32))
    path = _write(tmp_path, "trail.wav", wav + b"just some trailing text bytes")
    findings = _scan(path)
    rules = {f["rule"] for f in findings}
    assert "trailing_data" in rules and "polyglot" not in rules


def test_lsb_clean_vs_stego(tmp_path):
    import math
    import random
    from acidcat.core import lsb

    def wav(samples):
        pcm = b"".join(struct.pack("<h", max(-32768, min(32767, int(s)))) for s in samples)
        return _wav(_chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)),
                    _chunk(b"data", pcm))
    N = 20000
    clean = [6000 * math.sin(2 * math.pi * 220 * i / 44100) if i < N // 2 else 0
             for i in range(N)]
    rnd = random.Random(1)
    stego = [int(v) & ~1 | rnd.getrandbits(1) for v in clean]
    for name, samples, expect in (("c.wav", clean, False), ("s.wav", stego, True)):
        path = _write(tmp_path, name, wav(samples))
        fmt, chunks, warns = walk_file(path, deep=False)
        r = lsb.analyze(path, fmt, chunks)
        assert r is not None and r["uniform_high"] is expect


def _id3_dup_mp3():
    def fr(fid, text):
        b = b"\x03" + text.encode()
        return fid + struct.pack(">I", len(b)) + b"\x00\x00" + b
    frames = fr(b"TIT2", "one") + fr(b"TIT2", "two") + fr(b"TPE1", "a")
    n = len(frames)
    ss = bytes([(n >> 21) & 0x7f, (n >> 14) & 0x7f, (n >> 7) & 0x7f, n & 0x7f])
    return b"ID3\x03\x00\x00" + ss + frames + (b"\xff\xfb\x90\x00" + b"\x00" * 413) * 8


def test_duplicate_id3_frame_flagged(tmp_path):
    path = _write(tmp_path, "dup.mp3", _id3_dup_mp3())
    findings = _scan(path)
    dups = [f for f in findings if f["rule"] == "duplicate_frame" and "TIT2" in f["message"]]
    assert dups


def test_nonzero_padding_flagged(tmp_path):
    # a synthetic FLAC-shaped PADDING chunk whose content is non-zero
    path = _write(tmp_path, "pad.bin", b"\xaa" * 16)
    chunks = [{"id": "PADDING", "offset": 0, "size": 16, "payload_base": 0, "fields": []}]
    findings = anomalies.scan(path, "FLAC", chunks, [])
    assert any(f["rule"] == "cavity_content" for f in findings)


def test_appended_zip_polyglot_on_headerless_format(tmp_path):
    # a headerless carrier (no total-size header) + an appended zip: the
    # universal ZIP-EOCD scan must still catch it.
    import io, zipfile
    carrier = bytes([0xff, 0xfb, 0x90, 0x00]) + bytes(413)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.txt", b"hi")
    path = _write(tmp_path, "poly.mp3", carrier + buf.getvalue())
    try:
        fmt, chunks, warns = walk_file(path, deep=False)
    except Exception:
        fmt, chunks, warns = "?", [], []
    findings = anomalies.scan(path, fmt, chunks, warns)
    assert any(f["rule"] == "polyglot" for f in findings)


def _ogg_two_bos():
    # two BOS pages (header_type bit 0x02) with distinct serials -> two logical
    # bitstreams. built with bytes([...]) to stay heredoc/backslash-safe.
    def page(serial, htype, packet):
        seg = []
        rem = len(packet)
        while rem >= 255:
            seg.append(255)
            rem -= 255
        seg.append(rem)
        hdr = (b"OggS" + bytes([0, htype]) + bytes(8) + struct.pack("<I", serial)
               + struct.pack("<I", 0) + bytes(4) + bytes([len(seg)]) + bytes(seg))
        return hdr + packet
    vorbis = bytes([1]) + b"vorbis" + bytes(20)
    opus = b"OpusHead" + bytes(11)
    return page(1, 2, vorbis) + page(2, 2, opus)


def test_ogg_multistream_flagged(tmp_path):
    path = _write(tmp_path, "dual.ogg", _ogg_two_bos())
    findings = anomalies.scan(path, "Ogg Vorbis", [], [])
    assert any(f["rule"] == "ogg_multistream" for f in findings)


def test_ogg_single_stream_not_flagged(tmp_path):
    # one BOS page only -> no multistream flag
    def page(serial, htype, packet):
        seg = [len(packet)]
        hdr = (b"OggS" + bytes([0, htype]) + bytes(8) + struct.pack("<I", serial)
               + struct.pack("<I", 0) + bytes(4) + bytes([1]) + bytes(seg))
        return hdr + packet
    data = page(1, 2, bytes([1]) + b"vorbis" + bytes(20))
    path = _write(tmp_path, "solo.ogg", data)
    findings = anomalies.scan(path, "Ogg Vorbis", [], [])
    assert not any(f["rule"] == "ogg_multistream" for f in findings)


def _wav_with_junk(junk_body):
    junk = b"JUNK" + struct.pack("<I", len(junk_body)) + junk_body
    if len(junk) & 1:
        junk += b"\x00"
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, 8000, 8000, 1, 8)
    data = b"data" + struct.pack("<I", 4) + bytes(4)
    body = b"WAVE" + junk + fmt + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_junk_cavity_nonzero_flagged(tmp_path):
    from acidcat.core.walk import walk_file
    path = _write(tmp_path, "junk.wav", _wav_with_junk(b"HIDDEN-PAYLOAD" + bytes(20)))
    label, chunks, warns = walk_file(path)
    findings = anomalies.scan(path, label, chunks, warns)
    assert any(f["rule"] == "cavity_content" and "JUNK" in f["message"] for f in findings)


def test_junk_all_zero_not_flagged(tmp_path):
    from acidcat.core.walk import walk_file
    path = _write(tmp_path, "pad.wav", _wav_with_junk(bytes(32)))
    label, chunks, warns = walk_file(path)
    findings = anomalies.scan(path, label, chunks, warns)
    assert not any(f["rule"] == "cavity_content" for f in findings)
