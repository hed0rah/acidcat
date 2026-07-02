"""Vital preset reader.

A Vital .vital preset is bare UTF-8 JSON: metadata at the top level and the
full synth state (oscillators, wavetables as base64, modulations) under
"settings". This pulls the top-level metadata; the settings blob is opaque.
"""

import json

# top-level string metadata keys, in display order
META_KEYS = [
    "preset_name", "author", "comments", "preset_style", "synth_version",
    "macro1", "macro2", "macro3", "macro4",
]


def parse_vital(data):
    """Parse the JSON and confirm it is a Vital preset. Returns the dict, or
    None if it does not parse or lacks Vital markers. RecursionError (a forged
    deeply nested object) is caught, matching the Serum walker."""
    try:
        obj = json.loads(data)
    except (ValueError, RecursionError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    # distinguish a Vital preset from arbitrary JSON
    if "synth_version" not in obj and "settings" not in obj:
        return None
    return obj
