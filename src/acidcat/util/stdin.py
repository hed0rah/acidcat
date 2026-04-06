"""stdin buffering for pipe support."""

import os
import sys
import tempfile


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

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".acidcat_stdin")
    tmp.write(data)
    tmp.close()
    return tmp.name


def is_stdin_target(target):
    """Check if target means 'read from stdin'."""
    return target == "-"
