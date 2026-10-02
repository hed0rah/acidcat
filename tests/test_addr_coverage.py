"""Every verb that takes a location takes an ADDR (review R4).

`probe read/table/scan` resolved names by the walker's chunk ids only
(`fmt.sample_rate`); they now take the ADDR grammar, with the 1.8 spelling
still accepted. `od` and `carve` refused an address in a decoded layer and
pointed at `carve --layer`; they now read the layer's bytes
(`1:program/program_header`, `1:@0+4`).
"""

import struct

import pytest

import acidcat
import seeds
from acidcat.cli import main
from acidcat.core import probe as pr


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(seeds.build("wav"))
    return str(p)


@pytest.fixture
def psf(tmp_path):
    p = tmp_path / "a.psf"
    p.write_bytes(seeds.build("psf"))
    return str(p)


@pytest.mark.parametrize("spec,note", [
    ("RIFF/fmt_#sample_rate", "RIFF/fmt_#sample_rate"),
    ("fmt#sample_rate", "RIFF/fmt_#sample_rate"),
    ("@24+4", "@24+4"),
    ("fmt.sample_rate", "fmt.sample_rate"),          # 1.8's spelling
])
def test_probe_resolves_an_addr(wav, spec, note):
    off, n, got = pr.resolve(wav, spec)
    assert (off, n, got) == (24, 4, note)


def test_probe_a_node_is_its_payload(wav):
    assert pr.resolve(wav, "RIFF/fmt_")[:2] == (20, 16)


def test_probe_names_nothing(wav):
    with pytest.raises(KeyError, match="no node 'nope'"):
        pr.resolve(wav, "nope")


def test_probe_read_through_main(wav, capsys):
    assert main(["probe", "read", "RIFF/fmt_#sample_rate", wav, "-t", "u32"]) == 0
    out = capsys.readouterr().out
    assert "(RIFF/fmt_#sample_rate)" in out and "44100" in out


def test_probe_refuses_a_layer_address_and_says_who_reads_it(psf, capsys):
    assert main(["probe", "read", "1:@0+4", psf]) == 2
    assert "od and carve read a layer" in capsys.readouterr().err


def _layer1(psf):
    return acidcat.open(psf, forensics=False).layer_bytes(1)


def test_od_reads_a_layer_address(psf, capsys):
    assert main(["od", psf, "1:@0+8", "program/program_header#rom_bytes",
                 "--json"]) == 0
    rows = __import__("json").loads(capsys.readouterr().out)
    img = _layer1(psf)
    assert rows[0] == {"addr": "1:@0+8", "offset": 0, "length": 8,
                       "hex": img[:8].hex()}
    assert rows[1]["addr"] == "1:program/program_header#rom_bytes"
    assert rows[1]["hex"] == img[8:12].hex()


def test_carve_reads_a_layer_address(psf, tmp_path, capsys, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # to see a leak
    out = tmp_path / "hdr.bin"
    assert main(["carve", psf, "1:program/program_header", "-o", str(out)]) == 0
    assert out.read_bytes() == _layer1(psf)[:12]
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("acidcat_layer")]


def test_carve_a_layer_field_prints_its_value(psf, capsys):
    assert main(["carve", psf, "program/program_header#rom_bytes"]) == 0
    got = capsys.readouterr().out.strip()
    assert got == str(struct.unpack_from("<I", _layer1(psf), 8)[0])


@pytest.mark.parametrize("spec", ["@-8+2", "@+8+2", "@-5..4"])
def test_a_signed_offset_is_refused_not_read_from_the_end(wav, capsys, spec):
    # int() took the '-', and the negative start sliced from the end of the file
    assert main(["od", wav, spec]) != 0
    out, err = capsys.readouterr()
    assert not out and "unsigned" in err
