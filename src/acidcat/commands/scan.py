"""
acidcat scan -- batch-scan a directory of audio files.
"""

import csv
import os
import sys

from acidcat.core.formats.riff import (
    smpl_root_or_none, acid_root_or_none, effective_acid_beats,
)
from acidcat.commands._output import add_output_format_arg, out_stream
from acidcat.core.formats.aiff import is_aiff
from acidcat.core.tagged import is_tagged_format
from acidcat.util.midi import midi_note_to_name
from acidcat.util.csv_helpers import safe_basename_for_csv


# extensions to pick up during directory walk
AUDIO_EXTENSIONS = {
    ".wav", ".aif", ".aiff",
    ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac",
}


def register(subparsers):
    p = subparsers.add_parser("scan", help="Batch-scan a directory of audio files.")
    p.add_argument("target", help="Directory containing audio files.")
    p.add_argument("-o", "--output", help="Output CSV filename.")
    p.add_argument("-n", "--num", type=int, default=500, help="Max files to scan (default: 500).")
    p.add_argument("-q", "--quiet", action="store_true", help="Suppress console output.")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose output.")
    add_output_format_arg(p, default="csv", only=("table", "json", "csv", "tsv"))
    p.add_argument("--has", help="Filter: only WAV files containing these chunk IDs (comma-separated).")
    p.add_argument("--fallback", action="store_true",
                   help="Estimate BPM/key with librosa if no metadata found.")
    p.add_argument("--features", action="store_true",
                   help="Extract 50+ audio features for ML analysis.")
    p.set_defaults(func=run)


def _scan_wav(filepath):
    """Extract metadata row from a WAV file, from the inspect walker (the
    single WAV decoder since the 2026-07 unification)."""
    from acidcat.core.walk.wav import inspect_wav

    ctx = {}
    chunks, _warns = inspect_wav(filepath, ctx=ctx)
    seen = list(dict.fromkeys(c["id"] for c in chunks))
    duration = round(ctx["duration"], 4) if ctx.get("duration") else None
    bpm = ctx.get("acid_bpm")

    beats = effective_acid_beats(
        {"acid_beats": ctx.get("acid_beats"),
         "acid_one_shot": ctx.get("acid_one_shot"), "bpm": bpm}, duration)
    expected = diff = None
    if bpm and beats:
        expected = round((beats / bpm) * 60, 4)
        diff = round(duration - expected, 4) if duration else None

    # SMPL/ACID root_key = 0 is the documented "unset" sentinel
    # (MIDI C-1). Coerce to None before formatting so the CSV key
    # column does not ship `C-1` for files whose SMPL chunk is
    # present but unset.
    smpl_root = smpl_root_or_none(ctx.get("smpl_root"))
    acid_root = acid_root_or_none(ctx.get("acid_root"))
    key = midi_note_to_name(smpl_root) or midi_note_to_name(acid_root)

    return {
        "path": filepath,
        "format": "wav",
        "bpm": bpm,
        "key": key,
        "duration_sec": duration,
        "title": None,
        "artist": None,
        "acid_beats": beats,
        "expected_duration": expected,
        "duration_diff": diff,
        "chunks": ",".join(c for c in seen if c not in ("RIFF", "WAVE", "fmt ", "data")),
    }, seen


def _scan_aiff(filepath):
    """Extract metadata row from an AIFF file, from the inspect walker."""
    from acidcat.core.walk.aiff import inspect_aiff

    with open(filepath, "rb") as f:
        form = "AIFC" if f.read(12)[8:12] == b"AIFC" else "AIFF"
    ctx = {}
    chunks, _warns = inspect_aiff(filepath, form, ctx=ctx)
    seen = [c["id"] for c in chunks]

    return {
        "path": filepath,
        "format": "aiff",
        "bpm": None,
        "key": None,
        "duration_sec": ctx.get("duration"),
        "title": ctx.get("name"),
        "artist": ctx.get("author"),
        "acid_beats": None,
        "expected_duration": None,
        "duration_diff": None,
        "chunks": ",".join(seen),
    }, seen


def _scan_tagged(filepath):
    """Extract metadata row from a tagged format (MP3, FLAC, OGG, M4A)."""
    from acidcat.core.tagged import parse_tagged

    meta = parse_tagged(filepath)
    if meta is None:
        return None, []

    return {
        "path": filepath,
        "format": meta.get("format_type", "unknown"),
        "bpm": meta.get("bpm"),
        "key": meta.get("key"),
        "duration_sec": meta.get("duration"),
        "title": meta.get("title"),
        "artist": meta.get("artist"),
        "acid_beats": None,
        "expected_duration": None,
        "duration_diff": None,
        "chunks": None,
    }, []


