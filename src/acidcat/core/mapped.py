"""Read-only, random-access file bytes with bounded memory (the command layer's
answer to ``f.read()`` on untrusted input).

Slurping a whole file costs its full size in RAM before any parsing begins, and
a hard size cap would be the wrong fix -- a valid RF64/BW64 WAV legitimately
exceeds 4 GB. An ``mmap`` gives the same random access the commands need
(indexing, slicing, ``.find()``, ``struct.unpack_from``) while the OS pages
bytes in on demand, so peak memory no longer scales with file size.

Two mmap quirks callers must know:

  * iterating an mmap yields 1-byte ``bytes`` objects, not ints the way
    ``bytes`` iteration does; wrap the map in ``memoryview`` for byte-wise
    iteration (ints, zero-copy) or slice it (slices are real ``bytes``).
  * a zero-byte file cannot be mapped (``ValueError``); ``map_file`` falls
    back to ``b""`` so callers see one uniform bytes-like interface.
"""

import mmap


def map_file(path):
    """Open ``path`` for read-only random access without loading it into RAM.

    Returns ``(data, close)``: ``data`` is an mmap (or plain bytes when the
    file cannot be mapped) and ``close()`` releases the mapping. Raises OSError
    only when the file itself cannot be opened."""
    with open(path, "rb") as f:
        try:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            # ValueError: a zero-byte file. OSError: a filesystem that refuses
            # to map. Both fall back to an ordinary read -- the empty case is
            # free, and unmappable filesystems keep the old behavior.
            f.seek(0)
            return f.read(), (lambda: None)
    # the mmap holds its own reference to the file, so the descriptor above
    # can close; the mapping stays valid until close() below

    def close():
        try:
            mm.close()
        except BufferError:
            # a still-live memoryview references the map; the OS unmaps it
            # when that view is collected instead
            pass

    return mm, close
