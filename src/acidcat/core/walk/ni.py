"""Native Instruments preset structural walker: hsin containers
(Massive/Absynth/Kontakt 5+), NKS .nksf, the older zlib-XML .ksd, Kontakt 2-4
patches (formats/kontakt.py) and the Kontakt sample container .nkx/.nkr
(formats/ni_container.py)."""


from acidcat.core.formats import fm8 as fm8mod
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
# hsin frames given a chunk each, and how deep they may nest; a Reaktor 6
# ensemble's outer tree is under a dozen frames (the rest is compressed)
_HSIN_FRAME_CAP = 512
_HSIN_DEPTH_CAP = 128
# an item's own data read to decode it; the largest across 1,797 real
# presets, ensembles and instruments was a 4,930-byte ControllerAssignments
# (a module, a subtree or a synth state is placed, not read)
_HSIN_ITEM_CAP = 1 << 20
# a SubtreeItem's FastLZ stream decompressed as a layer, and the stream
# itself; the largest of 1,114 real ones opened to 68,730 bytes
_HSIN_SUBTREE_CAP = 16 * 1024 * 1024
# how much of a Reaktor instrument's end is searched for its NI metadata
# frame (the furthest of 497 real ones began 7 MB from the end), and how many
# hsin magics in it are tried as a frame
_REAKTOR_TAIL_CAP = 16 * 1024 * 1024
_REAKTOR_SCAN_CAP = 1 << 14

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
    at = nimod.reaktor_module_at(head)
    if at is not None:
        return _inspect_reaktor(filepath, file_size, head, at)
    if nimod.is_ni_hsin(head):
        walked = _hsin_tree(filepath, file_size, deep)
        if walked is not None:
            return walked
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
    fields = _meta_fields(meta)
    prod = meta.get("product") or meta.get("plugin") or "NI"
    summary = f"{prod} preset '{meta.get('name', '(unnamed)')}'"
    chunks = [{"id": kind, "offset": 0, "size": file_size, "summary": summary,
               "fields": fields, "warnings": [], "payload_base": 0}]
    warns = []
    if kind == "hsin" and len(data) >= 8:
        root = int.from_bytes(data[:8], "little")
        if nimod.FRAME_HEADER <= root < file_size:
            warns.append(info("container.trailing",
                              f"{file_size - root:,} bytes follow the root frame"))
    fm8 = prod == "FM8"
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
            loc = fm8mod.locate(inner, nimod._hsin_walk) if nested else None
            if loc is not None:
                at, n = loc
                chunks += _fm8_chunks(inner[at:at + n], warns)
    elif deep and kind == "ksd" and fm8:
        blob = _ksd_fm8_block(data)
        if blob is not None:
            chunks += _fm8_chunks(blob, warns)
    elif fm8 and kind in ("hsin", "ksd"):
        warns.append(info("decode.partial",
                          "the FM8 sound state (operators, envelopes, matrix) "
                          "is compressed; --deep decodes it"))
    return chunks, warns


def _text(v):
    return ", ".join(v) if isinstance(v, list) else str(v)


# ── FM8 sound state (formats/fm8.py) ──

# zlib streams tried in a .ksd for the one holding the FM8 state
_KSD_FM8_TRIES = 32


def _ksd_fm8_block(data):
    """The inflated `prst` stream of a KORE sound, when it is an FM8 state."""
    tries = 0
    pos = data.find(b"\x78", 0)
    while 0 <= pos and tries < _KSD_FM8_TRIES:
        if data[pos + 1:pos + 2] in (b"\x01", b"\x9c", b"\xda"):
            tries += 1
            out = nimod._safe_inflate(data[pos:])
            if out and out[:4] == fm8mod.MAGIC:
                return out
        pos = data.find(b"\x78", pos + 1)
    return None


def _num(x):
    return "%.6g" % x


def _row(values):
    return " ".join(_num(v) for v in values)


