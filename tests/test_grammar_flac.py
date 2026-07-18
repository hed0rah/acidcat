"""Grammar engine, FLAC foundation slice: the descriptor's STREAMINFO must
match walk/flac._flac_streaminfo byte-for-byte. STREAMINFO is the slice that
introduces the second container strategy (flac_blocks), the Codec type (u24be
frame sizes), and the BitGroup construct (rate/channels/bits/total packed into
one 8-byte word with overlapping display spans).

Only STREAMINFO is described in this slice; the other block types stay
walker-side until the repeat-over-records construct lands.
"""

import glob
import struct

import pytest

from acidcat.core.grammar import interpret
from acidcat.core.grammar.formats.flac import FLAC
from acidcat.core.walk.flac import inspect_flac

_KEYS = ("off", "len", "name", "value", "note", "enc", "raw")


def _norm(fields):
    return [{k: f.get(k) for k in _KEYS} for f in fields]


def _si(chunks):
    return next((c for c in chunks if c["id"] == "STREAMINFO"), None)


def _streaminfo(min_block=4096, max_block=4096, min_frame=10, max_frame=20,
                rate=44100, channels=2, bits=16, total=44100, md5=b"\x00" * 16):
    packed = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | total
    return (struct.pack(">HH", min_block, max_block)
            + min_frame.to_bytes(3, "big") + max_frame.to_bytes(3, "big")
            + struct.pack(">Q", packed) + md5)


def _flac(payload, is_last=True, block_type=0):
    header = bytes([(0x80 if is_last else 0) | block_type]) \
        + len(payload).to_bytes(3, "big")
    return b"fLaC" + header + payload


def _assert_streaminfo_matches(tmp_path, name, payload):
    p = tmp_path / f"{name}.flac"
    p.write_bytes(_flac(payload))
    _, gchunks, _ = interpret(FLAC, str(p))
    wchunks, _ = inspect_flac(str(p))
    g, w = _si(gchunks), _si(wchunks)
    assert g is not None and w is not None
    assert _norm(g["fields"]) == _norm(w["fields"])
    assert g["summary"] == w["summary"]
    assert g["warnings"] == w["warnings"]
    assert g.get("payload_base") == w.get("payload_base")
    return g


def test_streaminfo_basic_matches_walker(tmp_path):
    g = _assert_streaminfo_matches(tmp_path, "basic", _streaminfo())
    names = [f["name"] for f in g["fields"]]
    assert names == ["min_block_size", "max_block_size", "min_frame_size",
                     "max_frame_size", "sample_rate", "channels",
                     "bits_per_sample", "total_samples", "md5_signature"]


def test_streaminfo_bitgroup_enc_is_the_walkers(tmp_path):
    """The overlapping bit-fields carry the walker's exact bits: enc so they stay
    editable, and their display offsets overlap (channels+bits+total share
    bytes)."""
    g = _assert_streaminfo_matches(tmp_path, "bits",
                                   _streaminfo(rate=96000, channels=6, bits=24,
                                               total=1_000_000))
    by = {f["name"]: f for f in g["fields"]}
    assert by["sample_rate"]["enc"] == "bits:0:8:0:20:0"
    assert by["channels"]["enc"] == "bits:-2:8:20:3:-1"
    assert by["bits_per_sample"]["enc"] == "bits:-3:8:23:5:-1"
    assert by["total_samples"]["enc"] == "bits:-3:8:28:36:0"
    # overlapping display spans, exactly as the walker lays them out
    assert (by["bits_per_sample"]["off"], by["total_samples"]["off"]) == (13, 13)


def test_streaminfo_md5_unset_sentinel(tmp_path):
    g = _assert_streaminfo_matches(tmp_path, "md5unset", _streaminfo())
    md5 = next(f for f in g["fields"] if f["name"] == "md5_signature")
    assert md5["value"] == "0 (unset)"


def test_streaminfo_md5_hex(tmp_path):
    g = _assert_streaminfo_matches(tmp_path, "md5hex",
                                   _streaminfo(md5=bytes(range(16))))
    md5 = next(f for f in g["fields"] if f["name"] == "md5_signature")
    assert md5["value"] == bytes(range(16)).hex()
    assert md5.get("enc") is None      # a hash is not a numerically-editable field


def test_streaminfo_total_samples_note_reads_sample_rate(tmp_path):
    g = _assert_streaminfo_matches(tmp_path, "durnote",
                                   _streaminfo(rate=48000, total=96000))
    total = next(f for f in g["fields"] if f["name"] == "total_samples")
    assert total["note"] == "2.000 s at 48000 Hz"


def test_streaminfo_rate_zero_note_and_warning(tmp_path):
    g = _assert_streaminfo_matches(tmp_path, "rate0", _streaminfo(rate=0))
    total = next(f for f in g["fields"] if f["name"] == "total_samples")
    assert total["note"] == ""                  # no rate -> empty duration note
    assert "sample rate is 0" in g["warnings"]


def test_streaminfo_block_size_inversion_warning(tmp_path):
    g = _assert_streaminfo_matches(tmp_path, "blockinv",
                                   _streaminfo(min_block=8192, max_block=4096))
    assert any("min_block_size 8192 > max_block_size 4096" in w
               for w in g["warnings"])


