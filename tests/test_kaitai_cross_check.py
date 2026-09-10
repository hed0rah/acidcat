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

Point ACIDCAT_KSY_DIR at a checkout of
https://github.com/kaitai-io/kaitai_struct_formats. Nothing is vendored and
nothing is downloaded by the test itself, so a contributor without the specs
gets skips rather than failures.

CI IS NOT OPTIONAL ABOUT IT. `.github/workflows/test.yml` clones the specs at a
PINNED commit and fails the job if this file skips. For a while it did not, and
this file skipped on all five platforms -- which is how the `synchsafe` mask it
had already found could have been reverted with the matrix still green. The
pin is deliberate: the specs are a corpus, and a corpus that changes between
runs turns a reproducible failure into a flake, so upstream's corrections
arrive when someone bumps the SHA and reads what moved.

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


# ── VOC: two enums, and acidcat has a table for one of them ─────────


def test_every_voc_codec_the_spec_documents_is_known_to_acidcat():
    """acidcat's `_FMT` names the codec for a VOC block. Where the spec
    documents a code and acidcat does not, a real file gets described by a
    walker that does not know what it is holding."""
    from acidcat.core.walk.voc import _FMT

    spec = _enum(_spec("media/creative_voice_file.ksy"), "codecs")
    missing = sorted(set(spec) - set(_FMT))
    assert not missing, (
        "the spec documents VOC codecs acidcat has no name for: "
        + ", ".join("%d (%s)" % (v, spec[v]) for v in missing))


def test_acidcat_invents_no_voc_codec():
    """The direction that matters most in a forensics tool: a constant that
    exists nowhere but here. Reported as a set difference so a failure names
    the value rather than the count."""
    from acidcat.core.walk.voc import _FMT

    spec = _enum(_spec("media/creative_voice_file.ksy"), "codecs")
    invented = sorted(set(_FMT) - set(spec))
    assert not invented, (
        "acidcat names VOC codecs the spec does not: "
        + ", ".join("%d -> %r" % (v, _FMT[v][0]) for v in invented))


def test_every_voc_block_type_the_spec_documents_is_handled():
    """Block types are an if/elif chain here rather than a table, so the set
    has to be recovered from the walker's source. That is uglier than reading a
    dict and it is the honest way to ask the question: a type the spec
    documents and the walker has no branch for is a block acidcat will skip.

    Recovered from the source rather than by walking a file per type, because a
    seed carrying all ten would be asserting the seed, not the walker.
    """
    import inspect
    import re

    from acidcat.core.walk import voc

    src = inspect.getsource(voc)
    handled = {int(m) for m in re.findall(r"kind == (\d+)", src)}
    handled |= set(getattr(voc, "_FIXED", {}))
    spec = _enum(_spec("media/creative_voice_file.ksy"), "block_types")
    missing = sorted(set(spec) - handled)
    assert not missing, (
        "the spec documents VOC block types the walker has no branch for: "
        + ", ".join("%d (%s)" % (v, spec[v]) for v in missing))


# ── MP4: the atoms acidcat descends into ────────────────────────────


def _mp4_switch_cases(spec):
    """{atom fourcc: the type its body is parsed as} from the atom switch.

    The spec models container-ness properly and the first version of this file
    did not use it: `types/atom/seq` ends in a switch on `atom_type`, and a case
    mapping to `atom_list` IS the statement that the atom holds other atoms.
    Anything else is a typed leaf body.
    """
    atom = (spec.get("types") or {}).get("atom") or {}
    for field in atom.get("seq") or []:
        t = field.get("type")
        if isinstance(t, dict) and t.get("switch-on"):
            out = {}
            for case, body in (t.get("cases") or {}).items():
                name = str(case).rsplit("::", 1)[-1]
                out[name.encode("ascii")] = body
            return out
    pytest.skip("this kaitai revision does not model the atom body as a switch")


def test_every_container_the_spec_models_acidcat_also_descends_into():
    """A case mapping to `atom_list` is the spec saying "this holds atoms". If
    acidcat does not descend into one, every box inside it is invisible: not
    reported wrong, absent, with no warning that anything was skipped.
    """
    from acidcat.core.formats.mp4 import _CONTAINERS

    cases = _mp4_switch_cases(_spec("media/quicktime_mov.ksy"))
    spec_containers = {k for k, v in cases.items() if v == "atom_list"}
    assert spec_containers, "no atom_list case in this revision; nothing compared"
    missing = sorted(spec_containers - _CONTAINERS)
    assert not missing, (
        "the spec parses these as atom lists and acidcat treats them as leaves, "
        "so their contents never appear: "
        + ", ".join(m.decode() for m in missing))


