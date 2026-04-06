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

    # the JSON ends before binary blob starts; try progressively larger slices
    # (typically the JSON is small, <2KB)
    max_search = min(len(raw), json_start + 10000)
    for end in range(json_start + 50, max_search):
        try:
            parsed = json.loads(raw[json_start:end])
            # extract the fields we care about
            for key in ("fileType", "presetName", "presetAuthor",
                        "presetDescription", "product", "productVersion",
                        "tags", "hash", "vendor", "url", "version"):
                if key in parsed:
                    meta[key] = parsed[key]
            return meta
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

    return meta
