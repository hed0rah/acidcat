"""Safe RIFF/WAVE rewriting for acidcat's write capability.

Edits LIST/INFO tags and the acid chunk (bpm/key) while preserving the audio and
every unknown chunk byte-for-byte. Follows the RIFF rules exactly: little-endian
sizes, one uncounted 0x00 pad after any odd-sized chunk, riff_size = file - 8,
fmt before data. RF64/BW64 and malformed files are refused rather than guessed.
"""

import struct

from acidcat.core.edits import EditError
from acidcat.util.midi import NOTES

# field -> INFO sub-chunk id
_INFO_TAGS = {
    "title": b"INAM", "name": b"INAM",
    "artist": b"IART", "creator": b"IART",
    "album": b"IPRD",
    "genre": b"IGNR",
    "comment": b"ICMT",
    "date": b"ICRD", "year": b"ICRD",
    "software": b"ISFT", "engineer": b"IENG", "track": b"ITRK",
}
_ACID_FIELDS = {"bpm", "tempo", "key"}
# bext fixed ASCII fields: field -> (offset, width). Editing is a size-stable
# in-place patch (truncate to width, null-pad).
_BEXT_FIELDS = {
    "bext_description": (0, 256), "description": (0, 256),
    "originator": (256, 32),
    "originator_reference": (288, 32), "reference": (288, 32),
    "origination_date": (320, 10), "date_recorded": (320, 10),
    "origination_time": (330, 8), "time_recorded": (330, 8),
}
_BEXT_MIN = 602
_NOTE_INDEX = {n: i for i, n in enumerate(NOTES)}


def _note_to_midi(s):
    """Parse a note name ('C3', 'A#4') or bare int to a MIDI number, C3 = 60
    (DAW convention). Returns None if unparseable."""
    s = s.strip()
    if s.isdigit():
        return int(s)
    i = 1
    if len(s) > 1 and s[1] in "#b":
        i = 2
    name = s[:i].upper().replace("B", "b") if i == 2 and s[1] == "b" else s[:i].upper()
    pc = _NOTE_INDEX.get(name)
    if pc is None:
        return None
    try:
        octave = int(s[i:])
    except ValueError:
        octave = 3
    return (octave + 2) * 12 + pc


def _iter_chunks(data):
    """Yield (chunk_id, payload) preserving order. Raises EditError on anything
    unsafe to rewrite."""
    if data[:4] in (b"RF64", b"BW64"):
        raise EditError("RF64/BW64 file (64-bit sizes); refusing to rewrite")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise EditError("not a RIFF/WAVE file")
    n = len(data)
    pos = 12
    chunks = []
    seen_fmt = seen_data = False
    while pos + 8 <= n:
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if size == 0xFFFFFFFF or pos + 8 + size > n:
            raise EditError(f"chunk {cid!r} overruns the file; refusing to rewrite")
        if not all(32 <= b < 127 for b in cid):
            raise EditError("non-printable chunk id; refusing to rewrite")
        payload = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            seen_fmt = True
        if cid == b"data":
            if not seen_fmt:
                raise EditError("data chunk precedes fmt; refusing to rewrite")
            seen_data = True
        chunks.append([cid, payload])
        pos += 8 + size + (size & 1)
    if not seen_data:
        raise EditError("no data chunk; refusing to rewrite")
    trailing = data[pos:]  # bytes past the last aligned chunk, preserved verbatim
    return chunks, trailing


def _parse_info(payload):
    """{sub_id: text} from a LIST/INFO payload (payload starts with 'INFO')."""
    out = {}
    if payload[:4] != b"INFO":
        return out
    i, n = 4, len(payload)
    while i + 8 <= n:
        sid = payload[i:i + 4]
        sz = struct.unpack_from("<I", payload, i + 4)[0]
        if i + 8 + sz > n:
            break
        out[sid] = payload[i + 8:i + 8 + sz].split(b"\x00", 1)[0]
        i += 8 + sz + (sz & 1)
    return out


def _build_info(tags):
    """LIST payload ('INFO' + sub-chunks) from {sub_id: text bytes}."""
    body = b"INFO"
    for sid, text in tags.items():
        data = text + b"\x00"  # ZSTR: terminator counted in size
        body += sid + struct.pack("<I", len(data)) + data
        if len(data) & 1:
            body += b"\x00"  # uncounted pad
    return body


