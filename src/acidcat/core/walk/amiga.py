"""Amiga music-format walkers: the formats the MOD/XM/IT tracker walker does not
cover, harvested from the acidcat-cassie Amiga corpus. All big-endian (68000).

  * SMUS -- IFF `FORM/SMUS`, the Sonix "simple musical score": a sibling of
    8SVX/AIFF. SHDR (tempo/volume/track count), NAME, INS1 instruments, TRAK
    note tracks, and Sonix's private SNX1.
  * OKT  -- Oktalyzer, a 4-8 channel Amiga tracker: `OKTASONG` then IFF-style
    id+size chunks (CMOD channel split flags, SAMP sample table, SPEE, S/PLEN).
  * MED / OctaMED -- magic `MMD0`/`MMD1`/`MMD2`/`MMD3`: recognized and header-
    summarized (a pointer-based module; deep decode is a follow-up).
  * Future Composer -- a synth-driver chiptune format, magic `SMOD` (v1.3) or
    `FC14` (v1.4): recognized and header-summarized.

Header-level decode, never-raise. Deep pattern/sample decode is future work.
"""

import struct

from acidcat.core.walk.base import _bu16, _bu32, _dtext, _f

_CAP = 32 * 1024 * 1024
_CHUNK_CAP = 2048


def _read(filepath):
    with open(filepath, "rb") as f:
        return f.read(_CAP)


def _iff_chunks(b, start):
    """Yield (id_bytes, offset, size, payload_bytes) for IFF-style id+u32size
    chunks from `start`, big-endian, word-padded. Bounded and clamping."""
    pos, n = start, 0
    while pos + 8 <= len(b) and n < _CHUNK_CAP:
        cid = b[pos:pos + 4]
        size = _bu32(b, pos + 4)
        payload = pos + 8
        avail = max(0, min(size, len(b) - payload))
        yield cid, pos, size, b[payload:payload + avail]
        n += 1
        step = 8 + size + (size & 1)
        if step <= 8:
            break
        pos += step


# ── SMUS (IFF FORM/SMUS) ────────────────────────────────────────────────────

def inspect_smus(filepath):
    b = _read(filepath)
    chunks, warns = [], []
    if len(b) < 12 or b[:4] != b"FORM" or b[8:12] != b"SMUS":
        return chunks, ["not an IFF FORM SMUS file"]
    form_size = _bu32(b, 4)
    chunks.append({"id": "FORM", "offset": 0, "size": 12, "payload_base": 0,
                   "summary": "IFF SMUS (Sonix musical score)",
                   "fields": [_f(0x00, 4, "magic", "FORM"),
                              _f(0x04, 4, "form_size", form_size, enc=">I", raw=form_size),
                              _f(0x08, 4, "form_type", "SMUS")],
                   "warnings": []})
    name = None
    tracks = instruments = 0
    from collections import Counter
    kinds = Counter()
    for cid, off, size, p in _iff_chunks(b, 12):
        cid_s = cid.decode("ascii", "replace")
        kinds[cid_s] += 1
        fields, summary = [], f"{size:,} bytes"
        if cid == b"SHDR" and len(p) >= 4:
            tempo = _bu16(p, 0)
            volume, ct = p[2], p[3]
            tracks = ct
            fields = [_f(0x00, 2, "tempo", tempo, "raw SMUS tempo word"),
                      _f(0x02, 1, "volume", volume),
                      _f(0x03, 1, "ctTrack", ct, "number of note tracks")]
            summary = f"score header: tempo {tempo}, volume {volume}, {ct} track(s)"
        elif cid_s in ("NAME", "AUTH", "(c) ", "ANNO"):
            text = _dtext(p)
            if cid == b"NAME":
                name = text
            fields = [_f(0x00, len(p), "text", text or "(empty)")]
            summary = f"{cid_s.strip()}: {text}" if text else cid_s
        elif cid == b"INS1":
            instruments += 1
            summary = "instrument register"
        elif cid == b"TRAK":
            summary = "note track"
        elif cid == b"SNX1":
            summary = "Sonix private data"
        chunks.append({"id": cid_s, "offset": off, "size": size,
                       "summary": summary, "fields": fields, "warnings": []})
    bits = []
    if tracks:
        bits.append(f"{tracks} track(s)")
    if kinds.get("INS1"):
        bits.append(f"{kinds['INS1']} instrument(s)")
    if name:
        bits.append(f"'{name}'")
    if bits:
        chunks[0]["summary"] += " -- " + ", ".join(bits)
    return chunks, warns


# ── OKT (Oktalyzer) ─────────────────────────────────────────────────────────

