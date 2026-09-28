"""
acidcat similar -- find samples that sound like a reference, over the index.

The index-backed twin of the MCP `find_similar` tool: both call
core.search.find_similar, so the fan-out + z-standardized-cosine scoring lives
once in core. Reads the reference's feature vector from the index (indexed with
`acidcat index --features`), or extracts it live with librosa if the reference
is not indexed. Fans out across every registered library.
"""

import os
import sys

from acidcat.util.argtypes import nonneg_int
from acidcat.core.catalogue import paths as acidpaths
from acidcat.commands._output import add_output_format_arg
from acidcat.core.catalogue import registry as reg
from acidcat.core.catalogue import search
from acidcat.core.infra.render import output

_FIELDS = ["path", "similarity", "percentile_rank", "bpm", "key", "duration",
           "format", "library_label"]


def register(subparsers):
    p = subparsers.add_parser(
        "similar",
        help="Find samples similar to a reference file (over the index).")
    p.add_argument("target", help="Reference audio file.")
    p.add_argument("-n", "--num", type=nonneg_int, default=5, dest="num",
                   help="Number of results (default 5).")
    p.add_argument("--kind", choices=["loop", "one_shot", "any"],
                   help="Filter candidates by kind (default: the target's own "
                        "inferred kind).")
    p.add_argument("--no-kind-filter", dest="kind_filter", action="store_false",
                   help="Do not filter candidates by kind.")
    p.add_argument("--registry",
                   help="Override registry DB path (default ~/.acidcat/registry.db).")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
    p.add_argument("-o", "--output", help="Write output to file.")
    p.add_argument("--paths-only", action="store_true",
                   help="Print bare paths, one per line.")
    p.set_defaults(func=run, kind_filter=True)


def run(args):
    target = args.target
    if not os.path.exists(target):
        print(f"acidcat lib: file not found: {target}", file=sys.stderr)
        return 2          # could not read the input, as everywhere else

    rconn = reg.open_registry(getattr(args, "registry", None))
    try:
        libs = reg.list_libraries(rconn, only_existing=True)
    finally:
        rconn.close()
    if not libs:
        print("acidcat lib: no libraries registered. Run "
              "`acidcat lib index DIR --features` first.", file=sys.stderr)
        return 2          # nothing to search: the query could not run

    # reference features: from the index, else a live librosa extract
    target_feats, target_meta = search.resolve_target_features(target, libs)
    if target_feats is None:
        from acidcat.util.deps import require
        if not require("librosa", "numpy", group="analysis"):
            return 1
        from acidcat.core.analysis.features import extract_audio_features
        target_feats = extract_audio_features(target)
        if target_feats is None:
            print(f"acidcat lib: could not extract features from {target}",
                  file=sys.stderr)
            return 1
        target_meta = {"duration": target_feats.get("duration_sec"),
                       "acid_beats": None}

    try:
        result = search.find_similar(
            libs, target_feats, target_meta, n=args.num, kind=args.kind,
            kind_filter=args.kind_filter,
            exclude_path=acidpaths.normalize(target))
    except ValueError as e:
        print(f"acidcat lib: {e}", file=sys.stderr)
        return 1

    rows = result["results"]
    if not rows:
        if result["population"] == 0:
            print("acidcat lib: no indexed features to compare against. "
                  "Run `acidcat lib index DIR --features`.", file=sys.stderr)
        else:
            print("(no similar samples found)", file=sys.stderr)
        return 0

    if args.paths_only:
        for r in rows:
            print(r["path"])
        return 0

    shaped = [{k: r[k] for k in _FIELDS if r.get(k) is not None} for r in rows]
    stream = sys.stdout
    if getattr(args, "output", None):
        stream = open(args.output, "w", encoding="utf-8", newline="")
    try:
        output(shaped, fmt=args.output_format, stream=stream)
    finally:
        if stream is not sys.stdout:
            stream.close()
    return 0
