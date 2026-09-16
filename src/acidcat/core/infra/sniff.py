"""Canonical format detection by magic bytes.

One sniffing routine shared by the format walkers (and available to any
command), so the per-verb magic tables cannot drift apart. ``sniff_bytes``
classifies a 16-byte head; ``sniff`` reads the head from disk and also
resolves the one ambiguous case, an ID3v2 tag that wraps a non-MP3
container (some tools prepend ID3 tags to WAV/AIFF/FLAC files).

The check order is part of the contract: RIFF/WAVE must be tried before
the RIFF/NIKS preset magic, and the MP4 ftyp probe before the ID3
fallbacks, or edge-case files reroute. Do not reorder.

Every id this module can return is declared in ``KNOWN_FORMATS`` below -- the
canonical format-id namespace the rest of acidcat keys its dispatch tables on
(walk/_WALKERS, samples extractors, convert/repair). ``sniff`` returns one of
those ids or None. A test guards that the tables only use ids from this set (so a
typo'd key fails loudly, not as a silent dict-miss) and that the set stays in
sync with the ids sniff actually returns.

MOD has no leading signature (its magic is at offset 1080), so ``sniff``
confirms it from disk; ``sniff_bytes`` cannot classify a MOD from a head.
"""

from acidcat.core.codecs import ncw as ncwmod
from acidcat.core.formats import ableton as abletonmod
from acidcat.core.formats import akai as akaimod
from acidcat.core.formats import mdx as mdxmod
from acidcat.core.formats import pdx as pdxmod
from acidcat.core.formats import pmd as pmdmod
from acidcat.core.formats import psf as psfmod
from acidcat.core.formats import sid as sidmod
from acidcat.core.formats import dsd as dsdmod
from acidcat.core.formats import tracker as trackermod
from acidcat.core.formats import wave64 as wave64mod

# containers an ID3v2 tag is known to wrap; the tag then does not make
# the file an MP3.
_ID3_WRAPPED_MAGICS = (b"RIFF", b"RF64", b"FORM", b"fLaC", b"MThd")

# the canonical set of ids sniff/sniff_bytes can return. This is the source of
# truth every dispatch table keys on; keep it in sync with the returns below (the
# test suite asserts both directions). "id3-wrapped" is a sentinel, not a format.
KNOWN_FORMATS = frozenset({
    "8svx", "adg", "adv", "adx", "agr", "aifc", "aiff", "akp", "albank", "alc", "als", "amxd",
    "au",
    "asd", "bfdlac", "bitwig", "brstm",
    "caf", "cdxa", "cue", "e4b", "e5b", "fc", "flac", "fxp", "gbs", "gcm", "gf1pat", "hps",
    "id3-wrapped", "iq", "it", "krz", "labx", "med", "midi", "midi2", "mod",
    "mdx", "mp3", "mp4", "mpcpattern", "multisample", "n64rom", "ncw", "ni",
    "nsf", "nsfe", "ogg",
    "okt", "pdx", "pgm", "pmd", "psf", "pt3", "rf64", "rmid", "rx2", "s3m", "s3p", "sap", "serum", "sf2", "sigmf", "smus", "spc", "vgm",
    "dmx", "dff", "dsf", "sid", "stm", "snd", "snesrom", "vag", "vital", "voc", "w64", "wav", "wii", "wt", "xm", "xpm",
    "xpn", "xtd",
})

# audio container formats that carry a carvable/recoverable payload: format id ->
# (leading magic to sweep for, natural file extension for a carved region). The one
# definition `locate` (which magics to scan and which sniffed formats to accept)
# and `carve` (how to name a carved region) both read, so the two cannot drift.
# A hit is always re-confirmed with sniff_bytes, so the magic here is a coarse
# scan pattern, not the identification (RIFF and ID3 each cover several formats).
AUDIO_CONTAINERS = {
    "wav":  (b"RIFF", "wav"),
    "rf64": (b"RF64", "wav"),
    # a 16-byte GUID is the strongest scan pattern in this table -- four-byte
    # magics carry a real false-positive rate inside a disc image and this one
    # does not. Wave64 exists to hold files past 4 GB, so it is exactly the
    # container worth finding embedded in something larger.
    "w64":  (wave64mod.RIFF_GUID, "w64"),
    "caf":  (b"caff", "caf"),
    # the two DSD containers. FRM8 is IFF with 64-bit sizes, and the DSF
    # magic is four characters that also open its first chunk.
    "dff":  (b"FRM8", "dff"),
    "dsf":  (b"DSD ", "dsf"),
    "aiff": (b"FORM", "aiff"),
    "aifc": (b"FORM", "aiff"),
    "8svx": (b"FORM", "8svx"),
    "flac": (b"fLaC", "flac"),
    "ogg":  (b"OggS", "ogg"),
    "sf2":  (b"RIFF", "sf2"),
    "mp3":  (b"ID3",  "mp3"),
    # MIDI was missing, so `classify x.mid` said "midi, a format acidcat walks"
    # while `locate x.mid` said "0 regions" -- two format vocabularies behind
    # one pipeline, and the .mid in a disc image went unreported while a
    # raw-pcm blob was reported overlapping it. A sequencer's MIDI is often the
    # most recoverable thing on a music workstation's disk.
    "midi": (b"MThd", "mid"),
    "rmid": (b"RIFF", "mid"),
}

