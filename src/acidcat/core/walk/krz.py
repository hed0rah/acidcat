"""Kurzweil K2000 / K2500 / K2600 (VAST) `.KRZ` bank walker.

A .KRZ file is a flat object-database dump, big-endian throughout (the K-series
is a 68000 platform). A 32-byte `PRAM` header, then a run of length-prefixed
object blocks (Sample / Keymap / Program / Setup / Master / Studio-FX / ...),
an int32=0 end marker, then one contiguous region of raw 16-bit PCM that Sample
objects address by absolute word offset. Effects-only files use a `SROM` header
instead and are surfaced header-only.

Object framing and the Sample/Keymap/Program bodies follow the reverse-
engineered reference in to_analyze/mpc2emu/docs/KRZ_FORMAT.md (hardware-
confirmed) and the KurzFiler source; the hash encoding (type<<10 | id for
type<=42) is from KHash. Verified structurally against 242 real Sweetwater
soundset banks. Unknown object types and effects payloads are surfaced by
type/id/name without guessing at their bodies.
"""

import os
import struct

from acidcat.core.walk.base import _f
from acidcat.util.midi import midi_note_to_name

# read cap: a forged blocksize/osize cannot force an unbounded allocation. Real
# banks run to a few MB of PCM; the header+objects are kilobytes.
_READ_CAP = 64 * 1024 * 1024
_OBJECT_CAP = 4096          # max objects walked (a forged bank cannot spin forever)

# object type codes (hash = type<<10 | id for type<=42; KHash / KRZ_FORMAT.md).
# 36/37/38 are hardware-confirmed; the rest are labelled from the object names
# seen across the corpus, marked tentative where unconfirmed.
_TYPES = {
    25: "Master",
    26: "Intonation?",
    27: "QuickAccess?",
    28: "Studio/FX",
    36: "Program",
    37: "Keymap",
    38: "Sample",
    39: "Setup",
}

# program tagged-segment lengths (KRZ_FORMAT.md section 4). Most tags key on
# tag & 0xF8 (so LYR 0x09 shares PGM 0x08's family, HOB 0x50-0x53 share 0x50);
# FX 0x0F is the exact-tag exception (it lives in the 0x08 family but is 7 bytes,
# not 15), so exact tags are checked first.
_SEG_EXACT = {0x0F: 7}
_SEG_FAMILY = {0x08: 15, 0x10: 7, 0x18: 3, 0x20: 15,
               0x40: 31, 0x50: 15, 0x68: 7, 0x78: 31}


def _seg_len(tag):
    if tag in _SEG_EXACT:
        return _SEG_EXACT[tag]
    return _SEG_FAMILY.get(tag & 0xF8)


