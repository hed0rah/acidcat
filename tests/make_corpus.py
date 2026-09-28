"""Build a small, deterministic WAV corpus so the corpus sweep runs on a fresh
clone.

Why this exists
---------------
`test_ctx_keys.py` checks the WAV walker's ctx keys, parametrized over a
corpus. It was written for the grammar engine's parity sweeps, removed in 2.0.
The corpus defaulted to `~/sample_packs` -- 2,327 files that exist on
exactly one machine. The result: the grammar sweeps' three assertions produced 6,998 of the suite's 8,515 collected tests
locally and **three skips** on CI, so 82% of the headline test count was
unreproducible anywhere else.

Committing the real corpus is not an option: it is licensed third-party sample
content and this is a public repository. So the corpus is *generated* instead --
every byte written here is synthetic, deterministic, and ours.

What it covers
--------------
`fmt`, `inst` and `acid` get the most variation; `data`, `fact`, `smpl`,
`cue ` and `LIST` are there because each publishes ctx keys, and the awkward
cases (odd-sized payloads needing a pad byte, trailing bytes past the declared
end, WAVE_FORMAT_EXTENSIBLE) are where a walker most often goes wrong.

    python tests/make_corpus.py [outdir]

Idempotent: rewrites the same bytes every run, so it is safe in CI and produces
no diff noise.
"""

import os
import struct
import sys

# keep it deterministic without importing random: a tiny LCG, so the same
# bytes come out on every platform and Python version
_SEED = 0x5EED


def _pcm(n_bytes, seed=_SEED):
    """Deterministic pseudo-audio. Not silence -- an all-zero payload hides
    bugs in anything that treats a flat signal as a special case."""
    out = bytearray(n_bytes)
    x = seed
    for i in range(n_bytes):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        out[i] = (x >> 16) & 0xFF
    return bytes(out)


def _chunk(cid, payload):
    """A RIFF chunk, with the pad byte an odd payload requires."""
    assert len(cid) == 4, cid
    body = cid + struct.pack("<I", len(payload)) + payload
    return body + (b"\x00" if len(payload) & 1 else b"")


def _fmt(channels=1, rate=44100, bits=16, tag=1):
    block = channels * (bits // 8)
    return struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)


