"""VST 2 .fxp preset walker: the 'CcnK' container header, its fxMagic (preset
kind), the plugin id (a FourCC), the version fields, and the preset name. All
multi-byte fields are big-endian. Preset payload (params or an opaque chunk) is
reported as a region; it is plugin-specific, and only Xfer Serum 1's chunk is
decoded (core/formats/serum.py has its layout): the patch and wavetable zlib
streams are layers, the patch's parameters, file references and preset text
are fields."""

import struct

from acidcat.core.formats import serum as serummod
from acidcat.core.infra.findings import defect
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import _f, _open, _size

_FX_MAGIC = {
    b"FxCk": "regular preset (float params)",
    b"FPCh": "opaque-chunk preset",
    b"FxBk": "regular bank",
    b"FBCh": "opaque-chunk bank",
}
# a few common plugin ids (the FourCC each plugin registers); shown raw otherwise
_KNOWN_IDS = {b"XfsX": "Xfer Serum", b"NiMs": "NI Massive", b"syle": "LennarDigital Sylenth1"}

# the header is all a preset of any other plugin needs read
_FXP_HEAD_READ = 65536
# a Serum chunk is read whole to find its second stream; the largest of 913
# real presets was 4.2 MB
_FXP_READ_CAP = 64 * 1024 * 1024
# what either Serum stream may inflate to; the largest seen was 4.2 MB
_SERUM_INFLATE_CAP = 64 * 1024 * 1024


def _cstr(b):
    return b.split(b"\x00", 1)[0].decode("latin-1", errors="replace").strip()


def inspect_fxp(filepath):
    size = _size(filepath)
    with _open(filepath) as f:
        head = f.read(min(size, _FXP_HEAD_READ))
        if head[16:20] == serummod.FXP_PLUGIN_ID and size > len(head):
            head += f.read(min(size, _FXP_READ_CAP) - len(head))
    warns = []
    if head[:4] != b"CcnK":
        warns.append(defect("magic.mismatch", "missing CcnK magic"))
    if len(head) < 28:
        return ([{"id": "fxp", "offset": 0, "size": size,
                  "summary": "truncated FXP header", "fields": [],
                  "warnings": [defect("header.truncated", "header shorter than 28 bytes")],
                  "payload_base": 0}], warns)

    byte_size = struct.unpack_from(">I", head, 4)[0]
    fx_magic = head[8:12]
    version = struct.unpack_from(">I", head, 12)[0]
    fx_id = head[16:20]
    fx_version = struct.unpack_from(">I", head, 20)[0]
    num_count = struct.unpack_from(">I", head, 24)[0]
    kind = _FX_MAGIC.get(fx_magic, "unknown fxMagic")
    id_str = fx_id.decode("latin-1", errors="replace")
    plugin = _KNOWN_IDS.get(fx_id)
    is_bank = fx_magic in (b"FxBk", b"FBCh")

    fields = [
        _f(0x00, 4, "magic", "CcnK"),
        _f(0x04, 4, "byte_size", f"{byte_size:,}", "bytes after this field",
           enc=">I", raw=byte_size),
        _f(0x08, 4, "fx_magic", fx_magic.decode("latin-1", "replace"), kind),
        _f(0x0C, 4, "version", version),
        _f(0x10, 4, "plugin_id", id_str + (f" ({plugin})" if plugin else "")),
        _f(0x14, 4, "plugin_version", fx_version),
        # per the VST2 SDK this word is numPrograms only for a bank (fxSet);
        # for a single preset (fxProgram) it is numParams.
        _f(0x18, 4, "num_programs" if is_bank else "num_params", num_count),
    ]
    summary = f"VST {kind}, plugin {id_str}"
    name = ""
    if not is_bank and len(head) >= 56:
        name = _cstr(head[28:56])                      # 28-byte program name
        fields.append(_f(0x1C, 28, "preset_name", name or "(unnamed)"))
        if name:
            summary = f"'{name}', VST {kind}, plugin {id_str}"
    if is_bank and len(head) >= 156:
        current = struct.unpack_from(">I", head, 28)[0]
        fields.append(_f(0x1C, 4, "current_program", current, enc=">I", raw=current))
        fields.append(_f(0x20, 124, "reserved", "124 bytes", "reserved by the SDK"))

    cs_off = 156 if is_bank else 56                   # bank has 128 reserved bytes first
    chunk_size = None
    if fx_magic in (b"FPCh", b"FBCh") and len(head) >= cs_off + 4:
        chunk_size = struct.unpack_from(">I", head, cs_off)[0]
        fields.append(_f(cs_off, 4, "chunk_size", f"{chunk_size:,}",
                         "bytes of plugin chunk that follow", enc=">I", raw=chunk_size))

    # the header is the node's own: its payload starts where the described
    # fields end, so the normaliser does not fill the header in as unwalked
    hdr = max(fl["off"] + fl["len"] for fl in fields)
    for fl in fields:
        fl["off"] -= hdr
    chunks = [{"id": "CcnK", "offset": 0, "size": size - hdr, "summary": summary,
               "fields": fields, "warnings": [], "payload_base": hdr,
               "payload_len": size - hdr, "extent_len": size}]

    # opaque-chunk variants carry a length-prefixed plugin blob
    if chunk_size is not None:
        data_off = cs_off + 4
        n = max(0, min(chunk_size, size - data_off))
        if fx_id == serummod.FXP_PLUGIN_ID and fx_magic == b"FPCh":
            if size > _FXP_READ_CAP:
                warns.append(hit("read_bytes", _FXP_READ_CAP, size,
                                 "file is %d bytes; parsed the first %d"
                                 % (size, len(head))))
            serum = _serum(head[data_off:data_off + n], data_off, warns)
            if serum:
                chunks[0]["summary"] = serum.pop(0)
                return chunks + serum, warns
        # data_off already points past the 4-byte size field, so the
        # payload begins there and `size` is all of it.
        chunks.append({"id": "chunk", "offset": data_off,
                       "size": n, "payload_base": data_off,
                       "summary": f"opaque plugin chunk, {chunk_size:,} bytes",
                       "fields": [], "warnings": []})
    return chunks, warns


