"""
Serum preset parser.

Xfer Serum presets use an 'XferJson' header followed by a JSON metadata
block, then binary wavetable/modulation data.
"""

import json
import os


def is_serum_preset(filepath):
    """Check if file is a Serum preset."""
    try:
        with open(filepath, "rb") as f:
            header = f.read(8)
            return header == b"XferJson"
    except Exception:
        return False


def parse_serum_preset(filepath):
    """
    Parse a Serum preset file and extract JSON metadata.

    Returns dict with: presetName, presetAuthor, presetDescription,
    product, productVersion, tags, hash, vendor, url, fileType.
    """
    meta = {}

    with open(filepath, "rb") as f:
        raw = f.read()

    # find the JSON block
    json_start = raw.find(b"{")
    if json_start < 0:
        return meta

    # raw_decode returns (object, end_index) in a single linear pass,
    # stopping at the first complete JSON object. avoids the O(n^2)
    # progressive-slice scan that prior versions used.
    try:
        text = raw[json_start:].decode("utf-8", errors="replace")
        parsed, _ = json.JSONDecoder().raw_decode(text)
    except (json.JSONDecodeError, ValueError):
        return meta

    for key in ("fileType", "presetName", "presetAuthor",
                "presetDescription", "product", "productVersion",
                "tags", "hash", "vendor", "url", "version"):
        if key in parsed:
            meta[key] = parsed[key]
    return meta
