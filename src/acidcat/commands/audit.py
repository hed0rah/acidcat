"""acidcat audit -- a forensic verdict on one file.

Where `validate` answers "are the derived fields consistent" and `inspect` shows
the raw structure, `audit` composes read-only views into one report:

  STRUCTURE   the constraint model's violations (what `repair` would fix)
  HIDDEN      concealed or appended data, with the `carve` command to get it
  FORENSICS   other anomaly findings (high-entropy regions, duplicate or
              oversized chunks, spec violations)
  INTEGRITY   header vs audio: effective bit depth, duration consistency, and
              with --signal the decoded-audio checks (is this WAV really a
              decoded MP3, is this stereo really dual-mono)
  PROVENANCE  the writer/tool tells the file carries (encoder, software, muxer)

It is the "does the stored structure match reality, and who wrote it" question,
answered by reusing the same analyses the other verbs use. Writes nothing.

Every negative is a claim about a check that RAN. When there is no walker for
the format, the sections say "not scanned" and the verdict says "not
analyzable" rather than "clean" -- `locate` still finds embedded audio in a
file nothing can walk.

    acidcat audit FILE
    acidcat audit FILE --json          # machine-readable
    acidcat audit FILE --signal        # + decode the audio (needs numpy)
"""

import json
import os
import sys

from acidcat.commands._output import (add_output_format_arg,
                                      chosen_format)
from acidcat.core.forensics import anomalies, integrity, provenance
from acidcat.core.write import constraints
from acidcat.core.infra.mapped import map_file
from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported

# anomaly rules that mean concealed or appended data (vs structural lint) -- these
# get their own HIDDEN section with a carve hint to extract the region
_HIDDEN_RULES = {"trailing_data", "polyglot", "cavity_content",
                 "application_block", "mp4_mdat_coverage",
                 # Bytes nothing accounts for belong under HIDDEN, not
                 # FORENSICS. Without this the audit printed "no concealed or
                 # appended data" two lines above reporting exactly that, which
                 # is the tool contradicting itself in the same breath.
                 "unaccounted_bytes"}


def _carve_hint(path, finding):
    base = os.path.basename(path)
    if finding["rule"] == "trailing_data":
        return f"acidcat carve {base} --trailing -o out.bin"
    off = finding.get("offset")
    if off:
        return f"acidcat carve {base} --offset 0x{off:x} -o out.bin"
    return ""


def register(subparsers):
    p = subparsers.add_parser(
        "audit", help="Forensic verdict: structure + anomalies + provenance (read-only).")
    p.add_argument("input", nargs="+", metavar="FILE",
                   help="File(s) or directory(ies) to audit.")
    # through the shared registry, not a bare bool: --json here was the only
    # spelling, so `--output-format json` -- which works on 26 other verbs --
    # was an error on the forensic one. table+json only: an audit verdict is
    # nested (violations, findings, provenance) with no honest csv shape.
    add_output_format_arg(p, only=("table", "json"))
    p.add_argument("--signal", action="store_true",
                   help="Also analyze the decoded audio: bandwidth (is a WAV "
                        "really a decoded MP3) and channel relationship (is "
                        "stereo really dual-mono). Needs numpy and decodes the "
                        "samples, so it is opt-in.")
    p.set_defaults(func=run)


