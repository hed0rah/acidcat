"""acidcat classify -- what kind of thing is this, and what should look at it next.

The triage question, before any expensive analysis: is this a single file we
understand, a bigger thing with files inside, damaged remains of either, or not
audio at all. Each verdict names the verb that follows, so an unknown file is
the start of a workflow rather than a dead end.

    acidcat classify mystery.bin           # one verdict, with its evidence
    acidcat classify samples/ --json       # triage a whole tree
    acidcat classify huge.img --shallow    # magic + structure only, no sweep

Cheap by construction: magic detection is ~0.08 ms, the embedded-container
sweep ~76 ms on 32 MB. The statistical audio scan (~13 s on the same file) is
never run here -- when it is the right next step, that is reported, not done.
"""

import contextlib
import json
import os
import sys

from acidcat.commands._output import add_output_format_arg, add_report_arg
from acidcat.util import stdin as stdinmod
from acidcat.core.forensics.classify import classify as classify_file
from acidcat.core.infra.render import output
from acidcat.util.color import add_color_arg, color_enabled
from acidcat.util.stdin import display_name

_SHAPE_COLOR = {
    "single": "32",       # green: understood
    "container": "36",    # cyan: holds things
    "chunked": "36",
    "unwalked": "35",     # magenta: we know what it is, we just do not parse it
    "damaged": "33",      # yellow: recoverable with work
    "opaque": "90",       # dim: nothing structural
    "foreign": "90",
    "empty": "90",
}


def register(subparsers):
    p = subparsers.add_parser(
        "classify",
        help="Triage a file: single format, container, damaged, or not audio -- "
             "and what to run next.")
    p.add_argument("targets", nargs="+", metavar="FILE",
                   help="Files or directories to triage.")
    p.add_argument("--shallow", action="store_true",
                   help="Magic and chunk structure only -- skip the embedded "
                        "container sweep and resync. For large trees where the "
                        "per-file sweep would dominate.")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
    add_report_arg(p)
    add_color_arg(p)
    p.add_argument("--problems-only", action="store_true",
                   help="Report only files that are not a plain single file of "
                        "their format (the ones worth a closer look).")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Drop progress and summary lines on stderr (never "
                        "changes stdout).")
    p.set_defaults(func=run)


def _iter_targets(targets):
    for t in targets:
        if os.path.isdir(t):
            for root, _dirs, files in os.walk(t):
                for fn in sorted(files):
                    yield os.path.join(root, fn)
        else:
            yield t


def _c(code, text, on):
    return f"\033[{code}m{text}\033[0m" if on else text


# verdicts that mean "there is nothing here acidcat can work with". Every other
# shape names something it understood well enough to hand to another verb.
_NOTHING_FOUND = {"opaque", "foreign", "empty"}


def run(args):
    on = color_enabled(args)
    fmt = getattr(args, "output_format", "table")
    rows, exit_code = [], 0
    # counted separately from `rows` because --quiet drops the `single` rows,
    # and "nothing interesting to show" is a success, not a negative result
    identified = 0

    with contextlib.ExitStack() as stack:
        for path in _iter_targets(args.targets):
            # classify is the documented entry point of the triage pipeline
            # ("point it at anything"), and it was the one verb that could not
            # start one: `cat blob | acidcat classify -` was a file-not-found.
            display = path
            if stdinmod.is_stdin_target(path):
                path = stack.enter_context(stdinmod.resolved_input(path))
                if path is None:
                    print("acidcat classify: no data on stdin", file=sys.stderr)
                    exit_code = 2
                    continue
                display = "<stdin>"     # never the temp copy's path
            try:
                v = classify_file(path, deep=not args.shallow)
            except OSError as e:
                print(f"acidcat classify: {display}: {e}", file=sys.stderr)
                exit_code = 2                  # could not read it, not a verdict
                continue
            if v["shape"] not in _NOTHING_FOUND:
                identified += 1
            if getattr(args, "problems_only", False) and v["shape"] == "single":
                continue
            name = display if display == "<stdin>" else display_name(path)
            # `file` is for reading, `path` is for running: the latter must stay
            # the real filesystem path or a consumer cannot act on the verdict.
            # `next` alone was a bare verb ("locate") with no target, so the one
            # field whose whole purpose is "what to run now" could not be run --
            # next_command is the same line the table prints.
            target = display if display == "<stdin>" else os.path.normpath(path)
            nxt = v["next"] or ""
            rows.append({"file": name, "shape": v["shape"],
                         "format": v["format"] or "", "next": nxt,
                         # always quoted, unlike the table's display hint:
                         # _shell_quote only quotes on spaces, and a Windows
                         # path's backslashes are eaten by the shell unquoted
                         "next_command": (f'acidcat {nxt} "{target}"'
                                          if nxt else ""),
                         "detail": v["detail"], "path": target,
                         "evidence": v["evidence"]})

    # 1 when nothing among the targets was identifiable, so `classify f &&
    # inspect f` stops instead of running inspect on a file classify just
    # called opaque. A read failure (2) outranks it.
    if not exit_code and not identified:
        exit_code = 1

    if fmt in ("json", "csv", "tsv"):
        # the machine rows name a file by `path` and a format by registry id
        # and label (cli-2.0.md section 4.1); `file`, the display name, is
        # the table's
        from acidcat.core.walk import _WALKERS
        mrows = []
        for r in rows:
            m = {k: v for k, v in r.items() if k != "file"}
            m["format"] = r["format"] or None
            m["label"] = _WALKERS[r["format"]][0] if r["format"] in _WALKERS else m["format"]
            mrows.append(m)
        if fmt == "json":
            json.dump(mrows, sys.stdout, indent=2, default=str)
            sys.stdout.write("\n")
            return exit_code
        # both delimited renderings, not just csv: listing tsv in the choices
        # while falling through to the table would be a flag that is accepted
        # and ignored, which is the bug this pass exists to remove.
        output([{k: r[k] for k in ("path", "shape", "format", "label", "next", "detail")}
                for r in mrows], fmt=fmt)
        return exit_code

    if not rows:
        print("(nothing to report)", file=sys.stderr)
        return exit_code
    wid = min(38, max(len(r["file"]) for r in rows))
    for r in rows:
        shape = _c(_SHAPE_COLOR.get(r["shape"], "0"), f"{r['shape']:9}", on)
        nxt = _c("1", r["next"], on) if r["next"] else _c("90", "-", on)
        print(f"{r['file'][:wid]:<{wid}}  {shape}  {r['detail']}")
        if r["next"]:
            print(f"{'':<{wid}}  {'':9}  next: {nxt} "
                  f"{_shell_quote(r['file'])}")
    return exit_code


def _shell_quote(name):
    return f'"{name}"' if any(c in name for c in ' \t&()+;') else name
