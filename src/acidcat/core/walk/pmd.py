"""PMD walker: the compiled score's header, its part streams, tones and memo.

The parts are the file. Eleven of them, each a stream of driver commands, and
their extents are not stored anywhere: a part runs from its own offset to the
next thing in the file, whatever that is. So the walk sorts every offset the
header gives it -- eleven parts, the tone block, the memo table -- and each
region ends where the next begins. That is also how an MDX is walked, and for
the same reason.

The memo is walked last but shown first, because the title and composer are
what a reader came for. See core/formats/pmd.py for the layout and where it
was verified.
"""


from acidcat.core.formats import pmd as pmdmod
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import Unsupported as _Unsupported, _open, _size
from acidcat.core.walk.base import _f
from acidcat.core.infra.findings import coded, defect, info

# The largest real PMD file measured is under 64 KB, and the part offsets are
# 16-bit so nothing in the file can point past 64 KB anyway. The cap is far
# above that and announces itself when it bites.
_PMD_READ_CAP = 4 * 1024 * 1024
# A #Memo block may carry up to 128 lines by the MML manual. Listing them all
# is right; the cap is for a crafted table that loops.
_PMD_MEMO_LINE_CAP = pmdmod.MEMO_LINE_CAP
# Instruments listed on the tone chunk. The count is always reported; the
# largest real file measured holds under 40.
_PMD_TONE_LIST_CAP = 64


def inspect_pmd(filepath, deep=False):
    size = _size(filepath)
    with _open(filepath) as fh:
        raw = fh.read(min(size, _PMD_READ_CAP))
    if not pmdmod.is_pmd(raw):
        raise _Unsupported("not a compiled PMD file (no PMD header bytes)")

    warns = []
    if size > _PMD_READ_CAP:
        warns.append(hit("read_bytes", _PMD_READ_CAP, size,
                         "file is %d bytes; parsed the first %d"
                         % (size, len(raw))))
    h = pmdmod.parse(raw)
    if not h["ok"]:
        return [{"id": "header", "offset": 0, "size": min(size, 24),
                 "summary": "not a resolvable PMD file: %s" % h["why"],
                 "fields": [], "warnings": [], "payload_base": 0}], \
            [coded(h["code"], "header did not resolve: %s" % h["why"])]

    # every boundary the header knows about, in file coordinates
    # (the driver's offsets count from byte 1, so add one)
    memo_start = _memo_text_start(raw, h)
    bounds = sorted({1 + p["offset"] for p in h["parts"]}
                    | {1 + h["tone_at"], 1 + h["rhythm_table_at"]}
                    | ({memo_start} if memo_start is not None else set())
                    | {len(raw)})

    def extent_after(start):
        nxt = [b for b in bounds if b > start]
        return (nxt[0] if nxt else len(raw)) - start

    chunks = [_header_chunk(raw, h)]
    # The memo ANCHOR is the four bytes just before the tone block -- a
    # pointer word, a tag, 0xFE -- and the driver finds it as tone-4. It is
    # not its own region: it is the tail of whatever precedes the tones,
    # usually the rhythm address table. The memo TEXT it points at lives
    # after the tones. So the memo chunk is the text, placed where the text
    # is, and the anchor is described as a field on the region it sits in.
    memo_text_at = _memo_text_start(raw, h)
    if memo_text_at is not None:
        chunks.append(_memo_chunk(raw, h, memo_text_at, extent_after))

    silent = [p["name"] for p in h["parts"] if p["silent"]]
    seen = set()
    for p in h["parts"]:
        at = 1 + p["offset"]
        if at in seen:
            # two parts sharing one stream: the second is a reference, and
            # emitting it again would claim the same bytes twice
            continue
        seen.add(at)
        length = extent_after(at)
        chunks.append({
            "id": "part[%s]" % p["name"], "offset": at, "size": length,
            "summary": "%s %s, %s" % (p["kind"], p["name"],
                                       "silent" if p["silent"]
                                       else "%d bytes of commands" % length),
            "fields": [_f(None, 0, "kind", p["kind"]),
                       _f(None, 0, "offset", p["offset"],
                          "from byte 1, as the driver counts")],
            "warnings": [], "payload_base": at, "payload_len": length,
            "extent_len": length})

    # A tune with no rhythm patterns points its table at the tone block:
    # an empty table, and emitting a region for it would claim the tones'
    # bytes twice. Thirty-three of 786 real files do this.
    rt = 1 + h["rhythm_table_at"]
    if rt < len(raw) and rt not in seen and rt != 1 + h["tone_at"]:
        length = extent_after(rt)
        chunks.append({
            "id": "rhythm_table", "offset": rt, "size": length,
            "summary": "rhythm pattern address table, %d bytes" % length,
            "fields": [_f(None, 0, "offset", h["rhythm_table_at"],
                          "from byte 1, as the driver counts")],
            "warnings": [], "payload_base": rt, "payload_len": length,
            "extent_len": length})

    if h["has_tones"]:
        at = 1 + h["tone_at"]
        length = extent_after(at)
        instruments, end = pmdmod.tones(raw, h)
        fields = [_f(None, 0, "offset", h["tone_at"],
                     "from byte 1, as the driver counts"),
                  _f(None, 0, "instruments", len(instruments),
                     "%d bytes each: a number and 25 YM2608 registers"
                     % pmdmod.TONE_RECORD)]
        for num, off in instruments[:_PMD_TONE_LIST_CAP]:
            fields.append(_f(off - at, pmdmod.TONE_RECORD, "tone[%d]" % num,
                             raw[off + 1:off + 5].hex(" "),
                             "first four register bytes"))
        tone_warns = []
        if len(instruments) > _PMD_TONE_LIST_CAP:
            tone_warns.append(hit("list_rows", _PMD_TONE_LIST_CAP, len(instruments),
                                  "listing the first %d of %d instruments"
                                  % (_PMD_TONE_LIST_CAP, len(instruments))))
            warns.append(tone_warns[-1])
        if raw[end - 2:end] != pmdmod.TONE_END:
            tone_warns.append(info("layout.unmeasured",
                                   "the instrument list does not end with 00 FF, "
                                   "which every file measured does"))
        chunks.append({
            "id": "tones", "offset": at, "size": length,
            "summary": "%d FM instrument%s" % (len(instruments),
                                               "" if len(instruments) == 1
                                               else "s"),
            "fields": fields, "warnings": tone_warns,
            "payload_base": at, "payload_len": length, "extent_len": length})
    else:
        warns.append(info("convention.noted",
                          "no FM instruments are embedded: compiled without MC's "
                          "/V option, so the driver needs a .FF file to play this"))

    if len(silent) == pmdmod.PARTS:
        warns.append(defect("value.invalid", "every part is silent"))
    return chunks, warns