def _fmt_extensible(channels=2, rate=48000, bits=24):
    """WAVE_FORMAT_EXTENSIBLE: the real tag hides in a GUID 24 bytes in, which
    is a place parsers routinely fail to look."""
    block = channels * (bits // 8)
    base = struct.pack("<HHIIHH", 0xFFFE, channels, rate, rate * block, block, bits)
    ext = struct.pack("<HHI", 22, bits, 0x3)            # cbSize, valid bits, mask
    guid = struct.pack("<H", 1) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    return base + ext + guid


def _acid(one_shot=False, root=60, beats=4, tempo=120.0):
    """ACID loop metadata: the described region with the most fields."""
    flags = 0x01 if one_shot else 0x02                  # one-shot vs root-note set
    return struct.pack("<IHHfIIIIf", flags, root, 0x8000, 0.0,
                       0, beats, 4, 4, tempo)


def _inst(root=60, fine=0, gain=0, lo=0, hi=127, vlo=0, vhi=127):
    return struct.pack("<bbbbbbb", root, fine, gain, lo, hi, vlo, vhi) + b"\x00"


def _smpl(root=60, loops=1):
    body = struct.pack("<IIIIIIIII", 0, 0, 22675, root, 0, 0, 0, loops, 0)
    for i in range(loops):
        body += struct.pack("<IIIIII", i, 0, 0, 1000, 0, 0)
    return body


def _cue(points=2):
    body = struct.pack("<I", points)
    for i in range(points):
        body += struct.pack("<II4sIII", i, i * 500, b"data", 0, 0, i * 500)
    return body


def _list_info(name=b"acidcat synthetic corpus"):
    payload = b"INFO" + _chunk(b"INAM", name + b"\x00")
    payload += _chunk(b"ISFT", b"acidcat tests/make_corpus.py\x00")
    return payload


def _riff(chunks):
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _frames(channels, bits, n=200):
    return _pcm(n * channels * (bits // 8))


# (filename, builder) -- each one targets something a parser can get wrong
def _specimens():
    out = []

    # the fmt matrix: widths and channel counts the walkers branch on
    for bits in (8, 16, 24, 32):
        for ch in (1, 2):
            out.append((
                f"fmt_{bits}b_{ch}c.wav",
                _riff([_chunk(b"fmt ", _fmt(ch, 44100, bits)),
                       _chunk(b"data", _frames(ch, bits))]),
            ))

    # IEEE float PCM (tag 3) -- a different sample interpretation entirely
    out.append(("fmt_float32.wav", _riff([
        _chunk(b"fmt ", _fmt(1, 48000, 32, tag=3)),
        _chunk(b"data", _frames(1, 32))])))

    # extensible: the tag is a lie until you read the GUID
    out.append(("fmt_extensible_24b.wav", _riff([
        _chunk(b"fmt ", _fmt_extensible()),
        _chunk(b"data", _frames(2, 24))])))

    # sample rates that exercise byte_rate/block_align arithmetic
    for rate in (8000, 22050, 96000, 192000):
        out.append((f"rate_{rate}.wav", _riff([
            _chunk(b"fmt ", _fmt(1, rate, 16)),
            _chunk(b"data", _frames(1, 16))])))

    # acid: the richest described region, in both its modes
    out.append(("acid_loop.wav", _riff([
        _chunk(b"fmt ", _fmt(2, 44100, 16)),
        _chunk(b"acid", _acid(one_shot=False, beats=8, tempo=174.0)),
        _chunk(b"data", _frames(2, 16))])))
    out.append(("acid_oneshot.wav", _riff([
        _chunk(b"fmt ", _fmt(1, 44100, 16)),
        _chunk(b"acid", _acid(one_shot=True, beats=0, tempo=0.0)),
        _chunk(b"data", _frames(1, 16))])))

    # inst, alone and beside acid
    out.append(("inst_only.wav", _riff([
        _chunk(b"fmt ", _fmt(1, 44100, 16)),
        _chunk(b"inst", _inst(root=48, lo=36, hi=60)),
        _chunk(b"data", _frames(1, 16))])))
    out.append(("inst_and_acid.wav", _riff([
        _chunk(b"fmt ", _fmt(1, 44100, 24)),
        _chunk(b"inst", _inst(root=72, gain=-3)),
        _chunk(b"acid", _acid(beats=2, tempo=90.0)),
        _chunk(b"data", _frames(1, 24))])))

    # undescribed chunks: skeleton parity has to cover these too
    out.append(("smpl_cue_list.wav", _riff([
        _chunk(b"fmt ", _fmt(2, 44100, 16)),
        _chunk(b"smpl", _smpl(loops=2)),
        _chunk(b"cue ", _cue(3)),
        _chunk(b"LIST", _list_info()),
        _chunk(b"fact", struct.pack("<I", 200)),
        _chunk(b"data", _frames(2, 16))])))

    # odd-sized payload -> RIFF demands a pad byte the size does not count
    out.append(("odd_sized_chunk.wav", _riff([
        _chunk(b"fmt ", _fmt(1, 44100, 16)),
        _chunk(b"LIST", _list_info(b"odd")),          # odd length, padded
        _chunk(b"data", _frames(1, 16, n=99))])))

    # data before fmt: legal ordering that a sequential reader can trip on
    out.append(("data_before_fmt.wav", _riff([
        _chunk(b"data", _frames(1, 16)),
        _chunk(b"fmt ", _fmt(1, 44100, 16))])))

    # bytes past the declared RIFF end
    trailing = _riff([_chunk(b"fmt ", _fmt(1, 44100, 16)),
                      _chunk(b"data", _frames(1, 16))])
    out.append(("trailing_data.wav", trailing + b"APPENDED" + _pcm(64)))

    # an empty data chunk -- zero frames is valid and divides badly
    out.append(("empty_data.wav", _riff([
        _chunk(b"fmt ", _fmt(1, 44100, 16)),
        _chunk(b"data", b"")])))

    return out


def build(outdir):
    os.makedirs(outdir, exist_ok=True)
    written = []
    for name, data in _specimens():
        path = os.path.join(outdir, name)
        with open(path, "wb") as f:
            f.write(data)
        written.append(path)
    return written


DEFAULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "corpus_generated")


def ensure(outdir=DEFAULT_DIR):
    """Build the corpus if it is missing, and return its path. Cheap enough to
    call from conftest on every run."""
    marker = os.path.join(outdir, "fmt_16b_1c.wav")
    if not os.path.isfile(marker):
        build(outdir)
    return outdir


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    paths = build(target)
    total = sum(os.path.getsize(p) for p in paths)
    print(f"wrote {len(paths)} specimens to {target} ({total / 1024:.0f} KB)")
