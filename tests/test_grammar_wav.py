"""Grammar-engine v1: the WAV walking skeleton vs the hand-written walker.

The interpreter must emit walk/wav's exact fmt fields; the corpus test
asserts it file-by-file. Known, deliberate v1 divergences (out of comparison
scope, closed in Phase 1):

- the walker gives `data` a computed summary (duration/frames); the
  interpreter yields the unparsed hex preview,
- the walker gives a struct chunk a summary (fmt -> "PCM 16-bit 2ch 44100 Hz");
  the interpreter leaves summary "" until summary helpers land in Phase 1,
- the walker's truncated-fmt path is all-or-nothing (<16 bytes -> 0 fields
  plus a "truncated" summary); the interpreter emits the fields that fit
  (Region.min_len closes this).
"""

import glob
import os
import struct
import subprocess
import sys

import pytest

from conftest import SAMPLE_WAV, _make_riff_wav
from acidcat.core import fieldcodec
from acidcat.core.grammar import interpret
from acidcat.core.grammar.formats.wav import WAVE
from acidcat.core.walk import walk_file


def _fmt_chunk(chunks):
    return next((c for c in chunks if str(c["id"]).strip() == "fmt"), None)


def _fmt_fields(chunks):
    c = _fmt_chunk(chunks)
    return c["fields"] if c else None


def test_wav_fmt_hermetic_exact(tmp_path):
    """The exact field list from the build spec, including key ABSENCE:
    plain ints carry no enc/raw, the chunk carries no payload_base."""
    p = tmp_path / "two_channel.wav"
    p.write_bytes(_make_riff_wav(channels=2))
    label, chunks, warns = interpret(WAVE, str(p))
    assert label == "RIFF/WAVE"
    assert warns == []
    fmt = chunks[0]
    assert (fmt["id"], fmt["offset"], fmt["size"]) == ("fmt ", 12, 16)
    assert "payload_base" not in fmt
    assert fmt["fields"] == [
        {"off": 0x00, "len": 2, "name": "format_tag", "value": "0x0001",
         "note": "PCM", "enc": "<H", "raw": 1},
        {"off": 0x02, "len": 2, "name": "channels", "value": 2, "note": ""},
        {"off": 0x04, "len": 4, "name": "sample_rate", "value": 44100,
         "note": "Hz"},
        {"off": 0x08, "len": 4, "name": "avg_bytes_per_sec", "value": 176400,
         "note": ""},
        {"off": 0x0C, "len": 2, "name": "block_align", "value": 4, "note": ""},
        {"off": 0x0E, "len": 2, "name": "bits_per_sample", "value": 16,
         "note": ""},
    ]


def test_wav_ctx_matches_walker(tmp_path):
    """Comparative parity: every ctx key the descriptor publishes carries the
    same value the walker publishes for that key on the same file. Enforced
    against the walker, not a hard-coded literal, so the two cannot drift."""
    from acidcat.core.walk.wav import inspect_wav
    p = tmp_path / "ctx.wav"
    p.write_bytes(_make_riff_wav(channels=2))
    wctx = {}
    inspect_wav(str(p), ctx=wctx)
    gctx = {}
    interpret(WAVE, str(p), ctx=gctx)
    published = {f.ctx for r in WAVE.regions.values()
                 for f in getattr(r, "fields", ()) if getattr(f, "ctx", None)}
    assert published, "descriptor publishes no ctx keys"
    for k in published:
        assert gctx.get(k) == wctx.get(k), k


# the local corpus sweep (~/sample_packs on the dev box); absent on CI, where
# the hermetic tests above carry coverage
_CORPUS = os.environ.get(
    "ACIDCAT_CORPUS", os.path.join(os.path.expanduser("~"), "sample_packs"))


def _corpus_wavs():
    if not os.path.isdir(_CORPUS):
        return [pytest.param(None,
                             marks=pytest.mark.skip(reason="corpus not present"))]
    paths = sorted(glob.glob(os.path.join(_CORPUS, "**", "*.wav"),
                             recursive=True))
    limit = os.environ.get("ACIDCAT_CORPUS_LIMIT")
    return paths[:int(limit)] if limit else paths


