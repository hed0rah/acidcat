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

from acidcat.core.grammar.strategies import STRATEGIES


def interpret(fmt, filepath, ctx=None):
    """Walk filepath per the Format descriptor.

    Returns (label, chunks, file_warnings), the walk_file shape."""
    if ctx is None:
        ctx = {}
    strat = STRATEGIES[fmt.container]
    label = strat.label(filepath) or fmt.name
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
    return label, chunks, warns


def _parse_struct(spec, payload, ctx):
    """Parse an ordered struct region -> (summary, fields, warns). ``local``
    holds this region's raw values by name for guard evaluation; ``ctx`` is the
    file-global semantic channel. summary/warns gain producers in Phase 1
    (Valid, relation + summary helpers); for now they stay empty."""
    fields, warns, local, pos = [], [], {}, 0
    for fd in spec.fields:
        if fd.when and not all(g.holds(local, payload, pos) for g in fd.when):
            continue                  # guard fails: field absent, do not advance
        n = fd.type.length(payload, pos, ctx)
        if pos + n > len(payload):
            break                     # truncated: degrade, never raise
        disp, raw, enc = fd.type.decode(payload, pos, ctx)
        note = fd.note or (fd.type.note(raw) if hasattr(fd.type, "note") else "")
        # enc and raw travel together: a plain int (value == raw, enc None)
        # carries neither key, exactly like the walkers' _f calls
        fields.append(_f(pos, n, fd.name, disp, note, enc=enc,
                         raw=raw if enc is not None else None))
        local[fd.name] = raw
        if fd.ctx:
            ctx[fd.ctx] = raw         # published under the walker's semantic key
        pos += n
    return "", fields, warns
