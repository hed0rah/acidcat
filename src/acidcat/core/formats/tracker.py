"""Tracker-module parsers: ProTracker MOD (and its 15-instrument Soundtracker
ancestor), FastTracker II XM, Impulse Tracker IT.

These three formats are "song + instrument bank + concatenated PCM": a header,
a pattern order table, pattern data, then sample descriptors whose PCM lives
elsewhere in the same file. That last part is why they belong in acidcat --
every sample is a carveable byte region, and IT even stores an on-disk offset
table (instrument/sample/pattern pointers) that reads like a tiny filesystem.

The parsers are read-only and defensive: a lied-about count or length yields a
warning and a clamped read, never an unbounded allocation or a crash. Sample
byte offsets are resolved so a consumer (carve, the hex view) can extract the
raw PCM; the decoders for XM/IT delta or IT compressed PCM are intentionally
not here -- this maps structure, it does not render audio yet.
"""

import struct

# MOD magics -> channel count. The 4-channel variants have no digit to read.
_MOD_MAGIC_4CH = {b"M.K.", b"M!K!", b"M&K!", b"FLT4", b"EXO4", b"4CHN"}


def _c(b):
    """A null-terminated, space-trimmed latin-1 string (tracker names are ascii/
    cp437 in practice; latin-1 never raises and keeps the bytes reversible)."""
    return b.split(b"\x00")[0].decode("latin-1", errors="replace").rstrip()


def _mod_channels(magic):
    """Channel count from a MOD's offset-1080 magic. Handles the classic 4ch
    tags plus the 'nCHN' / 'nnCH' family (6CHN, 8CHN, 16CH, 32CH, ...)."""
    if magic in _MOD_MAGIC_4CH:
        return 4
    if magic[1:4] == b"CHN" and magic[0:1].isdigit():
        return int(magic[0:1])
    if magic[2:4] == b"CH" and magic[0:2].isdigit():
        return int(magic[0:2])
    if magic[:3] == b"TDZ" and magic[3:4].isdigit():
        return int(magic[3:4])
    return 4


def is_mod(data):
    """True when the 4 bytes at offset 1080 are a known MOD magic. MOD has no
    leading signature, so this is the only reliable tell (and needs 1084 bytes)."""
    if len(data) < 1084:
        return False
    magic = data[1080:1084]
    if magic in _MOD_MAGIC_4CH:
        return True
    return (magic[1:4] == b"CHN" and magic[0:1].isdigit()) \
        or (magic[2:4] == b"CH" and magic[0:2].isdigit()) \
        or (magic[:3] == b"TDZ" and magic[3:4].isdigit())


# The original Soundtracker module (Karsten Obarski, 1987) and its clones
# up to NoiseTracker: 15 instruments, no magic, always four channels. The
# 31-instrument layout with the tag at 1080 is the same file with sixteen
# more instrument headers pushed in front of the order table, which is why
# every offset below is derived from the instrument count.
MOD15_INSTRUMENTS = 15
MOD15_PATTERNS_AT = 600                    # 20 + 15 * 30 + 1 + 1 + 128
MOD15_TRAILING_CAP = 1024                  # bytes after the last sample still accepted


def _mod_layout(instruments):
    """(song_length_at, patterns_at) for a 15- or 31-instrument module."""
    at = 20 + instruments * 30
    return at, at + 2 + 128 + (4 if instruments == 31 else 0)


def mod15_expected_size(data):
    """The size a 15-instrument module of this header must have, or None if
    the header cannot be one. Identification is arithmetic because there is
    no magic: 600 bytes of header, 1,024 per pattern, then the samples,
    and the total has to land on the file's end. Zero false positives over
    214,483 files that are not one; 1,914 of 1,935 real ones accepted."""
    if len(data) < MOD15_PATTERNS_AT:
        return None
    total = 0
    for i in range(MOD15_INSTRUMENTS):
        off = 20 + i * 30
        length, _ft, vol = struct.unpack_from(">HBB", data, off + 22)
        if vol > 64:
            return None
        total += length * 2
    song_length = data[470]
    order = data[472:600]
    if not 1 <= song_length <= 128 or max(order) > 127:
        return None
    # the pattern count comes from all 128 slots, not the song length:
    # 121 of 1,935 real files keep patterns past the end of the song, and
    # every player stores them
    return MOD15_PATTERNS_AT + (max(order) + 1) * 1024 + total


