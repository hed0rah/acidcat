"""Coverage for the previously-untested container walkers: RF64/BW64 (64-bit
RIFF via ds64), AIFC (compressed AIFF), and Ogg Opus. The 2026-07-04 audit
flagged these as walkers-with-no-fixtures; these synthesize minimal valid files
and assert the decode."""
import struct

from acidcat.core.walk import walk_file


def _field(chunks, name):
    for c in chunks:
        for f in c.get("fields", []) or []:
            if f.get("name") == name:
                return f.get("value")
    return None


def test_rf64_reads_real_sizes_from_ds64(tmp_path):
    # RF64 uses 0xFFFFFFFF size sentinels; the true sizes live in the ds64 chunk
    ds64 = b"ds64" + struct.pack("<I", 28) + struct.pack("<QQQI", 0, 2000, 500, 0)
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
    data = b"data" + struct.pack("<I", 0xFFFFFFFF) + bytes(2000)
    body = b"WAVE" + ds64 + fmt + data
    rf64 = b"RF64" + struct.pack("<I", 0xFFFFFFFF) + body
    f = tmp_path / "big.wav"
    f.write_bytes(rf64)
    label, chunks, warns = walk_file(str(f))
    assert label.startswith("RF64")
    assert str(_field(chunks, "data_size")).replace(",", "") == "2000"
    assert int(str(_field(chunks, "sample_rate")).replace(",", "")) == 44100


def test_aifc_decodes_comm(tmp_path):
    rate80 = bytes.fromhex("400EAC44000000000000")            # 44100 Hz, 80-bit ext
    comm_body = (struct.pack(">H", 2) + struct.pack(">I", 500) + struct.pack(">H", 16)
                 + rate80 + b"NONE" + bytes([0, 0]))          # compression type + empty name
    comm = b"COMM" + struct.pack(">I", len(comm_body)) + comm_body
    fver = b"FVER" + struct.pack(">I", 4) + struct.pack(">I", 0xA2805140)
    ssnd = b"SSND" + struct.pack(">I", 8 + 16) + struct.pack(">II", 0, 0) + bytes(16)
    body = b"AIFC" + fver + comm + ssnd
    aifc = b"FORM" + struct.pack(">I", len(body)) + body
    f = tmp_path / "c.aifc"
    f.write_bytes(aifc)
    label, chunks, warns = walk_file(str(f))
    assert "AIFC" in label
    assert _field(chunks, "num_channels") == 2
    assert _field(chunks, "sample_rate") == 44100
    assert not warns                                          # SSND present, clean


def _ogg_page(serial, gran, htype, seq, packet):
    seg = []
    rem = len(packet)
    while rem >= 255:
        seg.append(255)
        rem -= 255
    seg.append(rem)
    return (b"OggS" + bytes([0, htype]) + struct.pack("<q", gran)
            + struct.pack("<I", serial) + struct.pack("<I", seq) + bytes(4)
            + bytes([len(seg)]) + bytes(seg) + packet)


def test_ogg_opus_identity_and_duration(tmp_path):
    head = (b"OpusHead" + bytes([1, 2]) + struct.pack("<H", 312)
            + struct.pack("<I", 48000) + struct.pack("<h", 0) + bytes([0]))
    tags = b"OpusTags" + struct.pack("<I", 4) + b"acid" + struct.pack("<I", 0)
    # the final granule includes the 312 pre_skip priming samples; playable
    # duration is (48312 - 312) / 48000 = exactly 1 s
    ogg = (_ogg_page(7, 0, 2, 0, head) + _ogg_page(7, 0, 0, 1, tags)
           + _ogg_page(7, 48312, 0, 2, bytes(40)))
    f = tmp_path / "o.opus"
    f.write_bytes(ogg)
    label, chunks, warns = walk_file(str(f))
    assert label.startswith("Ogg")
    assert _field(chunks, "codec") == "Opus"
    assert _field(chunks, "channels") == 2
    assert _field(chunks, "sample_rate") == 48000             # decode rate, not input
    assert _field(chunks, "pre_skip") == 312
    dur = _field(chunks, "duration")
    assert dur is not None and dur.startswith("1.000")        # opus granule at 48 kHz


