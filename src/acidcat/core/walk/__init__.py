"""Format walker registry: sniff the magic, dispatch to a walker.

``walk_file`` is the one entry point: it classifies the file through
core/sniff.py and routes to the format's walker module. Every walker
returns (chunks, file_warnings) in the shared chunk model documented in
walk/base.py.

To add a format: teach core/sniff.py its magic, write a walker module
in this package, and add one registry entry below.
"""

from acidcat.core import sniff as sniffmod
from acidcat.core.walk import (
    aiff, akai, bitwig, emu, flac, fxp, labx, midi, mp3, mp4, mpc, multisample,
    ncw, ni, ogg, rf64, rmid, rx2, serum, sf2, sigmf, tracker, vital, wav, wt,
)
from acidcat.core.walk.base import Unsupported

# format id (from core/sniff.py) -> (display label, walker). walkers are
# normalized to (filepath, deep); formats without a deep mode ignore it.
_WALKERS = {
    "wav": ("RIFF/WAVE", lambda path, deep: wav.inspect_wav(path)),
    "rf64": ("RF64/WAVE", lambda path, deep: rf64.inspect_rf64(path)),
    "aiff": ("IFF/AIFF", lambda path, deep: aiff.inspect_aiff(path, "AIFF")),
    "aifc": ("IFF/AIFC", lambda path, deep: aiff.inspect_aiff(path, "AIFC")),
    "midi": ("Standard MIDI File",
             lambda path, deep: midi.inspect_midi(path, deep=deep)),
    "rmid": ("RMID (RIFF/MIDI)",
             lambda path, deep: rmid.inspect_rmid(path, deep=deep)),
    "serum": ("Xfer Serum preset", lambda path, deep: serum.inspect_serum(path)),
    "fxp": ("VST FXP preset", lambda path, deep: fxp.inspect_fxp(path)),
    "rx2": ("ReCycle RX2", lambda path, deep: rx2.inspect_rx2(path)),
    "akp": ("Akai S5000/S6000 program", lambda path, deep: akai.inspect_akp(path)),
    "e4b": ("E-MU Emulator 4 / EOS bank", lambda path, deep: emu.inspect_emu(path)),
    "e5b": ("E-MU Emulator X / Proteus X bank",
            lambda path, deep: emu.inspect_emu(path)),
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
    file the walkers do not decode."""
    fmt = sniffmod.sniff(filepath)
    if fmt == "id3-wrapped":
        raise Unsupported("ID3 tag wraps a non-MP3 container; not supported")
    entry = _WALKERS.get(fmt)
    if entry is None:
        raise Unsupported("not a recognized audio/preset file (WAV, RF64, AIFF, "
                          "MIDI, Serum, Bitwig, Vital, NCW, SF2, MP4/M4A, Ogg, "
                          "Native Instruments, MP3, FLAC, a MOD/S3M/XM/IT "
                          "tracker module, or a SigMF/IQ capture)")
    label, walker = entry
    return (label, *walker(filepath, deep))
