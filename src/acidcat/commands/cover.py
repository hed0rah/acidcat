"""acidcat edit: extract, embed, or remove embedded cover art.

  acidcat cover song.mp3                 # show cover info
  acidcat cover song.mp3 -o art.jpg      # extract the cover to a file
  acidcat cover song.flac --set art.png  # embed (backs up the original)
  acidcat cover song.m4a --remove        # remove embedded art
"""

import os
import sys

from acidcat.core.formats import cover as covermod

_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
        "image/webp": "webp"}


def register(subparsers):
    p = subparsers.add_parser(
        "cover", help="Extract, embed, or remove embedded cover art.")
    p.add_argument("file")
    p.add_argument("-o", "--output",
                   help="Extract the cover image to this path.")
    p.add_argument("--set", metavar="IMAGE", dest="set_image",
                   help="Embed IMAGE as the front cover (backs up the original).")
    p.add_argument("--remove", action="store_true",
                   help="Remove embedded cover art (backs up the original).")
    p.add_argument("--overwrite", action="store_true",
                   help="Skip the _original backup when writing.")
    p.set_defaults(func=run)


def _mutate(path, image, overwrite, out=None, dry_run=False):
    """Embed `image` as the cover (None removes it) through the edit front door
    (acidcat.core.edit): the audio payload is checked unchanged, the cover is
    read back and the file re-walked before anything is committed. Nothing
    is written on a dry run, or when there was no cover to remove. Returns
    (written or None, backup, record)."""
    from acidcat.core import edit as editmod
    patch = editmod.edit_path(path, {"cover": image})
    try:
        patch.verify()
    except editmod.PatchError as e:
        raise covermod.CoverError(str(e))
    rec = patch.records[0]
    if dry_run or (image is None and not rec.field["removed"]):
        return None, None, rec
    written, backup = patch.commit(out=out, backup=not overwrite)
    return written, backup, rec


def change(path, image_path, out=None, dry_run=False, overwrite=False):
    """`edit --set cover=@IMAGE` (image_path) or `edit --unset cover` (None),
    honouring -o and --dry-run. The exit code."""
    if not os.path.isfile(path):
        print(f"acidcat edit: {path}: No such file", file=sys.stderr)
        return 2
    img = None
    if image_path is not None:
        if not os.path.isfile(image_path):
            print(f"acidcat edit: {image_path}: No such file", file=sys.stderr)
            return 2
        with open(image_path, "rb") as fh:
            img = fh.read()
    try:
        written, backup, rec = _mutate(path, img, overwrite, out=out,
                                       dry_run=dry_run)
    except covermod.CoverError as e:
        print(f"acidcat edit: {path}: {e}", file=sys.stderr)
        # no cover support for this kind of file is could-not-run
        return 2 if isinstance(e, covermod.CoverUnmodelled) else 1
    base = os.path.basename(path)
    if img is None and not rec.field["removed"]:
        print(f"acidcat edit: {base}: no embedded cover art", file=sys.stderr)
        return 0
    if dry_run:
        what = (f"embed the cover from {os.path.basename(image_path)} "
                f"({len(img):,} bytes)" if img is not None
                else "remove the cover art")
        print(f"{base}: would {what} (dry run, nothing written)")
        return 0
    note = f"  (backup: {os.path.basename(backup)})" if backup else ""
    if img is not None:
        print(f"embedded cover from {os.path.basename(image_path)} "
              f"({len(img):,} bytes) into {os.path.basename(written)}{note}")
    else:
        print(f"removed cover art from {os.path.basename(written)}{note}")
    return 0


def run(args):
    path = args.file
    if not os.path.isfile(path):
        print(f"acidcat edit: {path}: No such file", file=sys.stderr)
        return 2
    try:
        if args.set_image:
            if not os.path.isfile(args.set_image):
                print(f"acidcat edit: {args.set_image}: No such file", file=sys.stderr)
                return 2
            img = open(args.set_image, "rb").read()
            written, backup, _rec = _mutate(path, img, args.overwrite)
            note = f"  (backup: {os.path.basename(backup)})" if backup else ""
            print(f"embedded cover from {os.path.basename(args.set_image)} "
                  f"({len(img):,} bytes) into {os.path.basename(written)}{note}")
            return 0
        if args.remove:
            written, backup, rec = _mutate(path, None, args.overwrite)
            if not rec.field["removed"]:
                print(f"acidcat edit: {os.path.basename(path)}: no embedded cover art")
                return 0
            note = f"  (backup: {os.path.basename(backup)})" if backup else ""
            print(f"removed cover art from {os.path.basename(written)}{note}")
            return 0
        # default: extract (or just report if no -o)
        got = covermod.extract(path)
        if not got:
            print(f"{os.path.basename(path)}: no embedded cover art")
            return 0
        mime, blob = got
        if not args.output:
            print(f"{os.path.basename(path)}: cover art present, {mime}, {len(blob):,} bytes "
                  f"(use -o FILE to extract)")
            return 0
        out = args.output
        with open(out, "wb") as f:
            f.write(blob)
        print(f"extracted cover ({mime}, {len(blob):,} bytes) to {out}")
        return 0
    except covermod.CoverError as e:
        print(f"acidcat edit: {path}: {e}", file=sys.stderr)
        # no cover support for this kind of file is could-not-run
        return 2 if isinstance(e, covermod.CoverUnmodelled) else 1
