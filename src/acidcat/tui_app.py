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
_UNDO_CAP = 50         # most undo snapshots to keep

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


def _synchsafe_encode(text):
    v = int(text, 0)
    if not 0 <= v < (1 << 28):
        raise ValueError("synchsafe value out of 28-bit range")
    return bytes([(v >> 21) & 0x7f, (v >> 14) & 0x7f, (v >> 7) & 0x7f, v & 0x7f])


def _synchsafe_decode(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _float80_encode(text):
    import math
    v = float(text)
    if v == 0:
        return b"\x00" * 10
    sign = 0x80 if v < 0 else 0
    v = abs(v)
    m, e = math.frexp(v)                          # v = m * 2^e, 0.5 <= m < 1
    exponent = e + 16382                          # inverse of the decoder below
    mantissa = int(round(m * (1 << 64)))
    if mantissa >> 64:                            # rounding overflow
        mantissa >>= 1
        exponent += 1
    return (bytes([sign | ((exponent >> 8) & 0x7f), exponent & 0xff])
            + mantissa.to_bytes(8, "big"))


def _float80_decode(b):
    from acidcat.core.aiff import _parse_ieee_extended
    f = _parse_ieee_extended(bytes(b))
    return int(f) if f == int(f) else f


# named non-struct encodings a walker may declare in a field's `enc`:
# name -> (byte length, encode(text)->bytes, decode(bytes)->number). Used for
# the bespoke layouts struct can't express (ID3 synchsafe, AIFF 80-bit float).
def _u24be_encode(text):
    v = int(text, 0)
    if not 0 <= v < (1 << 24):
        raise ValueError("value out of 24-bit range")
    return v.to_bytes(3, "big")


_CODECS = {
    "synchsafe": (4, _synchsafe_encode, _synchsafe_decode),
    "float80": (10, _float80_encode, _float80_decode),
    "u24be": (3, _u24be_encode, lambda b: int.from_bytes(bytes(b), "big")),
}


# bit-packed fields declare enc="bits:DELTA:CLEN:BITPOS:WIDTH:BIAS": the field
# lives inside a CLEN-byte container starting DELTA bytes from the field's own
# offset; its value occupies WIDTH bits starting BITPOS bits from the container
# MSB; the stored bits are (display value + BIAS) (e.g. FLAC channels store
# count-1, so BIAS=-1). Editing does a read-modify-write on the container so the
# neighbouring bit-fields sharing those bytes are preserved.
def parse_bitfield(enc):
    if not isinstance(enc, str) or not enc.startswith("bits:"):
        return None
    delta, clen, bitpos, width, bias = (int(x) for x in enc.split(":")[1:])
    return delta, clen, bitpos, width, bias


def bitfield_extract(container, bitpos, width, bias):
    shift = len(container) * 8 - bitpos - width
    return ((int.from_bytes(container, "big") >> shift) & ((1 << width) - 1)) - bias


def bitfield_apply(container, bitpos, width, bias, value):
    shift = len(container) * 8 - bitpos - width
    v = int(value) + bias
    if shift < 0 or v < 0 or v >= (1 << width):
        raise ValueError("bitfield value out of range")
    ci = int.from_bytes(container, "big")
    mask = ((1 << width) - 1) << shift
    return ((ci & ~mask) | (v << shift)).to_bytes(len(container), "big")


# enum bit-fields: like bit-fields, but the raw bits map to a label via a table
# (the walker's own decode table). enc="bitsmap:DELTA:CLEN:BITPOS:WIDTH:MAPID".
# The reverse map (label -> raw) lets the user edit by name; the same RMW writes.
from acidcat.core.mp3 import _CHANNEL_MODES as _MP3_CHANMODE  # noqa: E402
_BITMAPS = {"mpeg_chanmode": dict(_MP3_CHANMODE)}


def parse_bitsmap(enc):
    if not isinstance(enc, str) or not enc.startswith("bitsmap:"):
        return None
    _tag, delta, clen, bitpos, width, mapid = enc.split(":")
    return int(delta), int(clen), int(bitpos), int(width), mapid


def resolve_bitsmap(mapid, text):
    """User text (a label, case-insensitive, or a raw index) -> raw bits, or
    None if it is neither."""
    m = _BITMAPS.get(mapid, {})
    t = text.strip()
    for k, v in m.items():
        if str(v).lower() == t.lower():
            return k
    try:
        iv = int(t, 0)
    except ValueError:
        return None
    return iv if iv in m else None


def enc_size(enc):
    return _CODECS[enc][0] if enc in _CODECS else struct.calcsize(enc)


def encode_value(enc, text):
    """Encode user text as bytes for a field's declared encoding: a named codec
    (synchsafe, ...) or a struct format string. Ints accept 0x../0b.. prefixes.
    Raises ValueError/struct.error on bad input."""
    if enc in _CODECS:
        return _CODECS[enc][1](text)
    if enc[-1] in "fd":
        return struct.pack(enc, float(text))
    return struct.pack(enc, int(text, 0))


def decode_value(enc, b):
    if enc in _CODECS:
        return _CODECS[enc][2](b)
    return struct.unpack(enc, b)[0]


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
            ("e", "edit the selected field (value or hex)"),
            ("ctrl+t", "toggle the edit between value and raw hex"),
            ("tab", "hex-edit the field in the pane (arrows move, 0-9a-f type)"),
            ("w", "edit tags (metadata form)"),
            ("s", "strip identifying metadata"),
            ("ctrl+s", "save to the original (writes a _original backup)"),
            ("ctrl+z", "undo the last edit"),
            ("o", "open another file"),
            ("esc", "cancel the current edit"),
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
        ("ctrl+z", "undo", "undo"),
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
        self._editval = {}        # id(node) -> (value, enc, raw)  for field nodes
        self._textfield = {}      # id(node) -> engine field  for variable-length text
        self._profile = None      # edit profile of the current file (WAV/AIFF/...)
        self._cur_node = None     # last highlighted tree node
        self._edit_target = None  # active inline edit: dict(off,length,name,mode,fmt,accent)
        self._hexedit = None      # active in-pane hex edit: dict(off,length,buf,cur,nib)
        self._undo = []           # working-copy byte snapshots for undo

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with Horizontal():
            yield Tree("file", id="tree")
            with Vertical(id="right"):
                yield Static(id="detail")
                with VerticalScroll(id="hexwrap"):
                    yield HexPane(id="hex")
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
        yet), snapshotting the prior state for undo, and refresh."""
        with open(self.work, "rb") as f:
            self._undo.append(f.read())
        self._undo = self._undo[-_UNDO_CAP:]
        with open(self.work, "wb") as f:
            f.write(new_bytes)
        self._recompute_dirty()
        self._load()

    def _recompute_dirty(self):
        """Dirty iff the working copy differs from the saved file, so undoing back
        to the saved state (or saving) clears the flag."""
        try:
            with open(self.work, "rb") as f:
                w = f.read()
            with open(self.src, "rb") as f:
                self.dirty = w != f.read()
        except OSError:
            self.dirty = True

    def action_undo(self):
        if not self._undo:
            self.notify("nothing to undo")
            return
        with open(self.work, "wb") as f:
            f.write(self._undo.pop())
        self._recompute_dirty()
        self._load()
        self.notify("undid last edit")

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

        prof = edit_profile(self.work)
        self._profile = prof[0] if prof else None
        tree = self.query_one("#tree", Tree)
        tree.clear()
        self._nodemeta = {}
        self._editval = {}
        self._textfield = {}
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

    def action_help(self):
        self.push_screen(HelpScreen())

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
        self._show(off, length, accent, name.strip(), self._edit_hint(event.node,
                                                                      off, length))

    def _edit_hint(self, node, off, length):
        """A short note in the detail pane telling the user how the highlighted
        field can be edited (value / hex / text), so it's discoverable."""
        if off is None or not length:
            return ""
        if id(node) in self._textfield:
            return f"text-editable ({self._textfield[id(node)]}) -- press e"
        value, enc, raw = self._editval.get(id(node), (None, None, None))
        rb = _read(self.work, off, length)
        if enc is not None:
            try:
                if encode_value(enc, str(raw if raw is not None else value)) == rb:
                    return f"value-editable ({enc}) -- press e, or tab for hex"
            except (ValueError, struct.error):
                pass
        if infer_enc(value, rb) is not None:
            return "value-editable -- press e, or tab for hex"
        if length <= _HEXEDIT_CAP:
            return "hex-editable -- press e or tab"
        return ""

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
        # variable-length text field: edit as text through the metadata engine,
        # which re-serializes the chunk so a longer/shorter value is valid.
        mf = self._textfield.get(id(node))
        if mf is not None:
            value = self._editval.get(id(node), (None,))[0]
            name = (node.label.plain if isinstance(node.label, Text)
                    else str(node.label)).strip()
            self._edit_target = {"off": off, "length": length, "name": name,
                                 "mode": "text", "fmt": None, "metafield": mf,
                                 "accent": accent}
            bar = self.query_one("#editbar", Input)
            bar.value = str(value) if value is not None else ""
            bar.remove_class("hidden")
            self._update_edit_title()
            bar.focus()
            self._render_preview()
            return
        if length > _HEXEDIT_CAP:
            self.notify(f"region too large to edit ({length:,} bytes); pick a field",
                        severity="warning")
            return
        raw_bytes = _read(self.work, off, length)
        name = (node.label.plain if isinstance(node.label, Text)
                else str(node.label)).strip()
        value, enc, raw_val = self._editval.get(id(node), (None, None, None))
        # enum bit-field: raw bits map to a label; edit by name (or index).
        bm = parse_bitsmap(enc)
        if bm is not None:
            delta, clen, bitpos, width, mapid = bm
            cont_off = off + delta
            cur = _read(self.work, cont_off, clen)
            if (len(cur) == clen and clen * 8 - bitpos - width >= 0
                    and _BITMAPS.get(mapid, {}).get(
                        bitfield_extract(cur, bitpos, width, 0)) == value):
                self._edit_target = {"off": cont_off, "length": clen, "name": name,
                                     "mode": "bitsmap", "fmt": None, "accent": accent,
                                     "bitpos": bitpos, "width": width, "mapid": mapid}
                bar = self.query_one("#editbar", Input)
                bar.value = str(value)
                bar.remove_class("hidden")
                self._update_edit_title()
                bar.focus()
                self._render_preview()
                return
            # annotation did not verify -> fall through to hex
        # bit-packed field: read-modify-write the value inside its container bytes
        # so neighbouring bit-fields survive. Only if the annotation decodes to
        # the shown value (same self-verify guard).
        bf = parse_bitfield(enc)
        if bf is not None:
            delta, clen, bitpos, width, bias = bf
            cont_off = off + delta
            cur = _read(self.work, cont_off, clen)
            if (len(cur) == clen and clen * 8 - bitpos - width >= 0
                    and bitfield_extract(cur, bitpos, width, bias) == value):
                self._edit_target = {"off": cont_off, "length": clen, "name": name,
                                     "mode": "bitfield", "fmt": None, "accent": accent,
                                     "bitpos": bitpos, "width": width, "bias": bias}
                bar = self.query_one("#editbar", Input)
                bar.value = str(value)
                bar.remove_class("hidden")
                self._update_edit_title()
                bar.focus()
                self._render_preview()
                return
            # annotation did not verify -> fall through to hex of the field bytes
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
            fmt = infer_enc(value, raw_bytes)
            if fmt is not None:
                initial = str(value)
        # 3) else raw hex.
        if fmt is not None:
            mode = "value"
        else:
            mode, initial = "hex", raw_bytes.hex(" ")
        self._edit_target = {"off": off, "length": length, "name": name,
                             "mode": mode, "fmt": fmt, "accent": accent}
        bar = self.query_one("#editbar", Input)
        bar.value = initial
        bar.remove_class("hidden")
        self._update_edit_title()
        bar.focus()
        self._render_preview()

    def _update_edit_title(self):
        tgt = self._edit_target
        bar = self.query_one("#editbar", Input)
        if tgt["mode"] == "value":
            kind = f"value ({tgt['fmt']})"
        elif tgt["mode"] == "bitsmap":
            opts = " | ".join(str(v) for v in _BITMAPS.get(tgt["mapid"], {}).values())
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
        if event.input.id != "editbar" or not self._edit_target:
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

    # ── in-pane hex editor: Tab into the hex pane and overwrite bytes ──

    def action_hex_focus(self):
        if len(self.screen_stack) > 1:       # a modal is open; leave Tab to it
            return
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
                name = (self._cur_node.label.plain
                        if isinstance(self._cur_node.label, Text)
                        else str(self._cur_node.label)).strip()
                self._show(*data, name, "")
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
