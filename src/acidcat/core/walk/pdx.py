"""PDX walker: the slot table of an X68000 ADPCM sample bank, and its samples.

Two kinds of chunk, because that is what the file is: one table of eight-byte
slots, then the sample data the slots point at.

The one thing worth getting right is that a slot and a sample are not the same
thing. Slots alias -- several sample numbers routinely point at one region of
bytes -- so emitting a chunk per slot would claim the same bytes twice and
report a file far larger than it is. The samples are therefore emitted per
distinct region, and each names every slot that reaches it.

See core/formats/pdx.py for the layout and where it was verified.
"""


from acidcat.core.formats import pdx as pdxmod
from acidcat.core.infra.limits import hit
from acidcat.core.primitives.notes import is_coverage
from acidcat.core.walk.base import _f, _open, _size
from acidcat.core.infra.findings import coded, defect, info

# The largest real bank measured holds 77 samples. A bank claiming hundreds is
# legal arithmetic, so the listing is bounded and says when it bit.
_PDX_SAMPLE_CAP = 256
# Fields on the table chunk, one per live slot. Same reasoning, lower bound:
# past this the table chunk stops being readable.
_PDX_SLOT_FIELD_CAP = 96


def inspect_pdx(filepath, deep=False):
    size = _size(filepath)
    with _open(filepath) as fh:
        # only the table is read; the samples are located, not loaded.
        # Eight banks is the format's own ceiling, so this is not a cap on
        # the answer -- a table that needs more is not a table.
        raw = fh.read(min(size, pdxmod.MAX_BANKS * pdxmod.BANK))

    h = pdxmod.parse_table(raw, size)
    if not h["ok"] and h["packer"]:
        return _packed(size, h), []
    if not h["ok"]:
        return [{"id": "table", "offset": 0, "size": min(size, pdxmod.BANK),
                 "summary": "not a resolvable PDX slot table: %s" % h["why"],
                 "fields": [], "warnings": [], "payload_base": 0}], \
            [coded(h["code"], "slot table did not resolve: %s" % h["why"])]

    chunks = [_table_chunk(h)]
    warns = [w for w in chunks[0]["warnings"] if is_coverage(w)]
    if h["padded"]:
        pad = h["data_start"] - h["table_size"]
        chunks.append({
            "id": "padding", "offset": h["table_size"], "size": pad,
            "summary": "%d zero bytes; the table rounded up to %d"
                       % (pad, h["data_start"]),
            "fields": [], "warnings": [],
            "payload_base": h["table_size"], "payload_len": pad,
            "extent_len": pad})

    # distinct REGIONS, not slots: a bank that maps one sample to six numbers
    # has one region and six slots reaching it
    regions = {}
    for i, (off, length) in enumerate(h["slots"]):
        if not (off or length):
            continue
        regions.setdefault((off, length), []).append(i)

    listed = 0
    for (off, length), slots in sorted(regions.items()):
        if listed >= _PDX_SAMPLE_CAP:
            break
        listed += 1
        names = ", ".join(str(s) for s in slots)
        entry = {
            "id": "sample[%d]" % slots[0],
            "offset": off, "size": length,
            "summary": ("{:,} bytes of MSM6258 ADPCM, {:,} samples".format(
                length, length * pdxmod.SAMPLES_PER_BYTE)),
            "fields": [
                _f(None, 0, "slot", names,
                   "sample number%s the MML plays this with"
                   % ("" if len(slots) == 1 else "s")),
                _f(None, 0, "adpcm_samples", length * pdxmod.SAMPLES_PER_BYTE,
                   "two per byte; the file states no rate, the player sets it"),
            ],
            "warnings": [], "payload_base": off, "payload_len": length,
            "extent_len": length,
        }
        if len(slots) > 1:
            entry["summary"] += ", shared by %d slots" % len(slots)
        chunks.append(entry)

    if len(regions) > _PDX_SAMPLE_CAP:
        chunks[0]["warnings"].append(hit(
            "list_rows", _PDX_SAMPLE_CAP, len(regions),
            "listing the first %d of %d samples" % (_PDX_SAMPLE_CAP, len(regions))))
        warns.append(chunks[0]["warnings"][-1])

    # Bytes past the last sample. Nearly always exactly one, and nearly
    # always zero: 343 of 3,255 banks measured end with a single byte no slot
    # reaches, 322 of them a NUL. Small enough to be the writer rounding up,
    # so it is named rather than warned about -- and emitted as a chunk, so
    # the file still tiles.
    end = max((o + s for o, s in regions), default=h["data_start"])
    if end < size:
        tail = size - end
        chunks.append({
            "id": "tail", "offset": end, "size": tail,
            "summary": ("%d byte%s after the last sample, reached by no slot"
                        % (tail, "" if tail == 1 else "s")),
            "fields": [], "warnings": [],
            "payload_base": end, "payload_len": tail, "extent_len": tail})
        if tail > pdxmod.SLOT:
            warns.append(defect("bytes.stray",
                                "%d bytes after the last sample are reached by no "
                                "slot, which is more than a writer rounding up"
                                % tail))
    return chunks, warns


