"""Console audio streams: ADX, BRSTM, HPS and VAG.

Four formats in one module because they are the same kind of thing wearing
different headers. Each is a small fixed record describing one stream -- codec,
channel count, sample rate, length -- followed by ADPCM frames, and each already
had a decoder here before it had a walker.

WHAT IS SHARED AND WHAT IS NOT. The vocabulary is shared: `_stream` renders the
same five facts in the same order for every one of them, so `codec`, `channels`,
`sampleRate`, `frames` and `duration` mean the same thing and sit in the same
place regardless of which console wrote the file. That is worth having, because
the alternative is four walkers that each invent their own words for the same
number.

The LAYOUT is not shared and should not be. ADX is a header and a body. BRSTM is
a header pointing at named blocks. HPS is a header followed by a linked list of
blocks, each naming the next. VAG is a header and a body with a name field in
the middle of it. A single loop that covered all four would either be four loops
behind a flag or a description so loose it stopped being a description.

Every decoder these call was already here, used by `extract` to pull audio out.
The structure was being parsed and then thrown away; this reports it.
"""


from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import _f, _open, _size

# Each of these reads its whole header from a bounded prefix. The audio is not
# read at all, so the cap is a bound on the header search rather than on the
# answer, and no format below has a header anywhere near it.
_HEAD_CAP = 1 * 1024 * 1024


def _stream(codec, channels, rate, frames, extra=None):
    """The five facts every one of these formats states, in one order.

    Duration is derived rather than read: none of them store it, and a frame
    count without a rate beside it is a number nobody can use.
    """
    out = [
        _f(None, 0, "codec", codec, "how the sample data is encoded"),
        _f(None, 0, "channels", channels,
           "mono" if channels == 1 else ("stereo" if channels == 2 else "multichannel")),
        _f(None, 0, "sampleRate", "%s Hz" % format(rate, ",")),
        _f(None, 0, "frames", format(frames, ","), "samples per channel"),
    ]
    if rate:
        out.append(_f(None, 0, "duration", "%.3f s" % (frames / float(rate)),
                      "frames / sampleRate; not stored, derived"))
    return out + list(extra or [])


def _read(filepath):
    size = _size(filepath)
    with _open(filepath) as fh:
        return fh.read(min(size, _HEAD_CAP)), size


# ── CRI ADX ─────────────────────────────────────────────────────────

def inspect_adx(filepath, deep=False):
    """CRI ADX: a header whose own length is stored in it, then frames.

    The header ends where the copyright offset says, which is the format's one
    quirk worth knowing: the field at 0x02 points two bytes BEFORE the marker
    '(c)CRI', so the data begins at that value plus four.
    """
    from acidcat.core.codecs import adx
    raw, size = _read(filepath)
    warns = []
    try:
        h = adx.parse_header(raw)
    except Exception as exc:
        return [_broken(size, "ADX header did not parse: %s" % exc)], [str(exc)]

    body = max(0, size - h["data_offset"])
    enc = {0: "linear PCM", 2: "standard ADPCM", 3: "exponential",
           4: "AHX"}.get(h["enc_type"], "type %d" % h["enc_type"])
    fields = _stream(enc, h["channels"], h["rate"], h["samples"], [
        _f(0x00, 2, "magic", "0x8000", "two bytes; the (c)CRI marker is what confirms it"),
        _f(0x02, 2, "copyrightOffset", "0x%04X" % h["copyright_off"],
           "points two bytes before '(c)CRI'; data begins four bytes later",
           enc=">H", raw=h["copyright_off"], xref=h["data_offset"]),
        _f(0x05, 1, "blockSize", h["block_size"], "bytes per ADPCM block"),
        _f(0x06, 1, "bitDepth", h["bits"]),
        _f(0x10, 2, "highpass", "%d Hz" % h["highpass"],
           "the encoder's highpass cutoff, kept so a decoder can undo it"),
        _f(0x12, 1, "version", "0x%02X" % h["version"]),
    ])
    if h["data_offset"] > size:
        warns.append("the header declares data beyond the end of the file")
    return [
        {"id": "header", "offset": 0, "size": min(h["data_offset"], size),
         "summary": "CRI ADX, %s, %d ch at %s Hz"
                    % (enc, h["channels"], format(h["rate"], ",")),
         "fields": fields, "warnings": [], "payload_base": 0},
        {"id": "frames", "offset": min(h["data_offset"], size), "size": body,
         "summary": "%s bytes of ADPCM" % format(body, ","),
         "fields": [], "warnings": [], "payload_base": min(h["data_offset"], size)},
    ], warns