# distinct leading magics of the audio containers, in first-seen order (the scan
# patterns for locate's signature sweep); and the id set locate accepts.
# Chunk/block ids whose payload IS the sample data, as the walkers emit them.
# One definition, because more than one surface needs to answer "are these bytes
# audio": the TUI colours them and refuses to audition anything else without
# asking, and a caller reinterpreting arbitrary bytes as PCM wants the same
# answer. Structural, not statistical -- inside a walked container the chunk id
# is the ground truth, and no heuristic beats it.
AUDIO_PAYLOAD_IDS = frozenset({
    "data",     # RIFF/WAVE, RF64
    "SSND",     # AIFF/AIFC
    "BODY",     # IFF 8SVX
    "smpl",     # not audio itself, but sampler loop points over it
})
# `smpl` describes the audio rather than being it; kept separate so a caller can
# ask the strict question.
AUDIO_SAMPLE_IDS = AUDIO_PAYLOAD_IDS - {"smpl"}


AUDIO_CONTAINER_MAGICS = tuple(dict.fromkeys(m for m, _ext in AUDIO_CONTAINERS.values()))
AUDIO_CONTAINER_FMTS = frozenset(AUDIO_CONTAINERS)
AUDIO_CONTAINER_EXT = {fid: ext for fid, (_m, ext) in AUDIO_CONTAINERS.items()}


