"""1.8 spellings as aliases of the 2.0 CLI (docs/contract/cli-2.0.md).

An old verb or flag marked *alias* keeps working through 2.x: it prints one
line to stderr naming the new spelling, then runs exactly the new form. That
is how this works, literally: `translate(argv)` turns the old command line into
the new one (or, for the few old spellings that were two things at once, the
new ones), and `acidcat.cli` runs those. An alias can therefore not print
anything its new form would not, which is what test_cli_mapping.py checks.

A flag already deprecated in 1.x (`-f`, `--no-color`, `carve --format`,
`formats --format-out`) is removed in 2.0: `translate` refuses it with the
spelling to use instead, and the command exits 2.

Old verbs are translated by parsing their command line with the old verb's own
parser (the old command modules are the implementations behind the new verbs
and keep their parsers), so abbreviations, `--flag=value` and repeated flags
all read exactly as 1.8 read them.
"""

import shlex

from acidcat.commands import _legacy


class Removed(Exception):
    """A spelling 2.0 removed; the message says what to write instead."""


# 1.x deprecations that 2.0 removes: flag -> (verbs it applies to or None for
# all, what to write instead)
_REMOVED = {
    "-f": (None, "--output-format FMT (or --json / --csv)"),
    "--no-color": (("probe",), "--color never"),
    "--format-out": (("formats",), "--output-format FMT"),
}


def _removed_check(verb, rest):
    for tok in rest:
        name = tok.split("=", 1)[0]
        if name.startswith("-f") and not name.startswith("--") and len(name) > 2:
            name = "-f"                          # -fjson
        spec = _REMOVED.get(name)
        if spec and (spec[0] is None or verb in spec[0]):
            raise Removed(f"{tok} was removed in 2.0; use {spec[1]}")
        if verb == "carve" and name == "--format":
            raise Removed("carve --format was removed in 2.0; use --encoding")


def _fmt(ns, default="table", allowed=None):
    fmt = getattr(ns, "output_format", None)
    if fmt is None or fmt == default:
        return []
    if allowed is not None and fmt not in allowed:
        return []
    return ["--output-format", fmt]


def _opt(argv, flag, value):
    return _legacy.flag(argv, flag, value)


def _sw(argv, flag, on):
    return _legacy.switch(argv, flag, on)


def _parse(module_name, verb, rest):
    from importlib import import_module
    module = import_module("acidcat.commands." + module_name)
    return _legacy.parser_for(module, verb).parse_args(rest)


# ── old verbs ──────────────────────────────────────────────────────────

def _info(rest):
    ns = _parse("info", "info", rest)
    base = ["inspect"] + ([] if ns.verbose else ["--summary"]) + list(ns.target)
    base += _fmt(ns, allowed=None if not ns.verbose else ("table", "json"))
    _sw(base, "-q", ns.quiet and not ns.verbose)
    _opt(base, "-o", ns.output)
    if not ns.deep:
        return [base]
    # `info --deep` was two things: the summary, and librosa's tempo and key
    return [base] + [["analyze", "--bpm-key", "--features", t] + _fmt(ns)
                     for t in ns.target]


def _chunks(rest):
    ns = _parse("chunks", "chunks", rest)
    new = ["inspect", "--chunks"] + list(ns.target) + _fmt(ns)
    _opt(new, "-o", ns.output)
    return [new]


def _dump(rest):
    ns = _parse("dump", "dump", rest)
    if ns.write:
        return [["carve", ns.target, cid, "-o", ns.write.rstrip("/\\") + "/"]
                + (["-q"] if ns.quiet else []) for cid in ns.chunks]
    rng = "+%d" % ns.bytes if "-b" in rest or any(
        r.startswith("--bytes") for r in rest) else ""
    new = ["od", ns.target] + [_addr_id(cid) + rng if rng else cid
                                for cid in ns.chunks]
    _sw(new, "--json", ns.output_format == "json")
    return [new]


