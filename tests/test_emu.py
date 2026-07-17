"""E-MU E4B (EOS 4.x) bank walker: the FORM E4B0 container, the TOC1 cross-check,
and the preset -> voice -> zone -> sample-index resolution.

The fixture is synthesized here from the documented byte layout (verified
against a writer-generated bank) -- no real, copyrighted E-MU content."""
import struct

import pytest

from acidcat.core.sniff import sniff, sniff_bytes
from acidcat.core.walk import emu, walk_file
from acidcat.core.walk.base import Unsupported


def _iff(tag, body):
    out = tag + struct.pack(">I", len(body)) + body
    return out + (b"\x00" if len(body) & 1 else b"")


def _name16(s):
    return s.encode("ascii")[:16].ljust(16, b" ")


def _toc_entry(tag, data_size, file_offset, index, name):
    return (tag + struct.pack(">I", data_size) + struct.pack(">I", file_offset)
            + struct.pack(">H", index) + _name16(name) + b"\x00\x00")  # 32 bytes


def _sample_chunk(idx, name, sample_rate, frames, loop=False):
    hdr = bytearray(94)
    struct.pack_into(">H", hdr, 0, idx)
    hdr[2:18] = _name16(name)
    struct.pack_into("<I", hdr, 54, sample_rate)
    struct.pack_into("<H", hdr, 60, 0x21 if loop else 0x20)
    if loop:
        struct.pack_into("<I", hdr, 38, 92)
        struct.pack_into("<I", hdr, 46, 92 + frames * 2)
    return _iff(b"E3S1", bytes(hdr) + b"\x00\x00" * frames)


def _voice(zones):
    v = bytearray(284)
    struct.pack_into(">H", v, 2, 284 + len(zones) * 22)   # zone-table trailer offset
    v[4] = len(zones)
    zb = bytearray()
    for lo, hi, sidx, root in zones:
        z = bytearray(22)
        z[2], z[5] = lo, hi
        struct.pack_into(">H", z, 10, sidx)
        z[14] = root
        zb += z
    return bytes(v) + bytes(zb)


def _preset_chunk(index, name, voices):
    hdr = bytearray(82)
    struct.pack_into(">H", hdr, 0, index)
    hdr[2:18] = _name16(name)
    struct.pack_into(">H", hdr, 20, len(voices))
    hdr[28] = 120
    return _iff(b"E4P1", bytes(hdr) + b"".join(voices) + b"\x00\x00")


def _make_e4b(tmp_path, name="TESTBANK",
              samples=(("KICK", 44100, 64, True), ("SNARE", 22050, 32, False)),
              corrupt_preset_size=False):
    """Build a minimal valid FORM E4B0 bank: E4Ma + one preset (one voice, one
    zone per sample) + one E3S1 per sample + a trailing EMSt."""
    e4ma = _iff(b"E4Ma", b"\x00" * 256)
    zones = [(0 if i == 0 else 64, 63 if i == 0 else 127, i + 1, 60)
             for i in range(len(samples))]
    preset = _preset_chunk(0, "TEST PRESET", [_voice(zones)])
    if corrupt_preset_size:
        preset = preset[:4] + struct.pack(">I", struct.unpack_from(">I", preset, 4)[0]
                                          - 4) + preset[8:]
    samp_chunks = [_sample_chunk(i + 1, nm, sr, fr, lp)
                   for i, (nm, sr, fr, lp) in enumerate(samples)]
    emst = _iff(b"EMSt", b"\x00" * 1366)

    n_entries = 1 + 1 + len(samp_chunks)
    toc1_len = 8 + n_entries * 32
    off = 12 + toc1_len
    entries = [_toc_entry(b"E4Ma", 256, off, 0, "Multimap")]
    off += len(e4ma)
    entries.append(_toc_entry(b"E4P1", len(preset) - 8, off, 0, "TEST PRESET"))
    off += len(preset)
    for i, (nm, sr, fr, lp) in enumerate(samples):
        entries.append(_toc_entry(b"E3S1", len(samp_chunks[i]) - 8, off, i + 1, nm))
        off += len(samp_chunks[i])
    toc1 = _iff(b"TOC1", b"".join(entries))

    body = b"E4B0" + toc1 + e4ma + preset + b"".join(samp_chunks) + emst
    blob = b"FORM" + struct.pack(">I", len(body) - 4) + body   # E-MU: filesize-12
    p = tmp_path / (name + ".e4b")
    p.write_bytes(blob)
    return str(p)


