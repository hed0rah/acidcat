"""What a walker reads: a Source, not a path.

A walker used to take a file path, open it, stat it and read it. That tied every
walk to the filesystem: walking bytes in memory (stdin, a carved region, a
decoded layer such as the YM inside an LHA archive) meant writing them to a temp
file first, 1.9x slower at 300 bytes and 400x at 64 MB, and RMID wrote its inner
MIDI file to disk to walk it with the MIDI walker.

A Source is the bytes and what little a walker needs besides them:

    size        how many bytes there are
    name        a display name; its extension is the one hint some formats need
    path        the file on disk, or None for bytes in memory
    view(o, n)  a zero-copy memoryview of up to n bytes at o, short at the end
    read(o, n)  the same bytes as `bytes`
    file()      a seekable, read-only file object over the bytes, for zipfile,
                gzip and the seek-based chunk iterators
    sibling(n)  another file beside this one (a PSF's _lib, a cue sheet's BIN),
                or None when it is absent or there is no directory to look in

Backends: MappedSource (a file, mmap'd, paged in on demand) and BytesSource
(bytes in memory).

Code that also serves callers holding a plain path uses the helpers at the
bottom (open_input, input_size, input_name, zip_open, gzip_open). They take a
path or a Source, and for a path they open the file exactly as before, so no
mapping outlives a call that did not ask for one.
"""

import io
import os

from acidcat.core.infra.mapped import map_file


class Source:
    """The interface; see the module docstring."""

    size = 0
    name = None
    path = None
    resolver = None

    def buffer(self):
        """The whole content as a bytes-like object (mmap or bytes)."""
        raise NotImplementedError

    def view(self, off, n):
        if off < 0 or n <= 0 or off >= self.size:
            return memoryview(b"")
        return memoryview(self.buffer())[off:min(off + n, self.size)]

    def read(self, off, n):
        return bytes(self.view(off, n))

    def file(self):
        return Reader(self)

    @property
    def ext(self):
        """The name's extension, lower-cased, with the dot ('' when none)."""
        return os.path.splitext(self.name or "")[1].lower()

    def sibling(self, name):
        return self.resolver(self, name) if self.resolver else None

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class BytesSource(Source):
    """Bytes in memory. It has no directory, so it has no siblings."""

    def __init__(self, data, name=None):
        self._data = data
        self.size = len(data)
        self.name = name

    def buffer(self):
        return self._data


class MappedSource(Source):
    """A file on disk, mapped read-only. Close it (or use `with`) when done."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self._data, self._close = map_file(path)
        self.size = len(self._data)
        self.resolver = directory_resolver

    def buffer(self):
        return self._data

    def close(self):
        self._close()


class Reader(io.RawIOBase):
    """A seekable, read-only file object over a Source's bytes."""

    def __init__(self, source):
        super().__init__()
        self._src = source
        self._pos = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, off, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            pos = off
        elif whence == io.SEEK_CUR:
            pos = self._pos + off
        elif whence == io.SEEK_END:
            pos = self._src.size + off
        else:
            raise ValueError(f"bad whence {whence!r}")
        if pos < 0:
            raise OSError("negative seek position")
        self._pos = pos
        return pos

    def readinto(self, b):
        chunk = self._src.view(self._pos, len(b))
        n = len(chunk)
        b[:n] = chunk
        self._pos += n
        return n

    def read(self, n=-1):
        if n is None or n < 0:
            n = max(0, self._src.size - self._pos)
        out = self._src.read(self._pos, n)
        self._pos += len(out)
        return out


def directory_resolver(source, name):
    """A sibling in the same directory as `source`, or None."""
    p = os.path.join(os.path.dirname(os.path.abspath(source.path)), name)
    return MappedSource(p) if os.path.isfile(p) else None


def as_source(x):
    """A Source for a path or a Source."""
    return x if isinstance(x, Source) else MappedSource(os.fspath(x))


# ── helpers for code that takes a path or a Source ─────────────────────

def open_input(x):
    """A readable, seekable file object for a path or a Source."""
    return x.file() if isinstance(x, Source) else open(x, "rb")


def input_size(x):
    """Byte count of a path or a Source."""
    return x.size if isinstance(x, Source) else os.path.getsize(x)


def input_name(x):
    """The path of a file on disk, else the Source's name ('' when none), so
    extension and directory checks keep working for both."""
    if isinstance(x, Source):
        return x.path or x.name or ""
    return os.fspath(x)


def zip_open(x):
    """A zipfile.ZipFile over a path or a Source. zipfile opens and closes a
    path itself; a Source is read through its Reader, which holds no
    descriptor."""
    import zipfile
    return zipfile.ZipFile(x.file() if isinstance(x, Source) else x)


def gzip_open(x):
    """A gzip.GzipFile over a path or a Source, closed by `with` either way."""
    import gzip
    if isinstance(x, Source):
        return gzip.GzipFile(fileobj=x.file())
    return gzip.open(x, "rb")