# ── Nintendo BRSTM ──────────────────────────────────────────────────

def inspect_brstm(filepath, deep=False):
    """BRSTM: an RSTM header pointing at named blocks.

    Unlike the others this one is a container. The 0x40-byte RSTM header holds
    offsets to HEAD, ADPC and DATA, and everything inside HEAD is addressed by
    references relative to HEAD's own start plus eight, not to the file.
    """
    from acidcat.core.codecs import brstm
    raw, size = _read(filepath)
    warns = []
    try:
        h = brstm.parse_header(raw)
    except Exception as exc:
        return [_broken(size, "BRSTM header did not parse: %s" % exc)], [str(exc)]

    audio = h.get("audio_off", 0)
    body = max(0, size - audio)
    fields = _stream("DSP-ADPCM", h["channels"], h["rate"], h["samples"], [
        _f(0x00, 4, "magic", "RSTM"),
        _f(None, 0, "blocks", format(h.get("blocks", 0), ","),
           "%s bytes each" % format(h.get("block_size", 0), ",")),
        _f(None, 0, "audioOffset", "0x%08X" % audio,
           "where the interleaved blocks begin", xref=audio),
        _f(None, 0, "coefficients", "%d x 16" % h["channels"],
           "one DSP-ADPCM predictor table per channel, carried in HEAD"),
    ])
    if audio > size:
        warns.append("the header points at audio beyond the end of the file")
    return [
        {"id": "header", "offset": 0, "size": min(audio, size),
         "summary": "Nintendo BRSTM, DSP-ADPCM, %d ch at %s Hz"
                    % (h["channels"], format(h["rate"], ",")),
         "fields": fields, "warnings": [], "payload_base": 0},
        {"id": "blocks", "offset": min(audio, size), "size": body,
         "summary": "%s bytes of interleaved DSP-ADPCM" % format(body, ","),
         "fields": [], "warnings": [], "payload_base": min(audio, size)},
    ], warns


# ── HAL PCM Stream ──────────────────────────────────────────────────

_HPS_CTX = 0x38


