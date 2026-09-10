"""The `acidcat-lab` entry point.

Deliberately a separate binary rather than a subcommand of `acidcat`. Someone
reading `acidcat --help` on a machine where the extra is not installed should
see no trace of construction tooling, and a subcommand that appears and
disappears depending on an extra is a worse interface than two names.

Every verb reads a real file and writes one that did not exist before: a cavity
planted in a spec-ignorable region, a polyglot valid as two formats at once, a
payload hidden in sample-data LSBs. Which format a carrier is gets SNIFFED
through acidcat, not matched against a table kept here, so the lab consumes the
engine it exists to test. `--into` forces a writer when the carrier deliberately
lies about what it is, which is the whole point of some of these files.
"""

import argparse
import os
import sys

from acidcat import sniff
from acidcat.util import outpath
from acidcat_lab import __version__, cavity, polyglot, stego


# sniff id -> the cavity writer module for that carrier
_CAVITY = {"wav": cavity.junk, "rf64": cavity.junk, "flac": cavity.flac,
           "mp3": cavity.id3, "mp4": cavity.mp4}


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _payload_bytes(args):
    """The payload to plant: a file, or inline text, or stdin if neither."""
    if args.payload:
        return _read(args.payload)
    if args.payload_text is not None:
        return args.payload_text.encode("utf-8")
    return sys.stdin.buffer.read()


def _write(data, out, source, *, force, verb):
    """Write `data` to `out`, refusing to clobber the source or an unnamed file.

    Reuses acidcat's own guards so a lab command destroys a bystanding file no
    more easily than a base command does.
    """
    err = outpath.refuse_self_overwrite(verb, source, out)
    if err:
        print(err, file=sys.stderr)
        return 2
    err = outpath.refuse_clobber(verb, out, force=force)
    if err:
        print(err, file=sys.stderr)
        return 2
    with open(out, "wb") as fh:
        fh.write(data)
    return 0


def _resolve_cavity(path, into):
    """(format id, writer module) for a carrier, sniffed unless `--into` forces it."""
    fid = into or (sniff(path) or "")
    return fid, _CAVITY.get(fid)


def _default_out(path, infix, ext=None):
    stem, e = os.path.splitext(path)
    return f"{stem}.{infix}{ext or e}"


# ── cavity ──────────────────────────────────────────────────────────

def _cavity_embed(args):
    data = _read(args.input)
    fid, mod = _resolve_cavity(args.input, args.into)
    if mod is None:
        print(f"acidcat-lab cavity: {args.input}: {fid or 'unrecognized'} has no "
              f"cavity writer (wav, flac, mp3, mp4)", file=sys.stderr)
        return 2
    payload = _payload_bytes(args)
    try:
        if mod is cavity.flac:
            out_bytes = (mod.embed_pad(data, payload) if args.block == "padding"
                         else mod.embed_app(data, payload))
        else:
            out_bytes = mod.embed(data, payload)
    except ValueError as e:
        print(f"acidcat-lab cavity: {args.input}: {e}", file=sys.stderr)
        return 1
    out = args.output or _default_out(args.input, "cavity")
    rc = _write(out_bytes, out, args.input, force=args.force, verb="cavity")
    if rc == 0:
        print(f"planted {len(payload):,} B in a {fid} cavity -> {out}")
    return rc


def _cavity_extract(args):
    data = _read(args.input)
    fid, mod = _resolve_cavity(args.input, args.into)
    if mod is None:
        print(f"acidcat-lab cavity: {args.input}: {fid or 'unrecognized'} has no "
              f"cavity reader", file=sys.stderr)
        return 2
    payload = mod.extract(data)
    if not payload:
        print(f"acidcat-lab cavity: {args.input}: no acidcat-lab cavity found",
              file=sys.stderr)
        return 1
    if args.output:
        rc = _write(payload, args.output, args.input, force=args.force, verb="cavity")
        if rc == 0:
            print(f"recovered {len(payload):,} B -> {args.output}")
        return rc
    sys.stdout.buffer.write(payload)
    return 0


