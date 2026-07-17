"""E-MU sampler bank walker (Emulator 4 / EOS and Emulator X / Proteus X).

Two generations of E-MU's IFF-like ``FORM`` bank format share this walker:

* **E4B0** - the Emulator 4 / E4XT / E5000 / E6400 (EOS 4.x) hardware bank
  (``.E4B``). Table of contents ``TOC1`` (32-byte ASCII-named entries), ``E4P1``
  presets, ``E3S1`` 16-bit PCM samples, ``EMSt`` master setup. Presets carry a
  flat, packed voice table; the FORM size uses the E-MU quirk (filesize - 12).

* **E5B0** - the Emulator X / Proteus X software sampler (~2004) bank (``.exb``)
  and sample-library (``.ebl``). Table of contents ``TOC2`` (78-byte entries,
  UTF-16LE names), ``E5P1`` presets (a nested container of ``Phdr``/``E5IC``/
  ``E5CL``/``LIST`` sub-chunks), ``E5S1`` samples, ``E5SL`` 6-byte sample links.
  Names are UTF-16LE and the FORM size is standard IFF (filesize - 8). Banks
  hold presets + links; the sample PCM lives in sibling ``.ebl`` files.

Both are dissected leniently: the chunk chain is followed by declared big-endian
sizes, a desync or overrun degrades to a warning rather than raising, and the
TOC offsets are cross-checked against the chain (a mismatch is the usual
signature of a corrupt bank). Sample indices are resolved to names where they
live in the same file.

Layout reverse-engineered from real specimens (E5B0 against the E-MU Classic /
Formula / Producer / Proteus libraries) and, for E4B0, the hardware-verified
mpc2emu docs (GPL-2.0); this is an independent MIT-licensed dissector, no code
copied. Voice/zone internals (envelopes, filters, mod routing) are not decoded
yet. Older E-MU formats (Emulator III banks, ESI) are not handled.
"""

import os
import struct

from acidcat.core.walk.base import Unsupported as _Unsupported
from acidcat.core.walk.base import _bu16, _bu32, _f

_FORM = b"FORM"
_E4B0 = b"E4B0"
_E5B0 = b"E5B0"

# E4B0 tags
_TOC1 = b"TOC1"
_E4MA = b"E4Ma"
_E4P1 = b"E4P1"
_E3S1 = b"E3S1"
_EMST = b"EMSt"
# E5B0 tags
_TOC2 = b"TOC2"
_E5P1 = b"E5P1"
_E5S1 = b"E5S1"
_E5SL = b"E5SL"
_PHDR = b"Phdr"

_SAMP_HDR = 94                  # E3S1 fixed header before the PCM
_PRES_HDR = 82                  # E4P1 fixed header before the voice blocks
_VOICE_FIXED = 284              # fixed part of an E4B voice block, then n_zones*22
_ZONE_ENTRY = 22
_TOC1_ENTRY = 32
_TOC2_ENTRY = 78
_E5S1_RATE_OFF = 0x6a           # sample rate (LE u32) in an E5S1 header

_READ_CAP = 128 * 1024 * 1024   # EOS hardware max bank/image size
_MAX_CHUNKS = 16384
_TOC_LIST_CAP = 512             # TOC entries surfaced as fields
_VOICE_CAP = 512
_ZONE_CAP = 512
_REF_CAP = 128


def _is_tag(b):
    """A valid IFF chunk tag: 4 printable-ASCII bytes."""
    return len(b) == 4 and all(0x20 <= c < 0x7f for c in b)


def _name(raw):
    """A space/null-padded ASCII name (E4B), trimmed."""
    return raw.decode("ascii", "replace").rstrip("\x00 ").strip()


def _wname(raw):
    """A UTF-16LE, null-terminated name (E5B), trimmed."""
    return raw.decode("utf-16-le", "replace").split("\x00")[0].strip()


def _walk_records(data):
    """Follow the chunk chain from the FORM body by declared big-endian sizes.

    Returns (records, warns, desync_pos): records is a list of (tag, offset,
    size); desync_pos is the offset where a non-tag was hit (or None). A truncated
    chunk (size past EOF) is still recorded and warned; a desync is left for the
    caller to phrase (trailing data vs corruption) via ``_desync_note``.
    """
    records, warns = [], []
    desync_pos = None
    pos = 12
    while pos + 8 <= len(data) and len(records) < _MAX_CHUNKS:
        tag = data[pos:pos + 4]
        size = _bu32(data, pos + 4)
        if not _is_tag(tag):
            desync_pos = pos
            break
        records.append((tag, pos, size))
        if pos + 8 + size > len(data):
            warns.append(
                f"{tag.decode('ascii', 'replace')} at {pos:#x} declares size "
                f"{size} but only {len(data) - pos - 8} bytes remain; truncated")
            break
        pos += 8 + size
        # word-alignment is inconsistent across E-MU writers: EOS pads odd chunks
        # to a word, the Proteus-X module banks do not. Only consume the pad byte
        # when skipping it does not already land on a real chunk tag.
        if size & 1 and not _is_tag(data[pos:pos + 4]):
            pos += 1
    return records, warns, desync_pos