def sniff_bytes(head):
    """Classify the first bytes of a file (pass at least 20).

    20, not 16: the XM signature is the 17-byte "Extended Module: ", so a
    caller honouring a 16-byte contract would silently never detect XM.
    ``sniff`` itself reads 20 for exactly this reason. Widening a documented
    minimum after 1.0 would be a breaking change to a frozen contract, so it is
    stated correctly here.

    Magic-only: an ID3v2 tag classifies as "mp3" here; use ``sniff`` to
    distinguish a tag that wraps a different container.
    """
    if head[:3] == b"PSF" and len(head) >= 4 and head[3] in psfmod.VERSIONS:
        # three letters and a version byte naming one of eight machines.
        # Checked with the version: "PSF" opens ordinary text too.
        return "psf"                                    # Portable Sound Format
    if head[:4] == b"Vgm ":
        return "vgm"                                    # Video Game Music register log
    if head[:13] == b"ProTracker 3." or head[:17] == b"Vortex Tracker II":
        return "pt3"                                    # ZX Spectrum AY module
    if head[:20] == b"SNES-SPC700 Sound Fi":
        # the 33-byte magic runs past the 20-byte head; twenty of it is
        # already more signature than most formats have
        return "spc"                                    # SNES SPC700 snapshot
    if head[:3] == b"GBS" and len(head) >= 4 and head[3] == 1:
        # three letters and a version byte. Checked with the version because
        # "GBS" opens ordinary text too, and every real file is version 1.
        return "gbs"                                    # Game Boy Sound System
    if head[:5] == b"NESM\x1a":
        return "nsf"                                    # NES Sound Format (v1 and NSF2)
    if head[:4] == b"NSFE":
        return "nsfe"                                   # NSF extended, chunk-based
    if head[:5] == b"SAP\r\n":
        return "sap"                                    # Slight Atari Player (POKEY)
    if head[:12] == b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00":
        return "cdxa"                                   # raw CD sector image (Mode1/2/2352)
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"sfbk":
        return "sf2"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"RMID":
        return "rmid"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"APRG":
        return "akp"                                   # Akai S5000/S6000 program
    if head[:8] == akaimod.MAGIC:
        # The S1000/S3000 program a decade earlier: not a file layout but a
        # recording of the sampler's own SysEx dump.
        return "s3p"                                   # Akai S1000/S3000 program
    if head[4:15] == b"MPC1000 PGM":                   # Akai MPC1000/2500 program
        return "pgm"
    if len(head) >= 12 and head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff" if head[8:12] == b"AIFF" else "aifc"
    if len(head) >= 12 and head[:4] == b"FORM" and head[8:12] == b"E4B0":
        return "e4b"                                   # E-MU Emulator 4 / EOS bank
    if len(head) >= 12 and head[:4] == b"FORM" and head[8:12] == b"E5B0":
        return "e5b"                                   # E-MU Emulator X / Proteus X
    if len(head) >= 12 and head[:4] == b"FORM" and head[8:12] == b"8SVX":
        return "8svx"                                  # IFF 8-bit sampled voice (Amiga)
    if len(head) >= 12 and head[:4] == b"FORM" and head[8:12] == b"SMUS":
        return "smus"                                  # IFF Sonix musical score (Amiga)
    if head[:8] == b"OKTASONG":
        return "okt"                                   # Oktalyzer module (Amiga)
    if head[:4] in (b"MMD0", b"MMD1", b"MMD2", b"MMD3"):
        return "med"                                   # MED / OctaMED module (Amiga)
    if head[:4] in (b"SMOD", b"FC14"):
        return "fc"                                    # Future Composer chiptune (Amiga)
    if head[:4] == b"PRAM" or head[:4] == b"SROM":
        return "krz"                                   # Kurzweil K2000/K2500/K2600
    if head[:4] == b"BFDC":
        return "bfdlac"                                # FXpansion BFD compressed audio
    if head[:8] == b"GF1PATCH":
        return "gf1pat"                                # Gravis UltraSound GF1 patch
    if head[:20] == b"Creative Voice File":
        return "voc"                                   # Creative Voice File (Sound Blaster)
    if head[:4] == b".snd":
        return "au"                                    # Sun/NeXT audio (also .snd)
    if head[:4] == b"VAGp":
        return "vag"                                   # PS1 SPU-ADPCM sample
    if head[:8] == b" HALPST\x00":
        return "hps"                                   # HAL PCM Stream (GameCube DSP-ADPCM)
    if head[:4] == b"RSTM":
        return "brstm"                                 # Nintendo streamed audio (GameCube/Wii DSP-ADPCM)
    if head[:4] == b"caff":
        return "caf"                                   # Apple Core Audio Format
    if head[:6] == b'FILE "':
        return "cue"                                   # CUE sheet (CD-DA track layout)
    if head[:8] == b"SMF2CLIP":
        return "midi2"                                 # MIDI 2.0 clip file (a UMP stream)
    if len(head) >= 14 and head[:4] == b"MThd":
        return "midi"
    if len(head) >= 12 and head[:4] == b"RF64" and head[8:12] == b"WAVE":
        return "rf64"
    # Wave64's container id is a 16-byte GUID whose first four bytes are 'riff'
    # in LOWERCASE, so it never collides with the RIFF test below.
    if wave64mod.is_wave64(head):
        return "w64"
    # DSD, the two containers behind SACD. DSF is checked on its declared
    # header size as well as its magic, because 'DSD ' is four common
    # characters; DSDIFF is checked on its form type as well as FRM8, which is
    # the 64-bit IFF container rather than a format of its own.
    if dsdmod.is_dsf(head):
        return "dsf"
    if dsdmod.is_dsdiff(head):
        return "dff"
    if head[:8] == b"XferJson":
        return "serum"
    if head[:4] == b"vawt":
        return "wt"
    if head[:4] == b"BtWg":
        return "bitwig"
    if head[:4] == b"ampf":
        return "amxd"                                  # Max for Live device
    # 'PSID'/'RSID' plus a version of 1-4. The magic alone would do; the version
    # check costs two bytes and makes a false positive essentially impossible.
    if sidmod.looks_like_sid(head):
        return "sid"                                   # Commodore 64 SID tune
    # two magic bytes would be far too weak on their own; looks_like_asd also
    # requires the reserved u32 at offset 6 to be zero and a sane entry count.
    if abletonmod.looks_like_asd(head):
        return "asd"                                   # Ableton analysis sidecar
    if head[:4] == b"CcnK":
        return "fxp"
    if head[:4] == b"CAT " and head[8:12] == b"REX2":
        return "rx2"
    if head[:4] == ncwmod.MAGIC:
        return "ncw"
    if head[:1] == b"{":
        return "vital"
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[12:16] == b"hsin" or head[:4] == b"-in-" \
            or (head[:4] == b"RIFF" and head[8:12] == b"NIKS"):
        return "ni"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:17] == b"Extended Module: ":
        return "xm"
    if head[:4] == b"IMPM":
        return "it"
    if head[:3] == b"ID3":
        return "mp3"
    if len(head) >= 4:
        from acidcat.core.formats import mp3 as mp3mod
        if mp3mod.decode_frame_header(head[:4]) is not None:
            return "mp3"
    return None


