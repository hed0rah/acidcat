"""
Serum preset primitives.

Xfer Serum presets use an 'XferJson' header followed by a JSON metadata
block, then binary wavetable/modulation data. Decoding lives in the
walker (core/walk/serum.py); this module keeps the magic check.
"""

from acidcat.core.infra.source import open_input



def is_serum_preset(filepath):
    """Check if file is a Serum preset."""
    try:
        with open_input(filepath) as f:
            header = f.read(8)
            return header == b"XferJson"
    except Exception:
        return False
