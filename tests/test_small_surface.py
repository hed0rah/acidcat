"""Review R11's small items: raw bytes on the command line, check naming
what it covers, ffmpeg's codec names, distinct --deep/--frames help."""

import json
import struct

import pytest

import acidcat
import seeds
from acidcat.cli import main
from acidcat.core.infra.capabilities import ffmpeg_pcm


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(seeds.build("wav"))
    return p


def test_edit_writes_raw_bytes_to_a_range(wav, tmp_path, capsys):
    out = tmp_path / "b.wav"
    # the fmt tag (bytes 20-21) rewritten as itself: raw bytes, no cascade
    assert main(["edit", str(wav), "--set", "@0x14+2=hex:0100", "-o", str(out)]) == 0
    assert "@0x14+2: hex:0100 -> hex:0100" in capsys.readouterr().out
    assert out.read_bytes() == wav.read_bytes()


def test_edit_hex_must_fit_the_range(wav, capsys):
    assert main(["edit", str(wav), "--set", "@0x14+2=hex:01", "--dry-run"]) == 1
    assert "is 2 bytes; 1 given" in capsys.readouterr().err


def test_edit_json_shows_bytes_as_hex(wav, capsys):
    main(["edit", str(wav), "--set", "@0x14+2=hex:0100", "--dry-run", "--json"])
    (row,) = json.loads(capsys.readouterr().out)
    assert row["changes"][0]["new"] == "hex:0100"


def test_check_names_what_it_covers(tmp_path, capsys):
    p = tmp_path / "x.bin"
    p.write_bytes(bytes(range(256)))
    assert main(["check", str(p)]) == 2
    err = capsys.readouterr().err
    assert "check covers:" in err and "wav" in err and "flac" in err


@pytest.mark.parametrize("args,want", [
    ((16, False, False), "pcm_s16le"), ((16, False, True), "pcm_s16be"),
    ((8, False, False), "pcm_u8"), ((8, False, True), "pcm_s8"),
    ((24, False, False), "pcm_s24le"), ((32, True, False), "pcm_f32le"),
    ((12, False, False), None),
])
def test_ffmpeg_codec_names(args, want):
    assert ffmpeg_pcm(*args) == want


@pytest.mark.parametrize("fmt,codec", [
    ("wav", "pcm_s16le"), ("aiff", "pcm_s16be"), ("8svx", "pcm_s8"),
    ("au", "pcm_s16be"), ("caf", "pcm_s16be"),
])
def test_the_audio_cap_names_its_layout(fmt, codec):
    doc = acidcat.open(seeds.build(fmt), forensics=False)
    caps = [n.caps["audio"] for n in doc.walk() if "audio" in n.caps]
    assert [c["codec"] for c in caps] == [codec]


@pytest.mark.parametrize("fmt", ["dsf", "rmid", "sid"])
def test_no_pcm_cap_where_the_data_is_not_pcm(fmt):
    doc = acidcat.open(seeds.build(fmt), forensics=False)
    assert not [n for n in doc.walk() if n.caps.get("audio", {}).get("source") == "inferred"]


def test_deep_and_frames_help_differ(capsys):
    with pytest.raises(SystemExit):
        main(["inspect", "-h"])
    out = capsys.readouterr().out
    deep = out.split("\n  --deep", 1)[1][:200]
    frames = out.split("\n  -F, --frames", 1)[1][:200]
    assert "extra decoding" in deep and "per-element rows" in frames and deep != frames
