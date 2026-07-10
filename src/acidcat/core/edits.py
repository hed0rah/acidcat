"""Metadata editors for acidcat's write capability.

Each editor takes the file bytes and a `changes` dict {field: value} (value None
clears the field) and returns (new_bytes, applied) where applied is a list of
(field, old, new) for the dry-run diff. Field names are the same ones acidcat
displays, so editing is WYSIWYG. An unsupported field raises EditError rather
than silently doing nothing.

WAV/RIFF editing lives in edit_riff.py (spec-sensitive); this module covers the
JSON and tag-library formats where correctness is straightforward.
"""

import json
import os
import tempfile


class EditError(ValueError):
    """A requested edit cannot be applied (unsupported field, wrong format, ...)."""


# ── Vital (bare JSON) ──────────────────────────────────────────────

_VITAL_FIELDS = {
    "name": "preset_name", "preset_name": "preset_name", "title": "preset_name",
    "author": "author", "creator": "author", "artist": "author",
    "comment": "comments", "comments": "comments", "description": "comments",
    "style": "preset_style", "category": "preset_style",
}


def edit_vital(data, changes):
    """Edit a Vital preset's top-level metadata. Re-serializing preserves the
    full synth state (every other key is left untouched)."""
    obj = json.loads(data)
    if not isinstance(obj, dict) or "synth_version" not in obj:
        raise EditError("not a Vital preset")
    applied = []
    for field, value in changes.items():
        key = _VITAL_FIELDS.get(field.lower())
        if key is None:
            raise EditError(f"Vital preset has no editable field {field!r}")
        old = obj.get(key)
        obj[key] = "" if value is None else str(value)
        applied.append((field, old, obj[key]))
    return json.dumps(obj).encode("utf-8"), applied


# ── Bitwig preset metadata (length-prefixed tagged block) ──────────

import struct as _struct

_BITWIG_FIELDS = {
    "creator": b"creator", "author": b"creator",
    "comment": b"comment", "description": b"comment",
    "tags": b"tags",
    "name": b"device_name", "device": b"device_name",
    "category": b"device_category", "preset_category": b"preset_category",
}


def edit_bitwig(data, changes):
    """Edit a Bitwig preset's meta strings by splicing the value and updating its
    u32-BE length prefix. EXPERIMENTAL: the file must be confirmed to reload in
    Bitwig; the caller keeps a backup. Only the first (top-level) occurrence of
    each key is touched."""
    if data[:4] != b"BtWg":
        raise EditError("not a Bitwig preset")
    out = bytearray(data)
    applied = []
    for field, value in changes.items():
        key = _BITWIG_FIELDS.get(field.lower())
        if key is None:
            raise EditError(f"Bitwig preset has no editable field {field!r}")
        marker = _struct.pack(">I", len(key)) + key + b"\x08"
        idx = out.find(marker)
        if idx < 0:
            raise EditError(f"field {field!r} not present in this preset")
        vp = idx + len(marker)
        vlen = _struct.unpack_from(">I", out, vp)[0]
        if vp + 4 + vlen > len(out):
            raise EditError(f"field {field!r} value overruns the file")
        old = out[vp + 4:vp + 4 + vlen].decode("utf-8", "replace")
        new_val = ("" if value is None else str(value)).encode("utf-8")
        out[vp:vp + 4 + vlen] = _struct.pack(">I", len(new_val)) + new_val
        applied.append((field, old, value))
    return bytes(out), applied


# ── Native Instruments presets ─────────────────────────────────────


def edit_ni(data, changes):
    """Edit NI preset metadata. Dispatches by container: nksf (RIFF/msgpack),
    ksd (zlib/XML), hsin (Massive/Absynth, cascading frame sizes)."""
    from acidcat.core import ni
    try:
        if ni.is_ni_nksf(data):
            return ni.edit_nksf(data, changes)
        if ni.is_ni_ksd(data):
            return ni.edit_ksd(data, changes)
        if ni.is_ni_hsin(data):
            if not hasattr(ni, "edit_hsin"):
                raise EditError("hsin (Massive/Absynth) writing is not available "
                                "yet in this build")
            return ni.edit_hsin(data, changes)
    except ValueError as e:
        raise EditError(str(e))
    raise EditError("unrecognized Native Instruments preset")


# ── tagged audio (mp3/flac/ogg/m4a via mutagen) ────────────────────