def _onoff(x):
    return "on" if x else "off"


def _fm8_chunks(blob, warns):
    """The FM8 state as chunks: the voice-wide settings, one chunk per
    operator, the pitch envelope and the matrix. The state is inflated, not
    file bytes, so the chunks are unpositioned."""
    try:
        st = fm8mod.parse(blob)
    except fm8mod.Short as e:
        warns.append(defect("size.overrun", f"FM8 state: {e}"))
        return []
    except ValueError as e:
        warns.append(defect("parse.failed", f"FM8 state: {e}"))
        return []
    head = {"id": "fm8", "offset": None, "size": None, "warnings": [],
            "summary": f"FM8 sound state, version {st['version']}",
            "fields": [_f(None, 0, "magic", "E8MF"),
                       _f(None, 0, "state_version", st["version"]),
                       _f(None, 0, "names", " | ".join(st["names"]),
                          "four names, stored like the rest of the state")]}
    if not st["laid_out"]:
        warns.append(info("layout.unmeasured",
                          f"FM8 state version {st['version']}: only versions "
                          f"{', '.join(map(str, fm8mod.VERSIONS))} are laid out; "
                          f"the header is decoded"))
        return [head]
    fl = head["fields"]
    fl.append(_f(None, 0, "transpose", "%+g" % st["transpose"], "semitones"))
    for i, lfo in enumerate(st["lfo"], 1):
        fl += [_f(None, 0, f"lfo{i}_wave", fm8mod.lfo_wave(lfo["wave"])),
               _f(None, 0, f"lfo{i}_rate", _num(lfo["rate"]), "as stored, not Hz"),
               _f(None, 0, f"lfo{i}_delay", _num(lfo["delay"])),
               _f(None, 0, f"lfo{i}_sync", _onoff(lfo["sync"]))]
    if not (st["blocks_agree"] and st["cells_agree"]):
        warns.append(info("decode.partial",
                          "the four stored copies of the operator block or the "
                          "matrix differ; the first is shown"))
    m = st["matrix"]
    on = [i for i in range(6) if st["on"][i]]
    head["summary"] += ": operators %s on" % ("".join(fm8mod.OPERATORS[i] for i in on)
                                              or "none")
    chunks = [head]
    for i, letter in enumerate(fm8mod.OPERATORS):
        ratio, off = st["ratio"][i], st["offset"][i]
        freq = (f"fixed {_num(off)} Hz" if ratio == 0
                else f"ratio {_num(ratio)}" + (f" {off:+.3g} Hz" if off else ""))
        times, levels, slopes = st["env"][i]
        keys, kvals, kslopes = st["keyscale"][i]
        mods = ", ".join(f"{fm8mod.OPERATORS[c]} {m[i][c]}" for c in range(6)
                         if c != i and m[i][c])
        fields = [_f(None, 0, "on", _onoff(st["on"][i])),
                  _f(None, 0, "ratio", _num(ratio), "0 for a fixed frequency"),
                  _f(None, 0, "offset", _num(off), "Hz"),
                  _f(None, 0, "frequency", freq, derived_from=["ratio", "offset"]),
                  _f(None, 0, "output", m[i][fm8mod.MATRIX_OUT], "matrix amount to the output, 0-100"),
                  _f(None, 0, "feedback", m[i][i], "matrix amount to itself, 0-100"),
                  _f(None, 0, "modulates", mods or "none", "matrix amounts, 0-100"),
                  _f(None, 0, "velocity", st["velocity"][i], "0-100"),
                  _f(None, 0, "rate_scaling", st["rate_scaling"][i], "0-100"),
                  _f(None, 0, "key_sync", _onoff(st["key_sync"][i])),
                  _f(None, 0, "env_points", len(times)),
                  _f(None, 0, "env_times", _row(times), "as stored"),
                  _f(None, 0, "env_levels", _row(levels), "0-1"),
                  _f(None, 0, "env_slopes", _row(slopes), "as stored"),
                  _f(None, 0, "keyscale_keys", _row(keys), "key positions 0-127"),
                  _f(None, 0, "keyscale_values", _row(kvals), "as stored")]
        chunks.append({"id": f"op[{letter}]", "offset": None, "size": None,
                       "warnings": [], "fields": fields,
                       "summary": f"operator {letter}: {_onoff(st['on'][i])}, {freq}, "
                                  f"output {m[i][fm8mod.MATRIX_OUT]}"})
    times, levels, slopes = st["env"][fm8mod.PITCH_ENV]
    chunks.append({"id": "pitch_env", "offset": None, "size": None,
                   "warnings": [], "summary": f"pitch envelope, {len(times)} points",
                   "fields": [_f(None, 0, "env_times", _row(times), "as stored"),
                              _f(None, 0, "env_levels", _row(levels), "as stored"),
                              _f(None, 0, "env_slopes", _row(slopes), "as stored")]})
    links = [(r, c) for r in range(6) for c in range(6) if r != c and m[r][c]]
    other = sum(1 for r in range(fm8mod.MATRIX) for c in range(fm8mod.MATRIX)
                if m[r][c] and not (r < 6 and (c < 6 or c == fm8mod.MATRIX_OUT)))
    mfields = [_f(None, 0, f"{fm8mod.OPERATORS[r]}>{fm8mod.OPERATORS[c]}", m[r][c])
               for r, c in links]
    mfields.append(_f(None, 0, "carriers", "".join(
        fm8mod.OPERATORS[r] for r in range(6) if m[r][fm8mod.MATRIX_OUT]) or "none",
        "operators with an output amount"))
    if other:
        mfields.append(_f(None, 0, "other_cells", other,
                          "nonzero cells in rows or columns not decoded"))
    chunks.append({"id": "matrix", "offset": None, "size": None,
                   "warnings": [], "fields": mfields,
                   "summary": f"modulation matrix: {len(links)} connection(s), "
                              f"carriers {mfields[len(links)]['value']}"})
    return chunks


