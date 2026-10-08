"""What kind of thing is this file, and what should look at it next?

Three questions a reverse engineer asks before anything else: is this a single
file I understand, is it a bigger thing with files inside, or is it damaged
remains of either. This answers all three and names the verb that follows, so
an unknown file has an obvious next move instead of a dead end.

Built only on checks that measured cheap, on real corpora:

    magic at offset 0        0.08 ms/file   (97.5% of a real 3,229-file library)
    container signature sweep   76 ms/32 MB
    chunk-grid resync          1.2 s/32 MB
    statistical audio scan      13 s/32 MB   <- 174x the sweep; never run here

Deliberately NOT used: entropy variance across sampled windows as a
"container" signal. It reads well in theory and fails on real files -- a 286 MB
zip-based instrument library measures variance 0.000 (uniformly compressed,
indistinguishable from one big compressed file) while a small FLAC measures
1.563 (sampling artefact). Measured, rejected, and left out rather than shipped
as a plausible-sounding heuristic.
"""

import os

from acidcat.core.infra import sniff as sniffmod

# shapes this can return
SINGLE = "single"          # one file, a format we walk
CONTAINER = "container"    # a bigger thing with recognizable files inside
CHUNKED = "chunked"        # unknown format, but its chunk grid is readable
UNWALKED = "unwalked"      # identified, audio-related, no walker written yet
DAMAGED = "damaged"        # structure survives but the normal path cannot use it
OPAQUE = "opaque"          # no structure we can see; may still hold raw audio
FOREIGN = "foreign"        # identified, and not audio (a PDF in a sample pack)
EMPTY = "empty"

# formats that are containers by nature -- a disc image or sector dump holds
# files rather than being one
_CONTAINER_FORMATS = frozenset(("gcm", "wii", "cdxa", "cue", "n64rom", "snesrom"))

# things that turn up in sample libraries and are not audio. Naming them beats
# "unrecognized": 1.9% of a real corpus was this, mostly documents and art.
_FOREIGN_MAGICS = (
    (b"%PDF-", "PDF document"),
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"GIF8", "GIF image"),
    (b"\x00\x05\x16\x07", "macOS AppleDouble resource fork"),
    (b"\x00\x05\x16\x00", "macOS AppleDouble resource fork"),
    (b"{\\rtf", "RTF document"),
    (b"\xd0\xcf\x11\xe0", "MS Office (OLE) document"),
    (b"\x7fELF", "ELF binary"),
    (b"MZ", "DOS/Windows executable"),
)

# compressed wrappers are NOT foreign: an Ableton Live Pack is a gzip stream and
# several instrument formats are zips, so what is inside may well be audio.
# Calling a .alp "not audio" would be wrong.
_COMPRESSED_MAGICS = (
    (b"PK\x03\x04", "zip archive"),
    (b"\x1f\x8b", "gzip stream"),
    (b"BZh", "bzip2 stream"),
    (b"\xfd7zXZ\x00", "xz stream"),
)


# Formats found in real preset libraries that we identify but do not yet walk.
# "opaque" is the wrong answer for a file we can name: it sends the user to a
# statistical scan when the honest reply is "that is a Reaktor ensemble, and no
# walker exists for it". Every entry below was confirmed against specimens, not
# guessed from the extension -- .ens, for instance, looked unsupported and turns
# out to already walk as `ni`.
_UNWALKED_MAGICS = (
    (b"NRKT", "Reaktor instrument/ensemble"),
    # Absynth banks carry the tag in four byte orders across versions
    (b"MalC", "Absynth bank"),
    (b"malc", "Absynth bank"),
    (b"ClaM", "Absynth bank"),
    (b"clam", "Absynth bank"),
)

# XML-backed formats (BFD3 grooves/palettes, assorted .preset files) are worth
# separating from opaque binary: the user can simply read them.
_XML_ROOTS = (b"<?xml", b"<root", b"<Root", b"<ROOT")


