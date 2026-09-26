"""SID walker: the PSID/RSID header, and the C64 memory image behind it.

Two chunks, because a .sid really is only two things. The header is a fixed
big-endian record of 0x76 or 0x7C bytes. Everything after it is an opaque C64
memory image: 6502 code and its data, which this walker describes the shape and
placement of rather than pretending to decode.

What it can say about that image is more than it looks. Where it loads, how far
it reaches, whether the init and play entry points fall inside it, whether the
tune addresses a second or third SID chip and at what address, which chip
revision and which video standard the music was written for, and -- for RSID --
whether the file honours the constraints that make it safe on real hardware.

See core/formats/sid.py for the layout and the byte-order trap.
"""


from acidcat.core.formats import sid as sidmod
from acidcat.core.primitives.notes import coverage
from acidcat.core.walk.base import _f, _open, _size

# A SID is tiny -- the largest of 630 measured tunes is 60 KB. The cap is far
# above anything real so that a forged header cannot make us read a huge file,
# while never truncating a genuine one.
_SID_READ_CAP = 16 * 1024 * 1024


def _addr(a):
    return "$%04X" % (a & 0xFFFF)


def inspect_sid(filepath, deep=False):
    size = _size(filepath)
    with _open(filepath) as fh:
        raw = fh.read(min(size, _SID_READ_CAP))
    warns = []
    if size > _SID_READ_CAP:
        # Named in bytes rather than MB. The cap shortens the ANSWER here --
        # the memory image length, and the extent derived from it, would
        # describe a prefix -- so the note has to be exact at any size, and
        # a rounded "0 MB" is what a small cap collapses to.
        warns.append(coverage("file is %d bytes; parsed the first %d"
                              % (size, len(raw))))

    h = sidmod.parse_header(raw)
    magic = h["magic"].decode("latin-1", "replace")
    version = h["version"]
    hdr_len = min(h["data_offset"] or sidmod.header_size(version), len(raw))

    for field, complaint in sidmod.violations(h, len(raw)):
        warns.append("%s: %s" % (field, complaint))
    if h["truncated"]:
        warns.append("file is shorter than a v1 header (0x%02X bytes)"
                     % sidmod.HEADER_V1)

    chunks = [_header_chunk(h, raw, magic, version, hdr_len, deep)]
    if h["data_offset"] and h["data_offset"] < len(raw):
        chunks.append(_data_chunk(h, raw))
    else:
        warns.append("no C64 data: dataOffset is at or past the end of the file")
    return chunks, warns


def _header_chunk(h, raw, magic, version, hdr_len, deep):
    variant = ("RSID -- requires a real C64 environment"
               if h["is_rsid"] else "PSID -- runs on emulators too")
    who = h["author"] or "unknown"
    title = h["name"] or "untitled"
    songs = h["songs"]
    summary = ("%s v%d, %r by %s, %d %s"
               % (magic, version, title, who, songs,
                  "subtune" if songs == 1 else "subtunes"))

    fields = [
        _f(0x00, 4, "magicID", magic, variant),
        _f(0x04, 2, "version", version,
           "v1 header is 0x76 bytes; v2, v3 and v4 are all 0x7C",
           enc=">H", raw=version),
        _f(0x06, 2, "dataOffset", "0x%04X" % h["data_offset"],
           "where the C64 memory image begins", enc=">H",
           raw=h["data_offset"], xref=h["data_offset"]),
        _f(0x08, 2, "loadAddress", _addr(h["load_address"]),
           ("0 means the load address is the first two bytes of the C64 data, "
            "little-endian -- which is how every real tune does it")
           if h["load_in_data"] else "explicit; the data carry no load address",
           enc=">H", raw=h["load_address"]),
        _f(0x0A, 2, "initAddress", _addr(h["init_address"]),
           "0 means the effective load address (%s)" % _addr(h["effective_load"])
           if not h["init_address"] else
           "called with the subtune number in the accumulator",
           enc=">H", raw=h["init_address"]),
        _f(0x0C, 2, "playAddress", _addr(h["play_address"]),
           ("0 means init installs its own interrupt handler; required for RSID"
            if not h["play_address"] else
            "called at interrupt rate to produce sound"),
           enc=">H", raw=h["play_address"]),
        _f(0x0E, 2, "songs", songs, "1-256 subtunes", enc=">H", raw=songs),
        _f(0x10, 2, "startSong", h["start_song"], "the subtune played by default",
           enc=">H", raw=h["start_song"]),
        _f(0x12, 4, "speed", "0x%08X" % h["speed"], _speed_note(h),
           enc=">I", raw=h["speed"]),
    ]

    for off, name in ((0x16, "name"), (0x36, "author"), (0x56, "released")):
        note = "32 bytes, Windows-1252"
        if not h.get(name + "_terminated", True):
            note += "; FULL 32 with no NUL -- a C-string read runs into the next field"
        fields.append(_f(off, 32, name, h[name] or "(empty)", note))

    if h["has_v2_fields"]:
        fields.extend(_v2_fields(h, version))
    elif version >= 2:
        fields.append(_f(0x76, 0, "flags",
                         "not present", "the header declares v%d but the file "
                         "is too short to hold the v2 tail" % version))

    fields.append(_f(None, 0, "songlengthMD5", sidmod.songlength_md5(raw),
                     "MD5 of the whole file; the HVSC Songlengths.md5 key"))

    if deep and h["songs"]:
        fields.append(_f(None, 0, "speed table", _speed_table(h),
                         "per subtune, from the speed bits"))

    return {"id": "header", "offset": 0, "size": hdr_len,
            "summary": summary, "fields": fields, "warnings": [],
            "payload_base": 0}


