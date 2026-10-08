"""Native Instruments preset structural walker: hsin containers
(Massive/Absynth/Kontakt 5+), NKS .nksf, the older zlib-XML .ksd, Kontakt 2-4
patches (formats/kontakt.py) and the Kontakt sample container .nkx/.nkr
(formats/ni_container.py)."""


from acidcat.core.formats import kontakt as ktmod
from acidcat.core.formats import ni as nimod
from acidcat.core.formats import ni_container as ncmod
from acidcat.core.infra.findings import defect, info
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import Unsupported as _Unsupported, _open, _size
from acidcat.core.walk.base import _f

# a Kontakt patch body is read whole; a monolith's samples are seeked, not read
_KONTAKT_READ_CAP = 32 * 1024 * 1024
# the inflated K2 XML; the largest of 1,716 real patches was 1.5 MB
_KONTAKT_XML_CAP = 32 * 1024 * 1024
# a Kontakt 4.2 body decompressed; the largest of 393 real ones was under 4 MB
_KONTAKT_FASTLZ_CAP = 64 * 1024 * 1024
# directory entries read into the tree, and objects plus entries stepped over
# in one sample container
_NI_ENTRY_CAP = 1 << 20
_NI_OBJECT_CAP = 1 << 21
# stored objects given a chunk each; the rest share one summary chunk
_NI_OBJECT_CHUNK_CAP = 512
# a monolith's patch inside a monolith's patch...; real ones nest exactly once
_NI_NEST_CAP = 4
# the soundinfo trailer read after a patch body; real ones are a few hundred bytes
_KONTAKT_TRAILER_CAP = 1 << 20

_NCW_MAGIC = b"\x01\xa8\x9e\xd6"
_MAGIC_OF = {name: magic for magic, (name, _l, _h) in ncmod.OBJECTS.items()}


def inspect_ni(filepath, deep=False):
    """Structural view of a Native Instruments file. Handles the hsin container
    (Massive .nmsv, Absynth .nabs, Kontakt 5+ .nki), the older zlib-XML .ksd
    (Absynth/KORE), Kontakt 2-4 patches and the Kontakt sample container. With
    deep (--verbose or --frames) it also decompresses what is compressed."""
    file_size = _size(filepath)
    with _open(filepath) as f:
        head = f.read(ktmod.HEADER_LEN + 4)
    if ktmod.is_kontakt(head):
        return _inspect_kontakt(filepath, file_size, head, deep)
    if ncmod.is_container(head):
        return _inspect_container(filepath, file_size, 0, deep)
    with _open(filepath) as f:
        data = f.read(min(file_size, 16 * 1024 * 1024))
    if nimod.is_ni_ksd(data):
        meta, kind = nimod.parse_ksd(data), "ksd"
    elif nimod.is_ni_nksf(data):
        meta, kind = nimod.parse_nksf(data), "nksf"
    else:
        meta, kind = nimod.parse_hsin(data), "hsin"
    if not meta:
        raise _Unsupported("not a recognized Native Instruments preset")
    order = ["name", "product", "plugin", "author", "vendor", "bank", "comment",
             "description", "device_type", "version", "tempo", "genre", "key"]
    fields = [_f(None, 0, k, str(meta[k])) for k in order if meta.get(k)]
    for k in meta:
        if k not in order:
            fields.append(_f(None, 0, k, str(meta[k])))
    prod = meta.get("product") or meta.get("plugin") or "NI"
    summary = f"{prod} preset '{meta.get('name', '(unnamed)')}'"
    chunks = [{"id": kind, "offset": 0, "size": file_size, "summary": summary,
               "fields": fields, "warnings": [], "payload_base": 0}]
    if deep and kind == "hsin":
        inner = nimod.decompress_subtree(data)
        if inner is not None:
            nested = nimod.is_ni_hsin(inner)
            chunks.append({"id": "payload", "offset": 0, "size": 0,
                           "summary": "FastLZ-compressed preset state",
                           "fields": [_f(None, 0, "decompressed_size",
                                         f"{len(inner):,} bytes"),
                                      _f(None, 0, "inner_container",
                                         "nested hsin (synth parameter state)"
                                         if nested else "opaque")],
                           "warnings": []})
    return chunks, []