_META_ORDER = ["name", "product", "plugin", "author", "vendor", "bank", "comment",
               "description", "device_type", "version", "tempo", "genre", "key"]


def _meta_fields(meta):
    """Unpositioned fields for a preset's metadata, in one order across NI's
    containers; a list is shown comma-joined."""
    def show(v):
        if not isinstance(v, (list, tuple)):
            return str(v)
        more = f", and {len(v) - 16:,} more" if len(v) > 16 else ""
        return ", ".join(map(str, v[:16])) + more
    fields = [_f(None, 0, k, show(meta[k]), text=k != "tempo") for k in _META_ORDER
              if meta.get(k)]
    fields += [_f(None, 0, k, show(v), text=True) for k, v in meta.items()
               if k not in _META_ORDER and k != "bankchain"
               and v not in (None, "", [], {})]
    return fields


# ── the hsin frame tree, walked for its structure ──

def _hsin_tree(filepath, file_size, deep=False):
    """An hsin preset walked frame by frame: each frame's header, its item
    records and every record's own data, decoded where the item's layout is
    known, the FastLZ subtree as a layer of its own. None when the root
    frame does not read, for the caller's string scan."""
    f, read_at = _reader(filepath)
    with f:
        frames, problem = nimod.read_frames(read_at, 0, file_size, _HSIN_FRAME_CAP,
                                           _HSIN_DEPTH_CAP)
        if not frames:
            return None
        warns = []
        chunks, items = _frame_chunks(frames, problem, warns, read_at=read_at)
        meta = nimod.hsin_meta(items)
        if not meta.get("name") and problem not in (None, "frames", "depth"):
            # the tree breaks before a SoundInfoItem is read: the string scan
            # reads what the walk cannot reach
            scanned = nimod.parse_hsin(read_at(0, min(file_size, 16 * 1024 * 1024)))
            meta = {**(scanned or {}), **meta}
        if meta.get("product") == "FM8":
            chunks += _hsin_fm8(read_at, file_size, deep, warns)
    root = chunks[0]
    root["fields"] = _meta_fields(meta) + root["fields"]
    if meta.get("name"):
        prod = meta.get("product") or "NI"
        root["summary"] = f"{prod} preset '{meta['name']}'"
    else:
        snaps = meta.get("snapshots") or []
        if snaps:
            root["summary"] = (f"hsin tree: {len(snaps):,} snapshot(s), "
                               f"the first '{snaps[0]}'")
        warns.append(info("decode.partial",
                          "the frame tree holds no SoundInfoItem at its top "
                          "(name, author, vendor)"
                          + (", only its snapshots'" if snaps else "")
                          + "; its frames and item records are walked"))
    end = frames[0]["size"]
    if end < file_size:
        n = file_size - end
        with _open(filepath) as f:
            f.seek(end)
            tail = f.read(min(n, 4096))
        if len(tail) == n and not any(tail):
            # up to 8 zero bytes follow the root in 65 of 1,200 real presets;
            # in 55 more the bytes are not zero, and read like the tail of a
            # longer earlier save (item data endings), so they stay unwalked
            chunks.append({"id": "padding", "offset": end, "size": n,
                           "payload_base": end, "payload_len": n, "extent_len": n,
                           "summary": f"{n:,} zero bytes after the root frame",
                           "fields": [], "warnings": []})
        else:
            warns.append(info("container.trailing",
                              f"{n:,} bytes follow the root frame"))
    return chunks, warns