def _v2_fields(h, version):
    out = [
        _f(0x76, 2, "flags", "0x%04X" % h["flags"], _flags_note(h),
           enc=">H", raw=h["flags"]),
        _f(0x78, 1, "startPage", "0x%02X" % h["start_page"],
           _startpage_note(h), raw=h["start_page"]),
        _f(0x79, 1, "pageLength", h["page_length"],
           "free pages after startPage" if h["start_page"] not in (0, 0xFF)
           else "must be 0 at this startPage", raw=h["page_length"]),
    ]
    out.append(_f(0x7A, 1, "secondSIDAddress",
                  "0x%02X" % h["second_sid_byte"],
                  _extra_sid_note(h["second_sid_byte"], h["second_sid"], 3, version),
                  raw=h["second_sid_byte"]))
    out.append(_f(0x7B, 1, "thirdSIDAddress",
                  "0x%02X" % h["third_sid_byte"],
                  _extra_sid_note(h["third_sid_byte"], h["third_sid"], 4, version),
                  raw=h["third_sid_byte"]))
    return out


def _extra_sid_note(value, addr, needs_version, version):
    if addr is None:
        if value == 0:
            return "no extra SID (a v%d field; 0 elsewhere)" % needs_version
        return ("0x%02X is not a legal position -- even values in 0x42-0x7F or "
                "0xE0-0xFE only, so no extra SID is used" % value)
    where = "%s" % _addr(addr)
    if version < needs_version:
        return ("%s, but the header declares v%d and this field is v%d specific"
                % (where, version, needs_version))
    return "a second chip mapped at %s" % where


def _flags_note(h):
    bits = []
    bits.append("MUS data" if h["mus_player"] else "built-in player")
    if h["is_rsid"]:
        bits.append("C64 BASIC" if h["psid_specific"] else "no BASIC")
    else:
        bits.append("PlaySID specific" if h["psid_specific"] else "C64 compatible")
    bits.append(sidmod.clock_name(h["clock"]))
    bits.append(sidmod.model_name(h["sid_model"]))
    if h["sid_model_2"]:
        bits.append("2nd SID %s" % sidmod.model_name(h["sid_model_2"]))
    if h["sid_model_3"]:
        bits.append("3rd SID %s" % sidmod.model_name(h["sid_model_3"]))
    return ", ".join(bits)


def _startpage_note(h):
    if h["start_page"] == 0:
        return "the tune is clean: it writes nothing outside its own data range"
    if h["start_page"] == 0xFF:
        return "not one free page; a driver cannot be relocated here"
    return ("largest free range starts at $%02X00, %d page(s)"
            % (h["start_page"], h["page_length"]))


def _speed_note(h):
    if h["is_rsid"]:
        return "RSID requires 0; the tune sets up its own timing"
    if not h["speed"]:
        return "all subtunes on vertical blank (50 Hz PAL, 60 Hz NTSC)"
    kinds = {sidmod.song_speed(h["speed"], n, h["songs"], h["psid_specific"],
                               h["version"])
             for n in range(1, min(h["songs"], 32) + 1)}
    if kinds == {"CIA"}:
        return "every subtune on the CIA 1 timer"
    return "one bit per subtune: 0 vertical blank, 1 CIA 1 timer (mixed here)"


def _speed_table(h):
    parts = []
    for n in range(1, min(h["songs"], 32) + 1):
        parts.append("%d:%s" % (n, sidmod.song_speed(
            h["speed"], n, h["songs"], h["psid_specific"], h["version"])))
    if h["songs"] > 32:
        parts.append("... (%d more)" % (h["songs"] - 32))
    return " ".join(parts)


def _data_chunk(h, raw):
    off = h["data_offset"]
    body = len(raw) - off
    fields = []
    if h["load_in_data"] and body >= 2:
        fields.append(_f(0, 2, "loadAddress", _addr(h["effective_load"]),
                         "little-endian, inside the C64 data -- the header is "
                         "big-endian, this is not", enc="<H",
                         raw=h["effective_load"]))
    fields.append(_f(None, 0, "memory", "%s-%s"
                     % (_addr(h["effective_load"]), _addr(h["memory_end"])),
                     "where the image sits in C64 memory (%d bytes)"
                     % h["code_length"]))
    fields.append(_f(None, 0, "init", _addr(h["effective_init"]),
                     _entry_note(h, h["effective_init"])))
    if h["play_address"]:
        fields.append(_f(None, 0, "play", _addr(h["play_address"]),
                         _entry_note(h, h["play_address"])))

    summary = ("C64 memory image, %d bytes at %s"
               % (h["code_length"], _addr(h["effective_load"])))
    if h["mus_player"]:
        summary = ("Compute!'s Sidplayer MUS data, %d bytes -- no built-in "
                   "player, one must be merged to replay it" % h["code_length"])
    return {"id": "data", "offset": off, "size": body, "summary": summary,
            "fields": fields, "warnings": [], "payload_base": off}


def _entry_note(h, addr):
    """Whether an entry point lands inside the loaded image.

    An address outside it is not automatically wrong -- a tune may legitimately
    jump into a driver it relocated, and RSID init routines run with both ROMs
    banked in. It is worth saying either way, because the common case is inside
    and the exception is where the interesting tunes live.
    """
    lo, hi = h["effective_load"], h["memory_end"]
    if h["code_length"] and lo <= addr <= hi:
        return "inside the loaded image (%s-%s)" % (_addr(lo), _addr(hi))
    if sidmod.in_rom(addr):
        return "outside the image, in a ROM/IO window"
    return "outside the loaded image (%s-%s)" % (_addr(lo), _addr(hi))
