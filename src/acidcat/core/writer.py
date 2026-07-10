"""Safe file writing for acidcat's edit capability.

exiftool-style model: by default edits happen in place after a `<name>_original`
backup is saved; `-o` writes a modified copy and never touches the input. Every
write is atomic: build the full bytes, write a temp file in the same directory,
fsync, then rename over the target so a crash never leaves a half-written file.
"""

import os
import tempfile


def backup_path(path):
    """The `<name>_original.<ext>` sibling used for in-place backups."""
    root, ext = os.path.splitext(path)
    return root + "_original" + ext


def atomic_write(path, data):
    """Write bytes to path atomically via a temp file in the same directory,
    then read the target back and verify it holds exactly those bytes before
    returning, so "wrote"/"saved" is never reported on a write that didn't land."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".acidtmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    with open(path, "rb") as f:
        if f.read() != data:
            raise OSError(f"read-back verification failed for {path}: "
                          "the bytes on disk do not match what was written")


def commit(src_path, new_data, out=None, overwrite=False):
    """Persist edited bytes for src_path.

    - out given: write there (a copy); the input is untouched.
    - else: back up the input to `<name>_original` (unless overwrite) then
      replace it in place.

    Returns (written_path, backup_path_or_None). The backup path is returned
    only when this call created the backup; None also covers the case where a
    `<name>_original` already existed and was kept -- callers that need to
    distinguish should check os.path.exists(backup_path(src_path)), because a
    pre-existing file there may predate acidcat and not hold this original.
    """
    if out:
        atomic_write(out, new_data)
        return out, None
    backup = None
    if not overwrite:
        bp = backup_path(src_path)
        if not os.path.exists(bp):
            with open(src_path, "rb") as f:
                atomic_write(bp, f.read())
            backup = bp
    atomic_write(src_path, new_data)
    return src_path, backup
