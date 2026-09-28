"""
acidcat tui -- interactive terminal inspector + metadata editor.

A Textual front-end over the walk_file/anomalies/write engine: browse a file's
structure as a tree, see each node's bytes in a hex pane, and edit the metadata
the CLI `write` supports. Run with no file to open the built-in file browser.
Opt-in extra so the core stays zero-dependency: `pip install acidcat[tui]`.
"""
import os
import sys


def register(subparsers):
    p = subparsers.add_parser(
        "tui", help="Interactive terminal inspector/editor (needs acidcat[tui]).")
    p.add_argument("file", nargs="?", metavar="FILE",
                   help="Audio or synth/DAW preset file. Omit to browse.")
    # No --theme flag, and that is not an omission.
    #
    # tui_theme reads ACIDCAT_THEME once at import, because every consumer does
    # `from acidcat.tui_theme import ACCENT` and binds the value there. And
    # `import acidcat` pulls the palette in through core.forensics.viz, which
    # runs before argparse exists -- so a flag parsed here could only set an
    # environment variable that nothing would read again. Measured: the flag
    # was written, and every theme it selected came out as the default.
    #
    # The earliest hook that would work is the package __init__, and a library
    # that reads sys.argv on import is a worse thing to own than an env var.
    p.epilog = ("Colour theme: set ACIDCAT_THEME=brand|killengn|faterally "
                "before running, e.g. ACIDCAT_THEME=killengn acidcat tui f.wav")
    p.set_defaults(func=run)


def _no_terminal():
    """Why a full-screen app cannot start here, or None if it can.

    Textual needs a real terminal to attach to. Git Bash on Windows does not
    provide one: MinTTY is a named pipe rather than a Windows console, so
    stdout.isatty() is False even though the window looks and behaves like a
    terminal. Textual then exits immediately and silently, which is
    indistinguishable from the command being broken -- it returns to the prompt
    with no output at all, on a tool whose whole job is not doing that.
    """
    if sys.stdout.isatty():
        return None
    if os.name == "nt" and os.environ.get("MSYSTEM"):
        return (
            "acidcat tui needs a terminal, and Git Bash (MinTTY) does not\n"
            "provide one -- it is a pipe, not a Windows console, so the\n"
            "full-screen UI has nothing to draw on and would exit silently.\n"
            "\n"
            "  winpty acidcat tui FILE       run it here, through a real console\n"
            "\n"
            "or start it from Windows Terminal, PowerShell or cmd, where it\n"
            "works without a wrapper. Everything else in acidcat is fine here;\n"
            "this affects the full-screen UI only."
        )
    return ("acidcat tui needs a terminal; stdout is not one (redirected or "
            "piped?). Every other verb writes plain text and pipes fine.")


def run(args):
    from acidcat.util.deps import require
    if not require("textual", group="tui"):
        return 1
    if args.file and not os.path.isfile(args.file):
        print(f"not a file: {args.file}")
        return 1
    why = _no_terminal()
    if why:
        # 2, not 1: the repo's convention is 0 it worked, 1 it ran and the
        # answer is no, 2 it could not run. Nothing was inspected here.
        print(why, file=sys.stderr)
        return 2
    from acidcat.tui_app import AcidcatTUI
    AcidcatTUI(args.file).run()
    return 0