_VITAL_WINDOW = 64 * 1024
_VITAL_KEY = b'"synth_version"'


def _is_vital(filepath):
    """True when a '{'-leading file really is a Vital preset.

    `synth_version` is the key the Vital parser itself requires -- 'settings'
    alone is too generic (core/formats/vital.py). Checking the same key here
    keeps the sniffer and the walker agreeing about what a Vital file is.

    Both ENDS are searched, and that is the whole subtlety. Vital serialises
    its JSON with keys in alphabetical order, so `settings` -- a wavetable and
    base64 blob that is routinely hundreds of KB -- always precedes
    `synth_version`, which lands about 24 bytes from EOF. Measured on 40 real
    presets: the key was at `filesize - 24` in every one, and files ran from
    170 KB to 3.2 MB. A head-only check finds it in none of them, which made
    the sniffer stricter than the parser and left `inspect` unable to reach
    any real preset.
    """
    try:
        with open(filepath, "rb") as fh:
            head = fh.read(_VITAL_WINDOW)
            if _VITAL_KEY in head:
                return True
            size = fh.seek(0, 2)
            if size <= _VITAL_WINDOW:
                return False                      # the head already was the file
            fh.seek(size - _VITAL_WINDOW)
            return _VITAL_KEY in fh.read(_VITAL_WINDOW)
    except OSError:
        return False


def _id3_wraps_other_container(filepath):
    """True when the leading ID3v2 tag is a wrapper around a different
    known container rather than the tag of an MPEG stream."""
    from acidcat.core.formats import mp3 as mp3mod
    hdr = mp3mod.read_id3v2(filepath)
    if not hdr:
        return False  # "ID3" magic but an unreadable header; treat as an MP3 attempt
    with open(filepath, "rb") as f:
        f.seek(hdr["total"])
        nxt = f.read(4)
    return nxt in _ID3_WRAPPED_MAGICS


def _second_frame_follows(filepath, head):
    """Corroborate a bare MPEG frame sync with the next frame.

    True when a second decodable header sits exactly ``frame_length`` bytes on,
    which is the cadence a real stream has and a chance byte pair does not. A
    single-frame file is vanishingly rare and losing it is a far better trade
    than calling every UTF-16 text file an MP3.
    """
    from acidcat.core.formats import mp3 as mp3mod
    first = mp3mod.decode_frame_header(head[:4])
    if not first:
        return False
    step = first.get("frame_length") or 0
    if step < 4:
        return False                       # free-format: no predictable cadence
    try:
        with open(filepath, "rb") as f:
            f.seek(step)
            nxt = f.read(4)
    except OSError:
        return False
    return len(nxt) == 4 and mp3mod.decode_frame_header(nxt) is not None


# How far past a zeroed head to look for the first frame. A zeroed region is a
# damaged or over-padded head, not a container, and a real one is under a
# sector or two; 64 KB is far above that.
_ZERO_HEAD_CAP = 64 * 1024


def _zeroed_head_mp3(filepath, head):
    """An MP3 whose leading bytes were zeroed, and nothing else.

    Files turn up that are ordinary MPEG audio behind a run of NUL bytes -- a
    head clobbered by a failed write, or a tag reserved and never filled. The
    audio is intact; acidcat was refusing the whole file because the frame sync
    is not at offset 0.

    The rule is deliberately narrow, because scanning forward for any sync
    would call half the disk an MP3:

      * every byte before the sync must be ZERO. Not "mostly zero", not
        "skippable" -- one non-zero byte and this is some other container whose
        magic acidcat does not know, and guessing is worse than saying so.
      * the sync must decode as a frame header.
      * a second header must sit exactly one frame length on, the same
        corroboration a bare sync at offset 0 has to pass.

    A file that passes all three is MPEG audio with a hole punched in front of
    it. Nothing else survives the combination.
    """
    from acidcat.core.formats import mp3 as mp3mod
    if not head or any(head[:4]):
        return False                       # a real head; not this case
    try:
        with open(filepath, "rb") as f:
            window = f.read(_ZERO_HEAD_CAP)
            start = next((i for i, b in enumerate(window) if b), -1)
            if start < 4 or start + 4 > len(window):
                return False
            first = mp3mod.decode_frame_header(window[start:start + 4])
            step = first and (first.get("frame_length") or 0)
            if not step or step < 4:
                return False
            f.seek(start + step)
            nxt = f.read(4)
    except OSError:
        return False
    return len(nxt) == 4 and mp3mod.decode_frame_header(nxt) is not None


