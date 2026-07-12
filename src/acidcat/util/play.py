"""Optional audio playback: hear a byte region as raw PCM.

Shells out to ffplay (ships with ffmpeg) so the core keeps its zero-dependency
promise; on Windows without ffplay, falls back to the built-in SoundPlayer. The
TUI uses this to audition a data/SSND chunk -- reinterpreting any byte range as
PCM, so you can literally hear a header or a cavity. No hard dependency:
have_audio() is False when nothing can play and callers degrade gracefully.
"""
import os
import shutil
import struct
import subprocess
import tempfile


def _ffplay():
    return shutil.which("ffplay")


def have_audio():
    """True if some player is available (ffplay, or SoundPlayer on Windows)."""
    return _ffplay() is not None or os.name == "nt"


def _wav_wrap(pcm, rate, ch, bits, tag):
    block = max(1, ch * bits // 8)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, tag, ch, rate,
                                    rate * block, block, bits)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def play_bytes(data, rate=44100, ch=1, bits=16, floating=False):
    """Play raw bytes as PCM, non-blocking. Returns an opaque handle to pass to
    stop(), or None if no player is available. The bytes are WAV-wrapped to
    sidestep ffplay's raw-input quirks; any bytes work."""
    wav = _wav_wrap(bytes(data), rate, ch, bits, 3 if floating else 1)
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.write(fd, wav)
    os.close(fd)
    ff = _ffplay()
    if ff:
        p = subprocess.Popen([ff, "-autoexit", "-nodisp", "-loglevel", "error", tmp])
    elif os.name == "nt":
        ps = f"(New-Object Media.SoundPlayer '{tmp}').Play()"
        p = subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])
    else:
        _unlink(tmp)
        return None
    try:
        p._acidcat_tmp = tmp
    except (AttributeError, TypeError):
        pass
    return p


def stop(handle):
    """Stop playback started by play_bytes and remove its temp file."""
    if handle is None:
        return
    try:
        handle.terminate()
    except Exception:
        pass
    _unlink(getattr(handle, "_acidcat_tmp", None))


def _unlink(path):
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