def test_e4b_sniffs_by_form_type():
    assert sniff_bytes(b"FORM\x00\x00\x00\x00E4B0") == "e4b"
    assert sniff_bytes(b"FORM\x00\x00\x00\x00AIFF") == "aiff"   # unchanged
    assert sniff_bytes(b"FORM\x00\x00\x00\x00AIFC") == "aifc"


def test_e4b_happy_path(tmp_path):
    p = _make_e4b(tmp_path)
    assert sniff(p) == "e4b"
    fmt, chunks, warns = walk_file(p)
    assert fmt == "E-MU Emulator 4 / EOS bank"
    assert warns == []
    ids = [c["id"] for c in chunks]
    assert ids == ["FORM", "TOC1", "E4Ma", "E4P1[0]", "E3S1[0]", "E3S1[1]", "EMSt"]

    form = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert form["form_type"] == "E4B0"


def test_e4b_form_size_field_matches_convention(tmp_path):
    p = _make_e4b(tmp_path)
    _, chunks, _ = walk_file(p)
    size_field = next(f for f in chunks[0]["fields"] if f["name"] == "form_size")
    # verify enc/raw re-encode to the exact on-disk bytes at offset 4
    raw = open(p, "rb").read()
    assert struct.pack(size_field["enc"], size_field["raw"]) == raw[4:8]
    assert size_field["raw"] == len(raw) - 12


def test_e4b_preset_resolves_sample_names_and_keys(tmp_path):
    p = _make_e4b(tmp_path)
    _, chunks, _ = walk_file(p)
    preset = next(c for c in chunks if c["id"] == "E4P1[0]")
    fields = {f["name"]: f for f in preset["fields"]}
    assert fields["num_voices"]["value"] == 1
    assert fields["zones"]["value"] == 2
    assert fields["sample[0]"]["value"] == "KICK"
    assert "0-63" in fields["sample[0]"]["note"]
    assert fields["sample[1]"]["value"] == "SNARE"


def test_e4b_sample_header_fields(tmp_path):
    p = _make_e4b(tmp_path)
    _, chunks, _ = walk_file(p)
    kick = next(c for c in chunks if c["id"] == "E3S1[0]")
    f = {x["name"]: x for x in kick["fields"]}
    assert f["sample_rate"]["raw"] == 44100
    assert f["frames"]["value"] == "64"
    assert f["bit_depth"]["value"] == 16
    assert int(f["options"]["raw"]) & 1 == 1        # KICK is looped
    assert "loop_start" in f
    snare = next(c for c in chunks if c["id"] == "E3S1[1]")
    fs = {x["name"]: x for x in snare["fields"]}
    assert int(fs["options"]["raw"]) & 1 == 0       # SNARE is not looped
    assert "loop_start" not in fs


def test_e4b_toc_cross_check_flags_corruption(tmp_path):
    """A preset whose own size field disagrees with the TOC offsets desyncs the
    chain: the walker must degrade (no raise) and flag both the desync and the
    TOC mismatch."""
    p = _make_e4b(tmp_path, corrupt_preset_size=True)
    fmt, chunks, warns = walk_file(p)          # must not raise
    assert fmt == "E-MU Emulator 4 / EOS bank"
    joined = " ".join(warns)
    assert "desync" in joined
    toc = next(c for c in chunks if c["id"] == "TOC1")
    assert any("TOC" in w for w in toc["warnings"])


def test_e4b_truncated_bank_degrades(tmp_path):
    p = _make_e4b(tmp_path)
    raw = open(p, "rb").read()
    trunc = tmp_path / "trunc.e4b"
    trunc.write_bytes(raw[:200])                # chop mid-bank
    fmt, chunks, warns = walk_file(str(trunc))  # must not raise
    assert chunks[0]["id"] == "FORM"


def test_e4b_rejects_non_emu(tmp_path):
    p = tmp_path / "x.e4b"
    p.write_bytes(b"FORM\x00\x00\x00\x00AIFF" + b"\x00" * 32)
    with pytest.raises(Unsupported):
        emu.inspect_emu(str(p))


# ── E5B0 (Emulator X / Proteus X) ────────────────────────────────────

def _wname(s):
    return s.encode("utf-16-le")


def _e5_sample(idx, name, sample_rate, pcm_frames=8):
    body = bytearray(0x6e)
    body[4] = 1                                   # the observed 01 00 flag
    nm = _wname(name)
    body[6:6 + len(nm)] = nm
    struct.pack_into("<I", body, 0x6a, sample_rate)
    return _iff(b"E5S1", bytes(body) + b"\x00\x00" * pcm_frames)


