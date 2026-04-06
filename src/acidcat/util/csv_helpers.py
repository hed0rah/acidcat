"""CSV output helpers."""

import os
import re


def safe_basename_for_csv(path_basename):
    """
    Preserve directory portion; slugify only the basename.
    Ensures parent directories exist and .csv suffix is present.
    """
    path_norm = os.path.normpath(path_basename)
    dirpart, base = os.path.split(path_norm)
    base_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", base.strip()) or "output.csv"
    if not base_slug.lower().endswith(".csv"):
        base_slug += ".csv"
    if dirpart:
        os.makedirs(dirpart, exist_ok=True)
        return os.path.join(dirpart, base_slug)
    return base_slug
