"""The TUI's model of one open file: its Document, where you are in it, and
what can be undone.

Everything the screen shows is a function of this and the pane size:

    document     the v1 Document (core/infra/contract.py) of the working copy
    layer        which of its layers the bytes pane shows (0 is the file)
    selection    (layer, offset, length) of the selected bytes, or None
    top          the first row the bytes pane shows, or None to follow the
                 selection
    undo, redo   byte deltas (start, old, new) against the working copy

It imports nothing from Textual and holds no reference to the app, so it is
tested without a terminal (tests/test_tui_model.py) and the app is only the
thing that draws it.
"""

import os

from acidcat.core.infra import contract, layers
from acidcat.tui_app.render import _UNDO_BYTES_CAP, _UNDO_CAP


class DocumentModel:
    """One view of one file. The app keeps one per navigation frame."""

    def __init__(self, path=None):
        self.path = path
        self.document = None
        self.layer = 0
        self.selection = None
        self.top = None
        self.undo = []
        self.redo = []
        self._bytes = {}          # decoded layers, by id; never layer 0
        self.chunk_caps = {}      # top-level chunk index -> caps
        # {"id", "name"} when this model's file is a decoded layer of the one
        # the user opened: what the status line calls it
        self.view = None
        self.prefer_be = False    # the format's fields are big-endian

    # ── the document ─────────────────────────────────────────────────────

    def load(self, fmt_id, label, chunks, warns, *, forced=False):
        """Build the Document from a walk the caller already made, so nothing
        is walked twice. Caches are dropped; the selection and undo stay."""
        from acidcat.core.infra import capabilities
        from acidcat.core.infra.source import MappedSource
        self._bytes = {}
        self.document = None
        try:
            self.chunk_caps = capabilities.caps(fmt_id, label, chunks or [],
                                                head=self.read(0, 16, 0),
                                                name=self.path)
        except Exception:             # a cap heuristic never breaks a view
            self.chunk_caps = {}
        self.prefer_be = capabilities.prefers_be(fmt_id, label)
        try:
            # mapped only while the Document is built: the working copy is
            # rewritten in place on every edit, and a map held across that
            # would fault on the truncated file
            with MappedSource(self.path) as src:
                self.document = contract.document(
                    fmt_id, label, chunks, warns, src.buffer(), forced=forced,
                    caps_fn=lambda f, l, c: self.chunk_caps,
                    prefer_be=self.prefer_be)
        except Exception:
            # the normaliser never raises on walker output, but the TUI opens
            # hostile files: a Document it cannot build is no Document, not a
            # dead session
            self.document = None
        if self.layer not in self.layer_ids():
            self.layer = 0
        return self.document

    # ── capabilities ─────────────────────────────────────────────────────

    # the caps that say what the FILE can do; they sit on its first chunk
    FILE_CAPS = ("render", "decode", "edit")

    def caps_of(self, index):
        """The caps of top-level chunk `index` of the walk."""
        return self.chunk_caps.get(index, {})

    def file_caps(self):
        """What the file as a whole offers (render, decode, edit), wherever
        the walk put them."""
        out = {}
        for caps in self.chunk_caps.values():
            for k in self.FILE_CAPS:
                if k in caps:
                    out.setdefault(k, caps[k])
        return out

    def audio_index(self):
        """The first top-level chunk that holds sample data, or None."""
        hits = [i for i, caps in self.chunk_caps.items() if "audio" in caps]
        return min(hits) if hits else None

    def layer_ids(self):
        if self.document is None:
            return [0]
        return [l["id"] for l in self.document["layers"]]

    def layer_info(self, lid=None):
        lid = self.layer if lid is None else lid
        if self.document is None:
            return {"id": 0, "name": "file", "kind": "file",
                    "length": self.layer_length(0)}
        return next(l for l in self.document["layers"] if l["id"] == lid)

    def layer_length(self, lid=None):
        lid = self.layer if lid is None else lid
        if lid == 0:
            if self.document is None:
                try:
                    return os.path.getsize(self.path)
                except (OSError, TypeError):
                    return 0
            return self.document["file"]["size"]
        return self.layer_info(lid)["length"]

    def read(self, off, n, lid=None):
        """`n` bytes at `off` in a layer. Layer 0 is read from the file each
        time (it may be gigabytes, browsed in place); a decoded layer is
        decoded once, under the walk's inflate limit, and kept."""
        lid = self.layer if lid is None else lid
        if off < 0 or n <= 0:
            return b""
        if lid == 0:
            try:
                with open(self.path, "rb") as f:
                    f.seek(off)
                    return f.read(n)
            except (OSError, TypeError, ValueError):
                return b""
        if lid not in self._bytes:
            from acidcat.core.infra.source import MappedSource
            with MappedSource(self.path) as src:
                self._bytes[lid] = layers.layer_bytes(self.document, lid,
                                                      src.buffer())
        return self._bytes[lid][off:off + n]

    def findings(self):
        return list(self.document["findings"]) if self.document else []

    # ── selection and the window onto the layer ─────────────────────────

    def select(self, off, length, layer=None):
        """Select `length` bytes at `off` in `layer` (default: the current
        one). The window follows a new selection; an unchanged one keeps
        wherever the user paged it to."""
        layer = self.layer if layer is None else layer
        sel = None if off is None else (layer, off, max(0, length or 0))
        if sel != self.selection:
            self.top = None
        self.selection = sel

    def node_for(self, off, length, layer=None):
        """The Document node the selection is: the deepest whose extent is
        exactly these bytes, else the deepest that encloses them."""
        if self.document is None or off is None:
            return None
        layer = self.layer if layer is None else layer
        exact = inside = None
        for n in contract.iter_nodes(self.document):
            e = n.get("extent")
            if not e or e["layer"] != layer:
                continue
            if e["off"] == off and e["len"] == length:
                exact = n
            elif e["off"] <= off and off + (length or 0) <= e["off"] + e["len"]:
                inside = n
        return exact or inside

    def spans(self, node=None):
        """(off, len) of each positioned field of `node` in the current layer:
        what the bytes pane tints."""
        if node is None:
            return []
        out = []
        for f in node["fields"]:
            at = f.get("at")
            if at and at.get("layer") == self.layer and at.get("len"):
                out.append((at["off"], at["len"]))
        return out

    def default_top(self, width, rows):
        """The first row that puts the selection in view with context: a
        selection that fits sits a third of the way down, one that does not
        starts two rows down."""
        if self.selection is None or self.selection[0] != self.layer:
            return 0
        _l, off, n = self.selection
        first = off // width
        last = (off + max(n, 1) - 1) // width
        span = last - first + 1
        lead = (rows - span) // 3 if span <= rows else min(2, rows // 8)
        return self.clamp(first - max(0, lead), width, rows)

    def clamp(self, top, width, rows):
        """`top` kept inside the layer: no row before the first, and the
        last row no higher than the bottom of the window."""
        size = self.layer_length()
        last_row = max(0, (size - 1) // width) if size else 0
        return max(0, min(top, max(0, last_row - rows + 1)))

    def window(self, width, rows):
        """(first offset, bytes) the bytes pane shows: `rows` rows of `width`
        from the top row."""
        top = self.top if self.top is not None else self.default_top(width, rows)
        top = self.clamp(top, width, rows)
        start = top * width
        return start, self.read(start, rows * width)

    def page(self, step, width, rows):
        """Move the window a screenful. Returns the new first offset, or None
        at either end of the layer."""
        cur = self.top if self.top is not None else self.default_top(width, rows)
        want = self.clamp(cur + step * rows, width, rows)
        if want == self.clamp(cur, width, rows):
            return None
        self.top = want
        return want * width

    def show_offset(self, offset, width, rows):
        """Put `offset` a third of the way down the window."""
        self.top = self.clamp(offset // width - rows // 3, width, rows)

    # ── the status line ──────────────────────────────────────────────────

    def status(self):
        """What the status line says: the layer, where the selection is, how
        many findings, and the actions the selected node offers."""
        lay = self.layer_info()
        node = (self.node_for(self.selection[1], self.selection[2], self.selection[0])
                if self.selection else None)
        view = self.view or {}
        return {
            "layer": view.get("name", lay["name"]),
            "layer_id": view.get("id", lay["id"]),
            "length": lay["length"],
            "offset": self.selection[1] if self.selection else None,
            "selected": self.selection[2] if self.selection else 0,
            "findings": len(self.findings()),
            "node": node["id"] if node else None,
            "actions": sorted(node["caps"]) if node else [],
        }

    # ── undo ─────────────────────────────────────────────────────────────

    def record(self, start, old, new):
        """Remember an edit. A fresh edit forgets the redo history."""
        self.undo.append((start, old, new))
        self.redo = []
        self.undo = self.undo[-_UNDO_CAP:]
        # cap by total delta bytes so history cannot pin gigabytes; the most
        # recent delta always survives
        while (len(self.undo) > 1
               and sum(len(o) + len(n) for _s, o, n in self.undo) > _UNDO_BYTES_CAP):
            self.undo.pop(0)

    def take_undo(self):
        """The delta to revert, moved to the redo history; None if none."""
        if not self.undo:
            return None
        d = self.undo.pop()
        self.redo = (self.redo + [d])[-_UNDO_CAP:]
        return d

    def take_redo(self):
        if not self.redo:
            return None
        d = self.redo.pop()
        self.undo = (self.undo + [d])[-_UNDO_CAP:]
        return d
