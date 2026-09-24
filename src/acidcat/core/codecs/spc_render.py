"""Render an .spc to PCM by running it.

An .spc is the SNES sound unit frozen mid-tune: the SPC700's registers, its
64 KB of RAM with the music driver and the samples in it, and the DSP's
registers. There is no score to read; to hear it the driver has to run,
and the DSP has to turn what the driver writes into sound. This loads the
snapshot into the SPC700 (core/codecs/spc700.py) and the S-DSP
(core/codecs/sdsp.py), resumes the CPU where it stopped, and collects the
DSP's output: 32 kHz, signed 16-bit, stereo.

The snapshot does not hold the DSP's internal voice state, only its
registers, so every voice starts silent and the driver's next key-on
brings it in; a tune dumped mid-note starts a moment late. Every player
has the same limitation, because the file does not carry the information.
The echo ring is played from the RAM as dumped, not cleared first: what is
there is the echo the tune had when it was frozen.

Length: the ID666 tag's seconds and fade when it has them, else the
caller's default. The fade is applied as a linear ramp over its length.
"""

import array

from acidcat.core.codecs import sdsp
from acidcat.core.codecs.spc700 import SPC700, CYCLES_PER_SAMPLE
from acidcat.core.formats import spc as spcmod

SAMPLE_RATE = sdsp.SAMPLE_RATE
_CHUNK = 1024                                 # samples per CPU/DSP hand-off


class CannotRender(Exception):
    """The file is not a playable snapshot."""


def load(raw):
    """(cpu, dsp, header) ready to run."""
    if not spcmod.is_spc(raw):
        raise CannotRender("not an SPC file")
    if len(raw) < spcmod.DSP_AT + 128:
        raise CannotRender("the file ends before the DSP registers")
    h = spcmod.parse_header(raw)
    cpu = SPC700()
    dsp = sdsp.SDSP(cpu.ram)
    cpu.dsp = dsp
    extra = raw[spcmod.IPL_AT:spcmod.IPL_AT + 64] if len(raw) >= spcmod.IPL_AT + 64 else None
    cpu.load_snapshot(raw[spcmod.RAM_AT:spcmod.RAM_AT + spcmod.RAM],
                      h["pc"], h["a"], h["x"], h["y"], h["psw"], h["sp"],
                      extra_ram=extra)
    dsp.load(raw[spcmod.DSP_AT:spcmod.DSP_AT + 128])
    return cpu, dsp, h


def tagged_length(h):
    """(seconds, fade_ms) from the tag, or (None, None)."""
    t = h.get("tag") or {}
    secs = t.get("seconds")
    fade = t.get("fade_ms")
    return (secs if secs else None), (fade if fade else None)


def render(raw, seconds=None, fade_ms=None, default_seconds=120.0):
    """Return (pcm_bytes, info). PCM is signed 16-bit little-endian stereo
    at 32 kHz. `seconds`/`fade_ms` override the tag."""
    cpu, dsp, h = load(raw)
    tag_secs, tag_fade = tagged_length(h)
    play = seconds if seconds is not None else (tag_secs or default_seconds)
    fade = fade_ms if fade_ms is not None else (tag_fade or 0)
    total = int(round((play + fade / 1000.0) * SAMPLE_RATE))
    done = 0
    while done < total:
        n = min(_CHUNK, total - done)
        target = (dsp.samples + n) * CYCLES_PER_SAMPLE
        cpu.run(target)
        dsp.run_to(target)
        done = dsp.samples
    pcm = dsp.out
    del pcm[total * 2:]
    if fade:
        _fade(pcm, int(play * SAMPLE_RATE), total)
    t = h.get("tag") or {}
    info = {"sample_rate": SAMPLE_RATE, "channels": 2, "seconds": total / float(SAMPLE_RATE),
            "title": t.get("title", ""), "game": t.get("game", ""),
            "halted": cpu.halted}
    return pcm.tobytes(), info


def _fade(pcm, start, end):
    span = max(1, end - start)
    for i in range(start, end):
        k = (end - i) / span
        pcm[2 * i] = int(pcm[2 * i] * k)
        pcm[2 * i + 1] = int(pcm[2 * i + 1] * k)


def render_file(path, **kw):
    with open(path, "rb") as fh:
        return render(fh.read(), **kw)


def can_render(raw):
    try:
        if not spcmod.is_spc(raw):
            return False, "not an SPC file"
        if len(raw) < spcmod.DSP_AT + 128:
            return False, "the file ends before the DSP registers"
    except Exception:
        return False, "unreadable"
    return True, ""
