"""SigMF recording and bare IQ capture walker (the audio-to-RF bridge).

SigMF is a JSON sidecar (.sigmf-meta) describing a headerless raw sample stream
(.sigmf-data): a `global` object (datatype, sample_rate, hardware), a `captures`
list (each a frequency-tagged segment of the stream), and optional `annotations`
(labeled sample ranges). Bare IQ captures (.cu8, .c16, GQRX .raw, PortaPack
.C16 + .TXT sidecar) are the same headerless stream with geometry inferred from
the extension, a sidecar, or the filename.

Walk semantics are anchored on the data plane: every capture and annotation is
a real byte region of the sample stream, so `carve --offset` extracts a segment.
Inspect-only; sha512 verification and DC/clipping stats are deep-gated (the point
is a multi-gigabyte capture). No numpy: a bounded struct read is enough.
"""

import hashlib
import json
import os
import re
import struct

from acidcat.core.walk.base import _f

_EXT_KEY_CAP = 48                  # metadata keys to list (mirrors _ZONE_CAP)
_ANNOTATION_CAP = 64
_DEEP_READ = 8 * 1024 * 1024       # DC/clipping sampled from the first 8 MB

_GQRX_RE = re.compile(r"gqrx_(\d{8})_(\d{6})_(\d+)_(\d+)_fc\.raw$", re.I)

# bare-capture extension -> (SigMF datatype, hexdump note)
_IQ_EXT_GEOMETRY = {
    ".cu8": ("cu8", "unsigned 8-bit: silence is 0x80, not 0x00"),
    ".cs8": ("ci8", ""), ".c8": ("ci8", ""),
    ".cs16": ("ci16_le", ""), ".c16": ("ci16_le", ""),
    ".cf32": ("cf32_le", ""), ".cfile": ("cf32_le", ""),
}


def _gqrx_name(path):
    """A GQRX capture filename match (gqrx_DATE_TIME_center_rate_fc.raw), or None.
    Shared with sniff() so a bare .raw is accepted only under this convention."""
    return _GQRX_RE.search(os.path.basename(path))


