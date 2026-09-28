"""Negative findings must be claims about checks that actually ran.

An external audit demonstrated `audit` printing "no concealed or appended
data", "nothing else flagged" and "VERDICT: clean" for a file whose walker
raised Unsupported -- so anomalies, provenance and integrity never executed --
while `locate` found an embedded WAV container in the same file at 0.90
confidence. Empty results were being rendered as negative evidence.
"""

import struct

from acidcat.cli import main


def _wav(n_frames=2000):
    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", n_frames * 2) + bytes(n_frames * 2))
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def _unwalkable_with_hidden_wav(tmp_path):
    p = tmp_path / "poly.bin"
    p.write_bytes(bytes(range(256)) * 400 + _wav())
    return p


def test_unwalkable_file_is_not_called_clean(tmp_path, capsys):
    p = _unwalkable_with_hidden_wav(tmp_path)
    # 2: nothing was checked. 0 would be the same claim the report text is
    # careful not to make -- "clean" about scans that never ran.
    assert main(["audit", str(p)]) == 2
    out = capsys.readouterr().out
    assert "VERDICT: clean" not in out, "claimed clean without running the scans"
    assert "no walker" in out
    assert "no concealed or appended data" not in out
    assert "nothing else flagged" not in out


def test_it_points_at_the_verb_that_can_still_help(tmp_path, capsys):
    """locate does not need a walker, so the dead end has an exit."""
    p = _unwalkable_with_hidden_wav(tmp_path)
    main(["audit", str(p)])
    assert "acidcat locate" in capsys.readouterr().out


def test_json_marks_whether_the_scan_ran(tmp_path, capsys):
    """A consumer must be able to tell "scanned, found nothing" from "never
    ran" -- both render as empty lists."""
    import json
    p = _unwalkable_with_hidden_wav(tmp_path)
    main(["audit", str(p), "--json"])
    assert json.loads(capsys.readouterr().out)[0]["scanned"] is False

    good = tmp_path / "a.wav"
    good.write_bytes(_wav())
    main(["audit", str(good), "--json"])
    assert json.loads(capsys.readouterr().out)[0]["scanned"] is True


def test_a_walkable_file_still_reports_clean(tmp_path, capsys):
    """The fix must not turn every ordinary file into a caveat."""
    p = tmp_path / "a.wav"
    p.write_bytes(_wav())
    main(["audit", str(p)])
    out = capsys.readouterr().out
    assert "no walker" not in out
    assert "VERDICT: clean" in out
