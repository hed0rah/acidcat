"""Forced parse: what every walker makes of a file none of them claims.

Lifted out of `commands/inspect.py` so the TUI can offer the same thing the CLI
offers with `--force`. The engine is pure -- a path in, a ranked list of
candidate dicts out -- and the two surfaces render it their own way.

Deliberately NOT a single answer. Walkers assume their magic rather than
verifying it, so a forced parse readily invents structure. These are leads, not
identifications.
"""

import os

from acidcat.core.primitives.notes import code_of
from acidcat.core.walk import walk_file

# the codes by which a walker says "these bytes are not really my format",
# chosen by code rather than by matching the message text
_WRONG_FORMAT = frozenset({"magic.mismatch", "parse.failed", "chunk.short",
                           "sibling.missing", "encoding.unknown"})


def _forced_candidates(filepath, deep):
    """Try every walker on a file none of them claims, and report what each one
    made of it -- ranked, with its own complaints attached.

    Deliberately NOT a single answer. Walkers assume their magic rather than
    verifying it, so a forced parse readily invents structure: pointed at an
    arbitrary blob, the MIDI walker reports an MThd chunk larger than the file
    and the FLAC walker reports a 'fLaC' magic that is not there. Picking a
    "winner" out of that would manufacture a false identification, which is
    worse than refusing. So this surfaces the candidates as leads for --format
    and lets the person decide.

    Each row carries: the chunk/field counts, whether the claimed sizes fit
    inside the real file (a parse claiming more bytes than exist is
    self-refuting), and the first thing the walker itself complained about.
    """
    from acidcat.core.walk import _WALKERS

    size = os.path.getsize(filepath)
    rows = []
    for fmt in _WALKERS:
        try:
            label, chunks, warns = walk_file(filepath, deep, fmt_override=fmt)
        except Exception:
            continue
        if not chunks:
            continue
        fits = all(c.get("offset", 0) + c.get("size", 0) <= size for c in chunks)
        ids_ok = all(str(c.get("id", "")).isprintable() for c in chunks)
        # the check a walker cannot talk its way past: if it reports a 4-byte id
        # at an offset, are those bytes actually there? A walker that assumes
        # its magic (FLAC reporting 'fLaC' over 03 13 a0 e0) fails this while
        # warning about nothing, which is exactly the silent fabrication that
        # would otherwise rank first.
        anchored = 0
        for c in chunks:
            cid = str(c.get("id", ""))
            if len(cid) == 4 and cid.isprintable():
                with open(filepath, "rb") as fh:
                    fh.seek(c.get("offset", 0))
                    if fh.read(4) == cid.encode("latin-1", "replace"):
                        anchored += 1
        complaint = next((w for w in warns if code_of(w) in _WRONG_FORMAT), "")
        rows.append({
            "format": fmt, "label": label,
            "chunks": len(chunks),
            "fields": sum(len(c.get("fields") or []) for c in chunks),
            "fits": fits, "ids_ok": ids_ok, "anchored": anchored,
            "complaint": complaint or (warns[0] if warns else ""),
        })
    # rank: a self-consistent parse that the walker did not complain about is
    # the strongest lead; a parse claiming bytes the file does not have is last
    # anchored ids first: bytes on disk beat a walker's silence. a parse that
    # invents its magic ranks below one that admits a problem but reads real ids.
    rows.sort(key=lambda r: (r["anchored"], r["fits"], r["ids_ok"],
                             r["fields"], r["chunks"]), reverse=True)
    return rows
