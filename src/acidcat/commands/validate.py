"""acidcat validate -- report structural constraint violations, read-only.

The read-only face of the constraint model (core/constraints): it runs the same
analysis ``repair`` uses, but writes nothing and returns an exit code, so it fits
a CI check or a sweep over a whole library to find the broken files before they
bite. A file whose container acidcat does not model structurally is skipped, not
failed.

    acidcat validate FILE...            # check specific files
    acidcat validate DIR                # walk a directory tree
    acidcat validate DIR -q             # only print files with issues

Exit status: 0 when every checked file is consistent, 1 when any file has a
violation, 2 on a usage error.
"""

import os
import sys

from acidcat.commands._output import add_output_format_arg, format_of
from acidcat.core.infra.render import output as _render
from acidcat.core.infra.mapped import map_file
from acidcat.core.write import constraints
from acidcat.core.forensics.checksums import _READ_CAP
from acidcat.util import targets as _targets
from acidcat.util.stdin import as_given, display_name

# No private extension list. validate kept its own 14-entry tuple, so a
# directory walk opened .wav and skipped .w64, .ogg, .opus, .caf and every
# tracker and preset type, then printed "all N file(s) consistent" with exit 0
# -- a CI gate reporting a clean tree it had never looked at. Adding .mp3 to
# that tuple fixed one symptom and left the cause: a second list, drifting from
# the real one. util/targets.py exists because eight commands each grew one.


def register(subparsers):
    p = subparsers.add_parser(
        "validate",
        help="Check container structure for stale size/offset/pad fields (read-only).")
    p.add_argument("inputs", nargs="+", help="File(s) or directory(ies) to check.")
    p.add_argument("--deep", action="store_true",
                   help="Also verify the checksums a format carries about "
                        "itself: FLAC frame CRCs, MP3 frame validity. These "
                        "PROVE damage rather than infer it, but cost a full "
                        "read (~10 MB/s), so they are off by default.")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Only print files that have violations.")
    # validate is the CI-gate verb: you could branch on its exit code but not
    # read WHICH file failed or WHY without scraping the human table. json
    # carries the violations nested; csv/tsv flatten to one row per file.
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
    p.set_defaults(func=run)


def _iter_paths(inputs):
    """Files to check, plus the count the walk passed over.

    The skip count is returned, not swallowed: a silent filter is
    indistinguishable from an empty directory, which is how this verb came to
    report a clean tree it had not read.
    """
    files, skipped = _targets.expand(inputs)
    return files, skipped


