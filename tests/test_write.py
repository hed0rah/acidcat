"""tests for acidcat's write/edit capability (safety + per-format editors)."""

import struct

import pytest

from acidcat.core import writer, edits, edit_riff
from acidcat.core.edit_riff import _iter_chunks


# ── helpers ────────────────────────────────────────────────────────

def _chunk(cid, payload):
    raw = cid + struct.pack("<I", len(payload)) + payload
    return raw + (b"\x00" if len(payload) % 2 else b"")


def _wav(*chunks):
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _fmt():
    return _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16))


def _data(n=8):
    return _chunk(b"data", bytes(range(n % 256)) * (n // (n % 256) if n % 256 else 1)
                  if n % 256 else b"\x00" * n)


def _payload(b, cid):
    return next(c[1] for c in _iter_chunks(b)[0] if c[0] == cid)


# ── safety layer ───────────────────────────────────────────────────

def test_commit_inplace_makes_backup(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"original")
    written, backup = writer.commit(str(p), b"edited")
    assert p.read_bytes() == b"edited"
    assert backup.endswith("_original.bin")
    assert open(backup, "rb").read() == b"original"


def test_commit_output_copy_leaves_input(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"original")
    out = str(tmp_path / "copy.bin")
    written, backup = writer.commit(str(p), b"edited", out=out)
    assert p.read_bytes() == b"original" and backup is None
    assert open(out, "rb").read() == b"edited"


def test_commit_overwrite_skips_backup(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"original")
    _, backup = writer.commit(str(p), b"edited", overwrite=True)
    assert backup is None and p.read_bytes() == b"edited"


# ── WAV: INFO tags ─────────────────────────────────────────────────

def test_wav_info_roundtrip_preserves_audio():
    src = _wav(_fmt(), _data(9))  # odd data -> exercises padding
    audio = _payload(src, b"data")
    out, applied = edit_riff.edit_wav(src, {"title": "Kick", "artist": "Me"})
    assert _payload(out, b"data") == audio  # audio untouched
    info = _payload(out, b"LIST")
    assert b"INAM" in info and b"Kick\x00" in info and b"Me\x00" in info


def test_wav_chunks_stay_word_aligned():
    src = _wav(_fmt(), _data(8))
    out, _ = edit_riff.edit_wav(src, {"title": "abc"})  # odd -> "abc\x00" = 4, even
    # every chunk must begin at an even offset; _iter_chunks would desync otherwise
    ids = [c[0] for c in _iter_chunks(out)[0]]
    assert b"data" in ids and b"LIST" in ids
    assert struct.unpack_from("<I", out, 4)[0] == len(out) - 8  # riff size = file-8


# ── WAV: acid bpm/key ──────────────────────────────────────────────

def test_wav_acid_created_with_correct_meter():
    src = _wav(_fmt(), _data())
    out, _ = edit_riff.edit_wav(src, {"bpm": "140"})
    acid = _payload(out, b"acid")
    den, num = struct.unpack_from("<HH", acid, 16)
    assert (den, num) == (4, 4)  # denominator-first, symmetric here
    assert abs(struct.unpack_from("<f", acid, 20)[0] - 140.0) < 1e-4


def test_wav_acid_key_sets_root_and_flag():
    src = _wav(_fmt(), _data())
    out, _ = edit_riff.edit_wav(src, {"key": "C3"})
    acid = _payload(out, b"acid")
    assert struct.unpack_from("<H", acid, 4)[0] == 60  # C3 = MIDI 60
    assert struct.unpack_from("<I", acid, 0)[0] & 0x02  # root-set flag


def test_wav_acid_preserves_asymmetric_meter():
    acid = struct.pack("<IHHfIHHf", 0, 0, 0x8000, 0.0, 6, 8, 3, 120.0)  # 3/8
    src = _wav(_fmt(), _chunk(b"acid", acid), _data())
    out, _ = edit_riff.edit_wav(src, {"bpm": "95"})
    a = _payload(out, b"acid")
    assert struct.unpack_from("<HH", a, 16) == (8, 3)  # meter untouched


# ── WAV: bext deep header ──────────────────────────────────────────

def test_wav_bext_patch_is_size_stable():
    bext = b"desc".ljust(256, b"\x00") + b"orig".ljust(346 - 256, b"\x00") + b"\x00" * 256
    src = _wav(_fmt(), _chunk(b"bext", bext), _data())
    out, _ = edit_riff.edit_wav(src, {"originator": "acidcat"})
    assert len(out) == len(src)  # fixed-field patch, no size change
    assert _payload(out, b"bext")[256:288].split(b"\x00")[0] == b"acidcat"


# ── WAV: refusals ──────────────────────────────────────────────────

def test_wav_refuses_rf64():
    with pytest.raises(edits.EditError):
        edit_riff.edit_wav(b"RF64" + b"\xff" * 4 + b"WAVE" + b"\x00" * 8, {"title": "x"})


def test_wav_refuses_data_before_fmt():
    src = _wav(_data(), _fmt())
    with pytest.raises(edits.EditError):
        edit_riff.edit_wav(src, {"title": "x"})


def test_wav_refuses_overrunning_chunk():
    src = _wav(_fmt(), b"data" + struct.pack("<I", 999999) + b"\x00\x00")
    with pytest.raises(edits.EditError):
        edit_riff.edit_wav(src, {"title": "x"})


def test_wav_unknown_field_rejected():
    with pytest.raises(edits.EditError):
        edit_riff.edit_wav(_wav(_fmt(), _data()), {"nonsense": "x"})


# ── Vital ──────────────────────────────────────────────────────────

def test_vital_edit_preserves_other_keys():
    data = (b'{"synth_version":"1","preset_name":"Old","author":"a",'
            b'"settings":{"osc_1_on":1.0}}')
    out, applied = edits.edit_vital(data, {"name": "New", "author": "acidcat"})
    import json
    obj = json.loads(out)
    assert obj["preset_name"] == "New" and obj["author"] == "acidcat"
    assert obj["settings"] == {"osc_1_on": 1.0}  # untouched


def test_note_to_midi():
    assert edit_riff._note_to_midi("C3") == 60
    assert edit_riff._note_to_midi("A3") == 69
    assert edit_riff._note_to_midi("60") == 60


def test_wav_smpl_root_note():
    src = _wav(_fmt(), _data())
    out, _ = edit_riff.edit_wav(src, {"root": "C3"})
    smpl = _payload(out, b"smpl")
    assert struct.unpack_from("<I", smpl, 12)[0] == 60  # C3 sampler root
    assert _payload(out, b"data") == _payload(src, b"data")  # audio intact


def test_aiff_edit_preserves_audio_big_endian():
    from acidcat.core import edit_aiff
    def bc(cid, p):
        return cid + struct.pack(">I", len(p)) + p + (b"\x00" if len(p) % 2 else b"")
    body = b"AIFF" + bc(b"COMM", b"\x00" * 18) + bc(b"SSND", b"\x01" * 7)
    aiff = b"FORM" + struct.pack(">I", len(body)) + body
    out, _ = edit_aiff.edit_aiff(aiff, {"title": "Snare", "artist": "Me"})
    chs = {c[0]: c[1] for c in edit_aiff._iter_chunks(out)[0]}
    assert chs[b"NAME"] == b"Snare" and chs[b"AUTH"] == b"Me"
    assert chs[b"SSND"] == b"\x01" * 7  # audio intact
    assert struct.unpack_from(">I", out, 4)[0] == len(out) - 8  # BE FORM size


def test_bitwig_meta_splice():
    from acidcat.core import edits
    def field(key, val):
        return (struct.pack(">I", len(key)) + key + b"\x08"
                + struct.pack(">I", len(val)) + val)
    data = (b"BtWg0003000200" + field(b"creator", b"relo")
            + field(b"tags", b"old"))
    out, applied = edits.edit_bitwig(data, {"creator": "me", "tags": "a b c"})
    m = __import__("acidcat.core.bitwig", fromlist=["parse_meta"]).parse_meta(out)
    assert m["creator"] == "me" and m["tags"] == "a b c"
