"""One answer to "which bytes does this chunk occupy?".

There were nine, spelled four ways, across six modules: `payload_base` else
`offset + 8`, sometimes with an end of `base + size`, sometimes `base + size + 8`,
and once (commands/inspect.py) without consulting `payload_base` at all. Six
consumers independently re-deriving the same geometry is not a convention, it is
a coincidence, and the coincidence had already broken in two places.

It broke because one key was answering two different questions. RIFF's `size` is
the PAYLOAD length; MP4's `size` is the TOTAL box length, header included. Both
are reasonable, neither is wrong, and no single reader can serve both. So a
normalized chunk carries two ranges instead of one:

    extent   offset .. offset + extent_len      every byte the chunk occupies:
                                                header, payload, and any
                                                structural padding. The bytes no
                                                sibling may claim.
    payload  payload_base .. + payload_len      the bytes inside it: what field
                                                offsets are measured from, and
                                                what a recursive walk descends
                                                into.

and says which of those it actually knows:

    declared    the walker stated it
    defaulted   nobody stated it, so the documented default was applied and the
                arithmetic checked out
    invalid     the default was applied and the result does not fit

`invalid` is deliberately not repaired here. A geometry that was guessed and
happens to fit is still a guess, and this codebase does not trust an unverified
annotation just because it is plausible. A walker that cannot describe its own
chunks is a walker to fix, and marking it is how it gets found.

Nothing is removed: `size` keeps its original meaning and value, so every
existing consumer keeps working while they migrate to the explicit keys.
"""

DECLARED = "declared"
DEFAULTED = "defaulted"
INVALID = "invalid"
UNPOSITIONED = "unpositioned"

# The documented default, in one place at last: a chunk is a 4-byte tag and a
# 4-byte size, and its contents follow. Right for RIFF/IFF and everything shaped
# like them, which is most of what this tool meets and not all of it.
DEFAULT_HEADER = 8


def normalize(chunks, filesize, parent_extent=None):
    """Guarantee the geometry keys on every chunk in `chunks`, in place.

    `parent_extent` is (offset, length) when these chunks were walked out of a
    slice of a bigger file, so "fits in the file" can be checked against the
    thing they actually live in.
    """
    lo, hi = 0, filesize
    if parent_extent is not None:
        lo, hi = parent_extent[0], parent_extent[0] + parent_extent[1]
    for c in chunks:
        _one(c, lo, hi)
        # a decoded layer's chunks live in the layer, not in the file
        if isinstance(c.get("layer"), dict) and c.get("layer_chunks"):
            normalize(c["layer_chunks"], c["layer"]["length"])
    return chunks


def _one(c, lo, hi):
    off, size = c.get("offset"), c.get("size")
    if not isinstance(off, int) or not isinstance(size, int):
        # A derived chunk with no byte position: real, and not a range. Saying
        # so beats inventing one.
        c.setdefault("geometry", UNPOSITIONED)
        return c

    pb = c.get("payload_base")
    declared = isinstance(pb, int)
    base = pb if declared else off + DEFAULT_HEADER
    header = base - off

    # A walker that already speaks the new vocabulary is believed; one that does
    # not gets `size` read as the payload, which is what the default rule and
    # every field offset in the codebase already assume.
    pay_len = c.get("payload_len")
    if not isinstance(pay_len, int):
        pay_len = size
    ext_len = c.get("extent_len")
    if not isinstance(ext_len, int):
        ext_len = header + pay_len

    ok = (header >= 0
          and pay_len >= 0
          and base >= off
          and lo <= off
          and base + pay_len <= hi
          and off + ext_len <= hi
          # a payload_base that is present but not an int is walker garbage:
          # the default was substituted above so the arithmetic held, but the
          # annotation itself is wrong and must read as INVALID, not raise
          # TypeError on `base - off` (every other key was already guarded)
          and (pb is None or declared))

    c["payload_base"] = base
    c["payload_len"] = pay_len
    c["extent_len"] = ext_len
    c["geometry"] = (DECLARED if declared else DEFAULTED) if ok else INVALID
    return c


def payload_of(chunk):
    """(offset, length) of a chunk's contents. The one reader, replacing nine.

    Falls back to the default rule for a chunk that never passed through
    `normalize` -- a walker's raw output, a hand-built dict in a test -- so this
    is safe to adopt everywhere before every producer is migrated.
    """
    base = chunk.get("payload_base")
    if base is None:
        base = (chunk.get("offset") or 0) + DEFAULT_HEADER
    n = chunk.get("payload_len")
    if not isinstance(n, int):
        n = chunk.get("size") or 0
    return base, n


def extent_of(chunk):
    """(offset, length) of every byte the chunk occupies, header included."""
    off = chunk.get("offset") or 0
    n = chunk.get("extent_len")
    if isinstance(n, int):
        return off, n
    base, pay = payload_of(chunk)
    return off, (base - off) + pay


def is_trustworthy(chunk):
    """Did anyone actually state this geometry, or did we fill it in?"""
    return chunk.get("geometry") == DECLARED
