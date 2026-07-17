"""SigMF recording and bare IQ capture walkers: the datatype grammar, the
data-anchored capture/annotation byte regions, and the three bare-IQ metadata
sources (extension, PortaPack .TXT, GQRX filename).

Fixtures are synthesized here (a JSON sidecar plus a few raw samples) -- the
format is open and headerless, so no real capture is needed to exercise it."""
import hashlib
import json
import struct

from acidcat.core.sniff import sniff
from acidcat.core.walk import sigmf


def test_parse_datatype_grammar():
    g = sigmf._parse_datatype("ci16_le")
    assert g == {"cplx": True, "kind": "int", "bits": 16, "endian": "le",
                 "sample_bytes": 4}
    assert sigmf._parse_datatype("cu8")["sample_bytes"] == 2       # 8-bit needs no suffix
    assert sigmf._parse_datatype("rf32_le")["cplx"] is False
    assert sigmf._parse_datatype("ci16") is None                  # multibyte needs endian
    assert sigmf._parse_datatype("x32_qe") is None


def _make_pair(tmp_path, extra_global=None, captures=None, annotations=None):
    data = b"".join(struct.pack("<hh", i - 4, i + 2) for i in range(8))  # 8 ci16 pairs
    (tmp_path / "cap.sigmf-data").write_bytes(data)
    g = {"core:datatype": "ci16_le", "core:sample_rate": 2_000_000,
         "core:version": "1.0.0", "core:sha512": hashlib.sha512(data).hexdigest()}
    g.update(extra_global or {})
    meta = {"global": g,
            "captures": captures if captures is not None else
            [{"core:sample_start": 0, "core:frequency": 8_428_190_000}],
            "annotations": annotations or []}
    p = tmp_path / "cap.sigmf-meta"
    p.write_text(json.dumps(meta))
    return str(p), data


def test_sigmf_sniffs_both_pair_members(tmp_path):
    meta, _ = _make_pair(tmp_path)
    assert sniff(meta) == "sigmf"
    assert sniff(str(tmp_path / "cap.sigmf-data")) == "sigmf"


def test_sigmf_global_and_derived_geometry(tmp_path):
    meta, _ = _make_pair(tmp_path)
    chunks, warns = sigmf.inspect_sigmf(meta)
    assert warns == []
    g = next(c for c in chunks if c["id"] == "global")
    f = {x["name"]: x for x in g["fields"]}
    assert f["datatype"]["note"] == "complex int16 little-endian, 4 B/sample"
    assert f["sample_count"]["value"] == "8"                       # 32 bytes / 4
    assert f["sha512"]["note"] == "not verified (use --deep)"


def test_sigmf_captures_are_byte_regions(tmp_path):
    meta, _ = _make_pair(tmp_path, captures=[
        {"core:sample_start": 0, "core:frequency": 8_428_190_000},
        {"core:sample_start": 4, "core:frequency": 8_500_000_000}])
    chunks, _ = sigmf.inspect_sigmf(meta)
    caps = [c for c in chunks if c["id"].startswith("capture")]
    assert caps[0]["offset"] == 0 and caps[0]["size"] == 16       # samples 0..4, 4 B each
    assert caps[1]["offset"] == 16 and caps[1]["size"] == 16
    start = next(x for x in caps[1]["fields"] if x["name"] == "sample_start")
    assert start["xref"] == 16                                    # followable pointer


def test_sigmf_annotation_region(tmp_path):
    meta, _ = _make_pair(tmp_path, annotations=[
        {"core:sample_start": 2, "core:sample_count": 3, "core:label": "burst"}])
    chunks, _ = sigmf.inspect_sigmf(meta)
    ann = next(c for c in chunks if c["id"].startswith("annotation"))
    assert ann["offset"] == 8 and ann["size"] == 12               # 3 samples * 4 B
    assert "burst" in ann["summary"]


def test_sigmf_deep_verifies_sha_and_stats(tmp_path):
    meta, _ = _make_pair(tmp_path)
    chunks, warns = sigmf.inspect_sigmf(meta, deep=True)
    g = next(c for c in chunks if c["id"] == "global")
    assert next(x for x in g["fields"] if x["name"] == "sha512")["note"] == "verified"
    samp = next(c for c in chunks if c["id"] == "samples")
    names = {x["name"] for x in samp["fields"]}
    assert {"dc_offset_i", "dc_offset_q", "clipping"} <= names


