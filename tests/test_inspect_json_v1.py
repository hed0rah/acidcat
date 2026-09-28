"""`inspect --json` is the contract v1 Document (milestone 4).

Every seed format's `inspect --json` validates against node-v1.schema.json,
and is the Document acidcat.open() builds from the same file, apart from what
the command line adds: `file.path` (the caller named the file) and, with
--anomalies, the forensic findings.
"""

import io
import json
import pathlib
import sys

import pytest

import acidcat
import seeds
from acidcat.cli import main

SCHEMA = json.loads((pathlib.Path(__file__).resolve().parent.parent / "docs"
                     / "contract" / "node-v1.schema.json").read_text(encoding="utf-8"))


def _inspect_json(*argv):
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = main(["inspect", "--json", *argv])
    finally:
        sys.stdout, sys.stderr = old
    return rc, [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]


@pytest.mark.parametrize("fmt", sorted(seeds.SEEDS))
def test_every_seed_validates(fmt, tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    p = tmp_path / ("seed" + seeds.suffix(fmt))
    p.write_bytes(seeds.build(fmt))
    rc, docs = _inspect_json(str(p))
    if rc == 2:
        pytest.skip(f"{fmt}: no walker reads the seed from its bytes alone")
    assert rc in (0, 1) and len(docs) == 1
    errs = list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(docs[0]))
    assert not errs, (errs[0].json_path, errs[0].message[:160])


def test_it_is_the_document_open_builds(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(seeds.build("wav"))
    _rc, (doc,) = _inspect_json(str(p))
    want = acidcat.open(p, forensics=False).to_json()
    assert doc["file"] == dict(want["file"], path=str(p))
    doc["file"] = want["file"]
    assert doc == json.loads(json.dumps(want))


def test_anomalies_add_the_forensic_findings(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(seeds.build("wav") + b"PK\x03\x04" + bytes(40))
    _rc, (plain,) = _inspect_json(str(p))
    _rc, (scanned,) = _inspect_json(str(p), "--anomalies")
    extra = [f for f in scanned["findings"] if f not in plain["findings"]]
    assert extra and all(f["code"].startswith("anomaly.") for f in extra)


def test_several_files_are_ndjson(tmp_path):
    paths = []
    for name in ("a.wav", "b.aiff"):
        p = tmp_path / name
        p.write_bytes(seeds.build("wav" if name.endswith("wav") else "aiff"))
        paths.append(str(p))
    rc, docs = _inspect_json(*paths)
    assert rc == 0 and [d["format"]["id"] for d in docs] == ["wav", "aiff"]


def test_only_the_root_id_is_the_whole_tree(tmp_path, capsys):
    """Review V13: the table's ids start with `RIFF/`, and `--only RIFF`
    said "names no chunk" (the root is the normaliser's, not a walker
    chunk). A node picks its subtree: the whole file, in the table and in
    the Document, and --exclude RIFF hides every chunk."""
    import json as _json
    import seeds as _seeds
    from acidcat.cli import main as _main
    p = tmp_path / "a.wav"
    p.write_bytes(_seeds.build("wav"))
    assert _main(["inspect", str(p), "--json"]) == 0
    full = _json.loads(capsys.readouterr().out)
    assert _main(["inspect", str(p), "--only", "RIFF", "--json"]) == 0
    assert _json.loads(capsys.readouterr().out)["nodes"] == full["nodes"]
    assert _main(["inspect", str(p), "--only", "RIFF", "--chunks"]) == 0
    out = capsys.readouterr().out
    assert "RIFF/fmt_" in out and "RIFF/data" in out
    assert _main(["inspect", str(p), "--exclude", "RIFF", "--chunks"]) == 0
    assert "showing 0 of 2 chunks" in capsys.readouterr().out
