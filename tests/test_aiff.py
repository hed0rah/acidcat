"""tests for acidcat.core.aiff."""

import struct

from acidcat.core.aiff import parse_aiff, _parse_ieee_extended


# 80-bit extended floats, verified by hand: exponent 0x400E means
# 2^15, and the top mantissa word of 44100/48000 is the rate itself
RATE_44100 = bytes.fromhex("400eac440000000000000000")[:10]
RATE_48000 = bytes.fromhex("400ebb800000000000000000")[:10]


def _chunk(cid, payload):
    raw = cid + struct.pack(">I", len(payload)) + payload
    if len(payload) % 2:
        raw += b"\x00"
    return raw


def _form(form_type, *chunks):
    body = form_type + b"".join(chunks)
    return b"FORM" + struct.pack(">I", len(body)) + body


def _comm_aiff(channels=1, frames=441, bits=16, rate=RATE_44100):
    return _chunk(b"COMM", struct.pack(">hIh", channels, frames, bits) + rate)


def _comm_aifc(comp, name=b"", channels=1, frames=441, bits=16):
    pstr = bytes([len(name)]) + name
    if (1 + len(name)) % 2:
        pstr += b"\x00"
    payload = struct.pack(">hIh", channels, frames, bits) + RATE_44100 + comp + pstr
    return _chunk(b"COMM", payload)


def _ssnd(n_bytes=8):
    return _chunk(b"SSND", struct.pack(">II", 0, 0) + b"\x00" * n_bytes)


class TestIeeeExtended:
    def test_44100(self):
        assert _parse_ieee_extended(RATE_44100) == 44100.0

    def test_48000(self):
        assert _parse_ieee_extended(RATE_48000) == 48000.0

    def test_zero(self):
        assert _parse_ieee_extended(b"\x00" * 10) == 0.0

    def test_short_input(self):
        assert _parse_ieee_extended(b"\x40") == 0.0


class TestParseAiff:
    def test_minimal_aiff(self, tmp_path):
        f = tmp_path / "a.aiff"
        f.write_bytes(_form(b"AIFF", _comm_aiff(), _ssnd()))
        _, meta, seen = parse_aiff(str(f))
        assert meta["channels"] == 1
        assert meta["num_frames"] == 441
        assert meta["sample_rate"] == 44100
        assert meta["duration_sec"] == 0.01
        assert meta["compression"] == "none"
        assert "COMM" in seen

    def test_aifc_none_compression(self, tmp_path):
        f = tmp_path / "a.aifc"
        f.write_bytes(_form(b"AIFC", _comm_aifc(b"NONE", b"not compressed"), _ssnd()))
        _, meta, _ = parse_aiff(str(f))
        assert meta["compression"] == "NONE"

    def test_aifc_raw_compression_with_trailing_space(self, tmp_path):
        """the 'raw ' 4cc carries a meaningful trailing space. the old
        code stripped the value before checking it against a known-set
        that stores the spaced form, so spec-conformant raw-PCM AIFC
        reported unknown:raw instead of raw.
        """
        f = tmp_path / "raw.aifc"
        f.write_bytes(_form(b"AIFC", _comm_aifc(b"raw "), _ssnd()))
        _, meta, _ = parse_aiff(str(f))
        assert meta["compression"] == "raw"

    def test_aifc_unknown_compression_is_surfaced(self, tmp_path):
        f = tmp_path / "x.aifc"
        f.write_bytes(_form(b"AIFC", _comm_aifc(b"XXyy"), _ssnd()))
        _, meta, _ = parse_aiff(str(f))
        assert meta["compression"] == "unknown:XXyy"

    def test_not_aiff(self, tmp_path):
        f = tmp_path / "n.bin"
        f.write_bytes(b"\x00" * 64)
        _, meta, seen = parse_aiff(str(f))
        assert meta["channels"] is None
        assert seen == []


def _basc(beats=32, root=48, scale=3, num=4, den=4):
    payload = struct.pack(">IIHHHH", 1, beats, root, scale, num, den)
    payload += b"\x00" * (84 - len(payload))
    return _chunk(b"basc", payload)


class TestAppleLoopsBasc:
    """the basc chunk is Apple Loops metadata: beat count and root key
    for tempo-flexible loops. no official spec; layout field-verified
    against 103 indexed Apple Loops (derived bpm matched the filename
    bpm on every file, root matched every filename key).
    """

    def test_basc_fields_surface(self, tmp_path):
        f = tmp_path / "loop.aiff"
        f.write_bytes(_form(b"AIFF", _comm_aiff(frames=441),
                            _basc(beats=32, root=57), _ssnd()))
        _, meta, seen = parse_aiff(str(f))
        assert meta["basc_beats"] == 32
        assert meta["basc_root_key"] == 57
        assert "basc" in seen

    def test_no_basc_keys_absent(self, tmp_path):
        f = tmp_path / "plain.aiff"
        f.write_bytes(_form(b"AIFF", _comm_aiff(), _ssnd()))
        _, meta, _ = parse_aiff(str(f))
        assert meta.get("basc_beats") is None


class TestIeeeExtendedNonFinite:
    # all-ones exponent encodes IEEE inf/NaN; a huge finite exponent
    # overflows a double. int(inf) downstream raised OverflowError and
    # turned the whole COMM chunk into a parse error, losing channels
    # and frame count too.

    def test_inf_returns_zero(self):
        assert _parse_ieee_extended(b"\x7f\xff" + b"\x80" + b"\x00" * 7) == 0.0

    def test_nan_returns_zero(self):
        assert _parse_ieee_extended(b"\x7f\xff" + b"\xc0" + b"\x00" * 7) == 0.0

    def test_negative_inf_returns_zero(self):
        assert _parse_ieee_extended(b"\xff\xff" + b"\x80" + b"\x00" * 7) == 0.0

    def test_huge_finite_exponent_returns_zero(self):
        # exponent 0x7FFE: finite in 80-bit but overflows a double
        assert _parse_ieee_extended(b"\x7f\xfe" + b"\x80" + b"\x00" * 7) == 0.0

    def test_comm_survives_inf_rate(self, tmp_path):
        inf_rate = b"\x7f\xff" + b"\x80" + b"\x00" * 7
        f = tmp_path / "inf.aiff"
        f.write_bytes(_form(b"AIFF", _comm_aiff(rate=inf_rate), _ssnd()))
        results, meta, _ = parse_aiff(str(f), enumerate_all=True)
        assert meta["channels"] == 1          # COMM still decodes
        assert meta["num_frames"] == 441
        assert meta["sample_rate"] == 0
        assert meta["duration_sec"] is None
        assert not any(k == "error" for _, k, _ in results)