def test_sigmf_bad_sha_warns(tmp_path):
    meta, _ = _make_pair(tmp_path, extra_global={"core:sha512": "00" * 64})
    _, warns = sigmf.inspect_sigmf(meta, deep=True)
    assert any("sha512 does not match" in w for w in warns)


def test_sigmf_missing_datatype_keeps_walking(tmp_path):
    # a data file with no sidecar still yields a samples chunk plus a warning
    (tmp_path / "x.sigmf-data").write_bytes(b"\x00" * 40)
    chunks, warns = sigmf.inspect_sigmf(str(tmp_path / "x.sigmf-data"))
    assert any("no .sigmf-meta sidecar" in w for w in warns)
    assert chunks[-1]["id"] == "samples" and chunks[-1]["size"] == 40


def test_iq_cu8_by_extension(tmp_path):
    p = tmp_path / "grab.cu8"
    p.write_bytes(bytes([127, 129] * 6))
    assert sniff(str(p)) == "iq"
    chunks, warns = sigmf.inspect_iq(str(p))
    samp = next(c for c in chunks if c["id"] == "samples")
    assert "cu8" in samp["summary"]
    assert any("silence is 0x80" in x["note"] for x in samp["fields"])
    assert any("sample rate unknown" in w for w in warns)


def test_iq_gqrx_filename(tmp_path):
    p = tmp_path / "gqrx_20250101_120000_433000000_2000000_fc.raw"
    p.write_bytes(struct.pack("<ff", 0.1, -0.2) * 4)
    assert sniff(str(p)) == "iq"
    chunks, _ = sigmf.inspect_iq(str(p))
    meta = next(c for c in chunks if c["id"] == "metadata")
    f = {x["name"]: x["value"] for x in meta["fields"]}
    assert f["center_frequency"] == 433000000 and f["sample_rate"] == 2000000
    assert f["datetime"] == "2025-01-01 12:00:00"
    assert "from GQRX filename" in meta["summary"]


def test_iq_portapack_txt_sidecar(tmp_path):
    (tmp_path / "rec.C16").write_bytes(struct.pack("<hh", 1, 2) * 4)
    (tmp_path / "rec.TXT").write_text("sample_rate=500000\ncenter_frequency=433920000\n")
    chunks, _ = sigmf.inspect_iq(str(tmp_path / "rec.C16"))
    meta = next(c for c in chunks if c["id"] == "metadata")
    assert "PortaPack .TXT sidecar" in meta["summary"]
    # no duplicate sample_rate field
    assert [x["name"] for x in meta["fields"]].count("sample_rate") == 1


def test_iq_bare_raw_not_gqrx_is_unsupported(tmp_path):
    # an arbitrary .raw without the GQRX convention must not sniff as iq
    p = tmp_path / "random.raw"
    p.write_bytes(b"\x00" * 64)
    assert sniff(str(p)) != "iq"


def test_malformed_json_types_degrade(tmp_path):
    """The .sigmf-meta sidecar is untrusted JSON. Wrong types (non-object top
    level, non-dict global/captures/annotations, string where a number is
    expected) must degrade, never raise -- the type-confusion class the audit
    found."""
    (tmp_path / "x.sigmf-data").write_bytes(b"\x00" * 64)
    meta = tmp_path / "x.sigmf-meta"
    for js in ("[]", "42", '"s"',
               '{"global":[1,2]}',
               '{"global":{"core:datatype":"ci16_le","core:sample_rate":"fast"}}',
               '{"global":{"core:datatype":"ci16_le"},"captures":["x",42]}',
               '{"global":{"core:datatype":"ci16_le"},"captures":[{"core:sample_start":"oops","core:frequency":"nan"}]}',
               '{"global":{"core:datatype":"ci16_le"},"annotations":[null,5]}'):
        meta.write_text(js)
        chunks, warns = sigmf.inspect_sigmf(str(meta))   # must not raise
        assert chunks                                    # always a degraded result
