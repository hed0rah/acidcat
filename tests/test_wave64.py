"""Sony Wave64 (.w64): GUID ids, u64 sizes that count their own header, and
8-byte alignment.

Every one of those three differs from RIFF in a way that makes a RIFF reader
produce a plausible wrong answer rather than an error, so each has a test here.

The byte facts were confirmed against libsndfile 1.2.2 output, which is also
where the final-chunk rule came from: the last chunk is NOT padded, so a walker
that always advances by the padded size calls every well-formed Wave64
truncated. That specimen cannot be committed (it is generated, not ours to
distribute) so the rule is pinned by `test_the_last_chunk_is_not_padded` below.
"""
import struct

import pytest

from acidcat.core.formats.wave64 import AUDIO_SUFFIX, CONTAINER_SUFFIX
from acidcat.core.infra.sniff import sniff
from acidcat.core.walk import walk_file
from acidcat.core.walk.wave64 import inspect_wave64

_RIFF = b"riff" + CONTAINER_SUFFIX
_WAVE = b"wave" + AUDIO_SUFFIX


def _guid(fourcc):
    return fourcc + AUDIO_SUFFIX


def _chunk(fourcc, payload, pad=True):
    """One Wave64 chunk. The size counts the 24-byte header; the pad does not."""
    body = _guid(fourcc) + struct.pack("<Q", 24 + len(payload)) + payload
    return body + (b"\x00" * (-(24 + len(payload)) & 7) if pad else b"")


def _fmt(channels=1, rate=44100, bits=16):
    align = channels * bits // 8
    return struct.pack("<HHIIHH", 1, channels, rate, rate * align, align, bits)