# ── Kontakt 2-4 patches ──

_APPS = {"Kon2": "Kontakt 2", "Kon3": "Kontakt 3", "Kon4": "Kontakt 4"}


def _reader(filepath):
    """An open file and a read_at(off, n) over it."""
    f = _open(filepath)

    def read_at(off, n):
        f.seek(off)
        return f.read(n)
    return f, read_at


def _inspect_kontakt(filepath, file_size, head, deep):
    h = ktmod.parse_header(head)
    if h is None:
        hv = int.from_bytes(head[8:10], "little") if len(head) >= 10 else None
        chunk = {"id": "header", "offset": 0, "size": min(file_size, 10),
                 "summary": "Kontakt patch, older header layout",
                 "fields": [_f(0, 4, "magic", "12 90 a8 7f")]
                 + ([_f(8, 2, "header_version", hv)] if hv is not None else []),
                 "warnings": [info("layout.unmeasured",
                                   f"header version {hv:#x} is not laid out; "
                                   f"only 0x100 and 0x110 are decoded"
                                   if hv is not None else
                                   "the header is too short to read")],
                 "payload_base": 0}
        return [chunk], []
    f, read_at = _reader(filepath)
    with f:
        return _kontakt_chunks(read_at, 0, file_size, h, deep, outer=True)


def _header_chunk(base, h, cid):
    hv = h["header_version"][2]
    start = ktmod.body_start(hv)
    app = h["application"][2]
    fields = [_f(0, 4, "magic", "12 90 a8 7f")]
    for name in ("header_version", "body_length", "app_version", "application",
                 "timestamp", "zones", "groups", "programs", "sample_bytes",
                 "author", "url", "uncompressed_length"):
        if name not in h:
            continue
        off, ln, v = h[name]
        note = ""
        if name == "application":
            v = f"{app} ({_APPS[app]})" if app in _APPS else f"{app} (byte-reversed)"
            note = "stored byte-reversed"
        elif name == "timestamp":
            v = ktmod.timestamp_text(v)
            note = "Unix seconds"
        elif name == "body_length" and v == 0:
            note = "0: the body runs to the end of the file"
        elif name in ("author", "url") and not v:
            continue
        elif name == "app_version":
            note = "stored least significant part first"
        fields.append(_f(off, ln, name, v, note))
    ver = h["app_version"][2]
    return {"id": cid, "offset": base, "size": start,
            "summary": f"Kontakt patch header ({_APPS.get(app, app)}, {ver})",
            "fields": fields, "warnings": [], "payload_base": base}


def _kontakt_chunks(read_at, base, end, h, deep, outer, depth=0):
    """Header, body and trailer of the patch at `base`, which runs to `end`.
    A monolith's body is a sample container, walked by _container_chunks."""
    hv = h["header_version"][2]
    start = base + ktmod.body_start(hv)
    chunks = [_header_chunk(base, h, "header" if outer else "patch_header")]
    warns = []
    kind = ktmod.body_kind(read_at(start, 4), 0)
    if kind == "container":
        cchunks, cwarns = _container_chunks(read_at, end, start, deep, depth)
        return chunks + cchunks, warns + cwarns
    declared = h["body_length"][2]
    avail = end - start
    if declared > avail:
        chunks[0]["warnings"].append(defect(
            "size.overrun", f"the body claims {declared:,} bytes but only "
                            f"{avail:,} remain"))
        declared = avail
    want = declared or avail
    n = min(want, _KONTAKT_READ_CAP)
    body = read_at(start, n)
    cut = n < want
    if cut:
        warns.append(hit("read_bytes", _KONTAKT_READ_CAP, want,
                         f"the patch body is {want:,} bytes; the first "
                         f"{_KONTAKT_READ_CAP // (1 << 20)} MB were read"))
    if kind == "zlib":
        chunk, body_len = _zlib_body(body, start, h, deep, warns)
    else:
        chunk, body_len = _fastlz_body(body, start, h, deep, warns, cut)
    if declared and body_len != declared:
        body_len = declared
        chunk["size"] = declared
    chunks.append(chunk)
    tr_at = start + body_len
    if tr_at < end:
        room = end - tr_at
        got = min(room, _KONTAKT_TRAILER_CAP)
        tr = ktmod.parse_trailer(read_at(tr_at, got), 0)
        if tr is not None:
            whole = tr["xml_offset"] + tr["xml_length"]
            if tr["xml_short"] and got < room:
                # the read stopped, not the file: the trailer runs on past it
                tr["xml_short"] = False
                tr["length"] = min(whole, room)
                warns.append(hit("read_bytes", _KONTAKT_TRAILER_CAP, whole,
                                 f"the soundinfo trailer is {whole:,} bytes; the first "
                                 f"{_KONTAKT_TRAILER_CAP // (1 << 20)} MB were read"))
            chunks.append(_trailer_chunk(tr, tr_at))
            tr_end = tr_at + tr["length"]
            if outer and tr_end < end:
                warns.append(info("container.trailing",
                                  f"{end - tr_end:,} bytes follow the soundinfo "
                                  f"trailer"))
        elif outer:
            warns.append(info("container.trailing",
                              f"{end - tr_at:,} bytes follow the patch body and "
                              f"are not a soundinfo trailer"))
    return chunks, warns