def _audio_digest(data):
    """(kind, fingerprint) of the audio payload in a tagged container, for
    verifying that a tag rewrite left the stream untouched. Byte hashes for
    formats whose audio region is byte-stable across a tag edit (flac: after
    the last metadata block; mp4: mdat payloads; mp3: between the leading
    ID3v2 and a trailing ID3v1); for ogg the header repaging legitimately
    renumbers pages, so the fingerprint is the audio pages' (serial, granule,
    length) sequence instead. fingerprint is None when there is nothing
    comparable (e.g. an mp4 read without its mdat)."""
    import hashlib
    mv = memoryview(data)
    h = hashlib.sha256()
    if data[:4] == b"fLaC":
        pos = 4
        while pos + 4 <= len(data):
            last = data[pos] & 0x80
            blen = int.from_bytes(data[pos + 1:pos + 4], "big")
            pos += 4 + blen
            if last:
                break
        h.update(mv[min(pos, len(data)):])
        return "flac", h.hexdigest()
    if len(data) >= 12 and data[4:8] == b"ftyp":
        from acidcat.core import mp4 as mp4mod
        found = False
        for b in mp4mod.iter_boxes(data):
            if b["depth"] == 0 and b["type"] == b"mdat" and not b["truncated"]:
                h.update(mv[b["offset"] + b["hdr"]:b["offset"] + b["size"]])
                found = True
        return "mp4", h.hexdigest() if found else None
    if data[:4] == b"OggS":
        from acidcat.core import ogg as oggmod
        return "ogg", tuple((p["serial"], p["granule"], p["data_len"])
                            for p in oggmod.iter_pages(data)
                            if p["granule"] != 0)
    # mp3: the frame run between the leading ID3v2 tag (if any) and a
    # trailing 128-byte ID3v1 block (if any); both may appear/disappear
    # across an edit, the frames in between must not change
    start = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) \
            | ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        start = min(10 + size + (10 if data[5] & 0x10 else 0), len(data))
    end = len(data)
    if end - start >= 128 and data[end - 128:end - 125] == b"TAG":
        end -= 128
    h.update(mv[start:end])
    return "mp3", h.hexdigest()


def _verify_audio_preserved(old, new):
    """Raise EditError when the audio payload changed across a tag rewrite.
    Metadata edits must never touch the stream; this is the tagged-format
    equivalent of the in-memory guards in edit_riff/edit_aiff."""
    okind, ofp = _audio_digest(old)
    nkind, nfp = _audio_digest(new)
    if ofp is None or nfp is None:
        return
    if okind != nkind or ofp != nfp:
        raise EditError("audio payload changed during the tag rewrite; "
                        "refusing to return corrupted audio")


# field -> mutagen "easy" key (the normalized cross-format interface)
_EASY_FIELDS = {
    "title": "title", "name": "title",
    "artist": "artist", "creator": "artist",
    "albumartist": "albumartist",
    "album": "album",
    "genre": "genre",
    "comment": "comment", "description": "comment",
    "date": "date", "year": "date",
    "bpm": "bpm",
    "key": "key", "initialkey": "key",
    "track": "tracknumber", "tracknumber": "tracknumber",
}
_easyid3_ready = False


def _register_easyid3_comment():
    """Teach mutagen's EasyID3 to read/write a plain comment (COMM), which it
    does not expose by default."""
    global _easyid3_ready
    if _easyid3_ready:
        return
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import COMM
    if "key" not in EasyID3.valid_keys:
        EasyID3.RegisterTextKey("key", "TKEY")
    if "comment" not in EasyID3.valid_keys:
        def _get(id3, _):
            return [c.text[0] for c in id3.getall("COMM")
                    if c.desc == "" and c.text]

        def _set(id3, _, value):
            id3.delall("COMM")
            id3.add(COMM(encoding=3, lang="eng", desc="", text=value))

        def _del(id3, _):
            id3.delall("COMM")
        EasyID3.RegisterKey("comment", _get, _set, _del)
    _easyid3_ready = True


