"""tests for acidcat.core.formats.aiff primitives and the AIFF walker behaviors
that replaced the legacy parse_aiff parser."""

import struct

from acidcat.core.formats.aiff import _parse_ieee_extended
from acidcat.core.walk.aiff import inspect_aiff


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


def _walk(f, form="AIFF"):
    ctx = {}
    chunks, warns = inspect_aiff(str(f), form, ctx=ctx)
    return chunks, warns, ctx


def _comm_warns(chunks):
    comm = next(c for c in chunks if c["id"] == "COMM")
    return comm["warnings"]


class TestIeeeExtended:
    def test_44100(self):
        assert _parse_ieee_extended(RATE_44100) == 44100.0

    def test_48000(self):
        assert _parse_ieee_extended(RATE_48000) == 48000.0

    def test_zero(self):
        assert _parse_ieee_extended(b"\x00" * 10) == 0.0

    def test_short_input(self):
        assert _parse_ieee_extended(b"\x40") == 0.0


class TestAiffWalker:
    def test_minimal_aiff(self, tmp_path):
        f = tmp_path / "a.aiff"
        f.write_bytes(_form(b"AIFF", _comm_aiff(), _ssnd()))
        chunks, _, ctx = _walk(f)
        assert ctx["channels"] == 1
        assert ctx["frames"] == 441
        assert ctx["sample_rate"] == 44100
        assert ctx["duration"] == 0.01
        assert "COMM" in [c["id"] for c in chunks]

    def test_aifc_none_compression(self, tmp_path):
        f = tmp_path / "a.aifc"
        f.write_bytes(_form(b"AIFC", _comm_aifc(b"NONE", b"not compressed"), _ssnd()))
        chunks, _, ctx = _walk(f, "AIFC")
        assert ctx["compression"] == "NONE"
        assert not any("not in the known set" in w for w in _comm_warns(chunks))

    def test_aifc_raw_compression_with_trailing_space(self, tmp_path):
        """the 'raw ' 4cc carries a meaningful trailing space. it is in the
        known set as the spaced form, so spec-conformant raw-PCM AIFC must
        not draw an unknown-compression warning.
        """
        f = tmp_path / "raw.aifc"
        f.write_bytes(_form(b"AIFC", _comm_aifc(b"raw "), _ssnd()))
        chunks, _, ctx = _walk(f, "AIFC")
        assert ctx["compression"] == "raw "
        assert not any("not in the known set" in w for w in _comm_warns(chunks))

    def test_aifc_unknown_compression_is_surfaced(self, tmp_path):
        f = tmp_path / "x.aifc"
        f.write_bytes(_form(b"AIFC", _comm_aifc(b"XXyy"), _ssnd()))
        chunks, _, ctx = _walk(f, "AIFC")
        assert ctx["compression"] == "XXyy"
        assert any("'XXyy' not in the known set" in w for w in _comm_warns(chunks))

    def test_not_aiff(self, tmp_path):
        f = tmp_path / "n.bin"
        f.write_bytes(b"\x00" * 64)
        chunks, _, ctx = _walk(f)
        assert chunks == []
        assert "channels" not in ctx


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
        chunks, _, ctx = _walk(f)
        assert ctx["basc_beats"] == 32
        assert ctx["basc_root_key"] == 57
        assert "basc" in [c["id"] for c in chunks]

    def test_no_basc_keys_absent(self, tmp_path):
        f = tmp_path / "plain.aiff"
        f.write_bytes(_form(b"AIFF", _comm_aiff(), _ssnd()))
        _, _, ctx = _walk(f)
        assert ctx.get("basc_beats") is None


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
        chunks, _, ctx = _walk(f)
        assert ctx["channels"] == 1          # COMM still decodes
        assert ctx["frames"] == 441
        assert ctx["sample_rate"] == 0
        assert ctx["duration"] is None
        assert any("sample rate decodes to 0" in w for w in _comm_warns(chunks))