def is_mod15(data, filesize):
    """True for a 15-instrument Soundtracker module. Needs the file size,
    not just the head: the arithmetic is the identification. A file with a
    31-instrument magic is never one."""
    if len(data) >= 1084 and is_mod(data):
        return False
    want = mod15_expected_size(data)
    return want is not None and 0 <= filesize - want <= MOD15_TRAILING_CAP


def parse_mod(data, instruments=None):
    """Parse a ProTracker MOD, or a 15-instrument Soundtracker one. Returns
    a dict: title, channels, magic, instruments, song_length, restart,
    order (list), num_patterns, pattern_data_off, samples (each with
    name/length/finetune/volume/loop + resolved offset), and warnings.

    `instruments` is 31 or 15; None picks 31 when the magic is there and 15
    when the arithmetic holds, else 31 (a damaged ProTracker file is the
    likelier reading and the caller has already decided it is a MOD)."""
    warns = []
    if instruments is None:
        instruments = 15 if (not is_mod(data)
                             and is_mod15(data, len(data))) else 31
    title = _c(data[:20])
    samples = []
    for i in range(instruments):
        off = 20 + i * 30
        length = struct.unpack_from(">H", data, off + 22)[0] * 2  # stored in words
        loop_start = struct.unpack_from(">H", data, off + 26)[0]
        loop_len = struct.unpack_from(">H", data, off + 28)[0] * 2
        # Soundtracker wrote the repeat point in BYTES; ProTracker in words.
        # Measured on 1,535 looped samples: 858 fit only as bytes, none fit
        # only as words.
        if instruments == 31:
            loop_start *= 2
        samples.append({
            "name": _c(data[off:off + 22]),
            "length": length,
            "finetune": data[off + 24] & 0x0F,
            "volume": data[off + 25],
            "loop_start": loop_start,
            "loop_len": loop_len,
            "hdr_off": off,
        })
    song_length_at, pattern_data_off = _mod_layout(instruments)
    song_length = data[song_length_at]
    # byte 951 is the restart position in ProTracker; in the 15-instrument
    # layout the same byte is Ultimate Soundtracker's TEMPO, 120 by default
    restart = data[song_length_at + 1]
    order = list(data[song_length_at + 2:song_length_at + 2 + 128])
    if instruments == 31:
        magic = data[1080:1084]
        channels = _mod_channels(magic)
    else:
        magic = b""
        channels = 4
    num_patterns = (max(order) + 1) if order else 0
    pattern_bytes = num_patterns * 64 * channels * 4
    cur = pattern_data_off + pattern_bytes
    for s in samples:
        s["offset"] = cur if s["length"] else None
        cur += s["length"]
    if cur > len(data):
        warns.append(f"sample data runs to {cur:,} but file is {len(data):,} bytes")
    return {
        "kind": "mod", "title": title, "channels": channels,
        "magic": magic.decode("latin-1", errors="replace"),
        "instruments": instruments,
        "song_length": song_length, "restart": restart, "order": order,
        "num_patterns": num_patterns, "pattern_data_off": pattern_data_off,
        "sample_data_off": pattern_data_off + pattern_bytes,
        "samples": samples, "warnings": warns,
    }


