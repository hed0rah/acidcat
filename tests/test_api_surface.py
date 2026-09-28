"""The Python API's names (review R10)."""

import pytest

import acidcat
import seeds


def test_patch_and_patcherror_are_exported():
    from acidcat.core.edit import Patch, PatchError
    assert acidcat.Patch is Patch and acidcat.PatchError is PatchError
    assert {"Patch", "PatchError"} <= set(acidcat.__all__)


def test_open_is_not_in_all_so_a_star_import_keeps_the_builtin():
    assert "open" not in acidcat.__all__
    ns = {}
    exec("from acidcat import *", ns)
    assert "open" not in ns


def test_open_takes_format():
    doc = acidcat.open(seeds.build("wav"), format="wav", forensics=False)
    assert doc.format.id == "wav" and doc.to_json()["format"]["forced"] is True
    with pytest.raises(TypeError):
        acidcat.open(seeds.build("wav"), fmt="wav")


def test_an_unrecognised_file_raises_unsupported():
    with pytest.raises(acidcat.Unsupported):
        acidcat.open(bytes(range(256)) * 8, forensics=False)
    with pytest.raises(acidcat.Unsupported, match="triage"):
        acidcat.open(seeds.build("unknown-container"), forensics=False)


def test_limits_say_which_values_were_honoured():
    doc = acidcat.open(seeds.build("wav"), forensics=False)
    assert doc.limits["applied"] == ["decode", "depth", "inflate_bytes"]


def test_a_findings_node_is_a_node():
    doc = acidcat.open(seeds.build("wav") + b"PK\x03\x04" + bytes(40))
    placed = [f for f in doc.findings if f.node_id]
    assert placed
    for f in placed:
        assert isinstance(f.node, acidcat.Node) and f.node.id == f.node_id
    loose = [f for f in doc.findings if not f.node_id]
    assert all(f.node is None for f in loose)
