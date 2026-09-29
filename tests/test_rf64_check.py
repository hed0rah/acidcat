"""A valid RF64 is not a broken RIFF.

RF64 writes 0xFFFFFFFF in the 32-bit RIFF and data size fields and keeps the
real sizes in the ds64 chunk. The IFF structure model read those fields
literally, so every valid RF64 failed validation with "the data chunk overruns
the file" and repair proposed an 88-byte RIFF size. Found on a file libsndfile
wrote. The checker now declines RF64 with a note, as the tag editor does."""
import struct

from acidcat.core.write import constraints

SENTINEL = 0xFFFFFFFF


def _rf64(n_audio=64):
    fmt = struct.pack("<HHIIHH", 1, 2, 48000, 192000, 4, 16)
    audio = bytes(range(256))[:n_audio]
    body_len = 4 + (8 + 28) + (8 + len(fmt)) + (8 + len(audio))
    ds64 = struct.pack("<QQQI", body_len, len(audio), len(audio) // 4, 0)
    return (b"RF64" + struct.pack("<I", SENTINEL) + b"WAVE"
            + b"ds64" + struct.pack("<I", len(ds64)) + ds64
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", SENTINEL) + audio)


def test_a_valid_rf64_has_no_violations():
    report = constraints.analyze(_rf64())
    assert not report.violations, report.violations
    assert "ds64" in report.note


def test_repair_leaves_a_valid_rf64_untouched():
    data = _rf64()
    new_data, _report = constraints.repair(data)
    assert new_data == data


def test_a_riff_with_a_real_overrun_is_still_caught():
    """The control: the exemption is for the RF64 magic, not for overruns."""
    fmt = struct.pack("<HHIIHH", 1, 2, 48000, 192000, 4, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", 4096) + bytes(64))
    data = b"RIFF" + struct.pack("<I", len(body)) + body
    assert constraints.analyze(data).violations


def test_audio_the_walk_cannot_reach_is_not_advertised_as_repairable():
    """A real CCRMA AIFC declares COMM as 18 bytes but writes the longer AIFC
    layout, so every chunk after it is misread and SSND is never found. repair
    refuses such a file, so check must not print a fix it will refuse."""
    comm = struct.pack(">hIh", 1, 16, 16) + bytes(10) + b"NONE" + b"\x0enot compressed" + b"\x00"
    ssnd = struct.pack(">II", 0, 0) + bytes(32)
    body = (b"AIFC" + b"COMM" + struct.pack(">I", 18) + comm
            + b"SSND" + struct.pack(">I", len(ssnd)) + ssnd)
    data = b"FORM" + struct.pack(">I", len(body)) + body
    report = constraints.analyze(data)
    assert report.violations
    assert not any(v.repairable for v in report.violations)
    assert "never reaches it" in report.violations[0].detail
