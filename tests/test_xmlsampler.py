"""TAL-Sampler (.talsmpl) and UVI (.uvip) programs. Fixtures built here."""

from acidcat.core.infra.sniff import sniff
from acidcat.core.walk import walk_file


def tal_text(n=2, name="Test Bass"):
    zones = "".join(
        f'<multisample url="hit {i}.wav" urlRelativeToPresetDirectory="kit\\hit {i}.wav" '
        f'rootkey="{36 + i}" lowkey="{36 + i}" highkey="{36 + i}" velocitystart="0" '
        f'velocityend="127" startsample="0" endsample="1000" loopstartsample="10" '
        f'loopendsample="900" loopenabled="1"/>\n'
        for i in range(n))
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<tal curprogram="0" version="7.0">\n'
            f'<programs><program path="C:\\presets\\{name}.talsmpl" programname="{name}" '
            f'category="Bass"><samplelayer0><multisamples>\n{zones}</multisamples>'
            f'</samplelayer0></program></programs></tal>\n').encode()


def uvi_text(n=2, sample_dir="Audio"):
    groups = "".join(
        f'<Keygroup Name="Keygroup {i}" DisplayName="zone {i}" LowKey="{40 + i}" '
        f'HighKey="{40 + i}" LowVelocity="1" HighVelocity="127">\n'
        f'<Oscillators><SamplePlayer Name="Oscillator" SamplePath="./{sample_dir}/s{i}.wav"/>'
        f'</Oscillators></Keygroup>\n'
        for i in range(n))
    return (f'<UVI4>\n<Program Name="Program" DisplayName="Test Program" '
            f'ProgramPath="/Users/user/test.uvip" Polyphony="16">\n'
            f'<Layers><Layer><Keygroups>\n{groups}</Keygroups></Layer></Layers>\n'
            f'</Program>\n</UVI4>\n').encode()


def _f(c):
    return {f["name"]: f["value"] for f in c["fields"]}


def test_sniff(tmp_path):
    t = tmp_path / "a.talsmpl"
    t.write_bytes(tal_text())
    u = tmp_path / "a.uvip"
    u.write_bytes(uvi_text())
    assert sniff(str(t)) == "talsmpl" and sniff(str(u)) == "uvip"
    other = tmp_path / "b.talsmpl"
    other.write_bytes(b'<?xml version="1.0"?><preset/>')
    assert sniff(str(other)) != "talsmpl"


def test_tal_program_and_zones(tmp_path):
    p = tmp_path / "a.talsmpl"
    data = tal_text()
    p.write_bytes(data)
    label, chunks, warns = walk_file(str(p))
    assert label == "TAL-Sampler program"
    top = _f(chunks[0])
    assert (top["name"], top["category"], top["zones"]) == ("Test Bass", "Bass", 2)
    z = _f(chunks[2])
    assert (z["sample"], z["root_key"], z["loop_end"]) == ("hit 1.wav", "37", "900")
    for c in chunks[1:]:
        for fl in c["fields"]:
            at = c["payload_base"] + fl["off"]
            assert data[at:at + fl["len"]].decode() == fl["value"]
    assert not warns


def test_uvi_program_keygroups_and_missing_samples(tmp_path):
    (tmp_path / "Audio").mkdir()
    (tmp_path / "Audio" / "s0.wav").write_bytes(b"RIFF")
    p = tmp_path / "a.uvip"
    p.write_bytes(uvi_text())
    label, chunks, warns = walk_file(str(p))
    assert label == "UVI program"
    top = _f(chunks[0])
    assert (top["name"], top["keygroups"], top["sample_files"]) == ("Test Program", 2, 2)
    assert _f(chunks[1])["key_low"] == "40"
    msg = [str(w) for w in warns if getattr(w, "code", None) == "sibling.missing"]
    assert msg and "1 of 2" in msg[0] and "s1.wav" in msg[0]


def test_many_unclosed_tags_are_linear():
    # each unclosed '<tag' scanned [^>]* to the end of the file
    import time
    from acidcat.core.formats import xmlsampler as xs
    data = b'<tal version="1">' + b"<multisample " * 40_000
    t = time.perf_counter()
    out, total = xs.elements(data, b"multisample", 16)
    assert time.perf_counter() - t < 1.0
    assert out == [] and total == 0
