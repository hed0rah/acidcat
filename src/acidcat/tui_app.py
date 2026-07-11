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
_UNDO_CAP = 50         # most undo deltas to keep
_UNDO_BYTES_CAP = 64 * 1024 * 1024   # total delta bytes kept (latest always kept)
_DIFF_CAP = 200        # most changed regions to list in the pending-changes view

# the field value<->bytes engine (struct inference, named codecs, the three
# bit-field encodings) lives in core/fieldcodec.py so the CLI and tests share
# it without a textual dependency; names are re-exported here for existing
# importers.
from acidcat.core.fieldcodec import (  # noqa: F401
    _BE_FMTS, _BITMAPS, _CODECS, _DYNMAPS, _field_abs, _resolve_in_map,
    bitfield_apply, bitfield_extract, decode_value, enc_size, encode_value,
    infer_enc, parse_bitfield, parse_bitsdyn, parse_bitsmap, resolve_bitsmap,
)


def _read(path, off, length):
    try:
        with open(path, "rb") as f:
            f.seek(off)
            return f.read(length)
    except OSError:
        return b""


def _fuzzy(query, text):
    """fzf-style subsequence match: every char of `query` appears in `text`, in
    order, case-insensitively. The default TUI search over field names/values."""
    q, t = query.lower(), text.lower()
    i = 0
    for ch in t:
        if i < len(q) and ch == q[i]:
            i += 1
    return i == len(q)


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


class HexPane(Static):
    """The right-hand hex view. Focusable so it can host in-place hex editing:
    when the app is in hex-edit mode, key events route to the app's handler
    (cursor movement + nibble overwrite); otherwise it behaves as a plain
    read-only pane."""
    can_focus = True

    def on_key(self, event):
        if getattr(self.app, "_hexedit", None):
            self.app._hexedit_key(event)


# tagged-audio text fields the write engine (mutagen) can set, keyed by the
# walker's field name: ID3 frame ids (mp3) and Vorbis comment keys (flac/ogg).
_ID3_TEXT = {"TIT2": "title", "TPE1": "artist", "TALB": "album", "TCON": "genre",
             "COMM": "comment", "TDRC": "date", "TYER": "date", "TBPM": "bpm",
             "TKEY": "key", "TRCK": "track"}
_VORBIS_TEXT = {"TITLE": "title", "ARTIST": "artist", "ALBUM": "album",
                "GENRE": "genre", "COMMENT": "comment", "DESCRIPTION": "comment",
                "DATE": "date", "BPM": "bpm", "KEY": "key", "INITIALKEY": "key",
                "TRACKNUMBER": "track"}


def text_field_for(profile, field_name):
    """If `field_name` (a walker field name) is a variable-length text field the
    write engine can edit, return the engine field name to route it through;
    else None. These must NOT be same-length byte-patched -- a longer title
    shifts the file -- so the editor re-serializes via the metadata engine."""
    if profile == "WAV":
        from acidcat.core.edit_riff import _INFO_TAGS
        rev = {v.decode("latin1").strip(): k for k, v in _INFO_TAGS.items()}
        return rev.get(field_name)
    if profile == "AIFF":
        from acidcat.core.edit_aiff import _AIFF_TEXT
        rev = {v.decode("latin1").strip(): k for k, v in _AIFF_TEXT.items()}
        return rev.get(field_name)
    if profile == "tagged":
        n = field_name.strip()
        return _ID3_TEXT.get(n) or _VORBIS_TEXT.get(n.upper())
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


class HelpScreen(ModalScreen):
    """Key reference overlay. Any of esc / ? closes it."""

    CSS = """
    HelpScreen { align: center middle; }
    #helpbox { width: 74; height: auto; max-height: 90%; border: round #56e0f0;
               background: #10161a; padding: 1 2; }
    """
    BINDINGS = [("escape", "close", "close"), ("question_mark", "close", "close")]

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("acidcat tui  --  keys\n\n", style=f"bold {ACCENT}")
        rows = [
            ("arrows / enter", "move + expand the tree"),
            ("a / c", "expand all / collapse all"),
            ("g", "goto offset (0x.. or decimal)"),
            ("/", "search: text=fuzzy name/value, 0x..=hex, \"..\"=ascii"),
            ("n / N", "next / previous search match"),
            ("f", "jump to the next forensics finding"),
            ("x", "follow a pointer field to where it points (flags dangling)"),
            ("m", "byte map: where the file's bytes go, biggest regions first"),
            ("y", "yank the selected bytes as hex to the clipboard"),
            ("d", "review all pending changes (offset old->new) before save"),
            ("e", "edit the selected field (value or hex)"),
            ("ctrl+t", "toggle the edit between value and raw hex"),
            ("tab", "hex-edit the field in the pane (arrows move, 0-9a-f type)"),
            ("w", "edit tags (metadata form)"),
            ("s", "strip identifying metadata"),
            ("ctrl+s", "save to the original (writes a _original backup)"),
            ("ctrl+z / ctrl+r", "undo / redo the last edit"),
            ("o", "open another file"),
            ("esc", "cancel the current edit / prompt"),
            ("q", "quit"),
        ]
        for k, d in rows:
            t.append(f"  {k:16}", style=f"bold {PEND}")
            t.append(f"{d}\n", style=SOFT)
        t.append("\nEdits go to a temp working copy; nothing touches the original "
                 "until ctrl+s.", style=DIM)
        with Vertical(id="helpbox"):
            yield Static(t)

    def action_close(self):
        self.dismiss(None)


