"""Chiptune containers: NSF, NSFe and SAP.

None of these three is a description of music. Each one ships the original 6502
program that PRODUCED the music, plus enough metadata to start it: where to load
it, where to call it, and how often. What a walker can honestly say about them is
therefore where the code goes and where the entry points are, not what it sounds
like -- the sound is a property of running it on a chip this module does not
emulate.

That lineage is not a coincidence. Both formats were shaped by PSID: the literal
`<?>` convention for an unknown author appears in all three, and all three are
"ship the code and run it" containers.

What separates them at the container level is who knows the load address. An NSF
carries a flat ROM image with the address out-of-band in the header, so nothing
in the payload can be checked against anything. A SAP payload is
self-describing: it is a standard Atari executable, and every block carries its
own start and end. SAP's binary half is the more parseable of the two, and it is
the only part of SAP with a second independent source.

Sources: the NESdev wiki (living spec, authoritative where it contradicts
Horton), Kevin Horton's nsfspec.txt v1.61 (2000, original intent), Disch's NSFe
Revision 2 (2003), and asap.sourceforge.net for SAP. No player source was read;
the reference implementations are GPL and this is MIT.

Two things a reader should know about the confidence here. NSF has only ever had
two version bytes, 1 and 2 -- there is no v1.01/1.02/1.03 ladder, and the
appearance of one in tooling is usually PSID bleeding across. And SAP is
effectively single-source outside its binary blocks, so the tag semantics below
rest on one document rather than two.
"""

import re
import struct

from acidcat.core.infra.findings import defect, info
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import _f, _open, _size

# An NSF is a NES ROM image: the mapper windows 4 KB banks into 32 KB of address
# space, so past about 1 MB the data cannot be reached by any bank value. The cap
# sits far above that, high enough never to truncate a real file.
_NSF_READ_CAP = 16 * 1024 * 1024

# NSFe and NSF2 trailers declare chunk lengths as unchecked 32-bit words, so a
# forged length can claim 4 GB. Chunks are walked to this many before the walk
# stops and says so.
_NSFE_CHUNK_MAX = 4096

_NSF_HEADER = 0x80
_STR_SLOT = 32

_EXPANSION = [
    (0, "VRC6"), (1, "VRC7"), (2, "FDS"), (3, "MMC5"),
    (4, "Namco 163"), (5, "Sunsoft 5B"), (6, "VT02+"),
]

# Every SAP tag the spec defines, with how its argument is written. Kept as data
# because the validity rules below are per-tag and reading them next to the
# vocabulary is the only way to see that they cover it.
_SAP_TAGS = {
    "AUTHOR": "string", "NAME": "string", "DATE": "string",
    "SONGS": "dec", "DEFSONG": "dec", "FASTPLAY": "dec",
    "STEREO": "none", "NTSC": "none",
    "TYPE": "letter",
    "INIT": "hex", "MUSIC": "hex", "PLAYER": "hex", "COVOX": "hex",
    "TIME": "time",
}

_SAP_TYPES = {
    "B": "standard: INIT called with the subsong in A, then PLAYER on a timer",
    "C": "Chaos Music Composer: MUSIC required, INIT invalid, fixed call sequence",
    "D": "digitised: INIT does not return and drives the audio itself",
    "S": "SoftSynth: PLAYER unused, $45 counts down and $B07B is bumped",
    "R": "raw POKEY register dump, not an Atari executable (no player implements it)",
}

# The header's permitted characters top out at 0x7C, so 0xFF cannot occur in it.
# The binary half must open with FF FF. That is what makes the boundary findable.
_SAP_TEXT_OK = set(range(0x20, 0x60)) | set(range(0x61, 0x7B)) | {0x7C, 0x0D, 0x0A}


def _addr(a):
    return "$%04X" % (a & 0xFFFF)


def _slot(raw, off):
    """One fixed-width 32-byte string slot, truncated at its first NUL.

    The slot is ALWAYS 32 bytes; the NUL terminates the text inside it rather
    than ending the field. Scanning for the NUL instead of capping at the slot
    would run a malformed 32-non-NUL slot into the next field, which is how the
    artist ends up appended to the title.

    Nothing in the file declares the encoding. Nominally ASCII, but real rips use
    Shift-JIS for Japanese titles and occasionally CP-1252, and no byte says
    which. Latin-1 cannot fail, so it is used to get SOMETHING printable, and the
    caller is told when the bytes were not plain ASCII rather than being handed a
    confident mojibake.
    """
    blob = raw[off:off + _STR_SLOT]
    nul = blob.find(b"\x00")
    text = blob if nul < 0 else blob[:nul]
    tail = b"" if nul < 0 else blob[nul + 1:]
    return (text.decode("latin-1").rstrip(),
            nul < 0,                                  # no terminator in the slot
            bool(tail.strip(b"\x00")),                # dirty padding after it
            any(b > 0x7E for b in text))              # not plain ASCII


