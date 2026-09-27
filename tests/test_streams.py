"""Tests for the console stream walkers: ADX, BRSTM, HPS and VAG.

These four had decoders here long before they had walkers. `extract` was
parsing their headers to pull audio out and then discarding the structure, so
`inspect` said nothing about files the tool could already read. The walkers add
no new parsing; they report what was already being computed.

The shared part is the vocabulary, not the layout. `_stream` renders codec,
channels, sample rate, frame count and duration in one order for all four, so
the same fact has the same name whichever console wrote the file. What is NOT
shared is the chunk structure, because it genuinely differs: a header and a
body, a header pointing at named blocks, a linked list, and a header with a
name in the middle of it.
"""
import os
import struct

import pytest

from conftest import CORPUS_BRSTM

from acidcat.core.infra import geometry, sniff
from acidcat.core.walk import streams


def _vag(rate=44100, name=b"KICK", blocks=10, declared=None):
    v = bytearray(0x30)
    v[0:4] = b"VAGp"
    struct.pack_into(">I", v, 4, 0x20)
    struct.pack_into(">I", v, 0x0C, 16 * blocks if declared is None else declared)
    struct.pack_into(">I", v, 0x10, rate)
    v[0x20:0x20 + len(name)] = name
    return bytes(v) + bytes(16 * blocks)


def _adx(**kw):
    from test_adx import _adx_file
    kw.setdefault("ch", 2)
    return _adx_file(bytes(18) * 40, **kw)


def _brstm():
    from test_brstm import _brstm_file
    return _brstm_file()


def _hps(channels=2, blocks=3, rate=32000):
    from test_hps import hps_file
    return hps_file([[bytes(0x20)] * channels] * blocks,
                    rate=rate, channels=channels)


ALL = [("adx", _adx, streams.inspect_adx),
       ("brstm", _brstm, streams.inspect_brstm),
       ("hps", _hps, streams.inspect_hps),
       ("vag", _vag, streams.inspect_vag)]


def _write(tmp_path, name, blob):
    p = tmp_path / name
    p.write_bytes(blob)
    return str(p)


# ── the four, held to the same contract ─────────────────────────────

@pytest.mark.parametrize("fmt,build,walk", ALL, ids=[a[0] for a in ALL])
def test_each_stream_is_identified_and_walked(tmp_path, fmt, build, walk):
    path = _write(tmp_path, "s." + fmt, build())
    assert sniff.sniff(path) == fmt
    chunks, _warns = walk(path)
    assert chunks and chunks[0]["id"] == "header"


@pytest.mark.parametrize("fmt,build,walk", ALL, ids=[a[0] for a in ALL])
def test_every_byte_is_accounted_for(tmp_path, fmt, build, walk):
    blob = build()
    path = _write(tmp_path, "s." + fmt, blob)
    chunks, _warns = walk(path)
    geometry.normalize(chunks, len(blob))
    assert all(geometry.is_trustworthy(c) for c in chunks)
    covered = sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                  for c in chunks)
    assert covered == len(blob), (covered, len(blob))


@pytest.mark.parametrize("fmt,build,walk", ALL, ids=[a[0] for a in ALL])
def test_the_shared_vocabulary_is_actually_shared(tmp_path, fmt, build, walk):
    """The point of putting these in one module. codec, channels and sampleRate
    mean the same thing and appear in the same order for all four, so a reader
    comparing two console formats is not also translating between two sets of
    words for the same number."""
    path = _write(tmp_path, "s." + fmt, build())
    chunks, _warns = walk(path)
    names = [f["name"] for f in chunks[0]["fields"]]
    lead = [n for n in names if n in ("codec", "channels", "sampleRate")]
    assert lead == ["codec", "channels", "sampleRate"], names


@pytest.mark.parametrize("fmt,build,walk", ALL, ids=[a[0] for a in ALL])
@pytest.mark.parametrize("n", [0, 4, 12, 32, 64])
def test_truncation_at_any_depth_does_not_raise(tmp_path, fmt, build, walk, n):
    path = _write(tmp_path, "t." + fmt, build()[:n])
    try:
        walk(path)
    except Exception as exc:                 # noqa: BLE001 - that IS the assertion
        pytest.fail("%s truncated to %d bytes raised %r" % (fmt, n, exc))


# ── per-format detail worth pinning ─────────────────────────────────

def test_adx_data_begins_four_bytes_past_the_copyright_offset(tmp_path):
    """The format's one real quirk: the field at 0x02 points two bytes BEFORE
    the '(c)CRI' marker, so the audio starts at that value plus four."""
    blob = _adx()
    co = struct.unpack_from(">H", blob, 2)[0]
    assert blob[co - 2:co + 4] == b"(c)CRI"
    path = _write(tmp_path, "a.adx", blob)
    chunks, _warns = streams.inspect_adx(path)
    frames = [c for c in chunks if c["id"] == "frames"][0]
    assert frames["offset"] == co + 4