def _table_chunk(h):
    """The slot table: every row, because the row number is the sample number."""
    fields = [
        _f(None, 0, "banks", h["banks"],
           "%d slots of %d bytes" % (h["banks"] * pdxmod.SLOTS_PER_BANK,
                                     pdxmod.SLOT)),
        _f(None, 0, "samples", h["used"], "slots that point at data"),
    ]
    shown = 0
    for i, (off, length) in enumerate(h["slots"]):
        if not (off or length):
            continue
        if shown >= _PDX_SLOT_FIELD_CAP:
            break
        shown += 1
        fields.append(_f(i * pdxmod.SLOT, pdxmod.SLOT, "slot[%d]" % i,
                         "%d bytes" % length, "at 0x%06X" % off, xref=off))
    warnings = []
    if h["used"] > _PDX_SLOT_FIELD_CAP:
        warnings.append(hit("list_rows", _PDX_SLOT_FIELD_CAP, h['used'],
                            "listing the first %d of %d filled slots"
                            % (_PDX_SLOT_FIELD_CAP, h["used"])))
    return {"id": "table", "offset": 0, "size": h["table_size"],
            "summary": "%d sample%s in %d bank%s"
                       % (h["used"], "" if h["used"] == 1 else "s",
                          h["banks"], "" if h["banks"] == 1 else "s"),
            "fields": fields, "warnings": warnings,
            "payload_base": 0, "payload_len": h["table_size"],
            "extent_len": h["table_size"]}


def _packed(size, h):
    """A bank compressed after it was written.

    Unlike a packed MDX there is no readable text to recover -- a PDX is all
    table -- so this says what the file is and what was done to it, and does
    not pretend to a sample list.
    """
    head = {"id": "table", "offset": 0, "size": min(size, pdxmod.BANK),
            "summary": "slot table packed with %s" % h["packer"],
            "fields": [_f(None, 0, "packer", h["packer"],
                          "unpack it to read the slot table")],
            "warnings": [info("decode.partial",
                              "the whole bank is packed with %s, so no sample is "
                              "located" % h["packer"])],
            "payload_base": 0, "payload_len": min(size, pdxmod.BANK),
            "extent_len": min(size, pdxmod.BANK)}
    if size <= pdxmod.BANK:
        return [head]
    return [head, {"id": "packed", "offset": pdxmod.BANK,
                   "size": size - pdxmod.BANK,
                   "summary": "%s stream, %d bytes"
                              % (h["packer"], size - pdxmod.BANK),
                   "fields": [], "warnings": [],
                   "payload_base": pdxmod.BANK,
                   "payload_len": size - pdxmod.BANK,
                   "extent_len": size - pdxmod.BANK}]
