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
    except (ValueError, RecursionError, UnicodeDecodeError, MemoryError):
        return None
    if not isinstance(obj, dict):
        return None
    # distinguish a Vital preset from arbitrary JSON: require the Vital-specific
    # synth_version key ('settings' alone is too generic).
    if "synth_version" not in obj:
        return None
    return obj


_VITAL_EFFECTS = ("chorus", "compressor", "delay", "distortion", "eq",
                  "filter_fx", "flanger", "phaser", "reverb")


def deep_structure(obj):
    """Deconstruct a Vital preset's synth structure from the parsed dict:
    active oscillators, wavetable names, the LFO inventory, the effects chain,
    and the modulation matrix (source -> destination : amount). Returns a dict of
    lists, empty if there is no usable settings object."""
    s = obj.get("settings")
    if not isinstance(s, dict):
        return {}
    out = {}
    out["oscillators"] = sorted(
        {k[:-3] for k in s if k.startswith("osc_") and k.endswith("_on") and s[k]})
    wt = s.get("wavetables") or []
    out["wavetables"] = [w.get("name") for w in wt
                         if isinstance(w, dict) and w.get("name")]
    lfos = s.get("lfos") or []
    out["lfos"] = [l.get("name") for l in lfos if isinstance(l, dict)]
    out["effects"] = [fx for fx in _VITAL_EFFECTS if s.get(fx + "_on")]
    wired = []
    for i, m in enumerate(s.get("modulations") or [], 1):
        if isinstance(m, dict) and m.get("source") and m.get("destination"):
            amt = s.get(f"modulation_{i}_amount")
            wired.append((m["source"], m["destination"], amt))
    out["modulations"] = wired
    return out