def sniff(filepath):
    """Sniff a file on disk. Same ids as ``sniff_bytes`` plus
    "id3-wrapped" for an ID3v2 tag around a non-MP3 container."""
    with open(filepath, "rb") as f:
        head = f.read(20)  # 20 covers the 17-byte "Extended Module: " XM signature
    fmt = sniff_bytes(head)
    if fmt == "mp3" and head[:3] == b"ID3" and _id3_wraps_other_container(filepath):
        return "id3-wrapped"
    if fmt == "mp3" and head[:3] != b"ID3" and not _second_frame_follows(filepath, head):
        # A bare 4-byte frame sync is weak evidence: 0xFF 0xFE decodes as a
        # perfectly "valid" MPEG-1 Layer I header, so every UTF-16-LE text file
        # -- which opens with exactly that BOM -- was identified as an MP3.
        # A real stream has a second frame exactly frame_length away; one lucky
        # byte pair does not. Costs one 4-byte read, and only on this path.
        fmt = None
    # a .cue may open with REM/CATALOG lines before FILE; trust the extension
    if fmt is None and filepath.lower().endswith(".cue"):
        return "cue"
    # A Doom DS* sound has no magic at all -- eight bytes of header over raw
    # samples. `03 00` as a format field is not an identification on its own,
    # and the corroboration needs the FILE: the declared count plus the header
    # must equal its length exactly. sniff_bytes only ever sees the head, so
    # this cannot live there and be honest.
    if fmt is None:
        import os
        from acidcat.core.walk import dmx as dmxmod
        try:
            if dmxmod.looks_like_dmx(head, os.path.getsize(filepath)):
                return "dmx"                           # Doom DS* sound lump
        except OSError:
            pass
    # MDX has no magic at all: it opens with a Shift-JIS title. Identification
    # is whether the arithmetic lands -- a title terminator, a NUL-terminated
    # sample-bank name, then an offset table that resolves to 9 or 16 channels
    # with every offset inside the file.
    # The shared head is 20 bytes, which cannot reach an MDX offset table, so
    # this one reads the file itself -- the same shape as the gzipped-Ableton
    # check above.
    if fmt is None and mdxmod.looks_like_mdx_file(filepath):
        return "mdx"                                   # Sharp X68000 MXDRV tune
    # The sample bank an MDX plays its ADPCM channel from, and the same
    # problem: no magic, just a table of big-endian offset/length pairs whose
    # first entry has to land exactly where the table ends.
    if fmt is None and pdxmod.looks_like_pdx_file(filepath):
        return "pdx"                                   # Sharp X68000 ADPCM bank
    # PMD's identification is three bytes with no magic in them, so it goes
    # LAST among the content checks and only for the extensions its compiler
    # writes: a random file passes a three-byte test one time in a few
    # thousand, and this tree walks millions.
    if (fmt is None and pmdmod.is_pmd(head)
            and filepath.lower().endswith((".m", ".m2", ".m86", ".mz"))):
        return "pmd"                                   # PC-98 PMD score
    # every Ableton document except .asd and .amxd is gzipped XML, so the magic
    # is just gzip's. Identifying it needs one decompressed block, which is why
    # this lives here rather than in sniff_bytes.
    if fmt is None and head[:2] == b"\x1f\x8b":
        ab = abletonmod.sniff_gzip_ableton(filepath)
        # spelled out rather than returned straight through: KNOWN_FORMATS is
        # verified against the string literals in THIS file, so an id that only
        # exists in another module would silently escape that check
        if ab == "adg":
            return "adg"
        if ab == "agr":
            return "agr"
        if ab == "adv":
            return "adv"
        if ab == "alc":
            return "alc"
        if ab == "als":
            return "als"
    # ADX opens with 0x8000 (weak); confirm via the (c)CRI marker before the audio
    if fmt is None and head[:2] == b"\x80\x00":
        from acidcat.core.codecs import adx
        if adx.is_adx(filepath):
            return "adx"
    # disc images carry their magic past the sniff head: Wii at 0x18, GameCube at
    # 0x1C. Wii is checked first (its partitions are encrypted; distinct magic).
    if fmt is None:
        from acidcat.core.containers import wiidisc
        if wiidisc.is_wii(filepath):
            return "wii"
    if fmt is None:
        from acidcat.core.containers import gcm
        if gcm.is_gcm(filepath):
            return "gcm"
    # an N64 .ctl audio bank opens with the weak 2-byte 0x4231 ('B1') revision;
    # confirm structurally (a valid bank offset -> a bank with a sane sample rate)
    if fmt is None and _is_albank(filepath):
        return "albank"
    # an N64 ROM (z64/n64/v64) by its fixed magic word -- extract recovers its
    # VADPCM samples regardless of the game's bank format
    if fmt is None and head[:4] in (b"\x80\x37\x12\x40", b"\x37\x80\x40\x12", b"\x40\x12\x37\x80"):
        return "n64rom"
    # S3M's 'SCRM' magic sits at 0x2C (outside the head), a disk-level confirm.
    # It runs before the MOD check (MOD's offset-1080 heuristic can false-
    # positive inside S3M pattern data) and before the SNES ROM check, whose
    # checksum-and-complement test is a 1-in-65,536 coincidence that one real
    # S3M has already won.
    if fmt is None and _is_s3m(filepath):
        return "s3m"
    # a SNES ROM has no leading magic; its internal cartridge header (LoROM 0x7FC0
    # / HiROM 0xFFC0) carries a checksum + complement that xor to 0xFFFF. extract
    # recovers its BRR samples regardless of the game's sample table.
    if fmt is None and _is_snes_rom(filepath):
        return "snesrom"
    # a .sigmf-meta is JSON starting with '{', which sniff_bytes reads as vital;
    # the mandated extension reroutes it, exactly like the id3-wrapped demotion.
    if fmt == "vital" and filepath.lower().endswith(".sigmf-meta"):
        return "sigmf"
    # an MPC .mpcpattern is also bare JSON ('{'); reroute on its extension.
    if fmt == "vital" and filepath.lower().endswith(".mpcpattern"):
        return "mpcpattern"
    # a bare '{' is the weakest magic here: it claims every JSON file, and every
    # RTF, since those open "{\rtf". That stole real files -- an RTF licence
    # agreement in a sample pack classified as a walkable Vital preset, because
    # classify consults sniff before its own foreign-file table, so the
    # `{\rtf` entry it already had was never reached. Confirm from the file,
    # the same way a bare MP3 frame sync is confirmed by a second frame.
    if fmt == "vital" and not _is_vital(filepath):
        fmt = None
    # a ZIP whose archive holds multisample.xml is a Bitwig .multisample. This is
    # the one content-sniff that must peek inside the container (the local-file
    # header magic alone cannot tell it from any other zip).
    if fmt is None and head[:4] == b"PK\x03\x04" and _is_multisample(filepath):
        return "multisample"
    # an Arturia Analog Lab .labx is also a zip; its entries follow an
    # <Engine>/User|Factory/<Bank>/<Preset> layout of boost text archives. The
    # multisample check (an exact member name) is more specific, so it runs first.
    if fmt is None and head[:4] == b"PK\x03\x04" \
            and (filepath.lower().endswith(".labx") or _is_labx(filepath)):
        return "labx"
    # an Akai MPC .xpn expansion package is a zip carrying an Expansion.xml
    # manifest alongside its .xpm programs and samples.
    if fmt is None and head[:4] == b"PK\x03\x04" \
            and (filepath.lower().endswith(".xpn") or _is_xpn(filepath)):
        return "xpn"
    # an MPC3 .xtd track/kit is gzip wrapping an ACVS container; confirm the
    # ACVS magic inside rather than claiming every .xtd gzip.
    if fmt is None and head[:2] == b"\x1f\x8b" \
            and filepath.lower().endswith(".xtd") and _is_xtd(filepath):
        return "xtd"
    # a .vgz is a VGM in gzip and nothing else; confirmed by the magic
    # inside and not by the extension, since a .vgm.gz is the same thing
    if fmt is None and head[:2] == b"\x1f\x8b" and _is_vgz(filepath):
        return "vgm"
    # a free-format MPEG sync (bitrate index 0): sniff_bytes stays strict
    # because 16 bytes cannot confirm it; with the file in hand, accept only
    # when the constant frame length is measurable (a matching second sync).
    if fmt is None and len(head) >= 4 and _free_format_mp3(filepath, head):
        return "mp3"
    # MPEG audio behind a run of NUL bytes: the frame sync is not at offset 0,
    # so every magic test misses it and the file reads as unrecognized. Narrow
    # on purpose -- see _zeroed_head_mp3 for why each of its three conditions
    # is load-bearing.
    if fmt is None and _zeroed_head_mp3(filepath, head):
        return "mp3"
    # Scream Tracker 2, the format S3M grew out of. Checked after S3M because
    # SCRM at 0x2C is the stronger signal and an STM has no magic at all --
    # only an EOF marker, a file type and eight free-form characters.
    if fmt is None and _is_stm(filepath):
        return "stm"
    # SigMF pair members and bare IQ captures are headerless: accept them only
    # when no magic matched, keyed on the mandated / conventional extensions.
    if fmt is None:
        low = filepath.lower()
        if low.endswith(".sigmf-data") or low.endswith(".sigmf-meta"):
            return "sigmf"
        if low.endswith(_IQ_EXTS) or (low.endswith(".raw") and _gqrx_sniff(filepath)):
            return "iq"
        # an MPC .xpm program is XML; content-confirm to avoid the X11 pixmap
        # that shares the extension.
        if low.endswith(".xpm") and _is_mpc_program(filepath):
            return "xpm"
        # an older MPC2000 .pgm has no magic (a 17-byte sample-name-table record
        # at offset 2); the MPC1000 form is caught by magic in sniff_bytes.
        if low.endswith(".pgm") and _is_mpc2000_pgm(filepath):
            return "pgm"
        # an MPC2000 .snd sound starts 0x01 0x02 then a printable name, which
        # distinguishes it from a NeXT/Sun .snd (magic ".snd").
        if low.endswith(".snd") and _is_mpc_snd(filepath):
            return "snd"
    # ProTracker MOD has no leading signature; its only reliable magic sits at
    # offset 1080, so it can only be confirmed with the file in hand.
    if fmt is None and _is_mod(filepath):
        return "mod"
    return fmt