# XM header flags (offset 74 in the header body) and the version word.
def parse_xm(data):
    """Parse a FastTracker II XM. Walks the pattern blocks and instrument
    blocks (both variable length) to resolve each sample's byte offset."""
    warns = []
    modname = _c(data[17:37])
    tracker = _c(data[38:58])
    version = struct.unpack_from("<H", data, 58)[0]
    hdr_size = struct.unpack_from("<I", data, 60)[0]
    song_length = struct.unpack_from("<H", data, 64)[0]
    restart = struct.unpack_from("<H", data, 66)[0]
    channels = struct.unpack_from("<H", data, 68)[0]
    num_patterns = struct.unpack_from("<H", data, 70)[0]
    num_instruments = struct.unpack_from("<H", data, 72)[0]
    flags = struct.unpack_from("<H", data, 74)[0]
    tempo = struct.unpack_from("<H", data, 76)[0]
    bpm = struct.unpack_from("<H", data, 78)[0]
    order = list(data[80:80 + min(song_length, 256)])

    pos = 60 + hdr_size
    patterns = []
    for _ in range(num_patterns):
        if pos + 9 > len(data):
            warns.append("pattern table truncated")
            break
        plen = struct.unpack_from("<I", data, pos)[0]
        rows = struct.unpack_from("<H", data, pos + 5)[0]
        packed = struct.unpack_from("<H", data, pos + 7)[0]
        patterns.append({"offset": pos, "rows": rows, "packed": packed,
                         "size": plen + packed})
        pos += plen + packed

    instruments = []
    for _ in range(num_instruments):
        if pos + 29 > len(data):
            warns.append("instrument table truncated")
            break
        isize = struct.unpack_from("<I", data, pos)[0]
        iname = _c(data[pos + 4:pos + 26])
        nsamp = struct.unpack_from("<H", data, pos + 27)[0]
        ihdr = pos
        pos += isize
        smps = []
        if nsamp > 0:
            for s in range(nsamp):
                so = pos + s * 40
                if so + 40 > len(data):
                    warns.append("sample header table truncated")
                    nsamp = s
                    break
                slen = struct.unpack_from("<I", data, so)[0]
                stype = data[so + 14]
                smps.append({"length": slen, "type": stype,
                             "name": _c(data[so + 18:so + 40]), "hdr_off": so,
                             "bits16": bool(stype & 0x10)})
            pos += nsamp * 40
            for sm in smps:
                sm["offset"] = pos if sm["length"] else None
                pos += sm["length"]
        instruments.append({"name": iname, "num_samples": nsamp,
                            "offset": ihdr, "size": isize, "samples": smps})
    if pos > len(data):
        warns.append(f"instrument/sample data overruns file by {pos - len(data):,} bytes")
    return {
        "kind": "xm", "modname": modname, "tracker": tracker, "version": version,
        "song_length": song_length, "restart": restart, "channels": channels,
        "num_patterns": num_patterns, "num_instruments": num_instruments,
        "flags": flags, "tempo": tempo, "bpm": bpm, "order": order,
        "patterns": patterns, "instruments": instruments, "warnings": warns,
    }


# S3M header flags (offset 0x26) and per-sample flags (offset 0x1F).
_S3M_FLAGS = [
    (0x08, "amiga_slides"), (0x10, "vol0_optimizations"),
    (0x20, "amiga_limits"), (0x40, "st3.0_volslides"),
    (0x80, "special_custom_data"),
]
_S3M_SAMPLE_FLAGS = [(0x01, "loop"), (0x02, "stereo"), (0x04, "16-bit")]
# cwt high nibble identifies the writer (feeds provenance later).
_S3M_WRITERS = {1: "ScreamTracker 3", 2: "Imago Orpheus", 3: "Impulse Tracker",
                4: "Schism Tracker", 5: "OpenMPT", 6: "BeRoTracker"}


def is_s3m(data):
    """True when offset 0x2C carries 'SCRM' and the type byte says module. Needs
    48 bytes; the 0x1A DOS-EOF byte at 0x1C is canonical but some writers zero
    it, so that is a warning in the parser, not a gate here."""
    return len(data) >= 48 and data[0x2C:0x30] == b"SCRM" and data[0x1D] == 16


def _s3m_channel_label(c):
    """Label for one channel-settings byte, or None if disabled/unused."""
    if c >= 128:
        return None                              # +128 disabled, 255 unused
    if c <= 7:
        return f"L{c + 1}"
    if c <= 15:
        return f"R{c - 7}"
    if c <= 24:
        return f"A{c - 15}"                       # adlib melody
    return f"D{c - 24}"                           # adlib drums


def _s3m_channel_map(channels):
    """(space-joined label string, active count) from the 32 channel bytes."""
    labels = [lab for c in channels if (lab := _s3m_channel_label(c))]
    return " ".join(labels), len(labels)


