"""Tracker-module walkers (MOD/XM/IT) on synthetic ground-truth files, plus the
IT/MP4/WAV pointer (xref) annotations."""
import struct

from acidcat.core import sniff
from acidcat.core.walk import tracker as wtk


# ── MOD ────────────────────────────────────────────────────────────

def _make_mod():
    title = b"TEST".ljust(20, b"\x00")
    smp_hdrs = []
    for i in range(31):
        name = b"snare".ljust(22, b"\x00") if i == 0 else b"\x00" * 22
        length_words = 10 if i == 0 else 0          # 10 words = 20 bytes
        smp_hdrs.append(name + struct.pack(">H", length_words)
                        + bytes([0, 64]) + struct.pack(">HH", 0, 1))
    order = bytes([0]) + b"\x00" * 127              # one pattern, index 0
    body = title + b"".join(smp_hdrs) + bytes([1, 127]) + order + b"M.K."
    body += b"\x00" * (64 * 4 * 4)                  # one 4-channel pattern
    body += bytes(range(20))                        # sample 0 PCM
    return body


def test_mod_detect_and_walk(tmp_path):
    p = tmp_path / "x.mod"
    p.write_bytes(_make_mod())
    assert sniff.sniff(str(p)) == "mod"
    chunks, warns = wtk.inspect_mod(str(p))
    head = chunks[0]
    assert head["id"] == "MOD"
    assert "4ch" in head["summary"] and "M.K." in head["summary"]
    smp = [c for c in chunks if c["id"].startswith("smp")]
    assert len(smp) == 1
    # sample data sits right after the single 1024-byte pattern
    assert smp[0]["offset"] == 1084 + 1024
    assert smp[0]["size"] == 20
    assert not warns


# ── XM ─────────────────────────────────────────────────────────────

def _make_xm():
    hdr = b"Extended Module: "
    hdr += b"song".ljust(20, b"\x00") + b"\x1a" + b"acidcat".ljust(20, b"\x00")
    hdr += struct.pack("<H", 0x0104)
    hdr += struct.pack("<I", 276)                   # header size from offset 60
    hdr += struct.pack("<HHHHHHHH", 1, 0, 4, 1, 1, 0, 6, 125)  # body fields
    hdr += b"\x00" * 256                            # order table
    # one pattern: header len 9, packing 0, rows 64, packed 0 bytes
    hdr += struct.pack("<IBHH", 9, 0, 64, 0)
    # one instrument: minimal 29-byte header, 1 sample
    hdr += struct.pack("<I", 29) + b"lead".ljust(22, b"\x00") + bytes([0]) \
        + struct.pack("<H", 1)
    # one 40-byte sample header: length 20, type 0 (8-bit), name at +18
    hdr += struct.pack("<I", 20) + b"\x00" * 14 + b"kick".ljust(22, b"\x00")
    hdr += bytes(range(20))                         # sample PCM
    return hdr


def test_xm_detect_and_walk(tmp_path):
    p = tmp_path / "x.xm"
    p.write_bytes(_make_xm())
    assert sniff.sniff(str(p)) == "xm"
    chunks, warns = wtk.inspect_xm(str(p))
    assert chunks[0]["id"] == "XM"
    assert "8ch" not in chunks[0]["summary"] and "4ch" in chunks[0]["summary"]
    smp = [c for c in chunks if c["id"].startswith("smp")]
    assert len(smp) == 1 and smp[0]["size"] == 20
    assert not warns


# ── IT (with xref) ─────────────────────────────────────────────────

def _make_it():
    body = b"IMPM" + b"song".ljust(26, b"\x00")
    body += struct.pack("<H", 0)                    # philight
    body += struct.pack("<HHHH", 2, 0, 1, 0)        # ord/ins/smp/pat
    body += struct.pack("<HH", 0x0214, 0x0200)      # cwt/cmwt
    body += struct.pack("<H", 0x0009)               # flags
    body += struct.pack("<H", 0)                    # special
    body += bytes([128, 48, 6, 125, 128, 0])        # gv,mv,is,it,sep,pwd
    body += struct.pack("<H", 0) + struct.pack("<I", 0) + b"\x00" * 4  # msg + reserved
    body += b"\x00" * 128                            # channel pan + vol
    body += bytes([0, 0])                            # order (ordnum=2)
    # sample-offset table (1 entry) points at the IMPS header
    imps_off = 194 + 4                              # after the 4-byte table
    body += struct.pack("<I", imps_off)
    assert len(body) == imps_off
    # IMPS header (80 bytes)
    imps = b"IMPS" + b"kick.wav".ljust(12, b"\x00") + bytes([0, 64, 0x01, 64])
    imps += b"kick".ljust(26, b"\x00") + bytes([0, 32])
    imps += struct.pack("<I", 20)                   # length (points)
    imps += struct.pack("<II", 0, 0)                # loop
    imps += struct.pack("<I", 8000)                 # C5
    imps += struct.pack("<II", 0, 0)                # sus loop
    data_off = imps_off + 80
    imps += struct.pack("<I", data_off)             # sample pointer
    imps += bytes([0, 0, 0, 0])                     # vibrato
    assert len(imps) == 80
    body += imps + bytes(range(20))
    return body, imps_off, data_off


def test_it_detect_walk_and_xref(tmp_path):
    raw, imps_off, data_off = _make_it()
    p = tmp_path / "x.it"
    p.write_bytes(raw)
    assert sniff.sniff(str(p)) == "it"
    chunks, warns = wtk.inspect_it(str(p))
    assert chunks[0]["id"] == "IMPM"
    # the sample-offset table entry xrefs the IMPS header
    tbl = next(c for c in chunks if c["id"] == "smp_offsets")
    assert tbl["fields"][0]["xref"] == imps_off
    # the IMPS header's sample_pointer xrefs the PCM
    smp = next(c for c in chunks if c["id"].startswith("smp["))
    ptr = next(f for f in smp["fields"] if f["name"] == "sample_pointer")
    assert ptr["xref"] == data_off
    assert not warns
