"""DSF and DSDIFF: the two containers behind SACD.

Both walkers were written from the published specifications -- Sony DSF 1.01
and Philips DSDIFF 1.5 -- and then checked against commercial rips. The
builders here reproduce the exact header bytes those rips carry, so a change
that breaks a real file breaks a test.

The thing that makes DSD different from everything else here is that **one
sample is one bit**. Every duration and size formula in an audio tool assumes
at least eight, and getting it wrong is a factor-of-eight error that still
produces a plausible-looking number.
"""

import struct

import pytest

from acidcat.core.formats import dsd as dsdmod
from acidcat.core.walk.dsd import inspect_dsdiff, inspect_dsf


# ── builders ────────────────────────────────────────────────────────

def make_dsf(rate=2822400, channels=2, ctype=2, bits=1, samples=2822400,
             block=4096, audio=None, tag=b"", version=1, format_id=0,
             reserved=0):
    """A Sony DSF, laid out exactly as the spec orders it."""
    audio = audio if audio is not None else bytes(block * channels)
    data = b"data" + struct.pack("<Q", len(audio) + 12) + audio
    fmt = (b"fmt " + struct.pack("<Q", 52)
           + struct.pack("<IIIIII", version, format_id, ctype, channels,
                         rate, bits)
           + struct.pack("<Q", samples)
           + struct.pack("<II", block, reserved))
    total = 28 + len(fmt) + len(data) + len(tag)
    meta = (28 + len(fmt) + len(data)) if tag else 0
    head = b"DSD " + struct.pack("<QQQ", 28, total, meta)
    return head + fmt + data + tag


def _bchunk(cid, payload):
    """A DSDIFF chunk: 64-bit big-endian size, IFF even-length pad."""
    return cid + struct.pack(">Q", len(payload)) + payload + (
        b"\x00" if len(payload) & 1 else b"")


def make_dff(rate=2822400, channel_ids=(b"SLFT", b"SRGT"),
             compression=b"DSD ", audio=b"\x00" * 64, version=(1, 5, 0, 0),
             extra=b""):
    """A Philips DSDIFF, with the PROP local chunks a real file carries."""
    desc = b"not compressed"
    prop = (b"SND "
            + _bchunk(b"FS  ", struct.pack(">I", rate))
            + _bchunk(b"CHNL", struct.pack(">H", len(channel_ids))
                      + b"".join(channel_ids))
            + _bchunk(b"CMPR", compression + bytes([len(desc)]) + desc)
            + _bchunk(b"ABSS", struct.pack(">HBBI", 0, 0, 2, 0))
            + _bchunk(b"LSCO", struct.pack(">H", 0)))
    body = (b"DSD "
            + _bchunk(b"FVER", bytes(version))
            + _bchunk(b"PROP", prop)
            + _bchunk(b"DSD ", audio)
            + extra)
    return b"FRM8" + struct.pack(">Q", len(body)) + body


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _named(chunks, cid):
    return next(c for c in chunks if c["id"] == cid)


def _field(chunk, name):
    return next(f for f in chunk["fields"] if f["name"] == name)


# ── the format module ───────────────────────────────────────────────

class TestMagic:
    def test_dsf_is_recognised(self):
        assert dsdmod.is_dsf(make_dsf()[:32])

    def test_a_bare_dsd_magic_is_not_enough(self):
        """'DSD ' is four common characters. The declared header size has to
        agree, or any file whose first bytes happen to spell it is claimed."""
        assert not dsdmod.is_dsf(b"DSD " + struct.pack("<Q", 999) + bytes(8))

    def test_dsdiff_needs_its_form_type(self):
        """FRM8 is the 64-bit IFF container, not a format. DSDIFF is one thing
        it can hold, and the form type is what says so."""
        assert dsdmod.is_dsdiff(make_dff()[:16])
        other = b"FRM8" + struct.pack(">Q", 8) + b"NOPE"
        assert not dsdmod.is_dsdiff(other)

    def test_the_rate_family_is_named(self):
        assert dsdmod.rate_name(2822400) == "DSD64"
        assert dsdmod.rate_name(11289600) == "DSD256"

    def test_an_unnamed_rate_still_says_what_it_is(self):
        """A bare number tells a reader nothing. An off-table rate that is a
        clean multiple of the CD rate still gets described."""
        assert dsdmod.rate_name(44100 * 32) == "32x the CD rate"
        assert dsdmod.rate_name(12345) == ""


# ── DSF ─────────────────────────────────────────────────────────────

