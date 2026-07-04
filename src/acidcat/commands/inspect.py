"""
acidcat inspect -- readelf-style structural dump for audio files.

Walks the container chunk by chunk and prints a structural table, a
decoded field breakdown per known chunk (with byte offsets), and any
spec violations it noticed along the way. `--hex` adds the raw bytes
next to each decoded field. `--frames` adds a per-element deep dump
(every MPEG frame for MP3, every event for MIDI). `--color` syntax-
highlights the table (auto/always/never, respects NO_COLOR). `-f json`
emits the same structure for machines.

Supports WAV/RIFF, RF64, AIFF/AIFC, Standard MIDI Files, Xfer Serum
presets, MP3 (ID3v2 + MPEG frames + Xing/LAME), and FLAC.

The format walkers live in acidcat/core/walk (dispatched through its
registry); this module is the CLI shell: argument parsing, chunk
selection, and rendering.
"""

import json
import os
import sys

from acidcat.core import anomalies as anomaliesmod
from acidcat.core import lsb as lsbmod
from acidcat.core.walk import Unsupported, walk_file

# --full emits raw region bytes for chunks that have decoded fields; cap the
# hex so a huge header (embedded art) cannot bloat the dump without bound.
_FULL_RAW_CAP = 8192


def register(subparsers):
    p = subparsers.add_parser(
        "inspect",
        help="readelf-style structural dump of a WAV, AIFF, MIDI, MP3, or FLAC file.",
    )
    p.add_argument("targets", nargs="+", metavar="target",
                   help="One or more WAV, RF64, AIFF, MIDI, Serum, MP3, or FLAC "
                        "files. With more than one, each is printed under a "
                        "'File:' banner; JSON output becomes NDJSON (one record "
                        "per line).")
    p.add_argument("--hex", action="store_true", dest="show_hex",
                   help="Show raw bytes next to each decoded field.")
    p.add_argument("-f", "--format", default="table", choices=["table", "json"],
                   help="Output format (default: table).")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Chunk table only, no per-chunk field detail.")
    p.add_argument("--pretty", action="store_true",
                   help="Human-friendly view of the decoded tags and metadata "
                        "(no byte offsets), ideal for presets and tagged files.")
    p.add_argument("-F", "--frames", action="store_true",
                   help="Per-element deep dump: every MPEG frame (MP3) or "
                        "MIDI event. No effect on formats without per-element "
                        "structure (WAV, AIFF, FLAC).")
    p.add_argument("--only", metavar="IDS",
                   help="Show only these chunk ids (comma-separated, e.g. "
                        "'fmt,bext'). Case-insensitive, matched against the "
                        "displayed id. Compose with --hex to hexdump one chunk.")
    p.add_argument("--exclude", metavar="IDS",
                   help="Hide these chunk ids (comma-separated). Applied after "
                        "--only.")
    p.add_argument("--full", action="store_true",
                   help="Emit a self-contained structural dump (implies -f json): "
                        "each chunk with its raw region bytes and every field's "
                        "absolute byte offset, so build_explorer.py can render a "
                        "standalone HTML explorer for the file.")
    p.add_argument("--anomalies", action="store_true",
                   help="Forensic scan: flag trailing data past the container, "
                        "appended-format magic (polyglots), structural size "
                        "mismatches, and control bytes smuggled into text fields.")
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                   help="Colorize table output: auto (default, when stdout is a "
                        "TTY), always, or never. Respects the NO_COLOR env var.")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


# ── rendering ──────────────────────────────────────────────────────


def _hex_bytes(filepath, offset, length, cap=8):
    with open(filepath, "rb") as f:
        f.seek(offset)
        raw = f.read(min(length, cap))
    s = raw.hex(" ")
    return s + " .." if length > cap else s


# ── color ──────────────────────────────────────────────────────────
# small, meaningful palette: structure (cyan), value (green), positional
# metadata (dim), warning (red). codes are zero-width, so callers pad to
# the column width first and paint the padded string.

