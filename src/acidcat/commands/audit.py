"""acidcat audit -- a forensic verdict on one file: structure, forensics, provenance.

Where `validate` answers "are the derived fields consistent" and `inspect` shows
the raw structure, `audit` composes three read-only views into one report:

  STRUCTURE   the constraint model's violations (what `repair` would fix)
  FORENSICS   the anomaly detector's findings (polyglots, cavities, trailing
              data, high-entropy regions, duplicate/oversized chunks)
  PROVENANCE  the writer/tool tells the file carries (encoder, software, muxer)

It is the "does the stored structure match reality, and who wrote it" question,
answered by reusing the same analyses the other verbs use. Writes nothing.

    acidcat audit FILE
    acidcat audit FILE --json          # machine-readable
"""

import json
import os
import sys

from acidcat.core import anomalies, constraints, provenance
from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported


def register(subparsers):
    p = subparsers.add_parser(
        "audit", help="Forensic verdict: structure + anomalies + provenance (read-only).")
    p.add_argument("input", help="File to audit.")
    p.add_argument("--json", action="store_true", help="Emit a machine-readable report.")
    p.set_defaults(func=run)


def _gather(path):
    with open(path, "rb") as f:
        data = f.read()
    report = constraints.analyze(data)              # structural violations (or None)
    findings = []
    label = None
    prov = []
    try:
        label, chunks, warns = walk_file(path)
        findings = anomalies.scan(path, label, chunks, warns)
        prov = provenance.identify(label, chunks, data)
    except Unsupported:
        pass
    if report is not None and label is None:
        label = report.label
    return label, report, findings, prov


def run(args):
    path = args.input
    try:
        label, report, findings, prov = _gather(path)
    except OSError as e:
        print(f"acidcat audit: {path}: {e}", file=sys.stderr)
        return 1
    size = os.path.getsize(path)

    if args.json:
        out = {
            "file": os.path.basename(path), "format": label, "size": size,
            "structure": [{"kind": v.kind, "path": v.path, "field": v.field,
                           "stored": v.stored, "computed": v.computed,
                           "witness": v.witness, "repairable": v.repairable}
                          for v in (report.violations if report else [])],
            "forensics": findings,
            "provenance": prov,
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

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

    if not findings:
        print("  FORENSICS   nothing flagged")
    else:
        print(f"  FORENSICS   {len(findings)} finding(s)")
        for f in sorted(findings, key=lambda x: -anomalies._SEVERITY.get(x["severity"], 0)):
            at = f" @ 0x{f['offset']:08x}" if f.get("offset") else ""
            print(f"                {f['severity']:<6} {f['message']}{at}")

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
    if n_fix:
        bits.append(f"{n_fix} structural fix(es) available")
    if alerts:
        bits.append(f"{alerts} forensic alert(s)")
    if not bits and not findings and (report is None or not vios):
        bits.append("clean")
    print(f"\n  VERDICT: {', '.join(bits) if bits else 'no structural fixes; review findings'}")
    return 0
