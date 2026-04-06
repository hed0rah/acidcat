"""
Output formatting: human-readable tables, JSON, and CSV.
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


def output(data, fmt="table", stream=None):
    """
    Dispatch to the right formatter.

    Args:
        data: dict (single record) or list of dicts (multiple records).
        fmt: 'table', 'json', or 'csv'.
        stream: writable file object.
    """
    if stream is None:
        stream = sys.stdout

    if fmt == "json":
        format_json(data, stream)
    elif fmt == "csv":
        if isinstance(data, list) and data:
            all_keys = []
            seen = set()
            for row in data:
                for k in row:
                    if k not in seen:
                        seen.add(k)
                        all_keys.append(k)
            format_csv_rows(data, all_keys, stream)
        elif isinstance(data, dict):
            format_csv_rows([data], list(data.keys()), stream)
    else:
        # table (default)
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