def inspect_hps(filepath, deep=False):
    """HPS: a header, then a linked list of blocks.

    Each block header carries its own size and the file offset of the next one,
    with 0xFFFFFFFF ending the chain. So the block layout is walked rather than
    computed, and a broken next-pointer truncates the stream rather than
    corrupting it.
    """
    import struct
    from acidcat.core.codecs import hps
    raw, size = _read(filepath)
    warns = []
    if raw[:8] != hps.MAGIC:
        return [_broken(size, "not a HAL PCM Stream")], ["missing ' HALPST\\0' magic"]

    if len(raw) < 0x10:
        # The magic is eight bytes and the rate and channel count are the
        # next eight. A file carrying the magic and nothing after it is
        # truncated rather than malformed, and unpacking past the end
        # would raise out of a walk instead of describing what is there.
        return ([_broken(size, "HAL PCM Stream header is truncated")],
                ["file ends after the magic, before the rate and channel count"])

    rate, channels = struct.unpack_from(">II", raw, 8)
    if not 1 <= channels <= 8:
        return [_broken(size, "implausible channel count %d" % channels)], \
               ["channel count %d is outside 1-8" % channels]
    head_end = 0x10 + channels * _HPS_CTX

    first = head_end
    # The chain is walked through the buffer, so a file longer than the cap has
    # its block count cut short. That is an answer being shortened, not just a
    # search, so it has to say so rather than report a smaller number as fact.
    capped = size > len(raw)
    chain, off, guard = [], first, 0
    while 0 <= off < size and off != 0xFFFFFFFF and guard < 100000:
        guard += 1
        if off + 12 > len(raw):
            break
        # The block header is 0x20 bytes: DSP size at +0, sample count at +4,
        # and the NEXT BLOCK OFFSET at +8. Reading the next pointer from +4
        # walks the chain into the sample count, which is not an offset.
        dsize, _samples, nxt = struct.unpack_from(">III", raw, off)
        chain.append((off, dsize))
        if nxt == 0xFFFFFFFF or nxt <= off:
            break
        off = nxt

    fields = _stream("DSP-ADPCM", channels, rate, 0, [
        _f(0x00, 8, "magic", "' HALPST\\0'"),
        # sampleRate and channels are NOT repeated here: the shared vocabulary
        # above already states them, and a field appearing twice under one name
        # makes a reader ask which of the two is authoritative.
        _f(0x10, channels * _HPS_CTX, "channelContexts",
           "%d x 0x%02X bytes" % (channels, _HPS_CTX),
           "each carries the 16 DSP coefficients at +0x10"),
        _f(None, 0, "blocks", len(chain),
           "walked through next-offsets, not computed from a count"),
    ])
    # frames is unknown without decoding, so it is not claimed
    fields = [x for x in fields if x["name"] not in ("frames", "duration")]
    if guard >= 100000:
        warns.append("block chain did not terminate; stopped after %d blocks" % guard)
    if capped:
        warns.append(hit("read_bytes", _HEAD_CAP, size,
                         "file is %d bytes; parsed the first %d, so the "
                         "block count is a lower bound" % (size, len(raw))))

    body = max(0, size - head_end)
    return [
        {"id": "header", "offset": 0, "size": min(head_end, size),
         "summary": "HAL PCM Stream, DSP-ADPCM, %d ch at %s Hz"
                    % (channels, format(rate, ",")),
         "fields": fields, "warnings": [], "payload_base": 0},
        {"id": "blocks", "offset": min(head_end, size), "size": body,
         "summary": "%d block(s), %s bytes" % (len(chain), format(body, ",")),
         "fields": [], "warnings": [], "payload_base": min(head_end, size)},
    ], warns


# ── Sony VAG ────────────────────────────────────────────────────────

_VAG_HEADER = 0x30


def inspect_vag(filepath, deep=False):
    """VAG: a header with a name in it, then SPU-ADPCM.

    The only one of the four that carries a human-readable name, and the only
    one whose declared size can legitimately be smaller than the bytes present:
    a VAG is often padded, and the field says which of the tail is real.
    """
    import struct
    from acidcat.core.codecs import vag
    raw, size = _read(filepath)
    warns = []
    try:
        h = vag.parse_vag(raw)
    except Exception as exc:
        return [_broken(size, "VAG header did not parse: %s" % exc)], [str(exc)]

    declared = struct.unpack_from(">I", raw, 0x0C)[0] if len(raw) >= 0x10 else 0
    present = max(0, size - _VAG_HEADER)
    # SPU-ADPCM is 16 bytes per 28 samples, one channel.
    frames = (min(declared, present) // 16) * 28
    fields = _stream("SPU-ADPCM", 1, h["rate"], frames, [
        _f(0x00, 4, "magic", "VAGp"),
        _f(0x04, 4, "version", "0x%08X" % h["version"], enc=">I", raw=h["version"]),
        _f(0x0C, 4, "dataSize", format(declared, ","),
           "bytes of ADPCM the header claims", enc=">I", raw=declared),
        _f(0x20, 16, "name", h["name"] or "(unnamed)",
           "a name field, which none of the others carry"),
    ])
    if declared > present:
        warns.append("dataSize claims %s bytes but only %s follow the header"
                     % (format(declared, ","), format(present, ",")))
    return [
        {"id": "header", "offset": 0, "size": min(_VAG_HEADER, size),
         "summary": "Sony VAG, SPU-ADPCM at %s Hz%s"
                    % (format(h["rate"], ","),
                       (", '%s'" % h["name"]) if h["name"] else ""),
         "fields": fields, "warnings": [], "payload_base": 0},
        {"id": "adpcm", "offset": min(_VAG_HEADER, size), "size": present,
         "summary": "%s bytes of SPU-ADPCM" % format(present, ","),
         "fields": [], "warnings": [], "payload_base": min(_VAG_HEADER, size)},
    ], warns


def _broken(size, why):
    return {"id": "header", "offset": 0, "size": min(size, 64), "summary": why,
            "fields": [], "warnings": [], "payload_base": 0}