def _make_w64(frames=64, channels=1, bits=16, rate=44100, extra=b""):
    """A minimal valid Wave64: header, fmt, an optional middle chunk, data.

    `data` is last and deliberately unpadded, the way libsndfile writes it.
    """
    pcm = b"\x00" * (frames * channels * bits // 8)
    body = _chunk(b"fmt ", _fmt(channels, rate, bits))
    if extra:
        body += _chunk(b"fact", extra)
    body += _chunk(b"data", pcm, pad=False)
    total = 40 + len(body)
    return _RIFF + struct.pack("<Q", total) + _WAVE + body


def _write(tmp_path, data, name="t.w64"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_sniffs_by_the_container_guid(tmp_path):
    """The GUID spells 'riff' in LOWERCASE, which is what keeps it clear of the
    RIFF branch four lines above it in the sniffer."""
    assert sniff(_write(tmp_path, _make_w64())) == "w64"
    assert _RIFF[:4] == b"riff" and _RIFF[:4] != b"RIFF"


def test_walks_header_fmt_and_data(tmp_path):
    label, chunks, warns = walk_file(_write(tmp_path, _make_w64(channels=2, bits=24)))
    assert label == "Wave64"
    assert warns == [], warns
    assert [c["id"] for c in chunks] == ["wave64", "fmt ", "data"]
    f = {x["name"]: x["value"] for c in chunks for x in c["fields"]}
    assert f["channels"] == 2
    assert str(f["bits_per_sample"]) == "24"


def test_the_size_counts_its_own_header(tmp_path):
    """RIFF's size is the payload; Wave64's includes the 24-byte chunk header.
    Reading it as RIFF does overstates every payload by exactly 24 bytes."""
    data = _make_w64(frames=64, bits=16, channels=1)
    _label, chunks, _warns = walk_file(_write(tmp_path, data))
    fmt = next(c for c in chunks if c["id"] == "fmt ")
    assert fmt["size"] == 16                       # payload, not 16 + 24
    declared = struct.unpack_from("<Q", data, fmt["offset"] + 16)[0]
    assert declared == 40                          # what is actually on disk
    assert fmt["payload_base"] == fmt["offset"] + 24
    assert fmt["extent_len"] == 40


def test_the_header_size_counts_the_whole_file(tmp_path):
    data = _make_w64()
    assert struct.unpack_from("<Q", data, 16)[0] == len(data)
    _l, _c, warns = walk_file(_write(tmp_path, data))
    assert warns == []


def test_a_lying_header_size_is_flagged(tmp_path):
    data = bytearray(_make_w64())
    struct.pack_into("<Q", data, 16, 999999)
    _l, _c, warns = walk_file(_write(tmp_path, bytes(data)))
    assert any("header declares" in w for w in warns), warns


def test_an_interior_chunk_is_padded_to_eight(tmp_path):
    """The desync case. `fact` carries 3 bytes, so its chunk is 27 long and the
    next GUID sits at 32. A reader carrying RIFF's 2-byte habit looks at 28 and
    reads four bytes of padding as the start of an id."""
    data = _make_w64(extra=b"\x01\x02\x03")
    _label, chunks, warns = walk_file(_write(tmp_path, data))
    assert [c["id"] for c in chunks] == ["wave64", "fmt ", "fact", "data"]
    assert warns == [], warns
    fact = next(c for c in chunks if c["id"] == "fact")
    data_c = next(c for c in chunks if c["id"] == "data")
    assert fact["size"] == 3
    # 24 + 3 = 27, rounded up to 32
    assert data_c["offset"] == fact["offset"] + 32


def test_the_last_chunk_is_not_padded(tmp_path):
    """libsndfile ends the file at the final chunk's unpadded end. A walker
    that advances by the padded size reports a well-formed file as overrunning,
    which is what this pins."""
    data = _make_w64(frames=5)                     # 10 bytes of PCM: 34 % 8 != 0
    assert len(data) % 8 != 0                      # the file itself is unaligned
    _label, chunks, warns = walk_file(_write(tmp_path, data))
    assert warns == [], warns
    last = chunks[-1]
    assert last["id"] == "data"
    assert last["offset"] + last["extent_len"] == len(data)


def test_a_size_below_the_header_does_not_spin(tmp_path):
    """A size under 24 cannot be a size -- advancing by it would not move the
    cursor, so the walk has to stop rather than loop."""
    data = bytearray(_make_w64())
    struct.pack_into("<Q", data, 40 + 16, 4)       # fmt's size field
    _label, chunks, warns = walk_file(_write(tmp_path, bytes(data)))
    assert any("less than" in w for w in warns), warns


def test_a_payload_past_eof_is_reported_not_trusted(tmp_path):
    data = bytearray(_make_w64())
    struct.pack_into("<Q", data, 40 + 16, 1 << 40)  # fmt claims a terabyte
    _label, chunks, warns = walk_file(_write(tmp_path, bytes(data)))
    assert any("only" in w and "remain" in w for w in warns), warns
    fmt = next(c for c in chunks if c["id"] == "fmt ")
    assert fmt["offset"] + fmt["extent_len"] <= len(data)


def test_an_unknown_guid_suffix_reports_the_guid(tmp_path):
    """A chunk whose suffix is neither constant cannot be split into a FOURCC,
    so it is reported as its GUID rather than as four bytes of a guess."""
    odd = bytes(range(16))
    body = odd + struct.pack("<Q", 24 + 8) + b"\x00" * 8
    data = _RIFF + struct.pack("<Q", 40 + len(body)) + _WAVE + body
    chunks, warns = inspect_wave64(_write(tmp_path, data))
    assert chunks[1]["id"] == "guid:" + odd.hex()
    assert any("unrecognized" in x["value"] for x in chunks[1]["fields"])


def test_a_short_file_degrades_rather_than_raising(tmp_path):
    """Reachable through fmt_override, which promises to degrade like any other
    walk."""
    chunks, warns = inspect_wave64(_write(tmp_path, _RIFF[:8]))
    assert chunks == []
    assert warns and "needs 40" in warns[0]


def test_locate_finds_it_standalone_and_embedded(tmp_path):
    """The vocabulary bug, pre-empted: a format `classify` calls walkable that
    `locate` cannot find is the defect test_format_vocabulary exists for. A
    16-byte GUID is the strongest scan pattern in the table, and Wave64 exists
    to hold files past 4 GB, so finding one inside something larger is the
    case that matters."""
    from acidcat.core.forensics import locate as locatemod
    from acidcat.core.infra.sniff import AUDIO_CONTAINERS
    assert "w64" in AUDIO_CONTAINERS
    data = _make_w64()
    assert [r["format"] for r in locatemod.locate(data)] == ["w64"]
    found = locatemod.locate(bytes(3000) + data + bytes(3000))
    assert [(r["format"], r["offset"]) for r in found] == [("w64", 3000)]


@pytest.mark.parametrize("bits,channels", [(8, 1), (16, 2), (24, 2), (32, 1)])
def test_the_reused_riff_parsers_read_fmt(tmp_path, bits, channels):
    """The payloads are RIFF's, unchanged; only the framing is Wave64's. This
    is the claim that lets the walker reuse wav._PARSERS outright."""
    _l, chunks, _w = walk_file(_write(tmp_path, _make_w64(bits=bits, channels=channels)))
    f = {x["name"]: x["value"] for c in chunks for x in c["fields"]}
    assert f["channels"] == channels
    assert str(f["bits_per_sample"]) == str(bits)
