"""Propellerhead ReCycle RX2 walker: the 'CAT ' / 'REX2' IFF container and its
big-endian chunks (HEAD, CREI creator, GLOB, SLCE slice markers, NAME, ...).
The chunk internals beyond the creator/name strings and the slice count are
proprietary, so they are reported as regions, not decoded. Byte-level facts only.
"""

from acidcat.core.primitives.notes import coverage

from acidcat.core.walk.base import _f, _bu32, _dtext, _open, _size

_MAX = 4 * 1024 * 1024


def _count_slices(data, start, end, depth=0):
    """Count SLCE slice markers, descending into nested 'CAT ' groups (the
    slice list is a sub-CAT, so a flat top-level walk misses them)."""
    n = 0
    pos = start
    guard = 0
    while pos + 8 <= end and guard < 100000 and depth < 8:
        guard += 1
        cid = data[pos:pos + 4]
        clen = _bu32(data, pos + 4)
        cbody = pos + 8
        if cbody + clen > end:
            break
        if cid == b"SLCE":
            n += 1
        elif cid == b"CAT ":                      # nested group: 4-byte form, then chunks
            n += _count_slices(data, cbody + 4, cbody + clen, depth + 1)
        pos = cbody + clen + (clen & 1)
    return n


def inspect_rx2(filepath):
    size = _size(filepath)
    with _open(filepath) as f:
        data = f.read(min(size, _MAX))
    warns = []
    if data[:4] != b"CAT ":
        warns.append("missing CAT container magic")
    form = data[8:12].decode("latin-1", "replace") if len(data) >= 12 else "?"
    cat_size = _bu32(data, 4) if len(data) >= 8 else 0

    chunks = [{"id": "CAT ", "offset": 0, "size": size,
               "summary": f"ReCycle {form} container",
               "fields": [_f(0x00, 4, "container", "CAT "),
                          _f(0x04, 4, "size", f"{cat_size:,}", "bytes in the group",
                             enc=">I", raw=cat_size),
                          _f(0x08, 4, "form", form)],
               "warnings": [], "payload_base": 0}]

    pos = 12                       # after CAT + size + the REX2 form id
    creator = name = None
    guard = 0
    while pos + 8 <= len(data) and guard < 100000:
        guard += 1
        cid = data[pos:pos + 4]
        clen = _bu32(data, pos + 4)
        cbody = pos + 8
        if cbody + clen > size:
            # genuinely past the end of the FILE
            warns.append(f"{cid.decode('latin-1', 'replace')} chunk runs past EOF")
            chunks.append({"id": cid.decode("latin-1", "replace"), "offset": pos,
                           "size": max(0, size - cbody), "summary": "truncated",
                           "fields": [], "warnings": ["size exceeds file"],
                           "payload_base": cbody})
            break
        if cbody + clen > len(data):
            # within the file but past our read window. RX2 embeds the whole
            # sample in SDAT, so any loop over ~4 MB lands here -- and comparing
            # against the truncated buffer reported a perfectly good file as
            # corrupt. Report the chunk at its real size and say we stopped.
            chunks.append({"id": cid.decode("latin-1", "replace"), "offset": pos,
                           "size": clen,   # payload only, as every sibling reports
                           "summary": (f"{clen:,} bytes, beyond the "
                                       f"{_MAX // (1024 * 1024)} MB read window"),
                           "fields": [], "warnings": [], "payload_base": cbody})
            warns.append(coverage(f"stopped at the {_MAX // (1024 * 1024)} MB read window; "
                         f"chunks after {cid.decode('latin-1', 'replace')} "
                         f"were not walked"))
            break
        cid_s = cid.decode("latin-1", "replace")
        cfields = []
        summary = ""
        if cid == b"CREI":
            creator = _dtext(data[cbody:cbody + clen]).strip("\x00 ").strip()
            if creator:
                # 0, not cbody: a field offset is RELATIVE to its chunk's
                # payload_base, which is already cbody. Passing the absolute
                # offset added it twice, so the creator string was reported at
                # 2*cbody -- outside the chunk that declares it. The same
                # defect the mod/au/voc/krz walkers carried, found the same way.
                cfields.append(_f(0, clen, "creator", creator[:80]))
                summary = creator[:60]
        elif cid == b"NAME":
            name = _dtext(data[cbody:cbody + clen]).strip("\x00 ").strip()
            if name:
                cfields.append(_f(0, clen, "name", name[:80]))
                summary = name[:60]
        chunks.append({"id": cid_s, "offset": pos, "size": clen,
                       "summary": summary, "fields": cfields,
                       "warnings": [], "payload_base": cbody})
        pos = cbody + clen + (clen & 1)          # IFF chunks pad to even

    slices = _count_slices(data, 12, len(data))  # SLCE live in a nested CAT group
    top = chunks[0]
    if slices:
        top["fields"].append(_f(None, 0, "slices", slices))
        top["summary"] += f", {slices} slices"
    if creator:
        top["summary"] += f", by {creator[:40]}"
    return chunks, warns
