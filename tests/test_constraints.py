"""The constraint framework: the shared Violation/Report vocabulary, the
analyze (read-only) vs apply split, and format-agnostic dispatch."""
import struct

from acidcat.core.write import constraints as C
from acidcat.core.write import structure
from acidcat.core.write.constraints import OFFSET, SIZE, ZERO
from acidcat.core.write.repairers import IffRepairer, Mp4OffsetRepairer


def _wav(payload=b"\x00" * 64):
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    data = b"data" + struct.pack("<I", len(payload)) + payload
    body = b"WAVE" + fmt + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _broken_wav():
    d = bytearray(_wav())
    struct.pack_into("<I", d, 4, 5)          # stale master size
    return bytes(d)


def test_registry_dispatch():
    assert isinstance(C.repairer_for(_wav()), IffRepairer)
    # an MP4 by ftyp brand
    mp4 = struct.pack(">I", 16) + b"ftypM4A " + b"\x00" * 4
    assert isinstance(C.repairer_for(mp4), Mp4OffsetRepairer)
    assert C.repairer_for(b"ID3\x04not a container") is None


def test_analyze_is_read_only_and_witnessed():
    data = _broken_wav()
    report = C.analyze(data)
    assert report.label == "WAVE"
    assert len(report.violations) == 1
    v = report.violations[0]
    assert v.kind == SIZE and v.field == "size"
    assert v.stored == 5 and v.computed == len(_wav()) - 8
    assert v.witness == "end-of-file" and v.repairable
    # analyze must not have mutated the input
    assert data == _broken_wav()


def test_repair_via_framework_restores_bytes():
    new, report = C.repair(_broken_wav())
    assert new == _wav()
    assert report.repairable            # the master-size violation was witnessed


def test_pad_byte_is_zero_kind_witnessed_by_spec():
    # 8-bit mono: 3 bytes is 3 whole frames, so the 0xEE after them is filler.
    # (a 16-bit fmt here made 3 bytes end mid-frame, which is a cut size, not
    # a pad: see test_cut_audio_size_is_a_defect_not_filler)
    odd = b"data" + struct.pack("<I", 3) + b"\x01\x02\x03" + b"\xEE"
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) \
        + struct.pack("<HHIIHH", 1, 1, 44100, 44100, 1, 8) + odd
    wav = b"RIFF" + struct.pack("<I", len(body)) + body
    report = C.analyze(wav)
    pad = next(v for v in report.violations if v.field == "pad_byte")
    assert pad.kind == ZERO and pad.witness.startswith("spec") and pad.repairable


def test_keep_pad_opt_suppresses_pad_violation():
    odd = b"data" + struct.pack("<I", 3) + b"\x01\x02\x03" + b"\xEE"
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) \
        + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16) + odd
    wav = b"RIFF" + struct.pack("<I", len(body)) + body
    kept = C.analyze(wav, {"keep_pad": True})
    assert not any(v.field == "pad_byte" for v in kept.violations)


def test_healthy_file_has_no_violations():
    assert C.analyze(_wav()).violations == []


def test_analyze_accepts_memoryview():
    # audit maps the file and analyzes through a memoryview (zero-copy chunk
    # payloads); the report must match the bytes path, ids must still decode
    with memoryview(_broken_wav()) as view:
        report = C.analyze(view)
    ref = C.analyze(_broken_wav())
    assert report.label == ref.label == "WAVE"
    assert [(v.kind, v.path, v.stored, v.computed) for v in report.violations] \
        == [(v.kind, v.path, v.stored, v.computed) for v in ref.violations]


