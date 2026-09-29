"""Kontakt 2-4 patches and the Kontakt sample container (.nkx/.nkr, and the
body of a monolith .nki), through the NI walker.

Every fixture is built here. The layouts were measured on real libraries
(see formats/kontakt.py and formats/ni_container.py); the opt-in corpus test at
the bottom walks a bounded slice of one when ACIDCAT_KONTAKT_CORPUS is set.
"""

import os
import random
import struct
import zlib

import pytest

from acidcat.core.formats import kontakt as kt
from acidcat.core.formats import ni_container as nc
from acidcat.core.infra.sniff import sniff_bytes
from acidcat.core.walk import walk_file

_TS = 1_250_000_000                     # 2009-08-11


def header(hv=0x100, app=b"2noK", ver=(3, 3, 2, 2), zones=2, groups=1,
           programs=1, sample_bytes=1000, body_len=0, author=b"user",
           url=b"example.com", ulen=0):
    h = bytearray(kt.body_start(hv))
    h[0:4] = kt.MAGIC
    struct.pack_into("<I", h, 4, body_len)
    struct.pack_into("<H", h, 8, hv)
    h[0x10:0x14] = bytes(ver)
    h[0x14:0x18] = app
    struct.pack_into("<I", h, 0x18, _TS)
    struct.pack_into("<HHHI", h, 0x20, zones, groups, programs, sample_bytes)
    h[0x3A:0x3A + len(author)] = author
    h[0x45:0x45 + len(url)] = url
    if hv == 0x110:
        struct.pack_into("<I", h, 0xBA, ulen)
    return bytes(h)


def k2_xml(name="Test Patch", files=("@d007samplesF00000016000a.wav",
                                     "@d007samplesF00000016000b.wav")):
    zones = "".join(
        f'<K2_Zone index="{i}" groupIdx="0" version="0.70"><Sample>'
        f'<V name="file_ex2" value="{f}"/></Sample></K2_Zone>'
        for i, f in enumerate(files))
    return (f'<?xml version="1.0"?>\n<K2_Container index="0" name="(null)" '
            f'type="single_program" version="0.50"><Programs>'
            f'<K2_Program index="0" name="{name}" version="0.60"><Groups>'
            f'<K2_Group index="0"/></Groups><Zones>{zones}</Zones>'
            f'</K2_Program></Programs></K2_Container>').encode()


def k2_patch(**kw):
    """A Kontakt 2 .nki: header, then the zlib XML to the end of the file."""
    xml = kw.pop("xml", None) or k2_xml()
    return header(**kw) + zlib.compress(xml)


def fastlz_literals(data, level=2):
    """A valid FastLZ stream of literal runs only."""
    out = bytearray()
    for i in range(0, len(data), 32):
        run = data[i:i + 32]
        out.append(len(run) - 1)
        out += run
    if level == 2:
        out[0] |= 0x20
    return bytes(out)


def trailer(name="Test Patch", digest=False):
    xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
           f'<soundinfo version="400"><properties><name>{name}</name>'
           f'<author>user</author></properties><attributes><attribute>'
           f'<value>KontaktInstrument</value></attribute></attributes>'
           f'</soundinfo>').encode()
    pre = (b"$2a$04$" + b"x" * 53) if digest else b""
    return kt.TRAILER_MAGIC + b"\x01\x01\x0c\x00" + struct.pack("<I", len(xml)) + pre + xml


def k42_patch(plain=b"object tree " * 20, digest=False, ulen=None):
    """A Kontakt 4.2 .nki: header version 0x110, a FastLZ body, a trailer."""
    body = fastlz_literals(plain)
    return (header(hv=0x110, app=b"4noK", ver=(255, 4, 2, 4), body_len=len(body),
                   ulen=len(plain) if ulen is None else ulen)
            + body + trailer(digest=digest))


# ── the sample container ──

def _entry(etype, value, name):
    raw = name.encode("utf-16-le") + b"\0\0"
    return struct.pack("<HIH", 8 + len(raw), value, etype) + raw


def _directory(entries):
    return (nc.DIRECTORY + struct.pack("<HIIII", 0x110, 0, 0xFF, len(entries), 0)
            + b"".join(entries))


