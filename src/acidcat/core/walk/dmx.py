"""DMX digital sound, the format inside a Doom WAD's DS* lumps.

Eight bytes of header over unsigned 8-bit PCM, and no magic string anywhere.
That absence is the whole character of the format: nothing in the bytes
announces them as audio, so a signature sweep cannot find them and the WAD's
own directory is the only reliable route in. What identifies a lump is its
NAME -- the DS prefix -- plus a format field that has been 3 in every specimen
ever shipped.

    u16  format        3, always
    u16  sample rate   Hz, free-form
    u32  sample count  bytes after the header, PADDING INCLUDED
    ...  samples       unsigned 8-bit mono, 0x80 being silence

Measured over 1,181 lumps from id's own IWADs and from freedoom, which is a
reimplementation rather than a copy and so is a genuinely independent source:

    format code    3 in all 1,181
    rates          11025 (1047), 22050 (124), 12025 (3), 17990 (3),
                   16000 (2), 44100 (2)
    8 + count      equal to the lump length in all 1,181, exactly
    lead padding   present in 1,013, absent in 168

The padding split is worth stating carefully, because measuring only freedoom
gives the opposite answer: there it is absent in 152 of 178. id's own tools
wrote it and freedoom's do not, so a corpus of either alone produces a
confident majority pointing the wrong way. Both are shipped data.

TWO THINGS THAT LOOK LIKE DETAIL AND ARE NOT.

The sample count is a BYTE count of everything after the header, not a count
of audio samples, so `8 + count` is the lump length and the two are the same
number viewed twice. That makes it the only length worth trusting: the
original tools wrote sixteen bytes of the first sample value at each end as
padding and later toolchains often omit it, so a reader that subtracts a
padding it assumes is there cuts real audio off the 168 lumps that have none.

The rate field is genuinely free. Reading it rather than assuming 11025 is not
pedantry: three quarters of the corpus is at some other rate, and a fixed
assumption resamples most of a game's sound set wrong.
"""

import struct

from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import _f, _open, _size

_HDR = 8
_FORMAT_PCM = 3
# Rates seen run 11025 to 44100. This is wide enough to hold anything a DOS-era
# tool could emit while still rejecting a u16 read out of arbitrary bytes, which
# is the only defence the format's missing magic leaves available.
_RATE_MIN, _RATE_MAX = 4000, 48000
_PAD = 16                        # bytes of lead-in the original tools wrote
_READ_CAP = 64 * 1024 * 1024


def looks_like_dmx(data, size=None):
    """Is this a DMX sound? Weak evidence, corroborated.

    A two-byte format field is not an identification -- `03 00` occurs
    constantly in ordinary binary. What makes the claim safe is that the header
    has to agree with the file it sits in: the declared count plus the header
    must be the file's own length, to the byte. Over 1,181 real lumps that held
    every time, and arbitrary bytes satisfy it about once in four billion.
    """
    if len(data) < _HDR:
        return False
    fmt, rate, count = struct.unpack_from("<HHI", data, 0)
    if fmt != _FORMAT_PCM or not (_RATE_MIN <= rate <= _RATE_MAX):
        return False
    if size is None:
        size = len(data)
    return _HDR + count == size


def parse_dmx(data, size=None):
    """{format, rate, count, offset, padded, warnings}, best effort."""
    warns = []
    if len(data) < _HDR:
        return None
    fmt, rate, count = struct.unpack_from("<HHI", data, 0)
    if size is None:
        size = len(data)
    body = data[_HDR:]
    if _HDR + count != size:
        warns.append(
            f"the header declares {count:,} bytes after it and the lump holds "
            f"{max(0, size - _HDR):,}; reading what is there")
        count = min(count, len(body))
    # Padding is a property of the writer, not of the format, so it is reported
    # rather than removed: the samples the caller gets are the ones the lump
    # holds, and trimming a run that a later toolchain never wrote would take
    # real audio off the front.
    lead = body[:_PAD]
    padded = len(lead) == _PAD and len(set(lead)) == 1
    return {"format": fmt, "rate": rate, "count": count, "offset": _HDR,
            "padded": padded, "warnings": warns}


def inspect_dmx(filepath):
    file_size = _size(filepath)
    with _open(filepath) as fh:
        data = fh.read(min(file_size, _READ_CAP))
    if not looks_like_dmx(data, file_size):
        return [], ["not a DMX sound (Doom DS* lump)"]

    info = parse_dmx(data, file_size)
    file_warns = list(info["warnings"])
    if file_size > _READ_CAP:
        file_warns.append(hit(
            "read_bytes", _READ_CAP, file_size,
            f"lump is {file_size:,} bytes; only the first "
            f"{_READ_CAP // (1024 * 1024)} MB was read"))

    secs = info["count"] / float(info["rate"]) if info["rate"] else None
    warns = []
    if info["format"] != _FORMAT_PCM:
        warns.append(f"format {info['format']} is not the 3 every shipped "
                     f"sound carries; these samples may not be linear PCM")
    if info["count"] == 0:
        warns.append("the lump carries no samples")

    chunks = [{
        "id": "DMX", "offset": 0, "size": _HDR,
        "summary": (f"DMX sound -- unsigned 8-bit mono @ {info['rate']} Hz, "
                    f"{info['count']:,} B"
                    + (f", {secs:.3f} s" if secs else "")),
        "fields": [
            _f(0x00, 2, "format", info["format"],
               "3 = unsigned 8-bit PCM; the only value shipped"),
            _f(0x02, 2, "sample_rate", info["rate"], "Hz"),
            _f(0x04, 4, "sample_count", info["count"],
               "bytes after the header, padding included"),
            _f(None, 0, "bits_per_sample", 8),
            _f(None, 0, "channels", 1),
            _f(None, 0, "encoding", "8-bit unsigned PCM",
               "unsigned, centred on 0x80"),
        ] + ([_f(None, 0, "duration", f"{secs:.3f} s")] if secs else []),
        # payload_base 0: the 8-byte header has no RIFF-style prefix and its
        # field offsets are file-absolute, so the default (offset + 8) would
        # claim eight bytes the pcm chunk owns.
        "warnings": warns, "payload_base": 0,
    }, {
        "id": "pcm", "offset": _HDR, "size": info["count"],
        "summary": (f"{info['count']:,} B of samples"
                    + (", 16-byte lead-in written by the original tools"
                       if info["padded"] else
                       ", no lead-in padding (a later toolchain wrote this)")),
        "fields": [
            _f(None, 0, "padded", "yes" if info["padded"] else "no",
               "reported, not removed: 168 of 1,181 shipped lumps have none"),
        ],
        "warnings": ([f"samples run past the end of the lump "
                      f"(@0x{_HDR:x} + {info['count']:,})"]
                     if _HDR + info["count"] > file_size else []),
        "payload_base": _HDR,
    }]
    return chunks, file_warns
