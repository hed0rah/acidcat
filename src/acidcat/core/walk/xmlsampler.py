"""TAL-Sampler and UVI program walkers. The layouts are in
core/formats/xmlsampler.py."""

from acidcat.core.formats import xmlsampler as xsmod
from acidcat.core.infra.findings import environment
from acidcat.core.infra.limits import hit
from acidcat.core.infra.source import as_source
from acidcat.core.walk.base import Unsupported, _f, _open, _size

_XS_READ_CAP = 32 * 1024 * 1024
# sample elements given a node each; the rest are counted
_XS_ZONE_CAP = 4096
_MISSING_NAMED = 5


def _read(filepath):
    size = _size(filepath)
    with _open(filepath) as fh:
        data = fh.read(min(size, _XS_READ_CAP))
    warns = []
    if size > _XS_READ_CAP:
        warns.append(hit("read_bytes", _XS_READ_CAP, size,
                         f"the file is {size:,} bytes; the first "
                         f"{_XS_READ_CAP // (1 << 20)} MB were read"))
    return data, warns


def _field(attrs, key, name=None, base=0):
    v = attrs.get(key)
    if v is None:
        return None
    value, at, n = v
    return _f(at - base, n, name or key, value)


def _zone_chunks(data, elems, total, kind, keys, label_key, warns):
    chunks = []
    for start, attrs in elems:
        end = data.find(b">", start) + 1
        fields = [f for f in (_field(attrs, k, n, start) for k, n in keys) if f]
        label = attrs.get(label_key, ("", 0, 0))[0]
        chunks.append({"id": kind, "offset": start, "size": end - start,
                       "summary": f"{kind} {label}".rstrip(), "fields": fields,
                       "warnings": [], "payload_base": start})
    if total > len(elems):
        warns.append(hit("list_rows", _XS_ZONE_CAP, total,
                         f"{total:,} {kind} elements; the first "
                         f"{_XS_ZONE_CAP:,} have a node each"))
    return chunks


def inspect_talsmpl(filepath):
    data, warns = _read(filepath)
    if not xsmod.is_tal(data):
        raise Unsupported("not a TAL-Sampler program")
    progs, _n = xsmod.elements(data, b"program", 1)
    tal, _n = xsmod.elements(data, b"tal", 1)
    samples, total = xsmod.elements(data, b"multisample", _XS_ZONE_CAP)
    urls = list(dict.fromkeys(a["url"][0] for _s, a in samples if "url" in a))
    top = {"id": "talsmpl", "offset": 0, "size": len(data),
           "summary": f"TAL-Sampler program: {total:,} sample zone(s)",
           "fields": [], "warnings": [], "payload_base": 0}
    if tal:
        f = _field(tal[0][1], "version")
        if f:
            top["fields"].append(f)
    if progs:
        for key, name in (("programname", "name"), ("category", "category"),
                          ("path", "saved_as")):
            f = _field(progs[0][1], key, name)
            if f and f["value"]:
                top["fields"].append(f)
        if progs[0][1].get("programname"):
            top["summary"] = (f"TAL-Sampler program {progs[0][1]['programname'][0]!r}: "
                              f"{total:,} sample zone(s)")
    top["fields"] += [_f(None, 0, "zones", total), _f(None, 0, "sample_files", len(urls))]
    keys = (("url", "sample"), ("urlRelativeToPresetDirectory", "preset_relative"),
            ("rootkey", "root_key"), ("lowkey", "key_low"), ("highkey", "key_high"),
            ("velocitystart", "velocity_low"), ("velocityend", "velocity_high"),
            ("startsample", "sample_start"), ("endsample", "sample_end"),
            ("loopstartsample", "loop_start"), ("loopendsample", "loop_end"),
            ("loopenabled", "loop"))
    return [top] + _zone_chunks(data, samples, total, "multisample", keys, "url", warns), warns


def inspect_uvip(filepath):
    data, warns = _read(filepath)
    if not xsmod.is_uvi(data):
        raise Unsupported("not a UVI program")
    progs, _n = xsmod.elements(data, b"Program", 1)
    groups, total = xsmod.elements(data, b"Keygroup", _XS_ZONE_CAP)
    players, nplayers = xsmod.elements(data, b"SamplePlayer", _XS_ZONE_CAP)
    if nplayers > len(players):
        warns.append(hit("list_rows", _XS_ZONE_CAP, nplayers,
                         f"{nplayers:,} sample players; the first "
                         f"{_XS_ZONE_CAP:,} were read"))
    paths = list(dict.fromkeys(a["SamplePath"][0].replace("\\", "/")
                               for _s, a in players if a.get("SamplePath")))
    top = {"id": "uvip", "offset": 0, "size": len(data),
           "summary": f"UVI program: {total:,} keygroup(s), {len(paths):,} sample file(s)",
           "fields": [], "warnings": [], "payload_base": 0}
    if progs:
        for key, name in (("DisplayName", "name"), ("ProgramPath", "saved_as"),
                          ("Polyphony", "polyphony")):
            f = _field(progs[0][1], key, name)
            if f and f["value"]:
                top["fields"].append(f)
    top["fields"] += [_f(None, 0, "keygroups", total), _f(None, 0, "sample_files", len(paths))]
    keys = (("DisplayName", "name"), ("LowKey", "key_low"), ("HighKey", "key_high"),
            ("LowVelocity", "velocity_low"), ("HighVelocity", "velocity_high"))
    chunks = [top] + _zone_chunks(data, groups, total, "keygroup", keys, "DisplayName", warns)
    for start, attrs in players:
        end = data.find(b">", start) + 1
        f = _field(attrs, "SamplePath", "sample", start)
        chunks.append({"id": "sampleplayer", "offset": start, "size": end - start,
                       "summary": f"sample {f['value']}" if f else "sample player",
                       "fields": [f] if f else [], "warnings": [], "payload_base": start})
    _check(filepath, paths, warns)
    return chunks, warns


def _check(filepath, paths, warns):
    """UVI sample paths are relative to the program file."""
    src = as_source(filepath)
    if src.path is None:
        if paths:
            warns.append(environment("sibling.unchecked",
                                     f"names {len(paths):,} sample file(s); not looked "
                                     f"for, the file was read from memory"))
        return
    missing = []
    for p in paths:
        sib = src.sibling(p)
        if sib is None:
            missing.append(p)
        else:
            sib.close()
    if missing:
        named = ", ".join(repr(p) for p in missing[:_MISSING_NAMED])
        more = len(missing) - _MISSING_NAMED
        warns.append(environment(
            "sibling.missing",
            f"{len(missing):,} of {len(paths):,} sample files are not where the "
            f"program says: {named}" + (f" and {more:,} more" if more > 0 else "")))
