"""Arturia Analog Lab .labx walker: bank census plus per-preset metadata read
out of the Boost text archive, with each preset a carveable byte region.

Fixtures are synthesized here (a small STORED zip of minimal Boost archives with
placeholder metadata) -- no real bank content."""
import zipfile

from acidcat.core.sniff import sniff
from acidcat.core.walk import labx


def _lps(s):
    b = s.encode()
    return f"{len(b)} ".encode() + b


def _boost(name, bank, author, comment, ts, ver, typ, sub, tags):
    blob = f"Characteristics,{tags};Genres,Test;Styles,Clean;"
    return b" ".join([
        b"22 serialization::archive 10 0 7 0 7",
        _lps(name), _lps(bank),
        b"5", _lps(author),                 # a flag int then the author string
        b"0  0 0 0 0 0 0",
        _lps(comment),
        str(ts).encode(),
        _lps(ver),
        b"0 0 0 0 6 0 0 0",
        _lps("Characteristics"), _lps(blob),
        b"4 Type", _lps(typ),
        b"7 Subtype", _lps(sub),
        b"0 0 0",
    ])


def _make_labx(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr("Pigments/User/Test Bank/Bass 1",
                   _boost("Bass 1", "Test Bank", "TestUser", "a deep bass",
                          1758316052, "1.13.2.5305", "Bass", "Sub Bass",
                          "Warm|Dark"))
        z.writestr("Pigments/User/Test Bank/Lead 1",
                   _boost("Lead 1", "Test Bank", "TestUser", "bright lead",
                          1758316099, "1.13.2.5305", "Lead", "Solo",
                          "Bright|Aggressive"))
        z.writestr("DX7/User/Test Bank/Keys 1",
                   _boost("Keys 1", "Test Bank", "TestUser", "an ep",
                          1758316120, "1.3.3.6106", "Keys", "Electric Piano",
                          "Clean"))
        z.writestr("Test Bank.png", b"\x89PNG\r\n\x1a\nfakecover")
    return str(path)


def test_labx_sniffs_by_content(tmp_path):
    # a .zip extension, sniffed as labx purely by the entry layout + boost magic
    p = _make_labx(tmp_path / "bank.zip")
    assert sniff(p) == "labx"


def test_labx_bank_census(tmp_path):
    p = _make_labx(tmp_path / "b.labx")
    chunks, warns = labx.inspect_labx(p)
    assert warns == []
    bank = chunks[0]
    assert bank["id"] == "bank"
    f = {x["name"]: x["value"] for x in bank["fields"]}
    assert f["bank_name"] == "Test Bank"
    assert f["presets"] == 3
    assert "Pigments 2" in f["engines"] and "DX7 1" in f["engines"]
    assert f["cover_image"] == "Test Bank.png"


def test_labx_preset_metadata_and_carve(tmp_path):
    p = _make_labx(tmp_path / "b.labx")
    data = open(p, "rb").read()
    chunks, _ = labx.inspect_labx(p)
    presets = [c for c in chunks if c["id"] == "preset"]
    assert len(presets) == 3
    bass = next(c for c in presets if c["summary"].startswith("Pigments: Bass 1"))
    f = {x["name"]: x["value"] for x in bass["fields"]}
    assert f["engine"] == "Pigments" and f["name"] == "Bass 1"
    assert f["type"] == "Bass" and f["subtype"] == "Sub Bass"
    assert f["author"] == "TestUser"
    assert f["comment"] == "a deep bass"
    assert f["engine_version"] == "1.13.2.5305"
    assert f["tags"] == "Warm|Dark"
    assert f["saved"].startswith("2025-09-")
    # the preset region carves to the literal boost archive
    blob = data[bass["offset"]:bass["offset"] + bass["size"]]
    assert blob.startswith(b"22 serialization::archive ")


def test_labx_not_a_zip(tmp_path):
    p = tmp_path / "bad.labx"
    p.write_bytes(b"PK\x03\x04 not really a zip")
    chunks, warns = labx.inspect_labx(str(p))
    assert warns == ["not a zip archive"]
    assert chunks[0]["warnings"] == ["not a zip archive"]


def test_labx_zip_without_layout_falls_back(tmp_path):
    p = tmp_path / "plain.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("readme.txt", b"hello")
        z.writestr("sound.wav", b"RIFF....WAVE")
    chunks, warns = labx.inspect_labx(str(p))
    assert any("does not follow" in w for w in warns)
    assert {c["id"] for c in chunks} == {"labx", "asset"}
