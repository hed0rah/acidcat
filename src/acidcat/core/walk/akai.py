"""Akai sampler program walker.

.akp is the Akai S5000/S6000 program: a RIFF file (form type APRG) of IFF
chunks -- prg (program header), out, tune, lfo, mods, then one kgrp per
keygroup. Each kgrp is itself nested IFF: kloc (key range), env x3, filt, and up
to four zone chunks, each naming the sample it plays. This surfaces the program
layout and the sampled zones (referenced sample names); the samples live in
sibling .wav files, so they are references, not carveable regions.
"""

import os
import struct

from acidcat.core.walk.base import Unsupported as _Unsupported
from acidcat.core.walk.base import _f

_KGRP_CAP = 128


def _iff(buf, start=0):
    """Yield (tag, body, abs_body_offset) over little-endian IFF chunks."""
    pos = start
    while pos + 8 <= len(buf):
        tag = buf[pos:pos + 4]
        size = struct.unpack_from("<I", buf, pos + 4)[0]
        if pos + 8 + size > len(buf):
            break
        yield tag, buf[pos + 8:pos + 8 + size], pos + 8
        pos += 8 + size + (size & 1)


def _zone_sample(zbody):
    """Sample name a zone references, or '' for an empty zone. Layout: [0] flag,
    [1] name length, [2:2+n] name."""
    if len(zbody) < 2:
        return ""
    n = zbody[1]
    if n == 0 or 2 + n > len(zbody):
        return ""
    return zbody[2:2 + n].decode("latin-1", "replace").strip()


def inspect_akp(filepath):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(size, 64 * 1024 * 1024))
    if data[:4] != b"RIFF" or data[8:12] != b"APRG":
        raise _Unsupported("not an Akai program (RIFF/APRG)")
    warns = []
    prog_name = os.path.splitext(os.path.basename(filepath))[0]

    prg = None
    keygroups = []                     # (low, high, [samples], body_offset, body_len)
    for tag, body, boff in _iff(data, 12):
        if tag == b"prg ":
            prg = body
        elif tag == b"kgrp":
            low = high = None
            samples = []
            for sub, sbody, _ in _iff(body):
                if sub == b"kloc" and len(sbody) >= 6:
                    low, high = sbody[4], sbody[5]
                elif sub == b"zone":
                    nm = _zone_sample(sbody)
                    if nm:
                        samples.append(nm)
            keygroups.append((low, high, samples, boff, len(body)))

    seen, all_samples = set(), []
    for _, _, samples, _, _ in keygroups:
        for s in samples:
            if s not in seen:
                seen.add(s)
                all_samples.append(s)

    fields = [_f(None, 0, "program_name", prog_name),
              _f(None, 0, "program_type", "Keygroup (S5000/S6000)"),
              _f(None, 0, "keygroups", len(keygroups))]
    if prg and len(prg) >= 2:
        fields.append(_f(None, 0, "midi_program", prg[1]))
    fields.append(_f(None, 0, "referenced_samples", len(all_samples)))
    if prg and len(prg) >= 3 and prg[2] != len(keygroups):
        warns.append(f"prg declares {prg[2]} keygroups but {len(keygroups)} kgrp "
                     "chunk(s) are present")

    chunks = [{"id": "APRG", "offset": 0, "size": size, "payload_base": 0,
               "summary": f"Akai program '{prog_name}': {len(keygroups)} keygroup(s), "
                          f"{len(all_samples)} sample(s)",
               "fields": fields, "warnings": warns}]

    for i, (low, high, samples, boff, blen) in enumerate(keygroups[:_KGRP_CAP]):
        kf = []
        if low is not None:
            kf.append(_f(None, 0, "key_range", f"{low}-{high}", "MIDI notes"))
        for j, s in enumerate(samples):
            kf.append(_f(None, 0, f"zone[{j}]", s))
        summ = (f"keygroup {i}: notes {low}-{high}, {len(samples)} zone(s)"
                if low is not None else f"keygroup {i}")
        chunks.append({"id": f"kgrp[{i}]", "offset": boff, "size": blen,
                       "summary": summ, "fields": kf, "warnings": [],
                       "payload_base": boff})
    if len(keygroups) > _KGRP_CAP:
        chunks.append({"id": "kgrp", "offset": 0, "size": 0,
                       "summary": f"... {len(keygroups) - _KGRP_CAP} more keygroup(s)",
                       "fields": [], "warnings": [], "payload_base": 0})
    return chunks, warns