def inspect_nsf(filepath, deep=False):
    """An NSF: a 128-byte header, a flat ROM image, and maybe an NSFe trailer.

    The header is little-endian throughout, which is worth saying only because
    the sibling formats in this repo are not: a SID header is big-endian on a
    little-endian target, and an MDX offset is relative to a word rather than to
    the file. NSF is the straightforward one.

    Two fields do not mean what they appear to. The load address stops being an
    address when bankswitching is in use -- its low 12 bits become a count of pad
    bytes at the start of the ROM. And bytes $7C-$7F were four reserved bytes in
    the original spec that were later given meaning, so in a version-1 file $7C
    must be ignored while $7D-$7F may still legitimately carry a trailer length.
    """
    size = _size(filepath)
    warns = []
    with _open(filepath) as fh:
        raw = fh.read(min(size, _NSF_READ_CAP))
    if size > _NSF_READ_CAP:
        warns.append(hit("read_bytes", _NSF_READ_CAP, size,
                         "read the first %s of %s bytes"
                         % (format(_NSF_READ_CAP, ","), format(size, ","))))
    if raw[:5] != b"NESM\x1a":
        # The siblings both check their magic and this did not, so anything at
        # least 128 bytes long parsed as an NSF and produced a confident title,
        # artist and load address out of arbitrary bytes. A corpus of 13,042
        # .nsf files held 50 that are HTML error pages or macOS resource forks
        # wearing the extension, and every one of them "walked".
        return [{"id": "header", "offset": 0, "size": size,
                 "summary": "not an NSF (magic is not NESM 1A)",
                 "fields": [], "warnings": [], "payload_base": 0}], \
               warns + [defect("magic.mismatch", "the first five bytes are not 4E 45 53 4D 1A")]
    if len(raw) < _NSF_HEADER:
        return [{"id": "header", "offset": 0, "size": size,
                 "summary": "NSF header is truncated (%d of 128 bytes)" % len(raw),
                 "fields": [], "warnings": [], "payload_base": 0}], \
               warns + [defect("header.truncated", "file ends inside the 128-byte header")]

    ver = raw[5]
    total, start = raw[6], raw[7]
    load, init, play = struct.unpack_from("<HHH", raw, 8)
    ntsc_speed, = struct.unpack_from("<H", raw, 0x6E)
    banks = list(raw[0x70:0x78])
    pal_speed, = struct.unpack_from("<H", raw, 0x78)
    region, chips, nsf2flags = raw[0x7A], raw[0x7B], raw[0x7C]
    data_len = raw[0x7D] | (raw[0x7E] << 8) | (raw[0x7F] << 16)

    banked = any(banks)
    is_nsf2 = ver == 2
    dual = bool(region & 0x02)
    pal = bool(region & 0x01)

    names, slots = [], []
    for off, label in ((0x0E, "title"), (0x2E, "artist"), (0x4E, "copyright")):
        text, unterminated, dirty, hi = _slot(raw, off)
        names.append(text)
        note = "32-byte slot, NUL-terminated inside it"
        if unterminated:
            note = "no NUL anywhere in the 32-byte slot"
            warns.append(defect("text.invalid",
                                "the %s slot has no terminator; text may run past it"
                                % label))
        elif dirty:
            note = "non-zero bytes after the terminator (ripper remnants)"
        elif hi:
            note = "bytes above 0x7E; the encoding is not declared anywhere"
        slots.append(_f(off, _STR_SLOT, label, text or "(empty)", note))

    fields = [
        _f(0, 5, "magic", "NESM 1A", "the 0x1A is part of the signature"),
        _f(5, 1, "version", ver,
           "NSF2" if is_nsf2 else "NSF; only 1 and 2 have ever existed",
           enc="B", raw=ver),
        _f(6, 1, "songs", total, "1-based count", enc="B", raw=total),
        _f(7, 1, "startSong", start, "1-based index, unlike NSFe which is 0-based",
           enc="B", raw=start),
        _f(8, 2, "loadAddress", _addr(load),
           "low 12 bits are a pad count, not an address, while banked" if banked
           else "where the ROM image is placed", enc="<H", raw=load),
        _f(0x0A, 2, "initAddress", _addr(init), "called once per song",
           enc="<H", raw=init),
        _f(0x0C, 2, "playAddress", _addr(play), "called on a timer",
           enc="<H", raw=play),
    ] + slots + [
        _f(0x6E, 2, "ntscSpeed", "%s us" % format(ntsc_speed, ","),
           "microseconds between PLAY calls; 16666 is 60 Hz",
           enc="<H", raw=ntsc_speed),
        _f(0x70, 8, "bankInit",
           " ".join("%02X" % b for b in banks),
           "all zero means bankswitching is unused; there is no flag bit"),
        _f(0x78, 2, "palSpeed", "%s us" % format(pal_speed, ","),
           "20000 is 50 Hz", enc="<H", raw=pal_speed),
        _f(0x7A, 1, "region",
           "dual" if dual else ("PAL" if pal else "NTSC"),
           "bit1 dual, bit0 PAL", enc="B", raw=region),
        _f(0x7B, 1, "expansion",
           ", ".join(n for b, n in _EXPANSION if chips & (1 << b)) or "none",
           "stock 2A03 APU only" if not chips else "extra sound hardware",
           enc="B", raw=chips),
    ]

    if is_nsf2:
        feat = []
        if nsf2flags & 0x10:
            feat.append("IRQ")
        if nsf2flags & 0x20:
            feat.append("non-returning INIT")
        if nsf2flags & 0x40:
            feat.append("no PLAY")
        if nsf2flags & 0x80:
            feat.append("mandatory metadata")
        fields.append(_f(0x7C, 1, "nsf2Flags", ", ".join(feat) or "none",
                         "bits 0-3 must be clear", enc="B", raw=nsf2flags))
    elif nsf2flags:
        fields.append(_f(0x7C, 1, "reserved", "0x%02X" % nsf2flags,
                         "must be ignored in a version 1 file, but it is not zero"))

    fields.append(_f(0x7D, 3, "dataLength",
                     format(data_len, ",") if data_len else "0 (runs to EOF)",
                     "where the appended metadata starts, if any"))

    if banked:
        pad = load & 0x0FFF
        fields.append(_f(None, 0, "romPadding", format(pad, ","),
                         "loadAddress & 0x0FFF while banked"))

    # ── validity, weighted rather than absolute ──────────────────────
    if ver not in (1, 2):
        warns.append(defect("value.invalid",
                            "version byte is %d; only 1 and 2 have ever been defined, so "
                            "this is corruption rather than a newer file" % ver))
    if total == 0:
        warns.append(defect("value.invalid", "song count is zero, and the count is 1-based"))
    if start == 0 or start > max(total, 1):
        warns.append(defect("value.invalid", "start song %d is outside 1..%d" % (start, total)))
    if region & 0xFC:
        warns.append(defect(
            "reserved.nonzero",
            "region byte has reserved bits set (0x%02X)" % region))
    if chips & 0x80:
        warns.append(defect("reserved.nonzero", "expansion byte bit 7 is reserved and set"))
    for label, a in (("init", init), ("play", play)):
        if a and a < 0x6000:
            warns.append(defect("address.outside",
                                "%s address %s is below $6000, which no NSF maps"
                                % (label, _addr(a))))
    if load < 0x8000 and not (chips & 0x04):
        warns.append(defect("address.outside",
                            "load address %s is below $8000 without the FDS bit; FDS rips "
                            "do this legitimately, other files do not" % _addr(load)))
    if (pal or dual) and not pal_speed:
        warns.append(defect("field.inconsistent", "declared PAL but the PAL speed word is zero"))
    if (not pal or dual) and not ntsc_speed:
        warns.append(defect("field.inconsistent", "declared NTSC but the NTSC speed word is zero"))
    if is_nsf2 and nsf2flags & 0x0F:
        warns.append(defect("reserved.nonzero", "NSF2 feature bits 0-3 are reserved and set"))
    if is_nsf2 and (nsf2flags & 0x80) and not data_len:
        warns.append(defect("required.missing",
                            "claims mandatory appended metadata but declares no data "
                            "length, so the trailer has no stated boundary"))
    if data_len and _NSF_HEADER + data_len > size:
        warns.append(defect("size.overrun",
                            "declared data length runs %s bytes past the end of the file"
                            % format(_NSF_HEADER + data_len - size, ",")))
        data_len = 0
    if all(n == "<?>" for n in names):
        warns.append(info("convention.noted",
                          "title, artist and copyright are all <?>: a bare rip, "
                          "which is a convention rather than damage"))

    # bit 7 has no name, so a file with only bit 7 set joins to the empty string
    # and the summary reads "NTSC, " with nothing after it
    named = ", ".join(n for b, n in _EXPANSION if chips & (1 << b))
    chip_note = "stock APU" if not chips else (named or "unnamed bits 0x%02X" % chips)
    chunks = [{"id": "header", "offset": 0, "size": _NSF_HEADER,
               "summary": "%s header, %d song(s), %s, %s"
                          % ("NSF2" if is_nsf2 else "NSF", total,
                             "dual" if dual else ("PAL" if pal else "NTSC"),
                             chip_note),
               "fields": fields, "warnings": [], "payload_base": 0}]

    body_end = _NSF_HEADER + data_len if data_len else size
    body_end = min(body_end, size)
    if body_end > _NSF_HEADER:
        chunks.append({"id": "program", "offset": _NSF_HEADER,
                       "size": body_end - _NSF_HEADER,
                       "summary": "%s bytes of 6502 code and data%s"
                                  % (format(body_end - _NSF_HEADER, ","),
                                     ", banked" if banked else ""),
                       "fields": [], "warnings": [], "payload_base": _NSF_HEADER})
    if body_end < size:
        trailer, tw = _nsfe_chunks(raw, body_end, size, bare=True,
                                   base=body_end)
        warns.extend(tw)
        chunks.append({"id": "metadata", "offset": body_end, "size": size - body_end,
                       "summary": "NSFe metadata appended after the program data",
                       "fields": trailer, "warnings": [], "payload_base": body_end})
    return chunks, warns


