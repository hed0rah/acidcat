"""Canonical format detection by magic bytes.

One sniffing routine shared by the format walkers (and available to any
command), so the per-verb magic tables cannot drift apart. ``sniff_bytes``
classifies a 16-byte head; ``sniff`` reads the head from disk and also
resolves the one ambiguous case, an ID3v2 tag that wraps a non-MP3
container (some tools prepend ID3 tags to WAV/AIFF/FLAC files).

The check order is part of the contract: RIFF/WAVE must be tried before
the RIFF/NIKS preset magic, and the MP4 ftyp probe before the ID3
fallbacks, or edge-case files reroute. Do not reorder.

Format ids returned (all lowercase strings):
    wav, rf64, aiff, aifc, midi, serum, bitwig, ncw, sf2, vital, mp4, ni,
    flac, ogg, mp3, mod, xm, it, id3-wrapped (an ID3 tag around a non-MP3
    container)
or None for anything unrecognized.

MOD has no leading signature (its magic is at offset 1080), so ``sniff``
confirms it from disk; ``sniff_bytes`` cannot classify a MOD from a head.
"""

from acidcat.core import mp3 as mp3mod
from acidcat.core import ncw as ncwmod

# containers an ID3v2 tag is known to wrap; the tag then does not make
# the file an MP3.
_ID3_WRAPPED_MAGICS = (b"RIFF", b"RF64", b"FORM", b"fLaC", b"MThd")


def sniff_bytes(head):
    """Classify the first bytes of a file (pass at least 16).

    Magic-only: an ID3v2 tag classifies as "mp3" here; use ``sniff`` to
    distinguish a tag that wraps a different container.
    """
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"sfbk":
        return "sf2"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"RMID":
        return "rmid"
    if len(head) >= 12 and head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff" if head[8:12] == b"AIFF" else "aifc"
    if len(head) >= 14 and head[:4] == b"MThd":
        return "midi"
    if len(head) >= 12 and head[:4] == b"RF64" and head[8:12] == b"WAVE":
        return "rf64"
    if head[:8] == b"XferJson":
        return "serum"
    if head[:4] == b"vawt":
        return "wt"
    if head[:4] == b"BtWg":
        return "bitwig"
    if head[:4] == b"CcnK":
        return "fxp"
    if head[:4] == b"CAT " and head[8:12] == b"REX2":
        return "rx2"
    if head[:4] == ncwmod.MAGIC:
        return "ncw"
    if head[:1] == b"{":
        return "vital"
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[12:16] == b"hsin" or head[:4] == b"-in-" \
            or (head[:4] == b"RIFF" and head[8:12] == b"NIKS"):
        return "ni"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:17] == b"Extended Module: ":
        return "xm"
    if head[:4] == b"IMPM":
        return "it"
    if head[:3] == b"ID3" or (len(head) >= 4
                              and mp3mod.decode_frame_header(head[:4]) is not None):
        return "mp3"
    return None


def _id3_wraps_other_container(filepath):
    """True when the leading ID3v2 tag is a wrapper around a different
    known container rather than the tag of an MPEG stream."""
    hdr = mp3mod.read_id3v2(filepath)
    if not hdr:
        return False  # "ID3" magic but an unreadable header; treat as an MP3 attempt
    with open(filepath, "rb") as f:
        f.seek(hdr["total"])
        nxt = f.read(4)
    return nxt in _ID3_WRAPPED_MAGICS


