"""acidcat cover: extract, embed, or remove embedded cover art.

  acidcat cover song.mp3                 # show cover info
  acidcat cover song.mp3 -o art.jpg      # extract the cover to a file
  acidcat cover song.flac --set art.png  # embed (backs up the original)
  acidcat cover song.m4a --remove        # remove embedded art
"""

import os
import sys
import tempfile

from acidcat.core import cover as covermod
from acidcat.core import writer

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


def _mutate(path, fn, overwrite):
    """Run fn(tmp) on a temp copy, then commit the result to path (backup+atomic)."""
    with open(path, "rb") as f:
        data = f.read()
    fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(path)[1])
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        fn(tmp)
        with open(tmp, "rb") as f:
            new = f.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return writer.commit(path, new, overwrite=overwrite)


def run(args):
    path = args.file
    if not os.path.isfile(path):
        print(f"acidcat cover: {path}: No such file", file=sys.stderr)
        return 1
    try:
        if args.set_image:
            if not os.path.isfile(args.set_image):
                print(f"acidcat cover: {args.set_image}: No such file", file=sys.stderr)
                return 1
            img = open(args.set_image, "rb").read()
            written, backup = _mutate(path, lambda t: covermod.set_cover(t, img),
                                      args.overwrite)
            note = f"  (backup: {os.path.basename(backup)})" if backup else ""
            print(f"embedded cover from {os.path.basename(args.set_image)} "
                  f"({len(img):,} bytes) into {os.path.basename(written)}{note}")
            return 0
        if args.remove:
            removed = {"v": False}

            def _rm(t):
                removed["v"] = covermod.remove_cover(t)
            written, backup = _mutate(path, _rm, args.overwrite)
            if not removed["v"]:
                print(f"acidcat cover: {os.path.basename(path)}: no embedded cover art")
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
        print(f"acidcat cover: {path}: {e}", file=sys.stderr)
        return 1
