"""SFZ instrument walker. The tokenizer is in core/formats/sfz.py.

One node for the whole file carrying the counts, then one per header section
with every opcode placed on its value's bytes. The samples a region names are
looked for beside the file (following `default_path`); a missing one is an
environment finding, not damage to the SFZ."""

from acidcat.core.formats import sfz as sfzmod
from acidcat.core.infra.findings import environment
from acidcat.core.infra.limits import hit
from acidcat.core.infra.source import as_source
from acidcat.core.walk.base import Unsupported, _f, _open, _size

# an SFZ is text; the largest of 783 real ones was 158 KB
_SFZ_READ_CAP = 16 * 1024 * 1024
# sections given a node each; the rest are counted in the file node
_SFZ_SECTION_CAP = 4096
# distinct sample files looked for on disk
_SFZ_SAMPLE_CHECK_CAP = 8192
# missing samples named in the finding; the rest are counted
_MISSING_NAMED = 5


def inspect_sfz(filepath):
    size = _size(filepath)
    with _open(filepath) as fh:
        data = fh.read(min(size, _SFZ_READ_CAP))
    warns = []
    if size > _SFZ_READ_CAP:
        warns.append(hit("read_bytes", _SFZ_READ_CAP, size,
                         f"the file is {size:,} bytes; the first "
                         f"{_SFZ_READ_CAP // (1 << 20)} MB were read"))
    sections, directives = sfzmod.tokenize(data)
    if not any(s["header"] for s in sections):
        raise Unsupported("no SFZ header in the file")
    defs = sfzmod.defines(directives)
    counts = {}
    for s in sections:
        counts[s["header"]] = counts.get(s["header"], 0) + 1

    # the sample every region plays: its own, else its group's, master's or
    # global's, each level reset when a new one of the level above opens
    default_path = ""
    level = {"global": {}, "master": {}, "group": {}}
    samples = []
    for s in sections:
        ops = {k: sfzmod.expand(v, defs) for k, _ka, v, _va, _vl in s["opcodes"]}
        h = s["header"]
        if h == "control" and "default_path" in ops:
            default_path = ops["default_path"].replace("\\", "/")
        if h == "global":
            level = {"global": ops, "master": {}, "group": {}}
        elif h == "master":
            level["master"], level["group"] = ops, {}
        elif h == "group":
            level["group"] = ops
        elif h == "region":
            val = (ops.get("sample") or level["group"].get("sample")
                   or level["master"].get("sample") or level["global"].get("sample"))
            p = sfzmod.sample_path(val, default_path) if val else None
            if p:
                samples.append(p)
    unique = list(dict.fromkeys(samples))

    top = {"id": "sfz", "offset": 0, "size": len(data),
           "summary": (f"SFZ instrument: {counts.get('region', 0):,} region(s), "
                       f"{counts.get('group', 0):,} group(s), "
                       f"{len(unique):,} sample file(s)"),
           "fields": [_f(None, 0, "regions", counts.get("region", 0)),
                      _f(None, 0, "groups", counts.get("group", 0)),
                      _f(None, 0, "sample_files", len(unique))],
           "warnings": [], "payload_base": 0}
    for h in ("control", "global", "master", "curve", "effect", "midi"):
        if counts.get(h):
            top["fields"].append(_f(None, 0, f"{h}_sections", counts[h]))
    for text, at, n in directives:
        top["fields"].append(_f(at, n, "directive", text))
    if default_path:
        top["fields"].append(_f(None, 0, "default_path", default_path))

    _check_files(filepath, unique, sfzmod.includes(directives), warns,
                 fragment_shaped="control" not in counts)

    chunks = [top]
    for i, s in enumerate(sections):
        if i >= _SFZ_SECTION_CAP:
            warns.append(hit("list_rows", _SFZ_SECTION_CAP, len(sections),
                             f"{len(sections):,} sections; the first "
                             f"{_SFZ_SECTION_CAP:,} have a node each"))
            break
        at, end = s["at"], s["end"]
        fields = [_f(va - at, vl, k, v) for k, _ka, v, va, vl in s["opcodes"]]
        name = s["header"] or "preamble"
        label = next((v for k, _ka, v, _va, _vl in s["opcodes"]
                      if k in ("sample", "group_label", "region_label", "sw_label")),
                     "")
        chunks.append({"id": name, "offset": at, "size": end - at,
                       "summary": f"<{name}> {label}".rstrip() if s["header"]
                                  else "opcodes before the first header",
                       "fields": fields, "warnings": [], "payload_base": at})
    return chunks, warns


def _check_files(filepath, unique, includes, warns, fragment_shaped):
    src = as_source(filepath)
    if src.path is None:
        if unique or includes:
            warns.append(environment(
                "sibling.unchecked",
                f"names {len(unique):,} sample file(s); not looked for, the "
                f"file was read from memory"))
        return
    checked = unique[:_SFZ_SAMPLE_CHECK_CAP]
    if len(unique) > _SFZ_SAMPLE_CHECK_CAP:
        warns.append(hit("list_rows", _SFZ_SAMPLE_CHECK_CAP, len(unique),
                         f"{len(unique):,} sample files; the first "
                         f"{_SFZ_SAMPLE_CHECK_CAP:,} were looked for"))
    missing = [p for p in checked if not _beside(src, p)]
    if missing:
        named = ", ".join(repr(p) for p in missing[:_MISSING_NAMED])
        more = len(missing) - _MISSING_NAMED
        msg = (f"{len(missing):,} of {len(checked):,} sample files are not where "
               f"the SFZ says: {named}" + (f" and {more:,} more" if more > 0 else ""))
        if fragment_shaped and len(missing) == len(checked):
            # a file another SFZ #includes resolves its paths from the
            # including file, which this walk cannot see
            msg += (". With no <control> header this may be a fragment another "
                    "SFZ includes, whose paths resolve from that file")
        warns.append(environment("sibling.missing", msg))
    for name, _at, _n in includes:
        if not _beside(src, name.replace("\\", "/")):
            warns.append(environment("sibling.missing",
                                     f"includes {name!r} and it is not beside "
                                     f"this file"))


def _beside(src, rel):
    """True when `rel` exists relative to the SFZ's directory. Read only: the
    sibling is opened to prove it is a file and closed at once."""
    sib = src.sibling(rel)
    if sib is None:
        return False
    sib.close()
    return True
