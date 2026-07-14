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

import gzip
import json
import os
import re
import struct
import zipfile
from collections import Counter

from acidcat.core.walk.base import Unsupported as _Unsupported
from acidcat.core.walk.base import _f

_INT64_MAX = 2 ** 63 - 1                          # MPC's "unbounded length" sentinel
_NOTE_PREVIEW = 16
_XPM_SAMPLE_CAP = 128
_XPN_ENTRY_CAP = 48
_XTD_CAP = 64 * 1024 * 1024                       # decompression-bomb guard
_PGM_PAD_CAP = 128
_SND_HDR = 38                                     # MPC2000 .snd header (RE'd exact)
_MPC1000_MAGIC = b"MPC1000 PGM"


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


# ---- .xtd (gzip ACVS container: MPC3 track / kit) -------------------------

def inspect_xtd(filepath):
    size = os.path.getsize(filepath)
    try:
        with gzip.open(filepath, "rb") as g:
            raw = g.read(_XTD_CAP + 1)
    except (OSError, EOFError) as e:
        raise _Unsupported("not a valid .xtd (gzip did not open: "
                           f"{e.__class__.__name__})")
    if not raw.startswith(b"ACVS"):
        raise _Unsupported("not an ACVS container (.xtd)")
    truncated = len(raw) > _XTD_CAP
    raw = raw[:_XTD_CAP]
    # header is newline-delimited lines (ACVS, app version, data type, format,
    # platform) followed by the JSON body at the first brace.
    brace = raw.find(b"{")
    hlines = [ln for ln in raw[:brace if brace >= 0 else len(raw)]
              .decode("utf-8", "replace").splitlines() if ln.strip()]
    hmap = dict(zip(["magic", "app_version", "data_type", "format", "platform"],
                    hlines))
    warns = []
    kit = {}
    if brace >= 0 and not truncated:
        try:
            obj = json.loads(raw[brace:])
            kit = obj.get("data", {}) if isinstance(obj, dict) else {}
        except (ValueError, RecursionError):
            warns.append("ACVS JSON payload did not parse")
    elif truncated:
        warns.append(f"decompressed payload exceeds {_XTD_CAP // (1 << 20)} MB cap; "
                     "metadata not parsed")

    samples = kit.get("samples") if isinstance(kit.get("samples"), list) else []
    prog = kit.get("program") if isinstance(kit.get("program"), dict) else {}
    name = kit.get("name", "")
    dtype = hmap.get("data_type", "ACVS data")
    fields = [_f(None, 0, "container", "ACVS"),
              _f(None, 0, "data_type", dtype),
              _f(None, 0, "app_version", hmap.get("app_version", "?")),
              _f(None, 0, "platform", hmap.get("platform", "?"))]
    if name:
        fields.insert(1, _f(None, 0, "name", name))
    if "version" in kit:
        fields.append(_f(None, 0, "data_version", kit["version"]))
    if prog.get("name"):
        fields.append(_f(None, 0, "program", prog["name"], prog.get("type", "")))
    fields.append(_f(None, 0, "samples", len(samples)))
    chunks = [{"id": "xtd", "offset": 0, "size": size, "payload_base": 0,
               "summary": f"MPC {dtype}" + (f" '{name}'" if name else "")
                          + f", {len(samples)} sample(s)  "
                          f"(gzip, {hmap.get('app_version', '?')})",
               "fields": fields, "warnings": warns}]
    if samples:
        sf = [_f(None, 0, f"[{i}]",
                 (s.get("name") if isinstance(s, dict) else None) or "(unnamed)",
                 (s.get("path") if isinstance(s, dict) else "") or "")
              for i, s in enumerate(samples[:_XPM_SAMPLE_CAP])]
        summ = f"{len(samples)} referenced sample(s)"
        if len(samples) > _XPM_SAMPLE_CAP:
            summ += f" (first {_XPM_SAMPLE_CAP} shown)"
        chunks.append({"id": "samples", "offset": 0, "size": 0, "payload_base": 0,
                       "summary": summ, "fields": sf, "warnings": []})
    return chunks, warns


# ---- .snd (MPC2000 sound: 16-bit PCM container) --------------------------

def inspect_snd(filepath):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        head = f.read(48)
    if len(head) < 42 or head[0] != 1:
        raise _Unsupported("not an MPC2000 .snd sound")
    name = head[2:18].split(b"\x00")[0].decode("latin-1", "replace").rstrip()
    level = head[19]
    stereo = head[21] == 1
    channels = 2 if stereo else 1
    warns = []
    # the frame count sits at 0x1e in the classic 42-byte header, but some
    # exporters write a compact 38-byte header (count at 0x1a). resolve both by
    # size-fit: pick the (header, count) whose PCM span lands exactly on EOF.
    resolved = None
    for hdr, foff in ((42, 0x1e), (38, 0x1a), (38, 0x1e), (42, 0x1a)):
        frames = struct.unpack_from("<I", head, foff)[0]
        if frames and hdr + frames * 2 * channels == size:
            resolved = (hdr, frames)
            break
    if resolved is None:
        hdr, frames = 42, struct.unpack_from("<I", head, 0x1e)[0]
        warns.append(f"{frames:,} frames x {channels}ch do not fit the "
                     f"{size:,}-byte file at a 38- or 42-byte header")
    else:
        hdr, frames = resolved
    pcm_bytes = frames * 2 * channels
    chan = "stereo (non-interleaved)" if stereo else "mono"
    dur = frames / 44100.0
    fields = [
        _f(0x02, 16, "name", name),
        _f(0x13, 1, "level", level),
        _f(0x15, 1, "channels", channels, chan),
        _f(0x1e if hdr == 42 else 0x1a, 4, "frames", f"{frames:,}",
           "sample frames per channel", enc="<I", raw=frames),
        _f(None, 0, "header_bytes", hdr),
        _f(None, 0, "duration", f"{dur:.3f}", "s at 44100 Hz (MPC2000 native)"),
    ]
    if hdr == 42 and head[0x26]:                      # classic header: loop fields
        loop_len = struct.unpack_from("<I", head, 0x22)[0]
        fields.append(_f(0x22, 4, "loop_length", f"{loop_len:,}", "frames"))
    chunks = [{"id": "SND", "offset": 0, "size": size, "payload_base": 0,
               "summary": f"MPC2000 sound '{name}': {frames:,} frames {chan}, "
                          f"{dur:.2f}s",
               "fields": fields, "warnings": warns}]
    if hdr < size:
        # the raw 16-bit PCM is a carveable region (stereo is non-interleaved)
        chan_word = "stereo" if stereo else "mono"
        note = " (non-interleaved)" if stereo else ""
        chunks.append({"id": "pcm", "offset": hdr,
                       "size": min(pcm_bytes, size - hdr),
                       "summary": f"{frames:,} x 16-bit signed LE {chan_word} "
                                  f"PCM{note}",
                       "fields": [], "warnings": [], "payload_base": hdr})
    return chunks, warns