def _deep_check(path, data):
    """Verify the integrity data the format carries about itself.

    Structural analysis asks whether the container's own arithmetic adds up.
    This asks a different and stronger question: does the payload still match
    the checksum written over it? A failure here is proof, not inference.

    Returns ``{"ran": bool, "failure": str|None, "caveat": str|None}``.

    Three states, because two are not enough. This used to return a string on
    failure and None otherwise, which made "this format carries nothing
    checkable" indistinguishable from "checked it, it is clean" -- so a valid
    MP3 that passed a deep check was reported as never examined, and
    `validate --deep song.mp3` exited 2 on a healthy file. `validate f && ship f`
    could therefore never pass an MP3.

    ``caveat`` carries the read cap. checksums stops at _READ_CAP and says so in
    its result; nothing read that flag, so a 200 MB FLAC was verified over its
    first 64 MB and reported as clean. A partial verification presented as a
    whole one is the failure this command exists to catch.

    Only FLAC and MP3 for now; both are verifiable without decoding, which
    matters because acidcat bundles no decoders.
    """
    from acidcat.core.forensics import checksums

    def _no():
        return {"ran": False, "failure": None, "caveat": None}

    def _cap(r, unit):
        if not r.get("partial"):
            return None
        return (f"scanning stopped at the {_READ_CAP // (1024 * 1024)} MB read "
                f"cap, so {unit} past that point were NOT verified")

    head = bytes(data[:4])
    if head == b"fLaC":
        pos = 4
        while pos + 4 <= len(data):
            hdr = data[pos]
            ln = int.from_bytes(bytes(data[pos + 1:pos + 4]), "big")
            pos += 4 + ln
            if hdr & 0x80:
                break
        else:
            return _no()
        r = checksums.flac_frames(data, pos, len(data), cap=None)
        caveat = _cap(r, "frames")
        if r["failed"]:
            where = ", ".join(f"0x{o:08x}" for o in r["offsets"][:3])
            return {"ran": True, "caveat": caveat,
                    "failure": (f"{r['failed']} of {r['checked']} FLAC frame(s) "
                                f"fail their CRC-16 (at {where}) -- the audio no "
                                f"longer matches the checksum the encoder wrote "
                                f"over it")}
        # nothing to verify is not the same as verified-and-clean
        if not r["checked"]:
            return _no()
        return {"ran": True, "failure": None, "caveat": caveat}

    start = 0
    if head[:3] == b"ID3":
        b = bytes(data[:10])
        start = 10 + ((b[6] & 0x7F) << 21 | (b[7] & 0x7F) << 14
                      | (b[8] & 0x7F) << 7 | (b[9] & 0x7F))
    elif not (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return _no()
    r = checksums.mp3_frames(data, start, cap=None)
    caveat = _cap(r, "frames")
    bad = r["resyncs"] + r["bad_bigvalues"] + r["bad_backref"]
    if bad and r["frames"]:
        where = ", ".join(f"0x{o:08x}" for o in r["offsets"][:3])
        return {"ran": True, "caveat": caveat,
                "failure": (f"{bad} damaged MP3 frame(s) of {r['frames']} (at "
                            f"{where}): {r['resyncs']} lost sync, "
                            f"{r['bad_bigvalues']} impossible big_values, "
                            f"{r['bad_backref']} dangling bit-reservoir "
                            f"back-reference(s)")}
    if not r["frames"]:
        return _no()
    return {"ran": True, "failure": None, "caveat": caveat}


def _check(path, quiet, rows=None, deep=False):
    """Return (checked, ok, error, repairable): checked is False for a skipped or
    unreadable file; error is True only when the file could not be read (I/O
    error), as opposed to being a format acidcat does not structurally model (a
    clean skip). When `rows` is given, append a record instead of printing --
    the same verdict, in the machine's shape.
    """
    # Mapped, not slurped -- the same treatment `audit` gives the same bytes.
    # constraints.analyze only walks the chunk-size cascade and never needs a
    # payload, but f.read() pulled the whole file in: on a 41 MB WAV that was
    # 82.9 MB of Python heap against 0.01 MB mapped, and validate's footprint
    # scaled with input where audit's stayed flat. A directory sweep over a
    # library of multi-GB RF64 files was slurping each one entire.
    # The memoryview matters as much as the map: the IFF engine keeps a slice
    # of every chunk it parses, and a view slice is a zero-copy window where a
    # bytes slice would materialize the payload.
    try:
        data, close = map_file(path)
    except OSError as e:
        print(f"acidcat check: {path}: {e}", file=sys.stderr)
        if rows is not None:
            rows.append({"path": as_given(path), **format_of(path), "status": "unreadable",
                         "issues": 0, "repairable": False, "detail": str(e)})
        return False, True, True, False
    deep_res = {"ran": False, "failure": None, "caveat": None}
    try:
        with memoryview(data) as view:
            report = constraints.analyze(view)
        if deep:
            deep_res = _deep_check(path, data)
    finally:
        close()
    deep_note = deep_res["failure"]
    caveat = deep_res["caveat"]
    if report is None:
        # A format acidcat does not structurally model can still carry a
        # checksum over its own bytes -- MP3 is exactly that case -- so --deep
        # has a verdict here even though the structural pass does not.
        if deep_note:
            detail = deep_note + (f"; {caveat}" if caveat else "")
            if rows is not None:
                rows.append({"path": as_given(path), **format_of(path), "status": "fail",
                             "issues": 1, "repairable": False,
                             "detail": detail})
            else:
                print(f"FAIL  {display_name(path)}  [deep]  1 issue(s)")
                print(f"        {detail}")
            return True, False, False, False
        if deep_res["ran"]:
            # Checked by --deep and clean. Reporting this as "not modelled" made
            # a passing deep check exit 2, so `validate f && ship f` could never
            # pass an MP3 -- the check ran, proved the payload matches its own
            # checksums, and the result was thrown away.
            if rows is not None:
                rows.append({"path": as_given(path), **format_of(path), "status": "ok",
                             "issues": 0, "repairable": False,
                             "detail": caveat or "deep check passed"})
            elif not quiet:
                print(f"OK    {display_name(path)}  [deep]")
                if caveat:
                    print(f"        {caveat}")
            return True, True, False, False
        if rows is not None:
            # a skip is a real answer and belongs in the record set, so a
            # consumer can tell "checked, clean" from "never modelled"
            rows.append({"path": as_given(path), **format_of(path), "status": "skipped",
                         "issues": 0, "repairable": False,
                         "detail": "not a structurally-modeled container"})
        return False, True, False, False        # not a structurally-modeled container
    base = display_name(path)
    if not report.violations and not deep_note:
        if rows is not None:
            rows.append({"path": as_given(path), **format_of(path), "status": "ok",
                         "issues": 0, "repairable": False,
                         "detail": caveat or ""})
        elif not quiet:
            print(f"OK    {base}  [{report.label}]")
            if caveat:
                # a pass over part of a file is not a pass over the file
                print(f"        {caveat}")
        return True, True, False, False
    if not report.violations:
        # structurally sound, but a checksum over its own payload disagrees --
        # which is a stronger statement than any structural check can make.
        # The caveat belongs here too: "damage found" over a partial scan still
        # leaves the rest of the file unexamined, and this branch was the one
        # path of four that did not say so.
        detail = deep_note + (f"; {caveat}" if caveat else "")
        if rows is not None:
            rows.append({"path": as_given(path), **format_of(path), "status": "fail",
                         "issues": 1, "repairable": False, "detail": detail})
        else:
            print(f"FAIL  {base}  [{report.label}]  1 issue(s)")
            print(f"        {detail}")
        return True, False, False, False
    if rows is not None:
        rows.append({"path": as_given(path), **format_of(path), "status": "fail",
                     "issues": len(report.violations),
                     "repairable": any(v.repairable for v in report.violations),
                     "detail": "; ".join(v.describe() for v in report.violations),
                     "violations": [{"describe": v.describe(), "kind": v.kind,
                                     "field": v.field, "stored": v.stored,
                                     "computed": v.computed,
                                     "repairable": v.repairable}
                                    for v in report.violations]})
        return True, False, False, any(v.repairable for v in report.violations)
    print(f"FAIL  {base}  [{report.label}]  {len(report.violations)} issue(s)")
    for v in report.violations:
        mark = "" if v.repairable else "  (no witness)"
        print(f"        {v.describe()}{mark}")
    return True, False, False, any(v.repairable for v in report.violations)


def run(args):
    from acidcat.util.stdin import resolved_input
    from contextlib import ExitStack
    # `-` is stdin, resolved up front; real paths pass through unchanged.
    with ExitStack() as stack:
        args.inputs = [
            stack.enter_context(resolved_input(t)) if t == "-" else t
            for t in args.inputs
        ]
        if any(t is None for t in args.inputs):
            print("acidcat check: no data on stdin", file=sys.stderr)
            return 2
        return _run(args)


def _run(args):
    # grep/diff exit-code family: 0 = every checked file is consistent,
    # 1 = some file has a violation (ran fine), 2 = an input could not be
    # read. a named input that cannot be read stops the verdict; a file inside
    # a walked directory that cannot be read is counted and the walk goes on,
    # but the run still exits 2 (the contract's unreadable input).
    checked = failed = errors = unreadable = total_skipped = 0
    unmodeled = 0
    any_repairable = False
    fmt = getattr(args, "output_format", "table")
    rows = None if fmt == "table" else []
    for inp in args.inputs:
        if not os.path.exists(inp):
            print(f"acidcat check: {inp}: No such file or directory",
                  file=sys.stderr)
            errors += 1
            continue
        named = not os.path.isdir(inp)
        paths, skipped = _iter_paths([inp])
        total_skipped += skipped
        for path in paths:
            did, ok, error, repairable = _check(
                path, args.quiet, rows, deep=getattr(args, 'deep', False))
            any_repairable = any_repairable or repairable
            if error:
                # Inside a directory walk an unreadable file used to be counted
                # nowhere -- not checked, not failed, not an error -- so a run
                # over a library with locked files printed "all N consistent"
                # and exited 0. It is not a pass: it is input the verb could
                # not read, which the contract gives 2, outranking a defect.
                if named:
                    errors += 1
                else:
                    unreadable += 1
            if did:
                checked += 1
                if not ok:
                    failed += 1
            elif not error:
                # walked, opened, and structurally unmodeled. Counted, because
                # otherwise it leaves no trace at all: "all 2 file(s)
                # consistent" over a directory of three audio files reads as a
                # verdict on all three.
                unmodeled += 1
    if rows is not None:
        # csv/tsv are one flat row per file; the nested per-violation detail
        # only survives in json, so drop the key rather than stringify a list
        # into a cell nobody can parse.
        if fmt in ("csv", "tsv"):
            rows = [{k: v for k, v in r.items() if k != "violations"} for r in rows]
        _render(rows, fmt=fmt)
    if errors:
        return 2
    skipped = f", {unreadable} unreadable (not checked)" if unreadable else ""
    # A filtered-out file is a file NOT checked, and the whole point of this
    # verb is that it says what it looked at.
    if total_skipped:
        skipped += (f", {total_skipped} skipped (unrecognised extension; name a "
                    f"file directly to force it)")
    if unmodeled:
        skipped += f", {unmodeled} not structurally modeled (not checked)"
    if checked == 0:
        # 2, not 0. `validate` is the natural gate in a script, and returning
        # success for files it never modelled gave a clean bill of health to
        # anything it did not understand -- `validate garbage.bin` and
        # `validate track.mod` both said "fine" while `audit` on the same byte
        # had findings. Nothing checked is "could not do the job", the same
        # class as an unreadable input, not a passing result.
        from acidcat.commands._output import checked_formats
        print("acidcat check: no structurally-modeled files to check"
              + skipped + f"; check covers: {checked_formats()}", file=sys.stderr)
        return 2
    if failed:
        # only point at repair when something is actually repairable. An
        # orphaned audio payload has no safe rewrite and repair refuses it, so
        # the advice would send the user round a loop.
        hint = " (fix with: acidcat check --fix)" if any_repairable else ""
        # stdout belongs to the records in a machine format -- a trailing human
        # sentence made the JSON unparseable ("Extra data"). `hush` (check -q)
        # drops it there, and only there: -q never changes stdout.
        if not (getattr(args, "hush", False) and rows is not None):
            print(f"\n{failed} of {checked} file(s) have structural issues"
                  f"{hint}{skipped}", file=sys.stderr if rows is not None else sys.stdout)
        return 2 if unreadable else 1
    if not args.quiet and not (getattr(args, "hush", False) and rows is not None):
        print(f"\nall {checked} file(s) consistent{skipped}",
              file=sys.stderr if rows is not None else sys.stdout)
    return 2 if unreadable else 0