@pytest.mark.parametrize("path", _corpus_wavs())
def test_wav_fmt_corpus_equivalence(path):
    """Full deep-equal of the fmt chunk vs the walker on every corpus WAV: all
    fields byte-exact, the summary, and per-chunk warnings as SETS (the walker's
    mid-region lint order differs from the interpreter's field-loop order), plus
    file warnings as sets. The fmt-corpus-proven milestone."""
    from acidcat.core.walk.base import Unsupported
    try:
        wlabel, wchunks, wfile = walk_file(path)
    except Unsupported:
        pytest.skip("walker does not decode this file")
    if wlabel != "RIFF/WAVE":
        pytest.skip(f"sniffed as {wlabel}, not plain RIFF/WAVE")
    wc = _fmt_chunk(wchunks)
    if wc is None:
        pytest.skip("no fmt chunk")
    _, gchunks, gfile = interpret(WAVE, path)
    gc = _fmt_chunk(gchunks)
    assert gc is not None, "interpreter found no fmt chunk"
    keys = ("off", "len", "name", "value", "note", "enc", "raw")
    assert [{k: f.get(k) for k in keys} for f in gc["fields"]] == \
           [{k: f.get(k) for k in keys} for f in wc["fields"]]
    assert gc["summary"] == wc["summary"]
    assert set(gc["warnings"]) == set(wc["warnings"])
    assert set(gfile) == set(wfile)


@pytest.mark.parametrize("path", _corpus_wavs())
def test_ctx_keys_covers_walker(path):
    """CTX_KEYS must stay a superset of every semantic ctx key the walker
    publishes, so the descriptor vocabulary cannot silently fall behind the
    walker (a published-but-unsanctioned key would reject a valid future
    descriptor field at construction). Self-maintaining across the corpus,
    which exercises smpl/acid/cue/fact chunks a hermetic file does not."""
    from acidcat.core.walk.wav import inspect_wav
    from acidcat.core.vocab import CTX_KEYS
    ctx = {}
    inspect_wav(path, ctx=ctx)  # non-WAV degrades to an empty ctx (passes)
    missing = set(ctx) - set(CTX_KEYS)
    assert not missing, f"walker publishes ctx keys not in CTX_KEYS: {missing}"


def _skeleton(chunks):
    """(id, offset, size, normalized payload_base) per chunk -- the traversal
    skeleton, independent of any field-level parsing. payload_base is
    normalized to the offset+8 default the walkers omit (design section 7)."""
    return [(str(c["id"]), c["offset"], c["size"],
             c.get("payload_base", c["offset"] + 8)) for c in chunks]


def test_wav_chunk_skeleton_hermetic(tmp_path):
    """The interpreter enumerates the walker's exact chunk skeleton (fmt +
    data here), covering the whole file, not just fmt's fields."""
    p = tmp_path / "skeleton.wav"
    p.write_bytes(_make_riff_wav(channels=2))
    _, wchunks, _ = walk_file(str(p))
    _, gchunks, _ = interpret(WAVE, str(p))
    assert _skeleton(gchunks) == _skeleton(wchunks)


@pytest.mark.parametrize("path", _corpus_wavs())
def test_wav_chunk_skeleton_parity(path):
    """Every corpus WAV: the interpreter enumerates exactly the walker's
    chunks -- same ids, offsets, sizes, payload bases -- which locks in the
    single shared traversal (core/riff.iter_spans over iter_chunks) at corpus
    scale, including the EXTENSIBLE/ADPCM files the fmt-field test skips. Also
    asserts the interpreter invents no traversal warning the walker lacks (the
    interpreter emits the traversal subset; format-rule warnings -- no fmt, fmt
    after data -- are walker-only until Phase 1)."""
    from acidcat.core.walk.base import Unsupported
    try:
        wlabel, wchunks, wwarns = walk_file(path)
    except Unsupported:
        pytest.skip("walker does not decode this file")
    if wlabel != "RIFF/WAVE":
        pytest.skip(f"sniffed as {wlabel}, not plain RIFF/WAVE")
    _, gchunks, gwarns = interpret(WAVE, path)
    assert _skeleton(gchunks) == _skeleton(wchunks)
    assert set(gwarns) <= set(wwarns)


def _assert_encs_verify(path):
    """Every enc-annotated field re-encodes raw to the exact on-disk bytes
    (the descriptor variant of test_all_walker_enc_annotations_verify)."""
    with open(path, "rb") as f:
        data = f.read()
    _, chunks, _ = interpret(WAVE, path)
    checked = 0
    for c in chunks:
        for fl in c["fields"]:
            if "enc" not in fl or fl.get("off") is None:
                continue
            abs_off = fieldcodec._field_abs(c, fl)
            raw = fl.get("raw", fl.get("value"))
            assert fieldcodec.encode_value(fl["enc"], str(raw)) == \
                data[abs_off:abs_off + fl["len"]], (path, fl["name"])
            checked += 1
    return checked


def test_grammar_enc_verifies_on_disk(tmp_path):
    p = tmp_path / "verify.wav"
    p.write_bytes(_make_riff_wav(channels=2))
    assert _assert_encs_verify(str(p)) > 0
    if os.path.isfile(SAMPLE_WAV):
        assert _assert_encs_verify(SAMPLE_WAV) > 0