def _is_albank(filepath):
    """An N64 libultra .ctl: revision 0x4231, a small bankCount, and a first bank
    offset that lands on an ALBank whose sampleRate is a sane audio rate. The
    sample-rate gate cheaply rejects the many false 0x4231 hits in random data."""
    import os
    import struct
    try:
        with open(filepath, "rb") as f:
            head = f.read(65536)
    except OSError:
        return False
    if len(head) < 16 or head[:2] != b"\x42\x31":
        return False
    bank_count = struct.unpack_from(">h", head, 2)[0]
    if not (1 <= bank_count <= 64) or 4 + 4 * bank_count > len(head):
        return False
    b0 = struct.unpack_from(">I", head, 4)[0]
    if not (4 + 4 * bank_count <= b0 <= len(head) - 8):
        return False
    sample_rate = struct.unpack_from(">i", head, b0 + 4)[0]
    return 8000 <= sample_rate <= 48000


def _is_snes_rom(filepath):
    """A headerless SNES ROM. No leading magic, but the internal cartridge header
    at 0x7FC0 (LoROM) or 0xFFC0 (HiROM) ends with a 16-bit checksum and its
    complement that xor to 0xFFFF -- a 1-in-65536 gate. A 512-byte copier header
    shifts both locations by 0x200. The map-mode byte (0x20..0x3F) is a sanity
    check so a chance complement pair in non-ROM data is not mistaken for a cart."""
    import os
    try:
        size = os.path.getsize(filepath)
    except OSError:
        return False
    if size < 0x8000 or size > 0x800000:               # 32 KiB .. 8 MiB (SNES range)
        return False
    base = 0x200 if size % 0x400 == 0x200 else 0        # strip a 512-byte copier header
    with open(filepath, "rb") as f:
        for hdr in (0x7FC0, 0xFFC0):                    # LoROM, HiROM header locations
            f.seek(base + hdr)
            h = f.read(0x20)
            if len(h) < 0x20:
                continue
            mapmode = h[0x15]
            comp = h[0x1C] | (h[0x1D] << 8)
            chk = h[0x1E] | (h[0x1F] << 8)
            if chk and (comp ^ chk) == 0xFFFF and 0x20 <= mapmode <= 0x3F:
                return True
    return False


