"""MP4 box-walk: a large box read only in part is beyond_cap, not truncated."""
import struct

from acidcat.core import mp4


def _boxes(data, **kw):
    return list(mp4.iter_boxes(data, **kw))


def test_large_mdat_beyond_cap_not_truncated():
    ftyp = struct.pack(">I", 16) + b"ftypM4A " + b"\x00" * 4
    mdat = struct.pack(">I", 1000) + b"mdat" + b"\x00" * 992
    full = ftyp + mdat
    # only the first 100 bytes were read, but file_size is the true length
    boxes = _boxes(full[:100], file_size=len(full))
    md = [b for b in boxes if b["type"] == b"mdat"]
    assert md, "mdat should still be enumerated from its header"
    assert md[0]["truncated"] is False
    assert md[0]["beyond_cap"] is True


def test_mdat_overrunning_the_file_is_truncated():
    ftyp = struct.pack(">I", 16) + b"ftypM4A " + b"\x00" * 4
    # mdat claims 9999 bytes but the file only has room for far less
    mdat = struct.pack(">I", 9999) + b"mdat" + b"\x00" * 20
    full = ftyp + mdat
    boxes = _boxes(full, file_size=len(full))
    md = [b for b in boxes if b["type"] == b"mdat"]
    assert md and md[0]["truncated"] is True


def _box(btype, payload):
    return struct.pack(">I", 8 + len(payload)) + btype + payload


def test_gnre_atom_resolves_id3v1_genre():
    # gnre stores type indicator 0 and a u16 of ID3v1 genre index + 1
    # (18 -> Rock); older iTunes wrote genre this way instead of \xa9gen
    data_box = _box(b"data", struct.pack(">II", 0, 0) + struct.pack(">H", 18))
    ilst = _box(b"ilst", _box(b"gnre", data_box))
    meta = _box(b"meta", b"\x00\x00\x00\x00" + ilst)
    tree = _box(b"moov", _box(b"udta", meta))
    assert mp4.parse_ilst(tree)["genre"] == "Rock"


def _stsd(entry):
    # stsd FullBox: version/flags u32 + entry_count u32 + the sample entry
    return _box(b"stsd", struct.pack(">II", 0, 1) + entry)


def test_audio_info_v0_sample_entry():
    body = (b"\x00" * 6 + struct.pack(">H", 1)        # reserved, data_ref_index
            + struct.pack(">HH", 0, 0) + b"\x00" * 4  # version 0, revision, vendor
            + struct.pack(">HHHH", 2, 16, 0, 0)       # channels, bits, cid, pkt
            + struct.pack(">I", 44100 << 16))         # rate, 16.16 fixed
    assert mp4.audio_info(_stsd(_box(b"mp4a", body))) == ("mp4a", 2, 44100)


def test_audio_info_v2_quicktime_sample_entry():
    # a v2 (QuickTime) AudioSampleEntry stores the rate as a float64 at +32
    # and the channel count as a u32 at +40; the v0 offsets there hold the
    # constants 3 / 65536 and previously decoded as 3ch at a garbage rate
    body = (b"\x00" * 6 + struct.pack(">H", 1)
            + struct.pack(">HH", 2, 0) + b"\x00" * 4       # version 2
            + struct.pack(">HHhH", 3, 16, -2, 0)           # v2 constants
            + struct.pack(">I", 0x00010000)                # always 65536
            + struct.pack(">I", 72)                        # sizeOfStructOnly
            + struct.pack(">d", 96000.0)                   # audioSampleRate
            + struct.pack(">I", 6)                         # numAudioChannels
            + b"\x00" * 8)
    assert mp4.audio_info(_stsd(_box(b"lpcm", body))) == ("lpcm", 6, 96000)


# ── esds / codec-config decoding ───────────────────────────────────

def _esds_chain(asc, oti=0x40, max_br=256000, avg_br=192000):
    """Build an ES_Descriptor -> DecoderConfig -> DecoderSpecificInfo chain."""
    dsi = b"\x05" + bytes([len(asc)]) + asc
    dcd = (b"\x04" + bytes([13 + len(dsi)]) + bytes([oti, 0x15])
           + b"\x00\x00\x00" + struct.pack(">II", max_br, avg_br) + dsi)
    return b"\x03" + bytes([3 + len(dcd)]) + b"\x00\x01\x00" + dcd


def test_esds_descriptor_chain_and_asc():
    # AAC LC (aot 2), 44100 (freq index 4), stereo: the classic 0x12 0x10
    info = mp4.parse_esds(_esds_chain(b"\x12\x10"))
    assert info["object_type_indication"] == 0x40
    assert info["stream_type"] == 0x05                 # audio
    assert info["max_bitrate"] == 256000
    assert info["avg_bitrate"] == 192000
    asc = mp4.parse_audio_specific_config(info["dsi"])
    assert asc == {"object_type": 2, "sample_rate": 44100, "channels": 2}


def test_asc_he_aac_explicit_sbr():
    # aot 5 (SBR), core 22050 (index 7), stereo, extension rate 44100 (index 4)
    # bits: 00101 0111 0010 0100 -> 0x2B 0x92 0x00
    asc = mp4.parse_audio_specific_config(b"\x2b\x92\x00")
    assert asc["object_type"] == 5
    assert asc["sample_rate"] == 22050
    assert asc["channels"] == 2
    assert asc["ext_sample_rate"] == 44100


