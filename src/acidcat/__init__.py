"""acidcat -- a scalpel for dissecting audio and preset file formats.

This is the public library API: the stable engine surface that the acidcat CLI
and the acidcat-playground both build on. Import from the package root; the
``acidcat.core.*`` and ``acidcat.commands.*`` modules are internal and may move.

    import acidcat

    # a file as a Document: nodes, fields, layers and findings, by ADDR
    doc = acidcat.open("tune.ym")                  # a path, bytes, or a Source
    doc.field("1:lh5/header#frames").value         # a field in a decoded layer
    doc.layer_bytes(1)[:4]                         # b"YM5!"
    [f.code for f in doc.findings if f.kind == "defect"]
    doc.to_json()                                  # the contract v1 dict

    # what is this? -- by path or by the first bytes, no file needed
    acidcat.sniff("song.wav")            # 'wav'
    acidcat.sniff_bytes(head)            # 'flac', 'ogg', ... or None

    # the inverse: find containers inside something that is not a file yet --
    # a blob, a disk image, a carved region
    for hit in acidcat.locate(blob):
        hit["offset"], hit["end"], hit["format"]

    # byte dissection (the RE surface): resolve a name to an offset, read typed
    off, length, note = acidcat.probe.resolve("song.wav", "RIFF/fmt_#sample_rate")
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

``walk`` and ``walk_file``, the 1.x tuple API (format label, chunk dicts,
warnings), still work through 2.x with a DeprecationWarning naming
``acidcat.open()``, and go in 3.0.

Importing acidcat pulls only the zero-optional-dependency core (the walkers, the
dissection primitives, the constraint model). Tagging (mutagen), the TUI
(textual), and librosa analysis load only when their commands are used.

See docs/format_internals.md for the formats acidcat walks.
"""

__version__ = "2.0.0"

# dissection namespaces
from acidcat.core import probe  # noqa: E402,F401
from acidcat.core.forensics import viz

# structural walking: the dispatcher behind acidcat.open() and, until 3.0,
# the deprecated tuple API below
from acidcat.core.walk import walk_file as _walk_file  # noqa: E402
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


def _tuple_api(name):
    """`walk` / `walk_file`: the 1.x tuple API, deprecated in 2.0 and removed
    in 3.0 (architecture-2.0.md section 6). Nothing in acidcat calls it
    (tests/test_tuple_api_deprecated.py)."""
    import functools
    import warnings

    @functools.wraps(_walk_file)
    def deprecated(*args, **kwargs):
        warnings.warn(
            f"acidcat.{name}() is deprecated and is removed in 3.0; use "
            f"acidcat.open(), which returns a Document (its to_json() is the "
            f"contract v1 dict)", DeprecationWarning, stacklevel=2)
        return _walk_file(*args, **kwargs)
    deprecated.__name__ = deprecated.__qualname__ = name
    return deprecated


walk = _tuple_api("walk")
walk_file = _tuple_api("walk_file")

# 2.0: a file as a Document -- read-only views over the contract v1 dict, an
# ADDR resolver, the layers' bytes, and the forensic findings with the
# walker's. `open` is `acidcat.open`; inside this module the builtin is not
# used below this line.
from acidcat.core.document import (  # noqa: E402,F401
    AddrError, Document, Field, Finding, Layer, Loc, Node,
    open_document as open,
)
from acidcat.core.infra.limits import Limits  # noqa: E402,F401
from acidcat.core.edit import Patch, PatchError  # noqa: E402,F401

# `open` is left out of __all__ on purpose: `from acidcat import *` would
# shadow the builtin. Call it as acidcat.open().
__all__ = [
    "__version__",
    "Document", "Node", "Field", "Layer", "Finding", "Loc", "Limits",
    "AddrError", "Patch", "PatchError",
    "walk", "walk_file", "Unsupported",
    "probe", "viz", "tui_theme", "play",
    "sniff", "sniff_bytes", "locate", "iter_pages", "decode_8svx",
    "analyze", "repair", "Report", "Violation",
    "anomalies_scan",
    "edit_metadata", "EditError", "read_tags", "read_id3v2", "list_id3v2_frames",
]