def _nsfe_chunks(raw, start, end, bare=False, base=0):
    """Walk an NSFe chunk sequence, returning fields describing what is there.

    The trap this exists to avoid: an NSFe chunk header is LENGTH FIRST, THEN
    FourCC. That is the reverse of RIFF and IFF, so plumbing borrowed from either
    reads the FourCC as a size and walks off into nothing. There is also no
    even-byte padding rule, so chunks are packed tight.

    Mandatoriness is encoded in the capitalisation of the FourCC's first byte:
    A-Z means a player that does not understand the chunk must refuse the file.
    An unknown chunk with a capital initial is the format saying "you do not
    understand me", which is worth more than silently skipping it.
    """
    fields, warns = [], []
    pos, n = start, 0
    while pos + 8 <= end and n < _NSFE_CHUNK_MAX:
        length, = struct.unpack_from("<I", raw, pos)
        fourcc = raw[pos + 4:pos + 8]
        name = fourcc.decode("latin-1", "replace")
        if pos + 8 + length > end:
            warns.append(defect(
                "size.overrun",
                "chunk %r at 0x%X declares %s bytes, which runs past the "
                "end of the file" % (name, pos, format(length, ","))))
            break
        mandatory = 0x41 <= fourcc[0] <= 0x5A
        fields.append(_f(pos - base, 8, name, "%s bytes" % format(length, ","),
                         "mandatory" if mandatory else "optional (skippable)"))
        n += 1
        pos += 8 + length
        if fourcc == b"NEND":
            if pos < end:
                warns.append(defect("bytes.stray",
                                    "%s bytes follow NEND, which ends the file"
                                    % format(end - pos, ",")))
            break
    if n >= _NSFE_CHUNK_MAX:
        warns.append(hit("work_steps", _NSFE_CHUNK_MAX, n,
                         "stopped after %d chunks" % _NSFE_CHUNK_MAX))
    if bare and not fields:
        warns.append(defect("parse.failed", "the appended metadata carries no readable chunk"))
    return fields, warns


