"""Akai MPC walkers: the .mpcpattern JSON sequence (both the flat MPC2 and the
nested MPC3 event schema) and the .xpm XML keygroup program.

Fixtures are synthesized here from the documented shapes -- no real expansion."""
import json
import struct

from acidcat.core.sniff import sniff
from acidcat.core.walk import mpc
from acidcat.core.walk.base import Unsupported

_INT64_MAX = 2 ** 63 - 1


def _mpc2_note(pitch, vel, length):
    return {"type": 2050, "time": 0, "len": length, "1": pitch, "2": vel, "3": 0,
            "mod": 0, "modVal": 0.0, "prob": 100, "ratchet": 1}


def _mpc3_note(pitch, vel, length, prob=100, ratchet=1):
    return {"version": 2, "time": 0, "type": 3, "channel": 0,
            "note": {"version": 1, "note": pitch, "velocity": vel, "length": length,
                     "probability": prob, "ratchet": ratchet, "articulation": 0}}


def _write_pattern(tmp_path, events, length=_INT64_MAX):
    p = tmp_path / "p.mpcpattern"
    p.write_text(json.dumps({"pattern": {"length": length, "events": events}}))
    return str(p)


def test_mpcpattern_mpc2_flat(tmp_path):
    events = [{"type": 257, "time": 0, "len": 0, "1": 131},        # a header event
              _mpc2_note(38, 0.157, 268), _mpc2_note(42, 1.0, 100)]
    chunks, warns = mpc.inspect_mpcpattern(_write_pattern(tmp_path, events))
    assert warns == []
    pat = chunks[0]
    f = {x["name"]: x for x in pat["fields"]}
    assert f["schema"]["value"] == "MPC2"
    assert f["events"]["value"] == 3 and f["notes"]["value"] == 2
    assert "INT64_MAX" in f["length"]["note"]
    notes = next(c for c in chunks if c["id"] == "notes")
    assert notes["fields"][0]["value"] == "pitch 38, vel 0.157, len 268"


def test_mpcpattern_mpc3_rich(tmp_path):
    events = [_mpc3_note(39, 1.0, 239, prob=80, ratchet=2)]
    chunks, _ = mpc.inspect_mpcpattern(_write_pattern(tmp_path, events))
    assert chunks[0]["fields"][0]["value"] == "MPC3"
    note0 = next(c for c in chunks if c["id"] == "notes")["fields"][0]
    assert note0["value"] == "pitch 39, vel 1.0, len 239"
    assert "80% prob" in note0["note"] and "ratchet 2" in note0["note"]


def test_mpcpattern_sniffs_over_vital(tmp_path):
    # bare JSON starting with '{' would sniff as vital; the extension reroutes it
    p = _write_pattern(tmp_path, [_mpc2_note(60, 1.0, 96)])
    assert sniff(p) == "mpcpattern"


def test_mpcpattern_rejects_non_pattern_json(tmp_path):
    p = tmp_path / "x.mpcpattern"
    p.write_text('{"not": "a pattern"}')
    try:
        mpc.inspect_mpcpattern(str(p))
        assert False, "expected Unsupported"
    except Unsupported:
        pass


_XPM = ('<?xml version="1.0" encoding="UTF-8"?>\n<MPCVObject>\n'
        '  <Version><File_Version>2.1</File_Version></Version>\n'
        '  <Program type="Keygroup">\n'
        '    <ProgramName>Test Program</ProgramName>\n'
        '    <KeygroupNumKeygroups>2</KeygroupNumKeygroups>\n'
        '    <Instruments>\n'
        '      <Instrument><SampleName>Kick</SampleName></Instrument>\n'
        '      <Instrument><SampleName>Snare</SampleName></Instrument>\n'
        '      <Instrument><SampleName>Kick</SampleName></Instrument>\n'   # dup
        '    </Instruments>\n  </Program>\n</MPCVObject>\n')


def test_xpm_keygroup_program(tmp_path):
    p = tmp_path / "prog.xpm"
    p.write_text(_XPM)
    chunks, warns = mpc.inspect_xpm(str(p))
    assert warns == []
    f = {x["name"]: x["value"] for x in chunks[0]["fields"]}
    assert f["program_name"] == "Test Program"
    assert f["program_type"] == "Keygroup"
    assert f["keygroups"] == "2"
    assert f["file_version"] == "2.1"
    assert f["referenced_samples"] == 2            # deduped (Kick listed once)
    samples = next(c for c in chunks if c["id"] == "samples")
    assert [x["value"] for x in samples["fields"]] == ["Kick", "Snare"]


def test_xpm_sniffs_and_x11_pixmap_does_not(tmp_path):
    p = tmp_path / "prog.xpm"
    p.write_text(_XPM)
    assert sniff(str(p)) == "xpm"
    # an X11 pixmap shares the .xpm extension but is not an MPC program
    x11 = tmp_path / "icon.xpm"
    x11.write_text('/* XPM */\nstatic char *icon[] = {\n"16 16 2 1",\n};\n')
    assert sniff(str(x11)) != "xpm"


