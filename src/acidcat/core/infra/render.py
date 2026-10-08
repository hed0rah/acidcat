"""Output rendering -- turn result records into a chosen representation.

One registry (RENDERERS) maps an output-format name to a renderer; ``output``
dispatches through it and ``register_format`` adds a new one, so a format lights
up in every command's ``--output-format`` choices at once. This is the "how it's
rendered" axis -- distinct from a file's *format* (its container/codec) and from
where output *goes* (a file via -o/--output, else stdout).
"""

import csv
import io
import json
import sys


def format_table(data, stream=None):
    """
    Print a human-readable key: value table to stream (default stdout).

    Args:
        data: dict or list of (key, value) tuples.
        stream: writable file object (default sys.stdout).
    """
    if stream is None:
        stream = sys.stdout

    if isinstance(data, dict):
        items = data.items()
    else:
        items = data

    max_key = max((len(str(k)) for k, _ in items), default=0)
    for key, value in (data.items() if isinstance(data, dict) else data):
        stream.write(f"{str(key):<{max_key + 1}} {value}\n")


def format_columns(rows, columns, stream=None, blank="-"):
    """One line per row: `columns` is [(key, heading)], each column as wide as
    its widest cell, the last one never padded. For per-file listings over a
    tree, where a key: value record per file is thousands of lines."""
    if stream is None:
        stream = sys.stdout
    cells = [[heading for _k, heading in columns]]
    for r in rows:
        cells.append([blank if r.get(k) in (None, "") else str(r.get(k))
                      for k, _h in columns])
    widths = [max(len(row[i]) for row in cells) for i in range(len(columns))]
    for row in cells:
        stream.write("  ".join(c.ljust(w) for c, w in zip(row[:-1], widths))
                     + ("  " if len(row) > 1 else "") + row[-1] + "\n")


def format_json(data, stream=None, indent=2):
    """Write data as JSON to stream (default stdout)."""
    if stream is None:
        stream = sys.stdout
    json.dump(data, stream, indent=indent, default=str)
    stream.write("\n")


def format_csv_rows(rows, fieldnames, stream=None):
    """Write rows as CSV to stream (default stdout)."""
    if stream is None:
        stream = sys.stdout
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)


def _render_table(data, stream):
    if isinstance(data, list):
        for i, item in enumerate(data):
            if i > 0:
                stream.write("\n")
            if isinstance(item, dict):
                format_table(item, stream)
            else:
                stream.write(str(item) + "\n")
    elif isinstance(data, dict):
        format_table(data, stream)
    else:
        stream.write(str(data) + "\n")


def _render_json(data, stream):
    format_json(data, stream)


def _delimited(data, stream, delimiter):
    """CSV/TSV: union of keys across records, in first-seen order."""
    if isinstance(data, dict):
        data = [data]
    if not (isinstance(data, list) and data):
        return
    keys, seen = [], set()
    for row in data:
        for k in row:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    writer = csv.DictWriter(stream, fieldnames=keys, delimiter=delimiter,
                            lineterminator="\n")
    writer.writeheader()
    writer.writerows(data)


def _render_csv(data, stream):
    _delimited(data, stream, ",")


def _render_tsv(data, stream):
    _delimited(data, stream, "\t")


# name -> renderer(data, stream). register_format() extends this, so a new format
# appears in every command's --output-format choices at once.
RENDERERS = {
    "table": _render_table,
    "json": _render_json,
    "csv": _render_csv,
    "tsv": _render_tsv,
}


def register_format(name, fn):
    """Register a new output format: fn(data, stream) writes the rendering."""
    RENDERERS[name] = fn


def output_formats():
    """The registered output-format names, in registration order."""
    return tuple(RENDERERS)


def output(data, fmt="table", stream=None):
    """Render ``data`` (a dict or list of dicts) in ``fmt`` to ``stream``.

    ``fmt`` is any registered output format (see output_formats()); the default
    is the human-readable table. Raises ValueError on an unknown format.
    """
    if stream is None:
        stream = sys.stdout
    renderer = RENDERERS.get(fmt)
    if renderer is None:
        raise ValueError(f"unknown output format: {fmt!r} "
                         f"(known: {', '.join(output_formats())})")
    renderer(data, stream)
