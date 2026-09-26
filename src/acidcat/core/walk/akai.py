"""Akai sampler program walkers, two generations apart.

.akp is the Akai S5000/S6000 program: a RIFF file (form type APRG) of IFF
chunks -- prg (program header), out, tune, lfo, mods, then one kgrp per
keygroup. Each kgrp is itself nested IFF: kloc (key range), env x3, filt, and up
to four zone chunks, each naming the sample it plays. This surfaces the program
layout and the sampled zones (referenced sample names); the samples live in
sibling .wav files, so they are references, not carveable regions.

.s3p is the S1000/S3000 program from a decade earlier, and is not a file layout
at all: it is a recording of the MIDI System Exclusive dump the sampler sends,
one message per 150-byte block, each with a length written in front of it. See
core/formats/akai.py for the layout and where it was verified.
"""

import os
import struct

from acidcat.core.formats import akai as akaimod
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import Unsupported as _Unsupported, _open, _size, _name
from acidcat.core.walk.base import _f
from acidcat.core.infra.findings import coded, defect

_KGRP_CAP = 128
# One .s3p message per keygroup, and the program block itself caps keygroups at
# 99. The bound is for a file that claims more than the sampler can hold.
_S3P_KEYGROUP_CAP = 128
# The largest .s3p measured is under 60 KB. The cap stops a crafted length
# from making us hold a huge file, and announces itself when it bites.
_S3P_READ_CAP = 16 * 1024 * 1024


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
    size = _size(filepath)
    with _open(filepath) as f:
        data = f.read(min(size, 64 * 1024 * 1024))
    if data[:4] != b"RIFF" or data[8:12] != b"APRG":
        raise _Unsupported("not an Akai program (RIFF/APRG)")
    warns = []
    prog_name = os.path.splitext(_name(filepath))[0]

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
        warns.append(defect("count.mismatch",
                            f"prg declares {prg[2]} keygroups but {len(keygroups)} kgrp "
                            "chunk(s) are present"))

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


def inspect_s3p(filepath):
    """Akai S1000/S3000 program: a transcript of a SysEx dump."""
    size = _size(filepath)
    with _open(filepath) as f:
        data = f.read(min(size, _S3P_READ_CAP))
    if data[:len(akaimod.MAGIC)] != akaimod.MAGIC:
        raise _Unsupported("not an Akai S1000/S3000 program (no PSYSSS30)")

    warns = []
    if size > _S3P_READ_CAP:
        warns.append(hit("read_bytes", _S3P_READ_CAP, size,
                         "file is %d bytes; parsed the first %d"
                         % (size, len(data))))

    h = akaimod.parse_program(data, len(data))
    if not h["ok"]:
        return [{"id": "header", "offset": 0, "size": min(size, akaimod.HEADER),
                 "summary": "not a resolvable Akai program: %s" % h["why"],
                 "fields": [], "warnings": [], "payload_base": 0}], \
            [coded(h["code"], "program did not resolve: %s" % h["why"])]

    prog = h["program"]
    fields = [_f(0x00, len(akaimod.MAGIC), "magic", "PSYSSS30"),
              _f(0x08, 4, "keygroups", h["declared_keygroups"],
                 "messages that follow the program block")]
    chunks = [{"id": "header", "offset": 0, "size": akaimod.HEADER,
               "summary": "Akai S1000/S3000 program, %d keygroup(s)"
                          % h["declared_keygroups"],
               "fields": fields, "warnings": [], "payload_base": 0,
               "payload_len": akaimod.HEADER, "extent_len": akaimod.HEADER}]

    # The program block's own count and the number of messages are written by
    # different parts of the sampler. They agreed in all 1,670 programs
    # measured, which is what makes the block layout trustworthy -- so when
    # they disagree, that is worth saying.
    declared = prog[42] if len(prog) > 42 else None
    if declared is not None and declared != len(h["keygroups"]):
        warns.append(defect("count.mismatch",
                            "the program block declares %d keygroups and %d keygroup "
                            "message(s) follow" % (declared, len(h["keygroups"]))))
    if h["corrupt"]:
        warns.append(defect("value.invalid",
                            "%d message(s) carry a payload byte with bit 7 set, which "
                            "no System Exclusive message can; those blocks were "
                            "masked to read them and their contents are not "
                            "trustworthy" % h["corrupt"]))
    if h["declared_keygroups"] != len(h["keygroups"]):
        warns.append(defect("count.mismatch",
                            "the file header declares %d keygroups and %d keygroup "
                            "message(s) follow"
                            % (h["declared_keygroups"], len(h["keygroups"]))))

    pf = [_f(None, 0, name, value, note)
          for name, value, note in akaimod.program_fields(prog)]
    # Each chunk starts at its own length word, so the file tiles: the
    # twelve-byte header, then 4 + length for every message.
    at, plen = h["program_at"], h["program_len"]
    chunks.append({
        "id": "program", "offset": at - 4, "size": plen + 4,
        "summary": "program %s" % (
            akaimod.akai_name(prog[3:3 + akaimod.NAME_LEN]) or "(unnamed)"),
        "fields": pf, "warnings": [], "payload_base": at,
        "payload_len": plen, "extent_len": plen})

    for i, (off, length, body) in enumerate(h["keygroups"][:_S3P_KEYGROUP_CAP]):
        zones = [z for z in akaimod.keygroup_zones(body) if z["sample"]]
        kf = [
            _f(None, 0, "key_range", "%d-%d" % (body[3], body[4]),
               "%s to %s" % (akaimod._note_name(body[3]),
                             akaimod._note_name(body[4]))),
            _f(None, 0, "filter", body[7], "cutoff, 0-99"),
            _f(None, 0, "amp_envelope",
               "A%d D%d S%d R%d" % (body[12], body[13], body[14], body[15]),
               "0-99 each"),
        ]
        for z in zones:
            kf.append(_f(None, 0, "zone[%d]" % z["zone"], z["sample"],
                         "velocity %d-%d, pan %+d"
                         % (z["low_velocity"], z["high_velocity"], z["pan"])))
        chunks.append({
            "id": "keygroup[%d]" % i, "offset": off - 4, "size": length + 4,
            "summary": "notes %d-%d, %d zone(s)"
                       % (body[3], body[4], len(zones)),
            "fields": kf, "warnings": [], "payload_base": off,
            "payload_len": length, "extent_len": length})
    if len(h["keygroups"]) > _S3P_KEYGROUP_CAP:
        note = hit("list_rows", _S3P_KEYGROUP_CAP, len(h['keygroups']),
                   "listing the first %d of %d keygroups"
                   % (_S3P_KEYGROUP_CAP, len(h["keygroups"])))
        chunks[0]["warnings"].append(note)
        warns.append(note)

    if h["consumed"] < size:
        warns.append(defect("bytes.stray",
                            "%d bytes after the last message are not part of any "
                            "SysEx frame" % (size - h["consumed"])))
    return chunks, [w for w in warns if w]
