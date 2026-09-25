"""acidcat -- a scalpel for dissecting audio and preset file formats.

This is the public library API: the stable engine surface that the acidcat CLI
and the acidcat-playground both build on. Import from the package root; the
``acidcat.core.*`` and ``acidcat.commands.*`` modules are internal and may move.

    import acidcat

    # what is this? -- by path or by the first bytes, no file needed
    acidcat.sniff("song.wav")            # 'wav'
    acidcat.sniff_bytes(head)            # 'flac', 'ogg', ... or None

    # structural walk: format label, chunk/field tree, lint warnings
    fmt, chunks, warns = acidcat.walk("song.wav")

    # the inverse: find containers inside something that is not a file yet --
    # a blob, a disk image, a carved region
    for hit in acidcat.locate(blob):
        hit["offset"], hit["end"], hit["format"]

    # byte dissection (the RE surface): resolve a name to an offset, read typed
    off, length, note = acidcat.probe.resolve("song.wav", "fmt.sample_rate")
    data = open("song.wav", "rb").read()
    (rate,) = acidcat.probe.read_typed(data, off, "u32", 1, "little")
    acidcat.probe.scan_value(data, 44100, "u32")     # Cheat-Engine value scan
    acidcat.probe.strings(data)                        # printable runs

    # the file's shape
    ent = acidcat.viz.windowed_entropy(data)           # bits/byte per window

    # audition any byte range as PCM; block=False returns a handle stop() takes
    h = acidcat.play.play_region("song.wav", 44, 176400, rate=44100, block=False)
    acidcat.play.stop(h)
    grid, side = acidcat.viz.hilbert_grid(data)        # binvis byte map

    # constraints / forensics
    report = acidcat.analyze(data)                     # derived-field violations
    fixed, report = acidcat.repair(data)               # re-satisfy the constraints
    findings = acidcat.anomalies_scan("song.wav")      # walks internally
    findings = acidcat.anomalies_scan("song.wav", fmt, chunks, warns)  # reuse a walk

Importing acidcat pulls only the zero-optional-dependency core (the walkers, the
dissection primitives, the constraint model). Tagging (mutagen), the TUI
(textual), and librosa analysis load only when their commands are used.

See docs/format_internals.md for the formats acidcat walks.
"""

__version__ = "1.8.5"

# dissection namespaces
from acidcat.core import probe  # noqa: E402,F401
from acidcat.core.forensics import viz

# structural walking
from acidcat.core.walk import walk_file  # noqa: E402
from acidcat.core.walk.base import Unsupported  # noqa: E402,F401

# identification. Exported because a tool whose whole job is "what is this
# file" left callers no public way to ask: the one consumer built on acidcat
# ended up with two hand-rolled magic tables instead. A missing export shows up
# as duplicated tables, not as an import, so nothing caught it.
from acidcat.core.infra.sniff import sniff, sniff_bytes  # noqa: E402,F401

# finding audio inside something that is not a file yet -- a blob, a disk image,
# a carved region. The counterpart to walk(): walk() reads a container, locate()
# finds the containers.
from acidcat.core.forensics.locate import locate  # noqa: E402,F401

# format primitives a consumer cannot reasonably reimplement: Ogg's page chain
# and 8SVX's Fibonacci-delta decode.
from acidcat.core.formats.ogg import iter_pages  # noqa: E402,F401
from acidcat.core.formats.svx import decode as decode_8svx  # noqa: E402,F401

# constraints / forensics
from acidcat.core.write.constraints import (  # noqa: E402,F401
    analyze, repair, Report, Violation,
)
from acidcat.core.forensics.anomalies import scan as anomalies_scan  # noqa: E402,F401

# metadata read/write + the brand theme: public entry points so tools built on
# acidcat use these instead of reaching into core/commands internals.
from acidcat.core.write.edits import edit_metadata, EditError  # noqa: E402,F401
from acidcat.core.tagged import read_tags  # noqa: E402,F401
from acidcat.core.formats.mp3 import read_id3v2, list_id3v2_frames  # noqa: E402,F401
from acidcat import tui_theme  # noqa: E402,F401

# audition: play a file, or reinterpret an arbitrary byte range as PCM.
# `play_region(..., block=False)` returns a handle `stop()` accepts, which is
# what an interactive caller needs to stay responsive.
from acidcat.util import play  # noqa: E402,F401

# ``walk`` is the public name; ``walk_file`` stays as an alias.
walk = walk_file

__all__ = [
    "__version__",
    "walk", "walk_file", "Unsupported",
    "probe", "viz", "tui_theme", "play",
    "sniff", "sniff_bytes", "locate", "iter_pages", "decode_8svx",
    "analyze", "repair", "Report", "Violation",
    "anomalies_scan",
    "edit_metadata", "EditError", "read_tags", "read_id3v2", "list_id3v2_frames",
]
