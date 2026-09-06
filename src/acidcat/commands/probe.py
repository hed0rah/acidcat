"""acidcat probe -- low-level byte dissection (the RE-tool surface).

Read a file as raw bytes the way a reverse engineer does, with the addresses
resolved through acidcat's format walker so you can name structure instead of
counting bytes.

    acidcat probe read AT [--type u32] [--count N] [--be|--le] FILE...
    acidcat probe scan VALUE [--type u32] FILE...
    acidcat probe find HEX FILE...
    acidcat probe strings [--min N] FILE...
    acidcat probe hexdump AT [--len N] FILE...
    acidcat probe diff OLD NEW

Operands come last and take any number of files, so a glob works and the
output is labelled per file when there is more than one -- the shape strings(1)
and file(1) have always had.

AT is a raw offset (0x2c / 44) OR a structural name: a chunk id (data), or a
chunk field (fmt.sample_rate). VALUE for scan is an integer (or a float for
f32/f64); it is searched in both byte orders. HEX for find is a hex string
(64617461) or, with a leading s:, literal text (s:data).
"""

import json
import os
import sys

from acidcat.commands._output import (add_output_format_arg,
                                      chosen_format)
from acidcat.util.stdin import display_name
from acidcat.util.color import add_color_arg, color_enabled, fg

from acidcat.core import probe as pr
from acidcat.core.forensics import viz
from acidcat.core.infra.mapped import map_file
from acidcat.tui_theme import BYTE_CLASS