def sample_obj(payload):
    return (nc.SAMPLE + struct.pack("<HIII", 0x110, 0, 0xFF, 3) + b"\0"
            + struct.pack("<III", len(payload), 0, 0) + payload)


def resource_obj(payload):
    return (nc.RESOURCE + struct.pack("<HIIII", 0x110, 0, 0xFF, len(payload), 0)
            + payload)


def patch_obj(payload):
    return (nc.PATCH + struct.pack("<HIII", 0x110, 0, 0xFF, 1) + b"\0"
            + struct.pack("<II", len(payload), 0) + payload)


def container(files=(("a.wav", b"RIFF" + b"\0" * 40),
                     ("b.ncw", b"\x01\xa8\x9e\xd6" + b"\0" * 40)),
              resources=(("pic.tga", b"\0" * 24),), patch=None, extra_files=0,
              tail=b"", base=0):
    """A sample container: root -> 'Samples' -> files and resources, then the
    resource objects, the sample objects, and a patch object if given. Offsets
    are absolute, so `base` is where the container will sit in the file."""
    def sub(res_offsets):
        ents = [_entry(2, 0x1F000000 + i, n) for i, (n, _p) in enumerate(files)]
        ents += [_entry(2, 0x1F100000 + i, f"missing{i}.wav") for i in range(extra_files)]
        ents += [_entry(4, off, n) for off, (n, _p) in zip(res_offsets, resources)]
        return _directory(ents)

    def root(sub_off, patch_off):
        ents = [_entry(1, sub_off, "Samples")]
        if patch is not None:
            ents.append(_entry(3, patch_off, "Test Patch.nki"))
        return _directory(ents)

    root_len = len(root(0, 0))
    sub_off = base + root_len
    sub_len = len(sub([0] * len(resources)))
    pos = sub_off + sub_len
    res_offsets, objs = [], b""
    for _n, p in resources:
        res_offsets.append(pos + len(objs))
        objs += resource_obj(p)
    for _n, p in files:
        objs += sample_obj(p)
    patch_off = pos + len(objs)
    if patch is not None:
        objs += patch_obj(patch)
    return root(sub_off, patch_off) + sub(res_offsets) + objs + tail


def monolith():
    inner = k2_patch(app=b"4noK", ver=(255, 3, 0, 4))
    inner_hdr = header(app=b"4noK", ver=(255, 3, 0, 4))
    return inner_hdr + container(patch=inner, base=len(inner_hdr))


def _walk(tmp_path, data, name="t.nki", deep=False):
    p = tmp_path / name
    p.write_bytes(data)
    label, chunks, warns = walk_file(str(p), deep=deep)
    return chunks, warns


def _fields(chunk):
    return {f["name"]: f["value"] for f in chunk["fields"]}


def _codes(chunks, warns):
    out = [getattr(w, "code", None) for w in warns]
    for c in chunks:
        out += [getattr(w, "code", None) for w in c.get("warnings", [])]
    return out


# ── sniff ──

def test_both_magics_sniff_as_ni():
    assert sniff_bytes(k2_patch()[:64]) == "ni"
    assert sniff_bytes(container()[:64]) == "ni"


def test_the_container_magic_alone_is_not_enough():
    assert sniff_bytes(nc.DIRECTORY + b"\x00\x00" + b"\0" * 58) != "ni"


# ── Kontakt 2-4.1: zlib XML ──

def test_a_kontakt_2_patch_reads_its_header_and_xml(tmp_path):
    chunks, warns = _walk(tmp_path, k2_patch())
    head, patch = chunks[0], chunks[1]
    hf, pf = _fields(head), _fields(patch)
    assert hf["application"] == "Kon2 (Kontakt 2)"
    assert hf["app_version"] == "2.2.3.3"
    assert (hf["zones"], hf["groups"], hf["programs"]) == (2, 1, 1)
    assert hf["author"] == "user" and hf["url"] == "example.com"
    assert hf["timestamp"].startswith("2009-08-11")
    assert pf["program"] == "Test Patch"
    assert pf["first_sample"] == "samples/a.wav"
    assert pf["sample_files"] == "2 referenced"
    assert patch["offset"] == 0xAA
    assert not _codes(chunks, warns)


def test_header_counts_that_disagree_with_the_xml_are_a_defect(tmp_path):
    chunks, warns = _walk(tmp_path, k2_patch(zones=3))
    assert "count.mismatch" in _codes(chunks, warns)