def _hsin_fm8(read_at, file_size, deep, warns):
    """An FM8 preset's sound state as unpositioned chunks (formats/fm8.py),
    with deep; without, a note that it is there."""
    if not deep:
        warns.append(info("decode.partial",
                          "the FM8 sound state (operators, envelopes, matrix) "
                          "is compressed; --deep decodes it"))
        return []
    inner = nimod.decompress_subtree(read_at(0, min(file_size, 16 * 1024 * 1024)))
    if inner is None or not nimod.is_ni_hsin(inner):
        return []
    loc = fm8mod.locate(inner, nimod._hsin_walk)
    if loc is None:
        return []
    at, n = loc
    return _fm8_chunks(inner[at:at + n], warns)


def _frame_chunks(frames, problem, warns, root_id="hsin", read_at=None, walk=None):
    """Chunks for frames from formats/ni.read_frames: a chunk per frame, its
    reference and header as fields before its payload, an `items` chunk for
    the item records opening its data section, and a chunk for each record's
    own data, decoded when read_at is given. `walk` carries what nested trees
    share: the frames left under _HSIN_FRAME_CAP, the depth the tree starts
    at, whether it is inside a decompressed layer. Returns (chunks, meta),
    `meta` the items' decoded metadata keyed by item id."""
    if walk is None:
        # `left` and `said` are shared by every tree of the walk, copies too
        walk = {"left": [_HSIN_FRAME_CAP - len(frames)], "depth": 0,
                "layer": False, "said": set()}
    chunks, meta = [], {}
    for fr in frames:
        at = fr["at"]
        base = at + nimod.FRAME_HEADER
        start = fr["ref"] if fr["ref"] is not None else at
        fs, hv, c1, c2, uuid, ds = fr["header"]
        fields = []
        if fr["ref_fields"] is not None:
            word, dom, iid = fr["ref_fields"]
            fields += [_f(-0x3C, 4, "ref_word", word, "uninterpreted", enc="<I", raw=word),
                       _f(-0x38, 4, "ref_domain", dom),
                       _f(-0x34, 4, "ref_item_id", iid, enc="<I", raw=iid)]
        fields += [_f(-0x30, 8, "frame_size", fs, "this frame, children included",
                      enc="<Q", raw=fs),
                   _f(-0x28, 4, "header_version", hv, enc="<I", raw=hv),
                   _f(-0x24, 4, "magic", "hsin"),
                   _f(-0x20, 4, "const", c1, "uninterpreted", enc="<I", raw=c1),
                   _f(-0x1C, 4, "const", c2, "uninterpreted", enc="<I", raw=c2),
                   _f(-0x18, 16, "uuid", uuid.hex()),
                   _f(-0x08, 8, "data_size", ds, "the item records and their data",
                      enc="<Q", raw=ds)]
        items = fr["items"]
        if items:
            dom, iid = items[0]["domain"], items[0]["item_id"]
        elif fr["ref_fields"] is not None:
            dom, iid = fr["ref_fields"][1], fr["ref_fields"][2]
        else:
            dom, iid = "?", None
        what = nimod.item_name(dom, iid)
        cid = root_id if fr["ref"] is None else f"{dom} {iid}"
        frame = {"id": cid, "offset": start, "size": at + fs - base,
                 "summary": f"hsin frame: {dom} {what}, {fs:,} bytes",
                 "fields": fields, "warnings": [], "payload_base": base,
                 "payload_len": at + fs - base, "extent_len": at + fs - start}
        chunks.append(frame)
        if fr["stack_problem"] and items is not None:
            frame["warnings"].append(defect(
                "parse.failed", f"item records: {fr['stack_problem']}"))
        if not items:
            continue
        chunks.append(_records_chunk(items, base))
        # each record's own data, in file order: the innermost record's first
        for rec in reversed(items):
            s, e = rec["own"]
            if e > s:
                chunk, imeta = _item_chunk(rec, read_at, warns, walk,
                                           walk["depth"] + fr["depth"])
                chunks.append(chunk)
                chunks += chunk.pop("_inner", [])
                if imeta:
                    nimod.collect_meta(meta, rec["item_id"], fr["depth"], imeta)
    if problem == "frames" and "frames" not in walk["said"]:
        walk["said"].add("frames")
        warns.append(hit("list_rows", _HSIN_FRAME_CAP, _HSIN_FRAME_CAP + 1,
                         f"the tree holds more than {_HSIN_FRAME_CAP:,} frames; "
                         f"the first {_HSIN_FRAME_CAP:,} have a chunk each and the "
                         f"rest are not walked"))
    elif problem == "depth" and "depth" not in walk["said"]:
        walk["said"].add("depth")
        warns.append(hit("depth", _HSIN_DEPTH_CAP, _HSIN_DEPTH_CAP + 1,
                         f"frames nest more than {_HSIN_DEPTH_CAP} deep; the walk "
                         f"stops there"))
    elif problem and problem not in ("frames", "depth"):
        warns.append(defect("parse.failed", f"frame tree: {problem}"))
    return chunks, meta


