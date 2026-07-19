"""Tests for the IFF 8SVX (Amiga 8-bit sampled voice) walker."""

import struct

from acidcat.core import sniff as sniffmod
from acidcat.core.walk import walk_file
from acidcat.core.walk.svx import inspect_8svx


def _chunk(cid, payload):
    return cid + struct.pack(">I", len(payload)) + payload + (
        b"\x00" if len(payload) & 1 else b"")


def _vhdr(one=1000, rep=0, cyc=0, rate=8000, octs=1, comp=0, vol=0x10000):
    return _chunk(b"VHDR", struct.pack(">IIIHBBI", one, rep, cyc, rate, octs, comp, vol))


def _form(chunks, ftype=b"8SVX", form_size=None):
    body = ftype + b"".join(chunks)
    size = form_size if form_size is not None else len(body)
    return b"FORM" + struct.pack(">I", size) + body


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_sniff_recognizes_8svx():
    head = b"FORM" + struct.pack(">I", 100) + b"8SVX" + b"VHDR"
    assert sniffmod.sniff_bytes(head) == "8svx"


def test_basic_8svx_decode(tmp_path):
    data = _form([_vhdr(one=8000, rate=16000),
                  _chunk(b"NAME", b"kick"),
                  _chunk(b"ANNO", b"Audio Master II"),
                  _chunk(b"BODY", b"\x00" * 8000)])
    p = _write(tmp_path, "k.8svx", data)
    label, chunks, warns = walk_file(p)
    assert label == "IFF/8SVX"
    ids = [c["id"] for c in chunks]
    assert ids == ["FORM", "VHDR", "NAME", "ANNO", "BODY"]
    vhdr = next(c for c in chunks if c["id"] == "VHDR")
    rate = next(f for f in vhdr["fields"] if f["name"] == "samplesPerSec")
    assert rate["value"] == 16000
    assert "Audio Master II" in chunks[0]["summary"]      # ANNO tool in the summary
    assert "'kick'" in chunks[0]["summary"]


def test_vhdr_fields(tmp_path):
    data = _form([_vhdr(one=1234, rep=567, rate=11025, octs=3, comp=0, vol=0x8000),
                  _chunk(b"BODY", b"\x00" * 16)])
    _, chunks, _ = walk_file(_write(tmp_path, "v.8svx", data))
    vhdr = next(c for c in chunks if c["id"] == "VHDR")
    fv = {f["name"]: f["value"] for f in vhdr["fields"]}
    assert fv["oneShotHiSamples"] == 1234
    assert fv["repeatHiSamples"] == 567
    assert fv["ctOctave"] == 3
    assert fv["volume"] == 0x8000                         # 0.5x, decoded in the note


def test_fibonacci_delta_flagged(tmp_path):
    data = _form([_vhdr(comp=1), _chunk(b"BODY", b"\x00" * 32)])
    _, chunks, _ = walk_file(_write(tmp_path, "c.8svx", data))
    vhdr = next(c for c in chunks if c["id"] == "VHDR")
    comp = next(f for f in vhdr["fields"] if f["name"] == "sCompression")
    assert "Fibonacci" in comp["note"]
    assert "Fibonacci-delta" in chunks[0]["summary"]


def test_chan_decode(tmp_path):
    data = _form([_vhdr(), _chunk(b"CHAN", struct.pack(">I", 6)), _chunk(b"BODY", b"\x00" * 4)])
    _, chunks, _ = walk_file(_write(tmp_path, "ch.8svx", data))
    chan = next(c for c in chunks if c["id"] == "CHAN")
    assert "stereo" in chan["summary"]


def test_form_size_undercount_flagged(tmp_path):
    # a writer that undercounts the FORM size (real bug in the Amiga corpus):
    # every chunk is correct but form_size is short. Trust the chunks, flag it.
    inner = [_vhdr(), _chunk(b"BODY", b"\x00" * 100)]
    body_len = len(b"8SVX" + b"".join(inner))
    data = _form(inner, form_size=body_len - 12)          # undercount by 12
    p = _write(tmp_path, "bug.8svx", data)
    _, chunks, _ = walk_file(p)
    form = chunks[0]
    assert any("off by 12" in w for w in form["warnings"])
    # the walk still finds every chunk despite the wrong FORM size
    assert [c["id"] for c in chunks] == ["FORM", "VHDR", "BODY"]


def test_truncated_body_degrades(tmp_path):
    # BODY declares more than is present -> flagged, never raises
    data = _form([_vhdr(), b"BODY" + struct.pack(">I", 999999) + b"\x00" * 10])
    p = _write(tmp_path, "t.8svx", data)
    _, chunks, _ = walk_file(p)
    body = next(c for c in chunks if c["id"] == "BODY")
    assert any("truncated" in w for w in body.get("warnings", []))


def test_non_8svx_rejected(tmp_path):
    # a FORM AIFF is not ours; inspect_8svx declines it cleanly
    data = b"FORM" + struct.pack(">I", 20) + b"AIFF" + b"\x00" * 12
    chunks, warns = inspect_8svx(_write(tmp_path, "a.aiff", data))
    assert chunks == []
    assert warns and "8SVX" in warns[0]