def parse_s3m(data):
    """Parse a ScreamTracker 3 S3M. Reads the header, the order table, the
    instrument and pattern parapointer tables (byte offset = value << 4, the
    format's segment-style quirk), and each 0x50-byte instrument header whose
    memseg field points at the PCM. Defensive like parse_it: every parapointer
    is bounds-checked, truncated tables warn and break, and an instrument header
    without an 'SCRS'/'SCRI' tag is marked invalid rather than trusted."""
    warns = []
    song_name = _c(data[0:28])
    if len(data) <= 0x1C or data[0x1C] != 0x1A:
        warns.append("missing 0x1A DOS-EOF marker at offset 0x1C")
    ordnum = struct.unpack_from("<H", data, 0x20)[0]
    insnum = struct.unpack_from("<H", data, 0x22)[0]
    patnum = struct.unpack_from("<H", data, 0x24)[0]
    flags = struct.unpack_from("<H", data, 0x26)[0]
    cwt = struct.unpack_from("<H", data, 0x28)[0]
    ffi = struct.unpack_from("<H", data, 0x2A)[0]
    gvol, speed, tempo, mvol = data[0x30], data[0x31], data[0x32], data[0x33]
    default_pan = data[0x35]
    channels = list(data[0x40:0x60])
    if ordnum % 2:
        # Scream Tracker itself pads the order list to an even count and never
        # writes an odd one: in 540 real files every odd count came from a
        # writer with a 0x3xxx id, which is Impulse Tracker exporting S3M.
        # So this is a fingerprint rather than a defect, and it says so.
        who = ("Impulse Tracker exporting S3M does this"
               if (cwt >> 12) == 3 else "Scream Tracker never writes one")
        warns.append(f"order count {ordnum} is odd; {who}")
    if ffi not in (1, 2):
        warns.append(f"sample format ffi={ffi} is not 1 (signed) or 2 (unsigned)")

    op = 0x60
    order = list(data[op:op + ordnum])
    op += ordnum

    def _paratable(base, count):
        out = []
        for i in range(count):
            o = base + i * 2
            if o + 2 > len(data):
                warns.append("parapointer table truncated")
                break
            out.append(struct.unpack_from("<H", data, o)[0])
        return out, base

    ins_para, ins_base = _paratable(op, insnum)
    pat_para, pat_base = _paratable(op + insnum * 2, patnum)

    samples = []
    for para in ins_para:
        hdr = para << 4
        if para == 0 or hdr + 0x50 > len(data):
            # a zero paragraph is an empty slot; a header past the end is not
            samples.append({"offset": hdr, "valid": False, "is_pcm": False,
                            "type": 0 if para == 0 else -1})
            continue
        tag = data[hdr + 0x4C:hdr + 0x50]
        stype = data[hdr]
        memseg = (data[hdr + 0x0D] << 16) | struct.unpack_from("<H", data, hdr + 0x0E)[0]
        length = struct.unpack_from("<I", data, hdr + 0x10)[0]
        sflags = data[hdr + 0x1F]
        bits16, stereo = bool(sflags & 0x04), bool(sflags & 0x02)
        low_len = length & 0xFFFF                # ST3 honors only the low 16 bits
        samples.append({
            "offset": hdr, "valid": tag in (b"SCRS", b"SCRI"), "type": stype,
            "is_pcm": stype == 1, "tag": tag.decode("latin-1", "replace"),
            "dos_name": _c(data[hdr + 1:hdr + 0x0D]),
            "name": _c(data[hdr + 0x30:hdr + 0x4C]),
            "memseg": memseg, "pcm_off": memseg << 4,
            "length": length, "low_len": low_len,
            "byte_len": low_len * (2 if bits16 else 1) * (2 if stereo else 1),
            "loop_beg": struct.unpack_from("<I", data, hdr + 0x14)[0],
            "loop_end": struct.unpack_from("<I", data, hdr + 0x18)[0],
            "vol": data[hdr + 0x1C], "packing": data[hdr + 0x1E], "flags": sflags,
            "c2spd": struct.unpack_from("<I", data, hdr + 0x20)[0] & 0xFFFF,
            "bits16": bits16, "stereo": stereo,
        })

    for i, s in enumerate(samples, 1):
        if not s.get("valid"):
            # An instrument of type 0 is an EMPTY slot -- the spec's own word
            # for it -- and carries no tag because there is nothing to tag.
            # Warning on it flagged 162 empty slots in the first 73 files of
            # a real archive as damage. Only a slot that claims a type and
            # then has no tag is worth saying anything about.
            if s.get("type", 0) == -1:
                warns.append(f"instrument {i} header lies past the end of "
                             f"the file")
            elif s.get("type", 0) != 0:
                warns.append(f"instrument {i} claims type {s['type']} but "
                             f"its header lacks an SCRS/SCRI tag")
        elif s["is_pcm"]:
            if s["packing"] == 1:
                warns.append(f"smp[{i}] packing=1 (ADPCM): not raw PCM, carve "
                             "will not yield playable data")
            if s["length"] >> 16:
                warns.append(f"smp[{i}] length high word 0x{s['length'] >> 16:04x} "
                             "set; ST3 reads only the low 16 bits")
            if s["pcm_off"] and s["pcm_off"] + s["byte_len"] > len(data):
                warns.append(f"smp[{i}] sample data @ 0x{s['pcm_off']:08x} runs past EOF")

    return {
        "kind": "s3m", "song_name": song_name, "ordnum": ordnum, "insnum": insnum,
        "patnum": patnum, "flags": flags, "cwt": cwt, "ffi": ffi, "gvol": gvol,
        "speed": speed, "tempo": tempo, "mvol": mvol, "default_pan": default_pan,
        "channels": channels, "order": order,
        "ins_para": ins_para, "ins_base": ins_base,
        "pat_para": pat_para, "pat_base": pat_base,
        "samples": samples, "warnings": warns,
    }


