"""The IFF structural model: byte-exact round-trip, and repair that recomputes
derived size/pad fields without touching content."""
import struct

import pytest

from acidcat.core import structure as S


# ── builders ───────────────────────────────────────────────────────

def _chunk(cid, payload, endian="<"):
    raw = cid + struct.pack(endian + "I", len(payload)) + payload
    return raw + (b"\x00" if len(payload) & 1 else b"")


def _riff(form, *chunks, size=None):
    body = form + b"".join(chunks)
    n = size if size is not None else len(body)
    return b"RIFF" + struct.pack("<I", n) + body


def _fmt():
    return _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16))


def _wav(*chunks, size=None):
    return _riff(b"WAVE", _fmt(), *chunks, size=size)


# ── round-trip (bedrock invariant) ─────────────────────────────────

def test_roundtrip_flat_wav():
    data = _wav(_chunk(b"data", b"\x01\x02\x03\x04"))
    assert S.emit(S.parse(data)) == data


def test_roundtrip_nested_list():
    info = _chunk(b"INAM", b"title\x00") + _chunk(b"IART", b"artist\x00")
    lst = _chunk(b"LIST", b"INFO" + info)
    data = _wav(_chunk(b"data", b"\x00" * 10), lst)
    assert S.emit(S.parse(data)) == data


def test_roundtrip_odd_chunk_with_pad():
    # 3-byte payload forces a pad byte; round-trip must keep it
    data = _wav(_chunk(b"data", b"\x01\x02\x03"))
    assert data[-1] == 0                      # the pad byte
    assert S.emit(S.parse(data)) == data


def test_roundtrip_aiff_big_endian():
    comm = _chunk(b"COMM", struct.pack(">hIh", 1, 100, 16) + b"\x00" * 4, endian=">")
    ssnd = _chunk(b"SSND", b"\x00" * 8, endian=">")
    body = b"AIFF" + comm + ssnd
    data = b"FORM" + struct.pack(">I", len(body)) + body
    node = S.parse(data)
    assert node.endian == ">"
    assert S.emit(node) == data


# ── repair ─────────────────────────────────────────────────────────

def test_repair_noop_on_clean_file():
    data = _wav(_chunk(b"data", b"\x00" * 8))
    new, changes = S.repair_bytes(data)
    assert changes == []
    assert new == data


def test_repair_fixes_stale_master_size():
    data = _wav(_chunk(b"data", b"\x00" * 100))
    broken = bytearray(data)
    struct.pack_into("<I", broken, 4, 999)     # lie about riff_size
    new, changes = S.repair_bytes(bytes(broken))
    assert struct.unpack_from("<I", new, 4)[0] == len(data) - 8
    assert new == data                         # repairing the lie restores the original
    assert any(c["field"] == "size" and c["old"] == 999 for c in changes)


def test_repair_master_size_cascades_over_nested_list():
    # the master-size recompute must sum a nested LIST's true on-disk length
    # (id+size+type+children), the double-counting bug the +4 fix addressed
    lst = _chunk(b"LIST", b"INFO" + _chunk(b"INAM", b"hi\x00\x00"))
    data = _wav(_chunk(b"data", b"\x00" * 4), lst)
    broken = bytearray(data)
    struct.pack_into("<I", broken, 4, 3)       # only the master size is stale
    new, changes = S.repair_bytes(bytes(broken))
    assert new == data                          # exact restoration
    assert struct.unpack_from("<I", new, 4)[0] == len(data) - 8


def test_recompute_is_idempotent():
    # repairing an already-repaired file is a no-op (the model converges)
    data = _wav(_chunk(b"data", b"\x00" * 9), _chunk(b"LIST", b"INFO"))
    once, _ = S.repair_bytes(data)
    twice, changes = S.repair_bytes(once)
    assert twice == once and changes == []


def test_repair_preserves_appended_data_outside_size():
    data = _wav(_chunk(b"data", b"\x00" * 32))
    appended = b"\xde\xad\xbe\xef random trailing blob"
    broken = bytearray(data)
    struct.pack_into("<I", broken, 4, 12)      # also break the master size
    combined = bytes(broken) + appended
    new, changes = S.repair_bytes(combined)
    # the master size is corrected to cover only the container, appended kept
    assert struct.unpack_from("<I", new, 4)[0] == len(data) - 8
    assert new.endswith(appended)
    assert new[:len(data)] == data


def test_repair_zeroes_nonzero_pad_byte():
    # an odd-length data chunk whose pad byte is non-zero (spec requires 0x00)
    odd_data = b"data" + struct.pack("<I", 3) + b"\x01\x02\x03" + b"\xEE"
    body = b"WAVE" + _fmt() + odd_data
    wav = b"RIFF" + struct.pack("<I", len(body)) + body
    new, changes = S.repair_bytes(wav)
    assert new[-1] == 0
    assert len(new) == len(wav)
    assert any(c["field"] == "pad_byte" for c in changes)


def test_repair_never_alters_data_payload():
    payload = bytes(range(256)) * 4
    data = _wav(_chunk(b"data", payload))
    broken = bytearray(data)
    struct.pack_into("<I", broken, 4, 7)
    new, _ = S.repair_bytes(bytes(broken))
    n = S.parse(new)
    got = next(c.payload for c in n.children if c.id == b"data")
    assert got == payload


# ── guards ─────────────────────────────────────────────────────────

def test_non_iff_rejected():
    assert not S.is_iff(b"ID3\x04not riff")
    with pytest.raises(S.StructError):
        S.parse(b"\x00" * 32)
