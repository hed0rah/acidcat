"""Scope a verb to a byte range inside a larger file.

`locate` finds audio inside a disk image or a proprietary container, but the
structural verbs walk whole *files*. This bridges the two: it materializes the
requested range to a temp file so `inspect` can walk a region of an image the
same way it walks a standalone file, without making the user carve to disk by
hand first.

The range vocabulary matches `carve` and `od` -- --offset/--length/--end, --at
for anchored starts, and --region N for the Nth thing `locate` found -- so a
region moves between verbs unchanged.
"""

import contextlib
import os
import tempfile


def add_region_args(parser, region_help=None, addr=False):
    """Add the range-selection flags to a parser. With `addr` (2.0), --at also
    takes an ADDR and the 1.8 --offset/--length/--end are left to the alias
    layer, which rewrites them as --at @OFF+LEN."""
    if not addr:
        parser.add_argument("--offset", metavar="N",
                            help="Start of a byte range to work on (0x.. or decimal).")
        parser.add_argument("--length", metavar="N",
                            help="Length of that range, in bytes.")
        parser.add_argument("--end", metavar="N",
                            help="End of the range (exclusive), instead of --length.")
    parser.add_argument("--at", metavar="EXPR",
                        help=("An ADDR (@0x100+64, @0x100..0x200, RIFF/data) or a "
                              "search anchor: end[-N] | find:STR|0xHEX[+N] | "
                              "chunk:ID[+N] | 0xNN." if addr else
                              "Anchored start: 0xNN | end[-N] | find:STR|0xHEX[+N] "
                              "| chunk:ID[+N]."))
    parser.add_argument("--region", type=int, metavar="N",
                        help=region_help or
                        "Work on the Nth region `locate` reports (0-based), so a "
                        "blob found inside a larger image can be walked directly.")
    return parser


def _is_anchor(text):
    from acidcat.commands._addr import is_anchor
    return is_anchor(text)


def _int(text, what):
    try:
        return int(text, 0)
    except (ValueError, TypeError):
        raise ValueError(f"{what}: not an offset/length: {text!r}")


def resolve_range(args, path):
    """(start, length) for the requested range, or None if none was requested."""
    region = getattr(args, "region", None)
    at = getattr(args, "at", None)
    offset = getattr(args, "offset", None)
    length_s = getattr(args, "length", None)
    end_s = getattr(args, "end", None)

    if region is not None:
        from acidcat.core.forensics import locate as locatemod
        with open(path, "rb") as f:
            recs = locatemod.locate(f.read(), mode=getattr(args, "mode", "normal"))
        if not recs:
            raise ValueError("no regions located in this file")
        if not 0 <= region < len(recs):
            raise ValueError(f"--region {region} out of range "
                             f"(locate found {len(recs)}: 0..{len(recs) - 1})")
        r = recs[region]
        return r["offset"], r["length"]

    size = os.path.getsize(path)
    start = None
    if at is not None and not _is_anchor(at):
        from acidcat.commands import _addr
        return _addr.resolve(path, at)
    if at is not None:
        from acidcat.core.infra import bytefields as bf
        start = bf.resolve_offset(at, path, size)
    elif offset is not None:
        start = _int(offset, "--offset")
    if start is None and length_s is None and end_s is None:
        return None
    start = start or 0
    if end_s is not None:
        length = max(0, _int(end_s, "--end") - start)
    elif length_s is not None:
        length = _int(length_s, "--length")
    else:
        length = size - start
    return start, length


@contextlib.contextmanager
def scoped_file(args, path):
    """Yield (path_to_read, scope_label) -- `path` itself when no range was
    asked for, else a temp copy of just that range.

    The copy is placed in a temp *directory* under the original basename, so
    everything downstream (the displayed name, the byte count, --hex reads)
    reports the region rather than a mix of the region's bytes and the whole
    file's size. Held open for the caller's whole turn, since rendering reads
    the file again after the walk.
    """
    rng = resolve_range(args, path)
    if rng is None:
        yield path, None
        return
    start, length = rng
    tmpdir = tempfile.mkdtemp(prefix="acidcat-region-")
    dest = os.path.join(tmpdir, os.path.basename(path))
    try:
        with open(path, "rb") as f, open(dest, "wb") as out:
            f.seek(start)
            remaining = length
            while remaining > 0:
                block = f.read(min(1 << 20, remaining))
                if not block:
                    break
                out.write(block)
                remaining -= len(block)
        yield dest, f"0x{start:08x}+{length:,}"
    finally:
        try:
            os.unlink(dest)
            os.rmdir(tmpdir)
        except OSError:
            pass