class DiffScreen(ModalScreen):
    """Review all pending byte changes (working copy vs the original) before a
    save. Any of esc / d closes it."""

    CSS = """
    DiffScreen { align: center middle; }
    #diffbox { width: 82; height: auto; max-height: 90%; border: round #ffcc55;
               background: #10161a; padding: 1 2; }
    """
    BINDINGS = [("escape", "close", "close"), ("d", "close", "close")]

    def __init__(self, regions, src_len, work_len):
        super().__init__()
        self.regions = regions
        self.src_len = src_len
        self.work_len = work_len

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("pending changes  ", style=f"bold {ACCENT}")
        if self.src_len != self.work_len:
            t.append(f"(file size {self.src_len:,} -> {self.work_len:,} bytes)\n",
                     style=SOFT)
        elif not self.regions:
            t.append("none -- working copy matches the original\n", style=SOFT)
        else:
            t.append(f"{len(self.regions)} region(s) vs the original\n", style=SOFT)
        for off, old, new in self.regions[:_DIFF_CAP]:
            t.append(f"\n0x{off:08x}  ", style=f"bold {PEND}")
            t.append(f"{len(old)}B\n", style=DIM)
            t.append("  old ", style=SOFT)
            t.append(old[:24].hex(" ") + (" .." if len(old) > 24 else ""), style=DIM)
            t.append("\n  new ", style=SOFT)
            t.append(new[:24].hex(" ") + (" .." if len(new) > 24 else ""),
                     style=PEND)
            t.append("\n")
        if len(self.regions) > _DIFF_CAP:
            t.append(f"\n.. {len(self.regions) - _DIFF_CAP} more regions\n", style=DIM)
        t.append("\nctrl+s to save, esc to keep editing.", style=DIM)
        with Vertical(id="diffbox"):
            yield Static(t)

    def action_close(self):
        self.dismiss(None)


class MapScreen(ModalScreen):
    """A byte-budget map: where the file's bytes actually go, top-level regions
    biggest first with a proportional bar. Any of esc / m closes it."""

    CSS = """
    MapScreen { align: center middle; }
    #mapbox { width: 86; height: auto; max-height: 90%; border: round #56e0f0;
              background: #10161a; padding: 1 2; }
    """
    BINDINGS = [("escape", "close", "close"), ("m", "close", "close")]

    def __init__(self, segments, fsize, unaccounted):
        super().__init__()
        self.segments = segments
        self.fsize = fsize
        self.unaccounted = unaccounted

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("byte map  ", style=f"bold {ACCENT}")
        t.append(f"{self.fsize:,} bytes, {len(self.segments)} top-level region(s)\n",
                 style=SOFT)
        for i, (cid, off, size, pct, accent) in enumerate(self.segments):
            bar = "#" * max(1, round(pct / 100 * 40)) if size else ""
            t.append(f"\n0x{off:08x}  ", style=DIM)
            t.append(f"{cid:<8}", style=f"bold {accent}")
            t.append(f"{size:>12,}  {pct:5.1f}%\n", style=SOFT)
            t.append("  " + bar + "\n", style=accent)
        if self.unaccounted > 0:
            t.append(f"\n{self.unaccounted:,} bytes unaccounted "
                     f"({self.unaccounted / self.fsize * 100:.1f}%): gaps, chunk "
                     f"headers, or trailing data\n", style=f"bold {SEV['warn']}")
        t.append("\nesc / m to close.", style=DIM)
        with Vertical(id="mapbox"):
            yield Static(t)

    def action_close(self):
        self.dismiss(None)