def _apply_custom_frames(tmp, suffix, changes):
    """Set custom frames on the temp file. Keys look like 'txxx:MOOD' (a
    user-defined ID3 text frame) or 'wxxx:URL'. Maps to the per-format
    equivalent: ID3 TXXX/WXXX for mp3/aiff, a plain Vorbis comment for flac/ogg
    (which are natively arbitrary key=value), a freeform atom for m4a.
    Returns [(field, old, new)]."""
    import mutagen
    m = mutagen.File(tmp)
    if m is None:
        raise EditError("mutagen could not read this audio file")
    cls = m.__class__.__name__
    is_id3 = cls in ("MP3", "AIFF", "WAVE") or (
        getattr(m, "tags", None) is not None
        and m.tags.__class__.__name__ == "ID3")
    applied = []
    for field, value in changes.items():
        kind, _, desc = field.partition(":")
        kind = kind.lower()
        if not desc:
            raise EditError(f"custom frame {field!r} needs a name, e.g. txxx:MOOD=value")
        clearing = value is None or value == ""
        old = None
        if is_id3:
            from mutagen.id3 import TXXX, WXXX
            if m.tags is None:
                m.add_tags()
            key = "TXXX" if kind == "txxx" else "WXXX"
            keep = []
            for fr in m.tags.getall(key):
                if fr.desc == desc:
                    val = getattr(fr, "text", None) or getattr(fr, "url", None)
                    old = val[0] if isinstance(val, list) and val else val
                else:
                    keep.append(fr)
            m.tags.delall(key)
            for fr in keep:
                m.tags.add(fr)
            if not clearing:
                m.tags.add(TXXX(encoding=3, desc=desc, text=[str(value)])
                           if kind == "txxx" else
                           WXXX(encoding=3, desc=desc, url=str(value)))
        elif cls in ("FLAC", "OggVorbis", "OggOpus", "OggFLAC"):
            cur = m.get(desc)
            old = cur[0] if isinstance(cur, list) and cur else cur
            if clearing:
                m.pop(desc, None)
            else:
                m[desc] = [str(value)]
        elif cls == "MP4":
            from mutagen.mp4 import MP4FreeForm
            key = f"----:com.acidcat:{desc}"
            cur = m.get(key)
            old = bytes(cur[0]).decode("utf-8", "replace") if cur else None
            if clearing:
                m.pop(key, None)
            else:
                m[key] = [MP4FreeForm(str(value).encode("utf-8"))]
        else:
            raise EditError(f"custom frames are not supported for {cls}")
        applied.append((field, old, value))
    m.save()
    return applied


def strip_tagged(data, suffix):
    """Delete every tag from a tagged-audio container (mp3/flac/ogg/opus/m4a)
    via mutagen, which owns the on-disk tag spec. Round-trips through a temp
    file so the caller still gets bytes. Returns (new_bytes, [removed keys])."""
    try:
        import mutagen
    except ImportError:
        raise EditError("stripping tagged audio needs mutagen (pip install mutagen)")
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        audio = mutagen.File(tmp)
        if audio is None:
            raise EditError("mutagen could not read this audio file")
        removed = sorted(audio.tags.keys()) if audio.tags else []
        audio.delete()          # removes the tag block from the file on disk
        with open(tmp, "rb") as r:
            new = r.read()
        _verify_audio_preserved(data, new)
        return new, removed
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def edit_tagged(data, suffix, changes):
    """Edit tags on an mp3/flac/ogg/opus/m4a via mutagen (which owns the on-disk
    tag spec). Round-trips through a temp file so the caller still gets bytes."""
    try:
        import mutagen
    except ImportError:
        raise EditError("editing tagged audio needs mutagen (pip install mutagen)")
    try:
        _register_easyid3_comment()
    except Exception:
        pass
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # ensure mutagen re-reads a fully-written file
        # custom frames (txxx:DESC / wxxx:DESC) go through the full interface;
        # everything else through the normalized "easy" one.
        custom = {k: v for k, v in changes.items()
                  if k.lower().startswith(("txxx:", "wxxx:"))}
        easy = {k: v for k, v in changes.items() if k not in custom}
        applied = []
        if easy:
            audio = mutagen.File(tmp, easy=True)
            if audio is None:
                raise EditError("mutagen could not read this audio file")
            for field, value in easy.items():
                key = _EASY_FIELDS.get(field.lower())
                if key is None:
                    raise EditError(f"tagged audio has no editable field {field!r} "
                                    f"(custom ID3 frames use txxx:NAME=value)")
                old = audio.get(key)
                old = old[0] if isinstance(old, list) and old else old
                try:
                    if value is None or value == "":
                        audio.pop(key, None)
                    else:
                        audio[key] = [str(value)]
                except (KeyError, ValueError, TypeError):
                    raise EditError(
                        f"{suffix.lstrip('.') or 'this format'} cannot store "
                        f"field {field!r}")
                applied.append((field, old, value))
            audio.save()
        if custom:
            applied += _apply_custom_frames(tmp, suffix, custom)
        with open(tmp, "rb") as r:
            new = r.read()
        _verify_audio_preserved(data, new)
        return new, applied
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