# ── Xfer Serum 1 ───────────────────────────────────────────────────


def _serum(chunk, at, warns):
    """[root summary, then the patch, tables and trailer chunks] for a Serum 1
    chunk, or None (with a finding) when its patch cannot be read."""
    try:
        sp = serummod.split_chunk(chunk, _SERUM_INFLATE_CAP)
    except serummod.ChunkError as e:
        warns.append(defect("parse.failed",
                            "the Serum chunk's patch stream does not inflate: %s" % e))
        return None
    if sp["patch"] is None:
        warns.append(hit("inflate_bytes", _SERUM_INFLATE_CAP, _SERUM_INFLATE_CAP + 1,
                         "the Serum patch inflates past %d bytes; not decoded"
                         % _SERUM_INFLATE_CAP))
        return None
    patch, n1 = sp["patch"], sp["patch_len"]
    info = serummod.patch_info(patch)
    name = info.get("name") or "(unnamed)"
    out = ["'%s', Xfer Serum 1 preset, %s-byte patch" % (name, format(len(patch), ","))]
    pf = [_f(None, 0, "inflated", "%s bytes" % format(len(patch), ","), "zlib")]
    if info.get("author"):
        pf.append(_f(None, 0, "author", info["author"], text=True))
    out.append({"id": "patch", "offset": at, "size": n1, "payload_base": at,
                "payload_len": n1, "extent_len": n1,
                "summary": "patch, %s bytes inflated from %s" % (
                    format(len(patch), ","), format(n1, ",")),
                "fields": pf, "warnings": [],
                "layer": _layer("Serum patch", len(patch)),
                "layer_chunks": _patch_layer(patch)})
    if sp["tables_len"] is None:
        # the second stream did not inflate: the rest is one region
        notes = [sp["tables_note"]]
        warns.extend(notes)
        out.append({"id": "tables", "offset": at + n1, "size": len(chunk) - n1,
                    "payload_base": at + n1, "summary": "%s bytes that do not inflate"
                    % format(len(chunk) - n1, ","), "fields": [], "warnings": notes})
        return out
    n2 = sp["tables_len"]
    out.append(_tables(sp["tables"], at + n1, n2, warns))
    notes = [sp["trailer_note"]] if sp["trailer_note"] else []
    warns.extend(notes)
    if sp["trailer_at"] is None:
        rest = len(chunk) - n1 - n2
        if rest:
            out.append({"id": "trailer", "offset": at + n1 + n2, "size": rest,
                        "payload_base": at + n1 + n2,
                        "summary": "%d of the trailer's 4 bytes" % rest,
                        "fields": [], "warnings": notes})
        return out
    t = sp["trailer"]
    out.append({"id": "trailer", "offset": at + sp["trailer_at"], "size": serummod.TRAILER,
                "payload_base": at + sp["trailer_at"],
                "summary": "where the wavetable stream starts",
                "fields": [_f(0, 4, "patch_stream_len", t,
                              "the patch stream's compressed length; "
                              + ("it matches" if t == n1 else "it is %d" % n1),
                              enc="<I", raw=t, xref=at + t)],
                "warnings": notes})
    end = sp["trailer_at"] + serummod.TRAILER
    if end < len(chunk):
        notes = [sp["stray_note"]]
        warns.extend(notes)
        out.append({"id": "stray", "offset": at + end, "size": len(chunk) - end,
                    "payload_base": at + end,
                    "summary": "%d bytes after the trailer" % (len(chunk) - end),
                    "fields": [], "warnings": notes})
    return out


