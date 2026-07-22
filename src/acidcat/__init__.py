"""acidcat -- a scalpel for dissecting audio and preset file formats.

This is the public library API: the stable engine surface that the acidcat CLI
and the acidcat-playground both build on. Import from the package root; the
``acidcat.core.*`` and ``acidcat.commands.*`` modules are internal and may move.

    import acidcat

    # structural walk: format label, chunk/field tree, lint warnings
    fmt, chunks, warns = acidcat.walk("song.wav")

    # byte dissection (the RE surface): resolve a name to an offset, read typed
    off, length, note = acidcat.probe.resolve("song.wav", "fmt.sample_rate")
    data = open("song.wav", "rb").read()
    (rate,) = acidcat.probe.read_typed(data, off, "u32", 1, "little")
    acidcat.probe.scan_value(data, 44100, "u32")     # Cheat-Engine value scan
    acidcat.probe.strings(data)                        # printable runs

    # the file's shape
    ent = acidcat.viz.windowed_entropy(data)           # bits/byte per window
    grid, side = acidcat.viz.hilbert_grid(data)        # binvis byte map

    # constraints / forensics
    report = acidcat.analyze(data)                     # derived-field violations
    fixed, report = acidcat.repair(data)               # re-satisfy the constraints
    findings = acidcat.anomalies_scan("song.wav", fmt, chunks, warns)

Importing acidcat pulls only the zero-optional-dependency core (the walkers, the
dissection primitives, the constraint model). Tagging (mutagen), the TUI
(textual), and librosa analysis load only when their commands are used.

See docs/format_internals.md for the formats acidcat walks.
"""

__version__ = "0.66.0"

# dissection namespaces
from acidcat.core import probe, viz  # noqa: E402,F401

# structural walking
from acidcat.core.walk import walk_file  # noqa: E402
from acidcat.core.walk.base import Unsupported  # noqa: E402,F401

# constraints / forensics
from acidcat.core.constraints import (  # noqa: E402,F401
    analyze, repair, Report, Violation,
)
from acidcat.core.anomalies import scan as anomalies_scan  # noqa: E402,F401

# metadata read/write + the brand theme: public entry points so tools built on
# acidcat use these instead of reaching into core/commands internals.
from acidcat.core.edits import edit_metadata, EditError  # noqa: E402,F401
from acidcat.core.tagged import read_tags  # noqa: E402,F401
from acidcat.core.mp3 import read_id3v2, list_id3v2_frames  # noqa: E402,F401
from acidcat import tui_theme  # noqa: E402,F401

# ``walk`` is the public name; ``walk_file`` stays as an alias.
walk = walk_file

__all__ = [
    "__version__",
    "walk", "walk_file", "Unsupported",
    "probe", "viz", "tui_theme",
    "analyze", "repair", "Report", "Violation",
    "anomalies_scan",
    "edit_metadata", "EditError", "read_tags", "read_id3v2", "list_id3v2_frames",
]
