"""One place that can build a valid specimen of a format.

Constructing an input was reinvented 56 times across this suite -- 46 of those
independently assembling a `WAVE` + `fmt ` header -- and that is the reason the
differential fuzzer covers exactly one of 52 registered walkers. A fuzzer is
only as wide as its ability to produce a seed, so scattering the seed builders
put a ceiling on it that had nothing to do with the fuzzing.

Each seed is deliberately MINIMAL and deliberately VALID: minimal so a mutation
has a good chance of landing somewhere structural rather than in a field of
padding, and valid because a fuzzer that starts from garbage tests the reject
path over and over and never reaches the parser.

Every seed here is checked against the sniffer in test_walker_fuzz.py, so a
builder that drifts out of shape fails loudly instead of quietly seeding the
sweep with bytes that walk as something else.
"""

import struct

_SAME = object()
SEEDS = {}


def seed(fmt, ext, sniffs_as=_SAME):
    """Register a builder under a name, and say what the sniffer should call it.

    `sniffs_as=None` is meaningful rather than missing: the unknown-container
    seed is supposed to be unrecognised, because the walker it exercises is the
    one that runs when nothing recognises anything.
    """
    def deco(fn):
        SEEDS[fmt] = (fn, ext, fmt if sniffs_as is _SAME else sniffs_as)
        return fn
    return deco


def _riff_chunk(cid, payload):
    body = cid + struct.pack("<I", len(payload)) + payload
    return body + (b"\x00" if len(payload) % 2 else b"")


def _iff_chunk(cid, payload):
    body = cid + struct.pack(">I", len(payload)) + payload
    return body + (b"\x00" if len(payload) % 2 else b"")


@seed("wav", ".wav")
def wav(channels=1, bits=16, rate=44100, frames=64):
    align = channels * bits // 8
    pcm = b"\x00" * (frames * align)
    body = (b"WAVE"
            + _riff_chunk(b"fmt ", struct.pack("<HHIIHH", 1, channels, rate,
                                               rate * align, align, bits))
            + _riff_chunk(b"data", pcm))
    return b"RIFF" + struct.pack("<I", len(body)) + body


@seed("aiff", ".aiff")
def aiff(channels=1, frames=441, bits=16):
    # 80-bit IEEE extended for 44100, the way AIFF stores a sample rate
    rate = b"\x40\x0e\xac\x44\x00\x00\x00\x00\x00\x00"
    body = (b"AIFF"
            + _iff_chunk(b"COMM", struct.pack(">hIh", channels, frames, bits) + rate)
            + _iff_chunk(b"SSND", struct.pack(">II", 0, 0) + b"\x00" * 32))
    return b"FORM" + struct.pack(">I", len(body)) + body


@seed("8svx", ".iff")
def svx(frames=64):
    body = (b"8SVX"
            + _iff_chunk(b"VHDR", struct.pack(">IIIHBBI", frames, 0, 32,
                                              8000, 1, 0, 0x10000))
            + _iff_chunk(b"BODY", b"\x01" * frames))
    return b"FORM" + struct.pack(">I", len(body)) + body


@seed("midi", ".mid")
def midi(division=96):
    """MThd, then one MTrk ending in the required end-of-track meta event.

    Carries a track name, a tempo and a scale rather than a bare terminator:
    the end-of-track-only version was 26 bytes, under the sweep's 64-byte
    floor, so it was registered and then never mutated once. Being seeded and
    being fuzzed are two claims and only the first was true here.
    """
    name = b"\x00\xff\x03\x04seed"                   # track name meta
    tempo = b"\x00\xff\x51\x03\x07\xa1\x20"          # 500000 us/qn = 120 bpm
    notes = b"".join(b"\x00\x90" + bytes([n, 0x40]) + b"\x30\x80" + bytes([n, 0x40])
                     for n in range(60, 72))
    track = name + tempo + notes + b"\x00\xff\x2f\x00"
    return (b"MThd" + struct.pack(">IHHH", 6, 0, 1, division)
            + b"MTrk" + struct.pack(">I", len(track)) + track)


@seed("flac", ".flac")
def flac(rate=44100, ch=2, bits=16, total=441):
    def blk(bt, payload, last=False):
        return (bytes([(0x80 if last else 0) | bt])
                + struct.pack(">I", len(payload))[1:] + payload)
    packed = (rate << 44) | ((ch - 1) << 41) | ((bits - 1) << 36) | total
    streaminfo = (struct.pack(">HH", 4096, 4096) + b"\x00\x00\x0e"
                  + b"\x00\x33\xa8" + struct.pack(">Q", packed) + b"\xab" * 16)
    return (b"fLaC" + blk(0, streaminfo)
            + blk(1, b"\x00" * 16, last=True)        # PADDING
            + b"\xff\xf8" + b"\x00" * 64)            # a frame-ish tail


@seed("ogg", ".ogg")
def ogg(serial=101, pages=3):
    def page(seq, *, bos=False, eos=False, body=b"\x11" * 512):
        htype = (0x02 if bos else 0) | (0x04 if eos else 0)
        segs, rest = [], len(body)
        while rest >= 255:
            segs.append(255)
            rest -= 255
        segs.append(rest)
        return (b"OggS" + bytes([0, htype]) + struct.pack("<q", seq * 1000)
                + struct.pack("<I", serial) + struct.pack("<I", seq)
                + struct.pack("<I", 0) + bytes([len(segs)]) + bytes(segs) + body)
    return b"".join(page(i, bos=(i == 0), eos=(i == pages - 1))
                    for i in range(pages))


