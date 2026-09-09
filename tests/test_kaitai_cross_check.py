"""acidcat's byte facts, checked against a specification nobody here wrote.

Every other test in this suite measures acidcat against acidcat: a builder
encodes one person's reading of a format, and the walker is asserted to agree
with it. That catches a walker that drifts from its own fixture and cannot,
even in principle, catch a format that was misread the same way twice. The
generated corpus says so itself -- "if it is wrong, the corpus inherits that
error and the walker is measured against its own assumption".

Kaitai Struct's format library is an independent reading of the same formats,
maintained by other people from the same primary sources, and it is YAML, so
the comparison is mechanical rather than a careful afternoon with two PDFs.

OPT-IN, like every other external-corpus check here: point ACIDCAT_KSY_DIR at a
checkout of https://github.com/kaitai-io/kaitai_struct_formats. Nothing is
vendored, nothing is downloaded by the test, and CI without the variable skips.

WHAT IS ASSERTED, and what deliberately is not. A difference between the two is
not automatically an acidcat bug, and treating it as one would make this file a
machine for importing someone else's opinions:

  magic bytes           must agree. There is one right answer and both claim it.
  documented enum       where acidcat's own message calls a value undocumented,
                        every value the spec documents must be in acidcat's
                        table, or the tool contradicts the format.
  no invented entries   every value acidcat names, the spec must also name.
                        This is the direction that catches a fabricated
                        constant, which is the failure mode that matters most
                        in a forensics tool.

  breadth               NOT asserted. acidcat names 9 of 265 WAVE format tags
                        and falls back to `unknown 0x....`, which claims
                        nothing false. Importing 256 codec ids for hardware
                        nobody has owned since 1998 would be noise, not rigour.
"""
import os

import pytest

yaml = pytest.importorskip("yaml", reason="pyyaml is not installed")

_KSY = os.environ.get("ACIDCAT_KSY_DIR")

pytestmark = pytest.mark.skipif(
    not _KSY or not os.path.isdir(_KSY),
    reason="set ACIDCAT_KSY_DIR to a kaitai_struct_formats checkout")


def _spec(rel):
    path = os.path.join(_KSY, rel)
    if not os.path.isfile(path):
        pytest.skip(f"{rel} is not in this kaitai checkout")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _enum(spec, name):
    """{value: id} for one of the spec's enums."""
    table = (spec.get("enums") or {}).get(name)
    if table is None:
        pytest.skip(f"this kaitai revision has no enum {name!r}")
    return {int(k): (v["id"] if isinstance(v, dict) else v)
            for k, v in table.items()}


def _contents(spec, field_id, type_name=None):
    """The literal bytes a `contents:` field pins, from seq or a named type."""
    seqs = [spec.get("seq") or []]
    for name, body in (spec.get("types") or {}).items():
        if type_name in (None, name):
            seqs.append(body.get("seq") or [])
    for seq in seqs:
        for field in seq:
            if field.get("id") == field_id and "contents" in field:
                c = field["contents"]
                if isinstance(c, str):
                    return c.encode("ascii")
                # a list mixes literal strings with byte values:
                # ['Creative Voice File', 26]
                out = bytearray()
                for part in c:
                    if isinstance(part, int):
                        out.append(part)
                    else:
                        out += part.encode("ascii")
                return bytes(out)
    pytest.skip(f"no contents for {field_id!r} in this kaitai revision")


# ── magic bytes: one right answer, both sides claim it ──────────────

def test_au_magic_agrees():
    from acidcat.core.walk import au
    assert _contents(_spec("media/au.ksy"), "magic") == b".snd"
    # acidcat has no constant for it: the sniffer spells it inline, so the
    # check is that the sniffer answers to the spec's bytes.
    from acidcat.core.infra.sniff import sniff_bytes
    import struct
    head = b".snd" + struct.pack(">IIIII", 24, 64, 3, 44100, 1)
    assert sniff_bytes(head) == "au"


def test_ogg_magic_agrees():
    from acidcat.core.infra.sniff import sniff_bytes
    assert _contents(_spec("media/ogg.ksy"), "sync_code", "page") == b"OggS"
    assert sniff_bytes(b"OggS" + b"\x00" * 24) == "ogg"


def test_midi_magic_agrees():
    from acidcat.core.infra.sniff import sniff_bytes
    import seeds
    assert _contents(_spec("media/standard_midi_file.ksy"), "magic", "header") == b"MThd"
    assert sniff_bytes(seeds.build("midi")[:20]) == "midi"


