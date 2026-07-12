"""Optional audio playback: hear a file, a time slice, or a raw byte region as PCM.

Shells out to ffplay (ships with ffmpeg) so the core keeps its zero-dependency
promise; on Windows without ffplay, falls back to the built-in SoundPlayer for
WAV. The TUI uses play_bytes/play_region to audition a data/SSND chunk (or a
header, a cavity) reinterpreted as PCM. No hard dependency: have_audio() is False
when nothing can play, and callers degrade gracefully. Shared by acidcat's TUI
and the acidcat-playground TUI.

  python -m acidcat.util.play FILE                 whole file
  python -m acidcat.util.play FILE --ss 2 --t 3    3 s starting 2 s in
  python -m acidcat.util.play FILE --raw OFF LEN   bytes [OFF, OFF+LEN) as PCM
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile


def _ffplay():
    return shutil.which("ffplay")


def have_audio():
    """True if some player is available (ffplay, or SoundPlayer on Windows)."""
    return _ffplay() is not None or os.name == "nt"


def play(path, start=None, dur=None, block=True):
    """Play a file, optionally a [start, start+dur] time slice, via ffplay
    (SoundPlayer fallback on Windows). Returns the process handle, or None."""
    ff = _ffplay()
    if not ff:
        return _fallback(path, block)
    cmd = [ff, "-autoexit", "-nodisp", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if dur is not None:
        cmd += ["-t", str(dur)]
    cmd.append(path)
    p = subprocess.Popen(cmd)
    if block:
        p.wait()
    return p


def _wav_wrap(pcm, rate, ch, bits, tag):
    block = max(1, ch * bits // 8)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, tag, ch, rate,
                                    rate * block, block, bits)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def play_bytes(data, rate=44100, ch=1, bits=16, floating=False, block=False):
    """Play raw bytes as PCM. Returns an opaque handle for stop() (or None if no
    player). The bytes are WAV-wrapped to sidestep ffplay's raw-input quirks;
    any bytes work (garbage in sounds like garbage, which is the point)."""
    wav = _wav_wrap(bytes(data), rate, ch, bits, 3 if floating else 1)
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.write(fd, wav)
    os.close(fd)
    if block:
        try:
            return play(tmp, block=True)
        finally:
            _unlink(tmp)
    p = play(tmp, block=False)
    try:
        p._acidcat_tmp = tmp        # temp travels with the handle; stop() unlinks it
    except (AttributeError, TypeError):
        _unlink(tmp)
    return p


def play_region(path, offset, length, block=False, **params):
    """Read a byte range from a file and hear it as raw PCM. `params` are
    play_bytes kwargs (rate / ch / bits / floating)."""
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(length)
    return play_bytes(data, block=block, **params)


def stop(handle):
    """Stop playback started by play_bytes/play_region and remove its temp file."""
    if handle is None:
        return
    try:
        if handle.poll() is None:
            handle.terminate()
    except Exception:
        pass
    _unlink(getattr(handle, "_acidcat_tmp", None))


def _fallback(path, block=True):
    """No ffplay: on Windows, play a WAV via the built-in SoundPlayer."""
    if os.name == "nt" and path.lower().endswith(".wav"):
        verb = "PlaySync" if block else "Play"
        ps = f"(New-Object Media.SoundPlayer '{path}').{verb}()"
        return subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])
    return None


def _unlink(path):
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def _main(argv):
    if not argv:
        print(__doc__)
        return 0
    path = argv[0]
    opt = {argv[j][2:]: argv[j + 1] for j in range(len(argv))
           if argv[j].startswith("--") and j + 1 < len(argv) and argv[j] != "--raw"}
    if "--raw" in argv:
        i = argv.index("--raw")
        play_region(path, int(argv[i + 1], 0), int(argv[i + 2], 0), block=True,
                    rate=int(opt.get("rate", 44100)), ch=int(opt.get("ch", 1)),
                    bits=int(opt.get("bits", 16)))
    else:
        play(path, start=(float(opt["ss"]) if "ss" in opt else None),
             dur=(float(opt["t"]) if "t" in opt else None))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
