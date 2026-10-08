"""A PS1 / CD-XA disc image's audio: which files hold it and how to decode them.

The disc's ISO 9660 tree names its audio by extension: .STR/.XA are the
interleaved XA-ADPCM soundtrack, .VB is a raw SPU sound bank, .VAG a single
SPU sample with a header. Moved here from the TUI in 2.0, which now browses
the entries without knowing what any of them is.
"""

from acidcat.core.codecs import cdxa, vag
from acidcat.core.containers import iso9660

# kind -> (what it is, as the disc browser lists it; the role it counts under)
KINDS = {
    "XA": ("soundtrack (XA)", "soundtrack"),
    "VB": ("sound bank (SPU)", "bank"),
    "VAG": ("sample (SPU)", "bank"),
}


def catalog(path):
    """The audio entries of `path` if it is a CD-XA disc image with named
    audio, else []: what decides whether a disc opens into its browser."""
    try:
        info = cdxa.detect_cd_image(path)
    except Exception:
        return []
    if not info or not info.get("xa"):
        return []
    return audio_entries(path)


def audio_entries(path):
    """The disc's audio files: each ISO 9660 entry plus `kind`, `what` and
    `role`. Empty when the image has no readable file system."""
    entries = []
    try:
        for ent in iso9660.walk(path):
            up = ent["path"].upper()
            kind = ("XA" if up.endswith((".STR", ".XA"))
                    else "VB" if up.endswith(".VB")
                    else "VAG" if up.endswith(".VAG") else None)
            if kind:
                what, role = KINDS[kind]
                entries.append({**ent, "kind": kind, "what": what, "role": role})
    except Exception:
        return []
    return entries


def decode_entry(path, ent, preview=False):
    """(pcm_bytes, {channels, rate, bits}) for one entry of `audio_entries`,
    or None. `preview` caps the length for a fast audition."""
    try:
        if ent["kind"] == "XA":
            count = (ent["size"] + 2047) // 2048
            return cdxa.decode_range(path, ent["lba"], count,
                                     max_audio=180 if preview else None)
        raw = iso9660.read_file(path, ent)
        if ent["kind"] == "VB":
            if preview:
                raw = raw[:24 * 1024]
            pcm = vag.decode_spu(raw, stop_on_end=False)
            return pcm, {"channels": 1, "rate": 22050, "bits": 16}
        info = vag.parse_vag(raw)                     # VAG
        data = info["data"][:24 * 1024] if preview else info["data"]
        return vag.decode_spu(data), {"channels": 1, "rate": info["rate"], "bits": 16}
    except Exception:
        return None
