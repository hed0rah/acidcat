"""The descriptor interpreter: walk the container, apply the field specs,
emit the walkers' exact chunk/field model.

interpret() is the descriptor-driven twin of a hand-written walker: same
(label, chunks, file_warnings) shape, same field dicts (walk/base._f), same
degrade-never-raise contract, so every consumer (inspect, TUI, probe,
anomalies) could read it unchanged. Byte-exactness against the walker is
asserted by tests/test_grammar_wav.py across the corpus.

The caller-supplied ctx dict mirrors inspect_wav's: a file-global semantic
dict (sample_rate, bits, ...) populated through explicit Field.ctx keys.
Nothing consumes it yet; it exists so the scan/index decode-once handoff has
a place to grow without reshaping the API.
"""

from acidcat.core.walk.base import _f

from acidcat.core.grammar.helpers import _HELPERS, _RELATIONS, _SUMMARIES
from acidcat.core.grammar.model import Helper, Switch
from acidcat.core.grammar.strategies import STRATEGIES


def interpret(fmt, filepath, ctx=None):
    """Walk filepath per the Format descriptor.

    Returns (label, chunks, file_warnings), the walk_file shape."""
    if ctx is None:
        ctx = {}
    strat = STRATEGIES[fmt.container]
    raw_label = strat.label(filepath)     # None when the header is not this format
    label = raw_label or fmt.name
    regions, warns = strat.regions(filepath)
    chunks = []
    for r in regions:
        spec = fmt.regions.get(r.id)  # exact id match, like the walkers
        entry = {"id": r.id, "offset": r.offset, "size": r.size,
                 "summary": "", "fields": [], "warnings": []}
        if r.payload_base != r.offset + 8:
            # walker convention: the key appears only when non-default
            entry["payload_base"] = r.payload_base
        if spec is None or spec.kind == "payload":
            preview = r.payload[:16].hex(" ")
            entry["summary"] = f"unparsed, first bytes: {preview}"
        elif spec.min_len and len(r.payload) < spec.min_len:
            # all-or-nothing truncated path: no fields, the walker's exact warning
            entry["summary"] = "truncated"
            entry["warnings"] = [spec.min_len_msg.format(
                n=len(r.payload), min=spec.min_len)]
        else:
            entry["summary"], entry["fields"], entry["warnings"] = \
                _parse_struct(spec, r.payload, ctx)
        chunks.append(entry)
    # format-level rules only when the header was recognized as this format --
    # the walker returns early (no format rules) on an unrecognizable container
    if raw_label is not None:
        seen = [c["id"] for c in chunks]
        for rule in fmt.rules:
            w = rule.check(seen)
            if w:
                warns.append(w)
    return label, chunks, warns


def _parse_struct(spec, payload, ctx):
    """Parse an ordered struct region -> (summary, fields, warns). ``local``
    holds this region's raw values by name for guards/switches, Valid, and the
    relation + summary helpers (which read local, not ctx, so the EXTENSIBLE
    override does not perturb them); ``ctx`` is the file-global channel."""
    local = {}
    fields, warns, _pos = _parse_entries(spec.fields, payload, 0, local, ctx)
    for rel in spec.relations:
        warns += _RELATIONS[rel](local)
    summary = _SUMMARIES[spec.summary](local) if spec.summary else ""
    return summary, fields, warns


def _parse_entries(entries, payload, pos, local, ctx):
    """Walk a sequence of entries (Field or Switch) from ``pos`` -> (fields,
    warns, pos). Shared by the top level and each Switch case (recursion), so
    dispatch nests the same way the walkers do."""
    fields, warns = [], []
    for entry in entries:
        if isinstance(entry, Switch):
            f, w, pos = _apply_switch(entry, payload, pos, local, ctx)
            fields += f
            warns += w
            continue
        if isinstance(entry, Helper):
            f, w = _HELPERS[entry.name](payload, pos, local, ctx)
            fields += f
            warns += w
            pos += sum(fl["len"] for fl in f)   # advance past what it consumed
            continue
        fd = entry
        if fd.when and not all(g.holds(local, payload, pos) for g in fd.when):
            continue                  # guard fails: field absent, do not advance
        n = fd.type.length(payload, pos, ctx)
        if pos + n > len(payload):
            break                     # truncated: degrade, never raise
        disp, raw, enc = fd.type.decode(payload, pos, ctx)
        note = _note_for(fd, raw)
        # enc and raw travel together: a plain int (value == raw, enc None)
        # carries neither key, exactly like the walkers' _f calls
        fields.append(_f(pos, n, fd.name, disp, note, enc=enc,
                         raw=raw if enc is not None else None))
        if fd.valid is not None:      # plausibility range -> warning, never raise
            w = fd.valid.check(raw)
            if w:
                warns.append(w)
        local[fd.name] = raw
        if fd.ctx:
            ctx[fd.ctx] = raw         # published under the walker's semantic key
        pos += n
    return fields, warns, pos


def _note_for(fd, raw):
    """A field's note: a note-source (NoteLookup/NoteFlags) resolved against raw,
    else a static string, else the type's own label note (Enum), else empty."""
    n = fd.note
    if hasattr(n, "resolve"):
        return n.resolve(raw)
    if n:
        return n
    return fd.type.note(raw) if hasattr(fd.type, "note") else ""


def _apply_switch(sw, payload, pos, local, ctx):
    """Dispatch a Switch: all-or-nothing per case, case parsing bounded by the
    window (an earlier field clamped to the remaining payload) or the remaining
    payload when unwindowed. Emits nothing (and does not advance) when no case
    matches or the window is below the case minimum."""
    case = sw.cases.get(local.get(sw.on))
    if case is None:
        return _parse_entries(sw.default, payload, pos, local, ctx)
    if sw.window is not None:
        win = local.get(sw.window)
        if win is None:
            return [], [], pos
        avail = min(win, len(payload) - pos)
    else:
        avail = len(payload) - pos
    if avail < case.min_window:
        return [], [], pos            # all-or-nothing: minimum window not met
    # bound the case to its window; absolute offsets survive the slice
    return _parse_entries(case.fields, payload[:pos + avail], pos, local, ctx)
