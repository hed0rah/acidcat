"""Akai MPC walker family.

Modern MPC (MPC2/MPC3 software, Force) ships a small cluster of formats:

  .mpcpattern  bare JSON sequence -- the MPC "MIDI": {"pattern": {"length",
               "events": [...]}}. Two event schemas: the flat MPC2 event
               (numbered keys) and the richer MPC3 note object.
  .xpm         XML keygroup/drum program (<MPCVObject>), each pad/keygroup
               referencing an external sample by name.
  .xpn         a ZIP expansion package (Expansion.xml manifest + .xpm programs
               + .wav samples + cover art).

Each is inspect-only: this maps structure and surfaces references, it does not
render a sequence or resolve sample paths on disk.
"""

import json
import os
import re
import zipfile
from collections import Counter

from acidcat.core.walk.base import Unsupported as _Unsupported
from acidcat.core.walk.base import _f

_INT64_MAX = 2 ** 63 - 1                          # MPC's "unbounded length" sentinel
_NOTE_PREVIEW = 16
_XPM_SAMPLE_CAP = 128
_XPN_ENTRY_CAP = 48


def _num(x):
    return round(x, 3) if isinstance(x, float) else x


def _data_offset(z, zi):
    """Absolute file offset of a zip entry's data (past the local file header):
    the entry's real on-disk bytes, so a STORED program carves to the literal
    .xpm (a DEFLATED one carves to its raw deflate stream)."""
    z.fp.seek(zi.header_offset)
    hdr = z.fp.read(30)
    n = int.from_bytes(hdr[26:28], "little")
    m = int.from_bytes(hdr[28:30], "little")
    return zi.header_offset + 30 + n + m


# ---- .mpcpattern (JSON sequence) -----------------------------------------

def _note_of(e):
    """(pitch, velocity, length, prob, ratchet) for a note event across both
    schemas, or None for a non-note event."""
    n = e.get("note")
    if isinstance(n, dict):                       # MPC3: nested note object
        return (n.get("note"), n.get("velocity"), n.get("length"),
                n.get("probability"), n.get("ratchet"))
    if e.get("type") == 2050:                     # MPC2: flat note event
        return (e.get("1"), e.get("2"), e.get("len"), e.get("prob"), e.get("ratchet"))
    return None


def inspect_mpcpattern(filepath):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(size, 64 * 1024 * 1024))
    try:
        obj = json.loads(data.decode("utf-8", "replace"))
    except (ValueError, RecursionError) as e:
        raise _Unsupported("not a valid MPC pattern (JSON did not parse: "
                           f"{e.__class__.__name__})")
    pat = obj.get("pattern") if isinstance(obj, dict) else None
    if not isinstance(pat, dict) or "events" not in pat:
        raise _Unsupported("not an MPC pattern (no 'pattern.events')")
    events = [e for e in (pat.get("events") or []) if isinstance(e, dict)]
    length = pat.get("length")
    types = Counter(e.get("type") for e in events)
    notes = [n for e in events if (n := _note_of(e))]
    schema = "MPC3" if any(isinstance(e.get("note"), dict) for e in events) else "MPC2"

    fields = [_f(None, 0, "schema", schema)]
    if length is not None:
        note = ("unbounded (INT64_MAX sentinel)" if length == _INT64_MAX
                else "ticks" if isinstance(length, int) else "")
        fields.append(_f(None, 0, "length", length, note))
    fields.append(_f(None, 0, "events", len(events)))
    fields.append(_f(None, 0, "notes", len(notes)))
    fields.append(_f(None, 0, "event_types",
                     ", ".join(f"{t}:{c}" for t, c in types.most_common()),
                     "MPC event type -> count"))
    chunks = [{"id": "pattern", "offset": 0, "size": size, "payload_base": 0,
               "summary": f"MPC {schema} pattern, {len(events)} events "
                          f"({len(notes)} notes)",
               "fields": fields, "warnings": []}]

    if notes:
        nf = []
        for i, (pitch, vel, ln, prob, rat) in enumerate(notes[:_NOTE_PREVIEW]):
            extra = []
            if isinstance(prob, (int, float)) and prob != 100:
                extra.append(f"{_num(prob)}% prob")
            if isinstance(rat, int) and rat > 1:
                extra.append(f"ratchet {rat}")
            nf.append(_f(None, 0, f"note[{i}]",
                         f"pitch {_num(pitch)}, vel {_num(vel)}, len {_num(ln)}",
                         ", ".join(extra)))
        summ = f"{len(notes)} notes"
        if len(notes) > _NOTE_PREVIEW:
            summ += f" (first {_NOTE_PREVIEW} shown)"
        chunks.append({"id": "notes", "offset": 0, "size": 0, "payload_base": 0,
                       "summary": summ, "fields": nf, "warnings": []})
    return chunks, []


# ---- .xpm (XML keygroup / drum program) ----------------------------------

def _xml_text(text, tag):
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
    return m.group(1).strip() if m else ""


