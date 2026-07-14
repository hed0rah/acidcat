"""Akai MPC walkers: the .mpcpattern JSON sequence (both the flat MPC2 and the
nested MPC3 event schema) and the .xpm XML keygroup program.

Fixtures are synthesized here from the documented shapes -- no real expansion."""
import json

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
