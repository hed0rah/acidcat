"""Vital preset structural walker (bare JSON): top-level metadata and,
in deep mode, the synth structure and modulation matrix."""

import os

from acidcat.core import vital as vitalmod
from acidcat.core.walk.base import Unsupported as _Unsupported
from acidcat.core.walk.base import _f

def inspect_vital(filepath, deep=False):
    """Structural view of a Vital preset (bare JSON): the top-level metadata,
    and with deep (--verbose or --frames) the full synth structure, active
    oscillators + wavetables, LFO inventory, effects chain, and the modulation
    matrix."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(min(file_size, 32 * 1024 * 1024))
    # a fast bytes search for the marker before the full JSON parse: an
    # arbitrary large JSON file that merely starts with '{' is rejected without
    # paying for json.loads. the substring may sit anywhere (some presets emit
    # the big 'settings' object before synth_version).
    if b'"synth_version"' not in data:
        raise _Unsupported("not a Vital preset (no synth_version marker)")
    obj, jend = vitalmod.parse_vital_span(data)
    if obj is None:
        raise _Unsupported("not a Vital preset (JSON did not parse or lacks "
                           "the synth_version key)")
    warns = []
    # bytes after the top-level JSON value are trailing data -- a tolerant loader
    # ignores them and the preset still loads, so warn instead of rejecting.
    if data[jend:].strip():
        warns.append(f"{len(data) - jend:,} bytes of trailing data after the "
                     "top-level JSON value (ignored by the parser; still loads)")
    # top-level members outside the Vital schema are an unvalidated side-channel
    unknown = sorted(k for k in obj if k not in vitalmod.KNOWN_TOP_LEVEL)
    if unknown:
        warns.append("unvalidated top-level key(s) outside the Vital schema: "
                     + ", ".join(unknown))
    fields = []
    for k in vitalmod.META_KEYS:
        v = obj.get(k)
        if v is not None and not isinstance(v, (dict, list)):
            fields.append(_f(None, 0, k, str(v)[:200]))
    settings = obj.get("settings")
    nkeys = len(settings) if isinstance(settings, dict) else 0
    name = obj.get("preset_name") or "unnamed"
    chunks = [{"id": "vital", "offset": 0, "size": file_size,
               "summary": f"'{name}' by {obj.get('author', '?')}, "
                          f"{nkeys} settings keys",
               "fields": fields, "warnings": warns}]
    if deep:
        st = vitalmod.deep_structure(obj)
        engine = []
        if st.get("oscillators"):
            wt = ", ".join(st["wavetables"]) if st.get("wavetables") else ""
            engine.append(_f(None, 0, "oscillators",
                             ", ".join(st["oscillators"]), wt))
        if st.get("lfos"):
            engine.append(_f(None, 0, "lfos",
                             f"{len(st['lfos'])}: " + ", ".join(st["lfos"])))
        if st.get("effects"):
            engine.append(_f(None, 0, "effects chain",
                             " > ".join(st["effects"])))
        if engine:
            chunks.append({"id": "engine", "offset": 0, "size": 0,
                           "summary": "active synth structure",
                           "fields": engine, "warnings": []})
        mods = st.get("modulations") or []
        if mods:
            mfields = []
            for src, dst, amt in mods:
                note = f"amount {amt:g}" if isinstance(amt, (int, float)) else ""
                mfields.append(_f(None, 0, src, f"-> {dst}", note))
            chunks.append({"id": "modulation", "offset": 0, "size": 0,
                           "summary": f"{len(mods)} wired modulations "
                                      "(source -> destination)",
                           "fields": mfields, "warnings": []})
    return chunks, []
