"""stdin buffering for pipe support."""

import contextlib
import os
import sys
import tempfile


_STDIN_SUFFIX = ".acidcat_stdin"


def stdin_to_tempfile():
    """Buffer stdin to a temporary file and return its path.

    Returns None if stdin is a terminal (not piped).
    Caller is responsible for cleanup via os.unlink().
    """
    if sys.stdin.isatty():
        return None

    data = sys.stdin.buffer.read()
    if not data:
        return None

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=_STDIN_SUFFIX)
    tmp.write(data)
    tmp.close()
    return tmp.name


def display_name(path):
    """Human/machine-facing name for an input path: ``<stdin>`` for a stdin
    buffer (so a random temp path never leaks into output), else the basename."""
    if path and path.endswith(_STDIN_SUFFIX):
        return "<stdin>"
    return os.path.basename(path)


def as_given(path):
    """The path as the caller gave it: ``<stdin>`` for a stdin buffer, else the
    path unchanged. For a record's `path` and a report's header, where the
    temp copy's full path (home directory and all) must never appear."""
    if path and path.endswith(_STDIN_SUFFIX):
        return "<stdin>"
    return path


# `-` with nothing piped in: the input could not be read, as a missing file
NO_STDIN = 2


def is_stdin_target(target):
    """Check if target means 'read from stdin'."""
    return target == "-"


@contextlib.contextmanager
def resolved_input(target):
    """Yield a real filesystem path for ``target`` for commands that parse a file.

    A normal path is yielded unchanged. ``-`` (stdin) is buffered to a temp file
    -- so byte-level parsers can still seek -- and the temp file is removed on
    exit. Yields ``None`` when ``-`` is given but stdin is a TTY or empty, so the
    caller can report "no data on stdin" and exit. Use as::

        with resolved_input(args.target) as path:
            if path is None:
                return 1
            ...  # parse path; is_stdin_target(args.target) tells you it was stdin
    """
    if is_stdin_target(target):
        tmp = stdin_to_tempfile()
        try:
            yield tmp
        finally:
            if tmp:
                os.unlink(tmp)
    else:
        yield target