def _desync_note(data, desync_pos, content_records, toc_count):
    """Phrase the end-of-chain condition. If every TOC-declared chunk was read,
    a desync is just trailing data (EOS CD-ROM banks pad the tail and carry no
    EMSt); only an early desync -- fewer chunks than the TOC promises -- means a
    chunk size field is actually wrong."""
    if desync_pos is None:
        return None
    if toc_count and content_records >= toc_count:
        return (f"{len(data) - desync_pos:,} bytes of trailing data after the "
                f"last chunk (no chunk tag; e.g. CD-streamed sample padding)")
    return (f"chunk chain desynced at offset {desync_pos:#x}: expected a chunk "
            f"tag (a preceding chunk's size field is likely wrong)")


def _toc_cross_check(toc_offsets, records):
    """TOC-declared offsets that do not land on a walked chunk (corruption)."""
    chain = {o for _, o, _ in records}
    return [o for o in toc_offsets if o not in chain]


def _form_chunk(size, form_size, form_type, note, size_note, summary):
    return {
        "id": "FORM", "offset": 0, "size": size, "payload_base": 0,
        "summary": summary,
        "fields": [
            _f(0, 4, "magic", "FORM"),
            _f(4, 4, "form_size", f"{form_size:,}", size_note, enc=">I",
               raw=form_size),
            _f(8, 4, "form_type", form_type.decode("ascii"), note),
        ],
        "warnings": [],
    }


# ── E4B0 (Emulator 4 / EOS) ──────────────────────────────────────────

