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
    ogg = (_ogg_page(7, 0, 2, 0, head) + _ogg_page(7, 0, 0, 1, tags)
           + _ogg_page(7, 48000, 0, 2, bytes(40)))            # granule 48000 -> 1.000 s
    f = tmp_path / "o.opus"
    f.write_bytes(ogg)
    label, chunks, warns = walk_file(str(f))
    assert label.startswith("Ogg")
    assert _field(chunks, "codec") == "Opus"
    assert _field(chunks, "channels") == 2
    dur = _field(chunks, "duration")
    assert dur is not None and dur.startswith("1.000")        # opus granule at 48 kHz