def _records_chunk(items, base):
    """The item records heading a data section, outermost first: each a
    domain tag, an item id and a version, then the u64 size of the record
    nested in it."""
    fields = []
    for rec in items:
        rel, i = rec["at"] - base, rec["item_id"]
        fields += [_f(rel, 4, "domain", rec["domain"]),
                   _f(rel + 4, 4, "item_id", i,
                      nimod.item_name(rec["domain"], i, ""), enc="<I", raw=i),
                   _f(rel + 8, 4, "version", rec["version"], enc="<I",
                      raw=rec["version"])]
        if rec["size_at"] is not None:
            n = rec["own"][0] - rec["size_at"]
            fields.append(_f(rec["size_at"] - base, 8, "nested_size", n,
                             "the record nested in this one, these 8 bytes "
                             "included", enc="<Q", raw=n))
    last = items[-1]
    stop = (last["size_at"] + 8 if last["size_at"] is not None
            else last["at"] + 12)
    return {"id": "items", "offset": base, "size": stop - base,
            "summary": f"{len(items)} item record(s): "
                       + " > ".join(str(r["item_id"]) for r in items),
            "fields": fields, "warnings": [], "payload_base": base,
            "payload_len": stop - base, "extent_len": stop - base}


def _field_of(off, n, name, value, note, enc):
    if enc is None:
        # a string of digits ("1", a property's value) is a number to the
        # Document, as any display string is; other text stays text
        return _f(off, n, name, value, note,
                  text=isinstance(value, str) and not value.strip().isdigit())
    return _f(off, n, name, value, note, enc=enc, raw=value)