def test_ogg_chained_streams_duration_scoped_to_first(tmp_path):
    head = (b"OpusHead" + bytes([1, 2]) + struct.pack("<H", 0)
            + struct.pack("<I", 48000) + struct.pack("<h", 0) + bytes([0]))
    tags = b"OpusTags" + struct.pack("<I", 4) + b"acid" + struct.pack("<I", 0)
    # a second logical bitstream (serial 99) with a huge granule must not
    # inflate the first stream's duration
    ogg = (_ogg_page(7, 0, 2, 0, head) + _ogg_page(7, 0, 0, 1, tags)
           + _ogg_page(7, 96000, 0, 2, bytes(40))
           + _ogg_page(99, 480000, 2, 0, bytes(40)))
    f = tmp_path / "chained.opus"
    f.write_bytes(ogg)
    label, chunks, warns = walk_file(str(f))
    dur = _field(chunks, "duration")
    assert dur is not None and dur.startswith("2.000")        # 96000/48000, not 10 s
    assert any("logical bitstreams" in w for w in warns)


def test_fxp_vst_preset(tmp_path):
    # VST2 .fxp: CcnK + FPCh (opaque-chunk preset), plugin id, 28-byte name
    name = b"My Preset".ljust(28, b"\x00")
    data = (b"CcnK" + struct.pack(">I", 100) + b"FPCh" + struct.pack(">I", 1)
            + b"XfsX" + struct.pack(">I", 1) + struct.pack(">I", 1) + name
            + struct.pack(">I", 8) + bytes(8))
    f = tmp_path / "p.fxp"
    f.write_bytes(data)
    label, chunks, warns = walk_file(str(f))
    assert "FXP" in label
    assert "XfsX" in str(_field(chunks, "plugin_id"))
    assert "My Preset" in str(_field(chunks, "preset_name"))


def test_rx2_recycle_loop(tmp_path):
    # RX2: CAT/REX2 group; slice markers (SLCE) live in a nested CAT/SLCL group
    def bu32(n):
        return struct.pack(">I", n)

    def chunk(cid, body):
        return cid + bu32(len(body)) + body
    slce = chunk(b"SLCE", b"")
    slcl = b"CAT " + bu32(len(b"SLCL" + slce + slce)) + b"SLCL" + slce + slce
    body = b"REX2" + chunk(b"CREI", b"ReCycle Test") + slcl
    data = b"CAT " + bu32(len(body)) + body
    f = tmp_path / "loop.rx2"
    f.write_bytes(data)
    label, chunks, warns = walk_file(str(f))
    assert "RX2" in label
    assert _field(chunks, "slices") == 2
    assert "ReCycle Test" in str(_field(chunks, "creator"))


def test_rmid_riff_wrapped_midi(tmp_path):
    # RMID: a Standard MIDI File wrapped in a RIFF 'data' chunk
    midi = (b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96)
            + b"MTrk" + struct.pack(">I", 4) + bytes([0x00, 0xFF, 0x2F, 0x00]))
    body = b"RMID" + b"data" + struct.pack("<I", len(midi)) + midi
    data = b"RIFF" + struct.pack("<I", len(body)) + body
    f = tmp_path / "w.rmid"
    f.write_bytes(data)
    label, chunks, warns = walk_file(str(f))
    assert "RMID" in label
    ids = [str(c["id"]).strip() for c in chunks]
    assert "RIFF" in ids           # the wrapper
    assert "MThd" in ids and "MTrk" in ids   # the delegated inner MIDI