class AcidcatTUI(App):
    CSS = """
    Screen { background: #10161a; }
    #tree { width: 48%; border: round #56e0f0; padding: 0 1; }
    #right { width: 52%; }
    #detail { height: auto; border: round #66e88a; padding: 0 1; color: #e6e6e1; }
    #hexwrap { border: round #56e0f0; }
    #hex { padding: 0 1; }
    #anomwrap { height: 30%; border: round #ff6e83; }
    #anom { height: auto; padding: 0 1; }
    #editbar { dock: bottom; height: 3; border: round #ffcc55; background: #10161a; }
    #editbar.hidden { display: none; }
    Tree { background: #10161a; }
    Tree > .tree--guides { color: #2a3540; }
    Tree > .tree--guides-selected { color: #56e0f0; }
    """

    BINDINGS = [
        ("q", "request_quit", "quit"),
        ("g", "goto", "goto offset"),
        ("slash", "search", "search"),
        ("n", "search_next", "next match"),
        ("N", "search_prev", "prev match"),
        ("f", "next_finding", "next finding"),
        ("x", "follow_xref", "follow pointer"),
        ("y", "yank", "yank hex"),
        ("d", "diff", "pending changes"),
        ("m", "map", "byte map"),
        ("ctrl+s", "save", "save"),
        ("ctrl+z", "undo", "undo"),
        ("ctrl+r", "redo", "redo"),
        ("o", "open", "open file"),
        ("e", "edit_field", "edit field"),
        ("tab", "hex_focus", "hex edit"),
        ("ctrl+t", "toggle_mode", "value/hex"),
        ("w", "edit", "edit tags"),
        ("s", "strip", "strip meta"),
        ("a", "expand_all", "expand"),
        ("c", "collapse_all", "collapse"),
        ("question_mark", "help", "help"),
        ("escape", "cancel_edit", "cancel edit"),
    ]

    def check_action(self, action, parameters):
        # while a modal (edit form / file browser / help / diff / map / confirm)
        # is open, the app-global single-letter bindings must not fire under it
        # -- so typing in the browser or a form does not trigger edit/strip/etc.
        if len(self.screen_stack) > 1:
            return False
        return True

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
        self._nodekey = {}        # id(node) -> stable key, to restore the cursor
                                  # and expansion across the post-edit tree rebuild
        self._editval = {}        # id(node) -> (value, enc, raw)  for field nodes
        self._textfield = {}      # id(node) -> engine field  for variable-length text
        self._profile = None      # edit profile of the current file (WAV/AIFF/...)
        self._prefer_be = False   # format is big-endian: bias infer_enc that way
        self._cur_node = None     # last highlighted tree node
        self._edit_target = None  # active inline edit: dict(off,length,name,mode,fmt,accent)
        self._hexedit = None      # active in-pane hex edit: dict(off,length,buf,cur,nib)
        self._undo = []           # working-copy byte snapshots for undo
        self._redo = []           # snapshots popped by undo, for redo
        self._prompt = None       # active editbar prompt: dict(kind, ...)
        self._allnodes = []       # (node, off, length) for offset/fuzzy navigation
        self._search = None       # active search: dict(desc, hits, idx)
        self._finding_idx = -1    # cursor into self.findings for jump-to-finding
        self._xref = {}           # id(field node) -> absolute target offset (pointer)

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with Horizontal():
            yield Tree("file", id="tree")
            with Vertical(id="right"):
                yield Static(id="detail")
                with VerticalScroll(id="hexwrap"):
                    yield HexPane(id="hex")
                with VerticalScroll(id="anomwrap"):
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
        self._undo = []
        self._src_stat = self._stat_src()
        self._force_stale = False

    def _stat_src(self):
        """(mtime_ns, size) of the source file, or None if it can't be stat'd.
        Taken at open and after each save; save refuses on a mismatch so an
        external change is never silently clobbered (and the _original backup
        never captures bytes the user wasn't editing)."""
        try:
            st = os.stat(self.src)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _discard_work(self):
        w = self.work
        self.work = None
        if w and os.path.isfile(w):
            try:
                os.unlink(w)
            except OSError:
                pass

    @staticmethod
    def _minimal_delta(old, new):
        """The minimal changed region between two byte strings as
        (start, old_segment, new_segment) -- common prefix and suffix trimmed.
        A same-length field/hex patch yields a few-byte delta even on a huge
        file, so undo history holds byte ranges, not whole-file snapshots."""
        n = min(len(old), len(new))
        start = 0
        while start < n and old[start] == new[start]:
            start += 1
        # suffix length, not crossing into the prefix on either side
        suf = 0
        while (suf < n - start
               and old[len(old) - 1 - suf] == new[len(new) - 1 - suf]):
            suf += 1
        return start, old[start:len(old) - suf], new[start:len(new) - suf]

    def _apply_to_work(self, new_bytes):
        """Write edited bytes to the working copy (no disk write to the original
        yet), recording a minimal-diff undo delta, and refresh."""
        with open(self.work, "rb") as f:
            old = f.read()
        start, old_seg, new_seg = self._minimal_delta(old, new_bytes)
        if old_seg == new_seg:                # nothing actually changed
            return
        self._undo.append((start, old_seg, new_seg))
        self._redo = []           # a fresh edit invalidates the redo history
        self._undo = self._undo[-_UNDO_CAP:]
        # cap by total delta bytes so history cannot pin gigabytes; the most
        # recent delta always survives.
        while (len(self._undo) > 1
               and sum(len(o) + len(n) for _s, o, n in self._undo) > _UNDO_BYTES_CAP):
            self._undo.pop(0)
        with open(self.work, "wb") as f:
            f.write(new_bytes)
        self.dirty = True         # cheap: no whole-file compare on the hot path
        self._load()

    def _recompute_dirty(self):
        """Dirty iff the working copy differs from the saved file. Only called on
        undo/redo (rare), so the whole-file compare stays off the edit hot path;
        a plain edit sets dirty=True directly."""
        try:
            with open(self.work, "rb") as f:
                w = f.read()
            with open(self.src, "rb") as f:
                self.dirty = w != f.read()
        except OSError:
            self.dirty = True

    def _apply_delta(self, start, seg_out, seg_in):
        """Replace the bytes at `start` currently equal to `seg_out` with
        `seg_in` in the working copy (the shared undo/redo primitive)."""
        with open(self.work, "rb") as f:
            data = f.read()
        with open(self.work, "wb") as f:
            f.write(data[:start] + seg_in + data[start + len(seg_out):])

    def action_undo(self):
        if not self._undo:
            self.notify("nothing to undo")
            return
        start, old_seg, new_seg = self._undo.pop()
        self._apply_delta(start, new_seg, old_seg)      # revert new -> old
        self._redo.append((start, old_seg, new_seg))
        self._redo = self._redo[-_UNDO_CAP:]
        self._recompute_dirty()
        self._load()
        self.notify("undid last edit")

    def action_redo(self):
        if not self._redo:
            self.notify("nothing to redo")
            return
        start, old_seg, new_seg = self._redo.pop()
        self._apply_delta(start, old_seg, new_seg)      # re-apply old -> new
        self._undo.append((start, old_seg, new_seg))
        self._undo = self._undo[-_UNDO_CAP:]
        self._recompute_dirty()
        self._load()
        self.notify("redid last edit")

    def action_save(self):
        if not self.work:
            return
        if not self.dirty:
            self.notify("no unsaved changes")
            return
        if self._src_stat is not None and self._stat_src() != self._src_stat:
            if not self._force_stale:
                self._force_stale = True
                # this is action_save, bound to ctrl+s; plain s is strip, so
                # naming the wrong key would silently strip instead of forcing
                self.notify("file changed on disk since it was opened; "
                            "press ctrl+s again to overwrite it anyway",
                            severity="error")
                return
        self._force_stale = False
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
        self._src_stat = self._stat_src()
        self._load()
        if backup:
            msg = f"saved; backup {os.path.basename(backup)}"
        elif not self._backed_up and os.path.exists(writer.backup_path(self.src)):
            # first save found a <name>_original already on disk and kept it;
            # that file may predate acidcat and not hold this original
            msg = "saved; existing backup kept"
        else:
            msg = "saved"
        self.notify(msg)

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
                if self.dirty:      # save failed: keep the session and the edits
                    return
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
        except Exception as e:
            # a crafted/corrupt file may make a walker raise something other
            # than Unsupported; the TUI opens files on mount, so this must not
            # crash the session (the DoS threat model is degrade-not-die)
            self.fmt, self.chunks, self.warns = (
                "walk failed", [], [f"{e.__class__.__name__}: {e}"])
        self._prefer_be = self.fmt in _BE_FMTS
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

        prof = edit_profile(self.work)
        self._profile = prof[0] if prof else None
        tree = self.query_one("#tree", Tree)
        # a rebuild (after an edit / undo / save) must not dump the user at the
        # root with everything collapsed: remember the highlighted node and the
        # expanded chunks by stable key (chunk index / field ordinal), restore
        # them after the rebuild.
        cur_key = (self._nodekey.get(id(self._cur_node))
                   if self._cur_node is not None else None)
        expanded = {self._nodekey[id(n)] for n in tree.root.children
                    if n.is_expanded and id(n) in self._nodekey}
        tree.clear()
        self._nodemeta = {}
        self._editval = {}
        self._textfield = {}
        self._nodekey = {}
        self._allnodes = []       # rebuilt each load, for goto/search/finding jumps
        self._xref = {}
        keyed = {}
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
            self._nodekey[id(node)] = ("chunk", i)
            keyed[("chunk", i)] = node
            self._allnodes.append((node, c.get("offset", 0), c.get("size", 0)))
            for j, fl in enumerate(c.get("fields", [])):
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
                self._nodekey[id(fnode)] = ("field", i, j)
                keyed[("field", i, j)] = fnode
                if fl.get("xref") is not None:
                    self._xref[id(fnode)] = fl["xref"]
                if abs_off is not None:
                    self._allnodes.append((fnode, abs_off, fl.get("len") or 0))
                if abs_off is not None:
                    self._editval[id(fnode)] = (fl.get("value"), fl.get("enc"),
                                                fl.get("raw"))
                    mf = text_field_for(self._profile, fl["name"])
                    if mf is not None:
                        self._textfield[id(fnode)] = mf
            # per-element rows: MIDI events, MP3 frames, device params, etc. --
            # the deep detail inspect --frames/--verbose shows. Rows carry no
            # uniform byte offset, so a row node uses its own if present else the
            # chunk's range for the hex pane.
            rows = c.get("rows") or []
            for k, row in enumerate(rows[:_ROW_CAP]):
                rlbl = Text("  ".join(f"{k2}={v}" for k2, v in row.items()),
                            style=SOFT)
                roff = row.get("offset") if isinstance(row.get("offset"), int) else None
                rlen = row.get("size") if isinstance(row.get("size"), int) else 0
                rnode = node.add_leaf(rlbl)
                rnode.data = ((roff, rlen, accent) if roff is not None
                              else node.data)
                self._nodemeta[id(rnode)] = rnode.data
                self._nodekey[id(rnode)] = ("row", i, k)
                keyed[("row", i, k)] = rnode
            if len(rows) > _ROW_CAP:
                more = node.add_leaf(Text(f"... {len(rows) - _ROW_CAP} more rows",
                                          style=DIM))
                self._nodemeta[id(more)] = node.data
        tree.root.expand()
        for ek in expanded:
            n = keyed.get(ek)
            if n is not None:
                n.expand()
        self._render_anomalies()
        target = keyed.get(cur_key)
        if target is not None:
            if target.parent is not None and not target.parent.is_expanded:
                target.parent.expand()
            self._cur_node = target
            # node lines are computed on the next refresh; moving now lands on -1
            self.call_after_refresh(tree.move_cursor, target)
            off, length, accent = self._nodemeta[id(target)]
            self._show(off, length, accent, self._node_name(target),
                       self._edit_hint(target, off, length))
        else:
            self._show(0, self.fsize, ACCENT, os.path.basename(self.src), "")

    def _render_anomalies(self):
        panel = self.query_one("#anom", Static)
        t = Text()
        t.append("forensics  ", style=f"bold {ACCENT}")
        if not self.findings:
            t.append("clean: no findings", style=SOFT)
            panel.update(t)
            return
        # severity legend so the colors are readable, then every finding
        # numbered (press f to jump the tree/hex to the next one). The panel
        # lives in a VerticalScroll, so findings past the fold stay reachable.
        t.append(f"{len(self.findings)} finding(s)   ", style=SOFT)
        t.append("alert", style=f"bold {SEV['alert']}")
        t.append(" / ", style=DIM)
        t.append("warn", style=f"bold {SEV['warn']}")
        t.append(" / ", style=DIM)
        t.append("notice", style=f"bold {SEV['notice']}")
        t.append("   (f = jump)\n", style=DIM)
        for i, f in enumerate(self.findings):
            sev = f.get("severity", "notice")
            marker = ">" if i == self._finding_idx else " "
            t.append(f" {marker}{i + 1:>2} ", style=f"bold {ACCENT}" if
                     i == self._finding_idx else DIM)
            t.append(f"{sev:<7}", style=f"bold {SEV.get(sev, SOFT)}")
            t.append(f"0x{f.get('offset', 0):08x} ", style=DIM)
            t.append(f"{f.get('message', '')}\n", style=FG)
        panel.update(t)

    @staticmethod
    def _node_name(node):
        lbl = node.label
        return (lbl.plain if isinstance(lbl, Text) else str(lbl)).strip()

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

    def action_help(self):
        self.push_screen(HelpScreen())

    # ── navigation: goto-offset, search, jump-to-finding ──────────────

    def _select_node(self, node):
        """Move the tree cursor to `node`, expanding its parents, and refresh the
        detail/hex panes -- the shared landing used by goto/search/finding."""
        tree = self.query_one("#tree", Tree)
        p = node.parent
        while p is not None:
            if not p.is_expanded:
                p.expand()
            p = p.parent
        self._cur_node = node
        self.call_after_refresh(tree.move_cursor, node)
        data = self._nodemeta.get(id(node))
        if data:
            off, length, accent = data
            self._show(off, length, accent, self._node_name(node),
                       self._edit_hint(node, off, length))

    def _node_containing(self, offset):
        """The most specific tree node whose byte range covers `offset` (a field
        beats its enclosing chunk), or None. Ties break to the smallest range."""
        best = None
        for node, off, length in self._allnodes:
            if length and off <= offset < off + length:
                if best is None or length < best[1]:
                    best = (node, length)
        return best[0] if best else None

    def _jump_to_offset(self, offset, hlen=1, label=""):
        """Land on `offset`: select the node that contains it if any, and show
        the hex there. Used by goto and byte-search hits."""
        node = self._node_containing(offset)
        if node is not None:
            self._select_node(node)
        acc = PEND
        name = label or (self._node_name(node) if node else f"offset 0x{offset:08x}")
        self._show(offset, hlen, acc, name,
                   "" if node else "no chunk covers this offset")

    def _arm_prompt(self, kind, title, initial=""):
        """Reuse #editbar as a one-line prompt (goto/search). Distinct from a
        field edit: on_input_submitted routes on self._prompt first."""
        if self._edit_target:
            self.action_cancel_edit()
        self._prompt = {"kind": kind}
        bar = self.query_one("#editbar", Input)
        bar.value = initial
        bar.remove_class("hidden")
        bar.border_title = title
        bar.focus()

    def action_goto(self):
        self._arm_prompt("goto", "goto offset (0x.. or decimal)  enter  esc")

    def action_search(self):
        self._arm_prompt(
            "search",
            "search: text=fuzzy name/value, 0x..=hex bytes, \"..\"=ascii  n/N cycle")

    def _run_goto(self, text):
        text = text.strip()
        if not text:
            return
        try:
            offset = int(text, 0)
        except ValueError:
            self.notify(f"not an offset: {text!r}", severity="error")
            return
        if not (0 <= offset < self.fsize):
            self.notify(f"offset 0x{offset:x} outside the file (0..{self.fsize:,})",
                        severity="error")
            return
        self._jump_to_offset(offset, 1, f"goto 0x{offset:08x}")

    def _run_search(self, text):
        text = text.strip()
        if not text:
            return
        needle = self._search_needle(text)
        if needle is not None:                       # raw-byte search
            with open(self.work, "rb") as f:
                data = f.read()
            hits, pos = [], data.find(needle)
            while pos != -1 and len(hits) < 4096:
                hits.append(("byte", pos, len(needle)))
                pos = data.find(needle, pos + 1)
            desc = f"{len(needle)} byte(s)"
        else:                                        # fuzzy name/value search
            hits = [("node", n) for n, _o, _l in self._allnodes
                    if _fuzzy(text, self._node_name(n))]
            desc = f"'{text}'"
        if not hits:
            self.notify(f"no match for {desc}", severity="warning")
            self._search = None
            return
        self._search = {"desc": desc, "hits": hits, "idx": -1}
        self.notify(f"{len(hits)} match(es) for {desc}; n/N to cycle")
        self._search_step(1)

    @staticmethod
    def _search_needle(text):
        """Bytes to search for, or None if `text` is a fuzzy (name/value) query.
        0x.. / bare even-length hex -> those bytes; "..'/'.." -> ascii bytes."""
        t = text.strip()
        if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
            return t[1:-1].encode("utf-8", "replace")
        h = t[2:] if t[:2].lower() == "0x" else t
        h = h.replace(" ", "")
        if t[:2].lower() == "0x" or (len(h) >= 2 and len(h) % 2 == 0
                                     and all(c in "0123456789abcdefABCDEF" for c in h)):
            try:
                return bytes.fromhex(h)
            except ValueError:
                return None
        return None

    def _search_step(self, direction):
        s = self._search
        if not s or not s["hits"]:
            self.notify("no active search (press / to search)")
            return
        s["idx"] = (s["idx"] + direction) % len(s["hits"])
        hit = s["hits"][s["idx"]]
        pos = f"{s['idx'] + 1}/{len(s['hits'])}"
        if hit[0] == "byte":
            self._jump_to_offset(hit[1], hit[2],
                                 f"match {pos} @ 0x{hit[1]:08x}  ({s['desc']})")
        else:
            self._select_node(hit[1])
            self.notify(f"match {pos}  {s['desc']}")

    def action_search_next(self):
        self._search_step(1)

    def action_search_prev(self):
        self._search_step(-1)

    def action_next_finding(self):
        if not self.findings:
            self.notify("no forensics findings")
            return
        self._finding_idx = (self._finding_idx + 1) % len(self.findings)
        f = self.findings[self._finding_idx]
        off = f.get("offset", 0)
        self._jump_to_offset(
            off, 1, f"finding {self._finding_idx + 1}/{len(self.findings)}: "
            f"{f.get('message', '')[:60]}")
        self._render_anomalies()

    def action_yank(self):
        """Copy the selected node's bytes (as hex) to the clipboard -- a common
        forensics move (paste an interesting region into another tool)."""
        node = self._cur_node
        data = self._nodemeta.get(id(node)) if node else None
        if not data or data[0] is None or not data[1]:
            self.notify("nothing to yank (highlight a field/chunk)", severity="warning")
            return
        off, length, _ = data
        blob = _read(self.work, off, min(length, _HEX_CAP))
        hexs = blob.hex(" ")
        try:
            self.copy_to_clipboard(hexs)
            where = "clipboard"
        except Exception:
            where = "(clipboard unavailable)"
        note = f", capped at {_HEX_CAP}" if length > _HEX_CAP else ""
        self.notify(f"yanked {len(blob)} bytes as hex -> {where}{note}")

    def _pending_changes(self):
        """(regions, src_len, work_len): the changed byte regions between the
        working copy and the saved original, each (offset, old_bytes, new_bytes).
        For a same-length file every differing run is listed; a length change is
        reported as one region from the first difference (a text re-serialization
        shifts the tail, so per-run diffing there is not meaningful)."""
        try:
            with open(self.work, "rb") as f:
                work = f.read()
            with open(self.src, "rb") as f:
                src = f.read()
        except OSError:
            return [], 0, 0
        if len(src) != len(work):
            start, o, n = self._minimal_delta(src, work)
            return ([(start, o, n)] if o != n else []), len(src), len(work)
        regions = []
        i = 0
        while i < len(src) and len(regions) < _DIFF_CAP + 1:
            if src[i] != work[i]:
                j = i
                while j < len(src) and src[j] != work[j]:
                    j += 1
                regions.append((i, src[i:j], work[i:j]))
                i = j
            else:
                i += 1
        return regions, len(src), len(work)

    def action_diff(self):
        if not self.work:
            return
        regions, sl, wl = self._pending_changes()
        self.push_screen(DiffScreen(regions, sl, wl))

    def _byte_map(self):
        """(segments, unaccounted): the file's top-level byte regions biggest
        first, each (id, offset, size, pct, accent). Excludes the whole-file
        container and any chunk nested inside another (e.g. SF2 samples inside
        smpl), so the map answers 'where do the bytes go' at the top level."""
        cand = [(c["id"], c["offset"], c["size"], PALETTE[i % len(PALETTE)])
                for i, c in enumerate(self.chunks)
                if isinstance(c.get("offset"), int) and isinstance(c.get("size"), int)
                and c["size"] > 0 and not (c["offset"] == 0 and c["size"] >= self.fsize)]

        def nested(off, size):
            return any(o <= off and off + size <= o + s and s > size
                       for _i, o, s, _a in cand)
        top = [(cid, off, size, _a) for cid, off, size, _a in cand
               if not nested(off, size)]
        top.sort(key=lambda x: -x[2])
        fsize = max(1, self.fsize)
        segs = [(str(cid).strip()[:8], off, size, size / fsize * 100, a)
                for cid, off, size, a in top]
        unaccounted = max(0, self.fsize - sum(s for _c, _o, s, _a in top))
        return segs, unaccounted

    def action_map(self):
        if not self.chunks:
            return
        segs, un = self._byte_map()
        self.push_screen(MapScreen(segs, self.fsize, un))

    def action_follow_xref(self):
        """Follow the selected field's pointer (its `xref` absolute offset) to
        where it points, flagging a dangling (out-of-bounds) one -- a real
        forensic tell as well as a navigation aid."""
        node = self._cur_node
        target = self._xref.get(id(node)) if node else None
        if target is None:
            self.notify("this field is not a pointer (no xref)", severity="warning")
            return
        if not (0 <= target < self.fsize):
            self.notify(f"DANGLING pointer -> 0x{target:x} is outside the file "
                        f"(0..0x{self.fsize:x})", severity="error")
            return
        self._jump_to_offset(target, 1, f"followed pointer -> 0x{target:08x}")

    def on_tree_node_highlighted(self, event):
        self._cur_node = event.node
        if self._edit_target:            # moving off the field cancels an edit
            self.action_cancel_edit()
        self._hexedit = None             # ditto an abandoned in-pane hex edit
        data = self._nodemeta.get(id(event.node))
        if not data:
            return
        off, length, accent = data
        hint = self._edit_hint(event.node, off, length)
        xref = self._xref.get(id(event.node))
        if xref is not None:
            danger = "" if 0 <= xref < self.fsize else " (DANGLING, out of bounds)"
            ptr = f"pointer -> 0x{xref:08x}{danger} -- press x to follow"
            hint = f"{hint}\n{ptr}" if hint else ptr
        self._show(off, length, accent, self._node_name(event.node), hint)

    def _edit_hint(self, node, off, length):
        """A short note in the detail pane telling the user how the highlighted
        field can be edited (value / enum / hex / text), so it's discoverable."""
        if off is None or not length:
            return ""
        if id(node) in self._textfield:
            return f"text-editable ({self._textfield[id(node)]}) -- press e"
        value, enc, raw = self._editval.get(id(node), (None, None, None))
        rb = _read(self.work, off, length)
        if enc is not None:
            bt = self._bit_target(off, value, enc)
            if bt is not None:
                if bt["mode"] == "bitfield":
                    return (f"value-editable ({bt['width']}-bit packed) -- "
                            "press e, or tab for hex")
                return "enum-editable -- press e, or tab for hex"
            try:
                if encode_value(enc, str(raw if raw is not None else value)) == rb:
                    return f"value-editable ({enc}) -- press e, or tab for hex"
            except (ValueError, struct.error):
                pass
        if infer_enc(value, rb, self._prefer_be) is not None:
            return "value-editable -- press e, or tab for hex"
        if length <= _HEXEDIT_CAP:
            return "hex-editable -- press e or tab"
        return ""

    # ── inline byte / value editor with live hex preview ──────────────

    def _bit_target(self, off, value, enc):
        """If enc is a bits/bitsmap/bitsdyn annotation, verify it against the
        working copy (the declared bits must decode to the displayed value; a
        wrong annotation must never write blind) and return the edit-target
        fields for it. None means not a bit annotation, or it did not verify --
        the caller falls back to plain value/hex editing."""
        for parse in (parse_bitsmap, parse_bitsdyn, parse_bitfield):
            p = parse(enc)
            if p is None:
                continue
            delta, clen, bitpos, width, extra = p
            cont_off = off + delta
            cur = _read(self.work, cont_off, clen)
            if len(cur) != clen or clen * 8 - bitpos - width < 0:
                return None
            tgt = {"off": cont_off, "length": clen, "fmt": None,
                   "bitpos": bitpos, "width": width}
            if parse is parse_bitsmap:
                ok = _BITMAPS.get(extra, {}).get(
                    bitfield_extract(cur, bitpos, width, 0)) == value
                tgt.update(mode="bitsmap", mapid=extra)
            elif parse is parse_bitsdyn:
                ok = _DYNMAPS[extra](cur).get(
                    bitfield_extract(cur, bitpos, width, 0)) == value
                tgt.update(mode="bitsdyn", dynid=extra)
            else:
                ok = bitfield_extract(cur, bitpos, width, extra) == value
                tgt.update(mode="bitfield", bias=extra)
            return tgt if ok else None
        return None

    def _arm_edit(self, target, initial):
        """Activate the edit bar for `target` with `initial` as the text."""
        self._edit_target = target
        bar = self.query_one("#editbar", Input)
        bar.value = initial
        bar.remove_class("hidden")
        self._update_edit_title()
        bar.focus()
        self._render_preview()

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
        name = self._node_name(node)
        # variable-length text field: edit as text through the metadata engine,
        # which re-serializes the chunk so a longer/shorter value is valid.
        mf = self._textfield.get(id(node))
        if mf is not None:
            value = self._editval.get(id(node), (None,))[0]
            self._arm_edit({"off": off, "length": length, "name": name,
                            "mode": "text", "fmt": None, "metafield": mf,
                            "accent": accent},
                           str(value) if value is not None else "")
            return
        if length > _HEXEDIT_CAP:
            self.notify(f"region too large to edit ({length:,} bytes); pick a field",
                        severity="warning")
            return
        raw_bytes = _read(self.work, off, length)
        value, enc, raw_val = self._editval.get(id(node), (None, None, None))
        # bit-packed / enum field: read-modify-write inside its container bytes
        # so neighbouring bit-fields survive. Only if the annotation verifies
        # against the working copy; else fall through to plain value/hex.
        if enc is not None:
            bt = self._bit_target(off, value, enc)
            if bt is not None:
                self._arm_edit({**bt, "name": name, "accent": accent}, str(value))
                return
        fmt = initial = None
        # 1) trust the walker's declared encoding ONLY if it reproduces the
        #    current bytes -- a wrong annotation must never write blind.
        if enc is not None:
            cand = raw_val if raw_val is not None else value
            try:
                if encode_value(enc, str(cand)) == raw_bytes:
                    fmt, initial = enc, str(cand)
            except (ValueError, struct.error):
                pass
        # 2) else infer the layout by round-tripping the displayed value.
        if fmt is None:
            fmt = infer_enc(value, raw_bytes, self._prefer_be)
            if fmt is not None:
                initial = str(value)
        # 3) else raw hex.
        if fmt is not None:
            mode = "value"
        else:
            mode, initial = "hex", raw_bytes.hex(" ")
        self._arm_edit({"off": off, "length": length, "name": name,
                        "mode": mode, "fmt": fmt, "accent": accent}, initial)

    def _update_edit_title(self):
        tgt = self._edit_target
        bar = self.query_one("#editbar", Input)
        if tgt["mode"] == "value":
            kind = f"value ({tgt['fmt']})"
        elif tgt["mode"] == "bitsmap":
            vals = list(_BITMAPS.get(tgt["mapid"], {}).values())
            kind = (f"enum ({tgt['mapid']}, {len(vals)} options)" if len(vals) > 8
                    else "enum: " + " | ".join(str(v) for v in vals))
        elif tgt["mode"] == "bitsdyn":
            cur = _read(self.work, tgt["off"], tgt["length"])
            opts = " | ".join(str(v) for v in _DYNMAPS[tgt["dynid"]](cur).values())
            kind = f"enum: {opts}"
        elif tgt["mode"] == "bitfield":
            kind = f"value ({tgt['width']}-bit packed field)"
        elif tgt["mode"] == "text":
            kind = f"text -> {tgt['metafield']} (variable length)"
        else:
            kind = f"raw hex ({tgt['length']}B)"
        toggle = "  ctrl+t=toggle" if tgt["fmt"] else ""
        bar.border_title = (f"edit {tgt['name']} @ 0x{tgt['off']:08x}  {kind}"
                            f"  enter=write  esc=cancel{toggle}")

    def action_toggle_mode(self):
        """Flip the active field edit between value and raw-hex. Only offered
        when the field has a known value encoding (fmt); a hex-only field stays
        hex. Converts the bar's current text to the other representation so the
        live preview stays consistent."""
        tgt = self._edit_target
        if not tgt:
            return
        if tgt["fmt"] is None:
            # enum/packed fields (bitsmap/bitsdyn/bitfield) are value-editable
            # by label or index in place; they just have no fmt to flip to hex
            if tgt.get("mode") in ("bitsmap", "bitsdyn", "bitfield"):
                self.notify("enum/packed field: edit the value in place "
                            "(no separate hex mode)", severity="warning")
            else:
                self.notify("this field is hex-only (no known value encoding)",
                            severity="warning")
            return
        bar = self.query_one("#editbar", Input)
        if tgt["mode"] == "value":
            try:                                  # value -> its bytes as hex
                bar.value = encode_value(tgt["fmt"], bar.value.strip()).hex(" ")
            except (ValueError, struct.error):
                pass                              # keep text; preview flags invalid
            tgt["mode"] = "hex"
        else:
            try:                                  # bytes -> decoded value
                b = bytes.fromhex(bar.value.replace(" ", ""))
                if len(b) == enc_size(tgt["fmt"]):
                    bar.value = str(decode_value(tgt["fmt"], b))
            except (ValueError, struct.error):
                pass
            tgt["mode"] = "value"
        self._update_edit_title()
        self._render_preview()

    def _patch_from_input(self, text):
        """Turn the current editbar text into bytes for the field's byte range, or
        None if invalid/incomplete."""
        tgt = self._edit_target
        try:
            if tgt["mode"] == "bitsmap":
                rawv = resolve_bitsmap(tgt["mapid"], text)
                cur = _read(self.work, tgt["off"], tgt["length"])
                if rawv is None or len(cur) != tgt["length"]:
                    return None
                return bitfield_apply(cur, tgt["bitpos"], tgt["width"], 0, rawv)
            if tgt["mode"] == "bitsdyn":
                cur = _read(self.work, tgt["off"], tgt["length"])
                if len(cur) != tgt["length"]:
                    return None
                rawv = _resolve_in_map(_DYNMAPS[tgt["dynid"]](cur), text)
                if rawv is None:
                    return None
                return bitfield_apply(cur, tgt["bitpos"], tgt["width"], 0, rawv)
            if tgt["mode"] == "bitfield":
                cur = _read(self.work, tgt["off"], tgt["length"])
                if len(cur) != tgt["length"]:
                    return None
                return bitfield_apply(cur, tgt["bitpos"], tgt["width"],
                                      tgt["bias"], int(text.strip(), 0))
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
        if tgt["mode"] == "text":
            d = Text()
            d.append(f"editing {tgt['name']} ", style=f"bold {PEND}")
            d.append(f"as text -> {tgt['metafield']}; re-serialized on write "
                     f"(length may change)", style=SOFT)
            self.query_one("#detail", Static).update(d)
            self.query_one("#hex", Static).update(
                hex_text(self.work, tgt["off"], tgt["length"], PEND))
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
        if event.input.id != "editbar":
            return
        if self._prompt:                     # goto / search prompt, not a field edit
            kind, text = self._prompt["kind"], event.value
            self._end_prompt()
            if kind == "goto":
                self._run_goto(text)
            elif kind == "search":
                self._run_search(text)
            return
        if not self._edit_target:
            return
        tgt = self._edit_target
        if tgt["mode"] == "text":
            try:
                _fmt, new_data, _applied = _write_edit(
                    self.work, {tgt["metafield"]: event.value})
            except (EditError, OSError, ValueError) as e:
                self.notify(f"error: {e}", severity="error")
                return
            self._end_edit()
            self._apply_to_work(new_data)
            self.notify(f"set {tgt['metafield']} (unsaved -- ctrl+s to save)")
            return
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

    def _end_prompt(self):
        self._prompt = None
        bar = self.query_one("#editbar", Input)
        bar.value = ""
        bar.add_class("hidden")
        self.query_one("#tree", Tree).focus()

    def action_cancel_edit(self):
        if self._prompt:                     # esc cancels an armed goto/search prompt
            self._end_prompt()
            return
        if not self._edit_target:
            return
        self._end_edit()
        if self._cur_node:
            data = self._nodemeta.get(id(self._cur_node))
            if data:
                self._show(*data, self._node_name(self._cur_node), "")

    def _end_edit(self):
        self._edit_target = None
        bar = self.query_one("#editbar", Input)
        bar.value = ""
        bar.add_class("hidden")
        self.query_one("#tree", Tree).focus()

    # ── in-pane hex editor: Tab into the hex pane and overwrite bytes ──

    def action_hex_focus(self):
        if len(self.screen_stack) > 1:       # a modal is open; leave Tab to it
            return
        if self._hexedit:                    # already editing; Tab must not
            return                           # restart and drop typed nibbles
        if self._edit_target:
            self.action_cancel_edit()
        node = self._cur_node
        data = self._nodemeta.get(id(node)) if node else None
        if not data:
            self.notify("highlight a field first", severity="warning")
            return
        off, length, _accent = data
        if off is None or not length:
            self.notify("this node has no editable byte range", severity="warning")
            return
        if length > _HEXEDIT_CAP:
            self.notify(f"region too large ({length:,} bytes); pick a field",
                        severity="warning")
            return
        self._hexedit = {"off": off, "length": length, "cur": 0, "nib": 0,
                         "buf": bytearray(_read(self.work, off, length))}
        self.query_one("#hex", HexPane).focus()
        self._render_hexedit()

    def _exit_hexedit(self):
        self._hexedit = None
        if self._cur_node:
            data = self._nodemeta.get(id(self._cur_node))
            if data:
                self._show(*data, self._node_name(self._cur_node), "")
        self.query_one("#tree", Tree).focus()

    def _hexedit_key(self, event):
        he = self._hexedit
        if he is None:
            return
        k = event.key
        if k == "escape":
            event.stop()
            self._exit_hexedit()
            return
        if k in ("enter", "return"):
            event.stop()
            with open(self.work, "rb") as f:
                data = f.read()
            new = (data[:he["off"]] + bytes(he["buf"])
                   + data[he["off"] + he["length"]:])
            length = he["length"]
            self._hexedit = None
            self._apply_to_work(new)
            self.notify(f"patched {length} bytes (unsaved -- ctrl+s to save)")
            self.query_one("#tree", Tree).focus()
            return
        n = he["length"]
        digit = k.lower()
        if k == "right":
            he["cur"], he["nib"] = min(he["cur"] + 1, n - 1), 0
        elif k == "left":
            he["cur"], he["nib"] = max(he["cur"] - 1, 0), 0
        elif k == "down":
            he["cur"], he["nib"] = min(he["cur"] + 16, n - 1), 0
        elif k == "up":
            he["cur"], he["nib"] = max(he["cur"] - 16, 0), 0
        elif len(digit) == 1 and digit in "0123456789abcdef":
            d, i = int(digit, 16), he["cur"]
            if he["nib"] == 0:
                he["buf"][i] = (he["buf"][i] & 0x0f) | (d << 4)
                he["nib"] = 1
            else:
                he["buf"][i] = (he["buf"][i] & 0xf0) | d
                he["nib"] = 0
                he["cur"] = min(he["cur"] + 1, n - 1)
        else:
            return
        event.stop()
        self._render_hexedit()

    def _render_hexedit(self):
        he = self._hexedit
        off, buf, cur, nib = he["off"], he["buf"], he["cur"], he["nib"]
        t = Text()
        t.append("HEX EDIT  ", style=f"bold {SEV['alert']}")
        t.append(f"byte {cur + 1}/{len(buf)} @ 0x{off + cur:08x}"
                 f"{' low-nibble' if nib else ''}   arrows move  0-9a-f overwrite"
                 f"  enter=apply  esc=cancel\n", style=DIM)
        cur_st = "bold #10161a on #ffcc55"
        for row in range(0, len(buf), 16):
            chunk = buf[row:row + 16]
            t.append(f"{off + row:08x}  ", style=GUTTER)
            for i in range(16):
                if i < len(chunk):
                    t.append(f"{chunk[i]:02x} ",
                             style=cur_st if row + i == cur else ACCENT)
                else:
                    t.append("   ")
                if i == 7:
                    t.append(" ")
            t.append(" ")
            for i, b in enumerate(chunk):
                ch = chr(b) if 32 <= b < 127 else "."
                t.append(ch, style=(cur_st if row + i == cur
                                    else (FG if 32 <= b < 127 else DIM)))
            t.append("\n")
        self.query_one("#hex", Static).update(t)

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
