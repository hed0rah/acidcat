"""optional dependency helpers."""

import sys


def require(*packages, group="analysis"):
    """Check that optional packages are importable.

    Returns True if all are available, False after printing
    an install hint to stderr.
    """
    missing = []
    for pkg in packages:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(
            f"acidcat: missing {', '.join(missing)} "
            f"-- install with: pip install acidcat[{group}]",
            file=sys.stderr,
        )
        return False
    return True