def _wav_with_chunk(cid, payload):
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
    data = b"data" + struct.pack("<I", 4) + bytes(4)
    pad = b"\x00" if len(payload) & 1 else b""
    ck = cid + struct.pack("<I", len(payload)) + payload + pad
    body = b"WAVE" + fmt + data + ck
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_wav_cart_chunk(tmp_path):
    cart = bytearray(2048)
    cart[0x04:0x04 + 6] = b"Jingle"          # title
    cart[0x44:0x44 + 7] = b"Station"         # artist
    f = tmp_path / "c.wav"
    f.write_bytes(_wav_with_chunk(b"cart", bytes(cart)))
    label, chunks, warns = walk_file(str(f))
    cartck = next(c for c in chunks if str(c["id"]).strip() == "cart")
    assert "Jingle" in cartck["summary"]
    assert _field(chunks, "title") == "Jingle"
    assert _field(chunks, "artist") == "Station"


def test_wav_ixml_chunk(tmp_path):
    xml = b"<BWFXML><SCENE>12A</SCENE><TAKE>3</TAKE><NOTE>windy</NOTE></BWFXML>"
    f = tmp_path / "x.wav"
    f.write_bytes(_wav_with_chunk(b"iXML", xml))
    label, chunks, warns = walk_file(str(f))
    assert _field(chunks, "scene") == "12A"
    assert _field(chunks, "take") == "3"


def test_wav_data_before_fmt_flagged(tmp_path):
    # data-before-fmt violates RIFF chunk order; the walker already warns and the
    # scan surfaces it, so no separate detector is needed.
    from acidcat.core import anomalies
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
    data = b"data" + struct.pack("<I", 4) + bytes(4)
    f = tmp_path / "dbf.wav"
    body = b"WAVE" + data + fmt          # data BEFORE fmt (spec violation)
    f.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    label, chunks, warns = walk_file(str(f))
    findings = anomalies.scan(str(f), label, chunks, warns)
    assert any("fmt appears after data" in x["message"] for x in findings)
    # normal order produces no such finding
    g = tmp_path / "ok.wav"
    body2 = b"WAVE" + fmt + data
    g.write_bytes(b"RIFF" + struct.pack("<I", len(body2)) + body2)
    la, ch, wa = walk_file(str(g))
    assert not any("fmt appears after data" in x["message"]
                   for x in anomalies.scan(str(g), la, ch, wa))


def test_wt_bitwig_wavetable(tmp_path):
    # vawt: 12-byte LE header + frame_count*frame_samples int16 LE samples
    frame_samples, frame_count = 2048, 3
    header = b"vawt" + struct.pack("<IHH", frame_samples, frame_count, 12)
    data = bytes(frame_count * frame_samples * 2)
    f = tmp_path / "t.wt"
    f.write_bytes(header + data)
    label, chunks, warns = walk_file(str(f))
    assert "wavetable" in label.lower()
    assert not warns                                    # size matches header exactly
    assert _field(chunks, "frame_samples") == 2048
    assert _field(chunks, "frame_count") == 3
    assert _field(chunks, "magic") == "vawt"

def test_multisample_bitwig(tmp_path):
    # a .multisample is a zip: multisample.xml manifest + member sample files
    import zipfile
    xml = ('<?xml version="1.0"?><multisample name="Kit">'
           '<generator>test</generator><category>Drums</category>'
           '<sample file="a.wav"><key root="36" low="36" high="40"/>'
           '<loop mode="off"/></sample>'
           '<sample file="b.wav"><key root="48" low="41" high="52"/></sample>'
           '</multisample>')
    f = tmp_path / "k.multisample"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("multisample.xml", xml)
        z.writestr("a.wav", b"RIFF____WAVE")
        z.writestr("b.wav", b"RIFF____WAVE")
    label, chunks, warns = walk_file(str(f))
    assert "multisample" in label.lower()
    assert _field(chunks, "name") == "Kit"
    assert _field(chunks, "sample_zones") == 2
    assert _field(chunks, "member_files") == 2
    zones = [c for c in chunks if c["id"] == "zone"]
    assert len(zones) == 2
    assert _field(chunks, "root") == "36"          # first zone's root note
    assert _field(chunks, "key_range") == "36-40"