# IT header flags (offset 44) and per-sample flags (IMPS offset 18).
_IT_FLAGS = [
    (0x01, "stereo"), (0x04, "use_instruments"), (0x08, "linear_slides"),
    (0x10, "old_effects"), (0x20, "link_Gxx"), (0x40, "midi_pitch"),
    (0x80, "embedded_midi"),
]


def parse_it(data):
    """Parse an Impulse Tracker IT. Reads the on-disk offset tables
    (instrument/sample/pattern pointers) and each IMPS sample header, whose
    SamplePointer is an absolute file offset to the PCM."""
    warns = []
    # parse_it was the one tracker parser with no length guard. The fields at
    # 32..47 are read with unpack_from, which raises the struct.error every
    # caller already handles -- but gvol/mvol/speed/tempo at 48..51 are bare
    # indices, so a file of exactly 48-51 bytes passed every unpack and then
    # raised IndexError, which `extract` deliberately does NOT catch (its
    # _MALFORMED set treats IndexError as a bug in us, and it was). Raising the
    # short-read signal here converts it to the handled path for every caller.
    if len(data) < 192:                  # the fixed IT header, before the orders
        raise struct.error(
            f"IT header truncated: {len(data)} bytes, need at least 192")
    songname = _c(data[4:30])
    ordnum = struct.unpack_from("<H", data, 32)[0]
    insnum = struct.unpack_from("<H", data, 34)[0]
    smpnum = struct.unpack_from("<H", data, 36)[0]
    patnum = struct.unpack_from("<H", data, 38)[0]
    cwt = struct.unpack_from("<H", data, 40)[0]
    cmwt = struct.unpack_from("<H", data, 42)[0]
    flags = struct.unpack_from("<H", data, 44)[0]
    special = struct.unpack_from("<H", data, 46)[0]
    gvol = data[48]
    mvol = data[49]
    speed = data[50]
    tempo = data[51]

    op = 192
    order = list(data[op:op + ordnum])
    op += ordnum

    def _table(base, count):
        out = []
        for i in range(count):
            o = base + i * 4
            if o + 4 > len(data):
                warns.append("offset table truncated")
                break
            out.append(struct.unpack_from("<I", data, o)[0])
        return out, base

    ins_off, ins_base = _table(op, insnum)
    smp_off, smp_base = _table(op + insnum * 4, smpnum)
    pat_off, pat_base = _table(op + insnum * 4 + smpnum * 4, patnum)

    samples = []
    for so in smp_off:
        if so + 80 > len(data) or data[so:so + 4] != b"IMPS":
            samples.append({"offset": so, "valid": False})
            continue
        sflags = data[so + 18]
        length = struct.unpack_from("<I", data, so + 48)[0]  # in sample points
        c5 = struct.unpack_from("<I", data, so + 60)[0]
        dataptr = struct.unpack_from("<I", data, so + 72)[0]
        bits16 = bool(sflags & 0x02)
        stereo = bool(sflags & 0x04)
        compressed = bool(sflags & 0x08)
        byte_len = length * (2 if bits16 else 1) * (2 if stereo else 1)
        samples.append({
            "offset": so, "valid": True,
            "name": _c(data[so + 20:so + 46]),
            "dos_name": _c(data[so + 4:so + 16]),
            "length": length, "byte_len": byte_len, "c5_speed": c5,
            "data_off": dataptr, "bits16": bits16, "stereo": stereo,
            "compressed": compressed, "has_sample": bool(sflags & 0x01),
        })
    for s in samples:
        if s.get("valid") and s.get("data_off", 0) and s["data_off"] > len(data):
            warns.append(f"sample data pointer 0x{s['data_off']:08x} is past EOF")
    return {
        "kind": "it", "songname": songname, "ordnum": ordnum, "insnum": insnum,
        "smpnum": smpnum, "patnum": patnum, "cwt": cwt, "cmwt": cmwt,
        "flags": flags, "special": special, "gvol": gvol, "mvol": mvol,
        "speed": speed, "tempo": tempo, "order": order,
        "ins_off": ins_off, "ins_base": ins_base,
        "smp_off": smp_off, "smp_base": smp_base,
        "pat_off": pat_off, "pat_base": pat_base,
        "samples": samples, "warnings": warns,
    }