def _layer(name, length):
    return {"name": name, "decoder": "zlib", "params": {"size": length},
            "length": length, "length_known": True,
            "verdict": {"result": "verified", "method": "adler32",
                        "detail": "zlib's own check"}}


def _region(cid, at, n, summary, fields):
    return {"id": cid, "offset": at, "size": n, "payload_base": at,
            "payload_len": n, "extent_len": n, "summary": summary,
            "fields": fields, "warnings": []}


def _patch_layer(patch):
    """The inflated patch record, positioned in itself: the regions whose
    meaning is known. The bytes between them are left to the normaliser."""
    out = []
    vals = serummod.params(patch)
    if vals:
        fields = []
        for i, v in enumerate(vals):
            name, note, kind = serummod.PARAMS.get(i, ("param[%d]" % i, "", ""))
            note = "0-1 spans " + note if note else ("stored 0 or 1" if kind else
                                                      "normalized 0-1")
            if i in serummod.NOTES:
                note = (note + "; " if note else "") + serummod.NOTES[i]
            value = ("on" if v >= 0.5 else "off") if kind == "switch" else round(v, 4)
            fields.append(_f(4 * i, 4, name, value, note, enc="<f", raw=v))
        p = dict(zip((serummod.PARAMS[i][0] for i in serummod.PARAMS
                      if i < len(vals)), vals))
        summary = "%d parameters, %d named" % (
            len(vals), sum(1 for i in serummod.PARAMS if i < len(vals)))
        if "filter_cutoff" in p:
            summary += ": master %.2f, osc A %s, osc B %s, filter %s at %.2f" % (
                p["master_vol"], _on(p["osc_a_on"]), _on(p["osc_b_on"]),
                _on(p["filter_on"]), p["filter_cutoff"])
        out.append(_region("params", serummod.PARAMS_AT, 4 * len(vals), summary, fields))
    order = serummod.fx_order(patch)
    if order is not None:
        out.append(_region("fx_order", serummod.FX_ORDER_AT, 4 * serummod.FX_MODULES,
                           "effects rack order " + " ".join(map(str, order)),
                           [_f(0, 4 * serummod.FX_MODULES, "fx_order",
                               " ".join(map(str, order)),
                               "the ten effect modules as numbers, in rack order")]))
    info = serummod.patch_info(patch)
    if "noise_sample" in info:
        L = serummod.PATH_LEN
        out.append(_region(
            "sources", serummod.OSC_A_TABLE_AT, 3 * L,
            "osc A %s, osc B %s, noise %s" % tuple(
                info[k] or "-" for k in ("osc_a_table", "osc_b_table", "noise_sample")),
            [_f(0, L, "osc_a_wavetable", info["osc_a_table"],
                "relative to Serum's Tables folder", text=True),
             _f(L, L, "osc_b_wavetable", info["osc_b_table"],
                "relative to Serum's Tables folder", text=True),
             _f(2 * L, L, "noise_sample", info["noise_sample"],
                "relative to Serum's Noises folder", text=True)]))
    if "macro_labels" in info:
        base = serummod.INFO_AT
        fields = [_f(0, serummod.NAME_LEN, "preset_name", info["name"], text=True),
                  _f(serummod.AUTHOR_AT - base, serummod.AUTHOR_LEN, "author",
                     info["author"], text=True),
                  _f(serummod.DESCRIPTION_AT - base, serummod.DESCRIPTION_LEN,
                     "description", info["description"], text=True)]
        for i, label in enumerate(info["macro_labels"]):
            fields.append(_f(serummod.MACRO_LABEL_AT - base + i * serummod.MACRO_LABEL_LEN,
                             serummod.MACRO_LABEL_LEN, "macro%d_label" % (i + 1),
                             label, text=True))
        out.append(_region("info", base, serummod.INFO_END - base,
                           "'%s'%s" % (info["name"], " by " + info["author"]
                                       if info["author"] else ""), fields))
    return out


