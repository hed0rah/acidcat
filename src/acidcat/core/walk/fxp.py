"""VST 2 .fxp preset walker: the 'CcnK' container header, its fxMagic (preset
kind), the plugin id (a FourCC), the version fields, and the preset name. All
multi-byte fields are big-endian. Preset payload (params or an opaque chunk) is
reported as a region, not decoded (it is plugin-specific)."""

import os
import struct

from acidcat.core.walk.base import _f

_FX_MAGIC = {
    b"FxCk": "regular preset (float params)",
    b"FPCh": "opaque-chunk preset",
    b"FxBk": "regular bank",
    b"FBCh": "opaque-chunk bank",
}
# a few common plugin ids (the FourCC each plugin registers); shown raw otherwise
_KNOWN_IDS = {b"XfsX": "Xfer Serum", b"NiMs": "NI Massive", b"syle": "LennarDigital Sylenth1"}


def _cstr(b):
    return b.split(b"\x00", 1)[0].decode("latin-1", errors="replace").strip()


def inspect_fxp(filepath):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        head = f.read(min(size, 65536))
    warns = []
    if head[:4] != b"CcnK":
        warns.append("missing CcnK magic")
    if len(head) < 28:
        return ([{"id": "fxp", "offset": 0, "size": size,
                  "summary": "truncated FXP header", "fields": [],
                  "warnings": ["header shorter than 28 bytes"],
                  "payload_base": 0}], warns)

    byte_size = struct.unpack_from(">I", head, 4)[0]
    fx_magic = head[8:12]
    version = struct.unpack_from(">I", head, 12)[0]
    fx_id = head[16:20]
    fx_version = struct.unpack_from(">I", head, 20)[0]
    num_programs = struct.unpack_from(">I", head, 24)[0]
    kind = _FX_MAGIC.get(fx_magic, "unknown fxMagic")
    id_str = fx_id.decode("latin-1", errors="replace")
    plugin = _KNOWN_IDS.get(fx_id)
    is_bank = fx_magic in (b"FxBk", b"FBCh")

    fields = [
        _f(0x00, 4, "magic", "CcnK"),
        _f(0x04, 4, "byte_size", f"{byte_size:,}", "bytes after this field"),
        _f(0x08, 4, "fx_magic", fx_magic.decode("latin-1", "replace"), kind),
        _f(0x0C, 4, "version", version),
        _f(0x10, 4, "plugin_id", id_str + (f" ({plugin})" if plugin else "")),
        _f(0x14, 4, "plugin_version", fx_version),
        _f(0x18, 4, "num_programs", num_programs),
    ]
    summary = f"VST {kind}, plugin {id_str}"
    name = ""
    if not is_bank and len(head) >= 56:
        name = _cstr(head[28:56])                      # 28-byte program name
        fields.append(_f(0x1C, 28, "preset_name", name or "(unnamed)"))
        if name:
            summary = f"'{name}', VST {kind}, plugin {id_str}"

    chunks = [{"id": "CcnK", "offset": 0, "size": size, "summary": summary,
               "fields": fields, "warnings": [], "payload_base": 0}]

    # opaque-chunk variants carry a length-prefixed plugin blob
    if fx_magic in (b"FPCh", b"FBCh"):
        cs_off = 156 if is_bank else 56               # bank has 128 reserved bytes first
        if len(head) >= cs_off + 4:
            chunk_size = struct.unpack_from(">I", head, cs_off)[0]
            data_off = cs_off + 4
            chunks.append({"id": "chunk", "offset": data_off,
                           "size": max(0, min(chunk_size, size - data_off)),
                           "summary": f"opaque plugin chunk, {chunk_size:,} bytes",
                           "fields": [], "warnings": []})
    return chunks, warns
