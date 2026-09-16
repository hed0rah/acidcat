"""Format walker registry: sniff the magic, dispatch to a walker.

``walk_file`` is the one entry point: it classifies the file through
core/sniff.py and routes to the format's walker module. Every walker
returns (chunks, file_warnings) in the shared chunk model documented in
walk/base.py.

To add a format: teach core/sniff.py its magic, write a walker module
in this package, and add one registry entry below.
"""

import os
import tempfile

from acidcat.core.infra import geometry
from acidcat.core.infra import sniff as sniffmod
from acidcat.core.walk import (
    ableton, aiff, akai, albank, amiga, au, bfdlac, bitwig, caf, chiptune, containers, dmx,
    gf1pat, voc, emu, flac, fxp, krz, labx,
    mdx, midi, midi2, mp3,
    mp4, mpc, multisample, ncw, ni, ogg, pdx, pmd, psf, pt3, rf64, rmid, rx2, serum, sf2,
    sigmf, spc, svx, vgm,
    tracker,
    dsd, sid, streams, vital, wav, wave64, wt,
)
from acidcat.core.walk.base import Unsupported

# format id (from core/sniff.py) -> (display label, walker). walkers are
# normalized to (filepath, deep); formats without a deep mode ignore it.
# Formats `sniff` names that no walker reads, ON PURPOSE. A ROM or a disc image
# is not a chunk tree; what acidcat recovers from one is its samples, which is
# `extract`'s job. They are listed rather than inferred so the gap reads as a
# decision instead of an oversight, and a test asserts the list stays exactly
# the set of sniffable formats with no walker.
_EXTRACT_ONLY = {
    "n64rom": "an N64 ROM",
    "snesrom": "a SNES ROM",
    "wii": "a Wii disc image",
}