# -- the Apple chunks, read through the shared readers ----------------
# A library of 4,015 AIFFs carried 214 CHAN, 241 AFAn and 53 ResU, and acidcat
# reported every one of them as unparsed bytes -- while naming the same bytes
# inside a WAV. One reader per structure is the fix; these are the tests that
# say each container reaches it.


def _aiff_with(chunk, tmp_path, name="apple.aif"):
    f = tmp_path / name
    f.write_bytes(_form(b"AIFF", _comm_aiff(channels=2), _ssnd(), chunk))
    return _walk(f)


def _named(chunks, cid):
    return next(c for c in chunks if c["id"] == cid)


def test_chan_is_the_coreaudio_layout_not_unparsed_bytes(tmp_path):
    """Logic writes CHAN as 32 bytes: the 12-byte AudioChannelLayout header and
    a single all-zero channel description the header says is not there, because
    the C struct declares mChannelDescriptions[1] and the writer emits the
    struct rather than the fields. 214 files in one library, byte-identical."""
    payload = struct.pack(">III", 0x00650002, 0x00000003, 0) + b"\x00" * 20
    chunks, _w, _c = _aiff_with(_chunk(b"CHAN", payload), tmp_path)
    chan = _named(chunks, "CHAN")
    f = {x["name"]: x for x in chan["fields"]}
    assert f["layout_tag"]["note"] == "stereo, 2 channel(s)"
    assert f["channel_bitmap"]["note"] == "FL, FR"
    assert f["descriptions"]["value"] == 0
    assert not chan["warnings"]


def test_an_unnamed_layout_tag_still_reports_its_channel_count(tmp_path):
    """The tag space continues into the MPEG, AC-3 and AAC families, which are
    deliberately not named -- a wrong name there would be a confident lie about
    a speaker map. The number and the channel count never guess."""
    payload = struct.pack(">III", (200 << 16) | 6, 0, 0)
    chunks, _w, _c = _aiff_with(_chunk(b"CHAN", payload), tmp_path)
    note = {x["name"]: x for x in _named(chunks, "CHAN")["fields"]}["layout_tag"]["note"]
    assert note == "layout 200, 6 channel(s)"


def test_resu_gives_up_logics_tempo(tmp_path):
    """ResU is zlib-compressed JSON: a DAW's own opinion about the material,
    written into the file. On a loop library it is the tempo someone worked
    to."""
    import json
    import zlib
    doc = {"rec_ctx": {"Tempo": [{"tempo": 99.0}],
                       "time_signatures": [{"signature": "4/4"}],
                       "beats": [{"onset": True}] * 33}}
    payload = zlib.compress(json.dumps(doc).encode())
    chunks, _w, _c = _aiff_with(_chunk(b"ResU", payload), tmp_path)
    resu = _named(chunks, "ResU")
    f = {x["name"]: x["value"] for x in resu["fields"]}
    assert f["tempo"] == "99 BPM"
    assert f["time_signature"] == "4/4"
    assert "99 BPM" in resu["summary"]


def test_afan_is_named_the_same_way_in_an_aiff_as_in_a_wav(tmp_path):
    """The point of the shared reader: the same bytes get the same answer in
    either container. This asserts the AIFF half; tests/test_riff.py asserts
    the RIFF half against the same structure."""
    archive = (bytes([0x04, 0x0B]) + b"streamtyped"
               + bytes([19]) + b"NSMutableDictionary"
               + bytes([12]) + b"NSDictionary")
    chunks, _w, _c = _aiff_with(_chunk(b"AFAn", archive), tmp_path)
    afan = _named(chunks, "AFAn")
    assert "Apple typedstream" in afan["summary"]
    classes = [x["value"] for x in afan["fields"] if x["name"] == "class"]
    assert classes[:2] == ["NSMutableDictionary", "NSDictionary"]


# -- the Apple Loops chunks -------------------------------------------