def _parse_datatype(dt):
    """SigMF core:datatype -> geometry dict, or None. Grammar:
    (c|r)(i|u|f)(8|16|32|64)(_le|_be)?; a multibyte type needs an endian suffix."""
    m = re.fullmatch(r"([cr])([iuf])(8|16|32|64)(_le|_be)?", dt or "")
    if not m:
        return None
    bits = int(m.group(3))
    if bits > 8 and not m.group(4):
        return None
    cplx = m.group(1) == "c"
    return {"cplx": cplx, "kind": {"i": "int", "u": "uint", "f": "float"}[m.group(2)],
            "bits": bits, "endian": (m.group(4) or "")[1:],
            "sample_bytes": bits // 8 * (2 if cplx else 1)}


def _dt_note(g):
    cx = "complex" if g["cplx"] else "real"
    en = {"le": " little-endian", "be": " big-endian", "": ""}[g["endian"]]
    return f"{cx} {g['kind']}{g['bits']}{en}, {g['sample_bytes']} B/sample"


def _component_fmt(geo):
    e = ">" if geo["endian"] == "be" else "<"
    code = {("int", 8): "b", ("uint", 8): "B", ("int", 16): "h", ("uint", 16): "H",
            ("int", 32): "i", ("uint", 32): "I", ("float", 32): "f",
            ("float", 64): "d"}.get((geo["kind"], geo["bits"]))
    return (e + code) if code else None


def _unpack(raw, geo):
    fmt = _component_fmt(geo)
    if not fmt:
        return []
    cs = struct.calcsize(fmt)
    return [struct.unpack_from(fmt, raw, o)[0]
            for o in range(0, len(raw) - cs + 1, cs)]


def _first_samples(data_path, geo, n=4):
    if not geo:
        return ""
    try:
        with open(data_path, "rb") as f:
            raw = f.read(geo["sample_bytes"] * n)
    except OSError:
        return ""
    vals = _unpack(raw, geo)
    if geo["cplx"]:
        return " ".join(f"({vals[i]:g},{vals[i + 1]:g})"
                        for i in range(0, len(vals) - 1, 2))
    return " ".join(f"{v:g}" for v in vals)


def _clip_count(vals, geo):
    if geo["kind"] == "float":
        return sum(1 for v in vals if abs(v) >= 1.0)
    if geo["kind"] == "uint":
        hi = (1 << geo["bits"]) - 1
        return sum(1 for v in vals if v == 0 or v >= hi)
    hi = (1 << (geo["bits"] - 1)) - 1
    return sum(1 for v in vals if v >= hi or v <= -hi - 1)


def _deep_stats(chunk, data_path, geo):
    """DC offset (I/Q means) and clipping percentage from the first 8 MB."""
    try:
        with open(data_path, "rb") as f:
            raw = f.read(_DEEP_READ)
    except OSError:
        return
    vals = _unpack(raw, geo)
    if not vals:
        return
    if geo["cplx"] and len(vals) >= 2:
        i_ch, q_ch = vals[0::2], vals[1::2]
        chunk["fields"].append(_f(None, 0, "dc_offset_i",
                                  f"{sum(i_ch) / len(i_ch):.2f}", "mean of first 8 MB"))
        chunk["fields"].append(_f(None, 0, "dc_offset_q",
                                  f"{sum(q_ch) / len(q_ch):.2f}", "mean of first 8 MB"))
    clip = _clip_count(vals, geo)
    chunk["fields"].append(_f(None, 0, "clipping",
                              f"{100 * clip / len(vals):.2f}%", "components at full scale"))


def _sha512(data_path):
    h = hashlib.sha512()
    with open(data_path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _samples_chunk(data_path, data_size, geo, dt, deep):
    sb = geo["sample_bytes"] if geo else 0
    n_samp = data_size // sb if sb else 0
    fields = []
    if geo and data_size:
        preview = _first_samples(data_path, geo)
        if preview:
            fields.append(_f(0, min(16, data_size), "first_samples", preview,
                             "I,Q pairs" if geo["cplx"] else "samples"))
    if sb:
        summ = f"{n_samp:,} x {dt or 'raw'} " + \
               ("complex " if geo["cplx"] else "") + "samples"
    else:
        summ = f"{data_size:,} bytes, geometry unknown"
    chunk = {"id": "samples", "offset": 0, "size": data_size, "payload_base": 0,
             "summary": summ, "fields": fields, "warnings": []}
    if deep and geo and data_size:
        _deep_stats(chunk, data_path, geo)
    return chunk, n_samp


def _pair_paths(path):
    low = path.lower()
    if low.endswith(".sigmf-meta"):
        stem = path[:-len(".sigmf-meta")]
    elif low.endswith(".sigmf-data"):
        stem = path[:-len(".sigmf-data")]
    else:
        stem = os.path.splitext(path)[0]
    return stem + ".sigmf-meta", stem + ".sigmf-data"


def inspect_sigmf(path, deep=False):
    meta_path, data_path = _pair_paths(path)
    warns = []
    g, captures, annotations, meta_ok = {}, [], [], False
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8", errors="replace") as f:
                m = json.loads(f.read())
            g = m.get("global") or {}
            captures = m.get("captures") or []
            annotations = m.get("annotations") or []
            meta_ok = True
        except (ValueError, OSError) as e:
            warns.append(f"sidecar JSON did not parse: {e.__class__.__name__}")
    else:
        warns.append("no .sigmf-meta sidecar; datatype unknown (SigMF requires the pair)")

    data_size = os.path.getsize(data_path) if os.path.isfile(data_path) else 0
    if not os.path.isfile(data_path):
        warns.append("no .sigmf-data beside this .sigmf-meta")

    dt = g.get("core:datatype", "")
    geo = _parse_datatype(dt)
    sb = geo["sample_bytes"] if geo else 0
    if dt and geo is None:
        warns.append(f"core:datatype {dt!r} does not parse; sample geometry unknown")
    elif geo and not geo["cplx"]:
        warns.append("datatype is real (rN); this is a scalar sample stream, not IQ")
    n_samp = data_size // sb if sb else 0
    if sb and data_size % sb:
        warns.append(f"data size {data_size:,} is not a whole number of {dt} "
                     f"samples ({data_size % sb} bytes trail)")
    fs = g.get("core:sample_rate")
    dur = (n_samp / fs) if (fs and n_samp) else None

    chunks = []
    if meta_ok:
        sha = g.get("core:sha512")
        sha_note = "not verified (use --deep)"
        if deep and sha and os.path.isfile(data_path):
            sha_note = "verified" if _sha512(data_path) == sha.lower() else "MISMATCH"
            if sha_note == "MISMATCH":
                warns.append("core:sha512 does not match the data file")
        gfields = []
        if dt:
            gfields.append(_f(None, 0, "datatype", dt,
                              _dt_note(geo) if geo else "unparsed"))
        if fs is not None:
            gfields.append(_f(None, 0, "sample_rate", fs, "Hz"))
        if sb:
            gfields.append(_f(None, 0, "sample_count", f"{n_samp:,}",
                              "derived: data size / sample bytes"))
        if dur is not None:
            gfields.append(_f(None, 0, "duration", f"{dur:.1f}", "s, derived"))
        for key, label in (("core:version", "version"), ("core:author", "author"),
                           ("core:description", "description"),
                           ("core:hw", "hardware"), ("core:recorder", "recorder")):
            if g.get(key):
                gfields.append(_f(None, 0, label, str(g[key])[:120]))
        if sha:
            gfields.append(_f(None, 0, "sha512", sha[:16] + "...", sha_note))
        base_keys = {"core:datatype", "core:sample_rate", "core:version",
                     "core:author", "core:description", "core:hw",
                     "core:recorder", "core:sha512"}
        extra = [k for k in g if k not in base_keys]
        for k in extra[:_EXT_KEY_CAP]:
            gfields.append(_f(None, 0, k, str(g[k])[:120]))
        if len(extra) > _EXT_KEY_CAP:
            warns.append(f"listing the first {_EXT_KEY_CAP} of {len(extra)} global keys")
        dur_s = f", {dur:.1f} s" if dur is not None else ""
        chunks.append({
            "id": "global", "offset": 0, "size": 0, "payload_base": 0,
            "summary": (f"{dt or 'unknown'}  "
                        + (f"{fs / 1e6:g} Msps  " if fs else "")
                        + f"{n_samp:,} samples{dur_s}  ({os.path.basename(meta_path)})"),
            "fields": gfields, "warnings": [],
        })

        for i, c in enumerate(captures):
            s0 = c.get("core:sample_start", 0) or 0
            nxt = (captures[i + 1].get("core:sample_start", n_samp)
                   if i + 1 < len(captures) else n_samp)
            off, span = s0 * sb, max(0, nxt - s0)
            fc = c.get("core:frequency")
            cf = []
            if fc is not None:
                cf.append(_f(None, 0, "frequency", fc, "Hz, center; maps to the DC bin"))
            cf.append(_f(None, 0, "sample_start", s0, "", xref=off))
            for k, v in c.items():
                if k not in ("core:frequency", "core:sample_start"):
                    cf.append(_f(None, 0, k.split(":")[-1], str(v)[:120]))
            if sb and off > data_size:
                warns.append(f"capture[{i}] sample_start implies offset "
                             f"0x{off:x} past EOF")
            chunks.append({
                "id": f"capture[{i}]", "offset": off, "size": span * sb,
                "summary": f"@ {(fc or 0) / 1e6:.3f} MHz, sample {s0:,}+{span:,}",
                "fields": cf, "warnings": [], "payload_base": off,
            })

        for i, a in enumerate(annotations[:_ANNOTATION_CAP]):
            s0 = a.get("core:sample_start", 0) or 0
            cnt = a.get("core:sample_count", 0) or 0
            off = s0 * sb
            lab = a.get("core:label") or a.get("core:generator") or f"annotation {i}"
            af = [_f(None, 0, "sample_start", s0, "", xref=off),
                  _f(None, 0, "sample_count", cnt)]
            for k, v in a.items():
                if k not in ("core:sample_start", "core:sample_count"):
                    af.append(_f(None, 0, k.split(":")[-1], str(v)[:120]))
            chunks.append({
                "id": f"annotation[{i}]", "offset": off, "size": cnt * sb,
                "summary": f"{lab}  sample {s0:,}+{cnt:,}",
                "fields": af, "warnings": [], "payload_base": off,
            })
        if len(annotations) > _ANNOTATION_CAP:
            warns.append(f"listing the first {_ANNOTATION_CAP} of "
                         f"{len(annotations)} annotations")

    samples, _ = _samples_chunk(data_path, data_size, geo, dt, deep)
    chunks.append(samples)
    return chunks, warns


def inspect_iq(path, deep=False):
    size = os.path.getsize(path)
    ext = os.path.splitext(path.lower())[1]
    warns = []
    dt = None
    fs = fc = dtime = None
    provenance = ""
    meta_fields = []

    gm = _gqrx_name(path)
    if gm:
        dt, provenance = "cf32_le", "GQRX filename"
        d, t = gm.group(1), gm.group(2)
        fc, fs = int(gm.group(3)), int(gm.group(4))
        dtime = f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}:{t[4:]}"

    txt = os.path.splitext(path)[0] + ".TXT"
    if os.path.isfile(txt):
        provenance = provenance or "PortaPack .TXT sidecar"
        try:
            with open(txt, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        meta_fields.append(_f(None, 0, k, v))
                        if k == "sample_rate" and v.isdigit():
                            fs = fs or int(v)
                        if k == "center_frequency" and v.isdigit():
                            fc = fc or int(v)
        except OSError:
            pass

    ext_note = ""
    if dt is None:                     # extension gives geometry, not metadata
        dt, ext_note = _IQ_EXT_GEOMETRY.get(ext, (None, ""))
        if dt:
            provenance = provenance or "extension"
    geo = _parse_datatype(dt) if dt else None
    sb = geo["sample_bytes"] if geo else 0

    chunks = []
    if fc is not None or fs is not None or dtime or meta_fields:
        have = {f["name"] for f in meta_fields}
        mf = []
        if fs is not None and "sample_rate" not in have:
            mf.append(_f(None, 0, "sample_rate", fs, "Hz"))
        if fc is not None and "center_frequency" not in have:
            mf.append(_f(None, 0, "center_frequency", fc, "Hz"))
        mf += meta_fields
        if dtime and "datetime" not in have:
            mf.append(_f(None, 0, "datetime", dtime))
        chunks.append({
            "id": "metadata", "offset": 0, "size": 0, "payload_base": 0,
            "summary": f"capture metadata (from {provenance})",
            "fields": mf, "warnings": [],
        })

    samples, _ = _samples_chunk(path, size, geo, dt, deep)
    if ext_note and samples["fields"]:
        samples["fields"][0]["note"] = (samples["fields"][0]["note"] + "; "
                                        + ext_note).strip("; ")
    elif ext_note:
        samples["fields"].append(_f(None, 0, "encoding", dt, ext_note))
    chunks.append(samples)

    if dt is None:
        warns.append("unknown IQ encoding; geometry from extension only")
    if fs is None:
        warns.append("sample rate unknown; duration not derivable")
    if sb and size % sb:
        warns.append(f"data size {size:,} is not a whole number of {dt} "
                     f"samples ({size % sb} bytes trail)")
    return chunks, warns