def _e4_sample_index_map(data, records):
    """Map each E3S1 sample's 1-based index to its name. The chunk chain is
    authoritative; the TOC1 seeds any gaps so a desynced bank still resolves."""
    idx_to_name = {}
    for tag, off, size in records:
        if tag == _TOC1:
            body = data[off + 8:off + 8 + size]
            for i in range(min(size // _TOC1_ENTRY, _TOC_LIST_CAP)):
                e = body[i * _TOC1_ENTRY:(i + 1) * _TOC1_ENTRY]
                if len(e) >= 30 and e[0:4] == _E3S1:
                    idx_to_name.setdefault(_bu16(e, 12), _name(e[14:30]))
    for tag, off, size in records:
        if tag == _E3S1:
            body = data[off + 8:off + 8 + min(size, _SAMP_HDR)]
            if len(body) >= 18:
                idx_to_name[_bu16(body, 0)] = _name(body[2:18])
    return idx_to_name


def _e4_sample_fields(body, size):
    fields, loop = [], False
    if len(body) < 2:
        return fields, "", False
    idx = _bu16(body, 0)
    name = _name(body[2:18]) if len(body) >= 18 else ""
    fields.append(_f(0, 2, "sample_index", idx, "1-based", enc=">H", raw=idx))
    if len(body) >= 18:
        fields.append(_f(2, 16, "name", name))
    if len(body) >= 62:
        sr = struct.unpack_from("<I", body, 54)[0]
        opt = struct.unpack_from("<H", body, 60)[0]
        loop = bool(opt & 1)
        frames = max(0, (size - _SAMP_HDR) // 2)
        fields.append(_f(54, 4, "sample_rate", f"{sr:,}", "Hz", enc="<I", raw=sr))
        fields.append(_f(None, 0, "bit_depth", 16, "16-bit signed PCM, mono"))
        fields.append(_f(None, 0, "frames", f"{frames:,}"))
        if sr:
            fields.append(_f(None, 0, "duration", f"{frames / sr:.3f} s"))
        fields.append(_f(60, 2, "options", f"{opt:#06x}", "bit0 = looped",
                         enc="<H", raw=opt))
        if loop:
            ls = struct.unpack_from("<I", body, 38)[0]
            le = struct.unpack_from("<I", body, 46)[0]
            fields.append(_f(38, 4, "loop_start", ls, "byte offset, 92-byte base",
                             enc="<I", raw=ls))
            fields.append(_f(46, 4, "loop_end", le, "byte offset, 92-byte base",
                             enc="<I", raw=le))
    return fields, name, loop


def _e4_walk_voices(body, idx_to_name):
    num_voices = _bu16(body, 20) if len(body) >= 22 else 0
    total_zones, refs, note = 0, [], ""
    off = _PRES_HDR
    for vi in range(min(num_voices, _VOICE_CAP)):
        if off + _VOICE_FIXED > len(body):
            note = (f"voice {vi} runs past the preset body "
                    f"(header says {num_voices} voices)")
            break
        trailer = _bu16(body, off + 2)
        span = trailer - _VOICE_FIXED
        if trailer < _VOICE_FIXED or span % _ZONE_ENTRY or span // _ZONE_ENTRY > _ZONE_CAP:
            note = f"voice {vi} zone-table trailer ({trailer}) is implausible"
            break
        n_zones = span // _ZONE_ENTRY
        for zi in range(n_zones):
            zoff = off + _VOICE_FIXED + zi * _ZONE_ENTRY
            if zoff + _ZONE_ENTRY > len(body):
                break
            entry = body[zoff:zoff + _ZONE_ENTRY]
            sidx = _bu16(entry, 10)
            name = idx_to_name.get(sidx, f"#{sidx}")
            if len(refs) < _REF_CAP:
                refs.append((name, entry[2], entry[5]))
        total_zones += n_zones
        off += _VOICE_FIXED + n_zones * _ZONE_ENTRY
        if vi == num_voices - 1:
            off += 2
    return num_voices, total_zones, refs, note


def _walk_e4b(data, size):
    warns = []
    form_size = _bu32(data, 4)
    if size <= _READ_CAP and form_size != size - 12:
        warns.append(f"FORM size {form_size} does not match the E-MU convention "
                     f"filesize-12 ({size - 12}); bank may be corrupt")

    records, chain_warns, desync_pos = _walk_records(data)
    warns.extend(chain_warns)
    toc_count = next((s // _TOC1_ENTRY for t, _, s in records if t == _TOC1), 0)
    note = _desync_note(data, desync_pos,
                        sum(1 for t, _, _ in records if t != _TOC1), toc_count)
    if note:
        warns.append(note)
    idx_to_name = _e4_sample_index_map(data, records)
    n_presets = sum(1 for t, _, _ in records if t == _E4P1)
    n_samples = sum(1 for t, _, _ in records if t == _E3S1)

    chunks = [_form_chunk(
        size, form_size, _E4B0, "E-MU Emulator 4 / EOS bank", "E-MU: filesize - 12",
        f"E-MU E4B bank: {n_presets} preset(s), {n_samples} sample(s)")]

    pi = si = 0
    saw_master = False
    for tag, off, csize in records:
        base = off + 8
        cw = []
        if tag == _TOC1:
            n_entries = csize // _TOC1_ENTRY
            fields, toc_offsets = [], []
            body = data[base:base + min(csize, _TOC_LIST_CAP * _TOC1_ENTRY)]
            for i in range(min(n_entries, _TOC_LIST_CAP)):
                e = body[i * _TOC1_ENTRY:(i + 1) * _TOC1_ENTRY]
                if len(e) < 30:
                    break
                foff = _bu32(e, 8)
                toc_offsets.append(foff)
                enm = _name(e[14:30])
                fields.append(_f(i * _TOC1_ENTRY, _TOC1_ENTRY, f"entry[{i}]",
                                 f"{e[0:4].decode('ascii', 'replace')} @ {foff:#x} "
                                 f"(size {_bu32(e, 4)}, idx {_bu16(e, 12)})"
                                 + (f" {enm}" if enm else ""), xref=foff))
            missing = _toc_cross_check(toc_offsets, records)
            if missing:
                cw.append(f"{len(missing)} TOC offset(s) do not match the chunk "
                          f"chain (e.g. {missing[0]:#x}); bank may be corrupt")
            chunks.append({"id": "TOC1", "offset": off, "size": csize,
                           "payload_base": base,
                           "summary": f"table of contents: {n_entries} entries",
                           "fields": fields, "warnings": cw})
        elif tag == _E4MA:
            chunks.append({"id": "E4Ma", "offset": off, "size": csize,
                           "payload_base": base,
                           "summary": f"MIDI multimap ({csize}-byte routing)",
                           "fields": [], "warnings": []})
        elif tag == _E4P1:
            body = data[base:base + csize]
            fields = []
            pname = _name(body[2:18]) if len(body) >= 18 else ""
            if len(body) >= 2:
                pidx = _bu16(body, 0)
                fields.append(_f(0, 2, "index", pidx, "0-based", enc=">H", raw=pidx))
            if len(body) >= 18:
                fields.append(_f(2, 16, "name", pname))
            nv, total_zones, refs, note = _e4_walk_voices(body, idx_to_name)
            if len(body) >= 22:
                fields.append(_f(20, 2, "num_voices", nv, enc=">H", raw=nv))
            if len(body) >= 29:
                fields.append(_f(28, 1, "volume", body[28]))
            fields.append(_f(None, 0, "zones", total_zones))
            seen, uniq = set(), []
            for nm, lo, hi in refs:
                if nm not in seen:
                    seen.add(nm)
                    uniq.append((nm, lo, hi))
            for j, (nm, lo, hi) in enumerate(uniq[:_REF_CAP]):
                fields.append(_f(None, 0, f"sample[{j}]", nm, f"keys {lo}-{hi}"))
            if note:
                cw.append(note)
            chunks.append({"id": f"E4P1[{pi}]", "offset": off, "size": csize,
                           "payload_base": base,
                           "summary": f"preset '{pname}': {nv} voice(s), "
                                      f"{total_zones} zone(s)",
                           "fields": fields, "warnings": cw})
            pi += 1
        elif tag == _E3S1:
            body = data[base:base + min(csize, _SAMP_HDR)]
            fields, sname, loop = _e4_sample_fields(body, csize)
            frames = max(0, (csize - _SAMP_HDR) // 2)
            chunks.append({"id": f"E3S1[{si}]", "offset": off, "size": csize,
                           "payload_base": base,
                           "summary": f"sample '{sname}': {frames:,} frames"
                                      + (", looped" if loop else ""),
                           "fields": fields, "warnings": []})
            si += 1
        elif tag == _EMST:
            saw_master = True
            chunks.append({"id": "EMSt", "offset": off, "size": csize,
                           "payload_base": base,
                           "summary": f"master setup ({csize}-byte global params)",
                           "fields": [], "warnings": []})

    if records and not saw_master:
        warns.append("no EMSt master-setup chunk; a hardware-saved E4B ends with "
                     "one (it is not in the TOC)")
    elif saw_master and records[-1][0] != _EMST:
        warns.append("EMSt master-setup chunk is not the last chunk")
    return chunks, warns


# ── E5B0 (Emulator X / Proteus X) ────────────────────────────────────

def _e5_preset(body):
    """Return (name, sub_chunks) for an E5P1 nested preset container."""
    name, subs = "", []
    p = 2                       # body[0:2] is a leading count/flag
    while p + 8 <= len(body) and len(subs) < _REF_CAP:
        st = body[p:p + 4]
        if not _is_tag(st):
            break
        ss = _bu32(body, p + 4)
        if st == _PHDR and p + 12 <= len(body):
            name = _wname(body[p + 12:p + 12 + 64])
        subs.append((st.decode("ascii", "replace"), p, ss))
        p += 8 + ss + (ss & 1)
    return name, subs


def _e5_sample_fields(body, size):
    fields, name, sr = [], "", 0
    if len(body) >= 8:
        name = _wname(body[6:6 + 64])
        if name:
            fields.append(_f(6, len(name) * 2, "name", name))
    if len(body) >= _E5S1_RATE_OFF + 4:
        v = struct.unpack_from("<I", body, _E5S1_RATE_OFF)[0]
        if 8000 <= v <= 192000:
            sr = v
            fields.append(_f(_E5S1_RATE_OFF, 4, "sample_rate", f"{sr:,}", "Hz",
                             enc="<I", raw=sr))
            fields.append(_f(None, 0, "bit_depth", 16, "16-bit PCM"))
            approx = max(0, size // 2)
            fields.append(_f(None, 0, "frames", f"~{approx:,}", "approx (incl. header)"))
            fields.append(_f(None, 0, "duration", f"~{approx / sr:.3f} s"))
    return fields, name, sr


def _walk_e5b(data, size):
    warns = []
    form_size = _bu32(data, 4)
    if size <= _READ_CAP and form_size != size - 8:
        warns.append(f"FORM size {form_size} does not match standard IFF "
                     f"filesize-8 ({size - 8}); bank may be corrupt")

    records, chain_warns, desync_pos = _walk_records(data)
    warns.extend(chain_warns)
    toc_count = next((s // _TOC2_ENTRY for t, _, s in records if t == _TOC2), 0)
    note = _desync_note(data, desync_pos,
                        sum(1 for t, _, _ in records if t != _TOC2), toc_count)
    if note:
        warns.append(note)
    n_pre = sum(1 for t, _, _ in records if t == _E5P1)
    n_smp = sum(1 for t, _, _ in records if t == _E5S1)
    n_lnk = sum(1 for t, _, _ in records if t == _E5SL)

    summ = f"E-MU E5B bank: {n_pre} preset(s), {n_smp} sample(s)"
    if n_lnk:
        summ += f", {n_lnk} sample link(s)"
    chunks = [_form_chunk(size, form_size, _E5B0,
                          "E-MU Emulator X / Proteus X bank", "standard IFF: filesize - 8",
                          summ)]

    pi = si = li = 0
    for tag, off, csize in records:
        base = off + 8
        cw = []
        if tag == _TOC2:
            n_entries = csize // _TOC2_ENTRY
            fields, toc_offsets = [], []
            body = data[base:base + min(csize, _TOC_LIST_CAP * _TOC2_ENTRY)]
            for i in range(min(n_entries, _TOC_LIST_CAP)):
                e = body[i * _TOC2_ENTRY:(i + 1) * _TOC2_ENTRY]
                if len(e) < 14:
                    break
                foff = _bu32(e, 8)
                toc_offsets.append(foff)
                enm = _wname(e[14:_TOC2_ENTRY])   # [12:14] is a BE u16 index
                fields.append(_f(i * _TOC2_ENTRY, _TOC2_ENTRY, f"entry[{i}]",
                                 f"{e[0:4].decode('ascii', 'replace')} @ {foff:#x} "
                                 f"(size {_bu32(e, 4)}, idx {_bu16(e, 12)})"
                                 + (f" {enm}" if enm else ""), xref=foff))
            missing = _toc_cross_check(toc_offsets, records)
            if missing:
                cw.append(f"{len(missing)} TOC offset(s) do not match the chunk "
                          f"chain (e.g. {missing[0]:#x}); bank may be corrupt")
            chunks.append({"id": "TOC2", "offset": off, "size": csize,
                           "payload_base": base,
                           "summary": f"table of contents: {n_entries} entries",
                           "fields": fields, "warnings": cw})
        elif tag == _E5P1:
            body = data[base:base + csize]
            name, subs = _e5_preset(body)
            fields = [_f(None, 0, "name", name)] if name else []
            for j, (st, soff, ss) in enumerate(subs[:_REF_CAP]):
                fields.append(_f(soff, 8, f"sub[{j}]", f"{st} ({ss:,} bytes)"))
            chunks.append({"id": f"E5P1[{pi}]", "offset": off, "size": csize,
                           "payload_base": base,
                           "summary": f"preset '{name}': {len(subs)} sub-chunk(s)",
                           "fields": fields, "warnings": cw})
            pi += 1
        elif tag == _E5S1:
            body = data[base:base + min(csize, 0x100)]
            fields, name, sr = _e5_sample_fields(body, csize)
            chunks.append({"id": f"E5S1[{si}]", "offset": off, "size": csize,
                           "payload_base": base,
                           "summary": f"sample '{name}'" + (f": {sr:,} Hz" if sr else ""),
                           "fields": fields, "warnings": []})
            si += 1
        elif tag == _E5SL:
            body = data[base:base + min(csize, 6)]
            slot = _bu16(body, 0) if len(body) >= 2 else 0
            sidx = _bu32(body, 2) if len(body) >= 6 else 0
            chunks.append({"id": f"E5SL[{li}]", "offset": off, "size": csize,
                           "payload_base": base,
                           "summary": f"sample link -> #{sidx}",
                           "fields": [_f(0, 2, "slot", slot, enc=">H", raw=slot),
                                      _f(2, 4, "sample_index", sidx, "into SamplePool",
                                         enc=">I", raw=sidx)],
                           "warnings": []})
            li += 1
        else:
            chunks.append({"id": tag.decode("ascii", "replace"), "offset": off,
                           "size": csize, "payload_base": base,
                           "summary": f"{csize:,}-byte chunk", "fields": [],
                           "warnings": []})
    return chunks, warns


def inspect_emu(filepath):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(size, _READ_CAP))
    if data[:4] != _FORM:
        raise _Unsupported("not an E-MU bank (FORM E4B0/E5B0)")
    ft = data[8:12]
    if ft == _E4B0:
        return _walk_e4b(data, size)
    if ft == _E5B0:
        return _walk_e5b(data, size)
    raise _Unsupported(f"not an E-MU E4B/E5B bank (FORM {ft.decode('latin-1', 'replace')})")
