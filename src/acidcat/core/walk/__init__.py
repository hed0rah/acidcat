"""Format walker registry: sniff the magic, dispatch to a walker.

``walk_file`` is the one entry point: it classifies the file through
core/sniff.py and routes to the format's walker module. Every walker
returns (chunks, file_warnings) in the shared chunk model documented in
walk/base.py.

To add a format: teach core/sniff.py its magic, write a walker module
in this package, and add one registry entry below.
"""

import os

from acidcat.core import sniff as sniffmod
from acidcat.core.walk import (
    aiff, akai, amiga, bfdlac, bitwig, emu, flac, fxp, krz, labx, midi, mp3, mp4, mpc,
    multisample, ncw, ni, ogg, rf64, rmid, rx2, serum, sf2, sigmf, svx, tracker,
    vital, wav, wt,
)
from acidcat.core.walk.base import Unsupported

# format id (from core/sniff.py) -> (display label, walker). walkers are
# normalized to (filepath, deep); formats without a deep mode ignore it.
_WALKERS = {
    "wav": ("RIFF/WAVE", lambda path, deep: wav.inspect_wav(path)),
    "rf64": ("RF64/WAVE", lambda path, deep: rf64.inspect_rf64(path)),
    "aiff": ("IFF/AIFF", lambda path, deep: aiff.inspect_aiff(path, "AIFF")),
    "aifc": ("IFF/AIFC", lambda path, deep: aiff.inspect_aiff(path, "AIFC")),
    "8svx": ("IFF/8SVX", lambda path, deep: svx.inspect_8svx(path)),
    "bfdlac": ("BFD compressed audio", lambda path, deep: bfdlac.inspect_bfdlac(path)),
    "smus": ("IFF/SMUS (Sonix score)", lambda path, deep: amiga.inspect_smus(path)),
    "okt": ("Oktalyzer module", lambda path, deep: amiga.inspect_okt(path)),
    "med": ("MED / OctaMED module", lambda path, deep: amiga.inspect_med(path)),
    "fc": ("Future Composer chiptune", lambda path, deep: amiga.inspect_fc(path)),
    "midi": ("Standard MIDI File",
             lambda path, deep: midi.inspect_midi(path, deep=deep)),
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
    "wt": ("Bitwig wavetable", lambda path, deep: wt.inspect_wt(path)),
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
    "xm": ("FastTracker II XM", lambda path, deep: tracker.inspect_xm(path)),
    "it": ("Impulse Tracker", lambda path, deep: tracker.inspect_it(path)),
}


def walk_file(filepath, deep=False):
    """Sniff the magic and dispatch to the format walker.

    Returns (fmt_label, chunks, file_warns); raises Unsupported for a
    file the walkers do not decode. Any other exception out of a walker
    is a walker bug, and the "degrade with warnings, never raise"
    contract is enforced HERE, at the one boundary every consumer
    shares (inspect/od/audit/shape, the TUI, the public walk()): the
    walk degrades to zero chunks plus a walker-error warning instead of
    crashing on hostile input. ACIDCAT_WALKER_RAISE=1 (set by the test
    suite) re-raises so a walker bug stays a loud traceback in CI."""
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
            from acidcat.core import triage
            generic = triage.generic_walk(filepath)
        except Exception:
            generic = None
        if generic is not None:
            return generic
        raise Unsupported("not a recognized audio/preset file (WAV, RF64, AIFF, "
                          "MIDI, Serum, Bitwig, Vital, NCW, SF2, MP4/M4A, Ogg, "
                          "Native Instruments, MP3, FLAC, a MOD/S3M/XM/IT "
                          "tracker module, or a SigMF/IQ capture)")
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
    return (label, chunks, file_warns)
