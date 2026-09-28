"""Shared CLI wiring for the output-format axis -- one definition, every command.

acidcat reserves three words so a flag never means two things:
  * **format**        a file's container/codec (WAV, FLAC, MP3) -- ``--format`` /
                      ``-f`` where a command filters or forces the input type.
  * **output**        where bytes go: ``-o`` / ``--output FILE`` (default stdout).
  * **output-format** how records are rendered: ``--output-format`` + the
                      ``--json`` / ``--csv`` shorthands, choices from the
                      render registry so they never drift.

``-f`` was historically the short output-rendering flag on many commands; it
still works but warns. ``--format`` (long) is reserved for the file-format axis
and never selects rendering. Add the standard flags with
``add_output_format_arg(parser)``.
"""

import contextlib
import argparse
import sys

from acidcat.core.infra import render


class _DeprecatedOutputFormat(argparse.Action):
    """Back-compat for the old -f/--format output flag: still sets the rendering
    but warns, since --format now means the file's format."""

    def __call__(self, parser, namespace, values, option_string=None):
        sys.stderr.write(
            f"acidcat: warning: {option_string} is deprecated for output "
            f"rendering; use --output-format {values} (or --json / --csv). "
            f"--format now selects the file format.\n")
        setattr(namespace, self.dest, values)


def add_output_format_arg(parser, default="table", only=None, deprecated_f=False):
    """Add the standard output-rendering flags to ``parser``.

    Adds ``--output-format`` (choices from the render registry, or the ``only``
    subset) plus ``--json`` / ``--csv`` shorthands when those formats are
    allowed. With ``deprecated_f`` (default), also accepts the old ``-f`` /
    ``--format`` spelling with a deprecation warning. The chosen format lands in
    ``args.output_format``; pass it to ``render.output(data, fmt=...)``.
    """
    choices = list(only) if only else list(render.output_formats())
    parser.add_argument(
        "--output-format", dest="output_format", default=default,
        choices=choices, metavar="FMT",
        help=f"Output rendering: {', '.join(choices)} (default: {default}).")
    if "json" in choices:
        parser.add_argument(
            "--json", dest="output_format", action="store_const", const="json",
            help="Render as JSON (shorthand for --output-format json).")
    if "csv" in choices:
        parser.add_argument(
            "--csv", dest="output_format", action="store_const", const="csv",
            help="Render as CSV (shorthand for --output-format csv).")
    if deprecated_f:
        # -f (short only) is the transitional bridge for the old output spelling.
        # --format is deliberately NOT added: it belongs to the file-format axis.
        parser.add_argument(
            "-f", dest="output_format", action=_DeprecatedOutputFormat,
            choices=choices, metavar="FMT", help=argparse.SUPPRESS)
    return parser


def chosen_format(args, default="table"):
    """The rendering the caller asked for.

    Reads ``args.output_format``, and falls back to a legacy ``args.json`` /
    ``args.as_json`` boolean. Three verbs (audit, extract, probe) declared a
    bare ``--json`` store_true instead of going through
    ``add_output_format_arg``, so ``--output-format json`` -- which works on 26
    other verbs -- was an error on the forensic one, the recovery one and the RE
    one. Converting them changed the attribute the code reads, and anything
    constructing an args object programmatically (tests, and the public API)
    would have silently lost its JSON. One accessor, so no verb has to remember
    which spelling it grew up with.
    """
    fmt = getattr(args, "output_format", None)
    if fmt:
        return fmt
    if getattr(args, "json", False) or getattr(args, "as_json", False):
        return "json"
    return default


@contextlib.contextmanager
def out_stream(path):
    """Yield a writable stream for ``path``, or stdout when it is None.

    The same three lines were written two different ways across sibling verbs:
    census/features/info/query/scan/similar wrapped the write in try/finally,
    while chunks/detect/survey did a bare `if stream is not sys.stdout:
    stream.close()` after it. In the second form an exception during the write
    -- a full disk, or a value the renderer cannot serialize -- leaves the
    output file open, and on Windows that keeps the partial file locked until
    the GC gets to it. That is the leaked-handle class that has already cost a
    fix here.

    Never closes stdout.
    """
    stream = sys.stdout if not path else open(path, "w", encoding="utf-8",
                                              newline="")
    try:
        yield stream
    finally:
        if stream is not sys.stdout:
            stream.close()


# ── the one JSON rule (cli-2.0.md section 4.1) ─────────────────────────

def format_of(path):
    """{"format": registry id, "label": its display label} for the file at
    `path`, both None when nothing recognises it. Every verb's JSON names a
    format this way: `format` is what `acidcat formats` lists and
    `--force-format` takes, `label` what a person reads."""
    from acidcat.core.infra import sniff as sniffmod
    from acidcat.core.walk import _WALKERS
    try:
        fid = sniffmod.sniff(path)
    except (OSError, ValueError):
        fid = None
    if not fid:
        return {"format": None, "label": None}
    entry = _WALKERS.get(fid)
    return {"format": fid, "label": entry[0] if entry else fid}


def snake(key):
    """A display label as a JSON key: `ACID Root` -> `acid_root`."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_") or "_"


def add_report_arg(parser):
    """`-o/--output PATH` for a verb whose -o means only "the report goes
    here": the dispatcher sends stdout to PATH for the run (cli._run_one), so
    the verb's own printing needs no change. Verbs whose -o names something
    else (carve's bytes, convert's file, edit's copy) keep their own."""
    parser.add_argument("-o", "--output", dest="report_to", metavar="PATH",
                        help="Write the report here instead of stdout.")
    return parser