def test_streaminfo_truncated_all_or_nothing(tmp_path):
    """A STREAMINFO under 34 bytes degrades to the walker's exact truncated
    path -- 0 fields, the 'spec says 34' warning -- via Region.min_len."""
    p = tmp_path / "trunc.flac"
    p.write_bytes(_flac(_streaminfo()[:20]))
    _, gchunks, _ = interpret(FLAC, str(p))
    wchunks, _ = inspect_flac(str(p))
    g, w = _si(gchunks), _si(wchunks)
    assert g["fields"] == [] == w["fields"]
    assert g["summary"] == "truncated" == w["summary"]
    assert g["warnings"] == w["warnings"]
    assert g["warnings"] == ["STREAMINFO is 20 bytes, spec says 34"]


@pytest.mark.parametrize("path",
                         sorted(glob.glob("data/test_formats/**/*.flac",
                                          recursive=True)))
def test_streaminfo_corpus_parity(path):
    """Byte-exact STREAMINFO across the real FLAC fixtures (24-bit, 96 kHz,
    truncated)."""
    _, gchunks, _ = interpret(FLAC, path)
    wchunks, _ = inspect_flac(path)
    g, w = _si(gchunks), _si(wchunks)
    if w is None:
        pytest.skip("no STREAMINFO in this fixture")
    assert _norm(g["fields"]) == _norm(w["fields"])
    assert g["summary"] == w["summary"]
    assert g["warnings"] == w["warnings"]


# ── descriptor-driven fuzz over STREAMINFO's decision points ─────────
# Same idea as test_descriptor_fuzz.py but for FLAC: derive the walker's
# STREAMINFO branch points from the descriptor and check field + summary parity.
# Warnings are compared EXCEPT the per-block "declared length overruns the file"
# warning, which inspect_flac attaches from the strategy level (file_size vs the
# block's declared end) and the STREAMINFO descriptor does not yet reproduce --
# deferred to the warnings slice alongside the repeat construct, exactly as WAV
# deferred file/chunk warnings to its PR-C. The descriptor fuzz confirmed this is
# the ONLY divergence across 1500 random mutations; fields and summary never
# diverged.

def _bitfield_edges():
    """Boundary values for each STREAMINFO bit-field, read from the descriptor."""
    from acidcat.core.grammar.model import BitGroup
    reg = FLAC.regions["STREAMINFO"]
    group = next(e for e in reg.fields if isinstance(e, BitGroup))
    out = {}
    for bf in group.fields:
        hi = (1 << bf.width) - 1 - bf.bias        # max stored value, un-biased
        lo = max(0, -bf.bias)                     # min value the display allows
        out[bf.name] = sorted({lo, lo + 1, hi})
    return out


def _flac_fuzz_cases():
    reg = FLAC.regions["STREAMINFO"]
    # min_len edges
    for n in (0, reg.min_len - 1, reg.min_len):
        yield (f"min_len={n}", _flac(_streaminfo()[:n]))
    # each bit-field at its boundaries (rate=0 also trips the relation lint)
    argmap = {"sample_rate": "rate", "bits_per_sample": "bits",
              "total_samples": "total"}
    for name, vals in _bitfield_edges().items():
        arg = argmap.get(name, name)
        for v in vals:
            try:
                yield (f"{name}={v}", _flac(_streaminfo(**{arg: v})))
            except (struct.error, ValueError):
                pass
    # u24be codec edges on the frame sizes
    for mf in (0, (1 << 24) - 1):
        yield (f"min_frame={mf}", _flac(_streaminfo(min_frame=mf)))
    # the block-inversion relation lint
    yield ("blockinv", _flac(_streaminfo(min_block=9000, max_block=4000)))
    # a full truncation sweep
    full = _streaminfo()
    for n in range(35):
        yield (f"trunc{n}", _flac(full[:n]))


_OVERRUN = "overrun"


def test_flac_descriptor_fuzz_field_summary_parity(tmp_path):
    """Every descriptor-derived STREAMINFO boundary: walker and interpreter agree
    on fields and summary, and on warnings modulo the deferred overrun warning."""
    p = tmp_path / "f.flac"
    findings = []
    for label, data in _flac_fuzz_cases():
        p.write_bytes(data)
        _, gchunks, _ = interpret(FLAC, str(p))
        wchunks, _ = inspect_flac(str(p))
        g, w = _si(gchunks), _si(wchunks)
        if g is None or w is None:
            continue
        if _norm(g["fields"]) != _norm(w["fields"]):
            findings.append(f"[{label}] fields")
        if g["summary"] != w["summary"]:
            findings.append(f"[{label}] summary")
        gw = {x for x in g["warnings"] if _OVERRUN not in x}
        ww = {x for x in w["warnings"] if _OVERRUN not in x}
        if gw != ww:
            findings.append(f"[{label}] warns {gw ^ ww}")
    assert not findings, "FLAC descriptor-fuzz findings:\n" + "\n".join(findings)


def test_flac_descriptor_fuzz_reaches_decision_points(tmp_path):
    """The generator actually trips STREAMINFO's meaningful branches."""
    p = tmp_path / "f.flac"
    seen = set()
    for _label, data in _flac_fuzz_cases():
        p.write_bytes(data)
        _, gchunks, _ = interpret(FLAC, str(p))
        g = _si(gchunks)
        if g is None:
            continue
        if g["summary"] == "truncated":
            seen.add("truncated")
        for wtext in g["warnings"]:
            if "sample rate is 0" in wtext:
                seen.add("rate0")
            if "min_block_size" in wtext:
                seen.add("blockinv")
    assert {"truncated", "rate0", "blockinv"} <= seen, f"only reached {seen}"