# ---- .pgm (MPC program: MPC1000 magic form, or older MPC2000 table form) --

def _pgm_name(raw):
    return raw.split(b"\x00")[0].decode("latin-1", "replace").rstrip()


def inspect_pgm(filepath):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(size, 8 * 1024 * 1024))
    prog = os.path.splitext(os.path.basename(filepath))[0]
    if data[4:4 + len(_MPC1000_MAGIC)] == _MPC1000_MAGIC:
        return _inspect_pgm_mpc1000(data, size, prog)
    if len(data) >= 19 and data[18] == 0 and 0x20 <= data[2] < 0x7f:
        return _inspect_pgm_mpc2000(data, size, prog)
    raise _Unsupported("not an MPC program (.pgm)")


def _inspect_pgm_mpc1000(data, size, prog):
    # 24-byte header (file size, magic), then 64 pads x 164 bytes; each pad has
    # four 24-byte layers whose first field is a NUL-terminated sample name.
    version = data[16:20].decode("latin-1", "replace").strip()
    pad0, padsz, npads, laysz, nlay = 24, 164, 64, 24, 4
    pads, seen, all_samples = [], set(), []
    for pi in range(npads):
        base = pad0 + pi * padsz
        if base + padsz > len(data):
            break
        layers = []
        for li in range(nlay):
            loff = base + li * laysz
            nm = _pgm_name(data[loff:loff + 16])
            if not nm:
                continue
            # layer params (offsets verified vs a factory-default PGM)
            level = data[loff + 0x11]
            vlo, vhi = data[loff + 0x12], data[loff + 0x13]
            tune = struct.unpack_from("<h", data, loff + 0x14)[0] / 100.0
            play = "one-shot" if data[loff + 0x16] == 0 else "note-on"
            note = f"level {level}, vel {vlo}-{vhi}, {tune:+g} st, {play}"
            layers.append((loff, nm, note))
            if nm not in seen:
                seen.add(nm)
                all_samples.append(nm)
        if layers:
            pads.append((pi, base, layers))
    fields = [_f(None, 0, "program_name", prog),
              _f(None, 0, "program_type", "MPC1000/2500"),
              _f(0x04, 16, "format", "MPC1000 PGM " + version),
              _f(None, 0, "pads_used", f"{len(pads)}/{npads}"),
              _f(None, 0, "referenced_samples", len(all_samples))]
    chunks = [{"id": "PGM", "offset": 0, "size": size, "payload_base": 0,
               "summary": f"MPC1000 program '{prog}': {len(pads)} pad(s), "
                          f"{len(all_samples)} sample(s)",
               "fields": fields, "warnings": []}]
    for pi, base, layers in pads[:_PGM_PAD_CAP]:
        pf = [_f(loff, laysz, f"layer[{j}]", nm, note)
              for j, (loff, nm, note) in enumerate(layers)]
        chunks.append({"id": f"pad[{pi}]", "offset": base, "size": padsz,
                       "summary": f"pad {pi}: {len(layers)} layer(s)",
                       "fields": pf, "warnings": [], "payload_base": base})
    return chunks, []


def _inspect_pgm_mpc2000(data, size, prog):
    # a table of 17-byte records (16-char name + 1 byte) from offset 2
    entries, off = [], 2
    while off + 17 <= len(data) and len(entries) < 256:
        if data[off] == 0:
            break
        entries.append((off, _pgm_name(data[off:off + 16])))
        off += 17
    distinct = len({n for _, n in entries if n})
    fields = [_f(None, 0, "program_name", prog),
              _f(None, 0, "program_type", "MPC2000/2000XL/3000"),
              _f(None, 0, "referenced_samples", distinct)]
    chunks = [{"id": "pgm", "offset": 0, "size": size, "payload_base": 0,
               "summary": f"MPC2000 program '{prog}': {distinct} sample(s)",
               "fields": fields, "warnings": []}]
    if entries:
        sf = [_f(o, 16, f"[{j}]", n)
              for j, (o, n) in enumerate(entries[:_PGM_PAD_CAP])]
        chunks.append({"id": "samples", "offset": 2, "size": len(entries) * 17,
                       "summary": f"{len(entries)} sample-name slot(s)",
                       "fields": sf, "warnings": [], "payload_base": 2})
    return chunks, []