def _item_chunk(rec, read_at, warns, walk, depth):
    """A chunk for one record's own data, its layout decoded when the item id
    has one. Returns (chunk, meta); a subtree stored as it is leaves its
    frames' chunks under chunk["_inner"]."""
    iid = rec["item_id"]
    s, e = rec["own"]
    n = e - s
    name = nimod.item_name(rec["domain"], iid)
    cid = f"item {iid}" if rec["domain"] == "DSIN" else f"{rec['domain']} item {iid}"
    chunk = {"id": cid, "offset": s, "size": n, "payload_base": s,
             "payload_len": n, "extent_len": n, "fields": [], "warnings": [],
             "summary": f"{name}: {n:,} bytes, its layout not decoded"}
    if read_at is None or not nimod.decodes(rec["domain"], iid):
        return chunk, {}
    head_only = iid in (104, 109, 115)    # a header, then a payload placed whole
    if n > _HSIN_ITEM_CAP and not head_only:
        warns.append(hit("read_bytes", _HSIN_ITEM_CAP, n,
                         f"{name} holds {n:,} bytes; one past "
                         f"{_HSIN_ITEM_CAP:,} is not decoded"))
        return chunk, {}
    b = read_at(s, min(n, 64) if head_only else n)
    fields, meta, why = nimod.decode_item(iid, b)
    chunk["fields"] = [_field_of(*fl) for fl in fields]
    chunk["summary"] = f"{name}: {n:,} bytes"
    if why:
        chunk["warnings"].append(info("layout.unmeasured",
                                      f"{name}: the layout stops matching: {why}"))
    if iid == 108 and meta.get("name"):
        chunk["summary"] = f"{name}: '{meta['name']}'"
    elif iid == 121:
        pages = meta.get("pages") or []
        chunk["summary"] = f"{name}: {len(pages)} page(s)"
        chunk["rows"] = [
            {"tick": k, "event": page,
             "detail": f"{pos}: {label} -> parameter {param}"}
            for k, (page, label, param, pos) in enumerate(
                (p[2], c[0], c[3], c[4]) for p in pages for c in p[3])]
    elif iid == 109 and meta.get("state"):
        rel, m = meta["state"]
        if _place(chunk, rel, m):
            chunk["summary"] = (f"{name}: {m:,} bytes of synth state, the "
                                f"synth's own layout, not decoded")
    elif iid == 104 and meta.get("module"):
        rel, m, lead = meta["module"]
        if _place(chunk, rel, m):
            chunk["summary"] = (f"{name}: a {m:,}-byte Reaktor module opening "
                                f"{lead}, as an .ism does; not decoded")
    elif iid == 115 and meta.get("subtree", {}).get("compressed") == 0:
        _plain_subtree(chunk, meta["subtree"], read_at, warns, walk, depth)
    elif iid == 115 and "subtree" in meta:
        _subtree(chunk, meta["subtree"], read_at, warns, walk)
    return chunk, meta


def _place(chunk, rel, m):
    """Make the m bytes at `rel` in an item's data the chunk's payload, its
    fields before it; False when they do not fit the item."""
    if rel + m > chunk["size"]:
        return False
    for fl in chunk["fields"]:
        fl["off"] -= rel
    chunk["payload_base"], chunk["payload_len"] = chunk["offset"] + rel, m
    return True


