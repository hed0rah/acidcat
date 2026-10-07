"""One guard against a verb destroying the file it is reading.

`carve -o` pointed at its own input truncated a 2,044-byte WAV to the 4 bytes it
had been asked to extract, and exited 0. `convert` has the same shape and can
reach it without `-o` at all, since the default output name is the input's stem
plus the target extension -- so converting a `.wav` to WAV replaces the original
with the converted bytes.

Neither is a torn write: both read the input fully into memory before opening
the output, so the bytes that land are correct. The file that was there before
is simply gone, and both commands' help says the input is never modified.

`repair` and `write` are already safe by a different route -- they go through
core/write/writer.py's atomic temp + os.replace, so even out == input is a clean
in-place replacement of fully-computed data.
"""

import os


def same_file(a, b):
    """True if two paths name the same file. Tolerates `b` not existing yet.

    os.path.samefile needs both to exist, which is exactly not the case when
    checking an output path before creating it, so fall back to comparing
    resolved paths -- that also collapses symlinks and, on Windows, differing
    case and separators.
    """
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.realpath(a) == os.path.realpath(b)


def input_named(out, argv):
    """The argument in `argv` that names the same file as `out`, else None.

    The output's own value (after -o/--output) is skipped; so are flags and
    stdin's "-". Only files that exist count: an output that does not exist
    yet cannot be anyone's input.
    """
    skip = False
    for tok in argv:
        if skip:
            skip = False
            continue
        if tok in ("-o", "--output"):
            skip = True
            continue
        if tok.startswith("-"):
            continue
        if os.path.isfile(tok) and same_file(tok, out):
            return tok
    return None


def refuse_self_overwrite(verb, source, out):
    """An error string when `out` would clobber `source`, else None.

    Returning the message rather than printing keeps this usable from commands
    that report differently, and keeps the wording identical across them.
    """
    if out and same_file(source, out):
        return (f"acidcat {verb}: {out}: output is the input; refusing to "
                f"overwrite the file being read")
    return None


def refuse_clobber(verb, out, *, force=False, flag="--force"):
    """An error string when `out` exists and was never named by the user.

    For a DERIVED output path only -- one the command computed, typically by
    swapping an extension. `convert` turned `precious.ncw` into `precious.wav`
    and opened it "wb", so an unrelated `precious.wav` sitting beside it was
    destroyed with no warning, no backup, and exit 0. NCW and WAV siblings are
    ordinary in a Kontakt library, so this was reachable by running the
    documented command in a normal directory.

    An explicit `-o` is deliberately NOT routed through here. The user naming a
    target is a different act from a tool inventing one, and the surprise this
    prevents is entirely in the second case.
    """
    if force or not out or not os.path.exists(out):
        return None
    return (f"acidcat {verb}: {out}: already exists; refusing to overwrite a "
            f"file this command named for you (pass {flag} to allow, or "
            f"--skip-existing to pass over it)")
