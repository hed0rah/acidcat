"""MDX walker: the X68000 sidetune header, its voices, and its channel streams.

Three chunks, because that is what the file is: a variable-length text header
ending in an offset table, a block of OPM voice definitions, and the MML
command streams the offsets point at.

The interesting structural property is that nothing in the file is at a fixed
offset. The title and the PDX sample-bank name are both variable length, so
the offset table's base moves per file, and every offset in the table is
relative to that base rather than to the start of the file. Read them as
absolute and every tune points somewhere wrong.

See core/formats/mdx.py for the layout and where it was verified.
"""


from acidcat.core.formats import mdx as mdxmod
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import _f, _open, _size

# The player itself caps an MDX at 86 KB; the largest of 27,166 real tunes is
# far inside that. The cap is well above anything genuine so a forged offset
# cannot make us read a huge file.
_MDX_READ_CAP = 4 * 1024 * 1024


def inspect_mdx(filepath, deep=False):
    size = _size(filepath)
    with _open(filepath) as fh:
        raw = fh.read(min(size, _MDX_READ_CAP))
    warns = []
    if size > _MDX_READ_CAP:
        warns.append(hit("read_bytes", _MDX_READ_CAP, size,
                         "file is %d bytes; parsed the first %d"
                         % (size, len(raw))))

    h = mdxmod.parse_header(raw)
    if not h["ok"] and h.get("packer"):
        # A PACKED module: the title and the PDX reference are in the clear
        # and everything after them is compressed. That is a readable header
        # in front of a body we cannot walk, which is a different answer to
        # "this is not an MDX" -- and the answer 440 of 27,166 modules on one
        # drive were getting.
        return _packed(size, h), warns
    if not h["ok"]:
        warns.append("header did not resolve: %s" % h["why"])
        return [_broken(raw, h)], warns

    chunks = [_header_chunk(raw, h)]
    voices = mdxmod.voice_count(raw, h)
    # Emit the chunk whenever the REGION exists, even when it is too short to
    # hold a single 27-byte voice. Those bytes are still in the file and
    # nothing else claims them; skipping the chunk left them unaccounted, which
    # reads as a cavity rather than as the short block it is.
    header_end = chunks[0]["offset"] + chunks[0]["size"]
    if h["voice_abs"] < header_end:
        # A voiceOffset of 0 points the voice block at the offset table it is
        # part of. Emitting a chunk there would overlap the header and claim
        # the same bytes twice, so the pointer is reported instead of followed.
        warns.append("voiceOffset 0x%04X puts the voice block at 0x%04X, inside "
                     "the header's own offset table"
                     % (h["voice_offset"], h["voice_abs"]))
    elif h["voice_abs"] < len(raw):
        chunks.append(_voice_chunk(raw, h, voices, deep))
        region = _voice_region_end(raw, h) - h["voice_abs"]
        # No voices at all is a tune that plays only its ADPCM channel, and
        # 416 of 54,178 do -- so an EMPTY region is a fact about the tune, not
        # damage. A region that is short but not empty is a voice cut off,
        # which seventeen real files show and which is worth a warning.
        if not voices and region > 0:
            warns.append("the voice region is %d bytes, too short for a %d-byte "
                         "voice definition" % (region, mdxmod.VOICE_SIZE))
    mml = _mml_chunk(raw, h)
    if mml:
        chunks.append(mml)

    for i, a in enumerate(h["mml_abs"]):
        if a > len(raw):
            warns.append("channel %s points past the end of the file"
                         % mdxmod.channel_name(i, h["channels"]))
    if h["voice_abs"] > len(raw):
        warns.append("the voice block points past the end of the file")
    return chunks, warns


def _packed(size, h):
    """The header of a module whose body is compressed.

    Everything here is real: the title and the PDX file name were written
    before the packer ran. What is NOT here is the offset table, the voices
    and the channel streams, because those are inside the compressed block --
    so they are named as absent rather than guessed at.
    """
    title = h["title"] or "(untitled)"
    fields = [
        _f(0x00, h["title_end"], "title", title[:120]),
        _f(None, 0, "packer", h["packer"],
           "the body is compressed; unpack it to walk the music"),
    ]
    base = max(h["base"], 0)
    if h["has_pdx"]:
        # the length in BYTES, not in decoded characters -- a Shift-JIS name
        # is not one byte per character, and the field is a byte range
        name_at = h["title_end"] + 3
        fields.append(_f(name_at, max(base - 1 - name_at, 0), "pdx_name",
                         h["pdx_name"],
                         "the ADPCM sample bank this tune plays from"))
    head = {"id": "header", "offset": 0, "size": base,
            "summary": "%s, packed with %s" % (title[:48], h["packer"]),
            "fields": fields,
            "warnings": ["the MML and voice data are packed with %s, so the "
                         "channels and voices are not walked" % h["packer"]],
            "payload_base": 0, "payload_len": base, "extent_len": base}
    if base >= size:
        return [head]
    # The packed block is named rather than walked. It still belongs to the
    # file, so it gets a chunk: leaving it out would read as a cavity, which
    # is what an unaccounted region means everywhere else in acidcat.
    body = {"id": "packed", "offset": base, "size": size - base,
            "summary": "%s stream, %d bytes" % (h["packer"], size - base),
            "fields": [], "warnings": [],
            "payload_base": base, "payload_len": size - base,
            "extent_len": size - base}
    return [head, body]


def _broken(raw, h):
    return {"id": "header", "offset": 0, "size": min(len(raw), 64),
            "summary": "not a resolvable MDX header: %s" % h["why"],
            "fields": [], "warnings": [], "payload_base": 0}