def _is_mod(filepath):
    """A ProTracker module by its magic at 1080, or a 15-instrument
    Soundtracker one by arithmetic: no magic, so the header's pattern count
    and sample lengths have to add up to the file's size."""
    import os
    from acidcat.core.formats import tracker as tkmod
    try:
        with open(filepath, "rb") as f:
            head = f.read(1084)
        return tkmod.is_mod(head) or tkmod.is_mod15(head, os.path.getsize(filepath))
    except OSError:
        return False


def _is_stm(filepath):
    """Scream Tracker 2, confirmed from disk.

    The identifying bytes sit at 28-31 and `head` in sniff() is twenty bytes,
    which is enough for the XM signature and not for this. A disk-level
    confirm, the same shape as the S3M one below.
    """
    try:
        with open(filepath, "rb") as f:
            return trackermod.is_stm(f.read(48))
    except OSError:
        return False


def _is_s3m(filepath):
    from acidcat.core.formats import tracker as tkmod
    try:
        with open(filepath, "rb") as f:
            return tkmod.is_s3m(f.read(48))
    except OSError:
        return False


# bare raw-IQ extensions (headerless): geometry comes from the extension itself.
_IQ_EXTS = (".cu8", ".c16", ".c8", ".cs8", ".cs16", ".cf32", ".cfile")