_WALKERS = {
    "wav": ("RIFF/WAVE", lambda path, deep: wav.inspect_wav(path)),
    "rf64": ("RF64/WAVE", lambda path, deep: rf64.inspect_rf64(path)),
    "w64": ("Wave64", lambda path, deep: wave64.inspect_wave64(path)),
    "dsf": ("Sony DSF (DSD)", lambda path, deep: dsd.inspect_dsf(path)),
    "dff": ("Philips DSDIFF (DSD)",
            lambda path, deep: dsd.inspect_dsdiff(path)),
    "caf": ("Apple Core Audio Format", lambda path, deep: caf.inspect_caf(path)),
    "aiff": ("IFF/AIFF", lambda path, deep: aiff.inspect_aiff(path, "AIFF")),
    "aifc": ("IFF/AIFC", lambda path, deep: aiff.inspect_aiff(path, "AIFC")),
    "8svx": ("IFF/8SVX", lambda path, deep: svx.inspect_8svx(path)),
    "bfdlac": ("BFD compressed audio", lambda path, deep: bfdlac.inspect_bfdlac(path)),
    "gf1pat": ("Gravis UltraSound patch", lambda path, deep: gf1pat.inspect_gf1pat(path)),
    "voc": ("Creative Voice File", lambda path, deep: voc.inspect_voc(path)),
    "au": ("Sun/NeXT audio", lambda path, deep: au.inspect_au(path)),
    "dmx": ("DMX sound (Doom lump)", lambda path, deep: dmx.inspect_dmx(path)),
    "smus": ("IFF/SMUS (Sonix score)", lambda path, deep: amiga.inspect_smus(path)),
    "okt": ("Oktalyzer module", lambda path, deep: amiga.inspect_okt(path)),
    "med": ("MED / OctaMED module", lambda path, deep: amiga.inspect_med(path)),
    "fc": ("Future Composer chiptune", lambda path, deep: amiga.inspect_fc(path)),
    "midi": ("Standard MIDI File",
             lambda path, deep: midi.inspect_midi(path, deep=deep)),
    "midi2": ("MIDI Clip File (MIDI 2.0 / UMP)",
              lambda path, deep: midi2.inspect_midi2(path, deep=deep)),
    "albank": ("N64 audio bank (.ctl / ALBankFile)",
               lambda path, deep: albank.inspect_albank(path, deep=deep)),
    "rmid": ("RMID (RIFF/MIDI)",
             lambda path, deep: rmid.inspect_rmid(path, deep=deep)),
    "serum": ("Xfer Serum preset", lambda path, deep: serum.inspect_serum(path)),
    "fxp": ("VST FXP preset", lambda path, deep: fxp.inspect_fxp(path)),
    "rx2": ("ReCycle RX2", lambda path, deep: rx2.inspect_rx2(path)),
    "akp": ("Akai S5000/S6000 program", lambda path, deep: akai.inspect_akp(path)),
    "e4b": ("E-MU Emulator 4 / EOS bank", lambda path, deep: emu.inspect_emu(path, deep)),
    "e5b": ("E-MU Emulator X / Proteus X bank",
            lambda path, deep: emu.inspect_emu(path, deep)),
    "krz": ("Kurzweil K2000/K2500/K2600 bank",
            lambda path, deep: krz.inspect_krz(path)),
    "asd": ("Ableton analysis sidecar",
            lambda path, deep: ableton.inspect_asd(path)),
    "als": ("Ableton Live Set",
            lambda path, deep: ableton.inspect_ableton_xml(path, "als")),
    "alc": ("Ableton Live Clip",
            lambda path, deep: ableton.inspect_ableton_xml(path, "alc")),
    "adg": ("Ableton device group / rack",
            lambda path, deep: ableton.inspect_ableton_xml(path, "adg")),
    "adv": ("Ableton device preset",
            lambda path, deep: ableton.inspect_ableton_xml(path, "adv")),
    "agr": ("Ableton groove",
            lambda path, deep: ableton.inspect_ableton_xml(path, "agr")),
    "amxd": ("Max for Live device", lambda path, deep: ableton.inspect_amxd(path)),
    # containers: they hold other things, so the walk describes what is inside
    # chiptune: the 6502 program that made the music, not the music
    "gbs": ("Game Boy Sound System",
            lambda path, deep: chiptune.inspect_gbs(path, deep=deep)),
    "hes": ("PC Engine HES",
            lambda path, deep: chiptune.inspect_hes(path, deep=deep)),
    "kss": ("MSX / Master System KSS",
            lambda path, deep: chiptune.inspect_kss(path, deep=deep)),
    "nsf": ("NES Sound Format",
            lambda path, deep: chiptune.inspect_nsf(path, deep)),
    "nsfe": ("NSF extended (chunked)",
             lambda path, deep: chiptune.inspect_nsfe(path, deep)),
    "sap": ("Slight Atari Player (POKEY)",
            lambda path, deep: chiptune.inspect_sap(path, deep)),
    "cdxa": ("raw CD sector image (CD-XA)",
             lambda path, deep: containers.inspect_cdxa(path, deep)),
    "cue": ("CUE sheet (CD track layout)",
            lambda path, deep: containers.inspect_cue(path, deep)),
    "gcm": ("GameCube disc image",
            lambda path, deep: containers.inspect_gcm(path, deep)),
    # console stream formats: header plus ADPCM, one module, shared vocabulary
    "adx": ("CRI ADX stream", lambda path, deep: streams.inspect_adx(path, deep)),
    "brstm": ("Nintendo BRSTM stream",
              lambda path, deep: streams.inspect_brstm(path, deep)),
    "hps": ("HAL PCM Stream (GameCube)",
            lambda path, deep: streams.inspect_hps(path, deep)),
    "vag": ("Sony VAG (SPU-ADPCM)", lambda path, deep: streams.inspect_vag(path, deep)),
    "mdx": ("Sharp X68000 MXDRV tune (MDX)",
            lambda path, deep: mdx.inspect_mdx(path, deep=deep)),
    "pdx": ("Sharp X68000 ADPCM sample bank (PDX)",
            lambda path, deep: pdx.inspect_pdx(path, deep=deep)),
    "psf": ("Portable Sound Format (PSF/PSF2/SSF/DSF/USF/GSF/SNSF/QSF)",
            lambda path, deep: psf.inspect_psf(path, deep=deep)),
    "spc": ("SNES SPC700 sound snapshot (SPC)",
            lambda path, deep: spc.inspect_spc(path, deep=deep)),
    "vgm": ("Video Game Music register log (VGM/VGZ)",
            lambda path, deep: vgm.inspect_vgm(path, deep=deep)),
    "pt3": ("ZX Spectrum ProTracker 3 / Vortex Tracker module (PT3)",
            lambda path, deep: pt3.inspect_pt3(path, deep=deep)),
    "pmd": ("PC-98 Professional Music Driver score (PMD)",
            lambda path, deep: pmd.inspect_pmd(path, deep=deep)),
    "s3p": ("Akai S1000/S3000 program (SysEx dump)",
            lambda path, deep: akai.inspect_s3p(path)),
    "sid": ("Commodore 64 SID tune (PSID/RSID)",
            lambda path, deep: sid.inspect_sid(path, deep=deep)),
    "wt": ("Surge/Bitwig wavetable", lambda path, deep: wt.inspect_wt(path)),
    "multisample": ("Bitwig multisample",
                    lambda path, deep: multisample.inspect_multisample(path)),
    "labx": ("Arturia Analog Lab bank", lambda path, deep: labx.inspect_labx(path)),
    "sigmf": ("SigMF recording",
              lambda path, deep: sigmf.inspect_sigmf(path, deep=deep)),
    "iq": ("Raw IQ capture", lambda path, deep: sigmf.inspect_iq(path, deep=deep)),
    "mpcpattern": ("Akai MPC pattern",
                   lambda path, deep: mpc.inspect_mpcpattern(path)),
    "xpm": ("Akai MPC program", lambda path, deep: mpc.inspect_xpm(path)),
    "xpn": ("Akai MPC expansion", lambda path, deep: mpc.inspect_xpn(path)),
    "xtd": ("Akai MPC track/kit", lambda path, deep: mpc.inspect_xtd(path)),
    "pgm": ("Akai MPC program", lambda path, deep: mpc.inspect_pgm(path)),
    "snd": ("Akai MPC2000 sound", lambda path, deep: mpc.inspect_snd(path)),
    "bitwig": ("Bitwig preset",
               lambda path, deep: bitwig.inspect_bitwig(path, deep=deep)),
    "ncw": ("NI Compressed Wave", lambda path, deep: ncw.inspect_ncw(path)),
    "sf2": ("SoundFont 2", lambda path, deep: sf2.inspect_sf2(path)),
    "vital": ("Vital preset",
              lambda path, deep: vital.inspect_vital(path, deep=deep)),
    "mp4": ("MP4/M4A", lambda path, deep: mp4.inspect_mp4(path)),
    "ni": ("Native Instruments preset",
           lambda path, deep: ni.inspect_ni(path, deep=deep)),
    "flac": ("FLAC", lambda path, deep: flac.inspect_flac(path)),
    "ogg": ("Ogg", lambda path, deep: ogg.inspect_ogg(path)),
    "mp3": ("MP3/MPEG audio",
            lambda path, deep: mp3.inspect_mp3(path, deep=deep)),
    "mod": ("ProTracker MOD", lambda path, deep: tracker.inspect_mod(path)),
    "s3m": ("ScreamTracker 3 S3M", lambda path, deep: tracker.inspect_s3m(path)),
    "stm": ("ScreamTracker 2 STM", lambda path, deep: tracker.inspect_stm(path)),
    "xm": ("FastTracker II XM", lambda path, deep: tracker.inspect_xm(path)),
    "it": ("Impulse Tracker", lambda path, deep: tracker.inspect_it(path)),
}


