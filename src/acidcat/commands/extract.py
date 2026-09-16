"""acidcat extract -- pull the embedded samples out of a bank or module as WAVs.

`inspect` shows the samples are in there; `extract` gets them out. One verb over
every walked sample-bearing format (core/samples.py): tracker modules (MOD/XM/
IT), 8SVX, NCW, SoundFont. Decodes where there's a codec, copies verbatim where
the samples are already PCM. Read-only on the source.

    acidcat extract kit.mod                 # -> kit_samples/0001_*.wav ...
    acidcat extract font.sf2 -o out/
    acidcat extract song.xm --json          # manifest to stdout
    cat kit.mod | acidcat extract -         # from stdin
"""

import json
import os
import sys

from acidcat.core.extract import samples as smod
from acidcat.core.infra.render import format_json
from acidcat.commands._output import (add_output_format_arg,
                                      chosen_format)
from acidcat.util.stdin import is_stdin_target, stdin_to_tempfile


def register(subparsers):
    p = subparsers.add_parser(
        "extract", help="Extract embedded samples (MOD/XM/IT, 8SVX, NCW, SF2, PDX) or a "
                        "PS1/CD-XA disc soundtrack to WAVs.")
    p.add_argument("input", help="Bank/module/disc image to extract from, or '-' "
                                 "for stdin.")
    p.add_argument("-o", "--output", metavar="DIR",
                   help="Output directory (default: <input>_samples).")
    # the manifest is one flat record per sample, so it gets the full set.
    # Note this is a RENDERING choice, not a dry-run switch: the help below
    # says a machine format prints the manifest INSTEAD of writing files.
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress the per-sample line on stderr.")
    p.set_defaults(func=run)


def _safe(name, idx, ext="wav"):
    keep = "".join(c if c.isalnum() or c in " -_.()" else "_" for c in (name or ""))
    return f"{idx:04d}_{keep.strip() or 'sample'}.{ext}"


def run(args):
    tmp = None
    path = args.input
    # the name to put in messages: stdin is buffered to a temp file so the
    # byte-level parsers can seek, and leaking that path told the user about a
    # file they never named and which no longer exists by the time they read it
    display = path
    if is_stdin_target(path):
        tmp = stdin_to_tempfile()
        if tmp is None:
            print("acidcat extract: no input on stdin", file=sys.stderr)
            return 2
        path = tmp
        display = "<stdin>"
    elif not os.path.isfile(path):
        print(f"acidcat extract: {path}: No such file", file=sys.stderr)
        return 2

    try:
        records = list(smod.iter_samples(path))
    except smod.SampleError as e:
        print(f"acidcat extract: {display}: {e}", file=sys.stderr)
        return 1
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # records with wav=None are informational notes (e.g. skipped compressed samples)
    written = [r for r in records if r.get("wav")]
    notes = [r["note"] for r in records if not r.get("wav")]

    fmt = chosen_format(args)
    if fmt != "table":
        manifest = [{"index": i, "name": r["name"], "bytes": len(r["wav"]),
                     "note": r.get("note")} for i, r in enumerate(written)]
        if fmt == "json":
            # the envelope keeps `notes`, which is the channel that reports
            # samples the bank declared but does not contain -- dropping it
            # would put the count back to describing more work than was done
            format_json({"samples": manifest, "notes": notes}, sys.stdout)
        else:
            # csv/tsv are one row per sample; the notes have no column, so they
            # go to stderr rather than being silently discarded
            from acidcat.core.infra.render import output as _render
            _render(manifest, fmt=fmt)
            for n in notes:
                print(f"acidcat extract: {n}", file=sys.stderr)
        # a manifest of nothing is the same negative result the table path
        # reports; it used to return 0 here and 1 three lines below
        return 0 if written else 1

    if not written:
        # `display`, not args.input: on the stdin path the latter is "-" and
        # every other message in this function already says <stdin>
        print(f"acidcat extract: {display}: no extractable samples"
              + (f" ({'; '.join(notes)})" if notes else ""), file=sys.stderr)
        return 1

    base = args.input if not is_stdin_target(args.input) else "stdin"
    outdir = args.output or (os.path.splitext(base)[0] + "_samples")
    os.makedirs(outdir, exist_ok=True)
    for i, r in enumerate(written):
        ext = r.get("ext", "wav")
        with open(os.path.join(outdir, _safe(r["name"], i, ext)), "wb") as f:
            f.write(r["wav"])
        if not args.quiet:
            print(f"  {_safe(r['name'], i, ext)}  {r.get('note', '')}", file=sys.stderr)
    print(f"extracted {len(written)} sample(s) -> {outdir}"
          + (f"  ({'; '.join(notes)})" if notes else ""))
    return 0