def _zlib_body(body, start, h, deep, warns):
    xml, consumed, truncated = ktmod.inflate(body, _KONTAKT_XML_CAP)
    chunk = {"id": "patch", "offset": start, "size": consumed or len(body),
             "summary": "zlib-compressed patch XML", "fields": [],
             "warnings": [], "payload_base": start}
    if xml is None:
        chunk["warnings"].append(defect("parse.failed",
                                        "the patch body does not inflate"))
        return chunk, len(body)
    if truncated:
        warns.append(hit(
            "inflate_bytes", _KONTAKT_XML_CAP, _KONTAKT_XML_CAP + 1,
            f"the patch XML exceeds {_KONTAKT_XML_CAP // (1 << 20)} MB; the "
            f"counts below describe only the part inflated"))
    p = ktmod.parse_xml(xml)
    fl = chunk["fields"]
    fl.append(_f(None, 0, "xml_length", f"{len(xml):,} bytes"))
    if p["type"]:
        fl.append(_f(None, 0, "type", p["type"]))
    for i, name in enumerate(p["programs"][:16]):
        fl.append(_f(None, 0, "program" if len(p["programs"]) == 1 else f"program {i}",
                     name))
    if len(p["programs"]) > 16:
        fl.append(_f(None, 0, "more_programs", f"{len(p['programs']) - 16} further"))
    fl.append(_f(None, 0, "zones", p["zones"]))
    fl.append(_f(None, 0, "groups", p["groups"]))
    files = p["files"]
    unique = list(dict.fromkeys(files))
    fl.append(_f(None, 0, "sample_files", f"{len(unique):,} referenced"
                 + (f" by {len(files):,} zones" if len(files) != len(unique) else "")))
    if unique:
        fl.append(_f(None, 0, "first_sample", unique[0]))
    if not truncated:
        for key, have in (("zones", p["zones"]), ("groups", p["groups"]),
                          ("programs", p["program_count"])):
            said = h[key][2]
            if said != have:
                chunk["warnings"].append(defect(
                    "count.mismatch", f"the header says {said} {key} and the "
                                      f"patch holds {have}"))
    if deep and files:
        # bounded by the XML cap: every row is a path the inflated XML holds
        chunk["rows"] = [{"tick": i, "event": "zone sample", "detail": path}
                         for i, path in enumerate(files)]
    chunk["summary"] = (f"zlib-compressed patch XML: {p['zones']} zone(s), "
                        f"{len(unique)} sample file(s)")
    return chunk, consumed or len(body)