def inspect_okt(filepath):
    b = _read(filepath)
    chunks, warns = [], []
    if len(b) < 8 or b[:8] != b"OKTASONG":
        return chunks, ["not an Oktalyzer OKTASONG file"]
    hdr = {"id": "OKTASONG", "offset": 0, "size": 8, "payload_base": 0,
           "summary": "Oktalyzer module", "fields": [_f(0x00, 8, "magic", "OKTASONG")],
           "warnings": []}
    chunks.append(hdr)
    channels = 0
    samples = 0
    first_sample = None
    for cid, off, size, p in _iff_chunks(b, 8):
        cid_s = cid.decode("ascii", "replace")
        fields, summary = [], f"{size:,} bytes"
        if cid == b"CMOD" and len(p) >= 8:
            # 4 words: each split channel (non-zero) becomes two voices
            words = [_bu16(p, i * 2) for i in range(4)]
            channels = sum(2 if w else 1 for w in words)
            fields = [_f(0x00, 8, "channel_modes", "/".join(str(w) for w in words),
                         f"{channels} voices (a split word = 2)")]
            summary = f"channel setup: {channels} voices"
        elif cid == b"SAMP":
            samples = size // 32                       # 32-byte sample entries
            nm = (_dtext(p[:20]).rstrip("\x00 ") if len(p) >= 20 else "")
            first_sample = nm or first_sample
            fields = [_f(0x00, min(20, len(p)), "first_sample", nm or "(unnamed)")]
            summary = f"sample table: {samples} entr(y/ies)"
        elif cid == b"SPEE" and len(p) >= 2:
            summary = f"initial speed/tempo: {_bu16(p, 0)}"
        elif cid in (b"SLEN", b"PLEN") and len(p) >= 2:
            summary = f"{'pattern count' if cid == b'SLEN' else 'song length'}: {_bu16(p, 0)}"
        chunks.append({"id": cid_s, "offset": off, "size": size,
                       "summary": summary, "fields": fields, "warnings": []})
    bits = []
    if channels:
        bits.append(f"{channels} voices")
    if samples:
        bits.append(f"{samples} sample(s)")
    if first_sample:
        bits.append(f"'{first_sample}'")
    if bits:
        hdr["summary"] += " -- " + ", ".join(bits)
    return chunks, warns


# ── MED / OctaMED ───────────────────────────────────────────────────────────

_MED_MAGICS = {b"MMD0": "MED", b"MMD1": "OctaMED", b"MMD2": "OctaMED Pro",
               b"MMD3": "OctaMED SoundStudio"}


def inspect_med(filepath):
    b = _read(filepath)
    chunks, warns = [], []
    magic = b[:4]
    if magic not in _MED_MAGICS:
        return chunks, ["not a MED/OctaMED MMDx file"]
    variant = _MED_MAGICS[magic]
    modlen = _bu32(b, 4) if len(b) >= 8 else 0
    fields = [_f(0x00, 4, "magic", magic.decode("ascii", "replace"),
                 f"{variant} module"),
              _f(0x04, 4, "modlen", modlen, "declared module length",
                 enc=">I", raw=modlen)]
    warn = []
    if 0 < modlen and modlen != len(b):
        warn.append(f"declared modlen {modlen:,} != file length {len(b):,}")
    chunks.append({"id": magic.decode("ascii", "replace"), "offset": 0,
                   "size": len(b), "payload_base": 0,
                   "summary": f"{variant} module ({magic.decode('ascii','replace')}), "
                              f"{len(b):,} bytes",
                   "fields": fields, "warnings": warn})
    return chunks, warns


# ── Future Composer ─────────────────────────────────────────────────────────

def inspect_fc(filepath):
    b = _read(filepath)
    chunks, warns = [], []
    magic = b[:4]
    if magic == b"SMOD":
        ver = "1.3"
    elif magic == b"FC14":
        ver = "1.4"
    else:
        return chunks, ["not a Future Composer SMOD/FC14 file"]
    # the header is a table of big-endian u32 offset/length pairs into the
    # sequence/pattern/frequency/volume/sample regions; surfaced at a high level.
    seqlen = _bu32(b, 4) if len(b) >= 8 else 0
    fields = [_f(0x00, 4, "magic", magic.decode("ascii", "replace"),
                 f"Future Composer v{ver}"),
              _f(0x04, 4, "seq_length", seqlen, "sequence-table length (bytes)",
                 enc=">I", raw=seqlen)]
    chunks.append({"id": magic.decode("ascii", "replace"), "offset": 0,
                   "size": len(b), "payload_base": 0,
                   "summary": f"Future Composer v{ver} chiptune, {len(b):,} bytes",
                   "fields": fields, "warnings": []})
    return chunks, warns