def _is_text(head):
    """True if `head` reads as text in a common encoding.

    Deliberately conservative: it decides whether to STOP calling something
    opaque binary, so a false positive here would mislabel a real binary. UTF-8
    or UTF-16 that decodes cleanly and is overwhelmingly printable qualifies;
    anything with a NUL in a byte-oriented encoding does not.
    """
    if not head:
        return False
    for bom, enc in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if head.startswith(bom):
            try:
                head[2:].decode(enc, "strict")
                return True
            except UnicodeDecodeError:
                # a truncated read can split a surrogate pair; that is not
                # evidence against the file being text
                return b"\x00\x00" not in head[2:]
    if b"\x00" in head:
        return False
    try:
        text = head.decode("utf-8", "strict")
    except UnicodeDecodeError:
        try:                       # a multi-byte char straddling the read edge
            text = head[:-4].decode("utf-8", "strict")
        except UnicodeDecodeError:
            return False
    if not text:
        return False
    printable = sum(1 for c in text if c.isprintable() or c in "\t\r\n")
    return printable / len(text) >= 0.95


def _unwalked(head):
    """(detail, next_verb, short_label) for a format we can name but not parse."""
    for magic, label in _UNWALKED_MAGICS:
        if head.startswith(magic):
            tag = magic.decode("ascii", "replace")
            return f"{label} ({tag}) -- no walker yet", "od", label
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    for root in _XML_ROOTS:
        if stripped.startswith(root):
            return ("XML document -- readable as text, no walker needed",
                    None, "XML document")
    if _is_text(head):
        # "opaque -- no magic, no embedded containers, no recoverable chunk
        # grid / next: locate" was the verdict on a 48-byte ASCII file. Every
        # README, sfz, cue sheet, .asd and text preset in a sample library got
        # called opaque binary and sent to the statistical audio scanner. Text
        # has no magic by definition; that is not the same as having no shape.
        return ("text -- readable as text, no walker needed", None, "text")
    return None


def _foreign(head):
    for magic, label in _FOREIGN_MAGICS:
        if head.startswith(magic):
            return label
    return None