def inspect_xpm(filepath):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(size, 64 * 1024 * 1024))
    text = data.decode("utf-8", "replace")
    if "<MPCVObject" not in text[:512]:
        raise _Unsupported("not an MPC program (no <MPCVObject>)")
    prog = re.search(r'<Program\s+type="([^"]*)"', text)
    ptype = prog.group(1) if prog else "?"
    name = _xml_text(text, "ProgramName")
    keygroups = _xml_text(text, "KeygroupNumKeygroups")
    file_ver = _xml_text(text, "File_Version")
    seen, refs = set(), []
    for m in re.finditer(r"<SampleName>([^<]+)</SampleName>", text):
        s = m.group(1).strip()
        if s and s not in seen:
            seen.add(s)
            refs.append(s)

    fields = [_f(None, 0, "program_name", name or "(unnamed)"),
              _f(None, 0, "program_type", ptype)]
    if keygroups:
        fields.append(_f(None, 0, "keygroups", keygroups))
    if file_ver:
        fields.append(_f(None, 0, "file_version", file_ver))
    fields.append(_f(None, 0, "referenced_samples", len(refs)))
    chunks = [{"id": "program", "offset": 0, "size": size, "payload_base": 0,
               "summary": f"MPC {ptype} program '{name or '(unnamed)'}': "
                          f"{len(refs)} sample(s)"
                          + (f", {keygroups} keygroups" if keygroups else ""),
               "fields": fields, "warnings": []}]
    if refs:
        # samples are external files referenced by name -- a referenced-sample
        # list, not carveable regions (the WAVs live beside the .xpm).
        sf = [_f(None, 0, f"[{i}]", r) for i, r in enumerate(refs[:_XPM_SAMPLE_CAP])]
        summ = f"{len(refs)} referenced sample file(s)"
        if len(refs) > _XPM_SAMPLE_CAP:
            summ += f" (first {_XPM_SAMPLE_CAP} shown)"
        chunks.append({"id": "samples", "offset": 0, "size": 0, "payload_base": 0,
                       "summary": summ, "fields": sf, "warnings": []})
    return chunks, []


# ---- .xpn (ZIP expansion package) ----------------------------------------

_XPN_MANIFEST_KEYS = ("title", "manufacturer", "type", "version", "identifier")


def inspect_xpn(filepath):
    size = os.path.getsize(filepath)
    try:
        z = zipfile.ZipFile(filepath)
    except zipfile.BadZipFile:
        return ([{"id": "xpn", "offset": 0, "size": size,
                  "summary": "not a valid zip archive", "fields": [],
                  "warnings": ["not a zip archive"], "payload_base": 0}],
                ["not a zip archive"])

    warns = []
    with z:
        names = set(z.namelist())
        infos = [zi for zi in z.infolist() if not zi.is_dir()]
        man = {}
        if "Expansion.xml" in names:
            try:
                xml = z.read("Expansion.xml").decode("utf-8", "replace")
                for tag in _XPN_MANIFEST_KEYS + ("description", "img"):
                    v = _xml_text(xml, tag)
                    if v:
                        man[tag] = v
            except Exception:
                warns.append("Expansion.xml did not parse")
        else:
            warns.append("no Expansion.xml manifest")

        programs = [zi for zi in infos if zi.filename.lower().endswith(".xpm")]
        samples = [zi for zi in infos
                   if zi.filename.lower().endswith((".wav", ".flac", ".aif", ".aiff"))]
        title = man.get("title") or os.path.basename(filepath)
        fields = [_f(None, 0, "title", title)]
        for k in _XPN_MANIFEST_KEYS[1:]:
            if man.get(k):
                fields.append(_f(None, 0, k, man[k]))
        fields.append(_f(None, 0, "programs", len(programs)))
        fields.append(_f(None, 0, "samples", len(samples)))
        if man.get("description"):
            fields.append(_f(None, 0, "description", man["description"][:120]))
        if man.get("img"):
            fields.append(_f(None, 0, "cover_image", man["img"]))
        maker = f" by {man['manufacturer']}" if man.get("manufacturer") else ""
        chunks = [{"id": "expansion", "offset": 0, "size": size, "payload_base": 0,
                   "summary": f"MPC expansion '{title}'{maker}: {len(programs)} "
                              f"program(s), {len(samples)} sample(s)",
                   "fields": fields, "warnings": []}]

        # each .xpm program is a real on-disk byte region; a STORED entry carves
        # to the literal .xpm, a DEFLATED one to its raw deflate stream.
        for zi in programs[:_XPN_ENTRY_CAP]:
            doff = _data_offset(z, zi)
            stored = zi.compress_type == zipfile.ZIP_STORED
            comp = "stored (carveable .xpm)" if stored else "deflated (raw stream)"
            chunks.append({"id": "program", "offset": doff, "size": zi.compress_size,
                           "summary": f"{zi.filename}  [{'stored' if stored else 'deflated'}]",
                           "fields": [_f(None, 0, "file", zi.filename),
                                      _f(None, 0, "size", f"{zi.file_size:,} bytes",
                                         "uncompressed"),
                                      _f(None, 0, "compression", comp)],
                           "warnings": [], "payload_base": doff})
        if len(programs) > _XPN_ENTRY_CAP:
            chunks.append({"id": "program", "offset": 0, "size": 0,
                           "summary": f"... {len(programs) - _XPN_ENTRY_CAP} more "
                                      "program(s)",
                           "fields": [], "warnings": [], "payload_base": 0})
    return chunks, warns
