"""Structured corruption: the inputs bitflipping rarely reaches.

Each mutation is `(data, rng, params=None) -> bytes`, deterministic for a given
rng, and returns the input unchanged when it does not apply. `ALL` maps a name
to `(category, fn)`; `REGISTRY` groups them as generic / riff / container.

The point is that random bitflipping mostly produces files a parser rejects in
the first eight bytes. These produce files that stay PLAUSIBLE for longer -- a
forged length that is large but not absurd, a required chunk dropped, a chunk
duplicated, a list nested past any sane depth -- which is where a parser has to
make a decision rather than bail, and therefore where it can be wrong.

Used by tests/test_mutation_robustness.py to hold acidcat to its contract: a
clean `Unsupported` or lint warnings on hostile input, never an uncaught
traceback and never a hang.
"""


import struct

_RIFF_MAGICS = (b"RIFF", b"RF64", b"FORM")


def _is_riff(d):
    return len(d) >= 12 and d[:4] in _RIFF_MAGICS


def _endian(d):
    return ">" if d[:4] == b"FORM" else "<"


# ── generic (any file) ───────────────────────────────────────────────

def truncate_half(d, r):
    return d[:max(1, len(d) // 2)]


def truncate_head(d, r):
    return d[:max(1, len(d) // 20)]  # header survives, body gone


def truncate_random(d, r):
    if len(d) < 4:
        return None
    return d[:r.randrange(1, len(d))]


def _mp(params, key, default):
    return (params or {}).get(key, default)


def bitflip(d, r, params=None):
    flips = max(1, int(_mp(params, "flips", 32)))
    b = bytearray(d)
    if not b:
        return bytes(b)
    for _ in range(min(flips, len(b) * 8)):
        b[r.randrange(len(b))] ^= 1 << r.randrange(8)
    return bytes(b)


def byte_run_scramble(d, r, params=None):
    run = max(1, int(_mp(params, "run", 16)))
    if len(d) < run + 1:
        return None
    b = bytearray(d)
    start = r.randrange(len(b) - run)
    for i in range(start, start + run):
        b[i] = r.randrange(256)
    return bytes(b)


def zero_tail(d, r, params=None):
    frac = float(_mp(params, "frac", 0.5))
    h = max(0, int(len(d) * (1 - frac)))
    return d[:h] + b"\x00" * (len(d) - h)


def junk_prefix(d, r, params=None):
    n = max(1, int(_mp(params, "bytes", 13)))
    return bytes(r.randrange(256) for _ in range(n)) + d


def append_junk(d, r, params=None):
    n = max(1, int(_mp(params, "bytes", 64)))
    return d + bytes(r.randrange(256) for _ in range(n))


def duplicate(d, r, params=None):
    return d * max(2, int(_mp(params, "times", 2)))


# ── RIFF / WAV / AIFF (chunked containers) ────────────────────────────

def riff_size_max(d, r):
    if not _is_riff(d):
        return None
    b = bytearray(d)
    struct.pack_into(_endian(d) + "I", b, 4, 0x7FFFFFFF)
    return bytes(b)


def riff_size_zero(d, r):
    if not _is_riff(d):
        return None
    b = bytearray(d)
    struct.pack_into(_endian(d) + "I", b, 4, 0)
    return bytes(b)


def first_chunk_size_max(d, r):
    if not _is_riff(d) or len(d) < 20:
        return None
    b = bytearray(d)
    struct.pack_into(_endian(d) + "I", b, 16, 0xFFFFFFF0)
    return bytes(b)


def first_chunk_size_huge_but_plausible(d, r):
    # a size just past the file end: classic off-by-a-lot overrun
    if not _is_riff(d) or len(d) < 20:
        return None
    b = bytearray(d)
    struct.pack_into(_endian(d) + "I", b, 16, len(d) * 4)
    return bytes(b)


def duplicate_first_chunk(d, r):
    if not _is_riff(d) or len(d) < 20:
        return None
    e = _endian(d)
    size = struct.unpack_from(e + "I", d, 16)[0]
    chunk = d[12:12 + 8 + size + (size & 1)]
    if not chunk or len(chunk) > len(d):
        return None
    return d[:12] + chunk + d[12:]


def insert_fake_chunk(d, r):
    if not _is_riff(d):
        return None
    e = _endian(d)
    fake = b"junk" + struct.pack(e + "I", 0xFFFFFF00)
    return d[:12] + fake + d[12:]


def unpad_odd_chunk(d, r):
    # give the first chunk an odd size and drop the pad byte, so the next chunk
    # header lands one byte off
    if not _is_riff(d) or len(d) < 20:
        return None
    b = bytearray(d)
    e = _endian(d)
    size = struct.unpack_from(e + "I", d, 16)[0]
    if size < 2:
        return None
    struct.pack_into(e + "I", b, 16, size - 1)
    return bytes(b)


def deep_nest_list(d, r):
    # a RIFF whose payload is deeply nested LIST chunks -- probes recursion /
    # stack depth in walkers that descend LIST/adtl trees
    depth = 4000
    inner = b"INFOIART" + struct.pack("<I", 2) + b"hi"
    blob = inner
    for _ in range(depth):
        body = b"LIST" + struct.pack("<I", len(blob) + 4) + b"adtl" + blob
        blob = body
    return b"RIFF" + struct.pack("<I", len(blob) + 4) + b"WAVE" + blob


def drop_fmt(d, r):
    # remove the fmt chunk from a WAVE, leaving data orphaned
    if not (_is_riff(d) and d[8:12] == b"WAVE"):
        return None
    i = d.find(b"fmt ")
    if i < 0:
        return None
    size = struct.unpack_from("<I", d, i + 4)[0]
    end = i + 8 + size + (size & 1)
    if end > len(d):
        return None
    return d[:i] + d[end:]


# ── container magic ───────────────────────────────────────────────────

def swap_magic(d, r):
    return b"XXXX" + d[4:] if len(d) >= 4 else None


def flip_magic_bit(d, r):
    if len(d) < 1:
        return None
    b = bytearray(d)
    b[0] ^= 0x20
    return bytes(b)


def wrong_subtype(d, r):
    # RIFF____ but the form type (WAVE/AIFF) is garbage
    if not _is_riff(d):
        return None
    return d[:8] + b"XXXX" + d[12:]


REGISTRY = {
    "generic": [truncate_half, truncate_head, truncate_random, bitflip,
                byte_run_scramble, zero_tail, junk_prefix, append_junk,
                duplicate],
    "riff": [riff_size_max, riff_size_zero, first_chunk_size_max,
             first_chunk_size_huge_but_plausible, duplicate_first_chunk,
             insert_fake_chunk, unpad_odd_chunk, deep_nest_list, drop_fmt],
    "container": [swap_magic, flip_magic_bit, wrong_subtype],
}

ALL = {fn.__name__: (cat, fn) for cat, fns in REGISTRY.items() for fn in fns}

# knobs for the mutations that have a genuine tunable dimension. the rest are
# one-shot structural pokes with nothing meaningful to adjust.
MUTATION_PARAMS = {
    "bitflip": [{"name": "flips", "kind": "int", "min": 1, "max": 512, "step": 8, "value": 32}],
    "byte_run_scramble": [{"name": "run", "kind": "int", "min": 4, "max": 256, "step": 4, "value": 16}],
    "zero_tail": [{"name": "frac", "kind": "float", "min": 0.1, "max": 0.9, "step": 0.05, "value": 0.5}],
    "junk_prefix": [{"name": "bytes", "kind": "int", "min": 1, "max": 512, "step": 8, "value": 13}],
    "append_junk": [{"name": "bytes", "kind": "int", "min": 1, "max": 4096, "step": 16, "value": 64}],
    "duplicate": [{"name": "times", "kind": "int", "min": 2, "max": 6, "step": 1, "value": 2}],
}