def test_deep_lists_every_zone_sample(tmp_path):
    chunks, _ = _walk(tmp_path, k2_patch(), deep=True)
    rows = chunks[1]["rows"]
    assert [r["detail"] for r in rows] == ["samples/a.wav", "samples/b.wav"]


def test_a_body_that_does_not_inflate_is_a_defect(tmp_path):
    chunks, warns = _walk(tmp_path, header() + b"\x78\x9c" + b"\xff" * 32)
    assert "parse.failed" in _codes(chunks, warns)


def test_a_declared_body_longer_than_the_file_is_an_overrun(tmp_path):
    data = header(app=b"4noK", body_len=10_000) + zlib.compress(k2_xml())
    chunks, warns = _walk(tmp_path, data)
    assert "size.overrun" in _codes(chunks, warns)


# ── Kontakt 4.2: FastLZ and the soundinfo trailer ──

def test_a_kontakt_4_2_patch_reads_body_and_trailer(tmp_path):
    plain = b"object tree " * 20
    chunks, warns = _walk(tmp_path, k42_patch(plain, digest=True), deep=True)
    ids = [c["id"] for c in chunks]
    assert ids == ["header", "patch", "soundinfo"]
    hf = _fields(chunks[0])
    assert hf["uncompressed_length"] == len(plain)
    assert _fields(chunks[1])["decompressed"] == f"{len(plain):,} bytes"
    sf = _fields(chunks[2])
    assert sf["name"] == "Test Patch" and sf["author"] == "user"
    assert sf["attributes"] == "KontaktInstrument"
    assert sf["hash"].startswith("$2a$04$")
    assert not _codes(chunks, warns)


def test_a_body_that_decompresses_short_is_a_defect(tmp_path):
    chunks, warns = _walk(tmp_path, k42_patch(ulen=10_000), deep=True)
    assert "size.overrun" in _codes(chunks, warns)


def test_an_older_header_layout_says_so_and_claims_little(tmp_path):
    data = bytearray(header())
    struct.pack_into("<H", data, 8, 0x50)
    chunks, warns = _walk(tmp_path, bytes(data))
    assert len(chunks) == 1 and chunks[0]["size"] == 10
    assert "layout.unmeasured" in _codes(chunks, warns)


# ── the sample container ──

def test_a_container_pairs_names_with_objects_and_names_payloads(tmp_path):
    chunks, warns = _walk(tmp_path, container(), name="t.nkx")
    top = _fields(chunks[0])
    assert (top["files"], top["resources"], top["directories"]) == (2, 1, 1)
    assert top["names"] == "paired with stored objects"
    by_name = {_fields(c).get("name"): c for c in chunks[1:]}
    assert _fields(by_name["Samples/a.wav"])["payload"] == "RIFF WAV"
    assert _fields(by_name["Samples/b.ncw"])["payload"] == "NCW"
    assert by_name["Samples/pic.tga"]["id"] == "resource"
    assert not _codes(chunks, warns)


def test_an_opaque_payload_is_named_as_opaque_not_as_damage(tmp_path):
    data = container(files=(("c.ncw", b"\x5a\x11\x22\x33" + b"\0" * 20),))
    chunks, warns = _walk(tmp_path, data, name="t.nkx")
    assert _fields(chunks[-1])["payload"].startswith("opaque")
    assert not _codes(chunks, warns)


def test_names_are_not_paired_when_the_counts_differ(tmp_path):
    chunks, _ = _walk(tmp_path, container(extra_files=1), name="t.nkx")
    assert _fields(chunks[0])["names"] == "not paired"
    samples = [c for c in chunks if c["id"] == "sample"]
    assert samples and not any("name" in _fields(c) for c in samples)


def test_an_undecoded_object_kind_stops_the_walk_and_says_so(tmp_path):
    data = container(tail=nc.UNDECODED + b"\x10\x01" + b"\0" * 40)
    chunks, warns = _walk(tmp_path, data, name="t.nkx")
    assert "layout.unmeasured" in _codes(chunks, warns)


def test_an_unknown_object_is_a_defect(tmp_path):
    chunks, warns = _walk(tmp_path, container(tail=b"JUNK" * 10), name="t.nkx")
    assert "parse.failed" in _codes(chunks, warns)


