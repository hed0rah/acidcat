"""classify and locate must not disagree about what a format is.

Two vocabularies sat behind one pipeline:

  `classify x.mid` said "midi, a format acidcat walks". `locate x.mid` said
  "0 regions" -- MIDI was absent from the container table locate sweeps. In a
  disc image the embedded .mid went unreported entirely while a raw-pcm blob
  was reported overlapping it, and classify/audit both point users at locate.

  `classify` called a 48-byte ASCII file "opaque -- no magic, no embedded
  containers, no recoverable chunk grid / next: locate", so every README, sfz,
  cue sheet and text preset in a sample library was labelled opaque binary and
  sent to the statistical audio scanner. It already had an "XML document --
  readable as text" bucket.
"""

import struct

import pytest

from acidcat.core.forensics import locate as locatemod
from acidcat.core.forensics.classify import classify
from acidcat.core.infra.sniff import AUDIO_CONTAINERS


def _smf(ntrks=1):
    trk = b"\x00\x90\x3c\x64\x60\x80\x3c\x00\x00\xff\x2f\x00"
    out = b"MThd" + struct.pack(">IHHH", 6, 0, ntrks, 96)
    for _ in range(ntrks):
        out += b"MTrk" + struct.pack(">I", len(trk)) + trk
    return out


def test_locate_knows_every_format_classify_calls_walkable():
    """The registry-level statement of the bug."""
    assert "midi" in AUDIO_CONTAINERS


def test_locate_finds_a_standalone_midi(tmp_path):
    p = tmp_path / "t.mid"
    p.write_bytes(_smf())
    recs = locatemod.locate(p.read_bytes())
    assert [r["format"] for r in recs] == ["midi"]


def test_an_embedded_midi_gets_its_exact_extent(tmp_path):
    """The extent is the part that matters. Without a MIDI-aware extent the
    region ran to EOF: 34 real bytes plus 4,096 of surrounding junk."""
    mid = _smf()
    junk = bytes((i * 37 + 11) % 256 for i in range(4096))
    recs = locatemod.locate(junk + mid + junk)
    hit = [r for r in recs if r["format"] == "midi"]
    assert len(hit) == 1
    assert hit[0]["offset"] == 4096
    assert hit[0]["length"] == len(mid)


def test_a_multi_track_midi_extent_is_exact(tmp_path):
    mid = _smf(ntrks=4)
    recs = locatemod.locate(bytes(512) + mid + bytes(512))
    hit = [r for r in recs if r["format"] == "midi"][0]
    assert hit["length"] == len(mid)


def test_a_lying_midi_header_is_not_trusted():
    """A truncated MTrk must yield no extent rather than a wrong one."""
    mid = bytearray(_smf())
    struct.pack_into(">I", mid, 18, 0xFFFF)      # track claims more than exists
    assert locatemod._midi_extent(bytes(mid), 0) is None


@pytest.mark.parametrize("name,body", [
    ("README.txt", b"This is a readme about the sample pack.\n"),
    ("notes.md", b"# Notes\n\n- one\n- two\n"),
])
def test_text_is_not_called_opaque(tmp_path, name, body):
    p = tmp_path / name
    p.write_bytes(body)
    v = classify(str(p))
    assert v["shape"] != "opaque"
    assert "readable as text" in v["detail"]
    assert not v["next"]        # do not send a text file to the audio scanner


def test_an_sfz_is_a_format_not_just_text(tmp_path):
    """SFZ was the text case above until it gained a walker; now classify
    names the format, and still does not send it to the audio scanner."""
    p = tmp_path / "patch.sfz"
    p.write_bytes(b"<group> key=60\n<region> sample=kick.wav\n")
    v = classify(str(p))
    assert v["shape"] != "opaque"
    assert "sfz" in v["detail"]


def test_real_binary_is_still_opaque(tmp_path):
    """The other half. A text heuristic that swallows binaries is worse than
    the bug it fixes."""
    p = tmp_path / "blob.bin"
    p.write_bytes(bytes(range(256)) * 8)
    v = classify(str(p))
    assert v["shape"] == "opaque"


def test_utf16_text_is_recognised(tmp_path):
    p = tmp_path / "utf16.txt"
    p.write_bytes(b"\xff\xfe" + "hello world\n".encode("utf-16-le"))
    assert "readable as text" in classify(str(p))["detail"]


def _mp3_frames(n=3):
    """MPEG-1 Layer III, 128 kbps, 44.1 kHz -- frame_length 417."""
    return (b"\xff\xfb\x90\x00" + bytes(413)) * n


def test_a_bare_frame_sync_needs_a_second_frame(tmp_path):
    """0xFF 0xFE decodes as a perfectly valid MPEG-1 Layer I header, so every
    UTF-16-LE text file (which opens with exactly that BOM) was sniffed as an
    MP3 -- and classify then reported "mp3, a format acidcat walks". One lucky
    byte pair is not a stream."""
    from acidcat.core.infra import sniff as sniffmod

    txt = tmp_path / "utf16.txt"
    txt.write_bytes(b"\xff\xfe" + "hello world\n".encode("utf-16-le"))
    assert sniffmod.sniff(str(txt)) != "mp3"

    lone = tmp_path / "lone.bin"
    lone.write_bytes(b"\xff\xfeh\x00" + bytes(64))
    assert sniffmod.sniff(str(lone)) != "mp3"


def test_a_real_headerless_mp3_still_sniffs(tmp_path):
    """The other half: corroboration must not cost the capability. A stream
    with no ID3 tag is exactly what this path exists to catch."""
    from acidcat.core.infra import sniff as sniffmod

    p = tmp_path / "bare.mp3"
    p.write_bytes(_mp3_frames())
    assert sniffmod.sniff(str(p)) == "mp3"


def test_id3_tagged_mp3_is_untouched(tmp_path):
    from acidcat.core.infra import sniff as sniffmod

    p = tmp_path / "tagged.mp3"
    p.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x0a" + bytes(10) + _mp3_frames())
    assert sniffmod.sniff(str(p)) == "mp3"


def test_audio_is_unaffected(tmp_path):
    pcm = b"\x00\x01" * 256
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    v = classify(str(p))
    assert v["shape"] == "single" and v["format"] == "wav"
