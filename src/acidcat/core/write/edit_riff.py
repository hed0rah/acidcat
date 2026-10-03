"""Safe RIFF/WAVE rewriting for acidcat's write capability.

Edits LIST/INFO tags and the acid chunk (bpm/key) while preserving the audio and
every unknown chunk byte-for-byte. Follows the RIFF rules exactly: little-endian
sizes, one uncounted 0x00 pad after any odd-sized chunk, riff_size = file - 8,
fmt before data. RF64/BW64 and malformed files are refused rather than guessed.
An odd chunk whose writer left the pad out is followed where the next chunk
really starts, as the walker does, and gets its pad on the rewrite.
"""

import io
import struct

from acidcat.core.formats.riff import pad_step
from acidcat.core.write.edits import BadValue, EditError
from acidcat.util.midi import NOTES, midi_note_to_name

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
# smpl chunk: the sampler root key (midi_unity_note) at payload offset 12.
_SMPL_FIELDS = {"root", "root_note", "unity_note"}
_NOTE_INDEX = {n: i for i, n in enumerate(NOTES)}


def _fmt_sample_rate(chunks):
    fmt = next((c[1] for c in chunks if c[0] == b"fmt "), None)
    if fmt and len(fmt) >= 8:
        return struct.unpack_from("<I", fmt, 4)[0]
    return 44100


# how far an acid chunk's beats at its tempo may miss the audio's length
# before the walker calls the chunk inconsistent (core/walk/wav.py _parse_acid)
_BEAT_DRIFT = 0.05


def _audio_seconds(chunks):
    """The audio's length in seconds as the walker reckons it: the fact
    chunk's sample count, else the data bytes over block_align. None when
    the file does not say."""
    fmt = next((c[1] for c in chunks if c[0] == b"fmt "), None)
    if not fmt or len(fmt) < 16:
        return None
    rate = struct.unpack_from("<I", fmt, 4)[0]
    align = struct.unpack_from("<H", fmt, 12)[0]
    fact = next((c[1] for c in chunks if c[0] == b"fact"), None)
    data = next(c[1] for c in chunks if c[0] == b"data")
    if fact is not None and len(fact) >= 4:
        frames = struct.unpack_from("<I", fact, 0)[0]
    elif align:
        frames = len(data) // align
    else:
        return None
    return frames / rate if rate and frames else None


def _drifts(beats, bpm, seconds):
    return abs(beats / bpm * 60 - seconds) / seconds > _BEAT_DRIFT


def _keep_beats_in_step(buf, seconds, notes):
    """After a new tempo, the acid chunk's beat count must still describe the
    audio, or the edit leaves a chunk that contradicts itself (4 beats at 128
    bpm in 0.014 s). A count that no longer fits becomes the whole number the
    new tempo gives over the audio, or 0 (not stated) when no whole number
    fits; `notes` says which."""
    beats = struct.unpack_from("<I", buf, 12)[0]
    bpm = struct.unpack_from("<f", buf, 20)[0]       # as stored, float32
    if not (beats and bpm > 0 and seconds) or not _drifts(beats, bpm, seconds):
        return
    fit = round(bpm * seconds / 60)
    if fit and _drifts(fit, bpm, seconds):
        fit = 0
    struct.pack_into("<I", buf, 12, fit)
    if notes is not None:
        notes.append(f"the acid chunk's {beats} beat(s) do not fit {seconds:.3f} s "
                     f"at {bpm:g} bpm; its beat count is now "
                     f"{fit if fit else '0 (not stated)'}")


_MINOR = ("m", "min", "minor")
_MAJOR = ("M", "maj", "major")


def _mode_of(s):
    """'minor' or 'major' when a key name says so ('Am', 'F# minor', 'Cmaj'),
    else None. A pitch ('A3') or a bare note ('A') names no mode."""
    s = str(s).strip()
    i = 2 if len(s) > 1 and s[1] in "#b" else 1
    rest = s[i:].strip()
    if rest in _MINOR or rest.lower() in ("min", "minor"):
        return "minor"
    if rest in _MAJOR or rest.lower() in ("maj", "major"):
        return "major"
    return None


def _note_name(v):
    """A note value in one domain: the note name of its MIDI number (C3 =
    60), so 'C3', '60' and 60 all read the same. None stays None."""
    if v is None:
        return None
    m = _note_to_midi(str(v))
    return midi_note_to_name(m) if m is not None else str(v)


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
    f = io.BytesIO(data)
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
        pos += 8 + size
        if size & 1:
            pos += pad_step(f, pos, n, "little")   # 0 when the writer left it out
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