def inspect_krz(filepath):
    """Walk a Kurzweil .KRZ file, returning (chunks, file_warnings)."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        b = f.read(min(file_size, _READ_CAP))
    chunks, warns = [], []
    if b[:4] == b"SROM":
        return _inspect_srom(b, file_size)
    if b[:4] != b"PRAM":
        return chunks, ["not a Kurzweil PRAM/SROM file"]
    if len(b) < 32:
        return chunks, [f"file is {len(b)} bytes; a PRAM header needs 32"]

    osize = struct.unpack_from(">i", b, 4)[0]
    version = struct.unpack_from(">i", b, 16)[0]
    hdr = {"id": "PRAM", "offset": 0, "size": 32, "payload_base": 0,
           "summary": f"Kurzweil bank, OS v{version / 100:.2f}",
           "fields": [
               _f(0x00, 4, "magic", "PRAM"),
               _f(0x04, 4, "pcm_offset", osize, "byte offset of the PCM region",
                  enc=">i", raw=osize),
               _f(0x10, 4, "software_version", version,
                  f"K2000 OS v{version / 100:.2f}", enc=">i", raw=version),
           ], "warnings": []}
    if not 0 < osize <= len(b):
        hdr["warnings"].append(
            f"pcm_offset {osize:,} is outside the file ({len(b):,} bytes)")
    chunks.append(hdr)

    # walk the object blocks: blocksize is a NEGATIVE i32 (block bytes), advance
    # by -blocksize; the int32=0 marker ends the object section.
    pos, n = 32, 0
    from collections import Counter
    kinds = Counter()
    while pos + 4 <= len(b) and n < _OBJECT_CAP:
        blocksize = struct.unpack_from(">i", b, pos)[0]
        if blocksize == 0:
            break                                   # object-section end marker
        if blocksize > 0 or pos - blocksize > len(b) + 4:
            warns.append(f"object at 0x{pos:08x} has a bad blocksize "
                         f"{blocksize}; stopping the walk")
            break
        block_len = -blocksize
        try:
            chunk = _object(b, pos, block_len)
        except Exception as e:                      # never raise on a bad object
            chunk = {"id": "obj", "offset": pos, "size": block_len,
                     "summary": "unparsed object",
                     "fields": [], "warnings": [
                         f"object decode error: {e.__class__.__name__}: {e}"]}
        kinds[chunk["id"]] += 1
        chunks.append(chunk)
        pos += block_len
        n += 1
    if n >= _OBJECT_CAP:
        warns.append(f"object walk stopped at the {_OBJECT_CAP}-object cap")

    # the PCM sample region after the end marker
    if 0 < osize < len(b):
        pcm = file_size - osize
        chunks.append({"id": "PCM", "offset": osize, "size": pcm,
                       "summary": f"raw 16-bit big-endian PCM, {pcm:,} bytes "
                                  f"({pcm // 2:,} samples)",
                       "fields": [], "warnings": []})
    summary = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
    if summary:
        chunks[0]["summary"] += f" ({summary})"
    return chunks, warns


def _object(b, pos, block_len):
    """Decode one object block header + (for known types) its body."""
    hash_ = struct.unpack_from(">H", b, pos + 4)[0]
    obj_size = struct.unpack_from(">H", b, pos + 6)[0]
    ofs = struct.unpack_from(">H", b, pos + 8)[0]
    tcode = hash_ >> 10
    oid = hash_ & 0x3FF
    tname = _TYPES.get(tcode, f"type{tcode}")
    name = b[pos + 10:pos + 10 + 16].split(b"\x00")[0].decode("ascii", "replace").strip()
    data = pos + 8 + ofs                            # object-specific data start

    fields = [
        _f(0x00, 4, "blocksize", block_len, "block bytes (stored negative)"),
        _f(0x04, 2, "hash", f"0x{hash_:04x}", f"type {tcode}, id {oid}",
           enc=">H", raw=hash_),
        _f(0x06, 2, "obj_size", obj_size),
        _f(0x08, 2, "name_ofs", ofs),
        _f(0x0A, len(name) or 1, "name", name or "(unnamed)"),
    ]
    warns = []
    summary = f"{tname} #{oid}" + (f" '{name}'" if name else "")

    if tcode == 38:                                 # Sample
        s, sf, sw = _sample_body(b, data)
        fields += sf
        warns += sw
        summary += f", {s}" if s else ""
    elif tcode == 37:                               # Keymap
        s, kf, kw = _keymap_body(b, data, pos + block_len)
        fields += kf
        warns += kw
        summary += f", {s}" if s else ""
    elif tcode == 36:                               # Program
        s, pf, pw = _program_body(b, data, pos + block_len)
        fields += pf
        warns += pw
        summary += f", {s}" if s else ""

    return {"id": tname, "offset": pos, "size": block_len,
            "summary": summary, "fields": fields, "warnings": warns}


def _sample_body(b, off):
    """KSample (12) + Soundfilehead (32): rootkey, loop flag, PCM word refs,
    sample rate from samplePeriod."""
    if off + 44 > len(b):
        return "truncated", [], ["sample body under 44 bytes"]
    # Soundfilehead starts right after the 12-byte KSample header
    sf = off + 12
    rootkey = b[sf]
    sflags = b[sf + 1]
    max_pitch = struct.unpack_from(">H", b, sf + 4)[0]
    sample_start = struct.unpack_from(">i", b, sf + 8)[0]
    loop_start = struct.unpack_from(">i", b, sf + 16)[0]
    sample_end = struct.unpack_from(">i", b, sf + 20)[0]
    period = struct.unpack_from(">I", b, sf + 28)[0]
    rate = round(1e9 / period) if period else 0
    looped = not (sflags & 0x80)                     # 0x80 set = one-shot (inverted)
    kind = "loop" if looped else "one-shot"
    fields = [
        _f(12, 1, "root_key", rootkey, midi_note_to_name(rootkey)),
        _f(13, 1, "sf_flags", f"0x{sflags:02x}", kind, enc=">B", raw=sflags),
        _f(20, 4, "sample_start", sample_start, "PCM word offset",
           enc=">i", raw=sample_start),
        _f(28, 4, "loop_start", loop_start, "PCM word", enc=">i", raw=loop_start),
        _f(32, 4, "sample_end", sample_end,
           "loop end (word)" if looped else "PCM end (word)",
           enc=">i", raw=sample_end),
        _f(40, 4, "sample_period", period, f"{rate} Hz" if rate else "",
           enc=">I", raw=period),
    ]
    return f"{rate} Hz {kind}, root {midi_note_to_name(rootkey)}", fields, []


def _keymap_body(b, off, block_end):
    """KKeymap header + up to 128 5-byte key entries: method, referenced
    sample ids."""
    if off + 28 > len(b):
        return "truncated", [], ["keymap body under 28 bytes"]
    method = struct.unpack_from(">H", b, off + 2)[0]
    cents = struct.unpack_from(">H", b, off + 6)[0]
    entry_size = struct.unpack_from(">H", b, off + 10)[0] or 5
    entries_off = off + 28
    sample_ids = set()
    keys = 0
    p = entries_off
    while p + 5 <= min(block_end, len(b)) and keys < 128:
        sid = struct.unpack_from(">H", b, p + 2)[0]
        if sid:
            sample_ids.add(sid)
        keys += 1
        p += entry_size
    fields = [
        _f(2, 2, "method", f"0x{method:04x}",
           "per-entry tuning|sampleID|subSample" if method == 0x13 else "",
           enc=">H", raw=method),
        _f(6, 2, "cents_per_entry", cents),
        _f(None, 0, "sample_refs",
           ",".join(str(s) for s in sorted(sample_ids)) or "(none)",
           f"{len(sample_ids)} unique sample(s) across {keys} keys"),
    ]
    return f"{len(sample_ids)} sample(s), {keys} keys", fields, []


def _program_body(b, off, block_end):
    """Program = tagged segments tag(1B)+data (KRZ_FORMAT.md section 4), int16=0
    terminated. Count VAST layers (one LYR 0x09 tag per layer). The CAL segment's
    keymap-ref offset is not yet pinned down, so it is not reported (verify-then-
    claim; a follow-up once save-and-diff locates it)."""
    p = off
    layers = 0
    end = min(block_end, len(b))
    guard = 0
    while p + 1 <= end and guard < 4096:
        guard += 1
        tag = b[p]
        if p + 2 <= end and struct.unpack_from(">h", b, p)[0] == 0:
            break                                   # int16=0 terminator
        seglen = _seg_len(tag)
        if seglen is None:
            break                                   # unknown tag: stop cleanly
        if tag == 0x09:
            layers += 1
        p += 1 + seglen
    return f"{layers} layer(s)", [_f(None, 0, "layers", layers,
                                     "VAST layer(s)")], []


def _inspect_srom(b, file_size):
    """Kurzweil SROM (sample ROM / effects) file: header only for now."""
    size = struct.unpack_from(">I", b, 4)[0] if len(b) >= 8 else 0
    chunk = {"id": "SROM", "offset": 0, "size": len(b), "payload_base": 0,
             "summary": f"Kurzweil sample-ROM/effects file, declared {size:,} bytes",
             "fields": [
                 _f(0x00, 4, "magic", "SROM"),
                 _f(0x04, 4, "size", size, enc=">I", raw=size),
             ], "warnings": []}
    return [chunk], []