def _trns(count, stride=4410, rate_hz=44100):
    head = (struct.pack(">HHHH", 1, 50, 16, 0)
            + struct.pack(">I", 1428390247)
            + b"\x00" * 0x3C + struct.pack(">I", count))
    assert len(head) == 76
    return _chunk(b"trns", head + b"".join(
        struct.pack(">HHI", 1, 0, i * stride) + b"\x00" * 16
        for i in range(count)))


def test_trns_positions_are_sample_frames(tmp_path):
    """The transient table is where the slice points are, and the positions are
    frames rather than bytes. The evidence is outside acidcat: on 3,194 files
    carrying a tempo in their own filename, the median gap between transients
    is a sixteenth note at that tempo -- 2,550 of them exactly.

    44,100 Hz and a 4,410-frame gap is a tenth of a second, which is what the
    seconds column has to say.
    """
    f = tmp_path / "loop.aif"
    f.write_bytes(_form(b"AIFF", _comm_aiff(frames=44100), _ssnd(), _trns(8)))
    chunks, _w, _c = _walk(f)
    trns = _named(chunks, "trns")
    fields = {x["name"]: x for x in trns["fields"]}
    assert fields["transients"]["value"] == 8
    assert fields["transient[1]"]["value"] == "frame 4,410"
    assert fields["transient[1]"]["note"] == "0.100 s"
    assert "every 0.100 s" in trns["summary"]


def test_a_transient_past_the_end_of_the_audio_is_reported(tmp_path):
    """34 files in a real library place a slice point past the frame count COMM
    declares. A slice table that runs off the end of its own audio is the kind
    of thing a walker exists to notice."""
    f = tmp_path / "over.aif"
    f.write_bytes(_form(b"AIFF", _comm_aiff(frames=8820), _ssnd(), _trns(8)))
    chunks, _w, _c = _walk(f)
    assert any("fall past" in w for w in _named(chunks, "trns")["warnings"])


def test_a_trns_that_lies_about_its_record_count_says_so(tmp_path):
    head = (struct.pack(">HHHH", 1, 50, 16, 0) + struct.pack(">I", 0)
            + b"\x00" * 0x3C + struct.pack(">I", 500))
    f = tmp_path / "short.aif"
    f.write_bytes(_form(b"AIFF", _comm_aiff(), _ssnd(),
                        _chunk(b"trns", head + b"\x00" * 48)))
    chunks, _w, _c = _walk(f)
    trns = _named(chunks, "trns")
    assert any("declares 500 transients" in w for w in trns["warnings"])
    assert trns["summary"] == "2 transient(s)"


def test_cate_gives_up_the_category_labels(tmp_path):
    """What the loop says it is. 3,199 files in one library carry these and
    acidcat was printing the chunk's name back at the reader."""
    body = struct.pack(">I", 1)
    for label in (b"Drums", b"Drum Kit"):
        body += label + b"\x00" * (50 - len(label))
    f = tmp_path / "cate.aif"
    f.write_bytes(_form(b"AIFF", _comm_aiff(), _ssnd(), _chunk(b"cate", body)))
    chunks, _w, _c = _walk(f)
    cate = _named(chunks, "cate")
    assert cate["summary"] == "Drums / Drum Kit"
    assert [x["value"] for x in cate["fields"] if x["name"] == "label"] == \
        ["Drums", "Drum Kit"]


def test_a_cate_with_no_readable_label_does_not_invent_one(tmp_path):
    """The control. The slot grid is measured rather than specified, so a
    payload that does not fit it has to come back as bytes, not as a label."""
    body = struct.pack(">I", 0) + bytes(range(1, 51))
    f = tmp_path / "odd.aif"
    f.write_bytes(_form(b"AIFF", _comm_aiff(), _ssnd(), _chunk(b"cate", body)))
    chunks, _w, _c = _walk(f)
    cate = _named(chunks, "cate")
    assert "bytes" in cate["summary"]
    assert not [x for x in cate["fields"] if x["name"] == "label"]