def edit_wav(data, changes, notes=None):
    """(new bytes, applied) for `changes`. What the edit could not store as
    asked, and wrote anyway, is said in `notes` when a list is given: the
    acid chunk holds a root note, so the mode of `key=Am` is dropped."""
    chunks, trailing = _iter_chunks(data)
    applied = []

    info_changes = {f: v for f, v in changes.items() if f.lower() in _INFO_TAGS}
    acid_changes = {f: v for f, v in changes.items() if f.lower() in _ACID_FIELDS}
    bext_changes = {f: v for f, v in changes.items() if f.lower() in _BEXT_FIELDS}
    smpl_changes = {f: v for f, v in changes.items() if f.lower() in _SMPL_FIELDS}
    unknown = (set(changes) - set(info_changes) - set(acid_changes)
               - set(bext_changes) - set(smpl_changes))
    if unknown:
        raise BadValue(f"WAV has no editable field(s): {', '.join(sorted(unknown))}")

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
                # a chunk this edit creates held no tempo, whatever its default
                old = round(struct.unpack_from("<f", buf, 20)[0], 3) if ac else None
                try:
                    bpm = float(value) if value else 0.0
                except ValueError:
                    raise BadValue(f"{field}={value!r}: not a number") from None
                struct.pack_into("<f", buf, 20, bpm)
                _keep_beats_in_step(buf, _audio_seconds(chunks), notes)
                applied.append((field, old, value))
            elif fl == "key":
                # the root note (offset 4) counts only when flag 0x02 says so
                flags = struct.unpack_from("<I", buf, 0)[0]
                root = struct.unpack_from("<H", buf, 4)[0]
                old = midi_note_to_name(root) if flags & 0x02 else None
                if value is None:
                    struct.pack_into("<H", buf, 4, 0)
                    struct.pack_into("<I", buf, 0, flags & ~0x02)
                    applied.append((field, old, None))
                else:
                    midi = _note_to_midi(str(value))
                    if midi is None:
                        raise EditError(f"unrecognized key {value!r}")
                    mode = _mode_of(value)
                    if mode and notes is not None:
                        notes.append(f"the acid chunk holds the root note only; "
                                     f"{mode!r} is not stored")
                    struct.pack_into("<H", buf, 4, midi)
                    struct.pack_into("<I", buf, 0, flags | 0x02)
                    # reported as what the chunk now holds, a pitch, so the
                    # report never claims a mode was written
                    applied.append((field, old, midi_note_to_name(midi)))
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

    # ---- smpl sampler root note ----
    if smpl_changes:
        sm = next((c for c in chunks if c[0] == b"smpl"), None)
        if sm:
            buf = bytearray(sm[1])
            if len(buf) < 36:
                raise EditError("smpl chunk too short to edit safely")
        else:
            period = round(1_000_000_000 / _fmt_sample_rate(chunks))
            buf = bytearray(struct.pack("<9I", 0, 0, period, 60, 0, 0, 0, 0, 0))
        for field, value in smpl_changes.items():
            old = struct.unpack_from("<I", buf, 12)[0]
            midi = _note_to_midi(str(value)) if value else 60
            if midi is None:
                raise EditError(f"unrecognized root note {value!r}")
            struct.pack_into("<I", buf, 12, midi)
            # old and new in one domain, note names: the chunk holds a MIDI
            # number and a request may say 'C3' or '60'
            applied.append((field, midi_note_to_name(old) if sm else None,
                            _note_name(midi)))
        if sm:
            sm[1] = bytes(buf)
        else:
            chunks.append([b"smpl", bytes(buf)])

    # ---- emit ----
    out = bytearray(b"RIFF\x00\x00\x00\x00WAVE")
    data_before = next(c[1] for c in _iter_chunks(data)[0] if c[0] == b"data")
    for cid, payload in chunks:
        out += cid + struct.pack("<I", len(payload)) + payload
        if len(payload) & 1:
            out += b"\x00"
    riff_size = len(out) - 8  # container covers header+chunks, NOT trailing junk
    out += trailing
    struct.pack_into("<I", out, 4, riff_size)

    # verify audio survived untouched
    data_after = next(c[1] for c in _iter_chunks(bytes(out))[0] if c[0] == b"data")
    if data_after != data_before:
        raise EditError("internal: audio data changed during rewrite (aborted)")
    return bytes(out), applied


# identifying / descriptive metadata chunks stripped by strip_wav. Functional
# chunks (fmt, data, fact, smpl, inst, acid, cue, plst) are kept: they carry
# playback and loop info, not authorship.
_WAV_META_CHUNKS = {b"LIST", b"bext", b"iXML", b"cart", b"ID3 ", b"id3 ",
                    b"_PMX", b"aXML", b"AXML"}


def strip_wav(data):
    """Drop the descriptive/identifying metadata chunks (LIST/INFO tags, bext
    broadcast metadata, iXML, cart, embedded ID3, XMP) and re-emit. Audio and
    functional chunks are preserved, verified byte-for-byte. Returns
    (new_bytes, [dropped chunk ids]). Trailing bytes past the container are
    left as-is."""
    chunks, trailing = _iter_chunks(data)
    data_before = next((c[1] for c in chunks if c[0] == b"data"), None)
    kept, dropped = [], []
    for c in chunks:
        if c[0] in _WAV_META_CHUNKS:
            dropped.append(c[0].decode("latin1").strip())
        else:
            kept.append(c)
    out = bytearray(b"RIFF\x00\x00\x00\x00WAVE")
    for cid, payload in kept:
        out += cid + struct.pack("<I", len(payload)) + payload
        if len(payload) & 1:
            out += b"\x00"
    riff_size = len(out) - 8
    out += trailing
    struct.pack_into("<I", out, 4, riff_size)
    data_after = next((c[1] for c in _iter_chunks(bytes(out))[0]
                       if c[0] == b"data"), None)
    if data_after != data_before:
        raise EditError("internal: audio data changed during strip (aborted)")
    return bytes(out), dropped