_ANSI = {
    # bright-black (a real palette slot the terminal theme defines) rather
    # than faint (\033[2m): terminals render faint by blending the fg toward
    # the background, which turns muddy on any non-black background. 90 stays
    # legible against whatever background the user's theme actually uses.
    "dim": "\033[90m",
    "id": "\033[1;36m",     # bold cyan: chunk ids, format label, anchors
    "val": "\033[32m",      # green: decoded field values
    "warn": "\033[1;31m",   # bold red: warnings
}
_RESET = "\033[0m"


def _color_enabled(args):
    # explicit always/never win; NO_COLOR governs auto only.
    mode = getattr(args, "color", "auto")
    if mode == "never":
        return False
    if mode == "always":
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


class _Paint:
    def __init__(self, on):
        self.on = on

    def __call__(self, role, text):
        text = str(text)
        return f"{_ANSI[role]}{text}{_RESET}" if self.on else text


def _render_rows(rows, paint):
    """Print a per-element listing as a compact dynamic-column table."""
    if not rows:
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    header = "    " + "  ".join(f"{c:<{widths[c]}}" for c in cols)
    print(paint("dim", header))
    for r in rows:
        print("    " + "  ".join(f"{str(r.get(c, '')):<{widths[c]}}" for c in cols))


def _human_size(n):
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or unit == "TB":
            return f"{int(x)} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024


def _render_pretty(filepath, fmt_label, chunks, file_warns, args):
    """A clean, human-friendly view of the decoded tags/metadata: section per
    chunk, aligned key/value, no byte offsets. Made for presets and tagged
    files (Bitwig, Vital, Serum, MP4 tags, WAV/FLAC/MP3 metadata)."""
    p = _Paint(_color_enabled(args))
    size = os.path.getsize(filepath)
    print(p("id", os.path.basename(filepath)))
    print(p("dim", f"{fmt_label}, {_human_size(size)}"))
    for c in chunks:
        fields = [f for f in c["fields"]
                  if f["value"] not in (None, "") and str(f["value"]).strip()]
        if not fields:
            continue
        print()
        head = c["id"].strip()
        meta = f"  {p('dim', c['summary'])}" if c.get("summary") else ""
        print(p("id", head) + meta)
        w = max(len(f["name"]) for f in fields)
        for f in fields:
            key = p("dim", f"{f['name']:<{w}}")
            note = f"  {p('dim', '(' + str(f['note']) + ')')}" if f["note"] else ""
            print(f"  {key}  {p('val', f['value'])}{note}")
    all_warns = list(file_warns) + [w for c in chunks for w in c["warnings"]]
    if all_warns:
        print()
        print(p("warn", "warnings:"))
        for w in all_warns:
            print(p("warn", f"  ! {w}"))
    return 0


def _render_anomalies(findings, args):
    """Print the forensic findings from `--anomalies` under the main dump."""
    p = _Paint(_color_enabled(args))
    role = {"alert": "warn", "warn": "warn", "notice": "dim"}
    print()
    if not findings:
        print(p("dim", "  anomalies: none"))
        return
    print(p("id", f"  anomalies ({len(findings)}):"))
    for f in findings:
        sev = f["severity"]
        tag = p(role.get(sev, "dim"), f"[{sev:6}]")
        off = p("dim", f"0x{f['offset']:08x}")
        print(f"    {tag} {off}  {f['rule']:16} {f['message']}")


