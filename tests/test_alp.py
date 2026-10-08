"""Ableton Live Packs (.alp). Every pack is built here with the index grammar
measured on real packs (core/formats/alp.py); the opt-in corpus test walks
real ones when ACIDCAT_ALP_CORPUS is set."""

import gzip
import os
import struct

import pytest

from acidcat.core.extract import samples as samplesmod
from acidcat.core.formats import alp as alpmod
from acidcat.core.infra.sniff import sniff
from acidcat.core.walk import walk_file


def _u32(v):
    return struct.pack("<I", v)


def _u64(v):
    return struct.pack("<Q", v)


def _text(s):
    return _u32(len(s)) + s.encode("utf-16-le")


def _cls(name):
    return bytes([len(name)]) + name.encode("latin-1")


def _list(items, size=None):
    """Items are (class, body); a list ends in a zero byte and an empty name."""
    out = _u32(len(items) if size is None else size)
    for i, (cls, body) in enumerate(items):
        out += b"\x00" + _cls(cls) + _u32(i) + body
    return out + b"\x00\x00"


def _meta(pairs):
    if not pairs:
        return _list([])
    items = []
    for key, value in pairs:
        if isinstance(value, int):
            vals = [("Int64Metadatum", _u64(value))]
        else:
            vals = [("StringMetadatum", _text(value))]
        items.append(("MetadataItem", _list(vals) + _text(key)))
    return _list([("MetadataRec", _list(items) + _u32(4))])


def _item(name, children=(), is_dir=False, off=0, size=0, orig=0, meta=()):
    return (_text(name) + _u32(1_700_000_000)
            + _list([("PackageItem", c) for c in children])
            + bytes([int(is_dir), 0]) + _u64(off) + _u64(size) + _u64(orig)
            + bytes([int(bool(orig))]) + _meta(meta) + b"\x00")


# the class definitions a real index carries before its instances; the reader
# looks up how each metadatum class stores its value here
_DEFS = (_cls("StringMetadatum") + _u32(1) + _text("Value") + b"\x14"
         + _cls("Int64Metadatum") + _u32(1) + _text("Value") + b"\x18")


def alp_bytes(files=(("Samples/kick.wav.flac", b"fLaC" + b"\x01" * 60, 200),
                     ("Samples/._kick.wav", b"\x00\x05\x16\x07" + bytes(28), 0),
                     ("Test.als", b"\x1f\x8b" + b"\x02" * 30, 0)),
              pack="Test Project", corrupt_offset=False, index_offset_delta=0):
    """A Live Pack: gzip over 'pl-a', the files back to back, then the index.
    Files are grouped into folders by their first path part."""
    data = b""
    placed = []
    for path, blob, orig in files:
        placed.append((path, alpmod.DATA_START + len(data), len(blob), orig))
        data += blob
    folders = {}
    top = []
    for path, off, size, orig in placed:
        if corrupt_offset and path.endswith(".als"):
            off += 3
        meta = [("uid", "abc123"), ("document-is-backup", 1)] if path.endswith(".als") else []
        name = path.rsplit("/", 1)[-1]
        body = _item(name, off=off, size=size, orig=orig, meta=meta)
        if "/" in path:
            folders.setdefault(path.split("/", 1)[0], []).append(body)
        else:
            top.append(body)
    kids = [_item(f, children=b, is_dir=True) for f, b in folders.items()] + top
    index = (alpmod.INDEX_MAGIC + b"\x04\x00\x00\x00\x00\x00" + _DEFS
             + _item(pack, children=kids, is_dir=True))
    idx = alpmod.DATA_START + len(data)
    body = b"pl-a" + _u32(idx + index_offset_delta) + _u32(0) + data + index
    return gzip.compress(body)


def _walk(tmp_path, data, name="t.alp"):
    p = tmp_path / name
    p.write_bytes(data)
    return walk_file(str(p))


def _f(c):
    return {f["name"]: f["value"] for f in c["fields"]}


def _codes(chunks, warns):
    return [getattr(w, "code", None) for w in list(warns)
            + [w for c in chunks for w in c.get("warnings", [])]]


def test_sniff_tells_a_pack_from_a_gzipped_live_document(tmp_path):
    p = tmp_path / "t.alp"
    p.write_bytes(alp_bytes())
    assert sniff(str(p)) == "alp"
    q = tmp_path / "t.als"
    q.write_bytes(gzip.compress(b'<?xml version="1.0"?><Ableton><LiveSet/></Ableton>'))
    assert sniff(str(q)) == "als"


