"""Camelot wheel key utilities for harmonic mixing.

Given a key string like 'Am', 'C#', 'F minor', or '5A', returns the
Camelot code (1A-12B) and its harmonically compatible neighbors:
same code (exact match), relative major/minor, perfect 4th, perfect 5th.
"""

import re


_PITCH_CLASS = {
    "c": 0, "b#": 0,
    "c#": 1, "db": 1,
    "d": 2,
    "d#": 3, "eb": 3,
    "e": 4, "fb": 4,
    "f": 5, "e#": 5,
    "f#": 6, "gb": 6,
    "g": 7,
    "g#": 8, "ab": 8,
    "a": 9,
    "a#": 10, "bb": 10,
    "b": 11, "cb": 11,
}

# Camelot: (pitch_class, mode) -> code
# mode: 0 = major (B), 1 = minor (A)
# Standard Camelot wheel mapping.
_CAMELOT_MAP = {
    # major (B)
    (11, 0): "1B",   # B
    (6,  0): "2B",   # F#
    (1,  0): "3B",   # Db
    (8,  0): "4B",   # Ab
    (3,  0): "5B",   # Eb
    (10, 0): "6B",   # Bb
    (5,  0): "7B",   # F
    (0,  0): "8B",   # C
    (7,  0): "9B",   # G
    (2,  0): "10B",  # D
    (9,  0): "11B",  # A
    (4,  0): "12B",  # E
    # minor (A)
    (8,  1): "1A",   # G#m / Abm
    (3,  1): "2A",   # Ebm / D#m
    (10, 1): "3A",   # Bbm
    (5,  1): "4A",   # Fm
    (0,  1): "5A",   # Cm
    (7,  1): "6A",   # Gm
    (2,  1): "7A",   # Dm
    (9,  1): "8A",   # Am
    (4,  1): "9A",   # Em
    (11, 1): "10A",  # Bm
    (6,  1): "11A",  # F#m
    (1,  1): "12A",  # C#m
}


def parse_key(key_str):
    """Parse a key string to (pitch_class, mode) where mode is 0=major, 1=minor.

    Handles: 'C', 'Cm', 'C#m', 'Db', 'A minor', 'F#min', 'Bb maj',
    MIDI-note-ish values like 'C4' (treated as C), Camelot codes like '5A'.
    Returns None if parsing fails.
    """
    if not key_str:
        return None
    s = str(key_str).strip()
    if not s:
        return None

    # Camelot code (e.g. 8A)
    m = re.match(r"^\s*(\d{1,2})\s*([ABab])\s*$", s)
    if m:
        num = int(m.group(1))
        letter = m.group(2).upper()
        if 1 <= num <= 12:
            # inverse lookup
            for (pc, mode), code in _CAMELOT_MAP.items():
                if code == f"{num}{letter}":
                    return pc, mode
        return None

    # note + optional 'm'/'minor'/'maj' (ignore octave numbers like C4)
    m = re.match(
        r"^\s*([A-Ga-g])([#b]?)\s*(m|min|minor|maj|major|M)?\s*(\d+)?\s*$",
        s,
    )
    if not m:
        return None
    root = (m.group(1) + (m.group(2) or "")).lower()
    suffix = (m.group(3) or "").lower()
    pc = _PITCH_CLASS.get(root)
    if pc is None:
        return None
    if suffix in ("m", "min", "minor"):
        mode = 1
    elif suffix in ("maj", "major", "M".lower()):
        mode = 0
    elif suffix == "":
        mode = 0
    else:
        mode = 0
    return pc, mode


def key_to_camelot(key_str):
    """Return 'NNL' Camelot code (e.g. '8A') or None if unparseable."""
    parsed = parse_key(key_str)
    if parsed is None:
        return None
    return _CAMELOT_MAP.get(parsed)


def _split_camelot(code):
    m = re.match(r"^(\d{1,2})([AB])$", code)
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def camelot_neighbors(code):
    """Return harmonic neighbors for a Camelot code.

    Returns a list of codes: [same, relative, perfect_fourth, perfect_fifth].
    Empty list if code is invalid.
    """
    split = _split_camelot(code)
    if split is None:
        return []
    num, letter = split
    other = "B" if letter == "A" else "A"
    down = 12 if num == 1 else num - 1
    up = 1 if num == 12 else num + 1
    return [
        f"{num}{letter}",
        f"{num}{other}",
        f"{down}{letter}",
        f"{up}{letter}",
    ]


def compatible_keys(key_str):
    """Return the set of key strings (normalized) harmonically compatible
    with key_str. The set includes key_str's own normalized form.

    Each element is a canonical pretty name like 'Am' or 'C'.
    """
    code = key_to_camelot(key_str)
    if code is None:
        return set()
    codes = camelot_neighbors(code)
    # reverse lookup -> (pc, mode) -> pretty name
    pretty = set()
    for c in codes:
        for (pc, mode), kc in _CAMELOT_MAP.items():
            if kc == c:
                pretty.add(pitch_class_to_name(pc, mode))
                break
    return pretty


_NOTE_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F",
                     "F#", "G", "G#", "A", "A#", "B"]


def pitch_class_to_name(pc, mode):
    """Return 'C', 'C#', 'Am', etc. for (pitch_class, mode)."""
    name = _NOTE_NAMES_SHARP[pc % 12]
    return name + ("m" if mode == 1 else "")


# enharmonic equivalents for each canonical sharp spelling
_ENHARMONICS = {
    "C":  {"C", "B#"},
    "C#": {"C#", "Db"},
    "D":  {"D"},
    "D#": {"D#", "Eb"},
    "E":  {"E", "Fb"},
    "F":  {"F", "E#"},
    "F#": {"F#", "Gb"},
    "G":  {"G"},
    "G#": {"G#", "Ab"},
    "A":  {"A"},
    "A#": {"A#", "Bb"},
    "B":  {"B", "Cb"},
}


def enharmonic_spellings(key_str):
    """Return every string form that is enharmonically equivalent to key_str.

    Used as a safety net when filtering a DB that may hold either
    flat or sharp spellings. Returns a set including key_str itself;
    empty set if key_str can't be parsed.
    """
    parsed = parse_key(key_str)
    if parsed is None:
        return set()
    pc, mode = parsed
    canonical = _NOTE_NAMES_SHARP[pc]
    suffix = "m" if mode == 1 else ""
    out = {name + suffix for name in _ENHARMONICS.get(canonical, {canonical})}
    out.add(str(key_str).strip())
    return out