def _render_table(filepath, fmt_label, chunks, file_warns, args, total=None):
    file_size = os.path.getsize(filepath)
    p = _Paint(_color_enabled(args))
    if total is not None and total != len(chunks):
        count = f"showing {len(chunks)} of {total} chunks"
    else:
        count = f"{len(chunks)} chunks"
    print(f"{os.path.basename(filepath)}: {p('id', fmt_label)}, {file_size:,} bytes, "
          f"{count}")
    print()
    print(p("dim", f"  {'idx':<5} {'id':<5} {'offset':<11} {'size':<11} summary"))
    for i, c in enumerate(chunks):
        idx = p("dim", f"[{c.get('_idx', i):>2}]")
        cid = p("id", f"{c['id']:<5}")
        off = p("dim", f"0x{c['offset']:08x}")
        print(f"  {idx}  {cid} {off}  {c['size']:<11,} {c['summary']}")

    if not args.quiet:
        for c in chunks:
            if not c["fields"] and not c.get("rows"):
                continue
            print()
            hdr_id = p("id", c["id"].strip())
            hdr_meta = p("dim", f"@ 0x{c['offset']:08x} ({c['size']} bytes)")
            print(f"{hdr_id} {hdr_meta}")
            for fl in c["fields"]:
                note = p("dim", f"  {fl['note']}") if fl["note"] else ""
                # derived stats (midi track facts) carry no byte offset
                off_col = f"+0x{fl['off']:04x}" if fl["off"] is not None else "      "
                off_col = p("dim", off_col)
                val = p("val", f"{fl['value']!s:<14}")
                if args.show_hex and fl["off"] is not None:
                    # field offsets are measured from the chunk's payload base.
                    # RIFF/AIFF/RF64/MThd all have an 8-byte id+size header, so
                    # that is the default; formats with a different header (FLAC
                    # blocks: 4 bytes) or whose fields are already absolute (MP3
                    # ID3 tags, MPEG frames, the FLAC/Serum magic) set their own.
                    base = c.get("payload_base")
                    if base is None:
                        base = c["offset"] + 8
                    hx = _hex_bytes(filepath, base + fl["off"], fl["len"])
                    print(f"  {off_col}  {p('dim', f'{hx:<26}')} "
                          f"{fl['name']:<22} {val}{note}")
                else:
                    print(f"  {off_col}  {fl['name']:<22} {val}{note}")
            if c.get("rows"):
                _render_rows(c["rows"], p)

    if getattr(args, "frames", False) and not any(c.get("rows") for c in chunks):
        print()
        print(p("dim", f"  (--frames: {fmt_label} has no per-element structure to dump)"))

    all_warns = list(file_warns)
    all_warns += [f"{c['id'].strip()}: {w}" for c in chunks for w in c["warnings"]]
    if all_warns:
        print()
        print(p("warn", "warnings:"))
        for w in all_warns:
            print(p("warn", f"  ! {w}"))
    return 0


def _parse_id_list(val):
    """A comma-separated chunk-id list into a normalized set (or None)."""
    if not val:
        return None
    return {x.strip().casefold() for x in val.split(",") if x.strip()}


def _select_chunks(chunks, only, exclude):
    """Filter chunks by --only/--exclude, tagging each survivor with its
    original index so the table keeps truthful [n] and file positions."""
    out = []
    for i, c in enumerate(chunks):
        cid = c["id"].strip().casefold()
        if only is not None and cid not in only:
            continue
        if exclude is not None and cid in exclude:
            continue
        c = dict(c)
        c["_idx"] = i
        out.append(c)
    return out


def _full_chunk(chunk, filepath):
    """Enrich a chunk for --full into a self-contained record: its absolute
    payload base, the raw region bytes as hex (capped), and every field's
    absolute byte offset. build_explorer.py needs nothing but this JSON."""
    c = {k: v for k, v in chunk.items() if k != "_idx"}
    pb = chunk.get("payload_base", chunk["offset"] + 8)
    c["payload_base"] = pb
    fields = []
    for f in chunk["fields"]:
        f2 = dict(f)
        # absolute file offset, so a field maps to raw[abs - offset]
        f2["abs"] = pb + f["off"] if f["off"] is not None else None
        fields.append(f2)
    c["fields"] = fields
    # only carry raw bytes for chunks that actually have positioned fields;
    # audio-data regions are huge and have nothing to highlight.
    if any(f["off"] is not None for f in chunk["fields"]):
        n = min(chunk["size"], _FULL_RAW_CAP)
        with open(filepath, "rb") as fh:
            fh.seek(chunk["offset"])
            raw = fh.read(n)
        c["raw"] = raw.hex()
        c["raw_base"] = chunk["offset"]
        if chunk["size"] > _FULL_RAW_CAP:
            c["raw_truncated"] = chunk["size"] - _FULL_RAW_CAP
    return c


