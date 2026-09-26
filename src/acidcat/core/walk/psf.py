"""PSF walker: the header, the reserved area, the program, the tags.

Four regions, always in that order, and the program is the one that varies:
it is a zlib blob whose contents mean something different on each of the
eight machines the container serves. The walk reports what the container
proves -- size, checksum, that it inflates -- and decodes the program's own
header only on platforms where that header is documented and verified.

See core/formats/psf.py for the layout and where it came from.
"""


from acidcat.core.formats import psf as psfmod
from acidcat.core.infra.limits import hit
from acidcat.core.infra.source import as_source
from acidcat.core.walk.base import Unsupported as _Unsupported, _open, _size
from acidcat.core.walk.base import _f

# A GSF library carrying a whole 16 MB GBA ROM compresses to a few MB. The
# cap is for a forged size, and announces itself when it bites.
_PSF_READ_CAP = 64 * 1024 * 1024
# Tag fields listed on the tag chunk. Real files carry a dozen.
_PSF_TAG_LIST_CAP = 64


def inspect_psf(filepath, deep=False):
    size = _size(filepath)
    with _open(filepath) as fh:
        raw = fh.read(min(size, _PSF_READ_CAP))
    if not psfmod.is_psf(raw):
        raise _Unsupported("not a PSF (no PSF magic with a known version)")

    warns = []
    if size > _PSF_READ_CAP:
        warns.append(hit("read_bytes", _PSF_READ_CAP, size,
                         "file is %d bytes; parsed the first %d"
                         % (size, len(raw))))
    h = psfmod.parse(raw, len(raw))
    if not h["ok"]:
        return [{"id": "header", "offset": 0, "size": min(size, psfmod.HEADER),
                 "summary": "not a resolvable PSF: %s" % h["why"],
                 "fields": [], "warnings": [], "payload_base": 0}], \
            warns + ["header did not resolve: %s" % h["why"]]

    short, machine = h["platform"]
    src = as_source(filepath)
    is_lib = src.ext.endswith("lib")
    chunks = [_header_chunk(h, short, machine)]

    if h["reserved_size"]:
        chunks.append(_reserved_chunk(h, is_lib))
        sv = h["save"]
        if sv and sv["ok"]:
            if sv["crc_ok"] is False:
                warns.append("the SAVE block's CRC32 does not match its header")
            if sv["inflated_size"] is None:
                warns.append("the SAVE block does not inflate as zlib")
            elif sv["consistent"] is False:
                warns.append("the SAVE block declares %d bytes and %d follow"
                             % (sv["length"], sv["inflated_size"] - 8))

    prog = _program_chunk(h, short, is_lib)
    chunks.append(prog)
    if h["crc_ok"] is False:
        warns.append("the program's CRC32 does not match the header: the "
                     "compressed program has been altered or damaged")
    if h["program_size"] == 0:
        pass                    # nothing to inflate; the program chunk says so
    elif h["inflated_size"] is None:
        warns.append("the program does not inflate as zlib")
    if h["gsf"] and not h["gsf"]["consistent"]:
        warns.append("the program header declares %d bytes of ROM and %d follow"
                     % (h["gsf"]["length"], h["gsf"]["rom_bytes"]))

    tags_at = h["tags_at"]
    if tags_at < len(raw):
        if h["tags"] or h["libs"]:
            tc = _tag_chunk(h, raw, tags_at, len(raw) - tags_at)
            chunks.append(tc)
            warns.extend(tc["warnings"])
        elif raw[tags_at:tags_at + len(psfmod.TAG_MARK)] == psfmod.TAG_MARK:
            # a [TAG] mark with nothing after it: 12 real DS libraries
            n = len(raw) - tags_at
            chunks.append(_region("tags", tags_at, n, "an empty [TAG] block"))
        else:
            n = len(raw) - tags_at
            chunks.append(_region("trailing", tags_at, n,
                                  "%d bytes after the program, not a [TAG] "
                                  "block" % n))
            warns.append("%d bytes after the program are not a tag block" % n)
    elif not is_lib:
        # a library carries no tags by design; a mini without them is odd
        warns.append("no [TAG] block, so no title, length or library reference")

    if h["libs"] and not is_lib and src.path is not None:
        for lib in h["libs"]:
            beside = src.sibling(lib)
            if beside is not None:
                beside.close()
            else:
                warns.append("names library %r and it is not beside this file, "
                             "so the tune cannot play" % lib)
    return chunks, warns


def _region(cid, at, length, summary):
    return {"id": cid, "offset": at, "size": length, "summary": summary,
            "fields": [], "warnings": [], "payload_base": at,
            "payload_len": length, "extent_len": length}


def _header_chunk(h, short, machine):
    fields = [
        _f(0x00, 3, "magic", "PSF"),
        _f(0x03, 1, "version", "0x%02X" % h["version"],
           "%s: %s" % (short, machine)),
        _f(0x04, 4, "reserved_size", h["reserved_size"]),
        _f(0x08, 4, "program_size", "{:,}".format(h["program_size"]),
           "compressed"),
        _f(0x0C, 4, "crc32", "0x%08X" % h["crc"],
           "of the compressed program; %s" % (
               "matches" if h["crc_ok"] else
               "DOES NOT MATCH" if h["crc_ok"] is False else "not checked")),
    ]
    return {"id": "header", "offset": 0, "size": psfmod.HEADER,
            "summary": "%s, %s" % (short, machine),
            "fields": fields, "warnings": [],
            "payload_base": 0, "payload_len": psfmod.HEADER,
            "extent_len": psfmod.HEADER}