def _groups(targets):
    """(dir, [names]) groups as os.walk gives them, for directories and for
    files named directly (`stats FILE`), in the order given."""
    for t in targets:
        if os.path.isfile(t):
            yield os.path.dirname(t) or ".", [], [os.path.basename(t)]
        else:
            yield from os.walk(t)


def run(args):
    # `targets` when stats passes several (files or directories); 1.8's
    # parser gave one directory as `target`
    targets = list(getattr(args, "targets", None) or [args.target])
    for t in targets:
        if not os.path.exists(t):
            print(f"acidcat stats: {t}: No such file or directory", file=sys.stderr)
            return 2
    directory = ", ".join(targets)

    # stdout unless -o names a file. This used to invent
    # `<dirname>_metadata.csv` in whatever directory you happened to be
    # standing in, silently overwriting a file of that name. It is documented
    # as "batch-scan with CSV output", which reads as a pipe, and an earlier
    # fix here only made the surprise easier to FIND by printing an absolute
    # path. Not writing it unasked is the actual fix.
    output_csv = getattr(args, 'output', None)
    if output_csv:
        output_csv = safe_basename_for_csv(output_csv)

    wanted = None
    has_val = getattr(args, 'has', None)
    if has_val:
        wanted = set(w.strip().upper() for w in has_val.split(",") if w.strip())

    quiet = getattr(args, 'quiet', False)
    verbose = getattr(args, 'verbose', False) and not quiet
    do_fallback = getattr(args, 'fallback', False)
    do_features = getattr(args, 'features', False)
    num = getattr(args, 'num', 500)

    def _vlog(msg):
        if verbose:
            print(msg, file=sys.stderr)

    _vlog(f"[scan] dir={directory} num={num} fallback={do_fallback} "
          f"features={do_features}")

    rows = []
    count = 0

    for root, _, files in _groups(targets):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue

            filepath = os.path.join(root, file)

            # dispatch by format
            try:
                if ext in (".wav",):
                    row, seen = _scan_wav(filepath)
                elif ext in (".aif", ".aiff") or is_aiff(filepath):
                    row, seen = _scan_aiff(filepath)
                elif is_tagged_format(filepath):
                    row, seen = _scan_tagged(filepath)
                    if row is None:
                        continue
                else:
                    continue
            except Exception as e:
                if not quiet:
                    print(f"  [skip] {file}: {e}", file=sys.stderr)
                continue

            # Chunk filter. `and seen` made this a no-op for any format with no
            # chunks -- a tagged MP3 or an AppleDouble stub returns seen == [],
            # so the filter was skipped and the row kept. `--has acid` returned
            # 95 rows where two independent counts (survey, and the index's own
            # `chunks` column) both say 80: 13 MP3s, an MP4 and a resource-fork
            # stub passed a RIFF-chunk filter. A file with no chunks cannot
            # contain the one you asked for.
            if wanted:
                upper_seen = {s.upper() for s in seen}
                if not (upper_seen & wanted):
                    continue

            # optional ML features
            if do_features:
                from acidcat.core.analysis.features import extract_audio_features
                if not quiet:
                    print(f"  [features] {os.path.basename(filepath)}...", file=sys.stderr)
                feats = extract_audio_features(filepath)
                if feats:
                    row.update(feats)

            # fallback BPM/key via librosa
            if do_fallback and not row.get("bpm"):
                from acidcat.core.analysis.detect import estimate_librosa_metadata
                estimates = estimate_librosa_metadata(filepath)
                if estimates.get("estimated_bpm") is not None:
                    row["bpm"] = estimates["estimated_bpm"]
                if estimates.get("estimated_key") is not None:
                    row["key"] = estimates["estimated_key"]
                if estimates.get("duration_sec") is not None and not row.get("duration_sec"):
                    row["duration_sec"] = estimates["duration_sec"]

            if not quiet:
                bpm_str = row.get("bpm") or "-"
                print(f"  {os.path.basename(filepath):40s} BPM={bpm_str}", file=sys.stderr)
            if verbose:
                _vlog(f"    format={row.get('format')} "
                      f"key={row.get('key') or '-'} "
                      f"dur={row.get('duration_sec') or '-'}")

            # `format` the registry id and `label` its display label, as every
            # verb's JSON names a format (cli-2.0.md section 4.1)
            from acidcat.commands._output import format_of
            fo = format_of(filepath)
            row["format"] = fo["format"] or row.get("format")
            row["label"] = fo["label"]
            rows.append(row)
            count += 1
            if count >= num:
                break
        if count >= num:
            break

    if not rows:
        if not quiet:
            print("acidcat stats: No audio files found.", file=sys.stderr)
        # nothing its mode reads is the answer no, as --by shape and --by
        # chunks say it (review V7)
        return 1

    # fieldnames: core set, then any extras from features
    base_fieldnames = [
        "path", "format", "label", "bpm", "key", "duration_sec",
        "title", "artist",
        "acid_beats", "expected_duration", "duration_diff", "chunks",
    ]
    if do_features and rows:
        all_keys = set()
        for r in rows:
            all_keys.update(r.keys())
        feature_keys = sorted(k for k in all_keys if k not in base_fieldnames)
        fieldnames = base_fieldnames + feature_keys
    else:
        fieldnames = base_fieldnames

    # An explicitly requested rendering goes to stdout. `add_output_format_arg`
    # registered --json/--csv/--output-format here and run() then ignored them
    # entirely, always writing CSV to a file -- so `scan DIR --json` accepted
    # the flag and produced CSV, and `scan DIR | anything` piped nothing at all.
    # The default is unchanged (a CSV file) because scripts depend on it.
    fmt = getattr(args, "output_format", None)
    if fmt in ("json", "table"):
        from acidcat.core.infra.render import format_columns, output as _render
        shaped = [{k: r.get(k) for k in fieldnames} for r in rows]
        stream = sys.stdout
        if getattr(args, "output", None):
            stream = open(args.output, "w", encoding="utf-8", newline="")
        try:
            if fmt == "table":
                # one line per file: a record per file was thousands of lines
                # over a real library (every field is in --json and --csv)
                format_columns(shaped, [("path", "file"), ("format", "format"),
                                        ("bpm", "bpm"), ("key", "key"),
                                        ("duration_sec", "seconds"),
                                        ("chunks", "chunks")], stream)
            else:
                _render(shaped, fmt=fmt, stream=stream)
        finally:
            if stream is not sys.stdout:
                stream.close()
        # This path returned before the cap note below, so `scan DIR --json`
        # that stopped at -n said nothing about stopping: the machine-readable
        # face was the one that could not tell a complete run from a truncated
        # one. stderr, so the records on stdout stay parseable.
        if getattr(args, "cap_flag", None) and count >= num:
            from acidcat.commands.stats import cap_note
            cap_note(num)
        elif not quiet and count >= num:
            # "may remain": the loop breaks AT the cap without peeking, so a
            # directory holding exactly -n files is indistinguishable from one
            # holding more. Claiming more remain would be a confident guess in
            # a release about not making those.
            print(f"[INFO] stopped at the -n {num} cap; more files may remain",
                  file=sys.stderr)
        return 0

    # output
    #
    # lineterminator="\n", and out_stream for the file/stdout split. csv's
    # default terminator is "\r\n"; a file opened with newline="" passes that
    # through untouched, but stdout is a TEXT stream that translates the "\n"
    # again -- so the moment this started piping, every row ended "\r\r\n" on
    # Windows. The sibling renderer already knew: core/infra/render.py's
    # `_delimited` passes lineterminator="\n" for exactly this reason.
    with out_stream(output_csv) as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames,
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    if not quiet:
        # a truncated run and a complete one must not print the same sentence:
        # "500 files" reads as the library's size, not as where we stopped
        cap_note = (f" (stopped at the -n {num} cap; more files may remain)"
                    if count >= num and not getattr(args, "cap_flag", None) else "")
        # The ABSOLUTE path when there is one. An earlier fix here printed the
        # absolute path because a bare filename left people hunting with
        # `find` -- which made the surprise easier to locate rather than
        # stopping it. Now there is only a path to print when one was asked for.
        where = f" to {os.path.abspath(output_csv)}" if output_csv else ""
        print(f"\n[INFO] Wrote metadata for {len(rows)} files{where}"
              f"{cap_note}", file=sys.stderr)
    if getattr(args, "cap_flag", None) and count >= num:
        from acidcat.commands.stats import cap_note as _cap
        _cap(num)

    return 0