def _header_chunk(raw, h):
    end = h["base"] + 2 + h["channels"] * 2
    kind = ("stock X68000: eight FM voices and one ADPCM channel"
            if h["channels"] == mdxmod.CHANNELS_BASE
            else "Mercury Unit present: the extra voices are addressable")
    title = h["title"] or "(untitled)"
    summary = "%r, %d channels" % (title[:48], h["channels"])
    if h["has_pdx"]:
        summary += ", samples from %s" % h["pdx_name"]

    fields = [
        _f(0, h["title_end"], "title", title,
           "Shift-JIS, terminated by 0D 0A 1A"),
        _f(h["title_end"], 3, "titleEnd", "0D 0A 1A",
           "the terminator; there is no magic number before it"),
        _f(h["title_end"] + 3, max(0, h["base"] - h["title_end"] - 3),
           "pdxName", h["pdx_name"] or "(none)",
           "NUL-terminated sample bank; a bare NUL when the tune uses none"),
        _f(h["base"], 2, "voiceOffset", "0x%04X" % h["voice_offset"],
           "relative to THIS field's position, not to the file start",
           enc=">H", raw=h["voice_offset"], xref=h["voice_abs"]),
    ]
    for i, (rel, absol) in enumerate(zip(h["mml_offsets"], h["mml_abs"])):
        name = mdxmod.channel_name(i, h["channels"])
        fields.append(_f(h["base"] + 2 + i * 2, 2, "channel %s" % name,
                         "0x%04X" % rel, "-> 0x%04X" % absol,
                         enc=">H", raw=rel, xref=absol))
    fields.append(_f(None, 0, "channelCount", h["channels"], kind))
    return {"id": "header", "offset": 0, "size": end, "summary": summary,
            "fields": fields, "warnings": [], "payload_base": 0}


def _voice_region_end(raw, h):
    """Where the voice block stops: the next channel stream, or the file end."""
    after = [a for a in h["mml_abs"] if h["voice_abs"] < a <= len(raw)]
    return min(after) if after else len(raw)


def _voice_chunk(raw, h, voices, deep):
    off = h["voice_abs"]
    # The region, not the voices. voice_count floors a division by 27, so a
    # region that is not a whole number of voices leaves a remainder of up to
    # 26 bytes -- real bytes, which nothing else claims. Sizing the chunk to
    # the region accounts for them; the field below says how many are spare.
    span = max(0, _voice_region_end(raw, h) - off)
    fields = [_f(None, 0, "voices", voices,
                 "%d bytes each; these are OPM register values, not an "
                 "abstraction over them" % mdxmod.VOICE_SIZE)]
    rows = []
    for i in range(voices):
        v = mdxmod.parse_voice(raw, off + i * mdxmod.VOICE_SIZE)
        if not v:
            break
        if i < 8 or deep:
            fields.append(_f(i * mdxmod.VOICE_SIZE, mdxmod.VOICE_SIZE,
                             "voice %d" % v["number"],
                             "FL %d CON %d" % (v["feedback"], v["connect"]),
                             "slot mask 0x%X, TL %s"
                             % (v["slot_mask"], v["tl"])))
        if deep:
            rows.append({"tick": i, "event": "voice %d" % v["number"],
                         "detail": "FL %d CON %d slots 0x%X TL %s AR %s"
                                   % (v["feedback"], v["connect"],
                                      v["slot_mask"], v["tl"], v["ks_ar"])})
    if voices > 8 and not deep:
        fields.append(_f(None, 0, "more", "%d further voices" % (voices - 8),
                         "shown with deep inspection"))
    spare = span - voices * mdxmod.VOICE_SIZE
    if spare:
        fields.append(_f(voices * mdxmod.VOICE_SIZE, spare, "spare", "%d bytes" % spare,
                         "the region is not a whole number of %d-byte voices"
                         % mdxmod.VOICE_SIZE))
    chunk = {"id": "voices", "offset": off, "size": span,
             "summary": ("%d OPM voice definition(s)" % voices) if voices
                        else "voice region, too short to hold one",
             "fields": fields, "warnings": [], "payload_base": off}
    if deep:
        chunk["rows"] = rows
    return chunk


def _mml_chunk(raw, h):
    """Every channel's command stream, as one region.

    One chunk rather than one per channel: the offsets are not required to be
    ordered and several channels routinely share a position, so per-channel
    extents would overlap and claim the same bytes twice.
    """
    starts = [a for a in h["mml_abs"] if 0 <= a <= len(raw)]
    if not starts:
        return None
    first = min(starts)
    if first >= len(raw):
        return None
    # The voice block is not required to come before the channel data, and
    # usually does not. Where it sits after, the MML region has to stop at it
    # or the two chunks claim the same bytes.
    stop = len(raw)
    if first < h["voice_abs"] <= len(raw):
        stop = h["voice_abs"]

    order = sorted(range(len(h["mml_abs"])), key=lambda i: h["mml_abs"][i])
    fields = []
    for i in order:
        a = h["mml_abs"][i]
        nxt = min([b for b in h["mml_abs"] if b > a] + [stop])
        used = max(0, min(nxt, stop) - a)
        name = mdxmod.channel_name(i, h["channels"])
        fields.append(_f(max(0, a - first), used, "channel %s" % name,
                         "%d bytes" % used,
                         "empty" if used <= 2 else "MML command stream"))
    live = sum(1 for f in fields if f["len"] > 2)
    return {"id": "mml", "offset": first, "size": max(0, stop - first),
            "summary": "%d channel stream(s), %d carrying data"
                       % (len(h["mml_abs"]), live),
            "fields": fields, "warnings": [], "payload_base": first}