def _concealment_findings(path, chans, rate):
    """Ripper concealment: where a CD rip wrote over a sector it could not read.

    This says something the other signal checks do not. It is not a claim that
    the file is damaged -- it is a claim about the file's ORIGIN. A CD player
    conceals errors in the playback path by design and says nothing about it,
    while the CD-ROM drives used for ripping generally do not, so concealment
    reaching a file is evidence it came off a disc that would not read cleanly.
    """
    from acidcat.core.forensics import concealment
    from acidcat.core.walk import walk_file

    depth = None
    try:
        _label, chunks, _w = walk_file(path)
        for c in chunks:
            for f in c.get("fields") or ():
                if f.get("name") == "bits_per_sample":
                    depth = int(f.get("value") or 0)
                    break
    except Exception:
        return []
    if depth is None:
        return []

    try:
        found = concealment.scan_float_channels(chans, bit_depth=depth,
                                                sample_rate=rate)
    except Exception as e:
        return [{"check": "concealment", "verdict": "check-failed",
                 "detail": f"could not run ({type(e).__name__}); this file was "
                           f"NOT screened for ripper concealment"}]
    if found is None:
        # a cap that announces itself: silence here would read as "clean"
        return [{"check": "concealment", "verdict": "not-applicable",
                 "detail": f"{depth}-bit audio; concealment analysis applies to "
                           f"16-bit CD-derived material, so this file was not "
                           f"screened"}]
    if not found:
        return []
    return [{"check": "concealment", "verdict": "concealed-sectors",
             "detail": concealment.summarise(found)}]


def _signal_findings(path):
    """Bandwidth and channel checks from the decoded samples.

    Kept behind a flag and never fatal: these need numpy and a decode, and a
    file we cannot decode is not an audit failure -- it just has no signal
    evidence to add.
    """
    from acidcat.core.analysis import bandwidth, channels, pcm
    try:
        chans, rate = pcm.load(path)
    except Exception as e:
        # `--signal` on a file we cannot decode used to be byte-identical to a
        # file that passed both checks: the caller asked for the signal checks
        # and got silence that read as "clean". Say which checks did not run.
        # not-applicable, so it does not become a finding about the file -- the
        # decode failing is our limitation, not the file's fault.
        return [{"check": "signal", "verdict": "not-applicable",
                 "detail": f"could not decode the audio ({type(e).__name__}); "
                           f"bandwidth, channel and concealment checks were NOT "
                           f"run on this file"}]
    # only surface verdicts that say something is off. INTEGRITY counts what it
    # lists as mismatches, so reporting a healthy "stereo" or "no-wall" here
    # would turn every ordinary file into a finding.
    clean = {"no-wall", "stereo"}
    out = []
    out.extend(_concealment_findings(path, chans, rate))
    for analyze in (bandwidth.analyze, channels.analyze):
        # the analyzers were called outside the guard, so only pcm.load was
        # protected: a WAV declaring nSamplesPerSec = 0 reached a division in
        # the spectrum and took the whole verb down with a traceback
        try:
            check = analyze(chans, rate)
        except Exception as e:
            out.append({"check": getattr(analyze, "__module__", "signal"),
                        "verdict": "check-failed",
                        "detail": f"could not run ({type(e).__name__}); this "
                                  f"file was NOT screened for it"})
            continue
        if check and check["verdict"] not in clean:
            out.append({"check": check["check"], "verdict": check["verdict"],
                        "detail": check["detail"]})
    return out


def _gather(path, signal=False):
    scanned = True
    # audit is the forensic verdict on untrusted input, so the file is mapped,
    # not slurped: peak memory must not scale with file size (and a size cap
    # would reject legitimate multi-GB RF64/BW64 files)
    data, close = map_file(path)
    try:
        # constraints gets a memoryview: the IFF engine keeps a slice of every
        # chunk payload it parses, and view slices are zero-copy windows into
        # the map where mmap/bytes slices would materialize the whole payload
        with memoryview(data) as view:
            report = constraints.analyze(view)      # structural violations (or None)
        findings = []
        label = None
        prov = []
        # an MP3's Xing/VBRI frame count vs the frames actually present is a
        # truncation tell, but the walker only cross-checks it on the deep path;
        # audit is thorough by nature, so deep-walk MP3 (only) to surface it.
        from acidcat.core.infra import sniff as sniffmod
        try:
            deep = sniffmod.sniff(path) == "mp3"
        except OSError:
            deep = False
        try:
            label, chunks, warns = walk_file(path, deep=deep)
            findings = anomalies.scan(path, label, chunks, warns)
            # provenance/integrity only take small or capped slices, which an
            # mmap serves as plain bytes -- no adaptation needed
            prov = provenance.identify(label, chunks, data)
            integ = integrity.analyze(label, chunks, data)
            if signal:
                integ = integ + _signal_findings(path)
            scanned = True
        except Unsupported:
            # No walker for this format, so anomalies/provenance/integrity never
            # ran. Their empty results are NOT negative findings, and printing
            # them as "no concealed data" / "nothing flagged" / "clean" was a
            # confident claim about a scan that did not happen -- on a file
            # where `locate` can still find an embedded container.
            integ = []
            scanned = False
        if report is not None and label is None:
            label = report.label
        return label, report, findings, prov, integ, scanned
    finally:
        close()


