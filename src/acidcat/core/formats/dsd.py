"""DSD: one-bit audio, and the two containers that carry it.

Direct Stream Digital is what a Super Audio CD holds. Where PCM stores a
multi-bit sample per frame, DSD stores ONE bit per frame at a very high rate --
2,822,400 Hz for DSD64, which is 64x the CD rate and where the name comes from.
The bit is not an amplitude; it is the output of a sigma-delta modulator, and
the amplitude lives in the local density of ones.

That matters to a walker for one practical reason: **bits per sample is 1, and
almost every duration formula in an audio tool assumes it is 8 or more.** The
frame count is sample_count, the byte count is sample_count / 8 per channel,
and reading them as the same number is off by a factor of eight.

Two containers, from the two companies behind SACD, and they agree about
nothing:

  DSF  (Sony)     little-endian, flat, four blocks, no nesting. The metadata
                  block is a pointer to an ID3v2 tag at the end of the file.
  DFF  (Philips)  IFF, big-endian, and 64-bit sizes: `FRM8` where IFF has
                  `FORM`, because a one-bit stream outgrows a 32-bit length.
                  The spec says so in as many words -- "the ckDataSize is not
                  a long but a double ulong".

Both were read from their published specifications (Sony DSF 1.01, Philips
DSDIFF 1.5) and then checked against commercial SACD rips.
"""

import struct

DSF_MAGIC = b"DSD "
DSDIFF_MAGIC = b"FRM8"
DSDIFF_FORM_TYPE = b"DSD "

# The DSD rate family. Each is 64x, 128x, ... the 44,100 Hz CD rate, and the
# marketing name is that multiplier. Named because a bare 2,822,400 tells a
# reader nothing and "DSD64" tells them everything.
DSD_RATES = {
    2822400: "DSD64",
    5644800: "DSD128",
    11289600: "DSD256",
    22579200: "DSD512",
    45158400: "DSD1024",
    # the 48 kHz-derived family, rarer but legal
    3072000: "DSD64 (48 kHz base)",
    6144000: "DSD128 (48 kHz base)",
    12288000: "DSD256 (48 kHz base)",
}

# DSF channel types. The low number is not the channel COUNT -- type 4 is quad
# and type 5 is four channels with a different assignment -- so the two fields
# are reported separately and checked against each other.
DSF_CHANNEL_TYPES = {
    1: "mono",
    2: "stereo",
    3: "3 channels",
    4: "quad",
    5: "4 channels",
    6: "5 channels",
    7: "5.1 channels",
}

# The channel layout each type implies, from the spec's own annotation. Used to
# say what the channels ARE, not merely how many.
#
# TYPES 4 AND 5 ARE EASY TO TRANSPOSE and MediaInfoLib does. Sony's Annotation
# 1 lists the layouts in enum order and is unambiguous: QUAD comes fourth and
# is "Front Left, Front Right, Back Left, Back Right"; 4-CHANNELS comes fifth
# and is "Front Left, Front Right, Center, Low Frequency". MediaInfoLib's
# table has the LFE layout at index 4 and the back-pair layout at index 5,
# which is the other way round.
#
# Both are four channels, so a file reads and plays either way -- and the
# centre channel comes out of a back speaker. This table follows the spec.
DSF_CHANNEL_LAYOUTS = {
    1: ("C",),
    2: ("FL", "FR"),
    3: ("FL", "FR", "C"),
    4: ("FL", "FR", "BL", "BR"),
    5: ("FL", "FR", "C", "LFE"),
    6: ("FL", "FR", "C", "BL", "BR"),
    7: ("FL", "FR", "C", "LFE", "BL", "BR"),
}

# DSDIFF channel ids, four characters each, from the spec.
DSDIFF_CHANNEL_IDS = {
    b"SLFT": "stereo left", b"SRGT": "stereo right",
    b"MLFT": "multi left", b"MRGT": "multi right",
    b"C   ": "centre", b"LFE ": "LFE",
    b"LS  ": "left surround", b"RS  ": "right surround",
}

# DSDIFF compression types. DST is the lossless codec SACD uses to fit a disc;
# an uncompressed DFF is raw DSD.
DSDIFF_COMPRESSION = {
    b"DSD ": "uncompressed",
    b"DST ": "DST lossless",
}

# The DSF block is fixed and zero-filled to its end, so the final block is not
# short -- it is padded. A reader that treats the tail as audio emits noise.
DSF_BLOCK_SIZE = 4096

DSF_HEADER_SIZE = 28
DSF_FMT_SIZE = 52


def is_dsf(head):
    """True if the bytes open a Sony DSF."""
    return len(head) >= 12 and head[:4] == DSF_MAGIC and \
        struct.unpack_from("<Q", head, 4)[0] == DSF_HEADER_SIZE


def is_dsdiff(head):
    """True if the bytes open a Philips DSDIFF.

    The form type is checked as well as the magic: FRM8 is the 64-bit IFF
    container, and DSDIFF is one thing it can hold.
    """
    return (len(head) >= 16 and head[:4] == DSDIFF_MAGIC
            and head[12:16] == DSDIFF_FORM_TYPE)


def dsf_header(data):
    """Decode the DSF header blocks. Returns a dict, or None if malformed.

    Reads the first 80 bytes: the DSD block (size, total file size, metadata
    pointer) and the fmt block (version, format id, channel type and count,
    rate, bit depth, sample count, block size).
    """
    if not is_dsf(data) or len(data) < DSF_HEADER_SIZE + 12:
        return None
    total, meta = struct.unpack_from("<QQ", data, 12)
    if data[28:32] != b"fmt ":
        return None
    fmt_size = struct.unpack_from("<Q", data, 32)[0]
    if len(data) < 40 + 40:
        return None
    version, format_id, ctype, channels, rate, bits = \
        struct.unpack_from("<IIIIII", data, 40)
    samples = struct.unpack_from("<Q", data, 64)[0]
    block, reserved = struct.unpack_from("<II", data, 72)
    return {
        "total_size": total, "metadata_offset": meta, "fmt_size": fmt_size,
        "version": version, "format_id": format_id,
        "channel_type": ctype, "channels": channels,
        "sample_rate": rate, "bits_per_sample": bits,
        "sample_count": samples, "block_size": block, "reserved": reserved,
    }


def dsd_duration(sample_count, rate):
    """Seconds. sample_count is per channel and each sample is ONE bit."""
    return (sample_count / rate) if rate else None


def rate_name(rate):
    """"DSD64" for 2,822,400, or a bare multiplier when it is off the table."""
    named = DSD_RATES.get(rate)
    if named:
        return named
    if rate and rate % 44100 == 0:
        return f"{rate // 44100}x the CD rate"
    if rate and rate % 48000 == 0:
        return f"{rate // 48000}x 48 kHz"
    return ""
