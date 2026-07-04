"""Xfer Serum preset structural walker: the XferJson magic, the JSON
metadata block, and the opaque wavetable/modulation blob."""

import json
import os

from acidcat.core.walk.base import _f

def inspect_serum(filepath):
    """Structural view of an Xfer Serum preset: XferJson magic, the
    JSON metadata block, then opaque wavetable/modulation data."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        raw = f.read(min(file_size, 4 * 1024 * 1024))
    chunks = []
    file_warns = []

    chunks.append({"id": "magc", "offset": 0, "size": 8,
                   "summary": "XferJson signature",
                   "fields": [_f(0x00, 8, "magic", "XferJson")],
                   "warnings": [], "payload_base": 0})

    json_start = raw.find(b"{")
    if json_start < 0:
        file_warns.append("no JSON block after the magic")
        return chunks, file_warns

    text = raw[json_start:].decode("utf-8", errors="replace")
    # RecursionError: the json scanner recurses per nesting level, so a
    # forged preset with thousands of nested objects blows the stack.
    try:
        parsed, end = json.JSONDecoder().raw_decode(text)
    except (ValueError, RecursionError) as e:
        file_warns.append(f"JSON block does not parse: {e.__class__.__name__}: {e}")
        return chunks, file_warns

    fields = []
    for key in ("fileType", "presetName", "presetAuthor",
                "presetDescription", "product", "productVersion",
                "tags", "vendor", "version"):
        if key in parsed:
            val = parsed[key]
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            fields.append(_f(None, 0, key, str(val)[:80]))
    name = parsed.get("presetName") or "unnamed"
    # raw_decode's end is a CHARACTER offset into the decoded text; the
    # blob boundary is a BYTE offset, so re-encode the parsed region to
    # measure it. exact for valid UTF-8 (which valid JSON is); off only
    # when the JSON region itself held invalid bytes, where any offset
    # is best-effort.
    end_bytes = len(text[:end].encode("utf-8"))
    chunks.append({"id": "json", "offset": json_start, "size": end_bytes,
                   "summary": f"'{name}' metadata, {len(parsed)} keys",
                   "fields": fields, "warnings": []})

    blob_off = json_start + end_bytes
    chunks.append({"id": "blob", "offset": blob_off,
                   "size": file_size - blob_off,
                   "summary": f"wavetable/modulation data, "
                              f"{file_size - blob_off:,} bytes (opaque)",
                   "fields": [], "warnings": []})
    return chunks, file_warns