# "I did not check this" is not a finding. A verdict that reports a check being
# skipped must be shown -- silence would read as "clean" -- but counting it as a
# mismatch turns every 24-bit file into a failure, which is a cap reported as a
# fact about the file.
# "check-failed" joins "not-applicable" here, and the distinction is the point:
# both mean acidcat did not produce a verdict, neither means the FILE has
# something wrong with it. A check that crashed is our infrastructure failing,
# and charging it to the file made `audit f || quarantine f` quarantine a clean
# file because our own analyser raised. Both still print as NOT CHECKED, so the
# gap stays visible; it is only the exit code and the mismatch count that stop
# blaming the file for it.
_NOT_A_FINDING = ("not-applicable", "check-failed")


def _real_findings(integ):
    return [i for i in integ if i.get("verdict") not in _NOT_A_FINDING]


def _skipped_notes(integ):
    return [i for i in integ if i.get("verdict") in _NOT_A_FINDING]


def _blaming_the_file(findings):
    """The findings that say the FILE has something to answer for.

    A `coverage` finding says our own walk stopped at an internal limit. It is
    printed, because a partial answer presented as a whole one is the defect
    this release is named for -- but it must not drive the exit code, or a
    structurally perfect file exits 1 for being large and
    `audit f || quarantine f` quarantines it. That was live across eighteen
    walker sites. An `environment` finding (a sibling not beside the file) and
    an `info` one are about the surroundings or merely worth knowing, and do
    not blame the file either.
    """
    return [f for f in findings
            if f.get("rule") not in ("coverage", "environment", "info")]


def _code(scanned, vios, findings, integ):
    """0 clean, 1 the file has something to answer for, 2 nothing was checked.

    audit returned 0 unconditionally, so the forensic verb could not gate a
    script: `audit f && ship f` shipped a file whose own report said
    "3 forensic alert(s)". 2 for an unscanned file keeps "no walker ran" apart
    from "walked it, clean" -- the distinction the report itself already draws
    in its section text.
    """
    if not scanned and not vios:
        return 2
    real = _blaming_the_file(findings)
    return 1 if (vios or real or _real_findings(integ)) else 0


def run(args):
    """One or many, files or directories -- audit is a per-file report,
    and it took a single file while `inspect` next to it took a list."""
    from acidcat.util import targets
    return targets.each(args, "input", _run_one, verb="audit")


