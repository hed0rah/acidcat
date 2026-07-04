"""NI Compressed Wave (.ncw) structural walker: audio parameters from
the header; the compressed blocks are opaque."""

import os

from acidcat.core import ncw as ncwmod
from acidcat.core.walk.base import Unsupported as _Unsupported
from acidcat.core.walk.base import _f

def inspect_ncw(filepath):
    """Structural view of an NI Compressed Wave (.ncw) file: the audio
    parameters from the header. The compressed blocks are opaque."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        head = f.read(64)
    hdr = ncwmod.parse_header(head)
    if hdr is None:
        raise _Unsupported("not a valid NCW header")
    dur = hdr["num_samples"] / hdr["sample_rate"] if hdr["sample_rate"] else 0
    fields = [
        _f(0x08, 2, "channels", hdr["channels"]),
        _f(0x0A, 2, "bits_per_sample", hdr["bits"]),
        _f(0x0C, 4, "sample_rate", hdr["sample_rate"], "Hz"),
        _f(0x10, 4, "num_samples", hdr["num_samples"],
           f"{dur:.3f} s" if dur else ""),
    ]
    chunks = [{"id": "NCW", "offset": 0, "size": file_size,
               "summary": f"NI Compressed Wave, {hdr['bits']}-bit "
                          f"{hdr['channels']}ch {hdr['sample_rate']} Hz, "
                          f"{dur:.3f} s (compressed audio opaque)",
               "fields": fields, "warnings": [], "payload_base": 0}]
    return chunks, []
