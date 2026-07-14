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


# top-level members Vital itself writes; anything else is an unvalidated
# side-channel a schema-reading loader leaves in place.
KNOWN_TOP_LEVEL = set(META_KEYS) | {"settings"}


def parse_vital_span(data):
    """Parse the leading JSON value of a Vital preset (bytes) and return
    (obj, end_byte) -- end_byte is just past the top-level value -- or (None, 0).
    Tolerant of trailing bytes: a preset with junk after the closing brace still
    parses (the walker flags the trailing span), matching tolerant loaders.
    RecursionError (a forged deeply nested object) is caught."""
    text = data.decode("utf-8", "replace")   # trailing binary junk -> replacement chars
    start = 0
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    try:
        obj, end = json.JSONDecoder().raw_decode(text, start)
    except (ValueError, RecursionError, MemoryError):
        return None, 0
    # require the Vital-specific synth_version key ('settings' alone is too generic)
    if not isinstance(obj, dict) or "synth_version" not in obj:
        return None, 0
    return obj, len(text[:end].encode("utf-8"))


def parse_vital(data):
    """Parse the JSON and confirm it is a Vital preset. Returns the dict, or
    None if it does not parse or lacks Vital markers. Tolerant of trailing bytes."""
    return parse_vital_span(data)[0]


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