def register(subparsers):
    p = subparsers.add_parser(
        "probe",
        help="Byte-level dissection: typed read, value scan, find, strings, hexdump, diff.")
    # The file operand belongs to each SUB-VERB, not to `probe` itself.
    #
    # It used to sit here, giving `acidcat probe FILE VERB ...` -- which reads
    # nicely and puts the thing you vary most (the operation) last, so shell
    # history editing is one word. It also cannot survive a glob: the shell
    # turns `probe *.wav strings` into `probe a.wav b.wav c.wav strings` before
    # acidcat sees it, and there is no reading of that which works. No
    # long-lived tool puts an operand between the command and its subcommand,
    # and this is why.
    #
    # `probe SUBVERB [OPTIONS] FILE...` is the openssl/git shape and the one
    # every shell idiom already expects.
    # probe is the RE surface and had no machine output at all: every subverb
    # printed its summary and its results together on stdout, so scripting
    # `probe find` meant `tail -n +2 | tr -d ' '`. Declared on the PARENT, so it
    # applies to whichever subverb follows -- which also means it has to be
    # written before the subverb, as argparse requires.
    #
    # table+json only: the subverbs return different shapes (typed values, hit
    # offsets, entropy windows, byte ranges) and there is no single column set a
    # csv could honestly claim to be.
    add_output_format_arg(p, only=("table", "json"))
    sub = p.add_subparsers(dest="verb", metavar="VERB")

    # The gap between acidcat-as-hex-viewer and acidcat-as-RE-workbench. You
    # could derive a container's offset table with `probe read` and then had no
    # way to act on it: --struct decodes one fixed record and cannot take a
    # count from a field it just read, so carving the regions meant leaving the
    # tool and computing offsets in Python. This emits `locate`-shaped records,
    # so the thing you just learned feeds `carve --batch -` unchanged.
    tb = sub.add_parser(
        "table", help="Walk an offset table into carve-ready regions.")
    tb.add_argument("at", help="Where the table starts (any --at expression).")
    tb.add_argument("--type", "-t", default="u32", choices=sorted(pr.FMT_STRUCT),
                    help="Entry type (default u32).")
    tb.add_argument("--count", "-n", type=int,
                    help="Number of entries, if you know it.")
    tb.add_argument("--count-at", metavar="EXPR",
                    help="Read the entry count from the file at EXPR instead.")
    tb.add_argument("--count-type", default="u32", choices=sorted(pr.FMT_STRUCT),
                    help="Type of the --count-at value (default u32).")
    tb.add_argument("--base", metavar="EXPR", default=None,
                    help="What the entries are relative to. An offset "
                         "expression, or 'after-table' for the byte just past "
                         "the table itself (the common layout). Default: the "
                         "entries are absolute file offsets.")
    tb.add_argument("--end", metavar="EXPR",
                    help="Where the last region ends (default: EOF).")
    tb.add_argument("--be", action="store_true", help="Force big-endian.")
    tb.add_argument("--le", action="store_true", help="Force little-endian.")
    tb.add_argument("files", nargs="+", metavar="FILE",
                     help="File(s) to dissect, or '-' for stdin.")

    r = sub.add_parser("read", help="Read AT as typed values (pwndbg x).")
    r.add_argument("at", help="Offset (0x.. / decimal) or name (chunk / chunk.field).")
    r.add_argument("--type", "-t", default="u32", choices=sorted(pr.FMT_STRUCT),
                   help="Value type (default u32).")
    r.add_argument("--count", "-n", type=int, default=1, help="How many values.")
    r.add_argument("--be", action="store_true", help="Force big-endian.")
    r.add_argument("--le", action="store_true", help="Force little-endian.")
    r.add_argument("files", nargs="+", metavar="FILE",
                     help="File(s) to dissect, or '-' for stdin.")

    s = sub.add_parser("scan", help="Find every offset holding VALUE (Cheat Engine).")
    s.add_argument("value", help="The value to find (int, or float for f32/f64).")
    s.add_argument("--type", "-t", default="u32", choices=sorted(pr.FMT_STRUCT),
                   help="How to encode VALUE (default u32).")
    s.add_argument("files", nargs="+", metavar="FILE",
                     help="File(s) to dissect, or '-' for stdin.")

    f = sub.add_parser("find", help="Find every offset of a byte pattern.")
    f.add_argument("pattern", help="Hex bytes (64617461) or s:text for literal ASCII.")
    f.add_argument("files", nargs="+", metavar="FILE",
                     help="File(s) to dissect, or '-' for stdin.")

    st = sub.add_parser("strings", help="Printable ASCII runs with offsets.")
    st.add_argument("--min", "-m", type=int, default=4, help="Minimum run length.")
    st.add_argument("files", nargs="+", metavar="FILE",
                     help="File(s) to dissect, or '-' for stdin.")

    h = sub.add_parser("hexdump", help="Annotated hexdump at AT.")
    h.add_argument("at", help="Offset or structural name.")
    h.add_argument("--len", "-l", dest="length", type=int, default=256,
                   help="Bytes to dump (default 256, or the chunk size for a name).")
    h.add_argument("files", nargs="+", metavar="FILE",
                     help="File(s) to dissect, or '-' for stdin.")

    d = sub.add_parser("diff", help="Changed byte ranges vs another file.")
    # two operands, like diff(1): `probe diff OLD NEW`. Not variadic --
    # "diff these five files" has no single meaning.
    d.add_argument("files", nargs=2, metavar="FILE",
                   help="The two files to compare.")

    en = sub.add_parser("entropy",
                        help="Shannon entropy curve + byte histogram (spot encrypted/compressed spans).")
    en.add_argument("--width", "-w", type=int, default=72, help="Plot width in cells.")
    en.add_argument("files", nargs="+", metavar="FILE",
                     help="File(s) to dissect, or '-' for stdin.")

    mp = sub.add_parser("map",
                        help="Hilbert byte-class map (binvis): the file's shape at a glance.")
    # no -o short form: -o is "output file" everywhere else in acidcat
    mp.add_argument("--order", type=int, default=5,
                    help="Grid is 2^order per side (default 5 = 32x32).")
    add_color_arg(mp, deprecated_no_color=True)
    mp.add_argument("files", nargs="+", metavar="FILE",
                     help="File(s) to dissect, or '-' for stdin.")

    lb = sub.add_parser("lsb",
                        help="Sample-LSB entropy of a PCM WAV (spot LSB stego).")
    lb.add_argument("--width", "-w", type=int, default=64, help="Plot width in cells.")
    lb.add_argument("files", nargs="+", metavar="FILE",
                     help="File(s) to dissect, or '-' for stdin.")

    p.set_defaults(func=run)






def _byteorder(args, label):
    if getattr(args, "be", False):
        return "big"
    if getattr(args, "le", False):
        return "little"
    return pr.default_byteorder(label)


def _emit(args, payload):
    """JSON to stdout for the machine path. Returns True if it handled output."""
    if chosen_format(args) != "json":
        return False
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return True


# House rule: say that a bound BIT, never that one exists. The note is on
# stderr so the records on stdout stay pipeable, and it is conditional, so a
# result that fits says nothing extra -- silence is the claim of completeness.
# Named so the cap ledger's enumerator can see them: it matches
# CAP/MAX/LIMIT/FINDINGS/CANDS, and a bare `_SHOWN = 512` was invisible to the
# very harness this release ships to catch unannounced bounds.
_SHOWN_CAP = 512
_STRINGS_CAP = 1000
_DIFF_SHOWN_CAP = 256


