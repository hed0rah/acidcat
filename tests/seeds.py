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
    track = b"\x00\xff\x2f\x00"                      # end-of-track, and nothing else
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
