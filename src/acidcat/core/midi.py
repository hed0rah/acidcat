"""
Standard MIDI File primitives.

The magic check, the VLQ decode, and the whole-file read cap the MIDI
walker (core/walk/midi.py) consumes. Event parsing lives in the walker.
"""

# bound the whole-file read: real SMFs are kilobytes to a few MB, and
# even pathological "black MIDI" renders stay well under this. a forged
# multi-GB .mid must not OOM the indexer (threat model is DoS).
MAX_SMF_BYTES = 256 * 1024 * 1024


def is_midi(filepath):
    """Check if file is a Standard MIDI File."""
    try:
        with open(filepath, "rb") as f:
            return f.read(4) == b"MThd"
    except Exception:
        return False


def _read_vlq(data, offset):
    """Read a variable-length quantity. Returns (value, new_offset)."""
    value = 0
    while offset < len(data):
        byte = data[offset]
        value = (value << 7) | (byte & 0x7F)
        offset += 1
        if not (byte & 0x80):
            break
    return value, offset
