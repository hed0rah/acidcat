"""
Serum preset primitives.

Xfer Serum presets use an 'XferJson' header followed by a JSON metadata
block, then binary wavetable/modulation data. Decoding lives in the
walker (core/walk/serum.py); this module keeps the magic check.
"""


def is_serum_preset(filepath):
    """Check if file is a Serum preset."""
    try:
        with open(filepath, "rb") as f:
            header = f.read(8)
            return header == b"XferJson"
    except Exception:
        return False
