"""acidcat wrap -- give headerless PCM a container, so it can be played.

The missing half of the recovery workflow. `locate` finds raw audio with no
header, `--analyze` infers its geometry, and `carve` pulls the bytes -- but raw
bytes are not a file anything will open. This adds the header, and nothing else.

A filter by design: bytes in, bytes out, so it composes rather than needing its
own file handling.

    acidcat carve img --offset 0x624400 --length 1536 \\
      | acidcat wrap --rate 44100 --bits 16 --endian be > frag.wav

    acidcat wrap raw.pcm --rate 22050 --channels 2 -o out.wav

WAV stores samples little-endian, so `--endian be` swaps them on the way
through. That is not a detail a caller should have to remember: `locate
--analyze` reports big-endian geometry regularly (it is common in sampler and
console formats), and the byte order it names is the one to pass here.
"""

import os
import struct
import sys

from acidcat.util import outpath

from acidcat.util.play import _wav_wrap

_WIDTHS = (8, 16, 24, 32, 64)


def register(subparsers):
    p = subparsers.add_parser(
        "wrap",
        help="Wrap raw PCM bytes in a WAV header so they can be played.")
    p.add_argument("input", nargs="?", default="-",
                   help="Raw PCM file, or '-' for stdin (the default).")
    p.add_argument("-o", "--output",
                   help="Write here (default: stdout).")
    p.add_argument("--rate", type=int, default=44100,
                   help="Sample rate in Hz (default: 44100). `locate --analyze` "
                        "cannot know this -- it is playback metadata, not "
                        "something the bytes carry.")
    p.add_argument("--channels", type=int, default=1,
                   help="Channel count (default: 1).")
    p.add_argument("--bits", type=int, default=16, choices=_WIDTHS,
                   help="Bits per sample (default: 16).")
    p.add_argument("--endian", choices=("le", "be"), default="le",
                   help="Byte order of the INPUT (default: le). WAV is always "
                        "little-endian, so 'be' is byte-swapped on the way out.")
    p.add_argument("--float", dest="floating", action="store_true",
                   help="Samples are IEEE float rather than integer "
                        "(32 or 64 bits).")
    p.set_defaults(func=run)


def _swap(data, width):
    """Reverse byte order within each sample."""
    step = width // 8
    if step < 2:
        return data
    usable = len(data) - (len(data) % step)
    out = bytearray(usable)
    for start in range(step):
        out[start::step] = data[step - 1 - start:usable:step]
    return bytes(out)


def pcm_to_wav(data, rate=44100, channels=1, bits=16, byte_order="le",
               floating=False, prog="wrap"):
    """(exit code, WAV bytes or None) for raw PCM `data`: checks the geometry,
    drops a partial trailing frame (saying so on stderr), swaps big-endian
    samples, and adds the header. The one implementation behind `wrap` and
    `carve --as-wav`."""
    if floating and bits not in (32, 64):
        print(f"acidcat {prog}: --float needs --bits 32 or 64", file=sys.stderr)
        return 2, None
    if channels < 1:
        print(f"acidcat {prog}: --channels must be at least 1", file=sys.stderr)
        return 2, None
    if not 1 <= rate <= 768000:
        print(f"acidcat {prog}: --rate must be between 1 and 768000",
              file=sys.stderr)
        return 2, None
    if not data:
        print(f"acidcat {prog}: no input bytes", file=sys.stderr)
        return 2, None
    block = channels * (bits // 8)
    trimmed = len(data) - (len(data) % block) if block else len(data)
    if trimmed != len(data):
        print(f"acidcat {prog}: dropped {len(data) - trimmed} trailing byte(s) "
              f"to land on a {block}-byte frame", file=sys.stderr)
    data = data[:trimmed]
    if not data:
        print(f"acidcat {prog}: fewer than one {block}-byte frame of input",
              file=sys.stderr)
        return 1, None
    if byte_order == "be":
        data = _swap(data, bits)
    return 0, _wav_wrap(data, rate, channels, bits, 3 if floating else 1)


def run(args):
    try:
        if args.input == "-":
            data = sys.stdin.buffer.read()
        else:
            with open(args.input, "rb") as f:
                data = f.read()
    except OSError as e:
        print(f"acidcat carve: {args.input}: {e}", file=sys.stderr)
        return 2
    rc, wav = pcm_to_wav(data, args.rate, args.channels, args.bits, args.endian,
                         args.floating)
    if wav is None:
        return rc
    # what went in the file: the input less any partial trailing frame
    block = args.channels * (args.bits // 8)
    data = data[:len(data) - (len(data) % block)] if block else data

    if args.output:
        err = outpath.refuse_self_overwrite('wrap', args.input, args.output)
        if err:
            print(err, file=sys.stderr)
            return 2
        try:
            with open(args.output, "wb") as f:
                f.write(wav)
        except OSError as e:
            print(f"acidcat carve: {args.output}: {e}", file=sys.stderr)
            return 2
        frames = len(data) // block if block else 0
        print(f"wrapped {len(data):,} bytes as {args.rate} Hz {args.channels}ch "
              f"{args.bits}-bit ({frames / args.rate:.3f} s) -> {args.output}",
              file=sys.stderr)
    else:
        if sys.stdout.isatty():
            # every other binary-emitting verb refuses this; dumping a WAV into
            # a terminal garbles the session and teaches nothing
            print("acidcat carve: refusing to write a WAV to the terminal -- "
                  "redirect it or use -o FILE", file=sys.stderr)
            return 2
        sys.stdout.buffer.write(wav)
        sys.stdout.buffer.flush()
    return 0