def _on(v):
    return "on" if v >= 0.5 else "off"


def _tables(tables, at, n, warns):
    """The wavetable stream's chunk: its layer when it inflated, its size
    when the cap stopped it."""
    chunk = {"id": "tables", "offset": at, "size": n, "payload_base": at,
             "payload_len": n, "extent_len": n, "fields": [], "warnings": []}
    if tables is None:
        w = hit("inflate_bytes", _SERUM_INFLATE_CAP, _SERUM_INFLATE_CAP + 1,
                "the wavetable stream inflates past %d bytes; not decoded"
                % _SERUM_INFLATE_CAP)
        warns.append(w)
        chunk["summary"] = "wavetable stream, %s bytes compressed" % format(n, ",")
        return chunk
    L = len(tables)
    tun = serummod.tuning_at(tables)
    audio = L if tun is None else tun
    frames, extra = divmod(audio // 4, serummod.FRAME_SAMPLES)
    chunk["fields"] = [_f(None, 0, "inflated", "%s bytes" % format(L, ","), "zlib"),
                       _f(None, 0, "samples", audio // 4, "float32 LE")]
    if not L:
        chunk["summary"] = "no embedded wavetables"
        return chunk
    what = "%s samples" % format(audio // 4, ",")
    if audio and not extra and audio % 4 == 0:
        what = "%d frame%s of %d samples" % (frames, "" if frames == 1 else "s",
                                             serummod.FRAME_SAMPLES)
        chunk["fields"].append(_f(None, 0, "frames", frames,
                                  "%d samples a frame" % serummod.FRAME_SAMPLES))
    chunk["summary"] = "embedded wavetables, %s%s" % (
        what, ", and a tuning" if tun is not None else "")
    chunk["layer"] = _layer("Serum wavetables", L)
    lay = []
    if audio:
        lay.append(_region("samples", 0, audio, what, []))
    if tun is not None:
        first = tables[tun:tun + 120].split(b"\n", 1)[0].rstrip(b"\r")
        lay.append(_region("tuning", tun, L - tun,
                           "AnaMark tuning, %s bytes" % format(L - tun, ","),
                           [_f(0, len(first), "first_line",
                               first.decode("latin-1"), text=True)]))
    chunk["layer_chunks"] = lay
    return chunk