@seed("unknown-container", ".bin", sniffs_as=None)
def unknown_container(magic=b"ZZZZ", n=4):
    """The shape `triage.generic_walk` claims: a tiling [tag][size] grid.

    Here because the walker that stands in for every format nobody has written
    a walker for deserves fuzzing more than most, not less -- it is the one
    that meets genuinely unknown bytes.
    """
    body = b"".join(b"ch%02d" % i + struct.pack(">I", 32) + b"\xaa" * 32
                    for i in range(n))
    return magic + struct.pack(">I", len(body)) + body


def build(fmt):
    """Bytes for one registered format."""
    return SEEDS[fmt][0]()


def suffix(fmt):
    """The extension to give a temp file, since some walkers sniff on it."""
    return SEEDS[fmt][1]


def sniffs_as(fmt):
    """What `sniff` should return for this seed, or None if nothing should."""
    return SEEDS[fmt][2]


# ── formats whose builders lived in one test module each ────────────
#
# Every one of these existed already, in the test file for its own walker,
# where only that file could reach it. Registering them here is what lets the
# CONTRACT tests -- the fuzz sweep, the geometry invariants -- see a format at
# all, and those run against a clone where `data/test_formats/` does not exist.
#
# That gap was the point. The sibling-overlap invariant gates on roughly five
# formats in CI and thirty-six on a machine that happens to hold the gitignored
# specimens, so the guarantee it offers is far weaker where it actually runs.
# A seed is committed code, so it is worth more to a contract test than a
# specimen nobody can distribute.


@seed("au", ".au")
def au(encoding=3, rate=44100, channels=1, frames=64):
    """Sun/NeXT: six big-endian words, then samples. 16-bit linear here."""
    pcm = b"\x00" * (frames * channels * 2)
    return (b".snd" + struct.pack(">IIIII", 24, len(pcm), encoding, rate, channels)
            + pcm)


@seed("nsf", ".nsf")
def nsf(songs=3, chips=0x00):
    """A 128-byte NES header and a little 6502 payload."""
    h = bytearray(0x80)
    h[0:5] = b"NESM\x1a"
    h[5], h[6], h[7] = 1, songs, 1
    struct.pack_into("<HHH", h, 8, 0x8000, 0x8003, 0x8006)
    h[0x0E:0x17] = b"Seed tune"
    struct.pack_into("<H", h, 0x6E, 16666)
    struct.pack_into("<H", h, 0x78, 20000)
    h[0x7B] = chips
    return bytes(h) + b"\xea" * 64


@seed("nsfe", ".nsfe")
def nsfe():
    """NSFe chunks: LENGTH first, THEN the FourCC. The reverse of RIFF."""
    def ch(fourcc, data):
        return struct.pack("<I", len(data)) + fourcc + data
    info = struct.pack("<HHH", 0x8000, 0x8003, 0x8006) + bytes([0, 0, 3, 0])
    return b"NSFE" + ch(b"INFO", info) + ch(b"DATA", b"\xea" * 32) + ch(b"NEND", b"")


@seed("sap", ".sap")
def sap():
    """A text header, then an Atari executable. The end address is INCLUSIVE."""
    head = ("SAP\r\n" + "\r\n".join([
        'AUTHOR "seed"', 'NAME "seed"', 'DATE "2026"',
        "TYPE B", "INIT 0F80", "PLAYER 247F", "TIME 00:01"]) + "\r\n")
    data = b"\xea" * 16
    body = b"\xff\xff" + struct.pack("<HH", 0x4000, 0x4000 + len(data) - 1) + data
    return head.encode("latin-1") + body


@seed("vag", ".vag")
def vag(rate=44100, blocks=8):
    v = bytearray(0x30)
    v[0:4] = b"VAGp"
    struct.pack_into(">I", v, 4, 0x20)
    struct.pack_into(">I", v, 0x0C, 16 * blocks)
    struct.pack_into(">I", v, 0x10, rate)
    v[0x20:0x24] = b"SEED"
    return bytes(v) + bytes(16 * blocks)


@seed("cdxa", ".cdxa")
def cdxa(sectors=4):
    """A raw CD image: no header at all, just 2352-byte sectors that each say
    what they are."""
    s = bytearray(2352)
    s[0:12] = b"\x00" + b"\xff" * 10 + b"\x00"
    s[15] = 2
    for base in (16, 20):
        s[base], s[base + 1], s[base + 2], s[base + 3] = 1, 0, 0x04, 0x01
    return bytes(s) * sectors


@seed("cue", ".cue", sniffs_as="cue")
def cue():
    return (b'FILE "SEED.bin" BINARY\r\n'
            b"  TRACK 01 AUDIO\r\n    INDEX 01 00:00:00\r\n"
            b"  TRACK 02 AUDIO\r\n    INDEX 01 00:02:00\r\n")


@seed("sid", ".sid")
def sid(songs=1, version=2):
    """PSID: a BIG-endian header on a little-endian 6502. The load address
    inside the C64 image is the little-endian one, which is the trap."""
    hlen = 0x7C if version >= 2 else 0x76
    h = bytearray(hlen)
    h[0:4] = b"PSID"
    struct.pack_into(">HHH", h, 4, version, hlen, 0)   # version, data, load=0
    struct.pack_into(">HH", h, 0x0A, 0x1000, 0x1003)   # init, play
    struct.pack_into(">HI", h, 0x0E, songs, 1)         # songs, start
    h[0x16:0x16 + 9] = b"Seed tune"
    h[0x36:0x36 + 4] = b"seed"
    h[0x56:0x56 + 4] = b"2026"
    # load==0 means the C64 image opens with its own little-endian load address
    return bytes(h) + struct.pack("<H", 0x1000) + b"\x60" * 64