def classify(path, *, deep=True):
    """Return a verdict dict for ``path``.

    keys: ``shape`` (one of the constants above), ``format`` (sniffed id or
    None), ``detail`` (a human sentence), ``next`` (the verb to run), and
    ``evidence`` (what the decision rested on).

    ``deep=False`` stops after the byte-cost-free checks -- magic and size --
    and never touches the container sweep or resync, for callers sorting a large
    tree where a per-file sweep would dominate.
    """
    size = os.path.getsize(path)
    if size == 0:
        return _verdict(EMPTY, None, "empty file", None, {"size": 0})
    with open(path, "rb") as f:
        head = f.read(64)

    fmt = sniffmod.sniff(path)
    ev = {"size": size, "sniff": fmt}

    # 1. a format we walk. Disc images and sector dumps are containers by nature.
    if fmt == "appledouble":
        # walked, but metadata about another file rather than audio: nothing
        # further to run on it
        return _verdict(FOREIGN, fmt, "macOS AppleDouble metadata sidecar -- not "
                                      "audio; inspect reads what it records",
                        None, ev)
    if fmt:
        if fmt in _CONTAINER_FORMATS:
            return _verdict(CONTAINER, fmt,
                            f"{fmt} image -- holds files rather than being one",
                            "extract", ev)
        return _verdict(SINGLE, fmt, f"{fmt}, a format acidcat walks",
                        "inspect", ev)

    # 2. identified, but not ours. Saying so beats "unrecognized".
    label = _foreign(head)
    if label:
        ev["identified_as"] = label
        return _verdict(FOREIGN, None, f"{label} -- not audio", None, ev)
    for magic, label in _COMPRESSED_MAGICS:
        if head.startswith(magic):
            ev["identified_as"] = label
            return _verdict(CONTAINER, None,
                            f"{label} -- compressed; what is inside may be audio",
                            "extract", ev)

    # 3. a format we can name but do not parse. Checked before generic triage so
    # a positive identification wins over "some chunk grid" -- a name tells the
    # user what they are holding and that the gap is ours, not the file's.
    named = _unwalked(head)
    if named:
        detail, nxt, label = named
        ev["identified_as"] = label
        return _verdict(UNWALKED, None, detail, nxt, ev)

    # 4. no magic we know, but is the structure readable anyway? Generic triage
    # walks an unknown chunk grid, and a surprising number of "unsupported"
    # formats are just IFF under another name -- a Reason .sxt is FORM/CAT/DESC
    # and walks today without anyone having written a walker for it.
    from acidcat.core.forensics import triage
    try:
        generic = triage.generic_walk(path)
    except Exception:
        generic = None
    if generic is not None:
        label, chunks, _warns = generic
        ev["triage_chunks"] = len(chunks)
        return _verdict(CHUNKED, None,
                        f"{label} -- {len(chunks)} chunk(s) readable without a "
                        f"format-specific walker", "inspect", ev)

    if not deep:
        return _verdict(OPAQUE, None, "no magic at offset 0", "od", ev)

    # 5. does it hold files? The signature sweep is 76 ms on 32 MB, so this is
    # affordable to ask of anything, at any size -- see _Bytes.
    with _Bytes(path) as data:
        from acidcat.core.forensics.locate import signature_sweep
        hits = signature_sweep(data)
        if hits:
            ev["embedded_containers"] = len(hits)
            ev["first_at"] = hits[0]["offset"]
            return _verdict(CONTAINER, None,
                            f"{len(hits)} audio container(s) embedded, first at "
                            f"0x{hits[0]['offset']:08x}", "locate", ev)

        # 6. is the structure damaged rather than absent? A chunk grid that
        # chains end-to-start is structure the normal path could not reach.
        from acidcat.core.forensics import resync
        rec = resync.recover(data, known_only=True)
        if rec["chain"]:
            ev["resync_chunks"] = len(rec["chain"])
            ev["resync_coverage"] = rec["coverage"]
            return _verdict(DAMAGED, None,
                            f"{len(rec['chain'])} chunk(s) recoverable by resync "
                            f"({rec['coverage']:.0%} of the file) -- the header is "
                            f"damaged but the grid survives", "inspect --resync", ev)

    # 7. nothing structural. It may still hold raw audio, which only the
    # statistical pass can find -- and that is expensive, so it is named, not run.
    return _verdict(OPAQUE, None,
                    "no magic, no embedded containers, no recoverable chunk grid",
                    "locate", ev)


# Above this we map instead of reading. The sweep and resync only need .find()
# and slicing, both of which mmap supports, so there is no reason to hold a
# multi-gigabyte image in RAM -- and no reason to cap it either. A cap here used
# to mean a 300 MB image with a WAV at 260 MB reported "no embedded containers",
# which is a confident wrong answer to the exact question this module exists for.
_MMAP_ABOVE = 32 * 1024 * 1024


class _Bytes:
    """Whole-file view: mmap for big files, plain bytes for small ones."""

    def __init__(self, path):
        self._f = self._m = None
        size = os.path.getsize(path)
        self._f = open(path, "rb")
        if size > _MMAP_ABOVE:
            try:
                import mmap
                self._m = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
                self.data = self._m
                return
            except (OSError, ValueError, ImportError):
                pass                      # fall through to a plain read
        try:
            self.data = self._f.read()
        except BaseException:
            # __init__ raising means the object never reaches its `with`, so
            # __exit__ never runs and the descriptor leaks -- which on Windows
            # keeps the file locked. The mmap branch above is already guarded;
            # this one was not.
            self.close()
            raise

    def close(self):
        for obj in (self._m, self._f):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass

    def __enter__(self):
        return self.data

    def __exit__(self, *exc):
        self.close()
        return False


def _verdict(shape, fmt, detail, nxt, evidence):
    return {"shape": shape, "format": fmt, "detail": detail,
            "next": nxt, "evidence": evidence}