def _gqrx_sniff(filepath):
    from acidcat.core.walk import sigmf
    return sigmf._gqrx_name(filepath) is not None


def _is_mpc_program(filepath):
    """An MPC .xpm is XML with an <MPCVObject> root; distinguishes it from an
    X11 pixmap, which also uses .xpm."""
    try:
        with open(filepath, "rb") as f:
            return b"<MPCVObject" in f.read(512)
    except OSError:
        return False


def _is_mpc2000_pgm(filepath):
    """An MPC2000/2000XL .pgm: a 17-byte sample-name record at offset 2 (a
    printable name then a 0 at [18]). The MPC1000 form is caught by magic."""
    try:
        with open(filepath, "rb") as f:
            h = f.read(20)
    except OSError:
        return False
    return len(h) >= 19 and h[18] == 0 and 0x20 <= h[2] < 0x7f


def _is_mpc_snd(filepath):
    """An MPC2000 .snd sound: validity byte 1, a type byte < 5 (classic files
    use 4, some exporters 2), then a printable name -- not a NeXT/Sun .snd
    (which starts with the ASCII magic '.snd')."""
    try:
        with open(filepath, "rb") as f:
            h = f.read(3)
    except OSError:
        return False
    return len(h) >= 3 and h[0] == 1 and h[1] < 5 and 0x20 <= h[2] < 0x7f


def _free_format_mp3(filepath, head):
    from acidcat.core.formats import mp3 as mp3mod
    hdr = mp3mod.decode_frame_header(head[:4], allow_free=True)
    if hdr is None or not hdr.get("free_format"):
        return False
    import os
    end = min(os.path.getsize(filepath), 2 * mp3mod._FREE_SCAN_CAP)
    with open(filepath, "rb") as f:
        return mp3mod._free_frame_length(f, 0, hdr, end) is not None


def _is_multisample(filepath):
    try:
        import zipfile
        with zipfile.ZipFile(filepath) as z:
            return "multisample.xml" in z.namelist()
    except Exception:
        return False


def _is_labx(filepath):
    """A zip whose entries follow <Engine>/User|Factory/<Bank>/<Preset> and hold
    boost text-serialization archives (Arturia Analog Lab bank export)."""
    try:
        import zipfile
        with zipfile.ZipFile(filepath) as z:
            for n in z.namelist()[:8]:
                if len(n.split("/")) >= 3 and ("/User/" in n or "/Factory/" in n):
                    # open().read(40) inflates ~40 bytes; z.read(n)[:40]
                    # inflates the WHOLE member first, which let a small
                    # crafted zip cost hundreds of MB inside the sniffer,
                    # before any walker boundary
                    with z.open(n) as member:
                        head = member.read(40)
                    if head.split(b" ", 1)[-1].startswith(
                            b"serialization::archive"):
                        return True
    except Exception:
        pass
    return False


def _is_xpn(filepath):
    """A zip carrying an Expansion.xml manifest (Akai MPC expansion package)."""
    try:
        import zipfile
        with zipfile.ZipFile(filepath) as z:
            return "Expansion.xml" in z.namelist()
    except Exception:
        return False


def _is_vgz(filepath):
    """A gzip stream whose decompressed head is the Vgm magic."""
    import gzip
    try:
        with gzip.open(filepath, "rb") as g:
            return g.read(4) == b"Vgm "
    except Exception:
        return False


def _is_xtd(filepath):
    """A gzip stream whose decompressed head is the ACVS magic (MPC3 .xtd)."""
    import gzip
    try:
        with gzip.open(filepath, "rb") as g:
            return g.read(4) == b"ACVS"
    except Exception:
        return False