def _fastlz_body(body, start, h, deep, warns, cut=False):
    want = h.get("uncompressed_length", (None, None, None))[2]
    chunk = {"id": "patch", "offset": start, "size": len(body),
             "summary": "FastLZ-compressed patch (binary object tree)",
             "fields": [_f(None, 0, "compression",
                           f"FastLZ level {(body[0] >> 5) + 1 if body else '?'}")],
             "warnings": [], "payload_base": start}
    if deep and body:
        out = nimod.fastlz_decompress(body, _KONTAKT_FASTLZ_CAP)
        if out is None:
            warns.append(hit(
                "inflate_bytes", _KONTAKT_FASTLZ_CAP, want or _KONTAKT_FASTLZ_CAP + 1,
                f"the body decompresses past {_KONTAKT_FASTLZ_CAP // (1 << 20)} MB"))
        else:
            chunk["fields"].append(_f(None, 0, "decompressed", f"{len(out):,} bytes"))
            # a body cut by the read cap cannot be held to the header's length;
            # the cap is already announced
            if want is not None and len(out) != want and not cut:
                chunk["warnings"].append(defect(
                    "size.overrun" if len(out) < want else "count.mismatch",
                    f"the header says the body decompresses to {want:,} bytes "
                    f"and it gave {len(out):,}"))
    return chunk, len(body)


def _trailer_chunk(tr, at):
    si = tr["soundinfo"]
    fields = []
    if tr["hash"]:
        fields.append(_f(12, len(tr["hash"]), "hash", tr["hash"],
                         "bcrypt-format string; what it hashes is not known"))
    for k in ("name", "author", "vendor", "bankchain", "comment"):
        if si.get(k):
            fields.append(_f(None, 0, k, si[k]))
    for k, v in si.items():
        if k not in ("name", "author", "vendor", "bankchain", "comment", "attributes"):
            fields.append(_f(None, 0, k, v))
    if si.get("attributes"):
        fields.append(_f(None, 0, "attributes", ", ".join(si["attributes"])))
    warns = []
    if tr["xml_short"]:
        warns.append(defect("size.overrun", f"the soundinfo claims "
                                            f"{tr['xml_length']:,} bytes and the "
                                            f"file ends first"))
    return {"id": "soundinfo", "offset": at, "size": tr["length"],
            "summary": f"soundinfo '{si.get('name', '')}'" if si.get("name")
                       else "soundinfo trailer",
            "fields": [_f(0, 4, "magic", "ae e1 0e b0"),
                       _f(8, 4, "xml_length", tr["xml_length"])] + fields,
            "warnings": warns, "payload_base": at}


# ── the sample container (.nkx, .nkr, a monolith's body) ──

def _inspect_container(filepath, file_size, base, deep):
    f, read_at = _reader(filepath)
    with f:
        return _container_chunks(read_at, file_size, base, deep)


def _payload_kind(obj):
    if obj["kind"] != "sample":
        return None
    if obj["magic"] == _NCW_MAGIC:
        return "NCW"
    if obj["magic"] == b"RIFF":
        return "RIFF WAV"
    if obj["magic"] == b"FORM":
        return "AIFF"
    return "opaque (neither NCW nor RIFF magic)"