def run(args):
    # accept either the multi-file `targets` or the legacy single `target`
    targets = getattr(args, "targets", None)
    if not targets:
        one = getattr(args, "target", None)
        targets = [one] if one else []
    if not targets:
        print("acidcat inspect: no target file given", file=sys.stderr)
        return 1

    deep = getattr(args, "frames", False) or getattr(args, "verbose", False)
    full = getattr(args, "full", False)
    as_json = args.format == "json" or full  # --full is a JSON dump
    multi = len(targets) > 1
    only = _parse_id_list(getattr(args, "only", None))
    exclude = _parse_id_list(getattr(args, "exclude", None))
    exit_code = 0

    try:
        for filepath in targets:
            if not os.path.isfile(filepath):
                print(f"acidcat inspect: {filepath}: No such file", file=sys.stderr)
                exit_code = 1
                continue
            try:
                fmt_label, chunks, file_warns = walk_file(filepath, deep)
            except Unsupported as e:
                print(f"acidcat inspect: {filepath}: {e}", file=sys.stderr)
                exit_code = 1
                continue
            except Exception as e:  # a walker bug must not sink the whole run
                print(f"acidcat inspect: {filepath}: {e.__class__.__name__}: {e}",
                      file=sys.stderr)
                exit_code = 1
                continue

            total = len(chunks)
            shown = _select_chunks(chunks, only, exclude)
            findings = (anomaliesmod.scan(filepath, fmt_label, chunks, file_warns)
                        if getattr(args, "anomalies", False) else None)
            lsb_info = None
            if getattr(args, "anomalies", False) or full:
                try:
                    lsb_info = lsbmod.analyze(filepath, fmt_label, chunks)
                except Exception:
                    lsb_info = None
            if findings is not None and lsb_info and lsb_info["uniform_high"]:
                findings.append({
                    "severity": "notice", "offset": lsb_info["region"][0],
                    "rule": "lsb_entropy",
                    "message": f"uniformly high LSB entropy (min {lsb_info['min']}, "
                               f"mean {lsb_info['mean']}): consistent with LSB "
                               f"steganography, but also with a noisy/dithered/"
                               f"high-bit-depth recording"})
                findings.sort(key=lambda x: (
                    -{"alert": 3, "warn": 2, "notice": 1}.get(x["severity"], 0),
                    x["offset"]))

            if as_json:
                # NDJSON: one compact record per file per line, so the stream
                # pipes cleanly into jq -c and other line-oriented tools.
                if full:
                    out_chunks = [_full_chunk(c, filepath) for c in shown]
                else:
                    out_chunks = [{k: v for k, v in c.items() if k != "_idx"}
                                  for c in shown]
                sys.stdout.write(json.dumps({
                    "file": filepath,
                    "format": fmt_label,
                    "size": os.path.getsize(filepath),
                    "full": full,
                    "chunks": out_chunks,
                    "warnings": file_warns,
                    **({"anomalies": findings} if findings is not None else {}),
                    **({"lsb": lsb_info} if lsb_info else {}),
                }) + "\n")
            else:
                pretty = getattr(args, "pretty", False)
                if multi and not pretty:
                    print(f"\nFile: {filepath}")  # readelf-style per-file banner
                elif multi:
                    print()  # separate files; --pretty prints its own name header
                if pretty:
                    _render_pretty(filepath, fmt_label, shown, file_warns, args)
                else:
                    _render_table(filepath, fmt_label, shown, file_warns, args, total)
                if findings is not None:
                    _render_anomalies(findings, args)
    except BrokenPipeError:
        # a downstream pager or `head` closed the pipe: exit quietly the way
        # cat and grep do, without a traceback.
        try:
            sys.stdout.close()
        except Exception:
            pass
        return exit_code

    return exit_code