def test_truncated_riff_degrades(truncated_riff):
    label, chunks, warns = interpret(WAVE, truncated_riff)
    assert chunks == []
    assert warns  # the riff_size lie is warned, never raised


def _wav_with_fmt(fmt_payload):
    """A minimal RIFF/WAVE carrying the given fmt payload + a tiny data chunk,
    for building the per-variant fmt fixtures the corpus does not guarantee."""
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt_payload)) + fmt_payload
    if len(fmt_payload) % 2:
        fmt_chunk += b"\x00"                      # word-align
    data_chunk = b"data" + struct.pack("<I", 4) + b"\x00\x00\x00\x00"
    body = b"WAVE" + fmt_chunk + data_chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _assert_fmt_matches_walker(tmp_path, name, fmt_payload):
    """Interpreter fmt fields byte-exact vs the walker for a hand-built fmt."""
    p = tmp_path / f"{name}.wav"
    p.write_bytes(_wav_with_fmt(fmt_payload))
    _, gchunks, _ = interpret(WAVE, str(p))
    _, wchunks, _ = walk_file(str(p))
    keys = ("off", "len", "name", "value", "note", "enc", "raw")
    gf = [{k: f.get(k) for k in keys} for f in _fmt_fields(gchunks)]
    wf = [{k: f.get(k) for k in keys} for f in _fmt_fields(wchunks)]
    assert gf == wf
    return _fmt_fields(gchunks)


def test_ima_variant_matches_walker(tmp_path):
    """Switch dispatch, simplest case: an IMA/DVI ADPCM fmt (tag 0x0011) emits
    cb_size + samples_per_block within the cb window, exactly like the walker."""
    ext = struct.pack("<H", 512)                  # samples_per_block
    fmt_payload = (struct.pack("<HHIIHH", 0x0011, 2, 44100, 44100, 4, 4)
                   + struct.pack("<H", len(ext)) + ext)
    fields = _assert_fmt_matches_walker(tmp_path, "ima", fmt_payload)
    assert [f["name"] for f in fields][-2:] == ["cb_size", "samples_per_block"]


def test_mpeglayer3_variant_matches_walker(tmp_path):
    """MPEGLAYER3WAVEFORMAT (tag 0x0055): note-sources (mp3_id + masked mp3_flags
    tables) and unpadded-hex display, byte-exact vs the walker."""
    ext = (struct.pack("<H", 1)            # mp3_id = 1 -> "MPEGLAYER3_ID_MPEG"
           + struct.pack("<I", 0x0002)     # mp3_flags & 0x3 == 2 -> "padding never"
           + struct.pack("<H", 417)        # block_size
           + struct.pack("<H", 1)          # frames_per_block
           + struct.pack("<H", 1105))      # codec_delay
    fmt_payload = (struct.pack("<HHIIHH", 0x0055, 2, 44100, 16000, 1, 0)
                   + struct.pack("<H", len(ext)) + ext)
    fields = _assert_fmt_matches_walker(tmp_path, "mp3", fmt_payload)
    assert [f["name"] for f in fields][-5:] == [
        "mp3_id", "mp3_flags", "block_size", "frames_per_block", "codec_delay"]


def test_adpcm_variant_matches_walker(tmp_path):
    """MS ADPCM (tag 0x0002): decode helper #1 -- composite coefficient field,
    standard-predictor-set detection, byte-exact vs the walker."""
    std = [(256, 0), (512, -256), (0, 0), (192, 64), (240, 0),
           (460, -208), (392, -232)]
    coefs = b"".join(struct.pack("<hh", a, b) for a, b in std)
    ext = struct.pack("<HH", 512, len(std)) + coefs          # spb, ncoef=7, coefs
    fmt_payload = (struct.pack("<HHIIHH", 0x0002, 2, 44100, 44100, 4, 4)
                   + struct.pack("<H", len(ext)) + ext)
    fields = _assert_fmt_matches_walker(tmp_path, "adpcm", fmt_payload)
    coef = next(f for f in fields if f["name"] == "adpcm_coefficients")
    assert coef["note"] == "the standard predictor set"


