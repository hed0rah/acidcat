"""Textual inspector app for `acidcat tui`.

Read + edit front-end over the same engine the CLI uses: walk_file for the
structure, core.anomalies for the forensic findings, and the write engine
(commands.write._edit + core.writer.commit) for the "sane" metadata editor.

Two edit modes:
- press e on a field to edit its raw bytes in place (a power tool: change
  sample_rate = 44100 to 69 and it patches those exact bytes). Where a field's
  displayed value round-trips to its bytes with a known struct format, you type
  the value and the hex pane previews the new bytes live; otherwise you edit the
  hex directly. Length-preserving, atomic, leaves a _original backup.
- press w for the exiftool-style metadata editor (title/artist/bpm/key/...).

Imported lazily by commands/tui.py so textual stays behind the [tui] extra and
the core remains zero-dependency.
"""
import os
import shutil
import struct
import tempfile

from rich.text import Text

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button, DirectoryTree, Footer, Input, Label, Static, Tree,
)

from acidcat.core.walk import walk_file, Unsupported
from acidcat.core import anomalies as ac_anom
from acidcat.core import writer
from acidcat.core.edits import EditError
from acidcat.commands.write import _edit as _write_edit, _strip as _write_strip


# palette carried over from the playground TUI (btop-ish); cyan accent.
PALETTE = ["#56e0f0", "#ffcc55", "#66e88a", "#ff6e83", "#c07ee0",
           "#4ee0c0", "#e0964e", "#e07eb0", "#8fa4ff", "#c0e04e"]
ACCENT = "#56e0f0"
FG = "#e6e6e1"
SOFT = "#9aa5ad"
DIM = "#5a6a78"
GUTTER = "#5a6a78"
PEND = "#ffcc55"       # pending / unsaved-preview color
SEV = {"alert": "#ff6e83", "warn": "#ffcc55", "notice": "#8fa4ff"}

_HEX_CAP = 1024        # most bytes to render in the hex pane for one node
_ROW_CAP = 400         # most per-element rows (events/frames) to list per chunk
_HEXEDIT_CAP = 512     # refuse editing a byte region bigger than this (pick a field)

# struct formats tried when inferring a numeric field's on-disk layout, smallest
# to largest, LE before BE. Verified by round-trip against the actual bytes, so a
# match is exact, never a guess.
_ENC_TRY = ("<B", ">B", "<H", ">H", "<h", ">h", "<I", ">I", "<i", ">i",
            "<Q", ">Q", "<q", ">q", "<f", ">f", "<d", ">d")


def _read(path, off, length):
    try:
        with open(path, "rb") as f:
            f.seek(off)
            return f.read(length)
    except OSError:
        return b""


def _field_abs(chunk, field):
    """Absolute file offset of a field, mirroring inspect's rule: field offsets
    are relative to the chunk payload base (payload_base, else offset + 8).
    Returns None for derived fields that carry no byte position."""
    if field.get("off") is None:
        return None
    base = chunk.get("payload_base")
    if base is None:
        base = (chunk.get("offset") or 0) + 8
    return base + field["off"]


