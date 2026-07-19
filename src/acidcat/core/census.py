"""Corpus census: walk a directory tree of RIFF-family audio files and answer
structural questions empirically -- a chunk-id histogram, the container-variant
and format-tag distributions, and flags for the open-question chunks.

Built to run over millions of files, read-only, and gentle on the machine:

  * the traversal never stats a file it will not open -- it filters on the
    directory entry (``d_type`` + name), so a non-matching file costs no syscall
    beyond the one ``getdents`` that listed its directory;
  * it never follows a symlink loop or a same-device bind-mount back onto itself
    (a visited ``(st_dev, st_ino)`` set on every directory entered), never
    descends an ``autofs`` mountpoint (read from the mount table, never probed),
    and never opens a FIFO/socket/device (regular-file gate);
  * each file is dissected with positioned reads (``pread``) of only the chunk
    headers -- no audio payload is ever read -- with readahead suppressed so a
    header peek does not pull 128 KB, and the touched pages dropped from the
    cache afterwards so a census does not evict the working set;
  * one bad entry is skipped and counted, never fatal.

The RIFF walk targets the open-questions catalogue from acidcat's WAV/RIFF
research: every FOURCC seen with a count and first-seen path, the format-tag and
container-variant distributions, LIST types, fact sizes, bext versions, and a
set of named flags (RIFX, BW64, Wave64, ID3-in-WAV, Pro Tools / ADM chunks, ...).
"""

import os
import struct
import sys
import threading

# platform capabilities, probed once
_HAS_FADVISE = hasattr(os, "posix_fadvise") and sys.platform.startswith("linux")
_HAS_NOATIME = hasattr(os, "O_NOATIME")

# positioned read: pread(2) is one syscall and needs no seek state, but is
# POSIX-only. On Windows fall back to lseek+read -- safe here because each file
# is opened and walked on a single thread, so its fd is never shared.
if hasattr(os, "pread"):
    def _pread(fd, n, offset):
        return os.pread(fd, n, offset)
else:
    def _pread(fd, n, offset):
        os.lseek(fd, offset, os.SEEK_SET)
        return os.read(fd, n)

# RIFF-family extensions worth opening. Extension-filtered off the dirent so a
# 1 M-file tree does not stat-and-open every unrelated file; the census is about
# RIFF containers. Compared case-folded.
_EXTS = frozenset({
    ".wav", ".wave", ".bwf", ".bw64", ".rf64", ".w64", ".acid",
    ".avi", ".ani", ".rmi", ".dls", ".sf2", ".cpt", ".ds", ".wav_",
})

# Wave64 RIFF GUID (little-endian on disk): 'riff' + fixed v1-UUID suffix.
_W64_RIFF_GUID = bytes.fromhex("726966662e91cf11a5d628db04c10000")

# pseudo-filesystems that are never worth (and sometimes dangerous to) walking.
_SKIP_DIRS = frozenset({"/proc", "/sys", "/dev", "/run"})

_MAX_CHUNKS = 4096             # a forged file cannot make the walk spin
_MAX_DEPTH = 512               # fallback recursion guard when st_ino is unreliable


def _safe_fourcc(cid):
    """A JSON-safe, histogram-stable key for a 4-byte chunk id. Printable ASCII
    ids pass through; anything else (garbage from a corrupt file) becomes a hex
    token so it groups cleanly and never injects a control byte into the output."""
    if all(0x20 <= b < 0x7F for b in cid):
        return cid.decode("ascii")
    return "hex:" + cid.hex()


def json_safe_path(path):
    """A path re-derived to guaranteed-valid Unicode text for JSON output: a
    POSIX filename that is not valid UTF-8 arrives here as a surrogate-escaped
    str, which ``json`` would emit as a lone surrogate; round-tripping through
    the original bytes with backslashreplace keeps the output well-formed."""
    if isinstance(path, bytes):
        return path.decode("utf-8", "backslashreplace")
    return os.fsencode(path).decode("utf-8", "backslashreplace")


def _autofs_mountpoints():
    """Mountpoints of type autofs, read from the kernel mount table. Read here,
    up front, because *probing* an autofs path (stat/scandir) triggers the
    automount -- the check would be the trigger."""
    skip = set()
    try:
        with open("/proc/mounts", encoding="utf-8", errors="surrogateescape") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[2] == "autofs":
                    skip.add(parts[1])
    except OSError:
        pass
    return skip


class ScanOptions:
    __slots__ = ("follow_symlinks", "one_file_system", "fadvise", "noatime")

    def __init__(self, follow_symlinks=False, one_file_system=False,
                 fadvise=True, noatime=False):
        self.follow_symlinks = follow_symlinks
        self.one_file_system = one_file_system
        self.fadvise = fadvise
        self.noatime = noatime


