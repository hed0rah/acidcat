"""
acidcat CLI -- top-level argument parser and subcommand dispatcher.

Usage:
    acidcat file.wav                 # info for a single file (WAV, AIFF, MIDI, Serum)
    acidcat /path/to/samples         # scan a directory
    acidcat info file.aif            # explicit info subcommand
    acidcat scan DIR [-n N]          # batch scan
    acidcat chunks file.wav          # RIFF chunk walk
    acidcat survey DIR               # chunk type census
    acidcat detect file.wav          # librosa BPM/key estimation
    acidcat features DIR             # ML feature extraction
    acidcat similar CSV TARGET       # similarity search
    acidcat search CSV QUERY         # text search
    acidcat dump file.wav acid       # hex dump a chunk
"""

import argparse
import os
import sys

from acidcat import __version__
from acidcat.commands import info, scan, chunks, survey, detect, features, similar, search, dump

SUBCOMMANDS = {"info", "scan", "chunks", "survey", "detect", "features", "similar", "search", "dump"}


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="acidcat",
        description="Audio metadata explorer and analysis tool.",
    )
    parser.add_argument("--version", action="version", version=f"acidcat {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    info.register(subparsers)
    scan.register(subparsers)
    chunks.register(subparsers)
    survey.register(subparsers)
    detect.register(subparsers)
    features.register(subparsers)
    similar.register(subparsers)
    search.register(subparsers)
    dump.register(subparsers)

    return parser


def _try_bare_path(argv):
    """
    If the first non-flag arg is a path (not a subcommand), auto-route to
    info (file) or scan (directory).
    """
    if argv is None:
        argv = sys.argv[1:]

    # is the first positional arg a known subcommand?
    positionals = [a for a in argv if not a.startswith("-")]
    if not positionals:
        return None
    first = positionals[0]
    if first in SUBCOMMANDS:
        return None  # let normal parsing handle it

    # not a subcommand -- is it a path?
    if os.path.exists(first):
        # build a lightweight fallback parser that accepts the bare-path form
        fb = argparse.ArgumentParser(add_help=False)
        fb.add_argument("target")
        fb.add_argument("-f", "--format", default="table")
        fb.add_argument("-o", "--output", default=None)
        fb.add_argument("-q", "--quiet", action="store_true")
        fb.add_argument("-v", "--verbose", action="store_true")
        fb.add_argument("--deep", action="store_true")
        fb.add_argument("-n", "--num", type=int, default=500)
        fb.add_argument("--has", default=None)
        fb.add_argument("--fallback", action="store_true")
        fb.add_argument("--features", action="store_true")
        fb.add_argument("--ml-ready", dest="ml_ready", action="store_true")
        fb_args, _ = fb.parse_known_args(argv)

        if os.path.isfile(fb_args.target):
            return info.run(fb_args)
        elif os.path.isdir(fb_args.target):
            return scan.run(fb_args)

    return None


def main(argv=None):
    # try bare-path dispatch first (before argparse can error on unknown subcommand)
    result = _try_bare_path(argv)
    if result is not None:
        return result

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if hasattr(args, 'func'):
        return args.func(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