def _cap_note(total, shown, unit):
    if total <= shown:
        return
    print(f"acidcat probe: {total:,} {unit} found; listing the first "
          f"{shown:,} (the rest are not shown)", file=sys.stderr)



def run(args):
    """Dispatch each operand through the sub-verb, like strings(1) or file(1).

    `diff` is the exception: its two operands are one comparison, not two runs.
    """
    from acidcat.util.stdin import resolved_input

    files = list(getattr(args, "files", []) or [])
    if not getattr(args, "verb", None):
        print("acidcat probe: pick a verb "
              "(table/read/scan/find/strings/hexdump/diff/entropy/map)",
              file=sys.stderr)
        return 2

    if args.verb == "diff":
        args.file, args.other = files[0], files[1]
        with resolved_input(args.file) as _p:
            if _p is None:
                print("acidcat probe: no data on stdin", file=sys.stderr)
                return 1
            args.file = _p
            return _run(args)

    worst = 0
    many = len(files) > 1
    for i, target in enumerate(files):
        with resolved_input(target) as _p:
            if _p is None:
                print("acidcat probe: no data on stdin", file=sys.stderr)
                return 1
            args.file = _p
            # grep/file style: name the file only when there is more than one,
            # so single-file output stays pipeable exactly as it was
            if many:
                if i:
                    print()
                print(f"==> {display_name(target)} <==")
            worst = max(worst, _run(args) or 0)
    return worst


def _run(args):
    verb = getattr(args, "verb", None)
    if not verb:
        print("acidcat probe: pick a verb (read/scan/find/strings/hexdump/diff)",
              file=sys.stderr)
        return 2
    path = args.file
    try:
        # mmap, not f.read(): every verb is random access (slice, find,
        # unpack_from), which a mapped file serves with OS paging -- a large
        # or crafted input no longer costs its full size in RAM
        data, close = map_file(path)
    except OSError as e:
        print(f"acidcat probe: {path}: {e}", file=sys.stderr)
        return 2
    try:
        return _dispatch(args, verb, path, data)
    finally:
        close()