def _cavity_analyze(args):
    data = _read(args.input)
    fid, mod = _resolve_cavity(args.input, args.into)
    if mod is None:
        print(f"acidcat-lab cavity: {args.input}: {fid or 'unrecognized'} has no "
              f"cavity reader", file=sys.stderr)
        return 2
    for line in mod.analyze(data):
        print(line)
    return 0


# ── polyglot ────────────────────────────────────────────────────────

def _polyglot_build(args):
    wav = _read(args.input)
    members = {}
    for spec in args.add or []:
        if "=" not in spec:
            print(f"acidcat-lab polyglot: --add wants NAME=FILE, got {spec!r}",
                  file=sys.stderr)
            return 2
        name, _, src = spec.partition("=")
        members[name] = _read(src)
    if not members:
        print("acidcat-lab polyglot: nothing to add (use --add NAME=FILE)",
              file=sys.stderr)
        return 2
    out_bytes = polyglot.build_wav_zip(wav, members)
    out = args.output or _default_out(args.input, "polyglot")
    rc = _write(out_bytes, out, args.input, force=args.force, verb="polyglot")
    if rc:
        return rc
    ok_wav, wd, ok_zip, zd = polyglot.verify(out_bytes)
    print(f"built {out} ({len(out_bytes):,} B)")
    print(f"  as WAV  {'OK  ' if ok_wav else 'FAIL'}  {wd}")
    print(f"  as ZIP  {'OK  ' if ok_zip else 'FAIL'}  {zd}")
    return 0 if ok_wav and ok_zip else 1


def _polyglot_verify(args):
    ok_wav, wd, ok_zip, zd = polyglot.verify(_read(args.input))
    print(f"{args.input} ({os.path.getsize(args.input):,} B)")
    print(f"  as WAV  {'OK  ' if ok_wav else 'FAIL'}  {wd}")
    print(f"  as ZIP  {'OK  ' if ok_zip else 'FAIL'}  {zd}")
    return 0 if ok_wav and ok_zip else 1


# ── stego ───────────────────────────────────────────────────────────

def _stego_embed(args):
    wav = _read(args.input)
    payload = _payload_bytes(args)
    try:
        out_bytes = stego.embed(wav, payload, key=args.key, raw=args.raw,
                                method=args.method)
    except ValueError as e:
        print(f"acidcat-lab stego: {args.input}: {e}", file=sys.stderr)
        return 1
    out = args.output or _default_out(args.input, "stego")
    rc = _write(out_bytes, out, args.input, force=args.force, verb="stego")
    if rc == 0:
        how = {"replace": "sample LSBs", "match": "sample LSBs (matching)",
               "adaptive": "the noisy sample LSBs"}[args.method]
        print(f"hid {len(payload):,} B in {how} -> {out}"
              + ("" if args.raw else " (whitened)"))
    return rc


def _stego_extract(args):
    wav = _read(args.input)
    try:
        payload = stego.extract(wav, key=args.key, raw=args.raw, method=args.method)
    except ValueError as e:
        print(f"acidcat-lab stego: {args.input}: {e}", file=sys.stderr)
        return 1
    if args.output:
        rc = _write(payload, args.output, args.input, force=args.force, verb="stego")
        if rc == 0:
            print(f"recovered {len(payload):,} B -> {args.output}")
        return rc
    sys.stdout.buffer.write(payload)
    return 0


def _stego_capacity(args):
    wav = _read(args.input)
    if args.method == "adaptive":
        try:
            cap = stego.adaptive_capacity(wav)
        except ValueError as e:
            print(f"acidcat-lab stego: {args.input}: {e}", file=sys.stderr)
            return 1
        print(f"{cap:,} bytes  (adaptive: noisy blocks only)")
    else:
        print(f"{stego.capacity(wav):,} bytes")
    return 0


# ── parser ──────────────────────────────────────────────────────────

def _add_payload_args(p):
    p.add_argument("--payload", help="file whose bytes are the payload")
    p.add_argument("--payload-text", help="inline text payload (utf-8)")


def _add_write_args(p):
    p.add_argument("-o", "--output", help="output path (default: beside the input)")
    p.add_argument("--force", action="store_true",
                   help="allow writing over an existing file at a derived path")


