"""Tracker-module parsers: ProTracker MOD, FastTracker II XM, Impulse Tracker IT.

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


def parse_mod(data):
    """Parse a ProTracker MOD. Returns a dict: title, channels, magic,
    song_length, restart, order (list), num_patterns, pattern_data_off,
    samples (each with name/length/finetune/volume/loop + resolved offset),
    and warnings."""
    warns = []
    title = _c(data[:20])
    samples = []
    for i in range(31):
        off = 20 + i * 30
        length = struct.unpack_from(">H", data, off + 22)[0] * 2  # stored in words
        loop_start = struct.unpack_from(">H", data, off + 26)[0] * 2
        loop_len = struct.unpack_from(">H", data, off + 28)[0] * 2
        samples.append({
            "name": _c(data[off:off + 22]),
            "length": length,
            "finetune": data[off + 24] & 0x0F,
            "volume": data[off + 25],
            "loop_start": loop_start,
            "loop_len": loop_len,
            "hdr_off": off,
        })
    song_length = data[950]
    restart = data[951]
    order = list(data[952:952 + 128])
    magic = data[1080:1084]
    channels = _mod_channels(magic)
    num_patterns = (max(order) + 1) if order else 0
    pattern_data_off = 1084
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