def test_xpm_rejects_non_mpc(tmp_path):
    p = tmp_path / "x.xpm"
    p.write_text("<?xml version='1.0'?><NotMPC/>")
    try:
        mpc.inspect_xpm(str(p))
        assert False, "expected Unsupported"
    except Unsupported:
        pass


def _make_xpn(tmp_path, deflated=False):
    import zipfile
    manifest = ('<?xml version="1.0"?><expansion version="2.0.0.0">'
                '<title>Test Expansion</title><manufacturer>ACME</manufacturer>'
                '<type>drum</type><version>1.0.0.0</version>'
                '<identifier>com.test.exp</identifier>'
                '<description>a test</description><img>cover.jpg</img></expansion>')
    p = tmp_path / "exp.xpn"
    comp = zipfile.ZIP_DEFLATED if deflated else zipfile.ZIP_STORED
    with zipfile.ZipFile(p, "w", comp) as z:
        z.writestr("Expansion.xml", manifest)
        z.writestr("Prog1.xpm", _XPM)
        z.writestr("kick.wav", b"RIFF____WAVE")
        z.writestr("cover.jpg", b"\xff\xd8\xff\xd9")
    return str(p)


def test_xpn_manifest_and_census(tmp_path):
    p = _make_xpn(tmp_path)
    assert sniff(p) == "xpn"
    chunks, warns = mpc.inspect_xpn(p)
    assert warns == []
    f = {x["name"]: x["value"] for x in chunks[0]["fields"]}
    assert f["title"] == "Test Expansion" and f["manufacturer"] == "ACME"
    assert f["type"] == "drum" and f["programs"] == 1 and f["samples"] == 1
    assert f["cover_image"] == "cover.jpg"


def test_xpn_stored_program_carves(tmp_path):
    p = _make_xpn(tmp_path, deflated=False)
    raw = open(p, "rb").read()
    chunks, _ = mpc.inspect_xpn(p)
    prog = next(c for c in chunks if c["id"] == "program")
    assert raw[prog["offset"]:prog["offset"] + prog["size"]] == _XPM.encode()
    comp = next(x for x in prog["fields"] if x["name"] == "compression")
    assert "stored" in comp["value"]


def test_xpn_deflated_program_noted(tmp_path):
    p = _make_xpn(tmp_path, deflated=True)
    chunks, _ = mpc.inspect_xpn(p)
    prog = next(c for c in chunks if c["id"] == "program")
    comp = next(x for x in prog["fields"] if x["name"] == "compression")
    assert "deflated" in comp["value"]     # honest: not a clean carve target


def test_xpn_not_a_zip(tmp_path):
    p = tmp_path / "bad.xpn"
    p.write_bytes(b"PK\x03\x04 not really")
    chunks, warns = mpc.inspect_xpn(str(p))
    assert warns == ["not a zip archive"]


def _make_xtd(tmp_path, name="Test Kit"):
    import gzip
    payload = {"data": {"version": 5, "name": name,
                        "program": {"name": name, "type": "Drum"},
                        "samples": [{"name": "Kick", "path": "../Samples/Kick.wav"},
                                    {"name": "Snare", "path": "../Samples/Snare.wav"}]}}
    body = (b"ACVS\n3.6.0.134\nSerialisableTrackData\njson\nLinux\n"
            + json.dumps(payload).encode())
    p = tmp_path / "kit.xtd"
    with gzip.open(p, "wb") as g:
        g.write(body)
    return str(p)


def test_xtd_kit(tmp_path):
    import gzip  # noqa: F401  (used by _make_xtd)
    p = _make_xtd(tmp_path)
    assert sniff(p) == "xtd"
    chunks, warns = mpc.inspect_xtd(p)
    assert warns == []
    f = {x["name"]: x for x in chunks[0]["fields"]}
    assert f["container"]["value"] == "ACVS"
    assert f["data_type"]["value"] == "SerialisableTrackData"
    assert f["name"]["value"] == "Test Kit"
    assert f["app_version"]["value"] == "3.6.0.134"
    assert f["program"]["value"] == "Test Kit" and f["program"]["note"] == "Drum"
    assert f["samples"]["value"] == 2
    samples = next(c for c in chunks if c["id"] == "samples")
    assert samples["fields"][0]["value"] == "Kick"
    assert samples["fields"][0]["note"] == "../Samples/Kick.wav"


def test_xtd_rejects_non_acvs(tmp_path):
    import gzip
    p = tmp_path / "x.xtd"
    with gzip.open(p, "wb") as g:
        g.write(b"not an acvs container")
    try:
        mpc.inspect_xtd(str(p))
        assert False, "expected Unsupported"
    except Unsupported:
        pass


def _make_snd(tmp_path, name="Kick", frames=100, stereo=False, classic=False):
    ch = 2 if stereo else 1
    hdr = bytearray(42 if classic else 38)
    hdr[0], hdr[1] = 1, (4 if classic else 2)      # classic hardware uses type 4
    hdr[2:18] = name.encode("latin-1")[:16].ljust(16, b" ")
    hdr[19] = 100                                  # level
    hdr[21] = 1 if stereo else 0
    struct.pack_into("<I", hdr, 0x1e, frames)      # total frame count
    if not classic:
        struct.pack_into("<I", hdr, 0x1a, frames)  # compact variant also at 0x1a
    p = tmp_path / (name + ".snd")
    p.write_bytes(bytes(hdr) + bytes(frames * 2 * ch))
    return str(p)