def _sub(tag, body):
    return tag + struct.pack(">I", len(body)) + body


def _zhdr(sample_index, key, size=16):
    b = bytearray(size)                          # real banks use 16 or 28
    struct.pack_into(">H", b, 4, sample_index)   # [4:6] sample index
    b[10] = key                                  # [10] root key
    return _sub(b"Zhdr", bytes(b))


def _e5_voice(zones, filler=None, zhdr_size=16):  # zones: [(sample_index, key), ...]
    parts = b""
    if filler is not None:                       # an unpadded interior chunk (may be odd)
        parts += _sub(b"EFGn", b"\x00" * filler)
    parts += _sub(b"LIST", b"E5ZL" + b"".join(_zhdr(s, k, zhdr_size) for s, k in zones))
    return _sub(b"E5V1", parts)


def _e5_preset_raw(name, voice_blobs):
    phdr_body = struct.pack(">I", 1) + _wname(name) + b"\x00\x00"
    parts = b"\x00\x00" + _sub(b"Phdr", phdr_body) + _sub(b"E5CL", b"\x00\x00\x00\x00")
    if voice_blobs:
        parts += _sub(b"LIST", b"E5VL" + b"".join(voice_blobs))
    return _iff(b"E5P1", parts)


def _e5_preset(name, voices=None):
    return _e5_preset_raw(name, [_e5_voice(z) for z in voices] if voices else [])


def _e5_link(slot, sample_index):
    return _iff(b"E5SL", struct.pack(">H", slot) + struct.pack(">I", sample_index))


def _make_e5b(tmp_path, name="TESTX", kind="bank", preset=None):
    """Build a minimal FORM E5B0 container. kind='bank' -> preset+links (.exb),
    kind='lib' -> one E5S1 sample (.ebl). Pass `preset` to override the default
    bank preset blob."""
    if kind == "lib":
        chunks = [(_e5_sample(1, "SPRING", 44100), b"E5S1", "SPRING")]
    else:
        pblob = preset or _e5_preset("DRUM KIT 1", voices=[[(1, 36)], [(2, 38)]])
        chunks = [(pblob, b"E5P1", "DRUM KIT 1"),
                  (_e5_link(1, 1), b"E5SL", "L1"),
                  (_e5_link(2, 2), b"E5SL", "L2")]
    n_entries = len(chunks)
    toc2_len = 8 + n_entries * 78
    off = 12 + toc2_len
    entries = []
    for i, (blob, tag, nm) in enumerate(chunks):
        entries.append(tag + struct.pack(">I", len(blob) - 8) + struct.pack(">I", off)
                       + struct.pack(">H", i)                     # [12:14] index
                       + _wname(nm).ljust(64, b"\x00"))           # [14:78] UTF-16 name
        off += len(blob)
    toc2 = _iff(b"TOC2", b"".join(entries))
    body = b"E5B0" + toc2 + b"".join(b for b, _, _ in chunks)
    blob = b"FORM" + struct.pack(">I", len(body)) + body     # standard IFF: filesize-8
    ext = ".ebl" if kind == "lib" else ".exb"
    p = tmp_path / (name + ext)
    p.write_bytes(blob)
    return str(p)


def test_e5b_sniffs_by_form_type():
    assert sniff_bytes(b"FORM\x00\x00\x00\x00E5B0") == "e5b"
    assert sniff_bytes(b"FORM\x00\x00\x00\x00E4B0") == "e4b"    # unchanged


def test_e5b_bank_presets_and_links(tmp_path):
    p = _make_e5b(tmp_path, kind="bank")
    assert sniff(p) == "e5b"
    fmt, chunks, warns = walk_file(p)
    assert fmt == "E-MU Emulator X / Proteus X bank"
    assert warns == []
    ids = [c["id"] for c in chunks]
    assert ids == ["FORM", "TOC2", "E5P1[0]", "E5SL[0]", "E5SL[1]"]
    toc = next(c for c in chunks if c["id"] == "TOC2")
    assert "DRUM KIT 1" in toc["fields"][0]["value"]   # name decoded from [14:78]
    preset = next(c for c in chunks if c["id"] == "E5P1[0]")
    names = {f["name"]: f["value"] for f in preset["fields"]}
    assert names["name"] == "DRUM KIT 1"
    link = next(c for c in chunks if c["id"] == "E5SL[0]")
    assert next(f for f in link["fields"] if f["name"] == "sample_index")["raw"] == 1