def _run_one(args):
    path = args.input
    try:
        label, report, findings, prov, integ, scanned = _gather(
            path, getattr(args, "signal", False))
    except OSError as e:
        print(f"acidcat audit: {path}: {e}", file=sys.stderr)
        return 2
    size = os.path.getsize(path)

    if chosen_format(args) == "json":
        out = {
            "file": os.path.basename(path), "format": label, "size": size,
            "structure": [{"kind": v.kind, "path": v.path, "field": v.field,
                           "stored": v.stored, "computed": v.computed,
                           "witness": v.witness, "repairable": v.repairable}
                          for v in (report.violations if report else [])],
            "hidden": [f for f in findings if f["rule"] in _HIDDEN_RULES],
            "forensics": [f for f in findings if f["rule"] not in _HIDDEN_RULES],
            "provenance": prov,
            "integrity": integ,
            # so a consumer can tell "scanned, nothing found" from "never ran"
            "scanned": scanned,
        }
        print(json.dumps(out, indent=2, default=str))
        return _code(scanned, out["structure"], findings, integ)

    print(f"{os.path.basename(path)}  [{label or 'unknown'}]  {size:,} bytes\n")

    vios = report.violations if report else []
    if report is None:
        print("  STRUCTURE   not a structurally-modeled container")
    elif not vios:
        print("  STRUCTURE   consistent")
    else:
        n_fix = len(report.repairable)
        tail = f" (repairable with: acidcat repair)" if n_fix else ""
        print(f"  STRUCTURE   {len(vios)} issue(s){tail}")
        for v in vios:
            mark = f"  [{v.witness}]" if v.repairable else "  (no witness)"
            print(f"                {v.describe()}{mark}")

    hidden = [f for f in findings if f["rule"] in _HIDDEN_RULES]
    other = [f for f in findings if f["rule"] not in _HIDDEN_RULES]

    if not scanned:
        print("  HIDDEN      not scanned -- no walker for this format")
    elif not hidden:
        print("  HIDDEN      no concealed or appended data")
    else:
        print(f"  HIDDEN      {len(hidden)} region(s)")
        for f in hidden:
            at = f" @ 0x{f['offset']:08x}" if f.get("offset") else ""
            print(f"                {f['message']}{at}")
            hint = _carve_hint(path, f)
            if hint:
                print(f"                  extract: {hint}")

    if not scanned:
        print("  FORENSICS   not scanned -- no walker for this format")
        print("                try: acidcat locate " +
              os.path.basename(path) + "   (finds embedded audio regardless)")
    elif not other:
        print("  FORENSICS   nothing else flagged")
    else:
        print(f"  FORENSICS   {len(other)} finding(s)")
        for f in sorted(other, key=lambda x: -anomalies._SEVERITY.get(x["severity"], 0)):
            at = f" @ 0x{f['offset']:08x}" if f.get("offset") else ""
            print(f"                {f['severity']:<6} {f['message']}{at}")

    real, skipped = _real_findings(integ), _skipped_notes(integ)
    if not real:
        print("  INTEGRITY   header matches the audio (or not checkable)")
    else:
        print(f"  INTEGRITY   {len(real)} mismatch(es)")
        for it in real:
            print(f"                {it['verdict']}")
            print(f"                  {it['detail']}")
    for it in skipped:
        # named separately from the mismatch count, because a check that did
        # not run is not evidence about the file
        print(f"  NOT CHECKED {it['detail']}")

    if prov:
        top = prov[0]
        conf = "" if top["confidence"] == "high" else f" ({top['confidence']})"
        print(f"  PROVENANCE  written by: {top['tool']}{conf}")
        print(f"                basis: {top['basis']}")
        for s in prov[1:]:
            print(f"                also: {s['tool']} ({s['basis']})")
    else:
        print("  PROVENANCE  no writer tells")

    # one-line verdict
    n_fix = len(report.repairable) if report else 0
    alerts = sum(1 for f in findings if f["severity"] == "alert")
    bits = []
    if _real_findings(integ):
        bits.append(f"{len(_real_findings(integ))} integrity mismatch(es)")
    if hidden:
        bits.append(f"{len(hidden)} hidden region(s)")
    if n_fix:
        bits.append(f"{n_fix} structural fix(es) available")
    if alerts:
        bits.append(f"{alerts} forensic alert(s)")
    if not bits and not findings and (report is None or not vios):
        # "clean" is a claim about checks that ran. With no walker they did not,
        # and the honest answer is that this verb had nothing to say -- `locate`
        # still finds embedded containers in a format we cannot walk.
        bits.append("clean" if scanned else "not analyzable -- no walker; try acidcat locate")
    print(f"\n  VERDICT: {', '.join(bits) if bits else 'no structural fixes; review findings'}")
    return _code(scanned, vios, findings, integ)
