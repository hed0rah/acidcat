"""Ableton Live Pack (.alp) walker. The layout is in core/formats/alp.py.

The pack is gzip over a 'pl-a' container, so the container is layer 1 and
every file in it is a node at its own offset there. The gzip is read once,
streamed: the index sits at the end and a pack can be hundreds of megabytes."""

import datetime

from acidcat.core.formats import alp as alpmod
from acidcat.core.infra.findings import defect
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import Unsupported, _f, _open, _size

# the index is metadata; a real one is tens of kilobytes
_ALP_INDEX_CAP = 64 * 1024 * 1024
# records parsed from the file tree
_ALP_RECORD_CAP = 1 << 20
# files given a node each; the rest are counted
_ALP_FILE_NODE_CAP = 4096


def _date(ts):
    if not ts:
        return "0 (not set)"
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC")


def inspect_alp(filepath, deep=False):
    size = _size(filepath)
    warns = []
    idx = None
    index = bytearray()
    total = 0
    complete = False
    with _open(filepath) as fh:
        try:
            for off, part in alpmod.stream(fh):
                if idx is None:
                    if not alpmod.is_alp_container(part):
                        raise Unsupported("gzip, but not a Live Pack container")
                    idx = int.from_bytes(part[4:8], "little")
                end = off + len(part)
                if end > idx:
                    keep = part[max(0, idx - off):]
                    room = _ALP_INDEX_CAP - len(index)
                    index += keep[:max(0, room)]
                total = end
            complete = True
        except Unsupported:
            raise
        except Exception as e:                      # zlib.error, truncated stream
            warns.append(defect("parse.failed", f"the gzip stream stops at "
                                                f"{total:,} bytes: {e}"))
    if idx is None:
        raise Unsupported("not a Live Pack")

    root = {"id": "gzip", "offset": 0, "size": size,
            "summary": f"Ableton Live Pack, {total:,} bytes inside the gzip",
            "fields": [_f(None, 0, "decompressed", f"{total:,} bytes",
                          f"{total / size:.2f}x the {size:,} bytes on disk" if size else "")],
            "warnings": [], "payload_base": 0}
    layer = [{"id": "header", "offset": 0, "size": alpmod.DATA_START,
              "summary": "pl-a container header",
              "fields": [_f(0, 4, "magic", "pl-a"),
                         _f(4, 4, "index_offset", idx)],
              "warnings": [], "payload_base": 0}]
    if complete:
        root["layer"] = {
            "name": "pl-a", "decoder": "gzip", "params": {"size": total},
            "length": total, "length_known": True,
            "verdict": {"result": "verified", "method": "crc32",
                        "detail": "gzip's CRC-32 and length trailer"}}
        root["layer_chunks"] = layer

    if idx > total:
        warns.append(defect("pointer.dangling", f"the index offset {idx:,} is past "
                                                f"the {total:,} bytes the gzip holds"))
        return [root], warns
    if total - idx > _ALP_INDEX_CAP:
        # a cut index would parse as a broken one; say what was not read and
        # stop, rather than report the reader's bound as damage
        warns.append(hit("read_bytes", _ALP_INDEX_CAP, total - idx,
                         f"the index is {total - idx:,} bytes; past the "
                         f"{_ALP_INDEX_CAP // (1 << 20)} MB read, so its files are "
                         f"not listed"))
        return [root], warns
    try:
        tree = alpmod.parse_tree(bytes(index), _ALP_RECORD_CAP)
    except alpmod.AlpError as e:
        warns.append(defect("parse.failed", f"file tree: {e}"))
        return [root], warns

    items = list(alpmod.walk(tree))
    files = [(p, it) for p, it in items if not it["is_dir"]]
    dirs = len(items) - len(files)
    stored = sorted((it for _p, it in files if it["size"]), key=lambda it: it["offset"])
    pos, gaps = alpmod.DATA_START, 0
    for it in stored:
        if it["offset"] != pos:
            gaps += 1
        pos = it["offset"] + it["size"]
    root["summary"] = (f"Ableton Live Pack {tree['name']!r}: {len(files):,} file(s) "
                       f"in {dirs:,} folder(s)")
    root["fields"] += [_f(None, 0, "pack", tree["name"]),
                       _f(None, 0, "files", len(files)),
                       _f(None, 0, "folders", dirs),
                       _f(None, 0, "stored_bytes", f"{sum(it['size'] for _p, it in files):,}")]
    if gaps or (stored and pos != idx):
        root["warnings"].append(defect(
            "geometry.invalid", f"the stored files do not tile the data region "
                                f"({gaps} gap(s); the last ends at {pos:,}, the index "
                                f"starts at {idx:,})"))

    rows = []
    for i, (path, it) in enumerate(files):
        if deep:
            # no offset: the file sits in layer 1, and a row is placed in the
            # file itself
            rows.append({"tick": i, "event": "file",
                         "detail": f"{path}  {it['size']:,} bytes at {it['offset']:#x}"})
        if i >= _ALP_FILE_NODE_CAP:
            continue
        fields = [_f(None, 0, "path", path), _f(None, 0, "stored", f"{it['size']:,} bytes"),
                  _f(None, 0, "modified", _date(it["mod_date"]))]
        if it["original_size"] and it["original_size"] != it["size"]:
            fields.append(_f(None, 0, "original", f"{it['original_size']:,} bytes",
                             "its size before the pack compressed it"))
        for k, v in it["metadata"].items():
            fields.append(_f(None, 0, k, str(v)))
        layer.append({"id": "file", "offset": it["offset"], "size": it["size"],
                      "summary": path, "fields": fields, "warnings": [],
                      "payload_base": it["offset"]})
    if len(files) > _ALP_FILE_NODE_CAP:
        warns.append(hit("list_rows", _ALP_FILE_NODE_CAP, len(files),
                         f"{len(files):,} files; the first {_ALP_FILE_NODE_CAP:,} "
                         f"have a node each"))
    layer.append({"id": "index", "offset": idx, "size": total - idx,
                  "summary": "file index (Ableton object serialisation)",
                  "fields": [_f(0, 4, "sentinel", index[:4].hex(" "))],
                  "warnings": [], "payload_base": idx})
    if deep and rows:
        root["rows"] = rows
    return [root], warns