def _inner_tree(read_at, at, end, warns, walk, depth, root_id):
    """The hsin tree at `at`, inside a SubtreeItem, as chunks; its frames
    count against the walk's _HSIN_FRAME_CAP and its depth below `depth`."""
    room = _HSIN_DEPTH_CAP - depth - 1
    if room < 0:
        if "depth" not in walk["said"]:
            walk["said"].add("depth")
            warns.append(hit("depth", _HSIN_DEPTH_CAP, _HSIN_DEPTH_CAP + 1,
                             f"frames nest more than {_HSIN_DEPTH_CAP} deep; the "
                             f"walk stops there"))
        return []
    frames, problem = nimod.read_frames(read_at, at, end, max(walk["left"][0], 0),
                                        room)
    walk["left"][0] -= len(frames)
    if not frames:
        if problem == "frames":
            _frame_chunks([], problem, warns, walk=walk)
        return []
    inner, _meta = _frame_chunks(frames, problem, warns, root_id=root_id,
                                 read_at=read_at,
                                 walk=dict(walk, depth=depth + 1))
    return inner


def _plain_subtree(chunk, sub, read_at, warns, walk, depth):
    """A SubtreeItem stored as it is (compressed 0, every Reaktor ensemble's):
    the inner hsin tree follows the five-byte header in the file, and its
    frames are walked in place."""
    s, n = chunk["offset"], chunk["size"]
    at = s + sub["tree_at"]
    for fl in chunk["fields"]:
        fl["off"] -= sub["tree_at"]
    chunk["payload_base"], chunk["payload_len"] = at, s + n - at
    chunk["summary"] = f"SubtreeItem: an hsin tree of {s + n - at:,} bytes, stored as it is"
    chunk["_inner"] = _inner_tree(read_at, at, s + n, warns, walk, depth, "subtree")


def _subtree(chunk, sub, read_at, warns, walk):
    """The SubtreeItem: its header fields before a payload that is the
    FastLZ stream, and the stream decompressed as a layer holding the inner
    hsin tree."""
    s = chunk["offset"]
    at, comp, size = s + sub["stream_at"], sub["stream_len"], sub["size"]
    if sub["compressed"] != 1 or at + comp > s + chunk["size"]:
        chunk["summary"] += ", the subtree not FastLZ as measured"
        return
    for fl in chunk["fields"]:
        fl["off"] -= sub["stream_at"]
    chunk["payload_base"], chunk["payload_len"] = at, comp
    chunk["summary"] = (f"SubtreeItem: {comp:,} bytes of FastLZ, "
                        f"{size:,} decompressed")
    if walk["layer"]:
        # a subtree inside a decompressed one is not opened again: a layer
        # is one decoding of the file's own bytes
        return
    if max(comp, size) > _HSIN_SUBTREE_CAP:
        warns.append(hit("inflate_bytes", _HSIN_SUBTREE_CAP, max(comp, size),
                         f"the subtree decompresses to {size:,} bytes; one past "
                         f"{_HSIN_SUBTREE_CAP:,} is not opened"))
        return
    image = nimod.fastlz_decompress(read_at(at, comp), size + 16)
    if image is None or len(image) != size:
        chunk["warnings"].append(defect(
            "parse.failed", f"the FastLZ stream does not decompress to the "
                            f"{size:,} bytes its header states"))
        return

    def image_at(off, n):
        return image[off:off + n]
    inner = _inner_tree(image_at, 0, len(image), warns, dict(walk, layer=True),
                        -1, "subtree")
    if not inner:
        return
    chunk["layer"] = {"name": "hsin subtree", "decoder": "fastlz",
                      "params": {"size": size}, "length": size,
                      "length_known": True,
                      "verdict": {"result": "length-only", "method": "exact-length",
                                  "detail": "FastLZ has no checksum; the stream "
                                            "fills the uncompressed_size exactly"}}
    chunk["layer_chunks"] = inner
    chunk["fields"].append(_f(None, 0, "decompressed", f"{size:,} bytes",
                              "an hsin tree of its own, walked as a layer"))