@seed("mdx", ".mdx")
def mdx(channels=9):
    """X68000 MXDRV. No magic at all: a Shift-JIS title, then offsets that are
    relative to the position of the voice-offset WORD, not to the file."""
    title = "SEED".encode("shift_jis") + b"\x0d\x0a\x1a"
    pdx = b"\x00"                                       # no sample bank
    head = title + pdx
    # voice offset word, then one word per channel; first channel's data sits
    # immediately after the table, which is what makes the count derivable
    table = 2 * (channels + 1)
    voice_off = table + channels * 4
    words = struct.pack(">H", voice_off)
    for i in range(channels):
        words += struct.pack(">H", table + i * 4)
    body = words + (b"\xf1\x00\xff\x00" * channels) + b"\x00" * 32
    return head + body


@seed("s3p", ".s3p")
def s3p(keygroups=2, name="SEED PROG"):
    """Akai S1000/S3000 program: a recording of a SysEx dump, so the payload is
    nibble-split (low nibble first) and every block is 192 bytes on the wire
    for 150 bytes of content.

    Names are padded with the AKAI space code rather than with zeros, because
    zero is the code for the character "0" -- an all-zero name field decodes
    to "000000000000", which is what a lazy builder produces and no sampler
    ever wrote.
    """
    import acidcat.core.formats.akai as A
    SPACE = A.CHARSET.index(" ")

    def put_name(buf, at, text):
        for i in range(A.NAME_LEN):
            ch = text[i] if i < len(text) else " "
            buf[at + i] = A.CHARSET.index(ch)

    def frame(fn, sel, body):
        nib = bytearray()
        for v in body:
            nib += bytes([v & 0x0F, v >> 4])
        return (bytes([A.SYSEX_START, A.AKAI_ID, 0, fn, A.S1000_ID])
                + bytes(sel) + bytes(nib) + bytes([A.SYSEX_END]))

    prog = bytearray(A.BLOCK)
    prog[0] = 1                              # PRIDENT
    prog[1] = A.BLOCK_USED                   # KGRP1@, an internal address
    put_name(prog, 3, name)
    prog[19], prog[20] = 24, 127             # play range
    prog[42] = keygroups                     # GROUPS, the cross-check

    out = bytearray(A.MAGIC + struct.pack(">I", keygroups))
    pd = frame(A.PDATA, [0, 0], prog)
    out += struct.pack(">I", len(pd)) + pd
    for k in range(keygroups):
        kg = bytearray(A.BLOCK)
        kg[0] = 2                            # KGIDENT
        # the sampler's own range is 24-127, so many keygroups wrap rather
        # than running off the end of a byte
        lo = 24 + (k * 12) % 96
        kg[3], kg[4] = lo, min(lo + 11, 127)
        for z in range(A.ZONES):
            at = A.ZONE_AT + z * A.ZONE_LEN
            put_name(kg, at, "SEED SAMPLE" if z == 0 else "")
            kg[at + 13] = 127                # HIVEL
        kd = frame(A.KDATA, [0, k, 0], kg)
        out += struct.pack(">I", len(kd)) + kd
    return bytes(out)


@seed("pmd", ".m")
def pmd(title="SEED", composer="NOBODY"):
    """PC-98 PMD: a flag byte, then twelve little-endian words -- eleven
    part offsets and the rhythm table -- then the tone offset, all counted
    from byte 1. The memo anchor is the four bytes before the tones."""
    import acidcat.core.formats.pmd as P
    parts = []
    for i in range(P.PARTS):
        parts.append(bytes([0xF6, 0x00, 0x80]))          # a short stream
    body = b""
    offs = []
    pos = P.STREAM_START
    for stream in parts:
        offs.append(pos)
        body += stream
        pos += len(stream)
    rhythm_at = pos                                      # empty rhythm table
    # one 26-byte instrument (number 1, then 25 registers), then the 00 FF
    # terminator, then the memo strings, then the pointer table last --
    # which is the order every real file has
    tone_at = pos + 4
    tone = bytes([1]) + bytes(25) + P.TONE_END
    strings_at = tone_at + len(tone)
    strings = b""
    ptrs = []
    for text in (b"/", title.encode("shift_jis"), composer.encode("shift_jis"),
                 b"/"):
        ptrs.append(strings_at + len(strings))
        strings += text + bytes(1)
    memo_table_at = strings_at + len(strings)
    anchor = struct.pack("<H", memo_table_at) + bytes([0x40, 0xFE])
    table = struct.pack("<5H", *(ptrs + [0]))
    head = struct.pack("<12H", *(offs + [rhythm_at])) + struct.pack("<H", tone_at)
    return bytes([P.FLAG_PC98]) + head + body + anchor + tone + strings + table


@seed("pdx", ".pdx")
def pdx(samples=3, length=64):
    """X68000 ADPCM sample bank. No magic either: 96 slots of big-endian
    offset/length, and the first sample has to begin exactly where the table
    ends, which is what makes the bank count derivable."""
    import acidcat.core.formats.pdx as P
    table = bytearray(P.BANK)
    pos = P.BANK
    for i in range(samples):
        struct.pack_into(">II", table, i * P.SLOT, pos, length)
        pos += length
    return bytes(table) + b"\x88" * (length * samples)