def _addr_id(cid):
    """A chunk id in its ADDR spelling: pad spaces are '_' (`fmt ` -> `fmt_`),
    since a space before `+N` does not end the id."""
    bare = cid.rstrip(" ")
    return bare + "_" * (len(cid) - len(bare))


def _wrap(rest):
    ns = _parse("wrap", "wrap", rest)
    new = ["carve", ns.input, "--as-wav"]
    if ns.rate != 44100:
        new += ["--rate", str(ns.rate)]
    if ns.channels != 1:
        new += ["--channels", str(ns.channels)]
    if ns.bits != 16:
        new += ["--bits", str(ns.bits)]
    if ns.endian != "le":
        new += ["--byte-order", ns.endian]
    _sw(new, "--float", ns.floating)
    _opt(new, "-o", ns.output)
    return [new]


def _validate(rest):
    ns = _parse("validate", "validate", rest)
    new = ["check"] + list(ns.inputs) + _fmt(ns)
    _sw(new, "--deep", ns.deep)
    _sw(new, "--problems-only", ns.quiet)
    return [new]


def _repair(rest):
    ns = _parse("repair", "repair", rest)
    new = ["check", "--fix"] + list(ns.inputs) + _fmt(ns)
    _opt(new, "-o", ns.output)
    _sw(new, "--dry-run", ns.dry_run)
    _sw(new, "--overwrite", ns.overwrite)
    _sw(new, "--keep-pad", ns.keep_pad)
    return [new]


def _write(rest):
    ns = _parse("write", "write", rest)
    new = ["edit"] + list(ns.inputs) + _fmt(ns)
    for s in ns.sets:
        new += ["--set", s]
    _opt(new, "-o", ns.output)
    _sw(new, "--dry-run", ns.dry_run)
    _sw(new, "--overwrite", ns.overwrite)
    _sw(new, "--strip", ns.strip)
    return [new]


def _cover(rest):
    ns = _parse("cover", "cover", rest)
    if ns.set_image:
        new = ["edit", ns.file, "--set", "cover=@" + ns.set_image]
    elif ns.remove:
        new = ["edit", ns.file, "--unset", "cover"]
    else:
        new = ["edit", ns.file, "--get", "cover"]
        _opt(new, "-o", ns.output)
    _sw(new, "--overwrite", ns.overwrite)
    return [new]


def _scan(rest):
    ns = _parse("scan", "scan", rest)
    if ns.fallback or ns.features:
        # both were librosa passes riding on scan; they are analyze now
        return [["analyze"] + (["--bpm-key"] if ns.fallback else [])
                + (["--features"] if ns.features else []) + [ns.target]]
    new = ["stats", ns.target, "--by", "meta"]
    # scan's own default rendering was csv; stats' is table, so a 1.8 scan
    # that asked for nothing asked for csv
    new += ["--output-format", ns.output_format or "csv"]
    if ns.num != 500 or any(r in ("-n", "--num") or r.startswith("--num=")
                            for r in rest):
        new += ["--max-files", str(ns.num)]
    else:
        new += ["--max-files", "500"]
    _opt(new, "-o", ns.output)
    _sw(new, "-q", ns.quiet)
    _sw(new, "-v", ns.verbose)
    _opt(new, "--has", ns.has)
    return [new]


def _shape(rest):
    ns = _parse("shape", "shape", rest)
    new = ["stats"] + list(ns.targets) + ["--by", "shape",
                                          "--output-format", ns.output_format or "tsv"]
    _opt(new, "--only-format", ns.fmt_filter)
    for name in ("no_path", "coarse", "fast", "anomalies", "warn_only"):
        _sw(new, "--" + name.replace("_", "-"), getattr(ns, name))
    return [new]


def _detect(rest, which="--bpm-key", module="detect"):
    ns = _parse(module, module, rest)
    new = ["analyze", which, ns.target] + _fmt(ns)
    new += ["--max-files", str(ns.num)]
    _opt(new, "-o", ns.output)
    _sw(new, "-q", ns.quiet)
    return [new]