def walk_file(filepath, deep=False, fmt_override=None):
    """Sniff the magic and dispatch to the format walker.

    ``fmt_override`` forces a walker by format id, skipping the sniff -- the
    reverse-engineering case where you recognize a variant the sniffer does not
    (an old RIFF dialect, a vendor container built on a format we model).

    Returns (fmt_label, chunks, file_warns); raises Unsupported for a
    file the walkers do not decode. Any other exception out of a walker
    is a walker bug, and the "degrade with warnings, never raise"
    contract is enforced HERE, at the one boundary every consumer
    shares (inspect/od/audit/shape, the TUI, the public walk()): the
    walk degrades to zero chunks plus a walker-error warning instead of
    crashing on hostile input. ACIDCAT_WALKER_RAISE=1 (set by the test
    suite) re-raises so a walker bug stays a loud traceback in CI."""
    if fmt_override:
        # the caller says what this is. an old or odd variant of a format we do
        # model often parses fine once dispatch stops depending on the magic --
        # so a forced walker runs even when sniff disagrees, and its failures
        # degrade to warnings like any other walk.
        entry = _WALKERS.get(fmt_override)
        if entry is None:
            raise Unsupported(
                f"no walker for {fmt_override!r} "
                f"(known: {', '.join(sorted(_WALKERS))})")
        fmt = fmt_override
    else:
        fmt = sniffmod.sniff(filepath)
        if fmt == "id3-wrapped":
            raise Unsupported("ID3 tag wraps a non-MP3 container; not supported")
        entry = _WALKERS.get(fmt)
    if entry is None:
        # no specific walker: try generic structural triage before giving up, so
        # an unknown-but-chunked container (e.g. a proprietary audio format we
        # have not written a walker for) is still recognized and its chunk grid
        # surfaced, instead of a flat rejection.
        try:
            from acidcat.core.forensics import triage
            generic = triage.generic_walk(filepath)
        except Exception:
            generic = None
        if generic is not None:
            return _normalized(filepath, generic)
        # A format the sniffer NAMED has already been recognized, so saying it
        # was not is false about the tool's own state. Three of these exist on
        # purpose -- the ROM and disc images are extract-side, since what is
        # recoverable from them is samples rather than a chunk tree -- and
        # telling their owner the file is unrecognized sends them away from the
        # verb that would have worked.
        if fmt in _EXTRACT_ONLY:
            raise Unsupported(
                "%s is recognized but has no chunk structure to walk; "
                "run `acidcat extract` to recover its samples" % _EXTRACT_ONLY[fmt])
        if fmt:
            raise Unsupported(
                "recognized as %r, but no walker reads it; "
                "run `acidcat formats` for the %d that are read"
                % (fmt, len(_WALKERS)))
        # Naming the formats here was a list that could only go stale, and had:
        # it named fifteen while the tool walked fifty-seven, so it told anyone
        # who read it that half the supported formats were not supported.
        # `acidcat formats` prints the real set with its capabilities, and it
        # cannot drift because it is generated from the dispatch table below.
        raise Unsupported("not a recognized audio or preset file; "
                          "run `acidcat formats` for the %d it reads"
                          % len(_WALKERS))
    label, walker = entry
    try:
        chunks, file_warns = walker(filepath, deep)
    except Unsupported:
        raise
    except Exception as e:
        if os.environ.get("ACIDCAT_WALKER_RAISE"):
            raise
        return (label, [],
                [f"walker error ({fmt}): {e.__class__.__name__}: {e}"])
    try:
        return _normalized(filepath, (label, chunks, file_warns))
    except Exception as e:
        # normalization ran OUTSIDE the walker try above, so a geometry edge
        # a walker bug produced escaped the very boundary that promises to
        # contain walker bugs. Degrade keeping the walk (consumers fall back
        # to geometry.payload_of's default rule); CI still gets the traceback.
        if os.environ.get("ACIDCAT_WALKER_RAISE"):
            raise
        return (label, chunks,
                list(file_warns)
                + [f"geometry error ({fmt}): {e.__class__.__name__}: {e}"])


