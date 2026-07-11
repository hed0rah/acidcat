"""acidcat repair -- fix structural inconsistencies without touching the audio.

Repair is the read-side of the same constraint model that powers write: parse the
container, recompute every derived field from the actual layout, and re-emit. The
bytes it changes are only the ones it can justify -- a stale master size, a nested
LIST/FORM size that no longer matches its children, a non-zero pad byte where the
spec requires 0x00. It never invents or removes content: appended data past the
container is preserved verbatim (carve it out separately if it is unwanted), and
the audio payload is compared before and after as a hard guard.

    acidcat repair FILE...              # fix in place (keeps a _original backup)
    acidcat repair FILE -o fixed.wav    # write a corrected copy instead
    acidcat repair FILE --dry-run       # show what would change, write nothing

Supports the IFF family acidcat models structurally: RIFF/WAVE, RF64, AIFF/AIFC,
and the SoundFont (sfbk) containers. Other formats report "nothing to repair here"
rather than guessing.
"""

import os
import sys

from acidcat.core import structure, writer

# the primary audio payload id per form type; compared before/after as a guard so
# a repair can never alter a single sample
_AUDIO_CHUNK = {b"WAVE": b"data", b"AIFF": b"SSND", b"AIFC": b"SSND"}


def register(subparsers):
    p = subparsers.add_parser(
        "repair",
        help="Recompute stale size/pad fields in a RIFF/AIFF container (audio preserved).")
    p.add_argument("inputs", nargs="+", help="File(s) to repair.")
    p.add_argument("-o", "--output", help="Write a corrected copy here (single input).")
    p.add_argument("--dry-run", action="store_true",
                   help="Show the changes and write nothing.")
    p.add_argument("--overwrite", action="store_true",
                   help="Skip the _original backup on in-place repair.")
    p.add_argument("--keep-pad", action="store_true",
                   help="Do not normalize a non-zero pad byte to 0x00.")
    p.set_defaults(func=run)


def _audio_payload(node):
    """The primary audio payload bytes (data/SSND) under a parsed container, or
    None. Only walks the top level, which is where both live."""
    want = _AUDIO_CHUNK.get(node.form_type)
    if not want or not node.children:
        return None
    for c in node.children:
        if c.id == want and not c.is_container:
            return c.payload
    return None


def _repair_one(path, args):
    with open(path, "rb") as f:
        data = f.read()
    if not structure.is_iff(data):
        print(f"acidcat repair: {path}: not a RIFF/AIFF container "
              f"(nothing to repair here)", file=sys.stderr)
        return 1
    try:
        node = structure.parse(data)
    except structure.StructError as e:
        print(f"acidcat repair: {path}: {e}", file=sys.stderr)
        return 1

    before_audio = _audio_payload(node)
    changes = structure.recompute(node, normalize_pad=not args.keep_pad)
    new_data = structure.emit(node)

    # hard guard: repairing structure must never move a byte of audio
    after_audio = _audio_payload(structure.parse(new_data))
    if before_audio != after_audio:
        print(f"acidcat repair: {path}: aborted, audio payload would change "
              f"(refusing to write)", file=sys.stderr)
        return 1

    label = node.form_type.decode("latin-1", "replace")
    if not changes:
        print(f"{os.path.basename(path)}  [{label}]  already consistent")
        return 0

    print(f"{os.path.basename(path)}  [{label}]")
    for c in changes:
        old, new = c["old"], c["new"]
        if c["field"] == "pad_byte":
            print(f"  {c['path']} pad byte: 0x{old:02x} -> 0x{new:02x}")
        else:
            print(f"  {c['path']} {c['field']}: {old:,} -> {new:,} bytes")
    if args.dry_run:
        return 0
    try:
        written, backup = writer.commit(
            path, new_data, out=args.output, overwrite=args.overwrite)
    except OSError as e:
        print(f"acidcat repair: {path}: {e}", file=sys.stderr)
        return 1
    note = f"  (backup: {os.path.basename(backup)})" if backup else ""
    print(f"  wrote {os.path.basename(written)}{note}")
    return 0


def run(args):
    if args.output and len(args.inputs) > 1:
        print("acidcat repair: -o works with a single input file", file=sys.stderr)
        return 2
    rc = 0
    for path in args.inputs:
        try:
            rc = _repair_one(path, args) or rc
        except OSError as e:
            print(f"acidcat repair: {path}: {e}", file=sys.stderr)
            rc = 1
    return rc