def test_a_pack_lists_its_files_and_their_metadata(tmp_path):
    label, chunks, warns = _walk(tmp_path, alp_bytes())
    assert label == "Ableton Live Pack"
    root = _f(chunks[0])
    assert (root["pack"], root["files"], root["folders"]) == ("Test Project", 3, 1)
    layer = chunks[0]["layer_chunks"]
    files = [c for c in layer if c["id"] == "file"]
    by_path = {_f(c)["path"]: c for c in files}
    flac = by_path["Samples/kick.wav.flac"]
    assert flac["offset"] == alpmod.DATA_START and _f(flac)["original"] == "200 bytes"
    als = _f(by_path["Test.als"])
    assert als["uid"] == "abc123" and als["document-is-backup"] == "1"
    assert not _codes(chunks, warns)


def test_files_that_do_not_tile_the_data_are_a_defect(tmp_path):
    _l, chunks, warns = _walk(tmp_path, alp_bytes(corrupt_offset=True))
    assert "geometry.invalid" in _codes(chunks, warns)


def test_an_index_offset_past_the_end_dangles(tmp_path):
    _l, chunks, warns = _walk(tmp_path, alp_bytes(index_offset_delta=10_000))
    assert "pointer.dangling" in _codes(chunks, warns)


def test_a_truncated_gzip_is_reported(tmp_path):
    data = alp_bytes()
    _l, chunks, warns = _walk(tmp_path, data[:len(data) // 2])
    assert "parse.failed" in _codes(chunks, warns)


def test_a_sparse_list_is_read_to_its_terminator():
    # a size of 2 holding one item, as newer packs write their metadata
    body = _list([("StringMetadatum", _text("v"))], size=2) + _text("k")
    r = alpmod._Reader(_DEFS + body, len(_DEFS))
    assert alpmod._metadata_item(r, [100, {}]) == ("k", ["v"])


def test_extract_writes_the_audio_as_stored_and_skips_sidecars(tmp_path):
    p = tmp_path / "t.alp"
    p.write_bytes(alp_bytes())
    got = list(samplesmod.iter_samples(str(p)))
    assert [g["name"] for g in got] == ["Samples_kick.wav"]
    assert got[0]["ext"] == "flac" and got[0]["wav"].startswith(b"fLaC")


_CORPUS = os.environ.get("ACIDCAT_ALP_CORPUS")


@pytest.mark.skipif(not _CORPUS, reason="set ACIDCAT_ALP_CORPUS to a folder of .alp files")
def test_real_packs_walk_clean():
    from acidcat.core.primitives.notes import DEFECT, kind_of
    seen, bad = 0, []
    for root, _d, names in os.walk(_CORPUS):
        for n in sorted(names):
            if not n.lower().endswith(".alp") or n.startswith("._"):
                continue
            _label, chunks, warns = walk_file(os.path.join(root, n))
            seen += 1
            found = [str(w) for w in list(warns) + [w for c in chunks
                                                    for w in c.get("warnings", [])]
                     if kind_of(w) == DEFECT]
            if found:
                bad.append((n, found[:2]))
    assert seen, "no .alp under the corpus"
    assert not bad, bad[:5]


# ── regressions from the 2026-10-02 bug hunt ──

def test_a_pack_cut_before_its_trailer_is_damage_not_verified(tmp_path):
    # the stream stopped short without raising, and the pl-a layer was
    # declared verified by a CRC nobody checked
    _label, chunks, warns = _walk(tmp_path, alp_bytes()[:-8])
    assert "parse.failed" in _codes(chunks, warns)
    assert "layer" not in chunks[0]


def test_a_bad_crc_still_walks_the_whole_container(tmp_path):
    # zlib's gzip mode dropped the last read's output on a bad CRC, so the
    # index read as dangling
    data = bytearray(alp_bytes())
    data[-6] ^= 0xFF
    _label, chunks, warns = _walk(tmp_path, bytes(data))
    codes = _codes(chunks, warns)
    assert "checksum.mismatch" in codes and "pointer.dangling" not in codes
    assert _f(chunks[0])["files"] == 3
    assert "layer" not in chunks[0]


def test_an_intact_pack_is_still_verified(tmp_path):
    _label, chunks, warns = _walk(tmp_path, alp_bytes())
    assert chunks[0]["layer"]["verdict"]["result"] == "verified"
    assert not [c for c in _codes(chunks, warns) if c and not c.startswith("cap.")]