def _features(rest):
    ns = _parse("features", "features", rest)
    new = ["analyze", "--features", ns.target,
           "--output-format", ns.output_format or "csv",
           "--max-files", str(ns.num)]
    _opt(new, "-o", ns.output)
    _sw(new, "-q", ns.quiet)
    return [new]


def _index(rest):
    ns = _parse("index", "index", rest)
    reg = ["--registry", ns.registry] if ns.registry else []
    if ns.list_libs or ns.orphans:
        return [["lib", "list"] + (["--orphans"] if ns.orphans else []) + reg]
    if ns.stats_target:
        return [["lib", "stats", ns.stats_target] + reg]
    if ns.refresh_stats:
        return [["lib", "stats", "--refresh"]
                + ([ns.refresh_stats_target] if ns.refresh_stats_target else []) + reg]
    if ns.forget:
        return [["lib", "forget", ns.forget] + reg]
    if ns.remove:
        return [["lib", "forget", ns.remove, "--delete-db"] + reg]
    new = ["lib", "index"] + ([ns.target] if ns.target else [])
    _opt(new, "--label", ns.label)
    _sw(new, "--in-tree", ns.in_tree)
    _sw(new, "--rebuild", ns.rebuild)
    _sw(new, "--reread", ns.force)
    _sw(new, "--features", ns.features)
    _opt(new, "--jobs", ns.jobs)
    _sw(new, "--analyze", ns.deep)
    _opt(new, "--import-tags", ns.import_tags)
    if ns.discover_root:
        new += ["--discover", ns.discover_root]
        if ns.min_samples != 20:
            new += ["--min-samples", str(ns.min_samples)]
        if ns.max_depth != 3:
            new += ["--max-depth", str(ns.max_depth)]
        if ns.label_prefix:
            new += ["--label-prefix", ns.label_prefix]
        _sw(new, "--dry-run", ns.dry_run)
    _sw(new, "-q", ns.quiet)
    _sw(new, "-v", ns.verbose)
    return [new + reg]


def _query(rest):
    ns = _parse("query", "query", rest)
    new = ["lib", "query"] + _fmt(ns)
    for name in ("registry", "bpm", "key", "duration", "device", "category",
                 "creator", "product", "text", "root", "compatible_with", "kind"):
        _opt(new, "--" + name.replace("_", "-"), getattr(ns, name))
    for t in ns.tag:
        new += ["--tag", t]
    _opt(new, "--only-format", ns.file_format)
    if ns.bpm_tolerance != 6.0:
        new += ["--bpm-tolerance", str(ns.bpm_tolerance)]
    for name in ("same_key", "no_half_double", "paths_only", "verbose"):
        _sw(new, "--" + name.replace("_", "-"), getattr(ns, name))
    if ns.limit != 50:
        new += ["--top", str(ns.limit)]
    _opt(new, "-o", ns.output)
    return [new]


def _similar(rest):
    ns = _parse("similar", "similar", rest)
    new = ["lib", "similar", ns.target] + _fmt(ns)
    if ns.num != 5:
        new += ["--top", str(ns.num)]
    _opt(new, "--kind", ns.kind)
    _sw(new, "--no-kind-filter", not ns.kind_filter)
    _sw(new, "--paths-only", ns.paths_only)
    _opt(new, "--registry", ns.registry)
    _opt(new, "-o", ns.output)
    return [new]


def _chunk_census(ns, cap, examples):
    """`stats --by chunks` for survey and census: their cap becomes
    --max-files (absent, the 10,000 default applies) and the rest passes."""
    targets = ns.target if isinstance(ns.target, list) else [ns.target]
    new = ["stats"] + list(targets) + ["--by", "chunks"] + _fmt(ns)
    _opt(new, "--max-files", cap)
    _opt(new, "--has", ns.has)
    _opt(new, "--examples", examples)
    _opt(new, "-o", ns.output)
    _sw(new, "-q", ns.quiet)
    return new


def _survey(rest):
    ns = _parse("survey", "survey", rest)
    given = any(r in ("-n", "--num") or r.startswith(("--num=", "-n"))
                for r in rest)
    return [_chunk_census(ns, ns.num if given else None,
                          ns.examples if ns.examples != 1 else None)]