def edit_wav(data, changes):
    chunks, trailing = _iter_chunks(data)
    applied = []

    info_changes = {f: v for f, v in changes.items() if f.lower() in _INFO_TAGS}
    acid_changes = {f: v for f, v in changes.items() if f.lower() in _ACID_FIELDS}
    bext_changes = {f: v for f, v in changes.items() if f.lower() in _BEXT_FIELDS}
    unknown = set(changes) - set(info_changes) - set(acid_changes) - set(bext_changes)
    if unknown:
        raise EditError(f"WAV has no editable field(s): {', '.join(sorted(unknown))}")

    # ---- LIST/INFO tags ----
    if info_changes:
        li = next((c for c in chunks
                   if c[0] == b"LIST" and c[1][:4] == b"INFO"), None)
        tags = _parse_info(li[1]) if li else {}
        for field, value in info_changes.items():
            sid = _INFO_TAGS[field.lower()]
            old = tags.get(sid, b"").decode("latin-1") or None
            if value is None:
                tags.pop(sid, None)
            else:
                tags[sid] = str(value).encode("utf-8")
            applied.append((field, old, value))
        payload = _build_info(tags)
        if li:
            li[1] = payload
        else:
            chunks.append([b"LIST", payload])  # append after data

    # ---- acid bpm / key ----
    if acid_changes:
        ac = next((c for c in chunks if c[0] == b"acid"), None)
        buf = bytearray(ac[1]) if ac else bytearray(struct.pack(
            "<IHHfIHHf", 0, 0, 0x8000, 0.0, 0, 4, 4, 120.0))
        if len(buf) < 24:
            raise EditError("acid chunk too short to edit safely")
        for field, value in acid_changes.items():
            fl = field.lower()
            if fl in ("bpm", "tempo"):
                old = round(struct.unpack_from("<f", buf, 20)[0], 3)
                struct.pack_into("<f", buf, 20, float(value) if value else 0.0)
                applied.append((field, old, value))
            elif fl == "key":
                flags = struct.unpack_from("<I", buf, 0)[0]
                if value is None:
                    struct.pack_into("<H", buf, 4, 0)
                    struct.pack_into("<I", buf, 0, flags & ~0x02)
                    applied.append((field, "set", None))
                else:
                    midi = _note_to_midi(str(value))
                    if midi is None:
                        raise EditError(f"unrecognized key {value!r}")
                    struct.pack_into("<H", buf, 4, midi)
                    struct.pack_into("<I", buf, 0, flags | 0x02)
                    applied.append((field, None, value))
        if ac:
            ac[1] = bytes(buf)
        else:
            chunks.append([b"acid", bytes(buf)])

    # ---- bext fixed fields (size-stable patch, or create a minimal chunk) ----
    if bext_changes:
        bx = next((c for c in chunks if c[0] == b"bext"), None)
        buf = bytearray(bx[1]) if bx else bytearray(_BEXT_MIN)
        if len(buf) < _BEXT_MIN:
            buf += bytearray(_BEXT_MIN - len(buf))
        for field, value in bext_changes.items():
            off, width = _BEXT_FIELDS[field.lower()]
            old = buf[off:off + width].split(b"\x00", 1)[0].decode("latin-1") or None
            raw = ("" if value is None else str(value)).encode("ascii", "replace")[:width]
            buf[off:off + width] = raw + b"\x00" * (width - len(raw))
            applied.append((field, old, value))
        if bx:
            bx[1] = bytes(buf)
        else:
            chunks.insert(next(i for i, c in enumerate(chunks) if c[0] == b"data"),
                          [b"bext", bytes(buf)])  # bext goes before data

    # ---- emit ----
    out = bytearray(b"RIFF\x00\x00\x00\x00WAVE")
    data_before = next(c[1] for c in _iter_chunks(data)[0] if c[0] == b"data")
    for cid, payload in chunks:
        out += cid + struct.pack("<I", len(payload)) + payload
        if len(payload) & 1:
            out += b"\x00"
    out += trailing
    struct.pack_into("<I", out, 4, len(out) - 8)

    # verify audio survived untouched
    data_after = next(c[1] for c in _iter_chunks(bytes(out))[0] if c[0] == b"data")
    if data_after != data_before:
        raise EditError("internal: audio data changed during rewrite (aborted)")
    return bytes(out), applied