def walk_tree(roots, opts, exts=_EXTS):
    """Yield the path of every regular file under ``roots`` whose extension is in
    ``exts``. Explicit-stack scandir (no recursion limit), loop-safe, boundary-
    aware, and free of any per-file stat: the extension test is a string op on
    the dirent name and the directory/regular test uses the cached ``d_type``."""
    autofs = _autofs_mountpoints() if os.name == "posix" else frozenset()
    visited = set()                                    # (st_dev, st_ino) of dirs entered
    follow = opts.follow_symlinks

    for root in roots:
        try:
            rst = os.stat(root)
        except OSError:
            continue
        root_dev = rst.st_dev
        ident = (rst.st_dev, rst.st_ino)
        if ident[1]:
            visited.add(ident)
        stack = [(os.fspath(root), 0)]
        while stack:
            dirpath, depth = stack.pop()
            if dirpath in _SKIP_DIRS or dirpath in autofs:
                continue
            try:
                it = os.scandir(dirpath)
            except OSError:
                continue                               # unreadable dir: skip, do not abort
            with it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=follow):
                            _maybe_push(entry, depth, follow, opts, root_dev,
                                        visited, autofs, stack)
                            continue
                        name = entry.name
                        dot = name.rfind(".")
                        if dot < 0 or name[dot:].casefold() not in exts:
                            continue
                        if entry.is_file(follow_symlinks=follow):
                            yield entry.path
                    except OSError:
                        continue                       # vanished / permission / bad entry


def _maybe_push(entry, depth, follow, opts, root_dev, visited, autofs, stack):
    """Decide whether to descend into a directory entry, enforcing the boundary,
    loop, and depth guards. Kept out of the hot loop for readability."""
    if entry.is_symlink() and not follow:
        return
    if depth + 1 > _MAX_DEPTH:
        return
    if entry.path in autofs:
        return
    # a directory is rare relative to files, so this stat is cheap; it drives
    # both the one-file-system boundary and the loop-identity check (which runs
    # unconditionally -- it is the only thing that stops a same-device bind mount
    # or a Windows junction loop that is_symlink() may not flag).
    try:
        st = entry.stat(follow_symlinks=follow)
    except OSError:
        return
    if opts.one_file_system and st.st_dev != root_dev:
        return
    ident = (st.st_dev, st.st_ino)
    if ident[1]:                                       # st_ino 0 = identity unreliable
        if ident in visited:
            return                                     # loop / same-device bind mount
        visited.add(ident)
    stack.append((entry.path, depth + 1))


