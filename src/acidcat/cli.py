"""
acidcat CLI -- the top-level parser, the 1.8 aliases, and dispatch.

    acidcat inspect FILE [--summary | --tags | --quiet]   # what is in it
    acidcat od FILE [ADDR]                                 # its bytes, annotated
    acidcat carve FILE ADDR [-o OUT]                       # take some out
    acidcat edit FILE --set NAME=VALUE                     # change it
    acidcat check FILE [--fix]                             # are its sizes right
    acidcat audit FILE                                     # anything suspicious
    acidcat stats DIR [--by meta|shape]                    # across a tree
    acidcat lib index DIR ; acidcat lib query --bpm 120    # the sample index
    acidcat FILE                                           # inspect --summary
    acidcat DIR                                            # stats

Seventeen verbs (docs/contract/cli-2.0.md). The 1.8 spellings still work
through 2.x: each prints one line on stderr naming its 2.0 form and then runs
exactly that (acidcat.cli_aliases).
"""

import argparse
import errno
import os
import sys
import traceback

from acidcat import __version__
from acidcat.commands._output import add_output_format_arg
from acidcat.commands import (
    inspect, od, carve, probe, classify, locate, audit, check, edit, stats,
    analyze, lib, convert, extract, formats, explore, tui,
)
from acidcat import cli_aliases
from acidcat.util.stdin import is_stdin_target

# Filled from the parser once it is built. It used to be a hand-maintained
# literal, which drifts the moment a verb is added: `census` and `wrap` were
# both missing, so a directory of either name in the cwd shadowed the command
# and `acidcat wrap ...` silently ran `scan` on the directory instead.
SUBCOMMANDS = set()


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="acidcat",
        description="Byte-level dissection of audio, sampler, synth-preset and DAW files.",
    )
    parser.add_argument("--version", action="version", version=f"acidcat {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    for module in (inspect, od, carve, probe, classify, locate, audit, check,
                   edit, stats, analyze, lib, convert, extract, formats,
                   explore, tui):
        module.register(subparsers)

    # keep a handle to the subparser table so unrecognized arguments can be
    # reported against the chosen subcommand's usage, not the top-level one.
    parser._sub = subparsers
    # derive the shadow-guard set from the parser itself, so adding a verb can
    # never again leave it out
    SUBCOMMANDS.update(subparsers.choices)
    return parser


def _bare_path(argv):
    """`acidcat FILE` is `acidcat inspect --summary FILE`, and `acidcat DIR`
    is `acidcat stats DIR`: the argv with the verb put in, or None when the
    first operand is a verb or not a path."""
    if not SUBCOMMANDS:                 # populate on first use
        _build_parser()
    positionals = [a for a in argv if not a.startswith("-") or a == "-"]
    if not positionals:
        return None
    first = positionals[0]
    if first in SUBCOMMANDS or first in cli_aliases._VERBS:
        return None
    if is_stdin_target(first) or os.path.isfile(first):
        return ["inspect", "--summary"] + list(argv)
    if os.path.isdir(first):
        return ["stats"] + list(argv)
    return None


def main(argv=None):
    """Entry point. Wraps the dispatch so a closed pipe is a normal exit.

    `acidcat od big.bin | head` closes stdout early; without this the
    interpreter reports BrokenPipeError on shutdown and exits non-zero, which
    makes every verb unsafe to pipe into a pager or `head`. Every other Unix
    tool treats it as "the reader left" and stops quietly, so we do too.
    """
    try:
        rc = _dispatch(argv)
        # flushed here, where a closed pipe is handled. A whole report can sit
        # in the buffer (3.14 buffers more), and then the write that meets the
        # closed pipe is the interpreter's own at exit, which reports it and
        # exits 120 however this function returned.
        sys.stdout.flush()
        return rc
    except OSError as e:
        if not _is_closed_pipe(e):
            # NOT a bare re-raise. `raise` here leaves main() entirely -- the
            # BaseException handler below is a sibling of this one, not an outer
            # net, so it never sees it. Every OSError from a parser therefore
            # kept printing a traceback and exiting 1, the code reserved for
            # "ran fine, and the answer is no", which is the exact hole the
            # handler below was added to close.
            traceback.print_exc()
            print(f"acidcat: {e.__class__.__name__}: {e}", file=sys.stderr)
            return 2
        # stop writing, and keep the interpreter's shutdown flush from raising
        # again on the dead descriptor
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 0
    except KeyboardInterrupt:
        print("\nacidcat: interrupted", file=sys.stderr)
        return 130                      # the shell's convention for SIGINT
    except SystemExit:
        raise                           # argparse's own exits are deliberate
    except BaseException:
        # An unhandled exception used to propagate, print a traceback, and let
        # the interpreter exit 1 -- the same code the exit-code contract gives
        # to "ran fine, and the answer is no". So `validate f && ship f` read a
        # crash as a clean negative, and `audit f || quarantine f` quarantined
        # on a bug. grep and diff, the tools that convention cites, both use 2
        # for "could not run", and that is what a crash is.
        #
        # The traceback still prints: this changes what the shell is told, not
        # what the developer sees.
        traceback.print_exc()
        print("acidcat: internal error (this is a bug); exiting 2",
              file=sys.stderr)
        return 2


