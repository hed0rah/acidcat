"""
acidcat tui -- interactive terminal inspector + metadata editor.

A Textual front-end over the walk_file/anomalies/write engine: browse a file's
structure as a tree, see each node's bytes in a hex pane, and edit the metadata
the CLI `write` supports. Run with no file to open the built-in file browser.
Opt-in extra so the core stays zero-dependency: `pip install acidcat[tui]`.
"""
import os


def register(subparsers):
    p = subparsers.add_parser(
        "tui", help="Interactive terminal inspector/editor (needs acidcat[tui]).")
    p.add_argument("file", nargs="?",
                   help="Audio or synth/DAW preset file. Omit to browse.")
    p.set_defaults(func=run)


def run(args):
    from acidcat.util.deps import require
    if not require("textual", group="tui"):
        return 1
    if args.file and not os.path.isfile(args.file):
        print(f"not a file: {args.file}")
        return 1
    from acidcat.tui_app import AcidcatTUI
    AcidcatTUI(args.file).run()
    return 0
