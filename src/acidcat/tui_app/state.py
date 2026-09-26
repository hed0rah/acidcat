"""What the TUI remembers between runs: where you last opened a file, and the
files you opened recently. One small JSON file, `<acidcat home>/tui.json`.

Nothing here may stop the TUI from starting: a missing, unreadable or mangled
file reads as an empty state, and a home that cannot be written is left alone.
"""

import json
import os

from acidcat.core.catalogue.paths import acidcat_home

RECENT_MAX = 10


def _path():
    return os.path.join(acidcat_home(), "tui.json")


def load():
    """{"last_dir": str or None, "recent": [paths that still exist]}."""
    try:
        with open(_path(), encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    last = raw.get("last_dir")
    recent = [p for p in raw.get("recent") or []
              if isinstance(p, str) and os.path.isfile(p)]
    return {"last_dir": last if isinstance(last, str) and os.path.isdir(last)
            else None,
            "recent": recent[:RECENT_MAX]}


def remember(path):
    """Put `path` at the top of the recent files and make its directory the
    last one. Written whole to a temp beside the file and renamed into place,
    so a crash leaves the old state rather than half a file."""
    path = os.path.abspath(path)
    st = load()
    recent = [path] + [p for p in st["recent"] if p != path]
    st = {"last_dir": os.path.dirname(path), "recent": recent[:RECENT_MAX]}
    target = _path()
    tmp = target + ".tmp"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, indent=1)
        os.replace(tmp, target)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