def test_snd_mono(tmp_path):
    p = _make_snd(tmp_path, frames=100)
    assert sniff(p) == "snd"
    chunks, warns = mpc.inspect_snd(p)
    assert warns == []
    f = {x["name"]: x for x in chunks[0]["fields"]}
    assert f["name"]["value"] == "Kick"
    assert f["channels"]["value"] == 1 and f["channels"]["note"] == "mono"
    assert f["frames"]["value"] == "100"
    assert f["header_bytes"]["value"] == 38
    pcm = next(c for c in chunks if c["id"] == "pcm")
    assert pcm["offset"] == 38 and pcm["size"] == 200      # 100 frames x 2 bytes mono


def test_snd_classic_42byte_header(tmp_path):
    # the documented hardware .snd has a 42-byte header with the count at 0x1e;
    # size-fit resolves it and the relaxed sniff (type byte 4) still catches it
    p = _make_snd(tmp_path, name="Classic", frames=500, classic=True)
    assert sniff(p) == "snd"
    chunks, warns = mpc.inspect_snd(p)
    assert warns == []
    f = {x["name"]: x["value"] for x in chunks[0]["fields"]}
    assert f["header_bytes"] == 42 and f["frames"] == "500"
    assert next(c for c in chunks if c["id"] == "pcm")["offset"] == 42


def test_snd_stereo_non_interleaved(tmp_path):
    p = _make_snd(tmp_path, frames=100, stereo=True)
    chunks, _ = mpc.inspect_snd(p)
    f = {x["name"]: x for x in chunks[0]["fields"]}
    assert f["channels"]["value"] == 2
    pcm = next(c for c in chunks if c["id"] == "pcm")
    assert pcm["size"] == 400                              # 100 x 2 bytes x 2ch
    assert "non-interleaved" in pcm["summary"]


def test_snd_not_confused_with_next_au(tmp_path):
    # a NeXT/Sun .snd starts with the ASCII magic '.snd' -- must not sniff as MPC
    p = tmp_path / "next.snd"
    p.write_bytes(b".snd" + b"\x00" * 40)
    assert sniff(str(p)) != "snd"


def _make_pgm_mpc1000(tmp_path, pads):
    pad0, padsz, npads, laysz = 24, 164, 64, 24
    data = bytearray(pad0 + npads * padsz + 236)
    struct.pack_into("<H", data, 0, len(data))
    data[4:20] = b"MPC1000 PGM 1.00"
    for pi, layers in enumerate(pads):
        base = pad0 + pi * padsz
        for li, nm in enumerate(layers):
            n = nm.encode("latin-1")
            data[base + li * laysz:base + li * laysz + len(n)] = n
    p = tmp_path / "kit.PGM"
    p.write_bytes(bytes(data))
    return str(p)


def test_pgm_mpc1000(tmp_path):
    p = _make_pgm_mpc1000(tmp_path, [["Kick", "KickB"], ["Snare"], ["Kick"]])
    assert sniff(p) == "pgm"                               # magic in sniff_bytes
    chunks, warns = mpc.inspect_pgm(p)
    f = {x["name"]: x["value"] for x in chunks[0]["fields"]}
    assert f["program_type"] == "MPC1000/2500"
    assert f["format"] == "MPC1000 PGM 1.00"
    assert f["pads_used"] == "3/64"
    assert f["referenced_samples"] == 3                    # Kick deduped
    pad0 = next(c for c in chunks if c["id"] == "pad[0]")
    assert [x["value"] for x in pad0["fields"]] == ["Kick", "KickB"]
    # each layer carries verified zone params in its note, at a real offset
    assert "level" in pad0["fields"][0]["note"] and "one-shot" in pad0["fields"][0]["note"]
    assert pad0["fields"][0]["off"] == 24                  # first layer of pad 0


def _make_pgm_mpc2000(tmp_path, names):
    data = bytearray(struct.pack("<H", len(names)))
    for nm in names:
        data += nm.encode("latin-1")[:16].ljust(16, b" ") + b"\x00"
    p = tmp_path / "kit2000.pgm"
    p.write_bytes(bytes(data))
    return str(p)


def test_pgm_mpc2000(tmp_path):
    p = _make_pgm_mpc2000(tmp_path, ["Kick", "Snare", "Hat"])
    assert sniff(p) == "pgm"                               # content-sniffed
    chunks, _ = mpc.inspect_pgm(p)
    f = {x["name"]: x["value"] for x in chunks[0]["fields"]}
    assert f["program_type"] == "MPC2000/2000XL/3000"
    assert f["referenced_samples"] == 3
    samples = next(c for c in chunks if c["id"] == "samples")
    assert [x["value"] for x in samples["fields"]] == ["Kick", "Snare", "Hat"]