# ── Reaktor instruments (.ism) ──


def _inspect_reaktor(filepath, file_size, head, at):
    """A Reaktor instrument: its header and class record, and the NI metadata
    frame near the end of the file. The module body between them is
    Reaktor's own structure graph and is not decoded."""
    chunks = []
    if head[:4] == nimod.REAKTOR_MAGIC:
        chunks.append({"id": "NRKT", "offset": 0, "size": 4, "payload_base": 0,
                       "summary": "Reaktor instrument (KSModul)",
                       "fields": [_f(0, 4, "magic", "NRKT")], "warnings": []})
    chunks.append({"id": "class", "offset": at, "size": 12, "payload_base": at,
                   "summary": "class record: KSModul, a Reaktor instrument",
                   "fields": [_f(0, 1, "tag", "0x5b"),
                              _f(1, 4, "length", 7, enc="<I", raw=7),
                              _f(5, 7, "class", "KSModul")],
                   "warnings": []})
    if len(chunks) == 1:
        chunks[0]["summary"] = "Reaktor instrument (KSModul), older layout"
    warns = []
    f, read_at = _reader(filepath)
    with f:
        found = _trailing_frames(read_at, file_size, warns)
        if not found:
            warns.append(info("decode.partial",
                              "no hsin metadata frame was found; the module body "
                              "is not decoded"))
            return chunks, warns
        warns.append(info("decode.partial",
                          "the module body (Reaktor's structure graph) is not decoded"))
        for frames, problem, meta in found:
            fchunks, _items = _frame_chunks(frames, problem, warns, read_at=read_at)
            top = fchunks[0]
            top["summary"] = "NI metadata frame" + (f": '{meta['name']}'"
                                                    if meta.get("name") else "")
            top["fields"] = _meta_fields(meta) + top["fields"]
            chunks += fchunks
            if meta.get("version"):
                chunks[0]["summary"] += f", written by Reaktor {meta['version']}"
    return chunks, warns


def _trailing_frames(read_at, file_size, warns):
    """The outermost hsin trees in the last _REAKTOR_TAIL_CAP bytes, each as
    (frames, problem, metadata), in file order. A magic counts only where a
    whole frame tree reads from it."""
    window = min(file_size, _REAKTOR_TAIL_CAP)
    base = file_size - window
    tail = read_at(base, window)
    if window < file_size:
        warns.append(hit("read_bytes", _REAKTOR_TAIL_CAP, file_size,
                         f"the NI metadata frame was looked for in the last "
                         f"{_REAKTOR_TAIL_CAP:,} of {file_size:,} bytes"))
    found, looked = [], 0
    pos = tail.rfind(nimod.MAGIC)
    while pos >= 12:
        looked += 1
        if looked > _REAKTOR_SCAN_CAP:
            warns.append(hit("work_steps", _REAKTOR_SCAN_CAP, looked,
                             f"the search for the NI metadata frame stopped after "
                             f"{_REAKTOR_SCAN_CAP:,} hsin magics"))
            break
        fo = base + pos - 12
        frames, problem = nimod.read_frames(read_at, fo, file_size, _HSIN_FRAME_CAP,
                                            _HSIN_DEPTH_CAP)
        if frames and problem in (None, "frames", "depth"):
            end = fo + frames[0]["size"]
            # a tree found earlier that this one holds is one of its frames
            found = [t for t in found if not fo < t[0][0]["at"] < end]
            meta = nimod.parse_hsin(tail[fo - base:end - base]) or {}
            found.append((frames, problem, meta))
        pos = tail.rfind(nimod.MAGIC, 0, pos)
    found.reverse()
    return found


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