def test_wav_chunk_fourccs_agree_where_both_name_one():
    """wav.ksy stores chunk ids as little-endian u4s, so decoding the enum
    recovers the four bytes each id is on disk. Only the ids BOTH sides name
    are compared: acidcat parses acid, cart, inst and smpl, which are real
    chunks this kaitai revision does not list, so a subset assertion in either
    direction would be measuring coverage rather than correctness.
    """
    from acidcat.core.infra.sniff import sniff_bytes
    from acidcat.core.walk.wav import _PARSERS

    import seeds
    spec = _spec("media/wav.ksy")
    ksy = {}
    for value, name in _enum(spec, "fourcc").items():
        ksy[name.lower()] = value.to_bytes(4, "little")
    assert ksy["riff"] == b"RIFF" and ksy["wave"] == b"WAVE"

    shared = 0
    for cid in _PARSERS:
        key = cid.strip().lower()
        if key in ksy:
            shared += 1
            # compared UNSTRIPPED: the trailing space in 'fmt ' is part of the
            # four bytes on disk, not padding in the name
            assert cid.encode("ascii").lower() == ksy[key].lower(), (
                f"acidcat spells this chunk {cid!r}, the spec has "
                f"{ksy[key]!r}")
    assert shared >= 4, f"only {shared} chunk ids were comparable"
    assert sniff_bytes(seeds.build("wav")[:20]) == "wav"


def test_voc_magic_agrees():
    from acidcat.core.infra.sniff import sniff_bytes
    import seeds
    magic = _contents(_spec("media/creative_voice_file.ksy"), "magic")
    assert magic == b"Creative Voice File\x1a"
    assert sniff_bytes(seeds.build("voc")[:24]) == "voc"


def test_s3m_magic_agrees():
    import seeds
    from acidcat.core.infra.sniff import sniff
    magic = _contents(_spec("media/tracker_modules/s3m.ksy"), "magic2")
    assert magic == b"SCRM"
    data = seeds.build("s3m")
    assert data[44:48] == magic


def test_xm_magic_agrees():
    import seeds
    spec = _spec("media/tracker_modules/fasttracker_xm_module.ksy")
    magic = _contents(spec, "signature0", "preheader")
    assert seeds.build("xm").startswith(magic)


# ── enums: the direction each one can actually be wrong in ──────────

def test_every_au_encoding_the_spec_documents_is_known_to_acidcat():
    """acidcat's own warning says a code "is not one of the documented codes",
    so a documented code missing from its table makes the tool contradict the
    format. This found six: 9, 16, 17, 22, 28 and 29."""
    from acidcat.core.walk.au import _ENC
    spec = _enum(_spec("media/au.ksy"), "encodings")
    missing = sorted(set(spec) - set(_ENC))
    assert not missing, (
        "au encoding codes the Kaitai spec documents and acidcat calls "
        "undocumented: " + ", ".join(f"{k} ({spec[k]})" for k in missing))


def test_acidcat_invents_no_au_encoding():
    """The direction that catches a fabricated constant."""
    from acidcat.core.walk.au import _ENC
    spec = _enum(_spec("media/au.ksy"), "encodings")
    invented = sorted(set(_ENC) - set(spec))
    assert not invented, (
        "au encoding codes acidcat names that the spec does not: "
        + ", ".join(f"{k} ({_ENC[k][0]!r})" for k in invented))


def test_acidcat_invents_no_wave_format_tag():
    """Breadth is deliberately not asserted -- acidcat names 9 of the registry
    and says `unknown 0x....` for the rest, which claims nothing false. What
    must hold is that the nine it does name are real."""
    from acidcat.core.infra.vocab import WAVE_FORMAT_TAGS
    spec = _enum(_spec("media/wav.ksy"), "w_format_tag_type")
    invented = sorted(set(WAVE_FORMAT_TAGS) - set(spec))
    assert not invented, (
        "WAVE format tags acidcat names that the spec does not: "
        + ", ".join(f"0x{k:04x} ({WAVE_FORMAT_TAGS[k]!r})" for k in invented))


# ── field layout ────────────────────────────────────────────────────

def test_the_au_header_field_order_agrees():
    """Both describe the same five big-endian u4s in the same order. acidcat
    reads them in one struct.unpack_from, so a disagreement here would be a
    silently shifted field rather than an error."""
    spec = _spec("media/au.ksy")
    seq = [f["id"] for f in (spec.get("seq") or []) if "contents" not in f]
    header = [f["id"] for f in spec["types"]["header"]["seq"]]
    assert seq[0] == "ofs_data"
    assert header[:4] == ["data_size", "encoding", "sample_rate",
                          "num_channels"]

    import struct

    import seeds
    from acidcat.core.walk.au import parse_header
    data = seeds.build("au")
    got = parse_header(data)
    off, size, enc, rate, chans = struct.unpack_from(">IIIII", data, 4)
    assert got["data_offset"] == off
    assert got["data_size"] == size
    assert got["encoding"] == enc
    assert got["sample_rate"] == rate
    assert got["channels"] == chans


def test_the_spec_library_was_actually_read():
    """A cross-check that silently matched nothing would pass forever. This is
    the same guard the geometry ratchet carries, for the same reason."""
    import glob
    found = glob.glob(os.path.join(_KSY, "**", "*.ksy"), recursive=True)
    assert len(found) > 50, (
        f"{_KSY} holds {len(found)} .ksy files; that is not the format library")