def infer_enc(value, raw):
    """Find a struct format whose pack(value) reproduces `raw` exactly, so a new
    value can be re-encoded to the same on-disk layout. Verified against the real
    bytes (no guessing): returns the format string, or None if `value` is not a
    plain number or nothing round-trips (a rounded-for-display float, a string,
    an odd width) -- in which case the caller falls back to raw hex editing."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    for fmt in _ENC_TRY:
        if struct.calcsize(fmt) != len(raw):
            continue
        try:
            if struct.pack(fmt, value) == raw:
                return fmt
        except (struct.error, OverflowError, ValueError):
            continue
    return None


def encode_value(fmt, text):
    """Encode user text as bytes using an inferred struct format. Ints accept
    0x.. / 0b.. prefixes. Raises ValueError/struct.error on bad input."""
    if fmt[-1] in "fd":
        return struct.pack(fmt, float(text))
    return struct.pack(fmt, int(text, 0))


def hex_text(path, off, length, accent):
    """A colored hex dump (offset gutter + hex columns + ascii) of up to
    _HEX_CAP bytes starting at off. Bytes render in `accent`, non-printable
    ascii dims out."""
    t = Text()
    if off is None or length in (None, 0):
        t.append("  (no byte range for this node)", style=DIM)
        return t
    shown = min(length, _HEX_CAP)
    raw = _read(path, off, shown)
    _hex_rows(t, off, raw, accent)
    if length > shown:
        t.append(f"  .. {length - shown:,} more bytes\n", style=DIM)
    return t


def _hex_rows(t, off, raw, byte_style):
    """Append hex-dump rows (gutter + hex + ascii) for `raw` to Text `t`."""
    for row in range(0, len(raw), 16):
        chunk = raw[row:row + 16]
        t.append(f"{off + row:08x}  ", style=GUTTER)
        for i in range(16):
            if i < len(chunk):
                t.append(f"{chunk[i]:02x} ", style=byte_style)
            else:
                t.append("   ")
            if i == 7:
                t.append(" ")
        t.append(" ")
        for b in chunk:
            ch = chr(b) if 32 <= b < 127 else "."
            t.append(ch, style=FG if 32 <= b < 127 else DIM)
        t.append("\n")


# editable-field profiles, mirroring what the write engine accepts per format.
# (field, label) -- field is the --set name commands.write understands.
_WAV_FIELDS = [("title", "title"), ("artist", "artist"), ("album", "album"),
               ("genre", "genre"), ("comment", "comment"), ("date", "date"),
               ("bpm", "bpm"), ("key", "key"),
               ("root_note", "root note (C3 or 60)")]
_AIFF_FIELDS = [("title", "title"), ("artist", "artist"), ("comment", "comment")]
_TAGGED_FIELDS = [("title", "title"), ("artist", "artist"), ("album", "album"),
                  ("genre", "genre"), ("comment", "comment"), ("date", "date"),
                  ("bpm", "bpm"), ("key", "key")]
_VITAL_FIELDS = [("name", "preset name"), ("author", "author"),
                 ("comment", "comments")]


def edit_profile(path):
    """Return (profile_name, [(field, label), ...]) for the file's format, or
    None where the write engine has no editor (or editing is disabled, e.g.
    Bitwig/NI). Routing mirrors commands.write._edit so the form only offers
    fields a save can actually apply."""
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        head = f.read(16)
    if ext == ".vital" or head[:1] == b"{":
        return ("Vital", _VITAL_FIELDS)
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ("WAV", _WAV_FIELDS)
    # Bitwig / NI preset writing is disabled in the engine; do not offer it.
    if (head[:4] == b"BtWg" or head[12:16] == b"hsin" or head[:4] == b"-in-"
            or (head[:4] == b"RIFF" and head[8:12] == b"NIKS")):
        return None
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return ("AIFF", _AIFF_FIELDS)
    tagged = (head[:4] == b"fLaC" or head[:3] == b"ID3" or head[:4] == b"OggS"
              or head[4:8] == b"ftyp"
              or ext in (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4"))
    if tagged:
        return ("tagged", _TAGGED_FIELDS)
    return None


class BrowseScreen(ModalScreen):
    """A file picker: navigate a directory tree, enter selects, esc cancels.
    dismiss()es with the chosen path string, or None on cancel."""

    CSS = """
    BrowseScreen { align: center middle; }
    #browsebox { width: 80%; height: 80%; border: round #56e0f0;
                 background: #10161a; padding: 1 2; }
    #browsehint { color: #9aa5ad; padding-bottom: 1; }
    DirectoryTree { background: #10161a; }
    """
    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, start):
        super().__init__()
        self.start = start

    def compose(self) -> ComposeResult:
        with Vertical(id="browsebox"):
            yield Static(Text("open a file  (enter selects, esc cancels)",
                              style=f"bold {ACCENT}"), id="browsehint")
            yield DirectoryTree(self.start, id="dtree")

    def on_directory_tree_file_selected(self, event):
        self.dismiss(str(event.path))

    def action_cancel(self):
        self.dismiss(None)


class EditScreen(ModalScreen):
    """The exiftool-style metadata editor: the write engine's supported fields
    for this format. Blank inputs are left unchanged; typed values are applied
    via commands.write._edit + core.writer.commit (atomic, leaves a _original
    backup). dismiss()es with a result dict on a successful write, else None."""

    CSS = """
    EditScreen { align: center middle; }
    #editbox { width: 72; height: auto; max-height: 90%; border: round #ffcc55;
               background: #10161a; padding: 1 2; }
    #edittitle { color: #ffcc55; text-style: bold; padding-bottom: 1; }
    #edithint { color: #5a6a78; padding-bottom: 1; }
    #editstatus { color: #ff6e83; padding-top: 1; }
    EditScreen Label { color: #9aa5ad; }
    EditScreen Input { margin-bottom: 1; }
    #editbtns { height: auto; padding-top: 1; }
    """
    BINDINGS = [("escape", "cancel", "cancel"), ("ctrl+s", "save", "save")]

    def __init__(self, path, profile, fields):
        super().__init__()
        self.path = path
        self.profile = profile
        self.fields = fields

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="editbox"):
            yield Static(f"edit metadata  [{self.profile}]", id="edittitle")
            yield Static("type to set a field; leave blank to keep current. "
                         "ctrl+s saves (makes a _original backup), esc cancels.",
                         id="edithint")
            for field, label in self.fields:
                yield Label(label)
                yield Input(id=f"f_{field}", placeholder=f"{field} (unchanged)")
            yield Static("", id="editstatus")
            with Horizontal(id="editbtns"):
                yield Button("save", id="save", variant="warning")
                yield Button("cancel", id="cancel")

    def on_button_pressed(self, event):
        if event.button.id == "save":
            self.action_save()
        else:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)

    def action_save(self):
        changes = {}
        for field, _ in self.fields:
            val = self.query_one(f"#f_{field}", Input).value.strip()
            if val:
                changes[field] = val
        if not changes:
            self.dismiss(None)
            return
        status = self.query_one("#editstatus", Static)
        try:
            _fmt, new_data, applied = _write_edit(self.path, changes)
        except (EditError, OSError, ValueError) as e:
            status.update(Text(f"error: {e}", style=SEV["alert"]))
            return
        self.dismiss({"new_data": new_data, "applied": applied})


class ConfirmScreen(ModalScreen):
    """Unsaved-changes prompt. dismiss()es with 'save', 'discard', or None
    (cancel)."""

    CSS = """
    ConfirmScreen { align: center middle; }
    #confbox { width: 60; height: auto; border: round #ffcc55;
               background: #10161a; padding: 1 2; }
    #confmsg { color: #e6e6e1; padding-bottom: 1; }
    #confbtns { height: auto; }
    """
    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, prompt):
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="confbox"):
            yield Static(Text(self.prompt, style=f"bold {PEND}"), id="confmsg")
            with Horizontal(id="confbtns"):
                yield Button("save", id="save", variant="success")
                yield Button("discard", id="discard", variant="error")
                yield Button("cancel", id="cancel")

    def on_button_pressed(self, event):
        self.dismiss(event.button.id if event.button.id != "cancel" else None)

    def action_cancel(self):
        self.dismiss(None)


class AcidcatTUI(App):
    CSS = """
    Screen { background: #10161a; }
    #tree { width: 48%; border: round #56e0f0; padding: 0 1; }
    #right { width: 52%; }
    #detail { height: auto; border: round #66e88a; padding: 0 1; color: #e6e6e1; }
    #hexwrap { border: round #56e0f0; }
    #hex { padding: 0 1; }
    #anom { height: auto; border: round #ff6e83; padding: 0 1; }
    #editbar { dock: bottom; height: 3; border: round #ffcc55; background: #10161a; }
    #editbar.hidden { display: none; }
    Tree { background: #10161a; }
    Tree > .tree--guides { color: #2a3540; }
    Tree > .tree--guides-selected { color: #56e0f0; }
    """

    BINDINGS = [
        ("q", "request_quit", "quit"),
        ("ctrl+s", "save", "save"),
        ("o", "open", "open file"),
        ("e", "edit_field", "edit field"),
        ("w", "edit", "edit tags"),
        ("s", "strip", "strip meta"),
        ("a", "expand_all", "expand"),
        ("c", "collapse_all", "collapse"),
        ("escape", "cancel_edit", "cancel edit"),
    ]

    def __init__(self, path=None):
        super().__init__()
        self.src = path           # the file being edited (save target + display name)
        self.work = None          # temp working copy: edits land here until save
        self.dirty = False        # unsaved edits present
        self._backed_up = False   # a _original backup was made this session
        self.fsize = 0
        self.chunks = []
        self.fmt = "?"
        self.warns = []
        self.findings = []
        self._nodemeta = {}       # id(node) -> (off, length, accent)  for the hex pane
        self._editval = {}        # id(node) -> value  for value-editable field nodes
        self._cur_node = None     # last highlighted tree node
        self._edit_target = None  # active inline edit: dict(off,length,name,mode,fmt,accent)

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with Horizontal():
            yield Tree("file", id="tree")
            with Vertical(id="right"):
                yield Static(id="detail")
                with VerticalScroll(id="hexwrap"):
                    yield Static(id="hex")
                yield Static(id="anom")
        yield Input(id="editbar", classes="hidden")
        yield Footer()

    def on_mount(self):
        if self.src:
            self._open_path(self.src)
        else:
            self.query_one("#title", Static).update(
                Text(" acidcat tui   press o to open a file",
                     style=f"bold {ACCENT}"))
            self.action_open()

    def on_unmount(self):
        self._discard_work()

    # ── working copy: all edits apply to a temp file until an explicit save ──

    def _open_path(self, path):
        """Point the app at `path`: make a fresh temp working copy and load it."""
        self.src = path
        self._make_work()
        self._load()

    def _make_work(self):
        self._discard_work()
        ext = os.path.splitext(self.src)[1]
        fd, self.work = tempfile.mkstemp(suffix=ext or ".bin", prefix="acidcat_tui_")
        os.close(fd)
        shutil.copyfile(self.src, self.work)
        self.dirty = False
        self._backed_up = False

    def _discard_work(self):
        w = self.work
        self.work = None
        if w and os.path.isfile(w):
            try:
                os.unlink(w)
            except OSError:
                pass

    def _apply_to_work(self, new_bytes):
        """Write edited bytes to the working copy (no disk write to the original
        yet) and refresh. Marks the session dirty."""
        with open(self.work, "wb") as f:
            f.write(new_bytes)
        self.dirty = True
        self._load()

    def action_save(self):
        if not self.work:
            return
        if not self.dirty:
            self.notify("no unsaved changes")
            return
        try:
            with open(self.work, "rb") as f:
                data = f.read()
            # back up the pristine original only on the first save; later saves
            # overwrite without clobbering that backup.
            _written, backup = writer.commit(self.src, data,
                                             overwrite=self._backed_up)
        except (OSError, ValueError) as e:
            self.notify(f"save failed: {e}", severity="error")
            return
        if backup:
            self._backed_up = True
        self.dirty = False
        self._load()
        self.notify("saved" + (f"; backup {os.path.basename(backup)}"
                               if backup else ""))

    def action_request_quit(self):
        if self.dirty:
            self.push_screen(
                ConfirmScreen("unsaved changes -- save before quitting?"),
                self._resolve_pending(lambda: self.exit()))
        else:
            self.exit()

    def _resolve_pending(self, proceed):
        """Return a ConfirmScreen callback: save/discard run `proceed`, cancel
        stays. Used for both quit and open-another-file with unsaved edits."""
        def cb(choice):
            if choice == "save":
                self.action_save()
                proceed()
            elif choice == "discard":
                self.dirty = False
                proceed()
        return cb

    def _load(self):
        """Walk the working copy and (re)build the tree + panes. Also the refresh
        after an edit or after opening a new file."""
        self.fsize = os.path.getsize(self.work)
        try:
            self.fmt, self.chunks, self.warns = walk_file(self.work, deep=True)
        except Unsupported as e:
            self.fmt, self.chunks, self.warns = "unsupported", [], [str(e)]
        try:
            self.findings = ac_anom.scan(self.work, self.fmt, self.chunks, self.warns)
        except Exception:
            self.findings = []

        head = Text()
        head.append(f" {os.path.basename(self.src)} ", style=f"bold {ACCENT}")
        head.append(f" {self.fmt}  {self.fsize:,} bytes  "
                    f"{len(self.chunks)} chunks", style=SOFT)
        if self.dirty:
            head.append("   ● UNSAVED", style=f"bold {SEV['alert']}")
        self.query_one("#title", Static).update(head)

        tree = self.query_one("#tree", Tree)
        tree.clear()
        self._nodemeta = {}
        self._editval = {}
        tree.root.set_label(Text(os.path.basename(self.src), style=f"bold {FG}"))
        tree.root.data = (0, self.fsize, ACCENT)
        self._nodemeta[id(tree.root)] = (0, self.fsize, ACCENT)
        for i, c in enumerate(self.chunks):
            accent = PALETTE[i % len(PALETTE)]
            lbl = Text()
            lbl.append(f"{str(c.get('id', '?')).strip():<6}", style=f"bold {accent}")
            lbl.append(f"0x{c.get('offset', 0):08x}  ", style=DIM)
            lbl.append(f"{c.get('size', 0):,}b  ", style=SOFT)
            lbl.append(str(c.get("summary", "")), style=FG)
            node = tree.root.add(lbl)
            node.data = (c.get("offset", 0), c.get("size", 0), accent)
            self._nodemeta[id(node)] = node.data
            for fl in c.get("fields", []):
                abs_off = _field_abs(c, fl)
                flbl = Text()
                flbl.append(f"{fl['name']}", style=SOFT)
                flbl.append(" = ", style=DIM)
                flbl.append(f"{fl['value']!s}", style=accent)
                if fl.get("note"):
                    flbl.append(f"  {fl['note']}", style=DIM)
                fnode = node.add_leaf(flbl)
                fnode.data = (abs_off, fl.get("len") or 0, accent)
                self._nodemeta[id(fnode)] = fnode.data
                if abs_off is not None:
                    self._editval[id(fnode)] = fl.get("value")
            # per-element rows: MIDI events, MP3 frames, device params, etc. --
            # the deep detail inspect --frames/--verbose shows. Rows carry no
            # uniform byte offset, so a row node uses its own if present else the
            # chunk's range for the hex pane.
            rows = c.get("rows") or []
            for row in rows[:_ROW_CAP]:
                rlbl = Text("  ".join(f"{k}={v}" for k, v in row.items()),
                            style=SOFT)
                roff = row.get("offset") if isinstance(row.get("offset"), int) else None
                rlen = row.get("size") if isinstance(row.get("size"), int) else 0
                rnode = node.add_leaf(rlbl)
                rnode.data = ((roff, rlen, accent) if roff is not None
                              else node.data)
                self._nodemeta[id(rnode)] = rnode.data
            if len(rows) > _ROW_CAP:
                more = node.add_leaf(Text(f"... {len(rows) - _ROW_CAP} more rows",
                                          style=DIM))
                self._nodemeta[id(more)] = node.data
        tree.root.expand()
        self._render_anomalies()
        self._show(0, self.fsize, ACCENT, os.path.basename(self.src), "")

    def _render_anomalies(self):
        panel = self.query_one("#anom", Static)
        t = Text()
        t.append("forensics  ", style=f"bold {ACCENT}")
        if not self.findings:
            t.append("clean: no findings", style=SOFT)
            panel.update(t)
            return
        t.append(f"{len(self.findings)} finding(s)\n", style=SOFT)
        for f in self.findings[:8]:
            sev = f.get("severity", "notice")
            t.append(f"  {sev:<7}", style=f"bold {SEV.get(sev, SOFT)}")
            t.append(f"0x{f.get('offset', 0):08x} ", style=DIM)
            t.append(f"{f.get('message', '')}\n", style=FG)
        if len(self.findings) > 8:
            t.append(f"  .. {len(self.findings) - 8} more\n", style=DIM)
        panel.update(t)

    def _show(self, off, length, accent, name, note):
        detail = self.query_one("#detail", Static)
        d = Text()
        d.append(name, style=f"bold {accent}")
        if off is not None:
            d.append(f"   @ 0x{off:08x}   {length:,} bytes", style=SOFT)
        else:
            d.append("   (derived, no byte range)", style=DIM)
        if note:
            d.append(f"\n{note}", style=SOFT)
        detail.update(d)
        self.query_one("#hex", Static).update(hex_text(self.work, off, length, accent))

    def on_tree_node_highlighted(self, event):
        self._cur_node = event.node
        if self._edit_target:            # moving off the field cancels an edit
            self.action_cancel_edit()
        data = self._nodemeta.get(id(event.node))
        if not data:
            return
        off, length, accent = data
        label = event.node.label
        name = label.plain if isinstance(label, Text) else str(label)
        self._show(off, length, accent, name.strip(), "")

    # ── inline byte / value editor with live hex preview ──────────────

    def action_edit_field(self):
        node = self._cur_node
        data = self._nodemeta.get(id(node)) if node else None
        if not data:
            self.notify("highlight a field first", severity="warning")
            return
        off, length, accent = data
        if off is None or not length:
            self.notify("this node has no editable byte range", severity="warning")
            return
        if length > _HEXEDIT_CAP:
            self.notify(f"region too large to edit ({length:,} bytes); pick a field",
                        severity="warning")
            return
        raw = _read(self.work, off, length)
        name = (node.label.plain if isinstance(node.label, Text)
                else str(node.label)).strip()
        # value mode if the field's displayed value round-trips to its bytes.
        fmt = infer_enc(self._editval.get(id(node)), raw)
        if fmt is not None:
            mode, initial = "value", str(self._editval[id(node)])
        else:
            mode, initial = "hex", raw.hex(" ")
        self._edit_target = {"off": off, "length": length, "name": name,
                             "mode": mode, "fmt": fmt, "accent": accent}
        bar = self.query_one("#editbar", Input)
        kind = f"value ({fmt})" if mode == "value" else f"raw hex ({length}B)"
        bar.border_title = (f"edit {name} @ 0x{off:08x}  {kind}  "
                            f"enter=write  esc=cancel")
        bar.value = initial
        bar.remove_class("hidden")
        bar.focus()
        self._render_preview()

    def _patch_from_input(self, text):
        """Turn the current editbar text into bytes, or None if invalid/incomplete
        for the field's length."""
        tgt = self._edit_target
        try:
            if tgt["mode"] == "value":
                patch = encode_value(tgt["fmt"], text.strip())
            else:
                patch = bytes.fromhex(text.replace(" ", "").replace("\n", ""))
        except (ValueError, struct.error):
            return None
        return patch if len(patch) == tgt["length"] else None

    def _render_preview(self):
        tgt = self._edit_target
        if not tgt:
            return
        text = self.query_one("#editbar", Input).value
        patch = self._patch_from_input(text)
        detail = self.query_one("#detail", Static)
        d = Text()
        d.append(f"editing {tgt['name']}", style=f"bold {PEND}")
        d.append("   ", style=SOFT)
        d.append("valid, enter to write" if patch is not None
                 else "invalid / wrong length", style=SOFT if patch else SEV["alert"])
        detail.update(d)
        t = Text()
        t.append("preview (unsaved)\n", style=f"bold {PEND}")
        if patch is None:
            _hex_rows(t, tgt["off"], _read(self.work, tgt["off"], tgt["length"]), DIM)
        else:
            _hex_rows(t, tgt["off"], patch, PEND)
        self.query_one("#hex", Static).update(t)

    def on_input_changed(self, event):
        if event.input.id == "editbar" and self._edit_target:
            self._render_preview()

    def on_input_submitted(self, event):
        if event.input.id != "editbar" or not self._edit_target:
            return
        tgt = self._edit_target
        patch = self._patch_from_input(event.value)
        if patch is None:
            self.notify(f"invalid value for a {tgt['length']}-byte field",
                        severity="error")
            return
        try:
            with open(self.work, "rb") as f:
                data = f.read()
            new = data[:tgt["off"]] + patch + data[tgt["off"] + tgt["length"]:]
        except OSError as e:
            self.notify(f"error: {e}", severity="error")
            return
        self._end_edit()
        self._apply_to_work(new)
        self.notify(f"patched {len(patch)} bytes (unsaved -- ctrl+s to save)")

    def action_cancel_edit(self):
        if not self._edit_target:
            return
        self._end_edit()
        if self._cur_node:
            data = self._nodemeta.get(id(self._cur_node))
            if data:
                name = (self._cur_node.label.plain
                        if isinstance(self._cur_node.label, Text)
                        else str(self._cur_node.label)).strip()
                self._show(*data, name, "")

    def _end_edit(self):
        self._edit_target = None
        bar = self.query_one("#editbar", Input)
        bar.value = ""
        bar.add_class("hidden")
        self.query_one("#tree", Tree).focus()

    # ── other actions ─────────────────────────────────────────────────

    def action_open(self):
        if self.dirty:
            self.push_screen(
                ConfirmScreen("unsaved changes -- save before opening another?"),
                self._resolve_pending(self._browse))
        else:
            self._browse()

    def _browse(self):
        start = os.path.dirname(os.path.abspath(self.src)) if self.src else os.getcwd()

        def after(path):
            if path and os.path.isfile(path):
                self._open_path(path)

        self.push_screen(BrowseScreen(start), after)

    def action_edit(self):
        if not self.work:
            self.notify("open a file first (o)", severity="warning")
            return
        prof = edit_profile(self.work)
        if prof is None:
            self.notify(f"no metadata editor for this format ({self.fmt})",
                        severity="warning")
            return

        def after(result):
            if result and result.get("new_data") is not None:
                self._apply_to_work(result["new_data"])
                n = len(result.get("applied", []))
                self.notify(f"edited {n} field(s) (unsaved -- ctrl+s to save)")

        self.push_screen(EditScreen(self.work, prof[0], prof[1]), after)

    def action_strip(self):
        if not self.work:
            self.notify("open a file first (o)", severity="warning")
            return
        try:
            _fmt, new_data, removed = _write_strip(self.work)
        except (EditError, OSError, ValueError) as e:
            self.notify(f"strip failed: {e}", severity="error")
            return
        self._apply_to_work(new_data)
        what = ", ".join(removed) if removed else "nothing to remove"
        self.notify(f"stripped: {what} (unsaved -- ctrl+s to save)")

    def action_expand_all(self):
        self.query_one("#tree", Tree).root.expand_all()

    def action_collapse_all(self):
        for node in self.query_one("#tree", Tree).root.children:
            node.collapse_all()
