"""Sony/Sonic Foundry Wave64 (.w64) structural walker.

RIFF's grammar with three substitutions, and every one of them is a place a
RIFF reader goes wrong rather than stopping:

  ids     a 16-byte GUID, not a 4-character code
  sizes   u64, and they INCLUDE the 24-byte chunk header, where RIFF counts
          only the payload
  align   chunks start on 8-byte boundaries, not 2, and that padding is
          excluded from the declared size

The payloads are unchanged, so this reuses `wav._PARSERS` outright, the way
rf64.py does. Only the framing is Wave64's.

The GUID trick is what makes the format readable at all: for every chunk id
Wave64 inherits from RIFF, the first four bytes on disk are the FOURCC in
lowercase ASCII, followed by one of two constant 12-byte suffixes -- one for
container ids (riff, list), one for audio-chunk ids (wave, fmt, data, fact).
So a GUID is matched by splitting it, not by table lookup, and an unknown
audio chunk still reports a readable id.

Verified against libsndfile 1.2.2 output (the implementation the Sony
specification is known to agree with), which supplied one rule the spec text
does not make obvious: the FINAL chunk is not padded. Its unpadded end is the
end of the file, so a walker that advances by the padded size reports every
well-formed Wave64 as running past EOF.
"""

import struct

from acidcat.core.infra.findings import defect
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import _PAYLOAD_CAP, _f, _open, _size
from acidcat.core.walk.wav import _PARSERS, _parse_data

from acidcat.core.formats.wave64 import (
    CHUNK_HEADER as _CHUNK_HEADER,
    HEADER as _HEADER,
    RIFF_GUID,
    WAVE_GUID,
    chunk_id as _chunk_id,
    is_wave64,
)

_MAX_CHUNKS = 4096             # a forged size cannot make the walk spin


def inspect_wave64(filepath):
    """Walk a Wave64 file: a 40-byte header, then GUID/u64 chunks."""
    file_size = _size(filepath)
    ctx = {"file_size": file_size}
    chunks, file_warns = [], []

    with _open(filepath) as f:
        hdr = f.read(_HEADER)
        if len(hdr) < _HEADER:
            # reachable through fmt_override, which promises to degrade like
            # any other walk; wav.py and rf64.py carry the same guard
            return chunks, [f"file is {len(hdr)} bytes; a Wave64 header needs "
                            f"{_HEADER}"]
        if hdr[:16] != RIFF_GUID:
            file_warns.append(defect("magic.mismatch", "missing the Wave64 RIFF GUID"))
        declared = struct.unpack_from("<Q", hdr, 16)[0]
        form = hdr[24:40]
        form_id, _note = _chunk_id(form)
        if form != WAVE_GUID:
            file_warns.append(defect(
                "magic.mismatch",
                f"form GUID is {form_id!r}, expected 'wave'"))
        # Unlike RIFF, this counts the whole file including its own 40-byte
        # header, so it is compared against the file size directly.
        if declared != file_size:
            file_warns.append(
                f"header declares {declared:,} bytes, file is {file_size:,}")

        chunks.append({
            "id": "wave64", "offset": 0, "size": _HEADER,
            "payload_base": 0, "payload_len": _HEADER, "extent_len": _HEADER,
            "summary": f"Wave64 {form_id}, {declared:,} bytes declared",
            "fields": [
                _f(0x00, 16, "riff_guid", hdr[:16].hex(" "),
                   "'riff' + the container-class UUID suffix"),
                _f(0x10, 8, "file_size", f"{declared:,}",
                   "u64, and it counts this 40-byte header too",
                   enc="<Q", raw=declared),
                _f(0x18, 16, "form_guid", form_id,
                   "'wave' + the audio-class UUID suffix"),
            ],
            "warnings": [],
        })

        pos = _HEADER
        seen = 0
        while pos + _CHUNK_HEADER <= file_size:
            if seen >= _MAX_CHUNKS:
                file_warns.append(hit(
                    "work_steps", _MAX_CHUNKS, seen,
                    f"stopped after {_MAX_CHUNKS} chunks; the file may continue"))
                break
            f.seek(pos)
            head = f.read(_CHUNK_HEADER)
            if len(head) < _CHUNK_HEADER:
                break
            guid = head[:16]
            size = struct.unpack_from("<Q", head, 16)[0]
            cid, note = _chunk_id(guid)

            # The size counts the header it is part of, so anything under 24 is
            # not a short chunk, it is a number that cannot be a size -- and
            # advancing by it would not move the cursor.
            if size < _CHUNK_HEADER:
                file_warns.append(
                    f"chunk {cid!r} at {pos} declares {size:,} bytes, less than "
                    f"the {_CHUNK_HEADER}-byte header it counts; stopping")
                chunks.append({
                    "id": cid, "offset": pos, "size": 0,
                    "payload_base": pos + _CHUNK_HEADER, "payload_len": 0,
                    "extent_len": max(0, file_size - pos),
                    "summary": "unusable size", "fields": [],
                    "warnings": ["declared size is below the chunk header"],
                })
                break

            payload_len = size - _CHUNK_HEADER
            avail = max(0, file_size - pos - _CHUNK_HEADER)
            truncated = payload_len > avail
            if truncated:
                file_warns.append(defect(
                    "size.overrun",
                    f"chunk {cid!r} claims {payload_len:,} payload bytes but "
                    f"only {avail:,} remain"))
            real_len = min(payload_len, avail)

            payload = f.read(min(real_len, _PAYLOAD_CAP))
            entry = {
                "id": cid, "offset": pos, "size": real_len,
                "payload_base": pos + _CHUNK_HEADER, "payload_len": real_len,
                "extent_len": _CHUNK_HEADER + real_len,
                "summary": "", "fields": [], "warnings": [],
            }
            if note:
                entry["fields"].append(_f(None, 0, "guid", note))
            try:
                if cid == "data":
                    entry["summary"], pf, pw = _parse_data(
                        payload, ctx, real_len, avail)
                    entry["fields"].extend(pf)
                    entry["warnings"].extend(pw)
                elif cid in _PARSERS:
                    entry["summary"], pf, pw = _PARSERS[cid](payload, ctx)
                    entry["fields"].extend(pf)
                    entry["warnings"].extend(pw)
                else:
                    entry["summary"] = (f"unparsed, first bytes: "
                                        f"{payload[:16].hex(' ')}")
            except Exception as e:                      # noqa: BLE001
                entry["warnings"].append(
                    f"parse error: {e.__class__.__name__}: {e}")
            if truncated:
                entry["warnings"].append(defect(
                    "size.overrun",
                    "payload runs past the end of the file"))
            chunks.append(entry)
            seen += 1

            # 8-byte alignment, and the padding is NOT counted in the size.
            # The final chunk is not padded -- libsndfile ends the file at its
            # unpadded end -- so a bare `pos += padded` reports every
            # well-formed Wave64 as overrunning. Stop when the unpadded end has
            # reached the file instead.
            end = pos + _CHUNK_HEADER + real_len
            if end >= file_size:
                break
            pos = end + (-size & 7)

    return chunks, file_warns