def sniff(filepath):
    """Sniff a file on disk. Same ids as ``sniff_bytes`` plus
    "id3-wrapped" for an ID3v2 tag around a non-MP3 container."""
    with open(filepath, "rb") as f:
        head = f.read(20)  # 20 covers the 17-byte "Extended Module: " XM signature
    fmt = sniff_bytes(head)
    if fmt == "mp3" and head[:3] == b"ID3" and _id3_wraps_other_container(filepath):
        return "id3-wrapped"
    # a .sigmf-meta is JSON starting with '{', which sniff_bytes reads as vital;
    # the mandated extension reroutes it, exactly like the id3-wrapped demotion.
    if fmt == "vital" and filepath.lower().endswith(".sigmf-meta"):
        return "sigmf"
    # a ZIP whose archive holds multisample.xml is a Bitwig .multisample. This is
    # the one content-sniff that must peek inside the container (the local-file
    # header magic alone cannot tell it from any other zip).
    if fmt is None and head[:4] == b"PK\x03\x04" and _is_multisample(filepath):
        return "multisample"
    # an Arturia Analog Lab .labx is also a zip; its entries follow an
    # <Engine>/User|Factory/<Bank>/<Preset> layout of boost text archives. The
    # multisample check (an exact member name) is more specific, so it runs first.
    if fmt is None and head[:4] == b"PK\x03\x04" \
            and (filepath.lower().endswith(".labx") or _is_labx(filepath)):
        return "labx"
    # a free-format MPEG sync (bitrate index 0): sniff_bytes stays strict
    # because 16 bytes cannot confirm it; with the file in hand, accept only
    # when the constant frame length is measurable (a matching second sync).
    if fmt is None and len(head) >= 4 and _free_format_mp3(filepath, head):
        return "mp3"
    # S3M's 'SCRM' magic sits at 0x2C (outside the head), a disk-level confirm.
    # It runs before the MOD check: it is cheaper and more precise, and MOD's
    # offset-1080 heuristic can false-positive inside S3M pattern data.
    if fmt is None and _is_s3m(filepath):
        return "s3m"
    # SigMF pair members and bare IQ captures are headerless: accept them only
    # when no magic matched, keyed on the mandated / conventional extensions.
    if fmt is None:
        low = filepath.lower()
        if low.endswith(".sigmf-data") or low.endswith(".sigmf-meta"):
            return "sigmf"
        if low.endswith(_IQ_EXTS) or (low.endswith(".raw") and _gqrx_sniff(filepath)):
            return "iq"
    # ProTracker MOD has no leading signature; its only reliable magic sits at
    # offset 1080, so it can only be confirmed with the file in hand.
    if fmt is None and _is_mod(filepath):
        return "mod"
    return fmt


def _is_mod(filepath):
    from acidcat.core import tracker as tkmod
    try:
        with open(filepath, "rb") as f:
            return tkmod.is_mod(f.read(1084))
    except OSError:
        return False


def _is_s3m(filepath):
    from acidcat.core import tracker as tkmod
    try:
        with open(filepath, "rb") as f:
            return tkmod.is_s3m(f.read(48))
    except OSError:
        return False


# bare raw-IQ extensions (headerless): geometry comes from the extension itself.
_IQ_EXTS = (".cu8", ".c16", ".c8", ".cs8", ".cs16", ".cf32", ".cfile")


def _gqrx_sniff(filepath):
    from acidcat.core.walk import sigmf
    return sigmf._gqrx_name(filepath) is not None


def _free_format_mp3(filepath, head):
    hdr = mp3mod.decode_frame_header(head[:4], allow_free=True)
    if hdr is None or not hdr.get("free_format"):
        return False
    import os
    end = min(os.path.getsize(filepath), 2 * mp3mod._FREE_SCAN_CAP)
    with open(filepath, "rb") as f:
        return mp3mod._free_frame_length(f, 0, hdr, end) is not None


def _is_multisample(filepath):
    try:
        import zipfile
        with zipfile.ZipFile(filepath) as z:
            return "multisample.xml" in z.namelist()
    except Exception:
        return False


def _is_labx(filepath):
    """A zip whose entries follow <Engine>/User|Factory/<Bank>/<Preset> and hold
    boost text-serialization archives (Arturia Analog Lab bank export)."""
    try:
        import zipfile
        with zipfile.ZipFile(filepath) as z:
            for n in z.namelist()[:8]:
                if len(n.split("/")) >= 3 and ("/User/" in n or "/Factory/" in n):
                    if z.read(n)[:40].split(b" ", 1)[-1].startswith(
                            b"serialization::archive"):
                        return True
    except Exception:
        pass
    return False
