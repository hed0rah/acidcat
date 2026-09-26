"""RMID walker: a Standard MIDI File wrapped in a RIFF container.

An RMID file is `RIFF` + size + form type `RMID`, with the actual SMF carried in
a `data` chunk (and sometimes a `DISP`/`INFO` chunk alongside). This walker
reports the RIFF wrapper, then hands the inner SMF bytes to the MIDI walker so
the MThd/MTrk detail shows through, with offsets shifted to the wrapped position.
Little-endian RIFF sizes; the wrapped MIDI is big-endian, decoded by the delegate.
"""

from acidcat.core.primitives.notes import coverage
import struct

from acidcat.core.walk import midi as midimod
from acidcat.core.infra.source import BytesSource
from acidcat.core.walk.base import _f, _open, _size

_RMID_CAP = 256 * 1024 * 1024      # a RIFF-wrapped SMF; match the MIDI read cap


def inspect_rmid(filepath, deep=False):
    size = _size(filepath)
    with _open(filepath) as f:
        data = f.read(min(size, _RMID_CAP))
    warns = []
    if size > _RMID_CAP:
        warns.append(coverage(f"file exceeds {_RMID_CAP >> 20} MB; parsed the first "
                     f"{_RMID_CAP >> 20} MB"))
    if data[:4] != b"RIFF" or data[8:12] != b"RMID":
        warns.append("missing RIFF/RMID magic")
    riff_size = struct.unpack_from("<I", data, 4)[0] if len(data) >= 8 else 0
    chunks = [{"id": "RIFF", "offset": 0, "size": len(data),
               "summary": "RMID (RIFF-wrapped MIDI)",
               "fields": [_f(0x00, 4, "magic", "RIFF"),
                          _f(0x04, 4, "riff_size", f"{riff_size:,}",
                             "bytes after this field"),
                          _f(0x08, 4, "form", "RMID")],
               "warnings": [], "payload_base": 0}]

    pos = 12                                # after RIFF + size + the RMID form id
    midi_off = midi_len = None
    guard = 0
    while pos + 8 <= len(data) and guard < 1000:
        guard += 1
        cid = data[pos:pos + 4]
        clen = struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        if body + clen > len(data):
            clen = max(0, len(data) - body)
        if cid == b"data" and midi_off is None:
            midi_off, midi_len = body, clen
        else:
            chunks.append({"id": cid.decode("latin-1", "replace"), "offset": pos,
                           "size": clen, "summary": "", "fields": [],
                           "warnings": [], "payload_base": body})
        pos = body + clen + (clen & 1)      # RIFF chunks pad to even

    if midi_off is None:
        warns.append("no data chunk (the wrapped MIDI is missing)")
        return chunks, warns

    # size is the payload length (the SMF); payload_base already skips the
    # 8-byte data-chunk header, so adding it here counted the header twice and
    # the extent ran eight bytes past the file.
    chunks.append({"id": "data", "offset": midi_off - 8, "size": midi_len,
                   "summary": f"wrapped SMF, {midi_len:,} bytes",
                   # the id and size are the header, before payload_base
                   "fields": [_f(-8, 4, "chunk", "data"),
                              _f(-4, 4, "size", f"{midi_len:,}")],
                   "warnings": [], "payload_base": midi_off})

    # the wrapped SMF is walked in memory by the MIDI walker: no temp file
    inner = data[midi_off:midi_off + midi_len]
    try:
        m_chunks, m_warns = midimod.inspect_midi(
            BytesSource(inner, name="wrapped.mid"), deep=deep)
    except Exception as e:                  # a malformed inner SMF should not crash
        warns.append(f"wrapped MIDI did not parse: {e}")
        return chunks, warns

    warns += m_warns
    for mc in m_chunks:                     # shift the inner offsets into place
        mc = dict(mc)
        mc["offset"] = (mc.get("offset") or 0) + midi_off
        if mc.get("payload_base") is not None:
            mc["payload_base"] = mc["payload_base"] + midi_off
        chunks.append(mc)
    return chunks, warns
