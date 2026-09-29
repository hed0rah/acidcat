"""Logic EXS24 instruments (.exs). Every fixture is built here; the layout was
measured on real instruments and their SFZ twins (core/formats/exs.py). The
opt-in corpus test walks real files when ACIDCAT_EXS_CORPUS is set."""

import os
import struct

import pytest

from acidcat.core.formats import exs as exsmod
from acidcat.core.infra.sniff import sniff_bytes
from acidcat.core.walk import walk_file


def chunk(ctype, name, data, e="<", flags=0):
    sig = 0x00000101 | (ctype << 24) if e == "<" else 0x00010000 | ctype
    magic = b"TBOS" if e == "<" else b"SOBT"
    head = struct.pack(e + "IIII", sig | (flags << 24 if e == "<" else 0), len(data), 0, 0)
    return head + magic + name.encode().ljust(64, b"\0") + data


def instrument(zones, groups, samples, params, e="<"):
    data = struct.pack(e + "IIIII", 0, zones, groups, samples, params) + bytes(20)
    c = chunk(0, "Test Kit", data, e)
    # the instrument signature is 0x00000101 (type 0), not the zone form
    sig = struct.pack(e + "I", 0x00000101 if e == "<" else 0x00010000)
    return sig + c[4:]


def zone(root=60, lo=48, hi=72, vlo=1, vhi=127, start=0, end=1000, ls=100, le=900,
         vol=-5, pan=10, tune=-3, group=0, sample=0, size=104, e="<"):
    z = bytearray(size)
    z[1], z[2], z[3], z[4] = root, tune & 0xFF, pan & 0xFF, vol & 0xFF
    z[6], z[7], z[9], z[10] = lo, hi, vlo, vhi
    struct.pack_into(e + "IIII", z, 12, start, end, ls, le)
    struct.pack_into(e + "ii", z, 88, group, sample)
    return chunk(1, f"zone {root}", bytes(z), e)


