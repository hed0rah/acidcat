"""
AIFF/IFF primitives.

Big-endian chunk-based format, Apple's counterpart to RIFF/WAV.
The lenient chunk traversal, the 80-bit extended-float decode, and the
shared value tables the AIFF walker (core/walk/aiff.py) consumes.
"""

import math
import os
import struct


# AIFC compression types in common circulation. Apple's spec defines
# NONE/sowt/raw /twos/in24/in32/fl32/fl64/alaw/ulaw; this set covers
# what real-world tools emit. Unknown values are surfaced as
# "unknown:<raw>" so we never silently mistreat them as PCM.
_AIFC_KNOWN_COMPRESSION = frozenset({
    "NONE", "none", "sowt", "raw ", "twos", "in24", "in32",
    "fl32", "fl64", "alaw", "ulaw", "FL32", "FL64",
    "MAC3", "MAC6", "ima4", "QDMC", "QDM2", "Qclp",
})

# INST sustain/release loop play mode (a big-endian int16)
_LOOP_MODES = {0: "off", 1: "forward", 2: "ping-pong"}

# AESD channel-status byte 0 enums (AES3)
_AES_RATES = {0: "unindicated", 1: "48000", 2: "44100", 3: "32000"}
_AES_EMPHASIS = {0b000: "unindicated", 0b100: "none",
                 0b110: "50/15 us", 0b111: "CCITT J.17"}



def _parse_ieee_extended(data):
    """
    Parse 80-bit IEEE 754 extended precision float (big-endian).
    Used for AIFF sample rate in COMM chunk.
    """
    if len(data) < 10:
        return 0.0
    exponent = ((data[0] & 0x7F) << 8) | data[1]
    mantissa = 0
    for i in range(2, 10):
        mantissa = (mantissa << 8) | data[i]
    sign = -1 if data[0] & 0x80 else 1
    if exponent == 0 and mantissa == 0:
        return 0.0
    elif exponent == 0x7FFF:
        # all-ones exponent is IEEE inf/NaN. neither is a usable sample
        # rate, and int(inf) downstream raised OverflowError, turning
        # the whole COMM chunk into a parse error. treat as unset.
        return 0.0
    else:
        f = mantissa / (1 << 63)
        try:
            f = f * (2.0 ** (exponent - 16383))
        except OverflowError:
            # a forged near-max exponent overflows a double the same
            # way the inf sentinel does; same treatment.
            return 0.0
        if not math.isfinite(f):
            return 0.0
        return f * sign


def is_aiff(filepath):
    """Check if file is AIFF/AIFC format."""
    try:
        with open(filepath, "rb") as f:
            header = f.read(12)
            if len(header) < 12:
                return False
            return (header[0:4] == b"FORM" and
                    header[8:12] in (b"AIFF", b"AIFC"))
    except Exception:
        return False


def iter_chunks(filepath):
    """Yield (chunk_id_str, offset, size) for each chunk in an AIFF file."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        header = f.read(12)
        if len(header) < 12 or header[0:4] != b"FORM":
            return
        form_type = header[8:12].decode("ascii", errors="ignore")
        if form_type not in ("AIFF", "AIFC"):
            return
        pos = 12
        while pos + 8 <= file_size:
            f.seek(pos)
            ch = f.read(8)
            if len(ch) < 8:
                break
            cid = ch[0:4].decode("ascii", errors="ignore")
            try:
                csz = struct.unpack(">I", ch[4:8])[0]
            except struct.error:
                break
            yield (cid, pos, csz)
            pos += 8 + csz
            if csz % 2 == 1:
                pos += 1  # word alignment
