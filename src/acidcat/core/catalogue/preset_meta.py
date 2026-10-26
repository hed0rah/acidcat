"""Unified preset-metadata extraction for indexing and search.

The per-format walkers (Bitwig, Native Instruments, Vital, DX7 SysEx, Serum 1)
each return their own key names. This normalizes them into one shape so the
index, query, and MCP can treat every preset the same way:

    {preset_name, device, product, creator, category, description, tags}

`tags` is a list; every other field is a string or None. `extract` returns None
for bytes that are not a recognized preset.
"""

from acidcat.core.formats import bitwig as bwmod
from acidcat.core.formats import ni as nimod
from acidcat.core.formats import serum as serummod
from acidcat.core.formats import syx as syxmod
from acidcat.core.formats import vital as vitalmod


def _split_tags(s):
    if not s:
        return []
    # a bank chain joined " / " is not a tag "/"
    return [t for t in s.replace(",", " ").split() if t and t != "/"]


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
        if nimod.is_ni_hsin(data):
            # an hsin SoundInfoItem's bank chain is whole names, product
            # first ("FM8", "-SETUP-.SYX"), and its attribute tags are whole
            # tags ("Synth Lead"): kept whole, not split into words
            chain = [s.strip() for s in (m.get("bank") or "").split(" / ")]
            tags = [s for s in chain[1:] if s] + list(m.get("attributes") or [])
        else:
            # an NKS preset's types and characters are whole tags too
            tags = _split_tags(m.get("bank")) + list(m.get("attributes") or [])
        desc = m.get("comment") or m.get("description")
        if not desc and m.get("snapshots"):
            # a Reaktor ensemble's name is not in it; its snapshots' are, and
            # they are what a search wants, as a bank's voices are
            snaps = m["snapshots"]
            desc = "%d snapshot%s: %s" % (len(snaps), "" if len(snaps) == 1 else "s",
                                          ", ".join(snaps))
        return {
            "preset_name": m.get("name"),
            "device": m.get("plugin") or m.get("product"),
            "product": m.get("product") or m.get("plugin") or m.get("vendor"),
            "creator": m.get("author") or m.get("vendor"),
            "category": m.get("device_type") or m.get("genre"),
            "description": desc,
            "tags": list(dict.fromkeys(tags)),
        }

    names = syxmod.dx7_voices(data)
    if names:  # Yamaha DX7 SysEx: one voice, or a bank of 32
        names = [n.strip() for n in names]
        single = len(names) == 1
        return {
            "preset_name": names[0] if single else None,
            "device": "DX7",
            "product": "Yamaha DX7",
            "creator": None,
            "category": None,
            # a bank has no name of its own; its voices are what a search wants
            "description": None if single else "%d voices: %s" % (
                len(names), ", ".join(names)),
            "tags": [],
        }

    m = serummod.preset_info(data)
    if m:  # Xfer Serum 1: a VST2 .fxp whose chunk holds the patch record
        return {
            # the record's name is whole; the CcnK header keeps 28 bytes of it
            "preset_name": m.get("name") or m.get("header_name") or None,
            "device": "Serum",
            "product": "Xfer Serum",
            "creator": m.get("author") or None,
            "category": None,
            "description": m.get("description") or None,
            "tags": [],
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
