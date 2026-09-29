"""SFZ instruments. Fixtures are built here; the opt-in corpus test walks real
files when ACIDCAT_SFZ_CORPUS is set."""

import os

import pytest

from acidcat.core.formats import sfz as sfzmod
from acidcat.core.infra.sniff import sniff
from acidcat.core.walk import walk_file


def sfz_text(n=2, control=True, sample_dir="samples", extra=""):
    lines = ["// test instrument", "/* a block", "   comment */"]
    if control:
        lines.append(f"<control> default_path={sample_dir}/")
    lines.append("<group> group_label=Layer 1 lovel=1 hivel=127")
    for i in range(n):
        lines.append(f"<region> sample=hit {i}.wav key={36 + i} volume=-3")
    return ("\n".join(lines) + "\n" + extra).encode()


def make(tmp_path, data, name="t.sfz", samples=(), sample_dir="samples"):
    d = tmp_path / sample_dir
    d.mkdir(exist_ok=True)
    for s in samples:
        (d / s).write_bytes(b"RIFF")
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _codes(warns, chunks=()):
    return [getattr(w, "code", None) for w in list(warns)
            + [w for c in chunks for w in c.get("warnings", [])]]


def _f(c):
    return {f["name"]: f["value"] for f in c["fields"]}


def test_sniff_takes_the_extension_and_a_header(tmp_path):
    assert sniff(make(tmp_path, sfz_text())) == "sfz"
    # the extension alone is not enough
    assert sniff(make(tmp_path, b"key=value\n", name="x.sfz")) != "sfz"
    # a header inside a comment does not count
    assert sniff(make(tmp_path, b"// <region>\n", name="y.sfz")) != "sfz"


def test_sections_opcodes_and_their_bytes(tmp_path):
    data = sfz_text()
    label, chunks, warns = walk_file(make(tmp_path, data, samples=("hit 0.wav", "hit 1.wav")))
    assert label == "SFZ instrument"
    top = _f(chunks[0])
    assert (top["regions"], top["groups"], top["sample_files"]) == (2, 1, 2)
    assert top["default_path"] == "samples/"
    ids = [c["id"] for c in chunks]
    assert ids == ["sfz", "control", "group", "region", "region"]
    region = chunks[3]
    assert _f(region)["sample"] == "hit 0.wav"       # a value with a space
    # every opcode value sits on its own bytes
    for c in chunks[1:]:
        for fl in c["fields"]:
            at = c["payload_base"] + fl["off"]
            assert data[at:at + fl["len"]].decode() == fl["value"]
    assert not _codes(warns, chunks)


def test_a_missing_sample_is_an_environment_finding(tmp_path):
    _l, chunks, warns = walk_file(make(tmp_path, sfz_text(), samples=("hit 0.wav",)))
    msg = [str(w) for w in warns if getattr(w, "code", None) == "sibling.missing"]
    assert msg and "1 of 2" in msg[0] and "hit 1.wav" in msg[0]


def test_a_fragment_shaped_file_says_why_everything_may_be_missing(tmp_path):
    _l, _c, warns = walk_file(make(tmp_path, sfz_text(control=False)))
    msg = [str(w) for w in warns if getattr(w, "code", None) == "sibling.missing"]
    assert msg and "fragment" in msg[0]


def test_defines_expand_and_includes_are_looked_for(tmp_path):
    data = (b'#define $DIR samples\n#include "missing.sfz"\n'
            b"<region> sample=$DIR/a.wav\n")
    _l, chunks, warns = walk_file(make(tmp_path, data, samples=("a.wav",)))
    top = _f(chunks[0])
    assert top["sample_files"] == 1
    msgs = [str(w) for w in warns]
    assert any("missing.sfz" in m for m in msgs)
    assert not any("a.wav" in m for m in msgs)


def test_region_inherits_the_group_sample(tmp_path):
    data = b"<group> sample=shared.wav\n<region> key=1\n<region> key=2\n"
    _l, chunks, warns = walk_file(make(tmp_path, data, sample_dir=".",
                                       samples=("shared.wav",)))
    assert _f(chunks[0])["sample_files"] == 1
    assert not _codes(warns, chunks)


def test_generators_are_not_files(tmp_path):
    data = b"<region> sample=*sine\n"
    _l, chunks, warns = walk_file(make(tmp_path, data))
    assert _f(chunks[0])["sample_files"] == 0
    assert not _codes(warns, chunks)


def test_tokenizer_keeps_backslash_paths_and_stops_at_the_next_opcode():
    data = b"<region> sample=..\\One Shots\\kick 1.wav lokey=36 hikey=36\n"
    secs, _d = sfzmod.tokenize(data)
    ops = {k: v for k, _a, v, _b, _n in secs[0]["opcodes"]}
    assert ops["sample"] == "..\\One Shots\\kick 1.wav"
    assert ops["lokey"] == "36"
    assert sfzmod.sample_path(ops["sample"]) == "../One Shots/kick 1.wav"


_CORPUS = os.environ.get("ACIDCAT_SFZ_CORPUS")


@pytest.mark.skipif(not _CORPUS, reason="set ACIDCAT_SFZ_CORPUS to a folder of .sfz files")
def test_real_sfz_files_walk_without_defects():
    from acidcat.core.primitives.notes import DEFECT, kind_of
    seen, bad = 0, []
    for root, _d, names in os.walk(_CORPUS):
        for n in sorted(names):
            if n.lower().endswith(".sfz"):
                p = os.path.join(root, n)
                _label, chunks, warns = walk_file(p)
                seen += 1
                found = [str(w) for w in list(warns) + [w for c in chunks
                                                        for w in c.get("warnings", [])]
                         if kind_of(w) == DEFECT]
                if found:
                    bad.append((n, found[:2]))
    assert seen, "no .sfz under the corpus"
    assert not bad, bad[:5]