class Census:
    """Accumulates the census. One instance per worker thread (each thread only
    touches its own), merged at the end -- so the hot path takes no lock."""

    def __init__(self):
        self.files = 0
        self.riff_files = 0
        self.errors = 0
        self.by_container = {}
        self.chunk_counts = {}
        self.chunk_first = {}
        self.fmt_tags = {}
        self.fmt_tag_example = {}
        self.list_types = {}
        self.fact_sizes = {}
        self.bext_versions = {}
        self.flags = {}

    @staticmethod
    def _bump(d, k):
        d[k] = d.get(k, 0) + 1

    def _flag(self, name, path, cap=25):
        lst = self.flags.setdefault(name, [])
        if len(lst) < cap:
            lst.append(json_safe_path(path))

    def merge(self, other):
        self.files += other.files
        self.riff_files += other.riff_files
        self.errors += other.errors
        for attr in ("by_container", "chunk_counts", "fmt_tags", "list_types",
                     "fact_sizes", "bext_versions"):
            dst, src = getattr(self, attr), getattr(other, attr)
            for k, v in src.items():
                dst[k] = dst.get(k, 0) + v
        self.chunk_first = {**other.chunk_first, **self.chunk_first}
        for k, v in other.fmt_tag_example.items():
            self.fmt_tag_example.setdefault(k, v)
        for name, paths in other.flags.items():
            lst = self.flags.setdefault(name, [])
            for p in paths:
                if len(lst) >= 25:
                    break
                lst.append(p)

    def census_file(self, path, fadvise=True, noatime=False):
        """Dissect one file into this accumulator. Positioned reads of chunk
        headers only; degrades on any malformed/short file, never raises."""
        self.files += 1
        flags = os.O_RDONLY
        if noatime and _HAS_NOATIME:
            try:
                fd = os.open(path, flags | os.O_NOATIME)
            except OSError:
                try:
                    fd = os.open(path, flags)
                except OSError:
                    self.errors += 1
                    return
        else:
            try:
                fd = os.open(path, flags)
            except OSError:
                self.errors += 1
                return
        try:
            if fadvise and _HAS_FADVISE:
                try:
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_RANDOM)
                except OSError:
                    pass
            self._census_fd(fd, path)
        except (OSError, struct.error):
            self.errors += 1
        finally:
            if fadvise and _HAS_FADVISE:
                try:
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                except OSError:
                    pass
            os.close(fd)

    def _census_fd(self, fd, path):
        head = _pread(fd, 16, 0)
        if len(head) < 12:
            return
        magic, form = head[0:4], head[8:12]
        if magic == b"RIFF" and form == b"WAVE":
            container, be = "RIFF", False
        elif magic == b"RIFX" and form == b"WAVE":
            container, be = "RIFX", True
            self._flag("rifx_big_endian", path)
        elif magic == b"RF64":
            container, be = "RF64", False
        elif magic == b"BW64":
            container, be = "BW64", False
            self._flag("bw64", path)
        elif head[0:16] == _W64_RIFF_GUID:
            self.riff_files += 1
            self._bump(self.by_container, "W64")
            self._flag("wave64", path)
            return                                     # GUID chunk walk: count only for v1
        elif magic == b"RIFF":
            container, be = "RIFF:" + form.decode("latin1"), False
        else:
            return                                     # not a RIFF-family file
        self.riff_files += 1
        self._bump(self.by_container, container)
        self._walk_riff(fd, path, be)

    def _walk_riff(self, fd, path, be):
        size_fmt = ">I" if be else "<I"
        u16 = ">H" if be else "<H"
        fsize = os.fstat(fd).st_size
        pos, n = 12, 0
        ds64_data = None                              # RF64/BW64 real data size
        while pos + 8 <= fsize and n < _MAX_CHUNKS:
            hdr = _pread(fd, 8, pos)
            if len(hdr) < 8:
                break
            cid = hdr[0:4]
            try:
                csz = struct.unpack(size_fmt, hdr[4:8])[0]
            except struct.error:
                break
            n += 1
            fourcc = _safe_fourcc(cid)
            self._bump(self.chunk_counts, fourcc)
            self.chunk_first.setdefault(fourcc, json_safe_path(path))

            if cid == b"ds64":
                # RF64/BW64 carry the real 64-bit data size here (dataSize is a
                # u64 at payload offset 8); the data chunk then holds 0xFFFFFFFF.
                db = _pread(fd, 8, pos + 16)
                if len(db) == 8:
                    ds64_data = struct.unpack("<Q", db)[0]
            elif cid == b"fmt ":
                tag = self._peek_u16(fd, pos + 8, u16)
                if tag is not None:
                    self._bump(self.fmt_tags, tag)
                    self.fmt_tag_example.setdefault(tag, json_safe_path(path))
                    if tag == 0x0039:
                        self._flag("fmt_tag_0x0039", path)
                    elif 0x0101 <= tag <= 0x0103:
                        self._flag("ibm_format_0x%04x" % tag, path)
            elif cid == b"LIST":
                lt = _pread(fd, 4, pos + 8)
                if len(lt) == 4:
                    lts = _safe_fourcc(lt)
                    self._bump(self.list_types, lts)
                    if lts in ("wavl", "slnt"):
                        self._flag("list_" + lts, path)
            elif cid == b"fact":
                self._bump(self.fact_sizes, csz)
                if csz != 4:
                    self._flag("fact_not_4_bytes", path)
            elif cid == b"CSET":
                self._flag("cset", path)
            elif cid == b"bext":
                # BWF version is a u16 at payload offset 346 (after the 256+32+32
                # +10+8-byte fixed fields and the two 4-byte TimeReference words),
                # just before the 64-byte UMID.
                v = self._peek_u16(fd, pos + 8 + 346, "<H")
                if v is not None:
                    self._bump(self.bext_versions, v)
            elif cid in (b"id3 ", b"ID3 "):
                self._flag("id3_in_wav", path)
            elif cid == b"r64m":
                self._flag("r64m", path)
            elif cid in (b"qlty", b"link"):
                self._flag(fourcc.strip(), path)
            elif cid in (b"minf", b"elm1", b"elmo", b"regn", b"ovwf"):
                self._flag("protools_" + fourcc.strip(), path)
            elif cid in (b"chna", b"axml", b"aXML"):
                self._flag("adm_" + fourcc.strip(), path)

            real = csz
            if csz == 0xFFFFFFFF and cid == b"data" and ds64_data is not None:
                real = ds64_data                       # resolve the RF64 sentinel
            step = 8 + real + (real & 1)               # pad odd sizes to even
            if step <= 8 or real == 0xFFFFFFFF:
                break                                  # garbage/sentinel size: stop cleanly
            pos += step

    def _peek_u16(self, fd, off, fmt):
        try:
            b = _pread(fd, 2, off)
            return struct.unpack(fmt, b)[0] if len(b) == 2 else None
        except (OSError, struct.error):
            return None

    def result(self, top=None):
        chunks = sorted(self.chunk_counts.items(), key=lambda kv: -kv[1])
        rare = [[c, n, self.chunk_first.get(c, "")] for c, n in chunks if n <= 5]
        hist = chunks[:top] if top else chunks
        return {
            "files_opened": self.files,
            "riff_family_files": self.riff_files,
            "errors": self.errors,
            "distinct_chunks": len(self.chunk_counts),
            "containers": dict(sorted(self.by_container.items(),
                                      key=lambda kv: -kv[1])),
            "chunk_histogram": {c: n for c, n in hist},
            "rare_chunks": rare,
            "format_tags": {"0x%04x" % t: n for t, n in
                            sorted(self.fmt_tags.items(), key=lambda kv: -kv[1])},
            "format_tag_examples": {"0x%04x" % t: p
                                    for t, p in self.fmt_tag_example.items()},
            "list_types": self.list_types,
            "fact_sizes": {str(k): v for k, v in self.fact_sizes.items()},
            "bext_versions": {str(k): v for k, v in self.bext_versions.items()},
            "flags": self.flags,
        }