def inspect_nsfe(filepath, deep=False):
    """An NSFe: "NSFE" then chunks to the end, length before FourCC.

    Where NSF puts everything in a fixed record, NSFe puts each fact in its own
    chunk, which is how it escapes the 31-character limit on the header strings
    and how it carries per-track times and labels at all. It has no version
    number by design; new revisions arrive as new mandatory chunk types, and the
    capitalisation rule is what makes that safe.
    """
    size = _size(filepath)
    with _open(filepath) as fh:
        raw = fh.read(min(size, _NSF_READ_CAP))
    warns = []
    if size > _NSF_READ_CAP:
        warns.append(hit("read_bytes", _NSF_READ_CAP, size,
                         "read the first %s of %s bytes"
                         % (format(_NSF_READ_CAP, ","), format(size, ","))))
    if len(raw) < 4 or raw[:4] != b"NSFE":
        return [{"id": "chunks", "offset": 0, "size": size,
                 "summary": "not an NSFe (magic is not NSFE)",
                 "fields": [], "warnings": [], "payload_base": 0}], \
               [defect("magic.mismatch", "magic is not NSFE")]

    fields, warns2 = _nsfe_chunks(raw, 4, len(raw))
    warns.extend(warns2)
    seen = {f["name"] for f in fields}
    for need in ("INFO", "DATA", "NEND"):
        if need not in seen:
            warns.append(defect("required.missing", "no %s chunk; the spec requires it" % need))
    if "INFO" in seen and "DATA" in seen:
        order = [f["name"] for f in fields]
        if order.index("DATA") < order.index("INFO"):
            warns.append(defect("chunk.order", "DATA appears before INFO, which the spec forbids"))
    dupes = sorted({f["name"] for f in fields}
                   & {n for n in seen if [f["name"] for f in fields].count(n) > 1})
    for d in dupes:
        warns.append(defect("chunk.order",
                            "chunk %r appears more than once, which the spec disallows" % d))
    unknown = [f["name"] for f in fields
               if f["name"] not in ("INFO", "DATA", "NEND", "BANK", "RATE", "NSF2",
                                    "VRC7", "plst", "psfx", "time", "fade", "tlbl",
                                    "taut", "auth", "text", "mixe", "regn")]
    for u in unknown:
        if u and 0x41 <= ord(u[0]) <= 0x5A:
            warns.append(defect("id.unknown",
                                "unknown MANDATORY chunk %r: the file says it cannot be "
                                "played by anything that does not understand it" % u))

    head = [_f(0, 4, "magic", "NSFE"),
            _f(None, 0, "chunks", len(fields),
               "length comes before the FourCC, the reverse of RIFF")]
    return [{"id": "chunks", "offset": 0, "size": size,
             "summary": "NSFe, %d chunk(s)" % len(fields),
             "fields": head + fields, "warnings": [], "payload_base": 0}], warns


def _sap_boundary(raw):
    """Where the text header stops and the Atari executable starts.

    The spec defines no end-of-header marker: no END tag, no blank line, no
    length. This rule is DERIVED, not quoted. The header's permitted character
    set tops out at 0x7C so 0xFF cannot occur in it, and the binary half must
    open with FF FF, so the first FF FF is the boundary. The check that the byte
    before it is a newline is what separates a real boundary from an FF FF inside
    a corrupt file.
    """
    i = raw.find(b"\xff\xff", 5)
    return -1 if i < 0 else i


