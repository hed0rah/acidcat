"""Apple Core Audio Format (.caf): big-endian, signed 64-bit sizes, no padding.

Three things here are worth a test each because each is a place a reader of the
neighbouring formats goes wrong:

  the -1 size      legal, and means "to the end of the file". It is why the
                   size is SIGNED, and a walker reading it unsigned sees a
                   16-exabyte chunk
  desc first       mandatory, so its absence is a structural finding
  sample endian    the container is big-endian; the SAMPLES follow a flag in
                   desc. Inferring one from the other is wrong on every file
                   written the other way

Byte facts confirmed against libsndfile 1.2.2 output, including the endianness
trap: its files carry flags 0, and decoding their data little-endian returns
noise where a tone is.
"""
import struct

import pytest

from acidcat.core.infra.sniff import sniff
from acidcat.core.walk import walk_file
from acidcat.core.walk.caf import inspect_caf


def _chunk(ctype, payload, size=None):
    """4cc + s64 big-endian size + payload. No alignment: chunks abut."""
    return ctype + struct.pack(">q", len(payload) if size is None else size) + payload


def _desc(rate=44100.0, fmt=b"lpcm", flags=0, bpp=2, fpp=1, chans=1, bits=16):
    return struct.pack(">d4sIIIII", rate, fmt, flags, bpp, fpp, chans, bits)


def _data(pcm=b"\x00" * 64, edits=0, size=None):
    return _chunk(b"data", struct.pack(">I", edits) + pcm, size=size)


def _make_caf(desc=None, extra=b"", pcm=b"\x00" * 64, version=1):
    body = _chunk(b"desc", _desc() if desc is None else desc) + extra + _data(pcm)
    return b"caff" + struct.pack(">HH", version, 0) + body