def test_hps_walks_the_block_chain_rather_than_counting(tmp_path):
    """Each HPS block header names the next block's file offset at +8. The
    sample count sits at +4, so reading the pointer from there walks the chain
    into a number that is not an offset -- which is what the first version of
    this walker did."""
    path = _write(tmp_path, "h.hps", _hps(channels=2, blocks=4))
    chunks, _warns = streams.inspect_hps(path)
    blocks = [f for f in chunks[0]["fields"] if f["name"] == "blocks"][0]
    assert blocks["value"] == 4, "expected four blocks from the chain"


def test_hps_does_not_claim_a_frame_count_it_cannot_know(tmp_path):
    """The frame count is not in an HPS header; it comes out of decoding. The
    shared vocabulary drops the field rather than reporting zero, because a
    zero here would read as an empty stream."""
    path = _write(tmp_path, "h.hps", _hps())
    chunks, _ = streams.inspect_hps(path)
    names = [f["name"] for f in chunks[0]["fields"]]
    assert "frames" not in names and "duration" not in names


def test_vag_reports_a_declared_size_larger_than_the_file(tmp_path):
    """A VAG is often padded, and the size field says which of the tail is
    real. A field claiming more than exists is worth saying out loud."""
    path = _write(tmp_path, "v.vag", _vag(blocks=4, declared=16 * 999))
    _chunks, warns = streams.inspect_vag(path)
    assert any("only" in w and "follow the header" in w for w in warns), warns


def test_vag_carries_a_name_and_the_others_do_not(tmp_path):
    path = _write(tmp_path, "v.vag", _vag(name=b"SNARE"))
    chunks, _ = streams.inspect_vag(path)
    name = [f for f in chunks[0]["fields"] if f["name"] == "name"][0]
    assert name["value"] == "SNARE"


def test_duration_is_derived_not_invented(tmp_path):
    """None of these store a duration. It is frames over rate, and stating
    where it came from is the difference between a fact and a guess."""
    path = _write(tmp_path, "a.adx", _adx(rate=22050, samples=44100))
    chunks, _ = streams.inspect_adx(path)
    dur = [f for f in chunks[0]["fields"] if f["name"] == "duration"][0]
    assert dur["value"] == "2.000 s"
    assert "derived" in dur["note"]


def test_a_broken_header_is_reported_not_raised(tmp_path):
    for fmt, walk in (("adx", streams.inspect_adx), ("brstm", streams.inspect_brstm),
                      ("hps", streams.inspect_hps), ("vag", streams.inspect_vag)):
        path = _write(tmp_path, "bad." + fmt, b"\x00" * 128)
        chunks, warns = walk(path)
        assert chunks, fmt
        assert warns, "%s accepted 128 zero bytes without complaint" % fmt


# ── a whole real file, end to end ───────────────────────────────────

def test_a_whole_brstm_walks_completely():
    """The 504 KB reference stream where the corpus is present, and the
    synthetic stand-in where it is not.

    Naming the corpus path directly is what the first version did, and the
    suite's own guard caught it: a test that names a gitignored file does not
    fail on a runner, it SKIPS, and a skip is a green run that checked nothing.
    """
    from acidcat.core.walk import walk_file
    label, chunks, _warns = walk_file(CORPUS_BRSTM)
    size = os.path.getsize(CORPUS_BRSTM)
    geometry.normalize(chunks, size)
    assert "BRSTM" in label
    assert all(geometry.is_trustworthy(c) for c in chunks)
    covered = sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                  for c in chunks)
    assert covered == size, (covered, size)


def test_codec_refusals_reach_extract_as_sampleerror(tmp_path):
    """Found by an adversarial audit: the walker accepts an .hps with 1..8
    channels but the decoder only 1..2, and HpsError was not in the extract
    boundary's malformed set -- so a 5-channel file escaped `acidcat extract`
    as a raw traceback. Codec refusals are malformed input, not bugs in us."""
    import struct
    import pytest
    from acidcat.core.codecs.hps import MAGIC
    from acidcat.core.extract.samples import iter_samples, SampleError
    p = tmp_path / "five.hps"
    p.write_bytes(MAGIC + struct.pack(">II", 32000, 5) + bytes(0x200))
    with pytest.raises(SampleError):
        list(iter_samples(str(p), fmt="hps"))


def test_a_cut_brstm_says_it_is_cut(tmp_path):
    """The RSTM header declares the whole file's size at 0x08. FFmpeg's test
    suite carries a stream cut to 200 KB of a declared 9.1 MB, and the walker
    reported it as whole because it never read that field."""
    data = bytearray(_brstm())
    struct.pack_into(">I", data, 0x08, len(data))
    chunks, warns = streams.inspect_brstm(_write(tmp_path, "whole.brstm", bytes(data)))
    assert not warns
    assert [f["value"] for f in chunks[0]["fields"] if f["name"] == "file_size"] == [len(data)]
    struct.pack_into(">I", data, 0x08, len(data) * 45)
    _chunks, warns = streams.inspect_brstm(_write(tmp_path, "cut.brstm", bytes(data)))
    assert len(warns) == 1
    assert "the stream is cut" in str(warns[0])