def test_offset_violation_kind_from_mp4(tmp_path):
    # build a minimal broken single-track MP4 and confirm the framework reports
    # an OFFSET-kind violation witnessed by mdat + sample tables
    def _box(t, p):
        return struct.pack(">I", 8 + len(p)) + t + p

    def _full(t, p):
        return _box(t, b"\x00\x00\x00\x00" + p)

    # stsz: version/flags, sample_size=0 (per-sample table follows), count=2, sizes 10,10
    stsz = _full(b"stsz", struct.pack(">II", 0, 2) + struct.pack(">II", 10, 10))
    stsc = _full(b"stsc", struct.pack(">I", 1) + struct.pack(">III", 1, 1, 1))
    ftyp = _box(b"ftyp", b"M4A \x00\x00\x00\x00")

    def tree(offs):
        stco = _full(b"stco", struct.pack(">I", 2)
                     + struct.pack(">II", offs[0], offs[1]))
        stbl = stsz + stsc + stco
        return _box(b"moov", _box(b"trak", _box(b"mdia",
                   _box(b"minf", _box(b"stbl", stbl)))))

    probe = ftyp + tree([0, 0]) + _box(b"mdat", bytes(20))
    from acidcat.core.write import mp4repair
    ms = mp4repair._find_boxes(probe)["mdat"]
    ps = ms["offset"] + ms["hdr"]
    data = ftyp + tree([8, 18]) + _box(b"mdat", bytes(20))   # broken: before mdat
    report = C.analyze(data)
    assert report.label == "MP4"
    assert any(v.kind == OFFSET and v.repairable for v in report.violations)


# a data/SSND size one short of a whole frame leaves the last audio byte where
# a pad byte would sit. It is the size that is wrong; the byte is audio.

def _wav_cut(bits=16, ch=1, n=1000, extra=b""):
    align = ch * bits // 8
    pcm = bytes((i * 37 + 1) & 0xFF for i in range(n))
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack(
        "<HHIIHH", 1, ch, 44100, 44100 * align, align, bits)
    body = b"WAVE" + fmt + b"data" + struct.pack("<I", n - 1) + pcm + extra
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _aiff_cut(n=1000):
    pcm = bytes((i * 11 + 1) & 0xFF for i in range(n))
    comm = struct.pack(">hIh", 1, n // 2, 16) + bytes.fromhex("400EAC44000000000000")
    ssnd = struct.pack(">II", 0, 0) + pcm
    body = (b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm
            + b"SSND" + struct.pack(">I", len(ssnd) - 1) + ssnd)
    return b"FORM" + struct.pack(">I", len(body)) + body


_INAM = b"LIST" + struct.pack("<I", 16) + b"INFO" + b"INAM" + struct.pack("<I", 4) + b"abc\x00"


def _cut_case(data, tmp_path, name):
    from acidcat.cli import main
    rep = C.analyze(data)
    assert [v.field for v in rep.violations] == ["size"]
    v = rep.violations[0]
    assert not v.filler and not v.repairable and rep.defects == [v]
    assert "mid-frame" in v.describe()
    p = tmp_path / name
    p.write_bytes(data)
    assert main(["check", str(p)]) == 1
    assert main(["check", "--fix", "--overwrite", str(p)]) == 1
    assert p.read_bytes() == data                 # the last audio byte kept


def test_cut_audio_size_is_a_defect_not_filler(tmp_path):
    _cut_case(_wav_cut(extra=_INAM), tmp_path, "mid.wav")       # LIST after
    _cut_case(_wav_cut(), tmp_path, "last.wav")                  # data last
    _cut_case(_wav_cut(bits=24, ch=2, n=600), tmp_path, "s24.wav")


def test_cut_ssnd_size_is_a_defect_not_filler(tmp_path):
    _cut_case(_aiff_cut(), tmp_path, "cut.aiff")


def test_text_chunk_pad_stays_filler():
    # a LIST/INAM string one short: "abcd" read as "abc" + pad. Text has no
    # frame, so the byte is filler and --fix zeroes it, as decided (F1)
    pcm = b"\x00" * 1000
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    inam = b"LIST" + struct.pack("<I", 16) + b"INFO" + b"INAM" + struct.pack("<I", 3) + b"abcd"
    body = b"WAVE" + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm + inam
    wav = b"RIFF" + struct.pack("<I", len(body)) + body
    rep = C.analyze(wav)
    assert [v.field for v in rep.violations] == ["pad_byte"]
    assert rep.violations[0].filler and rep.defects == []