def walk_bytes(data, deep=False, fmt_override=None, suffix=".bin",
               scratch_dir=None):
    """Walk bytes rather than a path, by giving them a path.

    Every walker takes a filepath -- they open it, stat it, and mmap it -- so
    this writes a temp file and deletes it again. That is not free: measured at
    3,000 iterations over a small WAV, the write is 84.8% of each one, 756
    walks per second against 4,974 for the same walk on a file already on disk.

    It exists anyway, for two reasons. It puts that cost in ONE place, so a
    future bytes-capable walker path makes every caller faster at once instead
    of one caller at a time. And it makes fuzzing a call rather than a chore:
    the reason the differential fuzzer covered one format out of 52 was never
    that anyone chose WAV, it was that each new target meant writing the
    plumbing again.

    `suffix` matters: a few walkers consult the extension when the magic is
    ambiguous, so a fuzz harness should hand over the one its seed would really
    have.
    """
    fd, tmp = tempfile.mkstemp(prefix="acidcat_walk_", suffix=suffix,
                               dir=scratch_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return walk_file(tmp, deep=deep, fmt_override=fmt_override)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _normalized(filepath, walked):
    """Give every chunk leaving this boundary the same geometry vocabulary.

    Here rather than in each walker, and here rather than in each consumer,
    because this is already the one place every consumer shares -- the same
    reason the degrade-never-raise contract lives here. Walkers stay unchanged
    until they have something better to say than the default.
    """
    label, chunks, warns = walked
    try:
        size = os.path.getsize(filepath)
    except OSError:
        return walked
    geometry.normalize(chunks, size)
    return (label, chunks, warns)
