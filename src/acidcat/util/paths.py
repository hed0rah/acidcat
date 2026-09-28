"""A path under a target, spelled the way the target was given.

On Windows `os.walk("C:/samples")` yields roots like `C:/samples\\loops`, and
`os.path.join` adds another backslash, so one row said `C:/samples\\loops\\a.wav`
beside the `C:/samples` the user typed, and `classify` normalised every path
to backslashes. cli-2.0.md section 4.1: a file is `path`, as given. A target
written with forward slashes keeps them in every path found under it; on
POSIX nothing changes.
"""

import os


def as_given(top, path):
    """`path`, found under the target `top`, with `top`'s separator."""
    if os.sep != "/" and "/" in top and os.sep not in top:
        return path.replace(os.sep, "/")
    return path


def under(top, root, name):
    """os.path.join(root, name) for a (root, name) os.walk(top) gave, spelled
    as `top` was."""
    return as_given(top, os.path.join(root, name))