def _census(rest):
    ns = _parse("census", "census", rest)
    from acidcat.core.census import CHUNK_EXAMPLES
    new = _chunk_census(ns, ns.limit,
                        ns.examples if ns.examples != CHUNK_EXAMPLES else None)
    if ns.top != 60:
        new += ["--top", str(ns.top)]
    if ns.jobs != "auto":
        new += ["--jobs", str(ns.jobs)]
    if ns.io_hint != "auto":
        new += ["--io-hint", ns.io_hint]
    for name in ("follow_symlinks", "one_file_system", "noatime", "no_fadvise"):
        _sw(new, "--" + name.replace("_", "-"), getattr(ns, name))
    return [new]


_VERBS = {
    "survey": _survey, "census": _census,
    "info": _info, "chunks": _chunks, "dump": _dump, "wrap": _wrap,
    "validate": _validate, "repair": _repair, "write": _write, "cover": _cover,
    "scan": _scan, "shape": _shape, "detect": _detect, "features": _features,
    "index": _index, "query": _query, "similar": _similar,
}

# ── old flags on verbs that keep their name ────────────────────────────

def _range_flags(rest, flags=("--offset", "--length", "--end")):
    """Pull --offset/--length/--end (either spelling) out of `rest`; returns
    (remaining tokens, {flag: value})."""
    out, got, i = [], {}, 0
    while i < len(rest):
        tok = rest[i]
        name, eq, val = tok.partition("=")
        if name in flags:
            if not eq:
                i += 1
                val = rest[i] if i < len(rest) else ""
            got[name] = val
        else:
            out.append(tok)
        i += 1
    return out, got


def _fold_at(rest, got):
    """1.8 let --at stand in for --offset beside --length/--end. A numeric
    --at folds into the range; a search anchor runs to the end of the file in
    2.0, so a length on one has no spelling and is refused rather than read
    from offset 0."""
    if "--offset" in got or not ("--length" in got or "--end" in got):
        return rest, got
    rest, at = _range_flags(rest, ("--at",))
    if "--at" not in at:
        return rest, got
    val = at["--at"]
    try:
        if int(val, 0) < 0:
            raise ValueError
    except ValueError:
        raise Removed(f"--length/--end beside --at {val} was removed in 2.0 (an "
                      "anchor runs to the end of the file); use an ADDR: a "
                      "node with a range (data+8) or @OFF+LEN")
    return rest, dict(got, **{"--offset": val})


def _after_file(verb, rest, extra):
    """`rest` with `extra` placed right after FILE: the first token that is
    neither an option nor an option's value. Not after rest[0], which may be an
    option ('od --color never FILE'), and not at the end: argparse before 3.13
    takes FILE and an `ADDR...` positional in one go, so an ADDR after an
    option is an unrecognised argument there (3.13 accepts it; CI runs 3.11)."""
    from importlib import import_module
    parser = _legacy.parser_for(import_module("acidcat.commands." + verb), verb)
    takes = {o for a in parser._actions if a.nargs != 0 for o in a.option_strings}
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("-") and tok != "-":
            i += 2 if ("=" not in tok and tok in takes) else 1
            continue
        return rest[:i + 1] + extra + rest[i + 1:]
    return rest + extra


def _as_addr(got):
    """The ADDR (or, for an offset alone, the anchor) the 1.8 range flags
    said: --offset N --length L -> @N+L, --offset N --end E -> @N..E, a bare
    --offset N -> the anchor N, which runs to the end of the file."""
    off = got.get("--offset", "0")
    if "--end" in got:
        return "@%s..%s" % (off, got["--end"])
    if "--length" in got:
        return "@%s+%s" % (off, got["--length"])
    return off


def _rename(rest, renames):
    out = []
    for tok in rest:
        name, eq, val = tok.partition("=")
        if name in renames:
            new = renames[name]
            if new is None:
                continue
            if isinstance(new, tuple):
                out.extend(new)
                continue
            out.append(new + (eq + val if eq else ""))
        else:
            out.append(tok)
    return out