def test_asc_explicit_24bit_rate():
    # freq index 15 = a literal 24-bit rate follows; aot 2, 37800 Hz, mono
    v = (2 << 35) | (15 << 31) | (37800 << 7) | (1 << 3)
    asc = mp4.parse_audio_specific_config(v.to_bytes(5, "big"))
    assert asc["sample_rate"] == 37800 and asc["channels"] == 1


def test_alac_cookie_and_dops():
    cookie = struct.pack(">IBBBBBBHIII", 4096, 0, 16, 40, 10, 14, 2, 255,
                         0, 0, 44100)
    c = mp4.parse_alac_cookie(cookie)
    assert c["frame_length"] == 4096 and c["bit_depth"] == 16
    assert c["channels"] == 2 and c["sample_rate"] == 44100
    # dOps is big-endian (OpusHead in Ogg is little-endian -- same fields)
    dops = (bytes([0, 2]) + struct.pack(">H", 312) + struct.pack(">I", 44100)
            + struct.pack(">h", -256) + bytes([0]))
    d = mp4.parse_dops(dops)
    assert d["pre_skip"] == 312 and d["input_sample_rate"] == 44100
    assert d["output_gain_db"] == -1.0 and d["mapping_family"] == 0


def _audio_entry(codec, esds_asc=None):
    body = (b"\x00" * 6 + struct.pack(">H", 1)
            + struct.pack(">HH", 0, 0) + b"\x00" * 4
            + struct.pack(">HHHH", 2, 16, 0, 0)
            + struct.pack(">I", 44100 << 16))
    if esds_asc is not None:
        body += _box(b"esds", b"\x00\x00\x00\x00" + _esds_chain(esds_asc))
    return _box(codec, body)


def test_sample_entries_enumerates_config_children():
    tree = _box(b"moov", _box(b"trak", _box(b"mdia", _box(b"minf", _box(
        b"stbl", _stsd(_audio_entry(b"mp4a", esds_asc=b"\x12\x10")))))))
    entries = list(mp4.sample_entries(tree))
    assert len(entries) == 1
    e = entries[0]
    assert e["codec"] == b"mp4a" and e["channels"] == 2
    assert e["sample_rate"] == 44100 and e["version"] == 0
    kinds = [c[0] for c in e["children"]]
    assert kinds == [b"esds"]


def test_freeform_atoms_decoded():
    def ff(mean, name, value):
        return _box(b"----",
                    _box(b"mean", b"\x00\x00\x00\x00" + mean)
                    + _box(b"name", b"\x00\x00\x00\x00" + name)
                    + _box(b"data", struct.pack(">II", 1, 0) + value))
    ilst = _box(b"ilst", ff(b"com.serato.dj", b"bpm", b"128")
                + ff(b"com.apple.iTunes", b"iTunNORM", b"0000 1234"))
    tree = _box(b"moov", _box(b"udta", _box(
        b"meta", b"\x00\x00\x00\x00" + ilst)))
    meta = mp4.parse_ilst(tree)
    assert meta["com.serato.dj:bpm"] == "128"
    assert meta["iTunNORM"] == "0000 1234"    # apple namespace elided


def test_walker_descends_stsd(tmp_path):
    from acidcat.core.walk.mp4 import inspect_mp4
    ftyp = _box(b"ftyp", b"M4A \x00\x00\x00\x00")
    tree = ftyp + _box(b"moov", _box(b"trak", _box(b"mdia", _box(
        b"minf", _box(b"stbl", _stsd(_audio_entry(b"mp4a",
                                                  esds_asc=b"\x12\x10")))))))
    p = tmp_path / "t.m4a"
    p.write_bytes(tree)
    chunks, warns = inspect_mp4(str(p))
    ids = [c["id"] for c in chunks]
    assert "mp4a" in ids and "esds" in ids            # entries now in the tree
    esds = next(c for c in chunks if c["id"] == "esds")
    vals = {f["name"]: f["value"] for f in esds["fields"]}
    assert vals["aac_object_type"] == 2               # AAC LC
    assert vals["asc_sample_rate"] == 44100
    tags = next(c for c in chunks if c["id"] == "tags")
    codec = next(f["value"] for f in tags["fields"] if f["name"] == "codec")
    assert codec.startswith("AAC LC")                 # profile from the esds


def _full_box(btype, version_flags, payload):
    return _box(btype, struct.pack(">I", version_flags) + payload)


def test_stco_entries_annotated_as_xref():
    from acidcat.core.walk.mp4 import inspect_mp4
    import tempfile, os
    # stco with two chunk offsets: one valid, one dangling past EOF
    entries = struct.pack(">II", 0x40, 0xFFFFFFF0)
    stco = _full_box(b"stco", 0, struct.pack(">I", 2) + entries)
    stbl = _box(b"stbl", stco)
    minf = _box(b"minf", stbl)
    mdia = _box(b"mdia", minf)
    trak = _box(b"trak", mdia)
    moov = _box(b"moov", trak)
    ftyp = _box(b"ftyp", b"M4A " + b"\x00" * 4)
    blob = ftyp + moov + _box(b"mdat", b"\x00" * 64)
    fd, path = tempfile.mkstemp(suffix=".m4a")
    os.write(fd, blob)
    os.close(fd)
    try:
        chunks, warns = inspect_mp4(path)
    finally:
        os.unlink(path)
    stco_chunk = next(c for c in chunks if c["id"] == "stco")
    xrefs = [f["xref"] for f in stco_chunk["fields"] if "xref" in f]
    assert 0x40 in xrefs
    assert "dangling" in stco_chunk["summary"]
    assert any("past EOF" in w for w in stco_chunk["warnings"])