def build_parser():
    ap = argparse.ArgumentParser(
        prog="acidcat-lab",
        description="Construct files that test what acidcat can see.")
    ap.add_argument("--version", action="version",
                    version="acidcat-lab %s" % __version__)
    verbs = ap.add_subparsers(dest="verb", metavar="VERB")

    # cavity: plant a payload in a spec-ignorable region
    cav = verbs.add_parser("cavity", help="plant/read a payload in an ignorable region")
    cav_s = cav.add_subparsers(dest="action", metavar="ACTION")
    ce = cav_s.add_parser("embed", help="plant a payload; carrier sniffed unless --into")
    ce.add_argument("input")
    _add_payload_args(ce)
    _add_write_args(ce)
    ce.add_argument("--into", choices=sorted(set(_CAVITY)),
                    help="force the carrier format instead of sniffing it")
    ce.add_argument("--block", choices=("application", "padding"),
                    default="application",
                    help="flac only: which metadata block carries it")
    ce.set_defaults(func=_cavity_embed)
    cx = cav_s.add_parser("extract", help="recover a planted payload to -o or stdout")
    cx.add_argument("input")
    cx.add_argument("-o", "--output")
    cx.add_argument("--force", action="store_true")
    cx.add_argument("--into", choices=sorted(set(_CAVITY)))
    cx.set_defaults(func=_cavity_extract)
    ca = cav_s.add_parser("analyze", help="report the carrier's ignorable regions")
    ca.add_argument("input")
    ca.add_argument("--into", choices=sorted(set(_CAVITY)))
    ca.set_defaults(func=_cavity_analyze)

    # polyglot: one file valid as two formats
    pg = verbs.add_parser("polyglot", help="build/verify a WAV that is also a ZIP")
    pg_s = pg.add_subparsers(dest="action", metavar="ACTION")
    pb = pg_s.add_parser("build", help="append a ZIP to a WAV, then verify both reads")
    pb.add_argument("input", help="the WAV carrier")
    pb.add_argument("--add", action="append", metavar="NAME=FILE",
                    help="an archive member; repeatable")
    _add_write_args(pb)
    pb.set_defaults(func=_polyglot_build)
    pv = pg_s.add_parser("verify", help="confirm a file parses as both WAV and ZIP")
    pv.add_argument("input")
    pv.set_defaults(func=_polyglot_verify)

    # stego: hide a payload in sample-data LSBs
    st = verbs.add_parser("stego", help="hide/recover a payload in sample LSBs")
    st_s = st.add_subparsers(dest="action", metavar="ACTION")
    se = st_s.add_parser("embed", help="hide a payload in the LSBs of a WAV")
    se.add_argument("input")
    _add_payload_args(se)
    _add_write_args(se)
    se.add_argument("--key", type=int, default=1337, help="whitening key (default 1337)")
    se.add_argument("--raw", action="store_true",
                    help="do not whiten (leaves a detectable LSB anomaly)")
    se.add_argument("--method", choices=("replace", "match", "adaptive"),
                    default="replace",
                    help="replace: overwrite the low bit (any depth). "
                         "match: +/-1 to set it (16-bit, defeats value-histogram "
                         "attacks). adaptive: only in already-noisy blocks "
                         "(16-bit, keeps the LSB-entropy profile unchanged)")
    se.set_defaults(func=_stego_embed)
    sx = st_s.add_parser("extract", help="recover a hidden payload to -o or stdout")
    sx.add_argument("input")
    sx.add_argument("-o", "--output")
    sx.add_argument("--force", action="store_true")
    sx.add_argument("--key", type=int, default=1337)
    sx.add_argument("--raw", action="store_true")
    sx.add_argument("--method", choices=("replace", "match", "adaptive"),
                    default="replace",
                    help="must match how it was embedded (replace and match read "
                         "the same way; adaptive differs)")
    sx.set_defaults(func=_stego_extract)
    sc = st_s.add_parser("capacity", help="how many payload bytes a WAV can hold")
    sc.add_argument("input")
    sc.add_argument("--method", choices=("replace", "match", "adaptive"),
                    default="replace",
                    help="adaptive reports the noisy-block capacity only")
    sc.set_defaults(func=_stego_capacity)

    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f"acidcat-lab: {e.filename}: no such file", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