def _write(tmp_path, data, name="t.caf"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_sniffs_by_magic(tmp_path):
    assert sniff(_write(tmp_path, _make_caf())) == "caf"


def test_walks_header_desc_and_data(tmp_path):
    label, chunks, warns = walk_file(_write(tmp_path, _make_caf()))
    assert label == "Apple Core Audio Format"
    assert warns == [], warns
    assert [c["id"] for c in chunks] == ["caff", "desc", "data"]


def test_the_size_is_the_payload_only(tmp_path):
    """RIFF's meaning, not Wave64's. desc is fixed at 32 bytes of payload."""
    _l, chunks, _w = walk_file(_write(tmp_path, _make_caf()))
    desc = next(c for c in chunks if c["id"] == "desc")
    assert desc["size"] == 32
    assert desc["payload_base"] == desc["offset"] + 12
    assert desc["extent_len"] == 44


def test_chunks_abut_with_no_padding(tmp_path):
    """No alignment rule at all: an odd payload does not shift the next chunk.
    RIFF pads to 2 and Wave64 to 8, so this is the third answer in the family."""
    odd = _chunk(b"free", b"\x01\x02\x03")           # 3-byte payload
    _l, chunks, _w = walk_file(_write(tmp_path, _make_caf(extra=odd)))
    free = next(c for c in chunks if c["id"] == "free")
    data = next(c for c in chunks if c["id"] == "data")
    assert free["size"] == 3
    assert data["offset"] == free["offset"] + 12 + 3   # not rounded anywhere


def test_the_minus_one_size_means_to_end_of_file(tmp_path):
    """Legal, and the reason the size field is signed. A walker reading it
    unsigned sees a chunk of 16 exabytes."""
    body = _chunk(b"desc", _desc()) + _data(b"\x11" * 100, size=-1)
    data = b"caff" + struct.pack(">HH", 1, 0) + body
    _l, chunks, warns = walk_file(_write(tmp_path, data))
    chunk = next(c for c in chunks if c["id"] == "data")
    assert chunk["size"] == 104                       # 4 edit-count + 100 audio
    assert chunk["offset"] + chunk["extent_len"] == len(data)
    assert any("to end of file" in w for w in warns), warns


def test_a_negative_size_that_is_not_the_sentinel_stops_the_walk(tmp_path):
    body = _chunk(b"desc", _desc()) + b"junk" + struct.pack(">q", -99)
    data = b"caff" + struct.pack(">HH", 1, 0) + body
    _l, _chunks, warns = walk_file(_write(tmp_path, data))
    assert any("negative size" in w for w in warns), warns


def test_the_audio_starts_after_the_edit_count(tmp_path):
    """The data payload opens with a u32 edit count. Treating it as samples
    puts four bytes of metadata at the front of the audio."""
    _l, chunks, _w = walk_file(_write(tmp_path, _make_caf(pcm=b"\x7f" * 64)))
    data = next(c for c in chunks if c["id"] == "data")
    f = {x["name"]: x["value"] for x in data["fields"]}
    assert f["edit_count"] == 0
    assert f["audio_bytes"] == "64"                   # 68 payload less the 4
    assert data["size"] == 68


def test_sample_endianness_is_read_from_the_flag_not_the_container(tmp_path):
    """The container is big-endian throughout; the samples are whatever desc
    says. libsndfile writes flags 0, meaning big-endian samples."""
    _l, chunks, _w = walk_file(_write(tmp_path, _make_caf()))
    desc = next(c for c in chunks if c["id"] == "desc")
    assert "big-endian samples" in desc["summary"]

    little = _desc(flags=0x02)
    _l, chunks, _w = walk_file(_write(tmp_path, _make_caf(desc=little), "le.caf"))
    desc = next(c for c in chunks if c["id"] == "desc")
    assert "little-endian samples" in desc["summary"]


def test_float_is_read_from_the_flag(tmp_path):
    f32 = _desc(flags=0x01, bpp=4, bits=32)
    _l, chunks, _w = walk_file(_write(tmp_path, _make_caf(desc=f32)))
    desc = next(c for c in chunks if c["id"] == "desc")
    assert "float" in desc["summary"]


def test_desc_must_come_first(tmp_path):
    body = _chunk(b"free", b"\x00" * 8) + _chunk(b"desc", _desc()) + _data()
    data = b"caff" + struct.pack(">HH", 1, 0) + body
    _l, _chunks, warns = walk_file(_write(tmp_path, data))
    assert any("desc to come first" in w for w in warns), warns


def test_a_file_with_no_data_chunk_is_flagged(tmp_path):
    body = _chunk(b"desc", _desc()) + _chunk(b"free", b"\x00" * 32)
    data = b"caff" + struct.pack(">HH", 1, 0) + body
    _l, _chunks, warns = walk_file(_write(tmp_path, data))
    assert any("does not carry" in w for w in warns), warns


def test_a_payload_past_eof_is_reported_not_trusted(tmp_path):
    body = _chunk(b"desc", _desc()) + b"free" + struct.pack(">q", 1 << 40)
    data = b"caff" + struct.pack(">HH", 1, 0) + body
    _l, chunks, warns = walk_file(_write(tmp_path, data))
    assert any("only" in w and "remain" in w for w in warns), warns
    for c in chunks:
        assert c["offset"] + c["extent_len"] <= len(data)


def test_a_bad_bytes_per_packet_is_flagged(tmp_path):
    """2 bytes per packet cannot hold 2 channels of 16-bit."""
    bad = _desc(chans=2, bits=16, bpp=2)
    _l, chunks, _w = walk_file(_write(tmp_path, _make_caf(desc=bad)))
    desc = next(c for c in chunks if c["id"] == "desc")
    assert any("needs 4" in w for w in desc["warnings"]), desc["warnings"]


def test_variable_packet_size_says_so_instead_of_no_duration(tmp_path):
    """bpp 0 means the packet sizes live in pakt. That is a fact about the
    format, not a missing answer."""
    vbr = _desc(fmt=b"aac ", bpp=0, fpp=1024, bits=0)
    pakt = _chunk(b"pakt", struct.pack(">qqii", 5, 5120, 2112, 0) + b"\x40" * 5)
    _l, chunks, _w = walk_file(_write(tmp_path, _make_caf(desc=vbr, extra=pakt)))
    data = next(c for c in chunks if c["id"] == "data")
    assert "variable packet size" in data["summary"]
    p = next(c for c in chunks if c["id"] == "pakt")
    f = {x["name"]: x["value"] for x in p["fields"]}
    assert f["packets"] == "5" and f["priming_frames"] == 2112


def test_info_strings_are_decoded(tmp_path):
    payload = struct.pack(">I", 2) + b"artist\x00hed0rah\x00tempo\x00120\x00"
    info = _chunk(b"info", payload)
    _l, chunks, _w = walk_file(_write(tmp_path, _make_caf(extra=info)))
    c = next(x for x in chunks if x["id"] == "info")
    f = {x["name"]: x["value"] for x in c["fields"]}
    assert f["artist"] == "hed0rah" and f["tempo"] == "120"


def test_an_info_count_that_disagrees_is_flagged(tmp_path):
    payload = struct.pack(">I", 9) + b"a\x00b\x00"
    _l, chunks, _w = walk_file(_write(tmp_path, _make_caf(extra=_chunk(b"info", payload))))
    c = next(x for x in chunks if x["id"] == "info")
    assert any("declares 9" in w for w in c["warnings"]), c["warnings"]


def test_a_short_file_degrades_rather_than_raising(tmp_path):
    chunks, warns = inspect_caf(_write(tmp_path, b"caf"))
    assert chunks == []
    assert warns and "needs 8" in warns[0]


def test_an_unknown_version_is_flagged(tmp_path):
    _l, _c, warns = walk_file(_write(tmp_path, _make_caf(version=7)))
    assert any("version is 7" in w for w in warns), warns


@pytest.mark.parametrize("fmt,label", [
    (b"lpcm", "linear PCM"), (b"alac", "Apple Lossless"), (b"aac ", "AAC"),
    (b"ulaw", "u-law"), (b".mp3", "MPEG-1 Layer 3"),
])
def test_the_format_ids_are_named(tmp_path, fmt, label):
    d = _desc(fmt=fmt)
    _l, chunks, _w = walk_file(_write(tmp_path, _make_caf(desc=d), f"{fmt.strip().decode('ascii', 'replace').strip('.')}.caf"))
    desc = next(c for c in chunks if c["id"] == "desc")
    assert label in desc["summary"]


def test_locate_finds_it_standalone_and_embedded(tmp_path):
    from acidcat.core.forensics import locate as locatemod
    from acidcat.core.infra.sniff import AUDIO_CONTAINERS
    assert "caf" in AUDIO_CONTAINERS
    data = _make_caf(pcm=b"\x00" * 512)
    assert [r["format"] for r in locatemod.locate(data)] == ["caf"]
    found = locatemod.locate(bytes(2000) + data + bytes(2000))
    assert [(r["format"], r["offset"]) for r in found] == [("caf", 2000)]
