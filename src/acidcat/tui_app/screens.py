"""acidcat TUI -- the modal screens and the hex-pane widget.

Self-contained Textual widgets the app pushes onto its screen stack: file
browse, metadata edit, confirm, help, pending-diff, byte map, validate,
carve-regions, disc-extract, and a text prompt, plus the focusable HexPane.
They talk back through callbacks, so nothing here imports the app (no cycle).
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, DirectoryTree, Input, Label, Static

from acidcat.commands.write import _edit as _write_edit
from acidcat.core.write.edits import EditError
from acidcat.tui_app.render import _DIFF_CAP
from acidcat import tui_theme as th
from acidcat.tui_theme import ACCENT, DIM, PEND, SEV, SOFT


class HexPane(Static):
    """The right-hand hex view. Focusable so it can host in-place hex editing:
    when the app is in hex-edit mode, key events route to the app's handler
    (cursor movement + nibble overwrite); otherwise it behaves as a plain
    read-only pane."""
    can_focus = True

    def on_key(self, event):
        if getattr(self.app, "_hexedit", None):
            self.app._hexedit_key(event)




class BrowseScreen(ModalScreen):
    """A file picker: navigate a directory tree, enter selects, esc cancels.
    dismiss()es with the chosen path string, or None on cancel."""

    CSS = th.css("""
    BrowseScreen { align: center middle; }
    #browsebox { width: 80%; height: 80%; border: round $TEAL;
                 background: $BG; padding: 1 2; }
    #browsehint { color: $SOFT; padding-bottom: 1; }
    DirectoryTree { background: $BG; }
    """)
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

    CSS = th.css("""
    EditScreen { align: center middle; }
    #editbox { width: 72; height: auto; max-height: 90%; border: round $ORANGE;
               background: $BG; padding: 1 2; }
    #edittitle { color: $ORANGE; text-style: bold; padding-bottom: 1; }
    #edithint { color: $DIM; padding-bottom: 1; }
    #editstatus { color: $ORANGE; padding-top: 1; }
    EditScreen Label { color: $SOFT; }
    EditScreen Input { margin-bottom: 1; }
    #editbtns { height: auto; padding-top: 1; }
    """)
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

    CSS = th.css("""
    ConfirmScreen { align: center middle; }
    #confbox { width: 60; height: auto; border: round $ORANGE;
               background: $BG; padding: 1 2; }
    #confmsg { color: $FG; padding-bottom: 1; }
    #confbtns { height: auto; }
    """)
    # save and discard were reachable by mouse only, on the one prompt that
    # stands between the user and losing an edit.
    BINDINGS = [
        ("s", "save", "save"),
        ("d", "discard", "discard"),
        ("c", "cancel", "cancel"),
        ("escape", "cancel", "cancel"),
    ]

    def __init__(self, prompt):
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="confbox"):
            yield Static(Text(self.prompt, style=f"bold {PEND}"), id="confmsg")
            with Horizontal(id="confbtns"):
                yield Button("save  (s)", id="save", variant="success")
                yield Button("discard  (d)", id="discard", variant="error")
                yield Button("cancel  (c)", id="cancel")

    def on_mount(self):
        self.query_one("#save", Button).focus()

    def on_button_pressed(self, event):
        self.dismiss(event.button.id if event.button.id != "cancel" else None)

    def action_save(self):
        self.dismiss("save")

    def action_discard(self):
        self.dismiss("discard")

    def action_cancel(self):
        self.dismiss(None)


class HelpScreen(ModalScreen):
    """Key reference overlay. Any of esc / ? closes it."""

    CSS = th.css("""
    HelpScreen { align: center middle; }
    #helpbox { width: 74; height: auto; max-height: 90%; border: round $TEAL;
               background: $BG; padding: 1 2; }
    """)
    BINDINGS = [("escape", "close", "close"), ("question_mark", "close", "close")]

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("acidcat tui  --  keys\n\n", style=f"bold {ACCENT}")
        # The map before the keys. Two of these screens are pixel-identical
        # apart from a line of title text, which is not enough to tell them
        # apart from the inside.
        t.append("Three screens.  ", style=f"bold {PEND}")
        t.append("The FILE view you start in: tree on the left, bytes on the "
                 "right. The REGION LIST (l): the same regions as a table you "
                 "can act on in bulk. A DESCENDED view (enter, from the list): "
                 "one region opened as though it were a file of its own, so "
                 "its offsets start again at zero -- u comes back out.\n\n",
                 style=SOFT)
        t.append("lowercase looks, SHIFT works on regions.  ", style=f"bold {PEND}")
        t.append("space marks one, A marks all, X extracts what is marked, E "
                 "extracts everything -- spelled the same in the tree and in "
                 "the list. The list's own tools are capitals for the same "
                 "reason (M mode, T lens, G shape, C carve): no letter should "
                 "mean one thing here and another there. One used to be worse "
                 "than confusing -- `s` was the shape column in the list and "
                 "STRIP METADATA in the tree.\n\n", style=SOFT)
        rows = [
            ("arrows / enter", "move + expand the tree"),
            ("ctrl+left/right", "pan the tree sideways when a deep branch runs "
                                "off the pane"),
            ("shift+left/right", "jump to a node's parent / to the next branch "
                                 "past it"),
            ("pgdn / pgup", "page the hex view through the file; up/down on "
                            "the focused hex pane move it a row"),
            ("a / c", "expand all / collapse all"),
            ("tab / shift+tab", "move focus between the tree and the hex pane"),
            ("z", "give the focused pane the whole screen (again to restore)"),
            ("g", "goto offset (0x.. or decimal)"),
            ("/", "search: text=fuzzy name/value, 0x..=hex, \"..\"=ascii"),
            ("n / N", "next / previous search match"),
            ("f", "jump to the next forensics finding"),
            ("x", "follow a pointer field to where it points (flags dangling)"),
            ("m", "byte map: where the file's bytes go, biggest regions first"),
            ("b", "byte view: cycle hex / entropy / hilbert / histogram"),
            ("r", "byte view: whole file or just the selected region"),
            ("S", "byte view: vertical scale (entropy 0-8 or auto; "
                  "histogram linear, log, clipped)"),
            ("u / U", "back and forward through the views you descended"),
            ("arrows", "on a focused graph: up/down change the scale, "
                       "left/right move the selection (a region-scoped graph "
                       "follows it live)"),
            ("p", "play the selected region as raw PCM (. stops); needs ffplay"),
            ("v", "validate structure: constraint violations, r to repair them"),
            ("y", "yank the selected bytes as hex to the clipboard"),
            ("d", "review all pending changes (offset old->new) before save"),
            ("e", "edit the selected field (value or hex)"),
            ("ctrl+t", "toggle the edit between value and raw hex"),
            ("ctrl+e", "hex-edit the field in the pane (arrows move, 0-9a-f type)"),
            ("w", "edit tags (metadata form)"),
            ("s", "strip identifying metadata (asks first)"),
            ("ctrl+s", "save to the original (writes a _original backup)"),
            ("ctrl+z / ctrl+r", "undo / redo the last edit"),
            ("o", "open another file"),
            ("l", "the region list: the same regions as a table, with bulk "
                  "actions and a name column when the file has a table of "
                  "contents"),
            ("F", "two things a stuck view can still do -- force a walker onto "
                  "a file nothing recognises (this is what finds the NAMES in "
                  "an archive), and scan forensics on a file too big to have "
                  "been scanned on open"),
            ("space", "mark the region under the cursor"),
            ("A", "mark every region, or none if they all already are"),
            ("X", "extract only the marked regions"),
            ("E", "extract every region, marked or not"),
            ("+", "on a '... more rows' line, list more of that chunk's rows"),
            ("esc", "cancel the current edit / prompt"),
            ("q", "quit"),
        ]
        # The separator is its own append, not the tail of the pad. `{k:16}`
        # ran straight into the description for any key exactly 16 characters
        # wide -- which `shift+left/right` is -- the same collision the tree
        # labels had.
        width = max(len(k) for k, _ in rows)
        for k, d in rows:
            t.append(f"  {k:<{width}}", style=f"bold {PEND}")
            t.append("  ", style=SOFT)
            t.append(f"{d}\n", style=SOFT)
        t.append("\nRegions live in the tree, under the file: expand the file to "
                 "scan for them, expand a region to walk it, and keep expanding "
                 "for its chunks and fields. `l` opens the same regions as a "
                 "list, which is where bulk work happens: space marks one, a "
                 "marks all, x extracts what is marked and e extracts every "
                 "region. enter descends into a region as if it were a file, "
                 "m cycles the forensics mode (strict/normal/aggressive), t toggles "
                 "the transform lens (audio hidden under XOR/rotate/nibble-swap), "
                 "c carves an arbitrary offset+length, / searches raw bytes. A big "
                 "image scans in segments with live progress: space pauses/resumes, "
                 "enter keeps what was found so far, esc discards and backs out.",
                 style=DIM)
        t.append("\nDisc audio browser (a PS1 / CD-XA disc image opens into it): the "
                 "game's .STR soundtrack tracks and .VB/.VAG SPU sound banks, from the "
                 "ISO filesystem. enter / p auditions the selected track, x / a extract "
                 "one / all to WAV.", style=DIM)
        t.append("\nEdits go to a temp working copy; nothing touches the original "
                 "until ctrl+s.", style=DIM)
        with Vertical(id="helpbox"):
            yield Static(t)

    def action_close(self):
        self.dismiss(None)


class DiffScreen(ModalScreen):
    """Review pending byte changes (working copy vs the original) before a save.
    Any of esc / d closes it.

    The count is of every region that exists; the LIST below it stops at
    _DIFF_CAP. Those were the same number until 1,000 changes reported as 201,
    on the one screen a person consults before overwriting their file."""

    CSS = th.css("""
    DiffScreen { align: center middle; }
    #diffbox { width: 82; height: auto; max-height: 90%; border: round $ORANGE;
               background: $BG; padding: 1 2; }
    """)
    BINDINGS = [("escape", "close", "close"), ("d", "close", "close")]

    def __init__(self, regions, src_len, work_len, total=None):
        super().__init__()
        self.regions = regions
        self.src_len = src_len
        self.work_len = work_len
        # how many regions EXIST, versus the prefix held in `regions`. Defaults
        # to len(regions) so an older two-arg caller still reads correctly.
        self.total = len(regions) if total is None else total

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("pending changes  ", style=f"bold {ACCENT}")
        if self.src_len != self.work_len:
            t.append(f"(file size {self.src_len:,} -> {self.work_len:,} bytes)\n",
                     style=SOFT)
        elif not self.regions:
            t.append("none -- working copy matches the original\n", style=SOFT)
        else:
            shown = ("" if self.total <= len(self.regions)
                     else f"; listing the first {len(self.regions):,}")
            t.append(f"{self.total:,} region(s) vs the original{shown}\n",
                     style=SOFT)
        for off, old, new in self.regions[:_DIFF_CAP]:
            t.append(f"\n0x{off:08x}  ", style=f"bold {PEND}")
            t.append(f"{len(old)}B\n", style=DIM)
            t.append("  old ", style=SOFT)
            t.append(old[:24].hex(" ") + (" .." if len(old) > 24 else ""), style=DIM)
            t.append("\n  new ", style=SOFT)
            t.append(new[:24].hex(" ") + (" .." if len(new) > 24 else ""),
                     style=PEND)
            t.append("\n")
        if self.total > len(self.regions):
            t.append(f"\n.. {self.total - len(self.regions):,} more regions\n",
                     style=DIM)
        t.append("\nctrl+s to save, esc to keep editing.", style=DIM)
        with Vertical(id="diffbox"):
            yield Static(t)

    def action_close(self):
        self.dismiss(None)


class MapScreen(ModalScreen):
    """A byte-budget map: where the file's bytes actually go, top-level regions
    biggest first with a proportional bar. Any of esc / m closes it."""

    CSS = th.css("""
    MapScreen { align: center middle; }
    #mapbox { width: 86; height: auto; max-height: 90%; border: round $TEAL;
              background: $BG; padding: 1 2; }
    """)
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


class ValidateScreen(ModalScreen):
    """The constraint model's read-only face inside the TUI: the derived-field
    violations of the working copy, each with the witness that makes it fixable.
    `r` applies the witnessed repairs to the working copy (still unsaved); esc /
    v close."""

    CSS = th.css("""
    ValidateScreen { align: center middle; }
    #valbox { width: 86; height: auto; max-height: 90%; border: round $TEAL;
              background: $BG; padding: 1 2; }
    """)
    BINDINGS = [("escape", "close", "close"), ("v", "close", "close"),
                ("r", "repair", "repair")]

    def __init__(self, report):
        super().__init__()
        self.report = report

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("validate  ", style=f"bold {ACCENT}")
        t.append(f"[{self.report.label}]\n", style=SOFT)
        vios = self.report.violations
        fixable = self.report.repairable
        self._can_repair = bool(fixable)
        if not vios:
            t.append("\nstructurally consistent -- every derived field matches "
                     "its function.\n", style=f"bold {SEV['notice']}")
        else:
            t.append(f"\n{len(vios)} violation(s), {len(fixable)} witnessed\n",
                     style=SOFT)
            for v in vios:
                t.append(f"\n  {v.kind:<7}", style=f"bold {ACCENT}")
                t.append(f"{v.describe()}\n", style=SOFT)
                wit = f"witness: {v.witness}" if v.witness else "no witness -- left as-is"
                t.append(f"          {wit}\n", style=DIM)
        if self._can_repair:
            t.append(f"\nr to repair {len(fixable)} witnessed field(s) "
                     f"(unsaved), esc / v to close.", style=DIM)
        else:
            t.append("\nesc / v to close.", style=DIM)
        with Vertical(id="valbox"):
            yield Static(t)

    def action_repair(self):
        self.dismiss("repair" if getattr(self, "_can_repair", False) else None)

    def action_close(self):
        self.dismiss(None)


class RegionsScreen(ModalScreen):
    """The blob region browser: the `locate` results as a navigable table.
    enter descends into the selected region (opened as if it were a standalone
    file), x extracts it, e extracts every region. dismiss()es with a dict
    {action: descend|extract|extract_all, index: row}, or None on esc."""

    CSS = th.css("""
    RegionsScreen { align: center middle; }
    #regbox { width: 92%; height: 84%; border: round $TEAL;
              background: $BG; padding: 1 2; }
    #reghint { color: $SOFT; padding-bottom: 1; }
    #regkeys { height: 1; }
    #regkeys2 { height: 1; padding-bottom: 1; }
    #regtable { height: 1fr; }
    DataTable { background: $BG; }
    """)
    BINDINGS = [
        # Spelled the same as in the tree. These four used to be space/a/x/e
        # here and expand/edit/follow/strip there -- six single letters meaning
        # two things depending on which screen you were looking at, which is
        # most of why moving between them felt like starting over.
        ("space", "toggle_sel", "select"),
        ("A", "select_all", "all/none"),
        ("X", "extract", "extract"),
        ("E", "extract_all", "extract all"),
        # The list's own tools, moved off the letters the tree already spends.
        # `s` was the dangerous one: shape column here, STRIP METADATA there,
        # so learning it in one place and reaching for it in the other edits
        # the file. `m` was costly rather than dangerous -- byte map there,
        # cycle-mode-and-rescan here.
        ("M", "mode", "cycle mode"),
        ("T", "transforms", "transform lens"),
        ("C", "carve", "manual carve"),
        ("G", "sparkline", "shape column"),
        ("slash", "search", "byte search"),
        ("question_mark", "help", "help"),
        ("escape", "cancel", "back"),
    ]

    # Off by default. It costs one read per region, which is nothing on a
    # 22-row list and real on a 4,000-row one, and the whole point of the
    # region browser is that it appears instantly.
    show_shape = False

    def __init__(self, regions, blob_name, mode="normal", transforms=False,
                 blob_src=None, show_shape=False, selected=None, cursor=0,
                 filter_text="", sort_col="offset", sort_desc=False,
                 header_focus=False, header_col=None):
        super().__init__()
        self.regions = regions
        self.blob_name = blob_name
        self.mode = mode
        self.transforms = transforms
        self.blob_src = blob_src
        self.show_shape = show_shape
        # Held by the app, not the screen: the list gets re-pushed whenever the
        # shape column is toggled or a scan lands, and a selection that did not
        # survive that would be worse than none at all.
        self.selected = set(selected or ())
        # Where the cursor was when the last one closed. Same reason as the
        # selection: the screen is re-pushed on every toggle, so a row it does
        # not carry is a row it cannot return to -- and marking N regions meant
        # scrolling from the top N times, in the feature the list exists for.
        self.cursor = cursor
        # Typed into the list to narrow it. Held by the app for the same reason
        # as the selection and the cursor: the screen is re-pushed on every
        # action, so a filter it did not carry would clear itself the moment
        # you marked a row.
        self.filter_text = filter_text
        # Which column orders the list, and which way. Held by the app for the
        # same reason as the filter: this screen is re-pushed constantly.
        self.sort_col = sort_col
        self.sort_desc = sort_desc
        # Whether the header bar has focus, so left/right pick a column rather
        # than scrolling the table. Two-step on purpose -- picking a column and
        # applying it are separate, or you cannot move PAST a column to reach
        # one further along without re-sorting the whole list on the way.
        self.header_focus = header_focus
        # Which sortable column the arrows are pointing at while the header has
        # focus. Starts on whatever is currently sorting, so tabbing up does not
        # lose your place.
        self.header_col = header_col or 0
        self._pin_header_col = header_col is not None
        self._sortable = []
        # Original indexes of the rows currently shown, in display order.
        # EVERYTHING downstream keys off region index, and once a filter is on,
        # table row N is no longer region N -- descend, extract and select would
        # all act on whatever happened to sit at that position in the full list.
        # This is the translation, and it is the reason the filter is not a
        # three-line change.
        self._view = list(range(len(regions)))

    def _matches(self, region):
        """Substring match, case-insensitive, over the fields you can read.

        Name first because that is what a table of contents gives you and what
        anyone is looking for, then format so `voc` narrows to the sounds, then
        kind. Not offset or length: nobody hunts a file by typing part of a
        byte count, and including them makes every short query match everything.
        """
        q = self.filter_text.lower()
        if not q:
            return True
        fmt = (region.get("transform") or region.get("format")
               or (region.get("probe") or {}).get("top") or "raw-pcm")
        return (q in (region.get("name") or "").lower()
                or q in str(fmt).lower()
                or q in str(region.get("kind", "")).lower())

    def _visible(self):
        rows = [i for i, r in enumerate(self.regions) if self._matches(r)]
        return self._sorted(rows)

    # What each sortable column reads off a region. Offset is the natural order
    # and is spelled out rather than left implicit, so "sorted by offset" and
    # "not sorted" are the same list and returning to it is one keystroke.
    SORT_KEYS = {
        "offset": lambda r: r.get("offset", 0),
        "end": lambda r: r.get("end", 0),
        "kind": lambda r: str(r.get("kind", "")),
        "format": lambda r: str(r.get("transform") or r.get("format")
                                or (r.get("probe") or {}).get("top") or ""),
        "conf": lambda r: r.get("confidence") or 0,
        "length": lambda r: r.get("length", 0),
        "name": lambda r: (r.get("name") or "").lower(),
    }

    def _head(self, col):
        """A column heading, marked up with the sort state.

        The arrow is the whole point of showing it: an order you cannot see is
        one you re-derive every time you look away, and this screen is redrawn
        constantly. The bracket marks which column the arrows would move to,
        which is a different thing from which one is sorting -- you can be
        pointing at `length` while still ordered by `name`.
        """
        if col not in self.SORT_KEYS:
            return col
        label = col
        if col == self.sort_col:
            label = f"{col} {'v' if self.sort_desc else '^'}"
        if self.header_focus and self._sortable and                 self._sortable[self.header_col] == col:
            return f"[{label}]"
        return label

    def _sorted(self, rows):
        """Order the visible rows, stably, by the chosen column.

        Stable on purpose: sorting by format leaves the VOCs in the order the
        archive stores them, which is the order their names run in, so the
        second key comes free and the list stays predictable. An unstable sort
        would reshuffle equal rows on every redraw and the screen is redrawn on
        every keystroke.
        """
        key = self.SORT_KEYS.get(self.sort_col)
        if key is None:
            return rows
        return sorted(rows, key=lambda i: key(self.regions[i]),
                      reverse=self.sort_desc)

    def compose(self) -> ComposeResult:
        with Vertical(id="regbox"):
            nc = sum(1 for r in self.regions if r.get("kind") == "container")
            nt = sum(1 for r in self.regions if r.get("kind") == "transformed")
            nb = len(self.regions) - nc - nt
            lens = "  lens:ON" if self.transforms else ""
            shown = len(self._visible())
            narrowed = (f"   filter:{self.filter_text!r} -> {shown} of "
                        f"{len(self.regions)}" if self.filter_text else "")
            yield Static(
                Text(f"{self.blob_name}  --  {len(self.regions)} region(s): "
                     f"{nc} container / {nb} blob"
                     + (f" / {nt} transformed" if nt else "")
                     + f"   [mode:{self.mode}{lens}]" + narrowed,
                     style=f"bold {ACCENT}"),
                id="reghint")
            marked = (f"   {len(self.selected)} selected"
                      if self.selected else "")
            # Two lines, split by what they do rather than by nothing: looking
            # at things, then acting on the ones you marked. One undifferentiated
            # row of eleven keys is a row nobody reads.
            yield Static(
                Text("look:  type to filter   bksp undo   esc clear/back   "
                     "tab sort header (arrows pick, enter flips)   "
                     "enter descend   M mode   T lens   G shape   "
                     "/ byte search   C carve   ? help", style=SOFT),
                id="regkeys")
            yield Static(
                Text("act:   space select   A all/none   X extract sel   "
                     "E extract all" + marked, style=f"bold {ACCENT}"),
                id="regkeys2")
            # populate before yield so the table never depends on post-mount
            # query timing (which was flaky for a re-pushed screen)
            t = DataTable(id="regtable")
            t.cursor_type = "row"
            t.zebra_stripes = True
            # a name column only when there is one: a table of contents gives
            # every entry a real name, and nothing else does
            self._named = any(r.get("name") for r in self.regions)
            cols = ["", "#", "offset", "end", "kind", "format", "conf",
                    "length"]
            cols.append("name" if self._named else "geometry")
            if self.show_shape:
                cols.append("shape")
            # Sortable columns, in display order, so left/right walks them the
            # way they appear rather than the way a dict happens to iterate.
            self._sortable = [c for c in cols if c in self.SORT_KEYS]
            if not self._pin_header_col and self.sort_col in self._sortable:
                self.header_col = self._sortable.index(self.sort_col)
            self.header_col = min(self.header_col, max(0, len(self._sortable) - 1))
            t.add_columns(*[self._head(c) for c in cols])
            self._view = self._visible()
            for i in self._view:
                r = self.regions[i]
                geo = r.get("geometry") or {}
                gs = ""
                if geo:
                    ch = "stereo" if geo.get("channels") == 2 else "mono"
                    gs = (f"float{geo['width']}" if geo.get("float")
                          else f"{geo.get('endian') or '?'}-{geo.get('width')}bit") + f" {ch}"
                fmt = (r.get("transform") or r.get("format")
                       or (r.get("probe") or {}).get("top") or "raw-pcm")
                # .get throughout: regions reach this screen from locate, from
                # a manual carve, from a byte search and now from a table of
                # contents, and a display must not crash on a producer that
                # left a field out.
                mark = Text("[x]", style=f"bold {SEV['notice']}")                     if i in self.selected else Text("[ ]", style=DIM)
                row = [mark, str(i), f"0x{r.get('offset', 0):08x}",
                       f"0x{r.get('end', 0):08x}",
                       r.get("kind", "region"), fmt,
                       f"{r.get('confidence') or 0:.2f}",
                       f"{r.get('length', 0):,}",
                       (r.get("name") or "")[-46:] if self._named else gs]
                if self.show_shape:
                    row.append(self._shape(r))
                t.add_row(*row)
            yield t

    def on_mount(self):
        try:
            table = self.query_one("#regtable", DataTable)
            table.focus()
            # Back to the row the last one closed on. The screen is re-pushed
            # on every toggle, so without this each mark dropped the cursor to
            # row 0 and marking five regions in a list of 266 meant scrolling
            # from the top five times.
            if self.cursor:
                table.move_cursor(row=min(self.cursor,
                                          max(0, len(self.regions) - 1)))
        except Exception:
            pass

    def on_data_table_row_selected(self, event):
        self.dismiss({"action": "descend",
                      "index": self._index(event.cursor_row)})

    def _row(self):
        """Where the cursor sits in the TABLE. A view position."""
        return self.query_one("#regtable", DataTable).cursor_row

    def _index(self, row=None):
        """The REGION that table row refers to.

        The two are the same number only while nothing is filtered. Every
        action here acts on a region and every one of them used to read the
        table row and use it as an index -- which, the moment a filter hides a
        single row above the cursor, descends into or extracts the wrong file
        and says nothing about it.
        """
        row = self._row() if row is None else row
        if 0 <= row < len(self._view):
            return self._view[row]
        return self._view[0] if self._view else 0

    def action_toggle_sel(self):
        """space: mark the row under the cursor for extraction."""
        row = self._row()
        sel = set(self.selected)
        sel.symmetric_difference_update({self._index(row)})
        self.dismiss({"action": "select", "selected": sel,
                      "filter": self.filter_text,
                      "cursor": row + 1 if row + 1 < len(self._view) else row})

    def action_select_all(self):
        """A: all or none -- of what you can SEE.

        Filtered, that is the useful reading and the one that composes: type
        `voc`, press A, press X, and 331 sounds come out of a 456-entry archive
        without touching the rest. Selecting the whole archive while looking at
        eleven rows would be a surprise, and an expensive one by the time it
        reached the extract step.
        """
        vis = set(self._view)
        sel = set(self.selected)
        sel = (sel - vis) if vis and vis <= sel else (sel | vis)
        self.dismiss({"action": "select", "selected": sel,
                      "filter": self.filter_text, "cursor": self._row()})

    def action_extract(self):
        """x: the selected regions, or the one under the cursor if none are.

        Falling back to the cursor keeps the single-region case a two-keystroke
        job rather than making selection mandatory for it.
        """
        if self.selected:
            self.dismiss({"action": "extract_selected",
                          "indexes": sorted(self.selected)})
        else:
            self.dismiss({"action": "extract", "index": self._index()})

    def action_extract_all(self):
        """E: everything visible, which is everything when nothing is filtered.

        Same reasoning as A. "All" has to mean the list in front of you, or the
        key quietly means two different things depending on state -- and the
        one where it means more is the one that writes files to disk.
        """
        self.dismiss({"action": "extract_selected",
                      "indexes": sorted(self._view)})

    def action_mode(self):
        nxt = {"strict": "normal", "normal": "aggressive",
               "aggressive": "strict"}[self.mode]
        self.dismiss({"action": "rescan", "mode": nxt, "transforms": self.transforms})

    def action_transforms(self):
        self.dismiss({"action": "rescan", "mode": self.mode,
                      "transforms": not self.transforms})

    def action_carve(self):
        self.dismiss({"action": "carve"})

    def action_search(self):
        self.dismiss({"action": "search"})

    # Every shortcut on this screen is an uppercase letter or a symbol, which
    # is what leaves the whole lowercase alphabet free to type into. That was
    # not luck -- it is why the filter can be live rather than hidden behind a
    # prompt, and it is worth preserving: a new lowercase binding here costs
    # the ability to type that letter.
    def on_key(self, event):
        key = event.key
        if key == "tab":
            # Into the header and back out. Tab means the same thing here as in
            # the file view -- move to a different part of the surface -- and it
            # is free on this screen because nothing else claimed it.
            self._repush(header_focus=not self.header_focus)
            event.stop()
            return
        if self.header_focus:
            if key in ("left", "right") and self._sortable:
                step = -1 if key == "left" else 1
                self.header_col = (self.header_col + step) % len(self._sortable)
                self._repush(header_focus=True, header_col=self.header_col)
                event.stop()
                return
            if key in ("enter", "space") and self._sortable:
                col = self._sortable[self.header_col]
                # Landing on a new column sorts it ascending; pressing again on
                # the one already sorting flips it. Anything else means the
                # first press on a column does something unpredictable.
                desc = (not self.sort_desc) if col == self.sort_col else False
                self._repush(sort_col=col, sort_desc=desc, header_focus=True,
                             header_col=self.header_col)
                event.stop()
                return
            if key == "escape":
                self._repush(header_focus=False)
                event.stop()
                return
            # While the header has focus the table is not being driven, so
            # typing would filter a list you cannot see the cursor in.
            return
        if key == "backspace":
            if self.filter_text:
                self._refilter(self.filter_text[:-1])
                event.stop()
            return
        if key == "escape":
            # Clear before leaving. Escaping out of a filtered list straight to
            # the tree loses two things at once, and the one you meant is
            # usually the filter.
            if self.filter_text:
                self._refilter("")
                event.stop()
            return
        if len(key) == 1 and (key.islower() or key.isdigit() or key in "._-"):
            self._refilter(self.filter_text + key)
            event.stop()

    def _refilter(self, text):
        self._repush(filter_text=text, cursor=0)

    def _repush(self, **changes):
        """Re-open the list with some of its state changed.

        Through the app rather than rebuilt in place, because the app owns the
        selection, the cursor, the filter and the sort, and this screen is
        already re-pushed on every other action. Doing it two ways depending on
        the key is how one path keeps the selection and the other drops it.
        """
        state = {"action": "select", "selected": set(self.selected),
                 "filter": self.filter_text, "cursor": self._safe_row(),
                 "sort_col": self.sort_col, "sort_desc": self.sort_desc,
                 "header_focus": self.header_focus,
                 "header_col": self.header_col}
        state.update(changes)
        if "filter_text" in changes:
            state["filter"] = state.pop("filter_text")
        self.dismiss(state)

    def _safe_row(self):
        try:
            return self._row()
        except Exception:
            return 0

    def _shape(self, region):
        """An entropy sparkline for one region: what it looks like, before you
        spend a descend finding out. Flat and high is compressed or encrypted,
        a varied middle is structure, flat and low is padding."""
        if not self.blob_src:
            return ""
        try:
            from acidcat.core.forensics import viz
            ent, _size, _sampled = viz.file_entropy(
                self.blob_src, 12, start=region["offset"], end=region["end"])
        except Exception:
            return ""
        if not ent:
            return ""
        blocks = " .:-=+*#%@"
        return "".join(blocks[min(len(blocks) - 1, int(e / 8 * len(blocks)))]
                       for e in ent)

    def action_sparkline(self):
        """s: toggle the shape column. Re-reads a slice per row, nothing more --
        it never rescans the file."""
        self.dismiss({"action": "shape", "show": not self.show_shape})

    def action_help(self):
        """? works here too. A screen with its own keys and no way to ask about
        them is a screen you have to have been told about."""
        self.app.push_screen(HelpScreen())

    def action_cancel(self):
        self.dismiss(None)


class DiscScreen(ModalScreen):
    """CD-XA disc audio browser: the game's .STR soundtrack tracks and .VB/.VAG
    SPU sound banks, from the ISO 9660 filesystem. enter/p auditions the selected
    entry, x/a extract one/all. dismiss()es with {action: play|extract|extract_all,
    index} or None on esc."""

    CSS = th.css("""
    DiscScreen { align: center middle; }
    #discbox { width: 92%; height: 84%; border: round $TEAL;
               background: $BG; padding: 1 2; }
    #dischint { color: $SOFT; padding-bottom: 1; }
    #disctable { height: 1fr; }
    DataTable { background: $BG; }
    """)
    BINDINGS = [
        ("x", "extract", "extract one"),
        ("a", "extract_all", "extract all"),
        ("p", "play", "audition"),
        ("escape", "cancel", "back"),
    ]
    _KIND = {"XA": "soundtrack (XA)", "VB": "sound bank (SPU)", "VAG": "sample (SPU)"}

    def __init__(self, entries, disc_name):
        super().__init__()
        self.entries = entries
        self.disc_name = disc_name

    def compose(self) -> ComposeResult:
        with Vertical(id="discbox"):
            nx = sum(1 for e in self.entries if e["kind"] == "XA")
            nb = sum(1 for e in self.entries if e["kind"] in ("VB", "VAG"))
            yield Static(
                Text(f"{self.disc_name}  --  {len(self.entries)} audio file(s): "
                     f"{nx} soundtrack / {nb} sound bank", style=f"bold {ACCENT}"),
                id="dischint")
            yield Static(Text("enter/p audition   x/a extract one/all   esc back",
                              style=SOFT), id="disckeys")
            t = DataTable(id="disctable")
            t.cursor_type = "row"
            t.zebra_stripes = True
            t.add_columns("#", "name", "kind", "size")
            for i, e in enumerate(self.entries):
                t.add_row(str(i), e["path"], self._KIND.get(e["kind"], e["kind"]),
                          f"{e['size']:,}")
            yield t

    def on_mount(self):
        try:
            self.query_one("#disctable", DataTable).focus()
        except Exception:
            pass

    def _cursor(self):
        return self.query_one("#disctable", DataTable).cursor_row

    def on_data_table_row_selected(self, event):
        self.app._audition_disc(self.entries[event.cursor_row])

    def action_play(self):
        self.app._audition_disc(self.entries[self._cursor()])

    def action_extract(self):
        self.app._extract_disc([self.entries[self._cursor()]])

    def action_extract_all(self):
        self.app._extract_disc(list(self.entries))

    def action_cancel(self):
        self.app.action_stop_play()
        self.dismiss(None)


class PromptScreen(ModalScreen):
    """A one-line text prompt (output dir, carve range, search pattern...). Enter
    submits, esc cancels. dismiss()es with the typed string, or None."""

    CSS = th.css("""
    PromptScreen { align: center middle; }
    #dpbox { width: 76; height: auto; border: round $ORANGE;
             background: $BG; padding: 1 2; }
    #dphint { color: $SOFT; padding-bottom: 1; }
    """)
    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, prompt, default):
        super().__init__()
        self.prompt = prompt
        self.default = default

    def compose(self) -> ComposeResult:
        with Vertical(id="dpbox"):
            yield Static(Text(self.prompt, style=f"bold {ACCENT}"), id="dphint")
            yield Input(value=self.default, id="dpinput")

    def on_mount(self):
        self.query_one(Input).focus()

    def on_input_submitted(self, event):
        self.dismiss(event.value.strip() or None)

    def action_cancel(self):
        self.dismiss(None)




class YesNoScreen(ModalScreen):
    """A plain proceed/cancel prompt. dismiss()es True or False.

    ConfirmScreen answers a three-way save/discard/cancel question, which is the
    wrong shape for "are you sure". Kept separate rather than overloading it,
    so neither prompt has to explain which of its buttons do not apply.
    """

    CSS = th.css("""
    YesNoScreen { align: center middle; }
    #ynbox { width: 66; height: auto; border: round $ORANGE;
             background: $BG; padding: 1 2; }
    #ynmsg { color: $FG; padding-bottom: 1; }
    #ynbtns { height: auto; }
    """)
    # The buttons are a convenience, not the interface: this prompt guards a
    # loudness hazard, so it has to be answerable without a mouse.
    BINDINGS = [
        ("y", "yes", "yes"),
        ("n", "cancel", "no"),
        ("escape", "cancel", "cancel"),
    ]

    def __init__(self, prompt, yes_label="play anyway"):
        super().__init__()
        self.prompt = prompt
        self.yes_label = yes_label

    def compose(self) -> ComposeResult:
        with Vertical(id="ynbox"):
            yield Static(Text(self.prompt, style=f"bold {PEND}"), id="ynmsg")
            with Horizontal(id="ynbtns"):
                yield Button(f"{self.yes_label}  (y)", id="yes", variant="warning")
                yield Button("cancel  (n)", id="cancel")

    def on_mount(self):
        # focus the SAFE choice, so a reflexive enter cancels rather than plays
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "yes")

    def action_yes(self):
        self.dismiss(True)

    def action_cancel(self):
        self.dismiss(False)


class ForcedScreen(ModalScreen):
    """What every walker made of a file none of them claims.

    The TUI equivalent of `inspect --force`. On an unknown container the tree is
    a single root node and there is nothing to explore, so this is the way in:
    pick a candidate and the file is re-walked with that walker forced.

    These are leads, not identifications. A walker assumes its magic rather than
    verifying it, so a forced parse invents structure readily -- the `ids`
    column is the one that resists it, counting chunk ids whose bytes are
    actually at the offset claimed. dismiss()es with a format id, or None.
    """

    CSS = th.css("""
    ForcedScreen { align: center middle; }
    #forcedbox { width: 96; height: auto; max-height: 90%; border: round $TEAL;
                 background: $BG; padding: 1 2; }
    #forcedhint { color: $DIM; padding-bottom: 1; }
    ForcedScreen DataTable { height: auto; max-height: 60%; }
    """)
    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, rows, title):
        super().__init__()
        self.rows = rows
        # not `name`: Textual reserves that on every widget as a read-only
        # property, and assigning it raises at construction time
        self.title_text = title

    def compose(self) -> ComposeResult:
        with Vertical(id="forcedbox"):
            t = Text()
            t.append("forced parse  ", style=f"bold {ACCENT}")
            # title_text, not `name` -- the comment three lines above the
            # constructor says exactly why it was stored under another name,
            # and then this read `name` anyway, so the header of the
            # forced-parse screen said "forced parse  None".
            t.append(f"{self.title_text}\n", style=SOFT)
            t.append(f"{len(self.rows)} walker(s) produced something. ",
                     style=SOFT)
            t.append("None of them verified a magic number", style=SEV["warn"])
            t.append(" -- a walker parses at fixed offsets whether or not the "
                     "header is really its format.", style=SOFT)
            yield Static(t)
            yield Static("enter walks the file with that walker forced; "
                         "esc cancels", id="forcedhint")
            table = DataTable(cursor_type="row", zebra_stripes=True)
            table.add_columns("format", "chunks", "fields", "ids", "sane",
                              "complaint from walker")
            for r in self.rows:
                table.add_row(
                    r["format"], str(r["chunks"]), str(r["fields"]),
                    f"{r['anchored']}/{r['chunks']}",
                    "yes" if (r["fits"] and r["ids_ok"]) else "NO",
                    (r["complaint"] or "")[:46],
                )
            yield table

    def on_data_table_row_selected(self, event):
        self.dismiss(self.rows[event.cursor_row]["format"])

    def action_cancel(self):
        self.dismiss(None)
