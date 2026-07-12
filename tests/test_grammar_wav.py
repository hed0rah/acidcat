"""Grammar-engine v1: the WAV walking skeleton vs the hand-written walker.

The interpreter must emit walk/wav's exact fmt fields; the corpus test
asserts it file-by-file. Known, deliberate v1 divergences (out of comparison
scope, closed in Phase 1):

- the walker gives `data` a computed summary (duration/frames); the
  interpreter yields the unparsed hex preview,
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


def _fmt_fields(chunks):
    c = next((c for c in chunks if str(c["id"]).strip() == "fmt"), None)
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


def test_wav_ctx_semantic_keys(tmp_path):
    """ctx publishes under the walker's semantic names ("bits"), and only
    for fields that declare a key (avg_bytes_per_sec publishes none)."""
    p = tmp_path / "ctx.wav"
    p.write_bytes(_make_riff_wav(channels=2))
    ctx = {}
    interpret(WAVE, str(p), ctx=ctx)
    assert ctx == {"format_tag": 1, "channels": 2, "sample_rate": 44100,
                   "block_align": 4, "bits": 16}


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
    """The milestone: interpreter fmt fields byte-exact vs the walker on
    every corpus WAV with a full fmt. No PCM filter is needed: an EXTENSIBLE
    fmt parses the same first 6 fields and the comparison is [:6]."""
    from acidcat.core.walk.base import Unsupported
    try:
        wlabel, wchunks, _ = walk_file(path)
    except Unsupported:
        pytest.skip("walker does not decode this file")
    if wlabel != "RIFF/WAVE":
        pytest.skip(f"sniffed as {wlabel}, not plain RIFF/WAVE")
    wf = _fmt_fields(wchunks)
    if wf is None or len(wf) < 6:
        # no fmt at all, or the walker's all-or-nothing truncated-fmt path
        pytest.skip("no full fmt")
    _, gchunks, _ = interpret(WAVE, path)
    gf = _fmt_fields(gchunks)
    assert gf is not None, "interpreter found no fmt chunk"
    keys = ("off", "len", "name", "value", "note", "enc", "raw")
    assert [{k: f.get(k) for k in keys} for f in wf[:6]] == \
           [{k: f.get(k) for k in keys} for f in gf[:6]]


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


def test_truncated_fmt_partial(tmp_path):
    """Deliberate v1 walker divergence: the walker's truncated-fmt path is
    all-or-nothing (0 fields + "truncated"); the interpreter emits the
    fields that fit. The corpus test skips <6-field fmts; Region.min_len
    closes the gap in Phase 1."""
    fmt_payload = struct.pack("<HHI", 1, 2, 44100)  # 8 of the 16 bytes
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt_payload)) + fmt_payload
    p = tmp_path / "short_fmt.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    _, chunks, _ = interpret(WAVE, str(p))
    names = [f["name"] for f in chunks[0]["fields"]]
    assert names == ["format_tag", "channels", "sample_rate"]


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