def test_an_object_running_past_the_end_is_a_defect(tmp_path):
    data = container()
    chunks, warns = _walk(tmp_path, data[:-10], name="t.nkx")
    assert "parse.failed" in _codes(chunks, warns)


def test_a_monolith_holds_container_samples_and_its_patch(tmp_path):
    chunks, warns = _walk(tmp_path, monolith())
    ids = [c["id"] for c in chunks]
    assert ids[0] == "header" and ids[1] == "container"
    assert "patch_header" in ids
    inner = chunks[ids.index("patch_header") + 1]
    assert inner["id"] == "patch"
    assert _fields(inner)["program"] == "Test Patch"
    assert not _codes(chunks, warns)


def test_a_forged_entry_count_is_damage_not_a_cap(tmp_path):
    # four billion entries cannot fit in the file: that is a verdict about the
    # file, and must not be announced as the walker running out of budget
    data = bytearray(container())
    struct.pack_into("<I", data, 14, 0xFFFFFFFF)
    chunks, warns = _walk(tmp_path, bytes(data), name="t.nkx")
    codes = _codes(chunks, warns)
    assert "parse.failed" in codes and "cap.steps" not in codes


def test_a_directory_offset_off_the_end_is_a_defect(tmp_path):
    data = bytearray(container())
    # the root's only entry points at its subdirectory; send it past the file
    struct.pack_into("<I", data, 22 + 2, len(data) + 100)
    chunks, warns = _walk(tmp_path, bytes(data), name="t.nkx")
    assert "parse.failed" in _codes(chunks, warns)


# ── primitives ──

@pytest.mark.parametrize("stored, readable", [
    ("@v001Cd005Usersd004userF00000016000kick.wav", "C:/Users/user/kick.wav"),
    ("@d007samplesF-0001013000a b.wav", "samples/a b.wav"),
    ("@bd007SamplesF00000021000x.wav", "../Samples/x.wav"),
    ("@v000d003abcF00000016000y.aif", "abc/y.aif"),
    ("plain/path.wav", "plain/path.wav"),
    ("@q001x", "@q001x"),                   # an unknown token: as stored
    ("@d009tooshort", "@d009tooshort"),     # a length past the end: as stored
])
def test_decode_path(stored, readable):
    assert kt.decode_path(stored) == readable


def test_soundinfo_is_read_without_an_xml_parser():
    xml = (b"<soundinfo><properties><name>A &amp; B</name></properties>"
           b"<attributes><attribute><value>X</value></attribute></attributes>"
           b"</soundinfo>")
    assert kt.parse_soundinfo(xml) == {"name": "A & B", "attributes": ["X"]}


# ── real files, opt-in ──

_CORPUS = os.environ.get("ACIDCAT_KONTAKT_CORPUS")


@pytest.mark.skipif(not _CORPUS, reason="set ACIDCAT_KONTAKT_CORPUS to a Kontakt library")
def test_real_kontakt_files_walk_clean():
    """A fixed slice of real patches and containers: none raise, and none
    report a defect. Opaque (encrypted) payloads and the undecoded monolith
    object kind are coverage notes, not defects."""
    from acidcat.core.primitives.notes import DEFECT, kind_of

    def is_defect(w):
        return kind_of(w) == DEFECT
    paths = []
    for root, _d, names in os.walk(_CORPUS):
        for n in names:
            if n.lower().endswith((".nki", ".nkm", ".nkx", ".nkr")):
                paths.append(os.path.join(root, n))
    paths.sort()
    random.Random(7).shuffle(paths)
    seen = 0
    bad = []
    for p in paths:
        with open(p, "rb") as f:
            head = f.read(6)
        if not (kt.is_kontakt(head) or nc.is_container(head)):
            continue
        seen += 1
        _label, chunks, warns = walk_file(p)
        found = [str(w) for w in warns if is_defect(w)]
        for c in chunks:
            found += [str(w) for w in c.get("warnings", []) if is_defect(w)]
        if found:
            bad.append((os.path.basename(p), found[:2]))
        if seen >= 300:
            break
    assert seen, "no Kontakt 2-4 patch or sample container under the corpus"
    assert not bad, bad[:5]
