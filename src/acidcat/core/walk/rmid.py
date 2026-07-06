"""RMID walker: a Standard MIDI File wrapped in a RIFF container.

An RMID file is `RIFF` + size + form type `RMID`, with the actual SMF carried in
a `data` chunk (and sometimes a `DISP`/`INFO` chunk alongside). This walker
reports the RIFF wrapper, then hands the inner SMF bytes to the MIDI walker so
the MThd/MTrk detail shows through, with offsets shifted to the wrapped position.
Little-endian RIFF sizes; the wrapped MIDI is big-endian, decoded by the delegate.
"""

import os
import struct
import tempfile

from acidcat.core.walk import midi as midimod
from acidcat.core.walk.base import _f


def inspect_rmid(filepath, deep=False):
    with open(filepath, "rb") as f:
        data = f.read()
    warns = []
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

    chunks.append({"id": "data", "offset": midi_off - 8, "size": midi_len + 8,
                   "summary": f"wrapped SMF, {midi_len:,} bytes",
                   "fields": [_f(0x00, 4, "chunk", "data"),
                              _f(0x04, 4, "size", f"{midi_len:,}")],
                   "warnings": [], "payload_base": midi_off})

    inner = data[midi_off:midi_off + midi_len]
    fd, tmp = tempfile.mkstemp(suffix=".mid")
    os.close(fd)
    try:
        with open(tmp, "wb") as t:
            t.write(inner)
        m_chunks, m_warns = midimod.inspect_midi(tmp, deep=deep)
    except Exception as e:                  # a malformed inner SMF should not crash
        warns.append(f"wrapped MIDI did not parse: {e}")
        return chunks, warns
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    warns += m_warns
    for mc in m_chunks:                     # shift the inner offsets into place
        mc = dict(mc)
        mc["offset"] = (mc.get("offset") or 0) + midi_off
        if mc.get("payload_base") is not None:
            mc["payload_base"] = mc["payload_base"] + midi_off
        chunks.append(mc)
    return chunks, warns