def _table_regions(args, path, data, order):
    """Offset table -> [{offset, length, kind}] in `locate` record shape.

    Entries are region starts; each region runs to the next entry, and the last
    to --end or EOF. Returns (records, meta) or raises ValueError with a reason
    the caller can print.
    """
    start, _ln, _note = pr.resolve(path, args.at)
    size = pr.FMT_STRUCT[args.type][1] if args.type != "u24" else 3

    count = args.count
    if args.count_at is not None:
        coff, _l, _n = pr.resolve(path, args.count_at)
        vals = pr.read_typed(data, coff, args.count_type, 1, order)
        if not vals:
            raise ValueError(f"could not read a count at {args.count_at}")
        count = int(vals[0])
    if count is None:
        raise ValueError("give --count N or --count-at EXPR")
    # a count read from the file is attacker-controlled by definition: it is the
    # value under investigation. Bound it by what the file can actually hold
    # rather than trusting it into a multi-GB allocation.
    room = max(0, (len(data) - start) // size)
    if count < 1:
        raise ValueError(f"entry count is {count}")
    declared = count                     # before clamping: what the FILE claimed
    truncated = count > room
    if truncated:
        count = room

    entries = pr.read_typed(data, start, args.type, count, order)
    if not entries:
        raise ValueError(f"no entries readable at 0x{start:x}")

    if args.base == "after-table":
        base = start + len(entries) * size
    elif args.base is not None:
        base, _l, _n = pr.resolve(path, args.base)
    else:
        base = 0
    if args.end is not None:
        end_at, _l, _n = pr.resolve(path, args.end)
    else:
        end_at = len(data)

    starts = [base + int(e) for e in entries]
    recs = []
    for i, off in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else end_at
        if off < 0 or off > len(data) or nxt <= off:
            continue                     # a lying entry drops out, not the run
        recs.append({"offset": off, "length": min(nxt, len(data)) - off,
                     "kind": "entry", "index": i})
    meta = {"table_at": start, "entries": len(entries), "base": base,
            "declared_count": declared,
            "truncated_to_file": truncated, "usable": len(recs)}
    return recs, meta


def _dispatch(args, verb, path, data):
    label, _chunks, _warns = pr._walk(path)

    if verb == "table":
        order = "big" if args.be else ("little" if args.le
                                       else _byteorder(args, label))
        try:
            recs, meta = _table_regions(args, path, data, order)
        except (KeyError, ValueError) as e:
            print(f"acidcat probe: {e}", file=sys.stderr)
            return 2
        if _emit(args, {"verb": "table", **meta, "regions": recs}):
            return 0 if recs else 1
        if meta["truncated_to_file"]:
            print(f"  count {meta['declared_count']} exceeds what the file "
                  f"holds; walked {meta['entries']}", file=sys.stderr)
        print(f"{meta['entries']} entr(ies) at 0x{meta['table_at']:08x}, "
              f"base 0x{meta['base']:08x} -> {len(recs)} region(s)",
              file=sys.stderr)
        for r in recs:
            print(f"  [{r['index']:>4}]  0x{r['offset']:08x}  {r['length']:>12,}")
        return 0 if recs else 1

    if verb == "read":
        try:
            off, _ln, note = pr.resolve(path, args.at)
        except (KeyError, ValueError) as e:
            print(f"acidcat probe: {e}", file=sys.stderr)
            return 2
        order = _byteorder(args, label)
        vals = pr.read_typed(data, off, args.type, args.count, order)
        if not vals:
            print(f"acidcat probe: nothing to read at 0x{off:x}", file=sys.stderr)
            return 1
        if _emit(args, {"verb": "read", "offset": off, "type": args.type,
                        "endian": order, "anchor": note,
                        "values": list(vals)}):
            return 0
        head = f"0x{off:08x}  {args.type} {order}-endian  ({note})"
        print(head)
        for i, v in enumerate(vals):
            print(f"  [{i}] {v}")
        return 0

    if verb == "scan":
        try:
            value = float(args.value) if args.type in ("f32", "f64") else pr.parse_int(args.value)
        except ValueError:
            print(f"acidcat probe: bad value {args.value!r}", file=sys.stderr)
            return 2
        hits, total_hits = pr.scan_value_counted(data, value, args.type, _SHOWN_CAP)
        if _emit(args, {"verb": "scan", "value": args.value, "type": args.type,
                        "hits": [{"offset": o, "endian": e} for o, e in hits]}):
            _cap_note(total_hits, len(hits), "hit(s)")
            return 0 if hits else 1
        print(f"{total_hits:,} hit(s) for {args.value} as {args.type}",
              file=sys.stderr)
        _cap_note(total_hits, len(hits), "hit(s)")
        for off, order in hits:
            print(f"  0x{off:08x}  ({order})")
        return 0 if hits else 1

    if verb == "find":
        pat = args.pattern
        if pat.startswith("s:"):
            needle = pat[2:].encode("latin-1")
        else:
            try:
                needle = bytes.fromhex(pat)
            except ValueError:
                print(f"acidcat probe: bad hex {pat!r} (use s: for text)", file=sys.stderr)
                return 2
        offs, total_offs = pr.find_bytes_counted(data, needle, _SHOWN_CAP)
        if _emit(args, {"verb": "find", "pattern": pat,
                        "length": len(needle),
                        "hits": [{"offset": o} for o in offs]}):
            _cap_note(total_offs, len(offs), "hit(s)")
            return 0 if offs else 1
        print(f"{total_offs:,} hit(s) for {pat}", file=sys.stderr)
        _cap_note(total_offs, len(offs), "hit(s)")
        for off in offs:
            print(f"  0x{off:08x}")
        return 0 if offs else 1

    if verb == "strings":
        # memoryview: byte-wise iteration must yield ints as bytes does
        # (iterating the mmap itself yields 1-byte bytes objects)
        with memoryview(data) as view:
            found, total_found = pr.strings_counted(view, args.min, _STRINGS_CAP)
        if _emit(args, {"verb": "strings", "min_length": args.min,
                        "strings": [{"offset": o, "text": t}
                                    for o, t in found]}):
            _cap_note(total_found, len(found), "string(s)")
            return 0 if found else 1
        _cap_note(total_found, len(found), "string(s)")
        for off, text in found:
            print(f"0x{off:08x}  {text}")
        return 0 if found else 1

    if verb == "hexdump":
        try:
            off, ln, _note = pr.resolve(path, args.at)
        except (KeyError, ValueError) as e:
            print(f"acidcat probe: {e}", file=sys.stderr)
            return 2
        length = args.length if args.length != 256 else (ln or 256)
        print(pr.hexdump(data, off, length))
        return 0

    if verb == "diff":
        try:
            other, oclose = map_file(args.other)
        except OSError as e:
            print(f"acidcat probe: {args.other}: {e}", file=sys.stderr)
            return 2
        try:
            ranges, total_ranges, la, lb = pr.diff_counted(data, other,
                                                            _DIFF_SHOWN_CAP)
        finally:
            oclose()
        if _emit(args, {"verb": "diff", "a": display_name(path),
                        "b": os.path.basename(args.other),
                        "a_length": la, "b_length": lb,
                        "identical": not ranges and la == lb,
                        "ranges": [{"offset": st, "end": en, "length": en - st}
                                   for st, en in ranges]}):
            _cap_note(total_ranges, len(ranges), "changed range(s)")
            return 0 if (not ranges and la == lb) else 1
        if not ranges and la == lb:
            print("identical")
            return 0
        print(f"{display_name(path)} ({la:,}) vs {os.path.basename(args.other)} "
              f"({lb:,}): {total_ranges:,} changed range(s)")
        _cap_note(total_ranges, len(ranges), "changed range(s)")
        for s, e in ranges:
            print(f"  0x{s:08x}..0x{e:08x}  ({e - s} bytes)")
        if la != lb:
            print(f"  lengths differ by {abs(la - lb):,} bytes")
        return 0

    if verb == "entropy":
        # memoryview: viz iterates bytes as ints, and view slices are
        # zero-copy windows into the map
        with memoryview(data) as view:
            ent = viz.windowed_entropy(view, max(8, args.width))
            if _emit(args, {"verb": "entropy", "file": display_name(path),
                            "size": len(data), "window": max(8, args.width),
                            "min": min(ent), "max": max(ent),
                            "mean": sum(ent) / len(ent),
                            "high_windows": sum(1 for e in ent if e >= 7.2),
                            "high_threshold": 7.2,
                            "windows": [round(e, 4) for e in ent]}):
                return 0
            print(f"entropy  {display_name(path)}  {len(data):,} bytes  (0 = uniform .. 8 = random)")
            for line in viz.braille_line(ent, width=args.width, height=8, vmin=0, vmax=8):
                print("  " + line)
            hi = sum(1 for e in ent if e >= 7.2)
            summary = (f"  min {min(ent):.2f}  max {max(ent):.2f}  "
                       f"mean {sum(ent) / len(ent):.2f} bits/byte")
            if hi:
                summary += f"   [{hi} window(s) >= 7.2: encrypted or compressed]"
            print(summary)
            print("  byte distribution:")
            for line in viz.byte_histogram(view, width=128, height=5):
                print("  " + line)
        return 0

    if verb == "map":
        # memoryview for the same int-iteration reason as entropy/strings
        with memoryview(data) as view:
            grid, side = viz.hilbert_grid(view, args.order)
        color = color_enabled(args)
        print(f"byte map  {display_name(path)}  {len(data):,} bytes  "
              f"({side}x{side} Hilbert; adjacent cells are adjacent bytes)")
        for row in grid:
            cells = []
            for b in row:
                glyph, cls = viz.byte_class(b)
                cells.append(fg(BYTE_CLASS[cls], "█") if color else glyph)
            print("  " + "".join(cells))
        print("  legend:  . null   o ascii   - control   + high   # 0xFF")
        return 0
    if verb == "lsb":
        from acidcat.core.forensics import lsb as lsbmod
        res = lsbmod.analyze(path, label, _chunks)
        if res is None:
            print(f"acidcat probe: {display_name(path)}: not a PCM WAV with an "
                  f"analyzable sample region", file=sys.stderr)
            return 2
        if _emit(args, {"verb": "lsb", "file": display_name(path), **res}):
            return 0
        w = res["windows"]
        signal = sum(1 for x in w if x > 0.1)
        print(f"lsb  {display_name(path)}  sample LSB entropy across {len(w)} "
              f"windows  (0 = flat .. 1 = random)")
        for line in viz.braille_line(w, width=args.width, height=6, vmin=0, vmax=1):
            print("  " + line)
        print(f"  min {res['min']:.3f}  max {res['max']:.3f}  mean {res['mean']:.3f}"
              f"   {signal}/{len(w)} window(s) carry signal")
        # A reading, never a verdict: a high LSB floor is consistent with an
        # encrypted payload AND with dithered or field-recorded audio, and
        # entropy alone cannot separate them (see forensics/lsb.py).
        if res["uniform_high"]:
            print("  uniformly high: consistent with an encrypted embedded "
                  "payload, but dithered and field-recorded audio look the same. "
                  "A heuristic, not proof.")
        elif res["mean"] >= 0.3:
            print("  a band of high-entropy LSBs over a quiet floor: consistent "
                  "with a raw payload written into the low bits.")
        else:
            print("  low and flat: no LSB-entropy signature of a hidden payload.")
        if res["capped"]:
            print("  (capped: only the leading PCM was read)")
        return 0

    return 2