def _reserved_chunk(h, is_lib):
    at, n = psfmod.HEADER, h["reserved_size"]
    sv = h["save"]
    if not sv or not sv["ok"]:
        return _region("reserved", at, n, "%d bytes, platform-specific" % n)
    fields = [_f(0, 4, "tag", "SAVE"),
              _f(4, 4, "compressed", "{:,} bytes".format(sv["size"]), "zlib"),
              _f(8, 4, "crc32", "ok" if sv["crc_ok"] else "MISMATCH")]
    summary = "SAVE block, {:,} bytes compressed".format(sv["size"])
    if sv["inflated_size"] is not None:
        fields.append(_f(None, 0, "inflated", "{:,} bytes".format(sv["inflated_size"])))
    if sv["offset"] is not None:
        fields += [_f(None, 0, "patch_offset", "0x%08X" % sv["offset"],
                      "where in the save state the bytes go"),
                   _f(None, 0, "patch_bytes", "{:,}".format(sv["length"]))]
        if sv["length"] <= 16 and not is_lib:
            summary = ("save-state patch: %d byte%s at 0x%08X"
                       % (sv["length"], "" if sv["length"] == 1 else "s", sv["offset"]))
        elif is_lib:
            summary = "save state: {:,} bytes for the emulator".format(sv["length"])
    return {"id": "reserved", "offset": at, "size": n, "summary": summary,
            "fields": fields, "warnings": [], "payload_base": at}


def _program_chunk(h, short, is_lib):
    at, n = h["program_at"], h["program_size"]
    if n == 0:
        return {"id": "program", "offset": at, "size": 0,
                "summary": "%s: no program; everything is in the library"
                           % short,
                "fields": [_f(None, 0, "compressed", "0 bytes")],
                "warnings": [], "payload_base": at}
    fields = [_f(None, 0, "compressed", "{:,} bytes".format(n), "zlib")]
    if h["inflated_size"] is not None:
        fields.append(_f(None, 0, "inflated", "{:,} bytes".format(h["inflated_size"])))
    g = h["gsf"]
    summary = "%s program, {:,} bytes compressed".format(n) % short
    if g:
        if g["entry"] is not None:
            fields.append(_f(None, 0, "entry_point", "0x%08X" % g["entry"],
                             "where the GBA starts executing"))
        fields += [
            _f(None, 0, "load_offset", "0x%08X" % g["offset"],
               "where the ROM bytes are placed"),
            _f(None, 0, "rom_bytes", "{:,}".format(g["length"]),
               "the program header's own count"),
        ]
        if g["length"] <= 4 and not is_lib:
            summary = ("%s mini: %d byte%s patched into the library at 0x%08X"
                       % (short, g["length"], "" if g["length"] == 1 else "s",
                          g["offset"]))
        elif is_lib:
            summary = "%s library: a %s-byte %s ROM" % (
                short, format(g["length"], ","),
                "GBA" if g["entry"] is not None else "DS")
        else:
            summary = "%s: a {:,}-byte GBA ROM".format(g["length"]) % short
    elif is_lib:
        summary = "%s library, {:,} bytes compressed".format(n) % short
    return {"id": "program", "offset": at, "size": n, "summary": summary,
            "fields": fields, "warnings": [], "payload_base": at,
            "payload_len": n, "extent_len": n}


def _tag_chunk(h, raw, at, length):
    t = h["tags"]
    # the marker is the header, before payload_base (at + 5)
    fields = [_f(-5, 5, "marker", "[TAG]"),
              _f(None, 0, "lines", h["tag_lines"])]
    for lib in h["libs"]:
        fields.append(_f(None, 0, "_lib", lib, "the library this mini loads over"))
    for key in ("title", "artist", "game", "year", "genre", "copyright",
                "length", "fade", "volume", "comment"):
        if key in t:
            fields.append(_f(None, 0, key, t[key][:120]))
    listed = 0
    for key, val in t.items():
        if key in ("title", "artist", "game", "year", "genre", "copyright",
                   "length", "fade", "volume", "comment"):
            continue
        if listed >= _PSF_TAG_LIST_CAP:
            break
        listed += 1
        fields.append(_f(None, 0, key, val[:120]))
    warnings = []
    if len(t) - 10 > _PSF_TAG_LIST_CAP:
        warnings.append(hit("list_rows", _PSF_TAG_LIST_CAP, len(t) - 10,
                            "listing the first %d of %d other tags"
                            % (_PSF_TAG_LIST_CAP, len(t))))
    title = t.get("title") or ("library" if h["libs"] == [] and not t else "(untitled)")
    by = t.get("artist")
    return {"id": "tags", "offset": at, "size": length,
            "summary": title + (" -- " + by if by else ""),
            "fields": fields, "warnings": warnings,
            "payload_base": at + 5, "payload_len": length - 5,
            "extent_len": length}