def test_acidcat_descends_into_no_atom_the_spec_gives_a_leaf_body():
    """The other direction. An atom the spec parses with a typed body -- ftyp,
    tkhd, mvhd -- is payload, and descending into payload reads field bytes as
    box headers and invents a tree out of them.

    What this CANNOT catch, stated because the test's previous name implied
    otherwise: an atom the spec does not model at all. `stco` is a leaf (a
    table of chunk offsets) and this revision has no case for it, so planting
    it in _CONTAINERS passes here. That was true of the check this replaces,
    which asserted only that a container id appears somewhere in the atom_type
    enum -- and that enum lists leaves too, so membership proved "is an atom",
    never "is a container".
    """
    from acidcat.core.formats.mp4 import _CONTAINERS

    cases = _mp4_switch_cases(_spec("media/quicktime_mov.ksy"))
    leaves = {k for k, v in cases.items() if v != "atom_list"}
    assert leaves, "no typed-body case in this revision; nothing compared"
    wrong = sorted(leaves & _CONTAINERS)
    assert not wrong, (
        "acidcat descends into atoms the spec parses as payload, so their "
        "field bytes get read as box headers: "
        + ", ".join(w.decode() for w in wrong))


def test_the_mp4_container_set_is_not_silently_empty():
    """The control the two assertions above need. Both are set differences, and
    a set difference against an empty `_CONTAINERS` is empty too -- so gutting
    the table would satisfy the leaf check and, without this, look clean.
    """
    from acidcat.core.formats.mp4 import _CONTAINERS

    assert len(_CONTAINERS) >= 8, (
        "only %d container atoms; acidcat carries thirteen and the ISO-BMFF "
        "boxes that are not in this kaitai revision (ilst, mvex, mfra) are "
        "real" % len(_CONTAINERS))


def test_the_mp4_atoms_acidcat_descends_into_are_spelled_right():
    """Four bytes each, and a typo makes a container silently a leaf: the
    walker stops descending and every box inside it disappears from the tree
    without any warning that it did.
    """
    from acidcat.core.formats.mp4 import _CONTAINERS

    bad = sorted(c for c in _CONTAINERS if len(c) != 4)
    assert not bad, "container atom ids that are not four bytes: %s" % bad


# ── ID3: two magics, a fixed length, and a synchsafe size ───────────


def _seq_of(spec, type_name):
    """The seq of a named type, as {id: field}."""
    body = (spec.get("types") or {}).get(type_name)
    if body is None:
        pytest.skip(f"this kaitai revision has no type {type_name!r}")
    return {f.get("id"): f for f in (body.get("seq") or [])}


def test_id3v1_magic_and_length_agree():
    """The trailer is found by seeking 128 from the end and reading `TAG`.
    Both numbers come from the spec: the magic, and the sum of its field
    widths. Getting the length wrong finds the tag on some files and not
    others, which reads as "this file has no tag" rather than as a bug.
    """
    from acidcat.core.formats.mp3 import find_id3v1

    spec = _spec("media/id3v1_1.ksy")
    types = spec.get("types") or {}
    tag = next(iter(types.values())) if types else None
    if not tag:
        pytest.skip("this kaitai revision shapes id3v1 differently")
    fields = {f.get("id"): f for f in (tag.get("seq") or [])}

    assert fields["magic"]["contents"] == "TAG"

    # 3 for the magic, then every sized field the spec lists
    total = len("TAG") + sum(
        f.get("size", 1) for k, f in fields.items() if k != "magic")
    assert total == 128, (
        "the spec's id3v1 fields sum to %d, not the 128 acidcat seeks back"
        % total)

    # and the function itself agrees, on a real trailer
    import tempfile

    d = tempfile.mkdtemp()
    path = os.path.join(d, "t.mp3")
    with open(path, "wb") as fh:
        fh.write(b"\xff\xfb" + b"\x00" * 400)
        fh.write(b"TAG" + b"\x00" * 125)
    assert find_id3v1(path) == os.path.getsize(path) - 128