# Scream Tracker 2. The direct ancestor of S3M, which acidcat already reads,
# and simple enough that the whole header is fixed-offset: no pointer table,
# no variable-length anything before the instruments.
#
# The tracker name is the identity. "!Scream!" is Scream Tracker itself; the
# field exists because other programs wrote the format and said so here.
STM_HEADER = 48
STM_INSTRUMENT = 32
STM_INSTRUMENTS = 31
STM_ORDER_LEN = 128
# 65535 in a loop end means "no loop", the same sentinel the other trackers of
# the era use. Reading it as a length gives a sample 64 KB longer than itself.
STM_NO_LOOP = 65535


def is_stm(data):
    """True if the bytes open a Scream Tracker 2 module.

    Checked on the EOF marker and the file type as well as the tracker name,
    because the name is 8 free-form characters that several programs wrote
    their own thing into.
    """
    return (len(data) >= 32 and data[28] == 0x1A and data[29] in (1, 2)
            and all(32 <= c < 127 or c == 0 for c in data[20:28]))


def parse_stm(data):
    """Parse a Scream Tracker 2 STM. Returns a dict of the header, the
    instrument table, the order list and the resolved sample offsets."""
    warns = []
    song_name = _c(data[:20])
    tracker = _c(data[20:28])
    file_type = data[29]
    ver_major, ver_minor = data[30], data[31]
    tempo, num_patterns, gvol = data[32], data[33], data[34]

    instruments = []
    for i in range(STM_INSTRUMENTS):
        off = STM_HEADER + i * STM_INSTRUMENT
        if off + STM_INSTRUMENT > len(data):
            break
        r = data[off:off + STM_INSTRUMENT]
        length, loop_beg, loop_end = struct.unpack_from("<HHH", r, 16)
        instruments.append({
            "name": _c(r[:12]),
            "disk": r[13],
            "length": length,
            "loop_start": loop_beg,
            "loop_end": loop_end,
            "volume": r[22],
            "c2spd": struct.unpack_from("<H", r, 24)[0],
            "hdr_off": off,
        })

    order_off = STM_HEADER + STM_INSTRUMENTS * STM_INSTRUMENT
    order = list(data[order_off:order_off + STM_ORDER_LEN])
    # the order list is terminated by 99 (or 255 in later writers); anything
    # after it is padding rather than a pattern to play
    used = []
    for entry in order:
        if entry in (99, 255):
            break
        used.append(entry)

    pattern_off = order_off + STM_ORDER_LEN
    # ST2 patterns are a fixed 64 rows x 4 channels x 4 bytes = 1024 bytes
    pattern_bytes = num_patterns * 1024
    cur = pattern_off + pattern_bytes
    for ins in instruments:
        ins["offset"] = cur if ins["length"] else None
        cur += ins["length"]
    if cur > len(data):
        warns.append(f"sample data runs to {cur:,} but the file is "
                     f"{len(data):,} bytes")
    if used and max(used) >= num_patterns:
        warns.append(f"the order list plays pattern {max(used)} and the "
                     f"header declares {num_patterns}")
    return {
        "kind": "stm", "song_name": song_name, "tracker": tracker,
        "file_type": file_type, "version": f"{ver_major}.{ver_minor:02d}",
        "tempo": tempo, "num_patterns": num_patterns, "global_volume": gvol,
        "instruments": instruments, "order": used, "order_off": order_off,
        "pattern_off": pattern_off, "sample_data_off": pattern_off + pattern_bytes,
        "warnings": warns,
    }
