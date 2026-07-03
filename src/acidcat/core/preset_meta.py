"""Unified preset-metadata extraction for indexing and search.

The per-format walkers (Bitwig, Native Instruments, Vital) each return their own
key names. This normalizes them into one shape so the index, query, and MCP can
treat every preset the same way:

    {preset_name, device, product, creator, category, description, tags}

`tags` is a list; every other field is a string or None. `extract` returns None
for bytes that are not a recognized preset.
"""

from acidcat.core import bitwig as bwmod
from acidcat.core import ni as nimod
from acidcat.core import vital as vitalmod


def _split_tags(s):
    if not s:
        return []
    return [t for t in s.replace(",", " ").split() if t]


def extract(data):
    """Normalized preset metadata from raw file bytes, or None."""
    if data[:4] == bwmod.MAGIC:  # Bitwig BtWg
        m = bwmod.parse_meta(data)
        if not m:
            return None
        return {
            "preset_name": m.get("device_name"),
            "device": m.get("device_name"),
            "product": "Bitwig",
            "creator": m.get("creator"),
            "category": m.get("device_category") or m.get("preset_category"),
            "description": m.get("comment"),
            "tags": _split_tags(m.get("tags")),
        }

    if nimod.is_ni_hsin(data) or nimod.is_ni_ksd(data) or nimod.is_ni_nksf(data):
        if nimod.is_ni_ksd(data):
            m = nimod.parse_ksd(data)
        elif nimod.is_ni_nksf(data):
            m = nimod.parse_nksf(data)
        else:
            m = nimod.parse_hsin(data)
        if not m:
            return None
        return {
            "preset_name": m.get("name"),
            "device": m.get("plugin") or m.get("product"),
            "product": m.get("product") or m.get("plugin") or m.get("vendor"),
            "creator": m.get("author") or m.get("vendor"),
            "category": m.get("device_type") or m.get("genre"),
            "description": m.get("comment") or m.get("description"),
            "tags": _split_tags(m.get("bank")),
        }

    if data[:1] == b"{" and b'"synth_version"' in data:  # Vital
        m = vitalmod.parse_vital(data)
        if not m:
            return None
        return {
            "preset_name": m.get("preset_name"),
            "device": "Vital",
            "product": "Vital",
            "creator": m.get("author"),
            "category": m.get("preset_style"),
            "description": m.get("comments"),
            "tags": _split_tags(m.get("preset_style")),
        }

    return None