def test_id3v2_magic_agrees_and_its_size_is_synchsafe():
    """`ID3`, then two version bytes, then flags, then a SYNCHSAFE size.

    Synchsafe is the whole trap of this header: seven bits per byte, so a tag
    read with a plain big-endian u4 is wrong the moment any length byte reaches
    0x80, and wrong in the direction that walks into the audio. The spec names
    the type, so this asserts acidcat decodes it the same way rather than
    asserting a number nobody checked.
    """
    from acidcat.core.formats.mp3 import synchsafe

    fields = _seq_of(_spec("media/id3v2_4.ksy"), "header")
    assert fields["magic"]["contents"] == "ID3"
    assert "synchsafe" in str(fields["size"].get("type", "")).lower(), (
        "this kaitai revision no longer calls the id3v2 size synchsafe: %r"
        % fields["size"].get("type"))

    # seven bits per byte: 0x00 0x00 0x02 0x01 is (2 << 7) | 1 = 257.
    # A plain big-endian u4 would read 513, and every byte that reaches 0x80
    # widens the gap -- in the direction that walks into the audio.
    assert synchsafe(bytes([0x00, 0x00, 0x02, 0x01])) == 257
    assert synchsafe(bytes([0x00, 0x00, 0x01, 0x00])) == 128, (
        "a synchsafe 0x0100 is 128; reading it as u4be gives 256")
    # the largest legal value: four 7-bit bytes
    assert synchsafe(bytes([0x7F] * 4)) == (1 << 28) - 1


def test_the_spec_library_was_actually_read():
    """A cross-check that silently matched nothing would pass forever. This is
    the same guard the geometry ratchet carries, for the same reason."""
    import glob
    found = glob.glob(os.path.join(_KSY, "**", "*.ksy"), recursive=True)
    assert len(found) > 50, (
        f"{_KSY} holds {len(found)} .ksy files; that is not the format library")


def test_it_says_which_formats_it_actually_compares():
    """Stated rather than implied, the way the geometry ratchet states its
    reach. A clean run here is evidence about the formats named below and
    silence about every other walker.

    The gap is the SPEC LIBRARY, not this file: aiff, flac, sf2, caf, wave64,
    rf64 and mp3 have no .ksy in the upstream collection at all, so no amount
    of work here reaches them. Every audio spec the collection DOES carry is
    now compared.
    """
    compared = {"au", "ogg", "midi", "wav", "voc", "s3m", "xm", "mp4",
                "id3v1", "id3v2"}
    assert len(compared) >= 10, sorted(compared)
    for rel in ("media/au.ksy", "media/wav.ksy", "media/ogg.ksy",
                "media/creative_voice_file.ksy", "media/quicktime_mov.ksy",
                "media/standard_midi_file.ksy"):
        assert os.path.isfile(os.path.join(_KSY, rel)), (
            f"{rel} is missing from this checkout, so the checks that read it "
            f"skipped and this file compared less than it claims")


def test_a_malformed_synchsafe_size_stays_small():
    """The mask is not cosmetic, and this is what it costs to omit it.

    A high bit in a synchsafe byte is malformed by definition -- the encoding
    exists so a length can never hold a 0xFF a decoder would read as a frame
    sync. Unmasked, four such bytes become 268 MB, and the walker then skips a
    quarter-gigabyte forward looking for audio that is sitting at offset 10.

    Found by widening this file: the spec names the type synchsafe, and asking
    acidcat to agree with that name surfaced two readers of the same field
    disagreeing by 268 MB on the same bytes.
    """
    from acidcat.core.formats.mp3 import synchsafe

    assert synchsafe(bytes([0x80, 0x00, 0x00, 0x00])) == 0
    assert synchsafe(bytes([0xFF, 0xFF, 0xFF, 0xFF])) == (1 << 28) - 1

    # and the same reading the forensics layer has always used
    b = bytes([0x80, 0x7F, 0x80, 0x7F])
    masked = (((b[0] & 0x7F) << 21) | ((b[1] & 0x7F) << 14)
              | ((b[2] & 0x7F) << 7) | (b[3] & 0x7F))
    assert synchsafe(b) == masked, (
        "core/formats/mp3.synchsafe and forensics/anomalies.py disagree about "
        "the same four bytes")
