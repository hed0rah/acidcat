"""Path layout helpers for acidcat v0.5+ per-library storage.

acidcat tracks a sample collection as a set of libraries. Each library
has its own SQLite index. By default the index lives centrally:

    ~/.acidcat/
        registry.db                          global registry of libraries
        libraries/
            <safe_label>_<hash>.db           per-library index, one per registered root

A user who would rather have the DB travel with the data passes
`--in-tree` at registration time, which puts the DB at
`<library_root>/.acidcat/index.db` instead. Either way the registry
records the canonical db_path so callers do not need to care which
mode any given library uses.

Everything here is stdlib-only.
"""

import hashlib
import os
import re


ACIDCAT_DIR_NAME = ".acidcat"
INDEX_DB_NAME = "index.db"
REGISTRY_DB_NAME = "registry.db"
LIBRARIES_DIR_NAME = "libraries"


def normalize(p):
    """Canonical absolute path with forward slashes (cross-platform stable).

    Used for every persisted path so we do not get equivalent rows differing
    only by separator or relative form.
    """
    return os.path.abspath(p).replace("\\", "/")


def acidcat_home():
    """`~/.acidcat/`. Created on demand by callers that write into it."""
    return os.path.join(os.path.expanduser("~"), ACIDCAT_DIR_NAME).replace("\\", "/")


def central_libraries_dir():
    """`~/.acidcat/libraries/`, where central-mode per-lib DBs live."""
    return os.path.join(acidcat_home(), LIBRARIES_DIR_NAME).replace("\\", "/")


def registry_db_path():
    """Default registry DB path: `~/.acidcat/registry.db`."""
    return os.path.join(acidcat_home(), REGISTRY_DB_NAME).replace("\\", "/")


def resolve_registry_path(cli_value=None):
    """Resolve registry path from --registry, ACIDCAT_REGISTRY env, or default."""
    if cli_value:
        return cli_value
    env = os.environ.get("ACIDCAT_REGISTRY")
    if env:
        return env
    return registry_db_path()


def legacy_global_db_path():
    """`~/.acidcat/index.db`, the v0.4 single-DB location.

    v0.5 does not read or write this file. We only check for its existence
    so we can surface a one-line stderr warning when it is encountered.
    """
    return os.path.join(acidcat_home(), INDEX_DB_NAME).replace("\\", "/")


_LABEL_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_label(label):
    """Return a filename-safe version of a label.

    Replaces any run of non-alphanumeric/underscore/dot/dash characters with
    a single underscore, trims surrounding underscores, and falls back to
    "library" if the result is empty.
    """
    if label is None:
        return "library"
    s = _LABEL_SAFE_RE.sub("_", str(label)).strip("_")
    return s or "library"


def path_hash(root):
    """8-character sha1 hex of the normalized root path.

    Stable across runs and across label changes. Used as a disambiguating
    suffix in central-mode DB filenames so two libraries with the same
    label slug never collide on disk.
    """
    return hashlib.sha1(normalize(root).encode("utf-8")).hexdigest()[:8]


def central_db_path_for(root, label):
    """Compute the central-mode DB path for a given root + label.

    Example:
        central_db_path_for("/foo/Hypnotize 03", "hypnotize")
        -> "~/.acidcat/libraries/hypnotize_a3f9c2d1.db"
    """
    name = f"{safe_label(label)}_{path_hash(root)}.db"
    return os.path.join(central_libraries_dir(), name).replace("\\", "/")


def in_tree_db_path_for(root):
    """Compute the in-tree DB path for a given root.

    Example:
        in_tree_db_path_for("/foo/Hypnotize 03")
        -> "/foo/Hypnotize 03/.acidcat/index.db"
    """
    return os.path.join(
        normalize(root), ACIDCAT_DIR_NAME, INDEX_DB_NAME
    ).replace("\\", "/")


def find_library_root_above(path):
    """Walk upward from `path` looking for a directory containing
    `.acidcat/index.db`. Returns the directory or None.

    Useful for detecting in-tree libraries when a caller has only a
    sample path. The registry is the source of truth; this is a fallback
    for the in-tree case.

    Skips `~` itself: a `.acidcat/index.db` at the user's home directory
    is the legacy v0.4 global DB, not an in-tree library root.
    """
    home = normalize(os.path.expanduser("~"))
    current = normalize(path)
    if os.path.isfile(current):
        current = os.path.dirname(current)
    while current and current != os.path.dirname(current):
        if current == home:
            return None
        candidate = os.path.join(current, ACIDCAT_DIR_NAME, INDEX_DB_NAME)
        if os.path.isfile(candidate):
            return current
        current = os.path.dirname(current)
    return None