def _inspect(rest):
    rest, got = _fold_at(*_range_flags(rest))
    rest = _rename(rest, {"--pretty": "--tags", "-v": "--deep", "--verbose": "--deep",
                          "--full": "--json", "--format": "--force-format",
                          "--force": "--try-all"})
    if got:
        rest += ["--at", _as_addr(got)]
    return ["inspect"] + rest


def _od(rest):
    rest, got = _fold_at(*_range_flags(rest))
    if not got:
        return ["od"] + rest
    addr = _as_addr(got)
    if addr.startswith("@"):
        return ["od"] + _after_file("od", rest, [addr])
    return ["od"] + rest + ["--at", addr]


def _carve(rest):
    rest, got = _fold_at(*_range_flags(rest))
    extra = []
    out, i = [], 0
    while i < len(rest):
        tok = rest[i]
        name, eq, val = tok.partition("=")
        if name in ("--chunk", "--field"):
            if not eq:
                i += 1
                val = rest[i] if i < len(rest) else ""
            extra.append(val if name == "--chunk" else "**#" + val)
        elif name == "--endian":
            out.append("--byte-order" + (eq + val if eq else ""))
        else:
            out.append(tok)
        i += 1
    if got:
        addr = _as_addr(got)
        if addr.startswith("@"):
            extra.append(addr)
        else:
            out += ["--at", addr]
    return ["carve"] + _after_file("carve", out, extra)


def _probe(rest):
    rest = _rename(rest, {"--be": ("--byte-order", "be"),
                          "--le": ("--byte-order", "le")})
    if "hexdump" not in rest:
        return ["probe"] + rest
    # probe hexdump AT [--len N] FILE... -> od FILE @AT+N, per file. Only an
    # offset takes the @: a chunk name (`fmt`) is an ADDR as it stands, and
    # 1.8's `chunk.field` is `chunk#field`; `@fmt+256` was an od error.
    i = rest.index("hexdump")
    ns = _hexdump_parser().parse_args(rest[i + 1:])
    return [["od", f, _hexdump_addr(ns.at, ns.length)] for f in ns.files]


def _hexdump_addr(at, length):
    try:
        int(at, 0)
    except ValueError:
        if "#" not in at and "." in at:
            node, _, key = at.rpartition(".")
            return f"{node}#{key}"
        return at
    return "@%s+%d" % (at, length)


def _hexdump_parser():
    import argparse
    p = argparse.ArgumentParser(prog="acidcat probe hexdump")
    p.add_argument("at")
    p.add_argument("--len", "-l", dest="length", type=int, default=256)
    p.add_argument("files", nargs="+")
    return p


_FLAGS = {"inspect": _inspect, "od": _od, "carve": _carve, "probe": _probe}


# ── entry ──────────────────────────────────────────────────────────────

def translate(argv):
    """(list of new argvs, the alias line to print or None) for a command
    line. A command line that is already 2.0 comes back unchanged with no
    line. Raises Removed for a spelling 2.0 removed."""
    argv = list(argv)
    verb_at = next((i for i, a in enumerate(argv) if not a.startswith("-")), None)
    if verb_at is None:
        return [argv], None
    head, verb, rest = argv[:verb_at], argv[verb_at], argv[verb_at + 1:]
    _removed_check(verb, rest)
    if verb in _VERBS:
        news = [head + n for n in _VERBS[verb](rest)]
    elif verb in _FLAGS:
        out = _FLAGS[verb](rest)
        news = [head + n for n in out] if out and isinstance(out[0], list) \
            else [head + out]
    else:
        return [argv], None
    if news == [argv]:
        return news, None
    old = "acidcat " + " ".join(shlex.quote(a) for a in argv)
    new = " ; ".join("acidcat " + " ".join(shlex.quote(a) for a in n) for n in news)
    return news, f"acidcat: `{old}` is `{new}` in 2.0"
