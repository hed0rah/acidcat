"""NI Compressed Wave (.ncw) header reader.

Kontakt stores samples as NCW, a lossless Native Instruments codec. The header
carries the audio parameters (like a WAV fmt / FLAC STREAMINFO); the compressed
audio blocks are opaque here. Layout confirmed against Kontakt library .ncw.
"""

import struct

MAGIC = b"\x01\xa8\x9e\xd6"  # 4-byte NCW signature


def parse_header(data):
    """Return {channels, bits, sample_rate, num_samples} or None if the bytes
    are not a plausible NCW header (validated so a coincidental magic is not
    trusted)."""
    if len(data) < 0x14 or data[:4] != MAGIC:
        return None
    channels = struct.unpack_from("<H", data, 0x08)[0]
    bits = struct.unpack_from("<H", data, 0x0A)[0]
    rate = struct.unpack_from("<I", data, 0x0C)[0]
    num_samples = struct.unpack_from("<I", data, 0x10)[0]
    if not (1 <= channels <= 32) or bits not in (8, 16, 24, 32) \
            or not (8000 <= rate <= 384000):
        return None
    return {"channels": channels, "bits": bits, "sample_rate": rate,
            "num_samples": num_samples}
