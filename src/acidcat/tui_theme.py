"""Shared TUI brand palette -- the single source of truth for the colors both
acidcat's TUI and the acidcat-playground TUI set in code, so those cannot drift.

Brand: an ink canvas with a gunmetal-silver grayscale carrying the interface,
and two accents used sparingly -- kill-engn teal for structure/focus, rally
orange for attention (unsaved, danger, mutation). Import the palette constants
and the Rich helpers instead of hardcoding hex:

    from acidcat import tui_theme as th
    label = th.mark("data", th.TEAL, bold=True)
    color = th.byte_color(b)

The apps' Textual CSS blocks still spell the same hex literally (kept in sync by
hand for now; a shared Textual theme could source the CSS too, later).

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
                       "null": "#3A3E45", "ff": "#FF8A5C", "empty": "#16181C"},
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


def byte_color(b):
    """Brand hex for a byte's binvis class -- composes core.viz.byte_class with
    BYTE_CLASS. Lazy-imports viz so importing the theme stays cheap."""
    from acidcat.core import viz
    return BYTE_CLASS[viz.byte_class(b)[1]]