def inspect_sap(filepath, deep=False):
    """A SAP: an ASCII tag header, then a standard Atari executable.

    The two halves are literally concatenated -- the spec points out you can
    build one with `cat`. So the walk is two chunks, and the interesting question
    is where one stops, which the format never says.
    """
    size = _size(filepath)
    with _open(filepath) as fh:
        raw = fh.read(min(size, _NSF_READ_CAP))
    warns = []
    if raw[:5] != b"SAP\r\n":
        return [{"id": "header", "offset": 0, "size": size,
                 "summary": "not a SAP (signature is not 'SAP' CR LF)",
                 "fields": [], "warnings": [], "payload_base": 0}], \
               [defect("magic.mismatch", "the first five bytes are not 53 41 50 0D 0A")]

    cut = _sap_boundary(raw)
    text = raw[:cut if cut > 0 else len(raw)]
    tags, fields = {}, []
    times = []
    for line in text.split(b"\n"):
        line = line.rstrip(b"\r")
        if not line or line == b"SAP":
            continue
        s = line.decode("latin-1")
        m = re.match(r"^([A-Z0-9]+)(?: (.*))?$", s)
        if not m:
            warns.append(defect("text.invalid",
                                "header line %r is not TAG or TAG<space>ARG" % s[:40]))
            continue
        tag, arg = m.group(1), (m.group(2) or "")
        if tag == "TIME":
            times.append(arg)
        else:
            tags[tag] = arg
        if tag not in _SAP_TAGS:
            warns.append(defect("id.unknown", "unknown tag %r" % tag))

    bad = [b for b in text if b not in _SAP_TEXT_OK]
    if bad:
        warns.append(defect("text.invalid",
                            "the text header holds %d byte(s) outside the character set "
                            "shared by ASCII and ATASCII" % len(bad)))
    if b"\r\n" not in raw[:cut if cut > 0 else len(raw)][5:] and len(text) > 5:
        warns.append(defect("text.invalid",
                            "header lines are not CR LF terminated, which the spec asks for"))

    typ = tags.get("TYPE", "").strip()
    fields.append(_f(0, 5, "magic", "SAP CR LF", "five bytes, and that is all of it"))
    for tag in ("NAME", "AUTHOR", "DATE"):
        if tag in tags:
            fields.append(_f(None, 0, tag.lower(), tags[tag].strip('"') or "(empty)"))
    if typ:
        fields.append(_f(None, 0, "type", typ,
                         _SAP_TYPES.get(typ, "not a defined player type")))
    songs = _int(tags.get("SONGS", "1"))
    fields.append(_f(None, 0, "songs", songs, "omitted when 1"))
    if "DEFSONG" in tags:
        fields.append(_f(None, 0, "defSong", _int(tags["DEFSONG"]), "0-based"))
    for tag in ("INIT", "PLAYER", "MUSIC", "COVOX"):
        if tag in tags:
            fields.append(_f(None, 0, tag.lower(), "$" + tags[tag].strip().upper(),
                             "hex address"))
    if "FASTPLAY" in tags:
        fp = _int(tags["FASTPLAY"])
        fields.append(_f(None, 0, "fastplay", fp,
                         "scanlines between PLAYER calls; 312 is PAL 50 Hz"))
    for flag in ("STEREO", "NTSC"):
        if flag in tags:
            fields.append(_f(None, 0, flag.lower(), "yes",
                             "dual POKEY" if flag == "STEREO" else "NTSC timing"))
    if times:
        fields.append(_f(None, 0, "times", len(times), "one TIME line per subsong"))

    if not typ:
        warns.append(defect("required.missing", "no TYPE tag, and there is no documented default"))
    elif typ not in _SAP_TYPES:
        warns.append(defect("value.invalid", "TYPE %r is not one of B, C, D, S, R" % typ))
    if typ in ("B", "D", "S") and "INIT" not in tags:
        warns.append(defect("required.missing", "TYPE %s requires INIT" % typ))
    if typ == "C" and "INIT" in tags:
        warns.append(defect("field.inconsistent", "TYPE C must not carry INIT"))
    if typ == "C" and "MUSIC" not in tags:
        warns.append(defect("required.missing", "TYPE C requires MUSIC"))
    if typ and typ != "C" and "MUSIC" in tags:
        warns.append(defect("field.inconsistent", "MUSIC is only valid for TYPE C"))
    if songs == 0:
        warns.append(defect("value.invalid", "SONGS is zero"))
    if songs > 32:
        warns.append(defect("value.invalid", "SONGS is %d; ASAP caps subsongs at 32" % songs))
    if "DEFSONG" in tags and _int(tags["DEFSONG"]) >= max(songs, 1):
        warns.append(defect("value.invalid", "DEFSONG is not below SONGS"))
    if times and len(times) != songs:
        warns.append(defect("count.mismatch",
                            "%d TIME line(s) for %d song(s)" % (len(times), songs)))
    if "COVOX" in tags and tags["COVOX"].strip().upper() != "D600":
        warns.append(defect("value.invalid",
                            "COVOX address is not D600, the only one ASAP supports"))

    chunks = [{"id": "header", "offset": 0, "size": max(cut, 0) if cut > 0 else size,
               "summary": "SAP text header, TYPE %s, %d song(s)"
                          % (typ or "?", songs),
               "fields": fields, "warnings": [], "payload_base": 0}]
    if cut < 0:
        if typ != "R":
            warns.append(defect("magic.mismatch",
                                "no FF FF anywhere, so the Atari executable never begins"))
        return chunks, warns

    blocks, bw = _sap_blocks(raw, cut, len(raw), deep, base=cut)
    warns.extend(bw)
    chunks.append({"id": "binary", "offset": cut, "size": size - cut,
                   "summary": "Atari executable, %d block(s)" % len(
                       [b for b in blocks if b["name"].startswith("block")]),
                   "fields": blocks, "warnings": [], "payload_base": cut})
    return chunks, warns


def _sap_blocks(raw, pos, end, deep, base=0):
    """Walk the Atari executable blocks.

    Two things make this unforgiving. The FF FF is required only on the FIRST
    block and optional after, so a walker cannot resynchronise by scanning for
    it: one wrong length desynchronises everything that follows. And the end
    address is INCLUSIVE, so a block is end - start + 1 bytes; reading it as
    end - start loses the last byte of every block in the file.
    """
    fields, warns = [], []
    n = 0
    if raw[pos:pos + 2] == b"\xff\xff":
        pos += 2
    while pos + 4 <= end and n < _NSFE_CHUNK_MAX:
        if raw[pos:pos + 2] == b"\xff\xff":       # optional repeat
            pos += 2
            if pos + 4 > end:
                break
        start, last = struct.unpack_from("<HH", raw, pos)
        if last < start:
            warns.append(defect("geometry.invalid",
                                "block %d ends at %s before it starts at %s"
                                % (n, _addr(last), _addr(start))))
            break
        length = last - start + 1
        n += 1
        if pos + 4 + length > end:
            warns.append(defect(
                "size.overrun",
                "block %d claims %s bytes but only %s remain; the file "
                "ends mid-block, which players tolerate"
                % (n, format(length, ","), format(end - pos - 4, ","))))
            fields.append(_f(pos - base, 4, "block %d" % n,
                             "%s-%s" % (_addr(start), _addr(last)),
                             "truncated"))
            break
        if deep or n <= 16:
            fields.append(_f(pos - base, 4, "block %d" % n,
                             "%s-%s" % (_addr(start), _addr(last)),
                             "%s bytes, end address is inclusive"
                             % format(length, ",")))
        if 0xD000 <= start <= 0xD7FF:
            warns.append(defect("address.outside",
                                "block %d loads into $%04X, which is hardware register "
                                "space rather than RAM" % (n, start)))
        pos += 4 + length
    if n > 16 and not deep:
        fields.append(_f(None, 0, "more", "%d further block(s)" % (n - 16),
                         "shown with deep inspection"))
    return fields, warns