def sample(name="kick.wav", frames=1000, rate=44100, bits=24, ch=2, long=False,
           e="<"):
    s = bytearray(600 if long else 336)
    struct.pack_into(e + "IIIII", s, 0, 44, frames, rate, bits, ch)
    s[28:32] = b"EVAW" if e == "<" else b"WAVE"
    struct.pack_into(e + "I", s, 32, 44 + frames * ch * bits // 8)
    path = b"/Users/user/Samples/"
    s[80:80 + len(path)] = path
    if long:
        s[336:336 + len(name)] = name.encode()
    return chunk(3, name, bytes(s), e)


def exs_file(e="<", zones=2, bad_sample=False, count_off=0, long=False):
    body = b"".join(zone(root=60 + i, sample=(9 if bad_sample and i == 1 else i), e=e)
                    for i in range(zones))
    body += chunk(2, "Group 1", bytes(120), e)
    body += b"".join(sample(f"s{i}.wav", e=e, long=long) for i in range(zones))
    body += chunk(4, "", bytes(304), e)
    return instrument(zones + count_off, 1, zones, 1, e) + body


def _walk(tmp_path, data):
    p = tmp_path / "t.exs"
    p.write_bytes(data)
    return walk_file(str(p))


def _f(c):
    return {f["name"]: f["value"] for f in c["fields"]}


def _codes(chunks, warns):
    return [getattr(w, "code", None) for w in list(warns)
            + [w for c in chunks for w in c.get("warnings", [])]]


def test_sniff_needs_the_magic_and_an_instrument_chunk_first():
    assert sniff_bytes(exs_file()[:64]) == "exs"
    assert sniff_bytes(exs_file(e=">")[:64]) == "exs"
    # a zone chunk first is not an instrument file
    assert sniff_bytes(zone()[:64]) != "exs"


def test_an_instrument_reads_its_counts_zones_and_samples(tmp_path):
    label, chunks, warns = _walk(tmp_path, exs_file())
    assert label == "Logic EXS24 instrument"
    ids = [c["id"] for c in chunks]
    assert ids == ["instrument", "zone", "zone", "group", "sample", "sample", "parameters"]
    inst = _f(chunks[0])
    assert (inst["zones"], inst["groups"], inst["samples"]) == (2, 1, 2)
    z = _f(chunks[2])
    assert (z["root_key"], z["key_low"], z["key_high"]) == (61, 48, 72)
    assert (z["volume"], z["pan"], z["fine_tune"]) == (-5, 10, -3)
    assert (z["sample_end"], z["loop_start"], z["loop_end"]) == (1000, 100, 900)
    assert z["sample"] == 1
    zone_fields = {f["name"]: f for f in chunks[2]["fields"]}
    assert zone_fields["sample"]["note"] == "'s1.wav'"
    s = _f(chunks[4])
    assert (s["frames"], s["sample_rate"], s["bits"], s["channels"]) == (1000, 44100, 24, 2)
    assert s["file_type"].startswith("WAVE")
    assert s["path"] == "/Users/user/Samples/"
    assert not _codes(chunks, warns)


def test_a_big_endian_instrument_reads_the_same(tmp_path):
    _label, chunks, warns = _walk(tmp_path, exs_file(e=">"))
    z = _f(chunks[1])
    assert (z["root_key"], z["sample_end"], z["volume"]) == (60, 1000, -5)
    assert _f(chunks[4])["file_type"] == "WAVE"
    assert "big-endian" in chunks[0]["summary"]
    assert not _codes(chunks, warns)


def test_the_long_sample_form_carries_a_file_name(tmp_path):
    _label, chunks, _w = _walk(tmp_path, exs_file(long=True))
    assert _f(chunks[4])["file_name"] == "s0.wav"


def test_a_zone_naming_a_missing_sample_is_unresolved(tmp_path):
    _label, chunks, warns = _walk(tmp_path, exs_file(bad_sample=True))
    assert "reference.unresolved" in _codes(chunks, warns)


def test_an_empty_zone_names_no_sample_and_is_not_damage(tmp_path):
    # -1 is "no sample", as it is "no group"; real instruments carry these
    data = bytearray(exs_file())
    struct.pack_into("<i", data, exsmod.HEAD + 40 + exsmod.HEAD + 92, -1)
    _label, chunks, warns = _walk(tmp_path, bytes(data))
    assert _f(chunks[1])["sample"] == -1
    assert not _codes(chunks, warns)


def test_counts_that_disagree_with_the_chunks_are_a_defect(tmp_path):
    _label, chunks, warns = _walk(tmp_path, exs_file(count_off=1))
    assert "count.mismatch" in _codes(chunks, warns)


def test_a_chunk_running_past_the_end_is_an_overrun(tmp_path):
    _label, chunks, warns = _walk(tmp_path, exs_file()[:-100])
    assert "size.overrun" in _codes(chunks, warns)


def test_trailing_bytes_too_short_for_a_header_are_reported(tmp_path):
    _label, chunks, warns = _walk(tmp_path, exs_file() + b"\0" * 20)
    assert "chunk.short" in _codes(chunks, warns)


def test_the_type_flag_bit_does_not_change_the_type(tmp_path):
    # real files set 0x40 in the type byte (0x41 zone, 0x43 sample)
    data = bytearray(exs_file())
    first_zone = exsmod.HEAD + 20 + 20
    data[first_zone + 3] |= 0x40
    _label, chunks, warns = _walk(tmp_path, bytes(data))
    assert chunks[1]["id"] == "zone" and _f(chunks[1])["root_key"] == 60
    assert not _codes(chunks, warns)


def test_an_undecoded_chunk_type_is_noted_not_damage(tmp_path):
    # type 8, a four-byte chunk real instruments carry
    _label, chunks, warns = _walk(tmp_path, exs_file() + chunk(8, "", bytes(4)))
    assert _codes(chunks, warns) == ["layout.unmeasured"]


def test_note_names_use_logic_octaves():
    assert exsmod.note_name(60) == "C3"
    assert exsmod.note_name(0) == "C-2"
    assert exsmod.note_name(127) == "G8"


_CORPUS = os.environ.get("ACIDCAT_EXS_CORPUS")


@pytest.mark.skipif(not _CORPUS, reason="set ACIDCAT_EXS_CORPUS to a folder of .exs files")
def test_real_instruments_walk_clean():
    from acidcat.core.primitives.notes import DEFECT, kind_of
    seen, bad = 0, []
    for root, _d, names in os.walk(_CORPUS):
        for n in sorted(names):
            if not n.lower().endswith(".exs"):
                continue
            p = os.path.join(root, n)
            _label, chunks, warns = walk_file(p)
            seen += 1
            found = [str(w) for w in list(warns) + [w for c in chunks
                                                    for w in c.get("warnings", [])]
                     if kind_of(w) == DEFECT]
            if found:
                bad.append((n, found[:2]))
    assert seen, "no .exs under the corpus"
    assert not bad, bad[:5]