def _header_chunk(raw, h):
    fields = [
        _f(0x00, 1, "platform",
           "X68000" if h["flag"] == pmdmod.FLAG_X68000 else "PC-98",
           "the driver's x68_flg"),
        _f(0x01, pmdmod.PART_TABLE, "parts", pmdmod.PARTS,
           "6 FM, 3 SSG, ADPCM, rhythm; offsets count from byte 1"),
        _f(0x17, 2, "rhythm_table", h["rhythm_table_at"],
           "the rhythm pattern address table"),
        _f(0x19, 2, "tone_offset", h["tone_at"],
           "instruments embedded" if h["has_tones"]
           else "no instruments: equals the table size"),
    ]
    silent = [p["name"] for p in h["parts"] if p["silent"]]
    if silent:
        fields.append(_f(None, 0, "silent_parts", ", ".join(silent),
                         "streams that open with 0x80"))
    return {"id": "header", "offset": 0, "size": pmdmod.STREAM_START + 1,
            "summary": "PMD for the %s, %d parts, %d playing"
                       % ("X68000" if h["flag"] else "PC-98",
                          pmdmod.PARTS, pmdmod.PARTS - len(silent)),
            "fields": fields, "warnings": [],
            "payload_base": 0, "payload_len": pmdmod.STREAM_START + 1,
            "extent_len": pmdmod.STREAM_START + 1}


def _memo_text_start(raw, h):
    """Where the memo block begins, in file coordinates, or None.

    The block is the STRINGS and then the pointer table, and the table sits
    at the very end of the file pointing backwards. So the block starts at
    the lowest string pointer -- which is also, in 1,086 of 1,086 files,
    exactly two bytes past the last instrument: the 00 FF that ends the tone
    list. The first draft placed the memo at the table and left the strings
    inside the tone region, which tiled and was wrong.
    """
    if h["memo_at"] is None:
        return None
    import struct
    at = 1 + h["memo_at"]
    if at + 2 > len(raw):
        return None
    table = 1 + struct.unpack_from("<H", raw, at)[0]
    if table >= len(raw):
        return None
    lowest = table
    pos = table
    for _ in range(_PMD_MEMO_LINE_CAP + 8):
        if pos + 2 > len(raw):
            break
        dx = struct.unpack_from("<H", raw, pos)[0]
        pos += 2
        if dx == 0:
            break
        if 0 < 1 + dx < lowest:
            lowest = 1 + dx
    return lowest


def _memo_chunk(raw, h, at, extent_after):
    length = extent_after(at)
    fields = [_f(None, 0, "anchor", "tone-4",
                 "pointer word, tag 0x%02X, FE; the four bytes before the "
                 "tone block" % h["memo_tag"])]
    for key, label in (("title", "title"), ("composer", "composer"),
                       ("arranger", "arranger")):
        if key in h["memo"]:
            fields.append(_f(None, 0, label, h["memo"][key]))
    for key, note in (("pcm_file", "the #PCMFile bank this tune plays from"),
                      ("pps_file", "the #PPSFile SSG-PCM bank"),
                      ("ppz_file", "the #PPZFile PPZ8 bank")):
        if key in h["memo"]:
            fields.append(_f(None, 0, key, h["memo"][key], note))
    for i, line in enumerate(h["memo_lines"][:_PMD_MEMO_LINE_CAP]):
        fields.append(_f(None, 0, "memo[%d]" % i, line))
    title = h["memo"].get("title") or "(untitled)"
    by = h["memo"].get("composer")
    return {"id": "memo", "offset": at, "size": length,
            "summary": title + (" -- " + by if by else ""),
            "fields": fields, "warnings": [],
            "payload_base": at, "payload_len": length, "extent_len": length}
