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
    with _open(filepath) as f:
        data = f.read(min(file_size, 16 * 1024 * 1024))
    if nimod.is_ni_ksd(data):
        meta, kind = nimod.parse_ksd(data), "ksd"
    elif nimod.is_ni_nksf(data):
        meta, kind = nimod.parse_nksf(data), "nksf"
    else:
        meta, kind = nimod.parse_hsin(data), "hsin"
    if not meta and kind == "hsin" and nimod.is_ni_hsin(data):
        # a tree with no SoundInfoItem (a Reaktor 6 ensemble) is still a tree
        return _hsin_structure(filepath, file_size)
    if not meta:
        raise _Unsupported("not a recognized Native Instruments preset")
    order = ["name", "product", "plugin", "author", "vendor", "bank", "comment",
             "description", "device_type", "version", "tempo", "genre", "key"]
    fields = [_f(None, 0, k, _text(meta[k])) for k in order if meta.get(k)]
    for k in meta:
        if k not in order:
            fields.append(_f(None, 0, k, _text(meta[k])))
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


# ── the hsin frame tree, walked for its structure ──

def _hsin_structure(filepath, file_size):
    """An hsin tree whose metadata could not be read: every frame as a chunk,
    with its header and item records, and a note that there is no metadata."""
    f, read_at = _reader(filepath)
    with f:
        frames, problem = nimod.read_frames(read_at, 0, file_size, _HSIN_FRAME_CAP,
                                           _HSIN_DEPTH_CAP)
    if not frames:
        raise _Unsupported("not a recognized Native Instruments preset")
    warns = [info("decode.partial",
                  "the frame tree holds no SoundInfoItem (name, author, vendor); "
                  "its frames are walked, their item data is not decoded")]
    chunks = _frame_chunks(frames, problem, warns)
    end = frames[0]["size"]
    if end < file_size:
        warns.append(info("container.trailing",
                          f"{file_size - end:,} bytes follow the root frame"))
    return chunks, warns


def _frame_chunks(frames, problem, warns, root_id="hsin"):
    """Chunks for frames from formats/ni.read_frames: a chunk per frame, its
    reference and header as fields before its payload, and an `items` chunk
    for the item records opening its data section."""
    chunks = []
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
            dom, iid = items[0][1], items[0][2]
        elif fr["ref_fields"] is not None:
            dom, iid = fr["ref_fields"][1], fr["ref_fields"][2]
        else:
            dom, iid = "?", None
        # item 1 ends a stack; a frame opening on it is named by number
        what = nimod.ITEM_NAMES.get(iid, "") if iid != 1 else ""
        what = what or f"item {iid}"
        cid = root_id if fr["ref"] is None else f"{dom} {iid}"
        chunks.append({"id": cid, "offset": start, "size": at + fs - base,
                       "summary": f"hsin frame: {dom} {what}, {fs:,} bytes",
                       "fields": fields, "warnings": [], "payload_base": base,
                       "payload_len": at + fs - base, "extent_len": at + fs - start})
        if items:
            ifields = []
            for pos, idom, i, ver, inner in items:
                rel = pos - base
                ifields += [_f(rel, 4, "domain", idom),
                            _f(rel + 4, 4, "item_id", i, nimod.ITEM_NAMES.get(i, ""),
                               enc="<I", raw=i),
                            _f(rel + 8, 4, "version", ver, enc="<I", raw=ver)]
                if inner is not None:
                    ifields.append(_f(rel + 12, 8, "inner_size", inner,
                                      "the records nested inside this one",
                                      enc="<Q", raw=inner))
            last = items[-1]
            stop = last[0] + (12 if last[4] is None else 20)
            chunks.append({"id": "items", "offset": base, "size": stop - base,
                           "summary": f"{len(items)} item record(s)",
                           "fields": ifields, "warnings": [], "payload_base": base,
                           "payload_len": stop - base, "extent_len": stop - base})
    if problem == "frames":
        warns.append(hit("list_rows", _HSIN_FRAME_CAP, _HSIN_FRAME_CAP + 1,
                         f"the tree holds more than {_HSIN_FRAME_CAP:,} frames; "
                         f"the first {_HSIN_FRAME_CAP:,} have a chunk each and the "
                         f"rest are not walked"))
    elif problem == "depth":
        warns.append(hit("depth", _HSIN_DEPTH_CAP, _HSIN_DEPTH_CAP + 1,
                         f"frames nest more than {_HSIN_DEPTH_CAP} deep; the walk "
                         f"stops there"))
    elif problem:
        warns.append(defect("parse.failed", f"frame tree: {problem}"))
    return chunks


# ── Reaktor instruments (.ism) ──

_META_ORDER = ["name", "product", "plugin", "author", "vendor", "bank", "comment",
               "description", "device_type", "version", "tempo", "genre", "key"]


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
                          "no hsin metadata frame was found; the module body is "
                          "not decoded"))
        return chunks, warns
    warns.append(info("decode.partial",
                      "the module body (Reaktor's structure graph) is not decoded"))
    for frames, problem, meta in found:
        fchunks = _frame_chunks(frames, problem, warns)
        top = fchunks[0]
        top["summary"] = "NI metadata frame" + (f": '{meta['name']}'"
                                                if meta.get("name") else "")
        top["fields"] += [_f(None, 0, k, str(meta[k])) for k in _META_ORDER
                          if meta.get(k)]
        top["fields"] += [_f(None, 0, k, str(v)) for k, v in meta.items()
                          if k not in _META_ORDER]
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