def test_extensible_variant_matches_walker(tmp_path):
    """WAVEFORMATEXTENSIBLE (tag 0xFFFE): decode helper #2 -- GUID sub_format,
    channel_mask NoteFlags, and the later-field-wins ctx override, byte-exact."""
    from acidcat.core.vocab import KSDATAFORMAT_TAIL
    sub = struct.pack("<H", 1) + KSDATAFORMAT_TAIL           # PCM subtype, std tail
    ext = struct.pack("<HI", 16, 0x3) + sub                 # valid_bits, mask=FL|FR
    fmt_payload = (struct.pack("<HHIIHH", 0xFFFE, 2, 44100, 176400, 4, 16)
                   + struct.pack("<H", 22) + ext)
    p = tmp_path / "ext.wav"
    p.write_bytes(_wav_with_fmt(fmt_payload))
    fields = _assert_fmt_matches_walker(tmp_path, "ext", fmt_payload)
    assert [f["name"] for f in fields][-4:] == [
        "cb_size", "valid_bits_per_sample", "channel_mask", "sub_format"]
    assert next(f for f in fields if f["name"] == "sub_format")["note"] \
        == "KSDATAFORMAT_SUBTYPE"
    ctx = {}
    interpret(WAVE, str(p), ctx=ctx)          # override: GUID's tag, not 0xFFFE
    assert ctx["format_tag"] == 1


def test_cb_window_zero_emits_no_variant(tmp_path):
    """cb_size=0 with trailing payload bytes parses NO variant -- the window
    bounds the case, not the remaining payload (byte-exact with the walker)."""
    fmt_payload = (struct.pack("<HHIIHH", 0x0011, 2, 44100, 44100, 4, 4)
                   + struct.pack("<H", 0) + b"\xAA\xBB\xCC\xDD")
    fields = _assert_fmt_matches_walker(tmp_path, "cb0", fmt_payload)
    assert "samples_per_block" not in [f["name"] for f in fields]


def test_short_extensible_emits_no_variant(tmp_path):
    """A 36-byte EXTENSIBLE (len < 40) emits 0 ext fields all-or-nothing, never
    a partial group, exactly like the walker."""
    fmt_payload = (struct.pack("<HHIIHH", 0xFFFE, 2, 44100, 176400, 4, 16)
                   + struct.pack("<H", 18) + b"\x00" * 18)
    fields = _assert_fmt_matches_walker(tmp_path, "shortext", fmt_payload)
    assert [f["name"] for f in fields] == [
        "format_tag", "channels", "sample_rate", "avg_bytes_per_sec",
        "block_align", "bits_per_sample"]


def test_truncated_fmt_matches_walker(tmp_path):
    """fmt < 16 bytes: Region.min_len now degrades all-or-nothing exactly like
    the walker -- 0 fields, "truncated" summary, the walker's exact warning
    (closes the v1 partial-fields divergence)."""
    fmt_payload = struct.pack("<HHI", 1, 2, 44100)  # 8 of the 16 bytes
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt_payload)) + fmt_payload
    p = tmp_path / "short_fmt.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    _, gchunks, _ = interpret(WAVE, str(p))
    gfmt = next(c for c in gchunks if c["id"] == "fmt ")
    assert gfmt["fields"] == []
    assert gfmt["summary"] == "truncated"
    assert gfmt["warnings"] == ["fmt payload is 8 bytes, spec minimum is 16"]
    _, wchunks, _ = walk_file(str(p))
    wfmt = next(c for c in wchunks if c["id"] == "fmt ")
    assert (wfmt["fields"], wfmt["summary"], wfmt["warnings"]) == \
           (gfmt["fields"], gfmt["summary"], gfmt["warnings"])


def test_sub_12_byte_file_warns(tmp_path):
    p = tmp_path / "stub.wav"
    p.write_bytes(b"RIFF\x00")
    _, chunks, warns = interpret(WAVE, str(p))
    assert chunks == []
    assert warns == ["file is 5 bytes; a RIFF header needs 12"]


def test_chunk_overrun_yielded_with_warning(tmp_path):
    """A chunk claiming more bytes than remain is still yielded, with its
    DECLARED size plus the walker's warning -- the lenient-vs-strict
    discriminator (core/structure would hide it as tail)."""
    fmt_payload = struct.pack("<HHIIHH", 1, 1, 8000, 8000, 1, 8)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt_payload
            + b"data" + struct.pack("<I", 1000) + b"\x00" * 4)
    p = tmp_path / "overrun.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    _, chunks, warns = interpret(WAVE, str(p))
    data_chunk = next(c for c in chunks if c["id"] == "data")
    assert data_chunk["size"] == 1000
    assert any("claims 1,000 bytes but only 4 remain" in w for w in warns)


def test_grammar_not_imported_by_default():
    """The opt-in contract: `import acidcat` must not pull core/grammar."""
    code = ("import acidcat, sys; "
            "bad = [m for m in sys.modules "
            "if m.startswith('acidcat.core.grammar')]; "
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], check=True)
