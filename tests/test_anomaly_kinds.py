"""Each forensic rule has the kind of what it says about the file (review
R8). A suspicion is info and audit exits 0 for it; a rule that catches the
file breaking its own format is a defect and audit exits 1."""

import struct

import pytest

from acidcat.cli import main
from acidcat.core.infra.findings import ANOMALY_RULES, DEFECT, INFO, REGISTRY

_INFO = {"trailing_data", "polyglot", "embedded_standalone_media",
         "json_trailing_data", "json_unknown_key", "unaccounted_bytes",
         "mp4_mdat_coverage", "dual_endianness", "cavity_content",
         "application_block", "ogg_multistream", "lsb_entropy",
         "nonprintable_text", "nonzero_pad", "id3_padding_nonzero"}
_DEFECT = {"wrong_format_tag", "duplicate_chunk", "duplicate_frame",
           "id3_swallows_frames"}


def test_every_rule_has_its_kind():
    assert _INFO | _DEFECT == set(ANOMALY_RULES) and not _INFO & _DEFECT
    for r in _INFO:
        assert REGISTRY[f"anomaly.{r}"][0] == INFO, r
    for r in _DEFECT:
        assert REGISTRY[f"anomaly.{r}"][0] == DEFECT, r


def _ck(cid, p):
    return cid + struct.pack("<I", len(p)) + p + (b"\0" if len(p) & 1 else b"")


def _wav(*extra, tail=b""):
    body = (b"WAVE" + _ck(b"fmt ", struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16))
            + b"".join(extra) + _ck(b"data", bytes(64)))
    return b"RIFF" + struct.pack("<I", len(body)) + body + tail


def test_a_polyglot_is_reported_and_exits_0(tmp_path, capsys):
    p = tmp_path / "poly.wav"
    p.write_bytes(_wav(tail=b"PK\x03\x04" + bytes(60)))
    assert main(["audit", str(p)]) == 0
    assert "polyglot" in capsys.readouterr().out


def test_a_duplicate_chunk_is_a_defect_and_exits_1(tmp_path, capsys):
    fmt2 = _ck(b"fmt ", struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16))
    p = tmp_path / "dup.wav"
    p.write_bytes(_wav(fmt2))
    assert main(["audit", str(p)]) == 1


def _codes(path):
    import acidcat
    return [f.code for f in acidcat.open(path, forensics=False).findings]


def test_a_riff_size_short_of_appended_bytes_is_info(tmp_path):
    p = tmp_path / "tail.wav"
    p.write_bytes(_wav(tail=bytes(range(1, 40))))
    assert "container.trailing" in _codes(p) and "count.mismatch" not in _codes(p)


def test_a_riff_size_short_of_a_real_chunk_is_a_defect(tmp_path):
    """A writer that appended a LIST and forgot the size: damage."""
    raw = _wav() + _ck(b"LIST", b"INFO" + _ck(b"INAM", b"late\0\0"))
    p = tmp_path / "under.wav"
    p.write_bytes(raw)
    codes = _codes(p)
    assert "count.mismatch" in codes and "container.trailing" not in codes
    assert main(["audit", str(p)]) == 1


def test_a_riff_size_past_the_file_is_a_defect(tmp_path):
    raw = bytearray(_wav())
    struct.pack_into("<I", raw, 4, len(raw) + 100)
    p = tmp_path / "trunc.wav"
    p.write_bytes(bytes(raw))
    assert "count.mismatch" in _codes(p)


@pytest.mark.parametrize("fmt", ["wav", "aiff"])
def test_appended_bytes_add_only_info(tmp_path, fmt):
    """The seed's own findings stay (the AIFF seed's COMM and SSND disagree);
    what the tail adds is info, from the walker and the normaliser alike."""
    import seeds
    from collections import Counter
    import acidcat
    base, tail = tmp_path / ("a." + fmt), tmp_path / ("b." + fmt)
    base.write_bytes(seeds.build(fmt))
    tail.write_bytes(seeds.build(fmt) + bytes(range(1, 40)))
    added = Counter(_codes(tail)) - Counter(_codes(base))
    kinds = {f.code: f.kind for f in acidcat.open(tail, forensics=False).findings}
    assert added and all(kinds[c] == "info" for c in added), added