def _is_closed_pipe(exc):
    """True when an OSError means "the reader went away".

    POSIX raises BrokenPipeError (EPIPE). Windows does not: writing to a pipe
    whose reader has closed surfaces as a plain OSError with EINVAL, or
    winerror 232 (ERROR_NO_DATA, "the pipe is being closed"). Matching only
    BrokenPipeError would leave `acidcat od big.bin | head` printing a
    traceback on Windows, which is where this tool mostly runs.
    """
    if isinstance(exc, BrokenPipeError):
        return True
    # EINVAL is the Windows spelling of a dead pipe, but it is ALSO what an
    # invalid output filename raises -- and treating that as "the reader left"
    # made a failed `carve -o` exit 0 having written nothing. A failed file
    # operation carries the path in .filename; a write to a broken stdout does
    # not, so that is the discriminator.
    if exc.filename is not None:
        return False
    return (exc.errno in (errno.EPIPE, errno.EINVAL)
            or getattr(exc, "winerror", None) == 232)


def _dispatch(argv=None):
    # audio metadata is Unicode (UTF-8/UTF-16 tags), so emit UTF-8 regardless
    # of the platform default. Windows consoles and pipes default to cp1252 and
    # would raise UnicodeEncodeError on a non-Latin tag; replace stays a safety
    # net (all text encodes under UTF-8).
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    argv = list(argv) if argv is not None else sys.argv[1:]
    # no arguments and piped input: summarise what is on stdin
    if not argv and not sys.stdin.isatty():
        argv = ["-"]
    bare = _bare_path(argv)
    if bare is not None:
        argv = bare

    try:
        commands, note = cli_aliases.translate(argv)
    except cli_aliases.Removed as e:
        print(f"acidcat: {e}", file=sys.stderr)
        return 2
    # -q drops what goes to stderr; the alias note is part of that
    if note and not {"-q", "--quiet"} & set(argv):
        print(note, file=sys.stderr)
    rc = 0
    for cmd in commands:
        rc = max(rc, _run_one(cmd) or 0)
    return rc


def _run_one(argv):
    parser = _build_parser()
    args, extras = parser.parse_known_args(argv)
    if extras:
        # an unrecognized flag or stray argument. if a valid subcommand was
        # named, print that subcommand's usage (readelf/git behavior) rather
        # than the top-level usage, which is what the user actually needs.
        cmd = getattr(args, "command", None)
        msg = "unrecognized arguments: " + " ".join(extras)
        if cmd and cmd in parser._sub.choices:
            parser._sub.choices[cmd].error(msg)
        parser.error(msg)

    if args.command is None or not hasattr(args, "func"):
        # no verb is a usage error, the same class as a bad flag
        parser.print_help(sys.stderr)
        return 2
    report_to = getattr(args, "report_to", None)
    # an -o that names an input destroyed it: the report verbs open their
    # output before reading, and carve's field path wrote its bytes over the
    # file. edit and check --fix are exempt: there, -o naming the input is an
    # in-place edit through the atomic writer, with a backup -- except
    # `edit --get`, whose -o is a plain write of what it reads out.
    out = report_to or getattr(args, "output", None)
    in_place = (args.command == "check"
                or (args.command == "edit" and not getattr(args, "get", None)))
    if isinstance(out, str) and not in_place:
        from acidcat.util import outpath
        if outpath.input_named(out, argv):
            print(f"acidcat {args.command}: {out}: output is the input; "
                  f"refusing to overwrite the file being read", file=sys.stderr)
            return 2
    if report_to:
        # -o on a verb whose -o only redirects its report (add_report_arg)
        import contextlib
        try:
            fh = open(report_to, "w", encoding="utf-8", newline="")
        except OSError as e:
            print(f"acidcat {args.command}: {report_to}: {e.strerror or e}",
                  file=sys.stderr)
            return 2
        with fh, contextlib.redirect_stdout(fh):
            return args.func(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
