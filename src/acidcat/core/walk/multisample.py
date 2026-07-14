"""Bitwig .multisample walker.

A .multisample is a ZIP archive holding a `multisample.xml` manifest plus the
member sample files (WAV/FLAC). The manifest names the instrument and lists
<sample> zones, each mapping a file to a key range, root note, velocity range,
and loop. This is the one Bitwig type acidcat did not already cover by magic.

Bitwig writes the entries STORED (uncompressed) but with a CRC that does not
match the data, so `ZipFile.read` (which validates the CRC) raises BadZipFile.
Entries are therefore read by seeking past the local file header, bypassing the
CRC check. zipfile + xml.etree are both stdlib, so this adds no dependency.
"""

import os
import xml.etree.ElementTree as ET
import zipfile
import zlib

from acidcat.core.walk.base import _f

_ZONE_CAP = 48                                   # don't flood the view on big kits


def _data_offset(z, zi):
    """Absolute file offset of a zip entry's data (past the local file header).
    ZipInfo.header_offset points at the PK local header, not the payload, so a
    chunk that means to be the entry's bytes -- a STORED sample, so `carve`
    yields the literal WAV/FLAC -- must start here, not at header_offset."""
    z.fp.seek(zi.header_offset)
    hdr = z.fp.read(30)
    n = int.from_bytes(hdr[26:28], "little")
    m = int.from_bytes(hdr[28:30], "little")
    return zi.header_offset + 30 + n + m


def _read_entry(z, name):
    """Read one zip entry bypassing CRC validation (Bitwig writes bad CRCs)."""
    zi = z.getinfo(name)
    z.fp.seek(_data_offset(z, zi))
    raw = z.fp.read(zi.compress_size)
    if zi.compress_type == zipfile.ZIP_DEFLATED:
        return zlib.decompress(raw, -15)         # raw deflate, no zlib wrapper
    return raw


def inspect_multisample(filepath):
    size = os.path.getsize(filepath)
    try:
        z = zipfile.ZipFile(filepath)
    except zipfile.BadZipFile:
        return ([{"id": "multisample", "offset": 0, "size": size,
                  "summary": "not a valid zip archive", "fields": [],
                  "warnings": ["not a zip archive"], "payload_base": 0}],
                ["not a zip archive"])

    warns = []
    with z:
        names = z.namelist()
        infos = {zi.filename: zi for zi in z.infolist()}
        root = None
        if "multisample.xml" not in names:
            warns.append("no multisample.xml in the archive")
        else:
            try:
                xml = _read_entry(z, "multisample.xml").decode("utf-8", "replace")
                root = ET.fromstring(xml)
            except Exception as e:
                warns.append(f"multisample.xml did not parse: {e.__class__.__name__}")

        name = gen = cat = creator = ""
        samples = []
        if root is not None:
            name = root.get("name", "")
            gen = (root.findtext("generator") or "").strip()
            cat = (root.findtext("category") or "").strip()
            creator = (root.findtext("creator") or "").strip()
            samples = root.findall("sample")

        wavs = [n for n in names if n.lower().endswith((".wav", ".flac", ".aif",
                                                        ".aiff", ".ogg"))]
        mfields = [_f(None, 0, "name", name or "(unnamed)")]
        for label, val in (("generator", gen), ("category", cat), ("creator", creator)):
            if val:
                mfields.append(_f(None, 0, label, val))
        mfields.append(_f(None, 0, "sample_zones", len(samples)))
        mfields.append(_f(None, 0, "member_files", len(wavs)))
        mx = infos.get("multisample.xml")
        mx_off = _data_offset(z, mx) if mx else 0
        chunks = [{"id": "multisample.xml",
                   "offset": mx_off,
                   "size": mx.compress_size if mx else 0,
                   "summary": (f"{name or 'multisample'}: {len(samples)} zone(s), "
                               f"{len(wavs)} sample file(s)"),
                   "fields": mfields, "warnings": [], "payload_base": mx_off}]

        for s in samples[:_ZONE_CAP]:
            fname = s.get("file", "?")
            key, vel, loop = s.find("key"), s.find("velocity"), s.find("loop")
            root_note = key.get("root") if key is not None else "?"
            zf = [_f(None, 0, "file", fname)]
            if key is not None:
                zf.append(_f(None, 0, "root", root_note))
                zf.append(_f(None, 0, "key_range",
                             f"{key.get('low', '?')}-{key.get('high', '?')}"))
                if key.get("tune", "0.00") not in ("0.00", "0", None):
                    zf.append(_f(None, 0, "tune", key.get("tune")))
            if vel is not None and (vel.get("low") or vel.get("high")):
                zf.append(_f(None, 0, "velocity",
                             f"{vel.get('low', '0')}-{vel.get('high', '127')}"))
            if loop is not None and loop.get("mode", "off") != "off":
                zf.append(_f(None, 0, "loop",
                             f"{loop.get('mode')} {loop.get('start', '?')}-"
                             f"{loop.get('stop', '?')}"))
            zi = infos.get(fname)
            z_off = _data_offset(z, zi) if zi else 0
            chunks.append({"id": "zone",
                           "offset": z_off,
                           "size": zi.compress_size if zi else 0,
                           "summary": f"{fname} @ root {root_note}",
                           "fields": zf, "warnings": [], "payload_base": z_off})
        if len(samples) > _ZONE_CAP:
            chunks.append({"id": "zone", "offset": 0, "size": 0,
                           "summary": f"... {len(samples) - _ZONE_CAP} more zone(s)",
                           "fields": [], "warnings": [], "payload_base": 0})
    return chunks, warns