class TestDsf:
    def test_the_real_header_from_a_commercial_rip(self, tmp_path):
        """DSD64 stereo, 1 bit, 4096-byte blocks: the shape every SACD rip
        examined actually has."""
        p = _write(tmp_path, "a.dsf", make_dsf(samples=297009152))
        chunks, warns = inspect_dsf(p)
        fmt = _named(chunks, "fmt ")
        assert _field(fmt, "sample_rate")["note"] == "DSD64"
        assert _field(fmt, "bits_per_sample")["value"] == 1
        assert _field(fmt, "channels")["note"] == "FL, FR"
        assert _field(fmt, "channel_type")["note"] == "stereo"
        assert not warns

    def test_duration_counts_one_bit_per_sample(self, tmp_path):
        """The factor-of-eight trap. sample_count is FRAMES, and a DSD frame
        is one bit -- so 2,822,400 samples at 2,822,400 Hz is one second, not
        eight."""
        p = _write(tmp_path, "b.dsf", make_dsf(samples=2822400))
        chunks, _w = inspect_dsf(p)
        assert _field(_named(chunks, "fmt "), "duration")["value"] == "1.000 s"

    def test_a_total_size_that_disagrees_with_the_file_is_reported(self, tmp_path):
        """The file states its own length, so it can be wrong about it -- the
        same class of damage as a RIFF with a bad size."""
        data = bytearray(make_dsf())
        struct.pack_into("<Q", data, 12, 999999)
        p = _write(tmp_path, "c.dsf", bytes(data))
        chunks, _w = inspect_dsf(p)
        assert any("total_size says 999,999" in w
                   for w in _named(chunks, "DSD ")["warnings"])

    def test_a_channel_type_that_disagrees_with_the_count(self, tmp_path):
        """channel_type is not the channel COUNT -- type 4 is quad and type 5
        is four channels laid out differently -- so the two are checked
        against each other rather than assumed consistent."""
        p = _write(tmp_path, "d.dsf", make_dsf(ctype=7, channels=2))
        chunks, _w = inspect_dsf(p)
        assert any("5.1 channels" in w and "channels says 2" in w
                   for w in _named(chunks, "fmt ")["warnings"])

    def test_the_id3_tag_is_read_through_the_shared_reader(self, tmp_path):
        """DSF points at an ID3v2 tag at the end of the file. It is the fifth
        container to embed one, and it needed no new tag code."""
        body = b"TIT2" + struct.pack(">I", 6) + b"\x00\x00" + b"\x00India"
        tag = b"ID3" + bytes([3, 0, 0]) + b"\x00\x00\x00\x7f" + body
        p = _write(tmp_path, "e.dsf", make_dsf(tag=tag))
        chunks, _w = inspect_dsf(p)
        id3 = _named(chunks, "ID3 ")
        assert _field(id3, "TIT2")["value"] == "India"

    def test_a_metadata_pointer_past_the_end_is_reported(self, tmp_path):
        data = bytearray(make_dsf())
        struct.pack_into("<Q", data, 20, 1 << 40)
        p = _write(tmp_path, "f.dsf", bytes(data))
        chunks, _w = inspect_dsf(p)
        assert any("past the end" in w
                   for w in _named(chunks, "DSD ")["warnings"])

    def test_an_undefined_block_size_is_reported(self, tmp_path):
        """The spec fixes the block at 4096 and says the last one is
        zero-padded rather than short. A different value means a reader
        cannot know where a block ends."""
        p = _write(tmp_path, "g.dsf", make_dsf(block=1024))
        chunks, _w = inspect_dsf(p)
        assert any("not the 4096" in w
                   for w in _named(chunks, "fmt ")["warnings"])


# ── DSDIFF ──────────────────────────────────────────────────────────