@seed("adx", ".adx")
def adx(channels=1, rate=44100, frames=32):
    """CRI ADX. The copyright offset points two bytes BEFORE '(c)CRI', so the
    audio starts at that value plus four."""
    co = 0x2C
    h = bytearray(co + 2)
    h[0:2] = b"\x80\x00"
    struct.pack_into(">H", h, 2, co)
    h[4], h[5], h[6], h[7] = 3, 18, channels, 0
    struct.pack_into(">I", h, 8, rate)
    struct.pack_into(">I", h, 12, frames * 32)
    h[16], h[17] = 4, 0
    h[co - 2:co + 4] = b"(c)CRI"
    return bytes(h[:co - 2]) + b"(c)CRI" + b"\x00" * 18 * frames * channels


@seed("hps", ".hps")
def hps(channels=2, rate=32000, blocks=2):
    """HAL PCM Stream. The block chain names the NEXT block's file offset at
    +8; the sample count sits at +4, so reading the pointer from there walks
    into a number that is not an offset."""
    head = bytearray(0x80)
    head[0:8] = b" HALPST\x00"
    struct.pack_into(">II", head, 8, rate, channels)
    blk = 0x20
    out = bytearray(bytes(head))
    for i in range(blocks):
        off = 0x80 + i * (0x20 + blk * channels)
        nxt = -1 if i == blocks - 1 else off + 0x20 + blk * channels
        b = bytearray(0x20)
        struct.pack_into(">III", b, 0, blk * channels, blk * 14, nxt & 0xFFFFFFFF)
        out += b + bytes(blk * channels)
    return bytes(out)


# ── batch 2: minimal RIFF/IFF and no-magic formats ──────────────────
#
# Lifted from tests/make_format_corpus.py, which built and verified each of
# these (named, walked, swept) but wrote them to a gitignored directory, so a
# contract test in a clone never saw them. Registering them here is what lets
# the fuzz sweep and the geometry invariants reach these formats where they
# actually gate.

_SEED = 0x5EED


def _noise(n, seed=_SEED):
    """Deterministic filler. Not random: a corpus that changes between runs
    turns a reproducible failure into a flake."""
    out = bytearray(n)
    x = seed
    for i in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out[i] = (x >> 16) & 0xFF
    return bytes(out)


@seed("dmx", ".dmx")
def dmx():
    """Doom DS* lump: u16 format=3, u16 rate, u32 count, then unsigned 8-bit.
    No magic -- the count must equal the bytes after the header exactly, and
    that arithmetic is the identification."""
    pcm = bytes((0x80 + (i % 40) - 20) & 0xFF for i in range(2000))
    return struct.pack("<HHI", 3, 11025, len(pcm)) + pcm