def detect_rotational(path, default=False):
    """True if ``path`` lives on a spinning disk, per Linux sysfs. Best-effort:
    returns ``default`` on WSL2/drvfs, macOS, Windows, or any lookup failure
    (WSL2 reports the virtual block device, so callers should treat auto as a
    hint, not truth)."""
    if not sys.platform.startswith("linux"):
        return default
    try:
        dev = os.stat(path).st_dev
        maj, minr = os.major(dev), os.minor(dev)
        with open("/sys/dev/block/%d:%d/queue/rotational" % (maj, minr)) as f:
            return f.read().strip() == "1"
    except OSError:
        return default


def default_workers(root, io_hint="auto"):
    """Worker count: 1 on a spinning disk (concurrent seeks thrash), a small
    multiple of the CPU count on SSD/NVMe (overlapping I/O hides latency)."""
    if io_hint == "hdd":
        return 1
    if io_hint == "auto" and detect_rotational(root, default=False):
        return 1
    return min(32, (os.cpu_count() or 4) * 4)


def run_census(roots, opts=None, jobs="auto", io_hint="auto", limit=None,
               progress=None):
    """Walk ``roots`` and return a merged Census. Single-threaded when ``jobs``
    is 1 (or a spinning disk is detected); otherwise a fixed worker pool reads
    files off a bounded queue while the main thread walks the tree, each worker
    folding into its own Census so the aggregation never locks."""
    import gc
    import queue

    opts = opts or ScanOptions()
    roots = [os.fspath(r) for r in roots]
    if jobs == "auto":
        jobs = default_workers(roots[0], io_hint) if roots else 1
    jobs = max(1, int(jobs))

    gc.disable()                                       # bulk ingest: no cycles to collect
    try:
        if jobs == 1:
            cx = Census()
            for path in walk_tree(roots, opts):
                cx.census_file(path, opts.fadvise, opts.noatime)
                if progress and cx.files % 5000 == 0:
                    progress(cx.files, cx.riff_files, cx.errors)
                if limit and cx.files >= limit:
                    break
            return cx

        q = queue.Queue(maxsize=jobs * 256)
        workers = []
        stop = object()

        def worker():
            local = Census()
            while True:
                path = q.get()
                if path is stop:
                    q.task_done()
                    workers_out.append(local)
                    return
                local.census_file(path, opts.fadvise, opts.noatime)
                q.task_done()

        workers_out = []
        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(jobs)]
        for t in threads:
            t.start()
        fed = 0
        for path in walk_tree(roots, opts):
            q.put(path)
            fed += 1
            if progress and fed % 5000 == 0:
                # threaded mode reports files *fed* to workers (a lock-free main-
                # thread counter); riff/error tallies land only after the merge
                progress(fed, -1, -1)
            if limit and fed >= limit:
                break
        for _ in threads:
            q.put(stop)
        for t in threads:
            t.join()
        merged = Census()
        for local in workers_out:
            merged.merge(local)
        return merged
    finally:
        gc.collect()
        gc.enable()
