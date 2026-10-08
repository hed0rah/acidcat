"""Logic EXS24 instrument walker. The layout is in core/formats/exs.py."""

import struct

from acidcat.core.formats import exs as exsmod
from acidcat.core.infra.findings import defect, info
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import Unsupported, _f, _open, _size

# an instrument is metadata; the samples live in other files
_EXS_READ_CAP = 32 * 1024 * 1024
# chunks walked; the largest of 1,007 real instruments had under 2,000
_EXS_CHUNK_CAP = 65536


def _header_fields(e, data, at, length):
    return [
        _f(-exsmod.HEAD, 4, "signature", f"0x{struct.unpack_from(e + 'I', data, at)[0]:08x}",
           "byte 3 is the chunk type"),
        _f(-exsmod.HEAD + 4, 4, "length", length),
        _f(-exsmod.HEAD + 0x10, 4, "magic", data[at + 0x10:at + 0x14].decode("latin-1")),
    ]


def inspect_exs(filepath):
    size = _size(filepath)
    with _open(filepath) as fh:
        data = fh.read(min(size, _EXS_READ_CAP))
    e = exsmod.endian(data)
    if e is None:
        raise Unsupported("not an EXS24 instrument")
    warns = []
    if size > _EXS_READ_CAP:
        warns.append(hit("read_bytes", _EXS_READ_CAP, size,
                         f"the instrument is {size:,} bytes; the first "
                         f"{_EXS_READ_CAP // (1 << 20)} MB were read"))
    found, end, stop = exsmod.read_chunks(data, e, _EXS_CHUNK_CAP)
    if stop == "chunks":
        warns.append(hit("list_rows", _EXS_CHUNK_CAP, _EXS_CHUNK_CAP + 1,
                         f"the walk stopped there, after {_EXS_CHUNK_CAP:,} chunks"))
    elif stop == "overrun" and len(data) == size:
        warns.append(defect("size.overrun", f"the chunk at {end:#x} claims more "
                                            f"bytes than the file holds"))
    elif stop == "short":
        warns.append(defect("chunk.short", f"{len(data) - end} bytes at {end:#x} "
                                           f"are too few for a chunk header"))
    samples = [c for c in found if c["type"] == 3]
    groups = [c for c in found if c["type"] == 2]
    counts = {t: sum(1 for c in found if c["type"] == t) for t in exsmod.TYPES}
    chunks = []
    for c in found:
        at, length, t = c["at"], c["length"], c["type"]
        base = at + exsmod.HEAD
        body = data[base:base + length]
        kind = exsmod.TYPES.get(t, f"type {t}")
        fields = _header_fields(e, data, at, length)
        if c["name"]:
            fields.append(_f(-exsmod.HEAD + exsmod.NAME_AT, exsmod.NAME_LEN, "name", c["name"]))
        cw = []
        if t == 0:
            _instrument_fields(e, body, fields, counts, cw)
        elif t == 1:
            _zone_fields(e, body, fields, samples, groups, cw)
        elif t == 3:
            _sample_fields(e, body, fields)
        elif t not in exsmod.TYPES:
            cw.append(info("layout.unmeasured", f"chunk type {t} is not decoded"))
        # size is the data; payload_base puts it after the 84-byte header
        chunks.append({"id": kind, "offset": at, "size": length,
                       "summary": f"{kind} {c['name']!r}" if c["name"] else kind,
                       "fields": fields, "warnings": cw, "payload_base": base})
    if chunks:
        chunks[0]["summary"] = (f"EXS24 instrument {found[0]['name']!r}: "
                                f"{counts[1]} zone(s), {counts[3]} sample(s)"
                                + (" (big-endian)" if e == ">" else ""))
    return chunks, warns


def _u(e, fmt, body, off):
    return struct.unpack_from(e + fmt, body, off)[0]


def _instrument_fields(e, body, fields, counts, cw):
    if len(body) < 20:
        cw.append(defect("chunk.short", f"the instrument chunk holds {len(body)} "
                                        f"bytes; its counts need 20"))
        return
    for off, name, t in ((4, "zones", 1), (8, "groups", 2), (12, "samples", 3),
                         (16, "parameter_chunks", 4)):
        said = _u(e, "I", body, off)
        fields.append(_f(off, 4, name, said))
        if said != counts[t]:
            cw.append(defect("count.mismatch", f"the instrument says {said} {name} "
                                               f"and the file holds {counts[t]}"))


def _zone_fields(e, body, fields, samples, groups, cw):
    if len(body) < exsmod.ZONE_MIN:
        cw.append(defect("chunk.short", f"a zone holds {len(body)} bytes; its "
                                        f"fields need {exsmod.ZONE_MIN}"))
        return
    root = body[1]
    fields += [
        _f(1, 1, "root_key", root, f"{exsmod.note_name(root)} (Logic octaves, C3 = 60)"),
        _f(2, 1, "fine_tune", _u(e, "b", body, 2), "cents"),
        _f(3, 1, "pan", _u(e, "b", body, 3)),
        _f(4, 1, "volume", _u(e, "b", body, 4), "dB"),
        _f(6, 1, "key_low", body[6], exsmod.note_name(body[6])),
        _f(7, 1, "key_high", body[7], exsmod.note_name(body[7])),
        _f(9, 1, "velocity_low", body[9]),
        _f(10, 1, "velocity_high", body[10]),
        _f(12, 4, "sample_start", _u(e, "I", body, 12)),
        _f(16, 4, "sample_end", _u(e, "I", body, 16)),
        _f(20, 4, "loop_start", _u(e, "I", body, 20)),
        _f(24, 4, "loop_end", _u(e, "I", body, 24)),
    ]
    g = _u(e, "i", body, 88)
    s = _u(e, "i", body, 92)
    fields.append(_f(88, 4, "group", g, "-1: none" if g == -1 else
                     (repr(groups[g]["name"]) if 0 <= g < len(groups) else "")))
    fields.append(_f(92, 4, "sample", s, "-1: none (an empty zone)" if s == -1 else
                     (repr(samples[s]["name"]) if 0 <= s < len(samples) else "")))
    if g >= len(groups) or g < -1:
        cw.append(defect("reference.unresolved", f"the zone names group {g} and "
                                                 f"the file holds {len(groups)}"))
    if s >= len(samples) or s < -1:
        cw.append(defect("reference.unresolved", f"the zone names sample {s} and "
                                                 f"the file holds {len(samples)}"))


def _sample_fields(e, body, fields):
    if len(body) < exsmod.SAMPLE_MIN:
        fields.append(_f(None, 0, "note", f"{len(body)} bytes; the fields need "
                                          f"{exsmod.SAMPLE_MIN}"))
        return
    ftype = body[28:32].decode("latin-1")
    if e == "<":
        ftype = ftype[::-1] + " (stored byte-reversed)"
    fields += [
        _f(0, 4, "data_offset", _u(e, "I", body, 0), "where the audio starts in the file"),
        _f(4, 4, "frames", _u(e, "I", body, 4)),
        _f(8, 4, "sample_rate", _u(e, "I", body, 8)),
        _f(12, 4, "bits", _u(e, "I", body, 12)),
        _f(16, 4, "channels", _u(e, "I", body, 16)),
        _f(28, 4, "file_type", ftype),
        _f(32, 4, "file_size", _u(e, "I", body, 32)),
    ]
    path = exsmod.cstr(body[80:336])
    if path:
        fields.append(_f(80, 256, "path", path))
    if len(body) >= 592:
        fname = exsmod.cstr(body[336:592])
        if fname:
            fields.append(_f(336, 256, "file_name", fname))