def _int(s):
    try:
        return int(str(s).strip())
    except ValueError:
        return 0


# ── GBS: the Game Boy's answer to NSF ───────────────────────────────

_GBS_HEADER = 0x70
# The three text slots, and where the load/init/play addresses must fall:
# the Game Boy's cartridge ROM window is $0400-$7FFF once the boot ROM has
# been paged out, and the spec says so in as many words.
_GBS_LOAD_LO, _GBS_LOAD_HI = 0x0400, 0x7FFF


_GBS_DIVIDER = {0: 1024, 1: 16, 2: 64, 3: 256}


def _gbs_timer_note(tma, tac):
    """What the two timer bytes mean, from the spec's formula."""
    if not tac & 0x04:
        return "timer off: play runs on VBlank, 60 Hz"
    hz = 4194304 / (_GBS_DIVIDER[tac & 3] * (256 - tma))
    return "play runs on the timer, %.1f Hz (modulo %d is a period of %d)" % (
        hz, tma, 256 - tma)


def inspect_gbs(filepath, deep=False):
    """A GBS: a 112-byte header, then Game Boy code and data loaded at an
    address the header names.

    The same shape as NSF -- a header naming init and play routines and a
    binary blob to run them from -- so the same reader would serve, except
    that the Game Boy has no expansion chips and no bank table: anything past
    $7FFF is simply the next 16 KB ROM bank, and a player maps it in through
    the cartridge's $2000 register. Three 32-byte text slots, three
    addresses, a stack pointer and two timer bytes, and that is the whole
    header. Spec: gbsplay's gbsformat.txt, the format's own reference.
    """
    size = _size(filepath)
    warns = []
    with _open(filepath) as fh:
        raw = fh.read(min(size, _NSF_READ_CAP))
    if size > _NSF_READ_CAP:
        warns.append(hit("read_bytes", _NSF_READ_CAP, size,
                         "read the first %s of %s bytes"
                         % (format(_NSF_READ_CAP, ","), format(size, ","))))
    if raw[:3] != b"GBS":
        return [{"id": "header", "offset": 0, "size": size,
                 "summary": "not a GBS (signature is not 'GBS')",
                 "fields": [], "warnings": [], "payload_base": 0}], \
            [defect("magic.mismatch", "the first three bytes are not 47 42 53")]
    if len(raw) < _GBS_HEADER:
        return [{"id": "header", "offset": 0, "size": size,
                 "summary": "GBS header truncated at %d of %d bytes"
                            % (len(raw), _GBS_HEADER),
                 "fields": [], "warnings": [], "payload_base": 0}], \
            [defect("header.truncated", "file ends inside the 112-byte header")]

    version, songs, first = raw[3], raw[4], raw[5]
    load, init, play, sp = struct.unpack_from("<HHHH", raw, 6)
    tma, tac = raw[0xE], raw[0xF]
    fields = [
        _f(0x00, 3, "magic", "GBS"),
        _f(0x03, 1, "version", version, "1 is the only version defined"),
        _f(0x04, 1, "songs", songs, "1-255"),
        _f(0x05, 1, "firstSong", first, "1-based; usually 1"),
        _f(0x06, 2, "load", _addr(load), "where the code below is placed"),
        _f(0x08, 2, "init", _addr(init), "called once per song, A = song"),
        _f(0x0A, 2, "play", _addr(play), "called every tick"),
        _f(0x0C, 2, "stack", _addr(sp)),
        _f(0x0E, 1, "timerModulo", tma, _gbs_timer_note(tma, tac)),
        _f(0x0F, 1, "timerControl", "0x%02X" % tac,
           "bit 2 enables the timer; bits 0-1 pick the divider"),
    ]
    for off, name in ((0x10, "title"), (0x30, "author"), (0x50, "copyright")):
        text, unterminated, dirty, non_ascii = _slot(raw, off)
        note = ""
        if non_ascii:
            note = "not plain ASCII; shown as latin-1"
        fields.append(_f(off, _STR_SLOT, name, text or "(empty)", note))
        if unterminated:
            warns.append(defect("text.invalid", "%s fills all 32 bytes with no NUL" % name))
        if dirty:
            warns.append(defect("reserved.nonzero",
                                "%s has non-NUL bytes after its terminator" % name))

    if version != 1:
        warns.append(defect("value.invalid", "version %d; only 1 is defined" % version))
    if songs == 0:
        warns.append(defect("value.invalid", "song count is 0"))
    if first == 0 or first > songs:
        warns.append(defect("value.invalid", "first song %d is outside 1..%d" % (first, songs)))
    for name, a in (("load", load), ("init", init), ("play", play)):
        if not _GBS_LOAD_LO <= a <= _GBS_LOAD_HI:
            warns.append(defect("address.outside",
                                "%s address %s is outside the cartridge window "
                                "$0400-$7FFF" % (name, _addr(a))))
    if init < load or play < load:
        warns.append(defect("address.outside",
                            "init or play sits below the load address, so it is not "
                            "in the code this file carries"))

    code = size - _GBS_HEADER
    chunks = [{"id": "header", "offset": 0, "size": _GBS_HEADER,
               "summary": "GBS v%d, %d song%s%s" % (
                   version, songs, "" if songs == 1 else "s",
                   (" -- " + fields[10]["value"]) if fields[10]["value"] != "(empty)" else ""),
               "fields": fields, "warnings": [], "payload_base": 0,
               "payload_len": _GBS_HEADER, "extent_len": _GBS_HEADER}]
    if code > 0:
        top = load + code - 1
        chunks.append({"id": "code", "offset": _GBS_HEADER, "size": code,
                       "summary": "%s bytes of Game Boy code and data, %s-%s"
                                  % (format(code, ","), _addr(load), _addr(top)),
                       "fields": [_f(None, 0, "loads_at", _addr(load)),
                                  _f(None, 0, "ends_at", _addr(top),
                                     "an address in the last ROM bank, not "
                                     "the CPU's; see banks" if top > 0x7FFF
                                     else "")],
                       "warnings": [], "payload_base": _GBS_HEADER,
                       "payload_len": code, "extent_len": code})
        if top > 0x7FFF:
            # the spec's banking: bytes past $7FFF go into 16 KB ROM banks
            # from bank 1, mapped at $4000-$7FFF by a write to $2000. A
            # file larger than the address space is the normal case for a
            # whole game's music, not damage.
            banks = 1 + -(-(top - 0x7FFF) // 0x4000)
            chunks[-1]["fields"].append(
                _f(None, 0, "banks", banks,
                   "16 KB ROM banks; bank 0 at the load address, the rest "
                   "switched in at $4000 by writing to $2000"))
    return chunks, warns


# ── HES: PC Engine / TurboGrafx-16 ────────────────────────────────────────

_HES_HEADER = 0x20
_HES_BLOCK_HEADER = 0x10
_HES_DATA = b"DATA"
# A HuC6280 has a 2 MB physical space; nothing a block can load past it.
_HES_READ_CAP = 2 * 1024 * 1024
# Data blocks listed. Every real file measured has one.
_HES_BLOCK_LIST_CAP = 64


def inspect_hes(filepath, deep=False):
    """A HES: the PC Engine's music cut out of the game, NSF-shaped.

    A 0x10-byte header: "HESM", a version, the first track, the address
    the player calls with a track number, and eight bytes that preset the
    HuC6280's MPR bank registers, which is how a 21-bit machine maps 8 KB
    pages into a 16-bit space. Then a DATA block: tag, size, address, four
    unused bytes, and the bytes. The address field reads 0x20 in every
    file measured, which is where the block's own bytes begin, so it is
    reported as the field and no more is claimed of it. Spec: the format's
    own hes.txt and game_music_emu's reader.
    """
    from acidcat.core.walk.base import Unsupported
    size = _size(filepath)
    warns = []
    with _open(filepath) as fh:
        raw = fh.read(min(size, _HES_READ_CAP))
    if size > _HES_READ_CAP:
        warns.append(hit("read_bytes", _HES_READ_CAP, size,
                         "file is %d bytes; read the first %d" % (size, _HES_READ_CAP)))
    if raw[:4] != b"HESM" or len(raw) < _HES_HEADER:
        raise Unsupported("no HESM header")
    version, first = raw[4], raw[5]
    init = struct.unpack_from("<H", raw, 6)[0]
    mpr = raw[8:16]
    fields = [
        _f(0x00, 4, "magic", "HESM"),
        _f(0x04, 1, "version", version),
        _f(0x05, 1, "firstTrack", first),
        _f(0x06, 2, "init", _addr(init), "called with the track number"),
        _f(0x08, 8, "mpr", " ".join("%02X" % b for b in mpr),
           "the eight 8 KB page registers, MPR0-MPR7, as the player presets them"),
    ]
    chunks = [{"id": "header", "offset": 0, "size": _HES_BLOCK_HEADER,
               "summary": "HES v%d, first track %d" % (version, first),
               "fields": fields, "warnings": [], "payload_base": 0}]
    pos = _HES_BLOCK_HEADER
    n = 0
    while pos + _HES_BLOCK_HEADER <= len(raw) and raw[pos:pos + 4] == _HES_DATA:
        if n >= _HES_BLOCK_LIST_CAP:
            warns.append(hit("list_rows", _HES_BLOCK_LIST_CAP, n,
                             "listing the first %d data blocks" % _HES_BLOCK_LIST_CAP))
            break
        bsize, baddr = struct.unpack_from("<II", raw, pos + 4)
        have = max(min(bsize, size - pos - _HES_BLOCK_HEADER), 0)
        c = {"id": "DATA" if n == 0 else "DATA[%d]" % n, "offset": pos,
             "size": _HES_BLOCK_HEADER + have,
             "summary": "%s bytes of HuC6280 code and data" % format(have, ","),
             # the 16-byte block header sits before the payload, so its
             # fields have negative offsets from it
             "fields": [_f(4 - _HES_BLOCK_HEADER, 4, "size", bsize, ""),
                        _f(8 - _HES_BLOCK_HEADER, 4, "address", "0x%08X" % baddr,
                           "the spec's load address"),
                        _f(12 - _HES_BLOCK_HEADER, 4, "unused",
                           raw[pos + 12:pos + 16].hex(), "")],
             "warnings": [], "payload_base": pos + _HES_BLOCK_HEADER, "payload_len": have}
        if have < bsize:
            c["warnings"].append(defect("size.overrun",
                                        "declares %s bytes and the file holds %s"
                                        % (format(bsize, ","), format(have, ","))))
            warns.append(defect("size.overrun",
                                "the data block declares more than the file holds"))
        chunks.append(c)
        pos += _HES_BLOCK_HEADER + have
        n += 1
        if have < bsize:
            break
    if n == 0:
        warns.append(defect("required.missing", "no DATA block follows the header"))
    if pos < size:
        chunks.append({"id": "trailing", "offset": pos, "size": size - pos,
                       "summary": "%d bytes after the last data block" % (size - pos),
                       "fields": [], "warnings": [], "payload_base": pos})
    return chunks, warns


# ── KSS: MSX and Master System ────────────────────────────────────────────

_KSS_HEADER = 0x10
_KSS_MAGICS = (b"KSCC", b"KSSX")
_KSS_BANK_8K = 8192
_KSS_BANK_16K = 16384
# A Z80 with a megabyte of banks is the largest real file measured by a
# wide margin; 16 MB reads any of them.
_KSS_READ_CAP = 16 * 1024 * 1024
# Banks listed as chunks. The extra-bank count is seven bits.
_KSS_BANK_LIST_CAP = 128
_KSS_CHIP_BITS = [(0x01, "FMPAC (YM2413)"), (0x02, "FM unit"),
                  (0x04, "SN76489 (Sega mode)"), (0x08, "RAM mode"),
                  (0x10, "MSX-AUDIO (Y8950)")]


def inspect_kss(filepath, deep=False):
    """A KSS: a Z80 sound driver and its data for the MSX, or in Sega mode
    the Master System, with the banks it switches through.

    Sixteen bytes: "KSCC" (or "KSSX", which adds an extension the byte at
    0x0E sizes), then the load address and length of the init data, the
    init and play addresses, the first bank number, an extra-bank byte
    whose top bit picks 8 KB banks over 16 KB and whose low seven bits
    count them, a reserved byte, and chip flags. The init data comes
    first, then the banks in order. A file may END INSIDE ITS LAST BANK:
    the player zero-fills, and nearly half of real files do it, so it is a
    fact on the bank and not a warning. Spec: libkss's reader.
    """
    from acidcat.core.walk.base import Unsupported
    size = _size(filepath)
    warns = []
    with _open(filepath) as fh:
        raw = fh.read(min(size, _KSS_READ_CAP))
    if size > _KSS_READ_CAP:
        warns.append(hit("read_bytes", _KSS_READ_CAP, size,
                         "file is %d bytes; read the first %d" % (size, _KSS_READ_CAP)))
    if raw[:4] not in _KSS_MAGICS or len(raw) < _KSS_HEADER:
        raise Unsupported("no KSCC/KSSX header")
    extended = raw[:4] == b"KSSX"
    load, ilen, init, play = struct.unpack_from("<HHHH", raw, 4)
    start_bank, ebanks, extra, chips = raw[12], raw[13], raw[14], raw[15]
    banks = ebanks & 0x7F
    bank_size = _KSS_BANK_8K if ebanks & 0x80 else _KSS_BANK_16K
    hdr = _KSS_HEADER + (extra if extended else 0)
    chip_names = [name for bit, name in _KSS_CHIP_BITS if chips & bit] or ["PSG only"]
    fields = [
        _f(0x00, 4, "magic", raw[:4].decode("ascii"),
           "KSSX carries an extension the byte at 0x0E sizes" if extended else ""),
        _f(0x04, 2, "load", _addr(load), "where the init data is placed"),
        _f(0x06, 2, "initLength", ilen, "bytes of init data"),
        _f(0x08, 2, "init", _addr(init), "called with the song number in A"),
        _f(0x0A, 2, "play", _addr(play), "called every tick"),
        _f(0x0C, 1, "startBank", start_bank),
        _f(0x0D, 1, "extraBanks", "0x%02X" % ebanks,
           "%d bank%s of %d KB" % (banks, "" if banks == 1 else "s", bank_size // 1024)
           if banks else "none"),
        _f(0x0E, 1, "extension", extra, "bytes of extended header" if extended else "reserved"),
        _f(0x0F, 1, "chips", "0x%02X" % chips, ", ".join(chip_names)),
    ]
    chunks = [{"id": "header", "offset": 0, "size": _KSS_HEADER,
               "summary": "KSS, %s, init at %s, %d bank%s"
                          % (", ".join(chip_names), _addr(init), banks,
                             "" if banks == 1 else "s"),
               "fields": fields, "warnings": [], "payload_base": 0}]
    if extended and extra:
        ext = raw[_KSS_HEADER:hdr]
        chunks.append({"id": "extension", "offset": _KSS_HEADER, "size": len(ext),
                       "summary": "KSSX extension, %d bytes" % len(ext),
                       "fields": [_f(0, len(ext), "bytes", ext.hex(" "),
                                     "reserved by the extended header; zero in "
                                     "nearly every file")],
                       "warnings": [], "payload_base": _KSS_HEADER})
    pos = min(hdr, size)
    have = min(ilen, max(size - pos, 0))
    c = {"id": "init", "offset": pos, "size": have,
         "summary": "%s bytes of init data at %s" % (format(have, ","), _addr(load)),
         "fields": [_f(None, 0, "declared", ilen)],
         "warnings": [], "payload_base": pos}
    if have < ilen:
        c["fields"].append(_f(None, 0, "present", have, "the file ends inside it"))
    chunks.append(c)
    pos += have
    listed = 0
    for i in range(banks):
        if pos >= size:
            break
        if listed >= _KSS_BANK_LIST_CAP:
            warns.append(hit("list_rows", _KSS_BANK_LIST_CAP, banks,
                             "listing the first %d of %d banks" % (_KSS_BANK_LIST_CAP, banks)))
            break
        n = min(bank_size, size - pos)
        chunks.append({"id": "bank[%d]" % (start_bank + i), "offset": pos, "size": n,
                       "summary": "bank %d, %s bytes%s" % (
                           start_bank + i, format(n, ","),
                           " of %d; the file ends inside it and a player zero-fills"
                           % bank_size if n < bank_size else ""),
                       "fields": [], "warnings": [], "payload_base": pos})
        pos += n
        listed += 1
    if pos < size:
        chunks.append({"id": "trailing", "offset": pos, "size": size - pos,
                       "summary": "%d bytes after the last bank" % (size - pos),
                       "fields": [], "warnings": [], "payload_base": pos})
    return chunks, warns