@seed("voc", ".voc")
def voc():
    """Creative Voice: 20-byte magic, u16 header size, u16 version, u16 check,
    then blocks. Block 01 carries a time constant; the terminator is ONE byte."""
    hdr = b"Creative Voice File\x1a" + struct.pack("<HHH", 0x1A, 0x010A, 0x1129)
    pcm = _noise(1500)
    body = struct.pack("<B", 1) + struct.pack("<I", len(pcm) + 2)[:3]
    body += bytes([256 - 1000000 // 11025 & 0xFF, 0]) + pcm
    return hdr + body + b"\x00"


@seed("rf64", ".rf64")
def rf64():
    """RF64: RIFF with a 64-bit size escape. riff_size is -1 and the real sizes
    live in ds64, which is the whole point of the format."""
    pcm = _noise(2000)
    fmt = struct.pack("<HHIIHH", 1, 2, 48000, 48000 * 4, 4, 16)
    ds64 = struct.pack("<QQQI", 0, len(pcm), len(pcm) // 4, 0)
    body = (b"WAVE" + _riff_chunk(b"ds64", ds64) + _riff_chunk(b"fmt ", fmt)
            + b"data" + struct.pack("<I", 0xFFFFFFFF) + pcm)
    return b"RF64" + struct.pack("<I", 0xFFFFFFFF) + body


@seed("smus", ".smus")
def smus():
    """EA IFF SMUS score: FORM..SMUS with SHDR and one TRAK of note events. The
    TRAK carries the file over the sweep's 64-byte floor."""
    shdr = struct.pack(">HBB", 120, 1, 0)
    trak = b"".join(bytes([n, 0x40, 0x10]) for n in range(60, 72)) + b"\x81\x00\x00"
    body = (b"SMUS" + _iff_chunk(b"SHDR", shdr) + _iff_chunk(b"NAME", b"synthetic\x00")
            + _iff_chunk(b"TRAK", trak))
    return b"FORM" + struct.pack(">I", len(body)) + body


@seed("sf2", ".sf2")
def sf2():
    """SoundFont 2: RIFF..sfbk with the three required LISTs (INFO, sdta, pdta).
    Minimal, but the chunk skeleton is what a walker reads."""
    ifil = _riff_chunk(b"ifil", struct.pack("<HH", 2, 1))
    isng = _riff_chunk(b"isng", b"EMU8000\x00")
    inam = _riff_chunk(b"INAM", b"synthetic\x00")
    info = _riff_chunk(b"LIST", b"INFO" + ifil + isng + inam)
    sdta = _riff_chunk(b"LIST", b"sdta" + _riff_chunk(b"smpl", _noise(2000)))
    pdta = _riff_chunk(b"LIST", b"pdta"
                       + _riff_chunk(b"phdr", b"\x00" * 38 * 2)
                       + _riff_chunk(b"shdr", b"\x00" * 46 * 2))
    body = b"sfbk" + info + sdta + pdta
    return b"RIFF" + struct.pack("<I", len(body)) + body


@seed("rmid", ".rmid")
def rmid():
    """RIFF-wrapped MIDI: RIFF..RMID with the whole SMF in one data chunk. The
    reason a RIFF reader must check the form type rather than assume WAVE. The
    inner SMF carries a tempo and notes so the wrapped file clears the sweep's
    64-byte floor -- the shared `midi` seed is smaller."""
    thd = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96)
    tempo = b"\x00\xff\x51\x03\x07\xa1\x20"          # 500000 us/qn = 120 bpm
    notes = b"".join(b"\x00\x90" + bytes([n, 0x40]) + b"\x30\x80" + bytes([n, 0x40])
                     for n in range(60, 72))
    ev = tempo + notes + b"\x00\xff\x2f\x00"
    smf = thd + b"MTrk" + struct.pack(">I", len(ev)) + ev
    return b"RIFF" + struct.pack("<I", 4 + 8 + len(smf)) + b"RMID" \
        + _riff_chunk(b"data", smf)


# ── borrowed builders ───────────────────────────────────────────────
#
# These formats already have a faithful minimal builder in their walker's test
# module. The batch above LIFTED builders that make_format_corpus.py defined
# itself; these have no such inline copy to lift, so the only alternative to
# borrowing is a THIRD hand-written definition of a fiddly format (a tracker
# header, an MPC slot table), which is precisely the drift the seed registry
# exists to end. make_format_corpus._borrowed() makes the same call for the same
# reason and against the same builders. Imported lazily inside each seed, so
# seeds.py stays importable even where a test module cannot be, and the coupling
# is paid only when a seed is actually built.


def _call(module, fn, *args, **kwargs):
    import importlib
    return getattr(importlib.import_module(module), fn)(*args, **kwargs)


def _call_path(module, fn, *args, **kwargs):
    """For builders that write a file and hand back its path rather than bytes."""
    import pathlib
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix="acidcat-seed-")
    try:
        made = _call(module, fn, pathlib.Path(d), *args, **kwargs)
        with open(str(made), "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


@seed("mod", ".mod")
def mod():
    """Amiga ProTracker: 20-byte title, 31 sample headers, order table, then the
    `M.K.` tag that a signature sweep keys on, one pattern, one sample."""
    return _call("test_tracker", "_make_mod")


@seed("xm", ".xm")
def xm():
    """FastTracker II: `Extended Module: ` magic, a header whose declared size is
    the offset to the pattern data, one pattern and one 8-bit sample."""
    return _call("test_tracker", "_make_xm")


@seed("it", ".it")
def it():
    """Impulse Tracker: `IMPM`, sample/instrument offset tables that the walker
    follows. The builder returns (bytes, off, off) for its xref test; the seed
    wants only the bytes."""
    return _call("test_tracker", "_make_it")[0]


@seed("s3m", ".s3m")
def s3m():
    """ScreamTracker 3: the `SCRM` tag at offset 44, parapointer tables, the
    0x1A end-of-header marker."""
    return _call("test_s3m", "_make_s3m")


@seed("ncw", ".ncw")
def ncw():
    """Native Instruments compressed wave: header, per-channel block table, then
    the compressed blocks. One mono 8-bit block of 512 samples."""
    return _call("test_ncw", "make_ncw", 1, 16, 44100, [[0] * 512], bits=8)


@seed("albank", ".ctl")
def albank():
    """N64 libultra ALBank (.ctl): the instrument-bank half of the ctl/tbl pair,
    a tree of bank -> instrument -> sound offsets."""
    return _call("test_albank", "_make_ctl")


@seed("bfdlac", ".bfdlac")
def bfdlac():
    """BFD3 compressed audio (`BFDC`): a chunked container, here the `fmt ` and
    `indx` chunks a walker needs to describe it."""
    return _call("test_bfdlac", "_bfdc",
                 [_call("test_bfdlac", "_fmt"), _call("test_bfdlac", "_indx")])


@seed("krz", ".krz")
def krz():
    """Kurzweil K2000 bank: RIFF-like object tree plus a trailing headerless PCM
    region. One sample object over 200 bytes of PCM."""
    obj = _call("test_krz", "_object", 1, 1, "Samp", _call("test_krz", "_sample_body"))
    return _call("test_krz", "_bank", [obj], pcm=b"\x00\x00" * 100)


@seed("asd", ".asd")
def asd():
    """Ableton analysis sidecar: the 0x06 magic and TIFF byte-order mark, then a
    warp/loop grid. Built from a 2-second grid at 44100."""
    return _call("test_ableton", "build_asd", _call("test_ableton", "grid_for", 44100, 2.0))


@seed("akp", ".akp")
def akp():
    """Akai MPC/S-series program: RIFF-like `RIFF`/`APRG` with keygroup chunks."""
    return _call_path("test_akai", "_make_akp")


@seed("e4b", ".e4b")
def e4b():
    """E-mu E4 bank (`FORM`/`E4B0`): the Emulator IV preset/sample container."""
    return _call_path("test_emu", "_make_e4b")


@seed("e5b", ".exb")
def e5b():
    """E-mu E5000 bank (`FORM`/`E5B0`): the later variant of the E4 container."""
    return _call_path("test_emu", "_make_e5b")


@seed("xpn", ".xpn")
def xpn():
    """Akai MPC program (XPM/XPN family): the pad-and-sample layout an MPC saves
    alongside a kit."""
    return _call_path("test_mpc", "_make_xpn")


@seed("xtd", ".xtd")
def xtd():
    """Akai MPC expansion metadata: the kit-description sidecar."""
    return _call_path("test_mpc", "_make_xtd")


@seed("snd", ".snd")
def snd():
    """Akai MPC1000 sample (`.snd`): a headered PCM one-shot. Distinct from the
    Sun/NeXT `.au`, which shares the extension and is why sniff cannot go on the
    extension alone."""
    return _call_path("test_mpc", "_make_snd")


@seed("pgm", ".pgm")
def pgm():
    """Akai MPC1000 program: the pad-map with its slot table. The MPC1000 variant
    on purpose -- the MPC2000 builder makes a 36-byte file, under the sweep's
    mutation floor, and this covers the same walker."""
    return _call_path("test_mpc", "_make_pgm_mpc1000", ["Kick", "Snare"])


# ── batch 4: the gzipped-XML family, and more borrowed builders ─────
#
# make_format_corpus.py is itself a source of builders, not only a consumer of
# them: `_ableton_xml` and `_aiff` are defined there and nowhere else. Calling
# them keeps that one definition, the same way the borrowed block above calls
# the walker test modules.


def _call_into(module, fn, name, *args, **kwargs):
    """For builders handed a FILE path to write, rather than a directory."""
    import os
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix="acidcat-seed-")
    try:
        path = os.path.join(d, name)
        _call(module, fn, path, *args, **kwargs)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


# Every Ableton document but .asd and .amxd is gzip around an XML file whose
# root's FIRST CHILD names the type. There is no magic past gzip's own, so the
# five below are one builder and differ in exactly one tag -- and .alc differs
# from .als in nothing but the extension, which is the fragile half and the
# reason it is worth its own seed rather than being folded into .als.

@seed("adg", ".adg")
def adg():
    """Ableton device group / rack: root child `GroupDevicePreset`."""
    return _call("make_format_corpus", "_ableton_xml", "GroupDevicePreset")


@seed("adv", ".adv")
def adv():
    """Ableton device preset. `DeviceChainPreset` is not in the child map, so
    this also covers the fall-through the map ends in."""
    return _call("make_format_corpus", "_ableton_xml", "DeviceChainPreset")


@seed("agr", ".agr")
def agr():
    """Ableton groove: root child `Groove`."""
    return _call("make_format_corpus", "_ableton_xml", "Groove")


@seed("als", ".als")
def als():
    """Ableton Live Set: root child `LiveSet`."""
    return _call("make_format_corpus", "_ableton_xml", "LiveSet")


@seed("alc", ".alc")
def alc():
    """Ableton Live Clip: byte-for-byte the `als` document. The EXTENSION is the
    entire difference, so this seed is what proves the split survives mutation
    of the bytes it does not depend on."""
    return _call("make_format_corpus", "_ableton_xml", "LiveSet")


@seed("aifc", ".aifc")
def aifc():
    """FORM..AIFC: AIFF plus the FVER chunk and a compression id in COMM. Here
    the id is `NONE`, so the frames are ordinary PCM and only the container
    shape differs."""
    return _call("make_format_corpus", "_aiff", compressed=True)


@seed("labx", ".labx")
def labx():
    """Arturia Analog Lab bank: a STORED zip of Boost text-archive presets. The
    zip primitive under it is the one the `xpn` seed found raising a bare
    OSError, so this seed puts a second walker on that path."""
    return _call_into("test_labx", "_make_labx", "bank.labx")


@seed("vital", ".vital")
def vital():
    """Vital preset: bare JSON, no envelope. The walker's whole job is reading a
    document a tolerant JSON loader would accept without complaint."""
    return _call("test_vital", "_preset")


@seed("gf1pat", ".pat")
def gf1pat():
    """Gravis UltraSound GF1 patch: `GF1PATCH110`, then a header/instrument/
    layer/sample-header chain that must be walked in order to find the PCM."""
    return _call("test_gf1pat", "gf1_patch", b"\x80" * 512)


@seed("brstm", ".brstm")
def brstm():
    """Nintendo BRSTM: `RSTM` with HEAD/ADPC/DATA offset table. The block chain
    is declared in the header rather than linked, which is what makes a forged
    block count reachable."""
    return _call("test_brstm", "_brstm_file")


@seed("gcm", ".iso")
def gcm():
    """GameCube disc image: the magic sits at 0x1C, and the file system is an
    FST of fixed 12-byte entries plus a string table the entries index into."""
    return _call("test_gamecube", "_gcm_image", "HELLO.HPS", b"AUDIO")


@seed("midi2", ".midi2")
def midi2():
    """MIDI 2.0 clip file: `SMF2CLIP` then a self-delimiting big-endian UMP
    stream. Word count comes from the message type nibble, so a mutated nibble
    reframes every packet after it."""
    return _call("test_midi2", "_clip")


# ── batch 4, the rest: lifted rather than borrowed ──────────────────
#
# Everything below already had a specimen SOMEWHERE, but built inline inside a
# test body rather than in a function, so there was nothing to call. Lifting
# them here is the same trade batch 2 made against make_format_corpus.py: a
# second definition is a real cost, but it is the one that makes the contract
# tests reach the format at all, and seeds.py is the copy the rest of the suite
# can now call instead of writing a third. Where a real function did exist --
# `_mp3`, the mp4 box helpers, the MPC fixtures -- it is called, not copied.


def _const(module, name):
    """For a fixture that is a module-level CONSTANT rather than a builder."""
    import importlib
    return getattr(importlib.import_module(module), name)


def _be_chunk(cid, payload):
    """A big-endian IFF chunk, padded to even. The pad is not decoration: the
    RX2 walker advances by `clen + (clen & 1)`, so a builder that omits it
    produces a file that genuinely is malformed and reads as a walker bug."""
    return (cid + struct.pack(">I", len(payload)) + payload
            + (b"\x00" if len(payload) % 2 else b""))


@seed("okt", ".okt")
def okt():
    """Oktalyzer: `OKTASONG` then big-endian chunks. CMOD's four words are
    channel split flags, so the voice count is derived, not stored."""
    cmod = _be_chunk(b"CMOD", struct.pack(">HHHH", 1, 0, 1, 0))
    entry = b"kick".ljust(20, b"\x00") + b"\x00" * 12
    samp = _be_chunk(b"SAMP", entry + b"snare".ljust(20, b"\x00") + b"\x00" * 12)
    return b"OKTASONG" + cmod + samp


@seed("med", ".med")
def med():
    """MED / OctaMED: `MMD0`-`MMD3` and a u32 modlen that must agree with the
    file size, which is the only structural check the format offers."""
    body = b"\x00" * 100
    return b"MMD1" + struct.pack(">I", len(body)) + body


@seed("fc", ".fc")
def fc():
    """Future Composer: `SMOD` (v1.3) or `FC14` (v1.4), then a length. The
    magic is the version, so the two are one walker and two constants."""
    body = b"\x00" * 100
    return b"SMOD" + struct.pack(">I", len(body)) + body


@seed("amxd", ".amxd")
def amxd():
    """Max for Live device: `ampf`, then a constant `aaaa` marker the chunk
    chain starts AFTER. Reading that marker as a chunk id turns its next four
    bytes into a 1.6 GB length, which is how the walker first failed on a real
    device -- so the marker is the part of this seed that matters."""
    return (b"ampf" + struct.pack("<I", 4) + b"aaaa"
            + b"meta" + struct.pack("<I", 4) + struct.pack("<I", 7)
            + b"ptch" + struct.pack("<I", 64) + b"mx@c"
            + b'{"a":1}'.ljust(60, b" "))


@seed("fxp", ".fxp")
def fxp():
    """VST2 preset: `CcnK`, then `FPCh` for the opaque-chunk form, a four-byte
    plugin id and a 28-byte fixed-width name."""
    name = b"Seed Preset".ljust(28, b"\x00")
    return (b"CcnK" + struct.pack(">I", 100) + b"FPCh" + struct.pack(">I", 1)
            + b"XfsX" + struct.pack(">I", 1) + struct.pack(">I", 1) + name
            + struct.pack(">I", 8) + bytes(8))


@seed("rx2", ".rx2")
def rx2(slices=6):
    """ReCycle RX2: a `CAT `/`REX2` IFF group whose slice markers live in a
    NESTED `CAT `/`SLCL` group, so a flat top-level walk counts zero of them."""
    inner = b"SLCL" + _be_chunk(b"SLCE", b"") * slices
    slcl = b"CAT " + struct.pack(">I", len(inner)) + inner
    body = (b"REX2" + _be_chunk(b"CREI", b"ReCycle Seed Loop")
            + _be_chunk(b"GLOB", b"\x00" * 8) + slcl)
    return b"CAT " + struct.pack(">I", len(body)) + body


@seed("wt", ".wt")
def wt(frame_samples=256, frames=3):
    """Surge/Bitwig wavetable: `vawt`, sample count, frame count, flags. Flags
    0x0C is int16 at full scale -- and is also decimal 12, which is why that
    word read convincingly as a data offset for as long as it did."""
    return (b"vawt" + struct.pack("<IHH", frame_samples, frames, 0x0C)
            + bytes(frames * frame_samples * 2))


@seed("multisample", ".multisample")
def multisample():
    """Bitwig .multisample: a zip of an XML manifest plus the member samples.
    Third walker on the zip primitive the `xpn` seed found raising OSError."""
    import io
    import zipfile
    xml = ('<?xml version="1.0"?><multisample name="Seed">'
           '<generator>seeds</generator><category>Drums</category>'
           '<sample file="a.wav"><key root="36" low="36" high="40"/>'
           '<loop mode="off"/></sample>'
           '<sample file="b.wav"><key root="48" low="41" high="52"/></sample>'
           '</multisample>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("multisample.xml", xml)
        z.writestr("a.wav", b"RIFF____WAVE")
        z.writestr("b.wav", b"RIFF____WAVE")
    return buf.getvalue()


@seed("serum", ".fxp")
def serum():
    """Xfer Serum: `XferJson` then a JSON document. The extension is .fxp, which
    the `fxp` seed also claims -- the magic is what separates them, so the pair
    is what proves the sniffer reads bytes rather than the filename."""
    import json
    meta = json.dumps({"presetName": "Seed", "author": "seeds"}).encode()
    return b"XferJson" + meta + b"\x00" * 64


@seed("bitwig", ".bwpreset")
def bitwig():
    """Bitwig preset: `BtWg` and a ten-byte ASCII version, then a token stream."""
    return b"BtWg" + b"0003000200" + b"\x00" * 8 + b"seed" * 16


@seed("ni", ".nksf")
def ni():
    """Native Instruments preset: RIFF whose form is `NIKS`, with the metadata
    in a MessagePack map inside NISI. Deliberately over the sweep floor -- the
    obvious minimal version is 36 bytes and would never be mutated."""
    nisi = struct.pack("<I", 1) + (b"\x83\xa4name\xa4Seed\xa7product\xa5Seeds"
                                   b"\xa7version\xa71.0.0.0")
    body = (b"NIKS" + b"NISI" + struct.pack("<I", len(nisi)) + nisi
            + (b"\x00" if len(nisi) & 1 else b""))
    return b"RIFF" + struct.pack("<I", len(body)) + body


@seed("xpm", ".xpm")
def xpm():
    """Akai MPC keygroup program: XML, and content-confirmed rather than sniffed
    on the extension, because an X11 pixmap is also .xpm."""
    return _const("test_mpc", "_XPM").encode()


@seed("mpcpattern", ".mpcpattern")
def mpcpattern():
    """Akai MPC pattern: bare JSON, so sniff_bytes reads it as `vital` and the
    mandated extension reroutes it -- the same demotion the SigMF pair uses."""
    import json
    events = [{"type": 257, "time": 0, "len": 0, "1": 131},
              _call("test_mpc", "_mpc2_note", 38, 0.157, 268),
              _call("test_mpc", "_mpc2_note", 42, 1.0, 100)]
    return json.dumps({"pattern": {"length": 2 ** 63 - 1,
                                   "events": events}}).encode()


@seed("mp3", ".mp3")
def mp3(frames=12):
    """Constant-bitrate MPEG-1 Layer III, 128 kbps 44.1 kHz, no ID3. Frame
    length comes from the header, so the stream is self-describing and a
    mutated header desynchronizes everything after it."""
    return _call("test_framescan_prefilter", "_mp3", frames)


@seed("mp4", ".m4a")
def mp4():
    """MP4/M4A: the ftyp/moov/trak/mdia/minf/stbl/stsd spine down to one mp4a
    sample entry with its esds. Nested length-prefixed boxes all the way down,
    which is what makes one bad length reframe the rest of the file."""
    box = lambda t, p: _call("test_mp4", "_box", t, p)          # noqa: E731
    entry = _call("test_mp4", "_audio_entry", b"mp4a", esds_asc=b"\x12\x10")
    stbl = box(b"stbl", _call("test_mp4", "_stsd", entry))
    return box(b"ftyp", b"M4A \x00\x00\x00\x00") + box(b"moov", box(
        b"trak", box(b"mdia", box(b"minf", stbl))))


@seed("sigmf", ".sigmf-meta")
def sigmf():
    """SigMF: the JSON half of the mandated .sigmf-meta / .sigmf-data pair.
    Also bare JSON, so like .mpcpattern it is the extension that reroutes it
    away from `vital`. Seeded alone on purpose -- the walker meeting its
    sidecar's absence is a case a paired specimen would never reach."""
    import json
    return json.dumps({
        "global": {"core:datatype": "cf32_le", "core:sample_rate": 8000,
                   "core:version": "1.0.0"},
        "captures": [{"core:sample_start": 0, "core:frequency": 100000000}],
        "annotations": [{"core:sample_start": 0, "core:sample_count": 100}],
    }).encode()


@seed("caf", ".caf")
def caf():
    """Apple Core Audio Format: big-endian, SIGNED 64-bit payload-only sizes,
    and no alignment at all. Carries an `info` chunk as well as desc and data,
    so a mutation has a string table to land in and not only fixed fields."""
    import struct as _s
    info = _call("test_caf", "_chunk", b"info",
                 _s.pack(">I", 2) + b"artist\x00seed\x00tempo\x00120\x00")
    return _call("test_caf", "_make_caf", None, info)


@seed("w64", ".w64")
def w64():
    """Sony Wave64: GUID chunk ids, u64 sizes that count their own 24-byte
    header, and 8-byte alignment. Carries a `fact` chunk so the seed has an
    INTERIOR chunk to pad -- the final chunk is unpadded, so a seed of only
    fmt+data would never exercise the alignment the format turns on."""
    return _call("test_wave64", "_make_w64", 64, 1, 16, 44100, b"\x01\x02\x03")


@seed("stm", ".stm")
def stm():
    """Scream Tracker 2: fixed-offset throughout, so there is no pointer to
    corrupt -- the interesting mutations are the ones that make a declared
    sample longer than the file, which is the check the walker exists for."""
    return _call("test_tracker", "_stm",
                 instruments={0: {"name": b"seed", "length": 64}})


@seed("dsf", ".dsf")
def dsf():
    """Sony DSF: little-endian, flat, and ONE BIT per sample. The seed carries
    an ID3v2 tag because the metadata block is a pointer to one at the end of
    the file -- a seed without it would never exercise the offset."""
    import struct as _s
    body = b"TIT2" + _s.pack(">I", 4) + bytes([0, 0]) + bytes([0]) + b"abc"
    tag = b"ID3" + bytes([3, 0, 0]) + bytes([0, 0, 0, 0x7F]) + body
    return _call("test_dsd", "make_dsf", tag=tag)


@seed("dff", ".dff")
def dff():
    """Philips DSDIFF: IFF with 64-bit big-endian sizes and the even-length
    pad kept. The seed carries an ODD-sized trailing chunk so the pad rule is
    exercised -- without one, a walk that drops the pad still passes."""
    return _call("test_dsd", "make_dff",
                 extra=_call("test_dsd", "_bchunk", b"COMT", b"x" * 7))


@seed("iq", ".cu8")
def iq():
    """A bare IQ capture: no header at all, interleaved unsigned 8-bit I/Q. The
    extension carries the sample format, which is the whole identification."""
    return bytes(range(256)) * 8