def _plural(n, one, many=None):
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _container_chunks(read_at, size, base, deep, depth=0):
    warns = []
    entries, _end, problem = ncmod.read_tree(read_at, base, _NI_ENTRY_CAP)
    counts = {t: 0 for t in ncmod.ENTRY_TYPES}
    for e in entries:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    if problem == "entries":
        warns.append(hit("list_rows", _NI_ENTRY_CAP, _NI_ENTRY_CAP + 1,
                         f"the directory holds more than {_NI_ENTRY_CAP:,} entries; "
                         f"the rest were not read"))
    elif problem:
        warns.append(defect("parse.failed", f"directory tree: {problem}"))
    objs, oend, stop = ncmod.read_objects(read_at, base, size, _NI_OBJECT_CAP)
    pairs = ncmod.pair_files(entries, objs) if stop is None and not problem else None
    names = {o["at"]: e["path"] for e, o in (pairs or [])}
    for e in entries:
        if e["type"] in (3, 4):
            names.setdefault(e["value"], e["path"])
    top = {"id": "container", "offset": base, "size": max(0, oend - base),
           "summary": (f"Kontakt sample container: {_plural(counts[2], 'file')}, "
                       f"{_plural(counts[4], 'resource')}, "
                       f"{_plural(counts[1], 'directory', 'directories')}"),
           "fields": [_f(0, 4, "magic", "54 ac 70 5e"),
                      _f(4, 2, "version", int.from_bytes(read_at(base + 4, 2), "little")),
                      _f(None, 0, "directories", counts[1]),
                      _f(None, 0, "files", counts[2]),
                      _f(None, 0, "resources", counts[4]),
                      _f(None, 0, "patches", counts[3]),
                      _f(None, 0, "objects", len(objs))],
           "warnings": [], "payload_base": base}
    if pairs and counts[2]:
        top["fields"].append(_f(None, 0, "names", "paired with stored objects",
                                "by order: a file entry's value is not an offset"))
    elif stop is None and counts[2]:
        top["fields"].append(_f(None, 0, "names", "not paired",
                                f"{counts[2]:,} file entries and a different number "
                                f"of stored objects; the data may be split across "
                                f"several files"))
    chunks = [top]
    if stop == "steps":
        warns.append(hit("work_steps", _NI_OBJECT_CAP, _NI_OBJECT_CAP + 1,
                         f"the walk stopped there, after {_NI_OBJECT_CAP:,} "
                         f"objects and directory entries"))
    elif stop == "undecoded":
        warns.append(info("layout.unmeasured",
                          f"an object of kind 9f 17 40 00 at {oend:#x} has a "
                          f"length this walker does not decode; the rest of the "
                          f"container is not walked"))
    elif stop is not None:
        warns.append(defect("parse.failed", f"the object at {oend:#x} is not a "
                                            f"container object ({stop})"))
    rows = []
    stored = [o for o in objs if o["kind"] != "directory"]
    for i, o in enumerate(stored):
        name = names.get(o["at"])
        kind = _payload_kind(o)
        if deep:
            # bounded by _NI_OBJECT_CAP: the walk stops there
            rows.append({"tick": i, "event": o["kind"],
                         "detail": f"{name or '(unnamed)'}  {o['length']:,} bytes"
                                   + (f"  {kind}" if kind else ""),
                         "offset": o["at"],
                         "size": ncmod.OBJECTS[_MAGIC_OF[o["kind"]]][2] + o["length"]})
        if i >= _NI_OBJECT_CHUNK_CAP:
            continue
        spec = ncmod.OBJECTS[_MAGIC_OF[o["kind"]]]
        fields = [_f(spec[1], 4, "length", o["length"])]
        if name:
            fields.insert(0, _f(None, 0, "name", name))
        if kind:
            fields.append(_f(None, 0, "payload", kind))
        chunks.append({"id": o["kind"], "offset": o["at"], "size": spec[2] + o["length"],
                       "summary": f"{o['kind']} {name or ''}".rstrip(),
                       "fields": fields, "warnings": [], "payload_base": o["at"]})
        if o["kind"] == "patch":
            chunks += _inner_patch(read_at, o, deep, warns, depth)
    if len(stored) > _NI_OBJECT_CHUNK_CAP:
        rest = stored[_NI_OBJECT_CHUNK_CAP:]
        first = rest[0]["at"]
        chunks.append({"id": "objects", "offset": first, "size": oend - first,
                       "summary": f"{len(rest):,} further stored object(s)",
                       "fields": [_f(None, 0, "objects", len(rest))],
                       "warnings": [], "payload_base": first})
        warns.append(hit("list_rows", _NI_OBJECT_CHUNK_CAP, len(stored),
                         f"{len(stored):,} stored objects; the first "
                         f"{_NI_OBJECT_CHUNK_CAP} have a chunk each and the rest "
                         f"are summarised"))
        # a patch past the chunk cap still gets its own chunks
        for o in rest:
            if o["kind"] == "patch":
                chunks += _inner_patch(read_at, o, deep, warns, depth)
    if deep and rows:
        top["rows"] = rows
    return chunks, warns


def _inner_patch(read_at, obj, deep, warns, depth=0):
    """The Kontakt patch a monolith stores inside its container."""
    if depth >= _NI_NEST_CAP:
        warns.append(hit("depth", _NI_NEST_CAP, depth + 1,
                         f"patches nest {depth + 1} deep here; the walk stops at "
                         f"{_NI_NEST_CAP} (a real monolith nests one)"))
        return []
    at = obj["payload_at"]
    head = read_at(at, ktmod.HEADER_LEN + 4)
    h = ktmod.parse_header(head)
    if h is None:
        return []
    chunks, pwarns = _kontakt_chunks(read_at, at, at + obj["length"], h, deep,
                                     outer=False, depth=depth + 1)
    warns.extend(pwarns)
    return chunks