class TestDsdiff:
    def test_the_real_header_from_a_commercial_rip(self, tmp_path):
        p = _write(tmp_path, "a.dff", make_dff())
        chunks, warns = inspect_dsdiff(p)
        assert _named(chunks, "FVER")["summary"] == "DSDIFF version 1.5"
        prop = _named(chunks, "PROP")
        assert _field(prop, "sample_rate")["note"] == "DSD64"
        assert _field(prop, "channels")["note"] == "stereo left, stereo right"
        assert _field(prop, "compression")["note"] == "uncompressed"
        assert not warns

    def test_the_frm8_size_counts_everything_after_its_own_header(self, tmp_path):
        """FRM8 counts from byte 12, so the file is size + 12. Wave64 counts
        its header IN, which is exactly the sort of disagreement that makes a
        reader of one wrong about the other."""
        raw = make_dff()
        p = _write(tmp_path, "b.dff", raw)
        chunks, warns = inspect_dsdiff(p)
        declared = _field(_named(chunks, "FRM8"), "size")["raw"]
        assert declared + 12 == len(raw)
        assert not warns

    def test_a_truncated_file_is_reported_against_its_own_number(self, tmp_path):
        raw = make_dff()
        p = _write(tmp_path, "c.dff", raw[:len(raw) // 2])
        _chunks, warns = inspect_dsdiff(p)
        assert any("FRM8 size says" in w for w in warns), warns

    def test_dst_compression_is_named(self, tmp_path):
        """DST is the lossless codec an SACD uses to fit a disc. An
        uncompressed DFF is raw DSD, and the two are not interchangeable."""
        p = _write(tmp_path, "d.dff", make_dff(compression=b"DST "))
        chunks, _w = inspect_dsdiff(p)
        assert _field(_named(chunks, "PROP"), "compression")["note"] == \
            "DST lossless"

    def test_a_version_other_than_1_5_says_so(self, tmp_path):
        p = _write(tmp_path, "e.dff", make_dff(version=(2, 0, 0, 0)))
        chunks, _w = inspect_dsdiff(p)
        assert any("the published spec is 1.5" in w
                   for w in _named(chunks, "FVER")["warnings"])

    def test_multichannel_names_every_speaker(self, tmp_path):
        ids = (b"MLFT", b"MRGT", b"C   ", b"LFE ", b"LS  ", b"RS  ")
        p = _write(tmp_path, "f.dff", make_dff(channel_ids=ids))
        chunks, _w = inspect_dsdiff(p)
        note = _field(_named(chunks, "PROP"), "channels")["note"]
        assert "LFE" in note and "left surround" in note

    def test_the_pad_rule_is_kept(self, tmp_path):
        """DSDIFF widened its sizes to 64 bits and KEPT the IFF even-length
        pad. A walk that drops the pad lands one byte into the next chunk on
        the first odd-sized one."""
        odd = _bchunk(b"COMT", b"x" * 7)               # 7 bytes: needs a pad
        p = _write(tmp_path, "g.dff", make_dff(extra=odd + _bchunk(b"DIIN", b"ok")))
        chunks, warns = inspect_dsdiff(p)
        ids = [c["id"] for c in chunks]
        assert "COMT" in ids and "DIIN" in ids, ids
        assert not warns


@pytest.mark.parametrize("corrupt", [b"", b"DSD ", b"FRM8", b"\x00" * 64])
def test_hostile_input_does_not_raise(tmp_path, corrupt):
    p = _write(tmp_path, "x.bin", corrupt)
    inspect_dsf(p)
    inspect_dsdiff(p)


# ── DST, the lossless half of DSDIFF ────────────────────────────────

def make_dst(frames=75, rate=75, declared=None, crc=True):
    """A DST-compressed DSDIFF: the sound chunk is a container, not a blob."""
    body = _bchunk(b"FRTE", struct.pack(">IH",
                                        frames if declared is None else declared,
                                        rate))
    for _ in range(frames):
        body += _bchunk(b"DSTF", bytes(32))
        if crc:
            body += _bchunk(b"DSTC", bytes(2))
    raw = make_dff(compression=b"DST ", audio=b"")
    raw = raw.replace(_bchunk(b"DSD ", b""), _bchunk(b"DST ", body), 1)
    return b"FRM8" + struct.pack(">Q", len(raw) - 12) + raw[12:]


class TestDst:
    """DST is why a Super Audio CD can hold multichannel at all: 4.7 GB at
    5.6 Mbit/s is under two hours in stereo and far less with six channels.

    The frames are NOT decoded. DST is a real codec with its own arithmetic
    coder, and naming a thing is not reading it.
    """

    def test_the_compression_type_is_the_only_thing_that_says_which(self, tmp_path):
        """A DST file and a raw one look identical at the container level
        apart from CMPR and the sound chunk's id."""
        p = _write(tmp_path, "a.dff", make_dst())
        chunks, _w = inspect_dsdiff(p)
        assert _field(_named(chunks, "PROP"), "compression")["note"] == \
            "DST lossless"
        assert "DST " in [c["id"] for c in chunks]
        assert "DSD " not in [c["id"] for c in chunks]

    def test_duration_comes_from_the_frame_count(self, tmp_path):
        """The one place a compressed file states its length. An uncompressed
        DSDIFF has a sample count; a DST one does not, so FRTE is it."""
        p = _write(tmp_path, "b.dff", make_dst(frames=150, rate=75))
        chunks, _w = inspect_dsdiff(p)
        dst = _named(chunks, "DST ")
        assert _field(dst, "frames")["value"] == "150"
        assert _field(dst, "duration")["value"] == "2.000 s"
        assert "150 frames at 75/s" in dst["summary"]

    def test_a_frame_count_that_disagrees_with_the_frames_present(self, tmp_path):
        p = _write(tmp_path, "c.dff", make_dst(frames=10, declared=99))
        chunks, _w = inspect_dsdiff(p)
        assert any("declares 99 frames, 10 DSTF" in w
                   for w in _named(chunks, "DST ")["warnings"])

    def test_a_dst_stream_with_no_frte_says_so(self, tmp_path):
        """Without FRTE the file does not state its own length anywhere."""
        raw = make_dff(compression=b"DST ", audio=b"")
        raw = raw.replace(_bchunk(b"DSD ", b""),
                          _bchunk(b"DST ", _bchunk(b"DSTF", bytes(16))), 1)
        raw = b"FRM8" + struct.pack(">Q", len(raw) - 12) + raw[12:]
        p = _write(tmp_path, "d.dff", raw)
        chunks, _w = inspect_dsdiff(p)
        assert any("no FRTE" in w for w in _named(chunks, "DST ")["warnings"])

    def test_frames_without_a_crc_are_still_counted(self, tmp_path):
        """DSTC is optional, so a reader that steps by a fixed pair loses
        count on any file that omits it."""
        p = _write(tmp_path, "e.dff", make_dst(frames=20, crc=False))
        chunks, _w = inspect_dsdiff(p)
        assert not _named(chunks, "DST ")["warnings"]
        assert _field(_named(chunks, "DST "), "frames")["value"] == "20"