def test_e5b_preset_decodes_voices_zones(tmp_path):
    p = _make_e5b(tmp_path, kind="bank")
    _, chunks, _ = walk_file(p)
    preset = next(c for c in chunks if c["id"] == "E5P1[0]")
    fd = {f["name"]: f for f in preset["fields"]}
    assert fd["voices"]["value"] == 2
    assert fd["zones"]["value"] == 2
    assert "2 voice(s), 2 zone(s)" in preset["summary"]
    assert fd["sample[0]"]["value"] == "#1" and "key 36" in fd["sample[0]"]["note"]
    assert fd["sample[1]"]["value"] == "#2" and "key 38" in fd["sample[1]"]["note"]


def test_e5b_unpadded_odd_interior_chunks(tmp_path):
    """Regression: Proteus-X banks do not pad odd interior chunks. A voice with
    an odd-sized (417) unpadded filler before its zone list must still have its
    zones found -- the old unconditional-pad interior walk desynced here and
    reported 1 voice / 0 zones."""
    preset = _e5_preset_raw("KIT", [_e5_voice([(1, 36)], filler=417),
                                    _e5_voice([(2, 38)])])
    _, chunks, _ = walk_file(_make_e5b(tmp_path, kind="bank", preset=preset))
    fd = {f["name"]: f for f in next(c for c in chunks if c["id"] == "E5P1[0]")["fields"]}
    assert fd["voices"]["value"] == 2 and fd["zones"]["value"] == 2
    assert fd["sample[1]"]["value"] == "#2"


def test_e5b_zhdr_28byte_variant(tmp_path):
    """The v2 zone header is 28 bytes (Proteus module banks); the same early
    fields decode."""
    preset = _e5_preset_raw("V2", [_e5_voice([(5, 60)], zhdr_size=28)])
    _, chunks, _ = walk_file(_make_e5b(tmp_path, kind="bank", preset=preset))
    fd = {f["name"]: f for f in next(c for c in chunks if c["id"] == "E5P1[0]")["fields"]}
    assert fd["zones"]["value"] == 1
    assert fd["sample[0]"]["value"] == "#5" and "key 60" in fd["sample[0]"]["note"]


def test_e5b_no_voice_list_is_zero(tmp_path):
    """A 'lnk' preset with no E5VL decodes to 0 voices / 0 zones, no warning."""
    preset = _e5_preset_raw("LNK", [])
    _, chunks, _ = walk_file(_make_e5b(tmp_path, kind="bank", preset=preset))
    c = next(c for c in chunks if c["id"] == "E5P1[0]")
    fd = {f["name"]: f for f in c["fields"]}
    assert fd["voices"]["value"] == 0 and fd["zones"]["value"] == 0
    assert c["warnings"] == []


def test_e5b_short_zhdr_not_counted(tmp_path):
    """A Zhdr shorter than 11 bytes carries no key and must not count as a zone."""
    e5zl = _sub(b"LIST", b"E5ZL" + _sub(b"Zhdr", b"\x00" * 10) + _zhdr(3, 40))
    preset = _e5_preset_raw("K", [_sub(b"E5V1", e5zl)])
    _, chunks, _ = walk_file(_make_e5b(tmp_path, kind="bank", preset=preset))
    fd = {f["name"]: f for f in next(c for c in chunks if c["id"] == "E5P1[0]")["fields"]}
    assert fd["zones"]["value"] == 1        # only the valid 16-byte Zhdr


def test_e5b_lib_sample_name_and_rate(tmp_path):
    p = _make_e5b(tmp_path, kind="lib")
    fmt, chunks, warns = walk_file(p)
    assert warns == []
    samp = next(c for c in chunks if c["id"] == "E5S1[0]")
    f = {x["name"]: x for x in samp["fields"]}
    assert f["name"]["value"] == "SPRING"
    assert f["sample_rate"]["raw"] == 44100
    # enc/raw must re-encode to the on-disk bytes
    raw = open(p, "rb").read()
    base = samp["payload_base"]
    assert struct.pack(f["sample_rate"]["enc"], f["sample_rate"]["raw"]) \
        == raw[base + 0x6a:base + 0x6a + 4]


def test_e5b_form_size_is_standard_iff(tmp_path):
    p = _make_e5b(tmp_path, kind="bank")
    _, chunks, _ = walk_file(p)
    sf = next(f for f in chunks[0]["fields"] if f["name"] == "form_size")
    raw = open(p, "rb").read()
    assert sf["raw"] == len(raw) - 8            # E5B0 is standard IFF, not -12
