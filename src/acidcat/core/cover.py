"""Embedded cover-art extract / embed / remove across MP3, FLAC, MP4/M4A, and
Ogg. Each format stores art differently (ID3 APIC frame, FLAC/Ogg PICTURE block,
MP4 'covr' atom), so this dispatches on the mutagen object type and hides the
difference behind extract/set/remove. mutagen owns the on-disk encoding."""

import base64
import os


class CoverError(Exception):
    pass


def _ext_mime(image_bytes):
    """Sniff (mime, ext) from the image magic; default jpeg."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp", "webp"
    return "image/jpeg", "jpg"


def _open(path):
    try:
        import mutagen
    except ImportError:
        raise CoverError("cover art needs mutagen (pip install mutagen)")
    m = mutagen.File(path)
    if m is None:
        raise CoverError("mutagen could not read this file")
    return m


def extract(path):
    """First embedded cover as (mime, bytes), or None."""
    m = _open(path)
    # FLAC / Ogg-FLAC: a real Picture list
    pics = getattr(m, "pictures", None)
    if pics:
        return pics[0].mime or "image/jpeg", bytes(pics[0].data)
    tags = getattr(m, "tags", None)
    if not tags:
        return None
    # MP4 'covr'
    if "covr" in tags:
        cov = tags["covr"][0]
        fmt = getattr(cov, "imageformat", None)
        mime = "image/png" if fmt == 14 else "image/jpeg"  # MP4Cover.FORMAT_PNG == 14
        return mime, bytes(cov)
    # ID3 APIC (mp3, id3-in-aiff/wav)
    for k in tags.keys():
        if k == "APIC" or k.startswith("APIC:"):
            apic = tags[k]
            return getattr(apic, "mime", "image/jpeg") or "image/jpeg", bytes(apic.data)
    # Ogg Vorbis/Opus: base64 METADATA_BLOCK_PICTURE
    mbp = tags.get("metadata_block_picture") if hasattr(tags, "get") else None
    if mbp:
        from mutagen.flac import Picture
        pic = Picture(base64.b64decode(mbp[0]))
        return pic.mime or "image/jpeg", bytes(pic.data)
    return None


def _make_flac_picture(image_bytes, mime):
    from mutagen.flac import Picture
    pic = Picture()
    pic.type = 3          # front cover
    pic.mime = mime
    pic.data = image_bytes
    return pic


def set_cover(path, image_bytes):
    """Embed image_bytes as the front cover, replacing any existing art."""
    m = _open(path)
    mime, _ = _ext_mime(image_bytes)
    cls = m.__class__.__name__
    if cls == "FLAC":
        m.clear_pictures()
        m.add_picture(_make_flac_picture(image_bytes, mime))
    elif cls == "MP4":
        from mutagen.mp4 import MP4Cover
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        m["covr"] = [MP4Cover(image_bytes, imageformat=fmt)]
    elif cls in ("MP3", "AIFF", "WAVE") or _is_id3(m):
        from mutagen.id3 import APIC
        if m.tags is None:
            m.add_tags()
        m.tags.delall("APIC")
        m.tags.add(APIC(encoding=3, mime=mime, type=3, desc="", data=image_bytes))
    elif cls in ("OggVorbis", "OggOpus", "OggFLAC", "OggTheora"):
        pic = _make_flac_picture(image_bytes, mime)
        m["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
    else:
        raise CoverError(f"embedding cover art into {cls} is not supported")
    m.save()


def remove_cover(path):
    """Remove embedded cover art. Returns True if something was removed."""
    m = _open(path)
    removed = False
    if getattr(m, "pictures", None):
        m.clear_pictures()
        removed = True
    tags = getattr(m, "tags", None)
    if tags is not None:
        if "covr" in tags:
            del tags["covr"]
            removed = True
        if hasattr(tags, "delall"):
            if any(k == "APIC" or k.startswith("APIC:") for k in tags.keys()):
                tags.delall("APIC")
                removed = True
        elif hasattr(tags, "get") and tags.get("metadata_block_picture"):
            del tags["metadata_block_picture"]
            removed = True
    if removed:
        m.save()
    return removed


def _is_id3(m):
    tags = getattr(m, "tags", None)
    return tags is not None and tags.__class__.__name__ == "ID3"
