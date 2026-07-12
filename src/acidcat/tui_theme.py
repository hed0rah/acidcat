"""Shared TUI brand theme -- the single source of truth for both acidcat's TUI
and the acidcat-playground TUI, so their colors cannot drift.

Brand: an ink canvas with a gunmetal-silver grayscale carrying the interface,
and two accents used sparingly -- kill-engn teal for structure/focus, rally
orange for attention (unsaved, danger, mutation). Import the constants and the
CSS helpers; do not hardcode hex in the apps.

    from acidcat import tui_theme as th
    class MyApp(App):
        CSS = th.BASE_CSS + '''#mine { ... }'''

Pure data + string helpers -- no Textual/Rich import, so importing this is free
and it stays usable from either repo.
"""

# ── themes ────────────────────────────────────────────────────────────
# the brand theme is the default. add alternates (a high-contrast variant, a
# more-colorful one) as new dict entries; the module-level constants below are
# sourced from DEFAULT_THEME, so a future switcher only re-sources them.
DEFAULT_THEME = "brand"

THEMES = {
    "brand": {
        # grayscale ramp: ink canvas -> gunmetal silver text
        "BG": "#16181C", "INSET": "#101217", "GUTTER": "#3A3E45",
        "DIM": "#565B63", "SOFT": "#8A9099", "FG": "#C9CDD3",
        # accents (sparing): kill-engn teal, rally orange, calmer amber
        "TEAL": "#08F9DF", "ORANGE": "#FF4D00", "AMBER": "#E0913E",
        # multi-item ramp (teal -> silver -> orange), restrained, no neon rainbow
        "PALETTE": ["#08F9DF", "#5CD9CE", "#93C9C2", "#C9CDD3",
                    "#D6B49E", "#E88F63", "#F56A31", "#FF4D00"],
        # byte-class colors (Hilbert / entropy byte-map, per-byte tint)
        "BYTE_CLASS": {"ascii": "#08F9DF", "high": "#FF4D00", "ctrl": "#8A9099",
                       "null": "#3A3E45", "ff": "#FF8A5C"},
    },
}

_T = THEMES[DEFAULT_THEME]
BG = _T["BG"]
INSET = _T["INSET"]
GUTTER = _T["GUTTER"]
DIM = _T["DIM"]
SOFT = _T["SOFT"]
FG = _T["FG"]
TEAL = _T["TEAL"]
ORANGE = _T["ORANGE"]
AMBER = _T["AMBER"]
PALETTE = _T["PALETTE"]
BYTE_CLASS = _T["BYTE_CLASS"]

# semantic aliases (name the role, not the color, at the call site)
ACCENT = TEAL        # navigation / structure / focus
PEND = ORANGE        # pending / unsaved / live edit preview
ALERT = ORANGE       # danger / forensics

# ── severity -> color ─────────────────────────────────────────────────
SEV = {"alert": ORANGE, "warn": AMBER, "notice": TEAL, "info": DIM}


# ── helpers ───────────────────────────────────────────────────────────
def chunk_color(i):
    """Stable brand color for the i-th chunk/region (cycles the ramp)."""
    return PALETTE[i % len(PALETTE)]


def sev_color(level):
    """Color for a severity level; falls back to FG."""
    return SEV.get(level, FG)


def mark(text, color, bold=False):
    """Rich-markup a string: mark('data', TEAL, bold=True) -> '[b #08F9DF]data[/]'."""
    tag = ("b " if bold else "") + color
    return f"[{tag}]{text}[/]"


# ── shared CSS skeleton ───────────────────────────────────────────────
# element-level rules apply to both apps automatically; class-level rules let a
# pane opt into a role: class="panel" (idle gutter border, teal on focus),
# "readout" (neutral detail pane), "danger" (forensics), "mutate" (edit surfaces).
BASE_CSS = f"""
Screen {{ background: {BG}; }}
Header {{ background: {BG}; color: {TEAL}; text-style: bold; }}
Footer {{ background: {BG}; }}

Tree {{ background: {BG}; color: {FG}; }}
Tree > .tree--guides {{ color: {GUTTER}; }}
Tree > .tree--guides-selected {{ color: {TEAL}; }}
Tree > .tree--cursor {{ background: {INSET}; color: {FG}; }}
Tree > .tree--label {{ color: {FG}; }}

DataTable {{ background: {INSET}; }}
DataTable > .datatable--header {{ background: {BG}; color: {TEAL}; text-style: bold; }}
DataTable > .datatable--cursor {{ background: {GUTTER}; color: {FG}; }}

.panel {{ border: round {GUTTER}; }}
.panel:focus, .panel:focus-within {{ border: round {TEAL}; }}
.readout {{ border: round {GUTTER}; color: {FG}; }}
.danger {{ border: round {ORANGE}; }}
.mutate {{ border: round {ORANGE}; background: {BG}; }}
.hint {{ color: {SOFT}; }}
.dim {{ color: {DIM}; }}
"""


def modal_css(accent=TEAL, width=64):
    """Standard centered modal box CSS with a chosen accent border.
    accent: TEAL for neutral (help/pick), ORANGE for edit/confirm/diff."""
    return f"""
    ModalScreen {{ align: center middle; }}
    #box {{ width: {width}; max-height: 90%; padding: 1 2;
            background: {BG}; border: round {accent}; }}
    #box .title {{ color: {accent}; text-style: bold; padding-bottom: 1; }}
    """
