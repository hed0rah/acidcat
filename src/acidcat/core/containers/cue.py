"""CUE sheet parsing and CD-DA (Red Book) audio extraction.

A .cue sheet describes a disc's track layout over one or more .bin files: which
tracks are data (MODE1/MODE2) and which are audio (AUDIO), and where each begins
(an INDEX in MM:SS:FF, 75 frames per second). Audio tracks are Red Book CD-DA:
raw 2352-byte sectors of 16-bit little-endian stereo PCM at 44100 Hz -- no codec,
no sector headers, the whole sector is samples. This is how PS1, Sega CD, Neo Geo
CD, PC-Engine CD and countless others carry their music.

Two layouts both fall out of the same parse: split (one .bin per track, INDEX
resets per file) and single-.bin (all tracks in one file at cumulative MSF
offsets). Each audio track's byte range runs from its INDEX 01 to the next
track's start (or end of file).

    from acidcat.core.containers import cue
    for t in cue.parse("game.cue"):
        print(t["num"], t["type"], t["file"])
"""

import io
import os

from acidcat.core.infra.source import input_name, open_input

SECTOR = 2352                        # raw CD sector; a CD-DA sector is all samples
CDDA_RATE = 44100


class CueError(Exception):
    """A .cue sheet that cannot be parsed. A cue is hand-editable text, so
    malformed ones are ordinary input, not an internal error."""


def _msf_to_lba(msf):
    parts = msf.split(":")
    if len(parts) != 3:
        raise CueError(f"malformed MSF timestamp {msf!r} (want MM:SS:FF)")
    try:
        m, s, f = (int(x) for x in parts)
    except ValueError:
        raise CueError(f"non-numeric MSF timestamp {msf!r}") from None
    return (m * 60 + s) * 75 + f     # 75 sectors per second


def parse(cue_path):
    """Parse a .cue sheet (a path or a Source) into a list of tracks, each
    {num, type, file, start_lba} where start_lba is the track's INDEX 01 (its
    audio start, past any pregap) within its file, in sectors."""
    tracks = []
    cur_file = None
    cur = None
    base = os.path.dirname(input_name(cue_path))
    raw = open_input(cue_path)
    if not isinstance(raw, io.BufferedIOBase):
        raw = io.BufferedReader(raw)
    with io.TextIOWrapper(raw, encoding="latin-1") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("FILE "):
                # an unterminated quote used to yield "", making cur_file the
                # containing DIRECTORY; the later open() then raised an OSError
                # that escaped as a traceback and exit 1
                if '"' in line:
                    q = line.split('"')
                    name = q[1] if len(q) > 2 else ""
                else:
                    rest = line.split()
                    name = rest[1] if len(rest) > 1 else ""
                if not name:
                    raise CueError(f"FILE line names no file: {line!r}")
                cur_file = os.path.join(base, name)
            elif line.startswith("TRACK "):
                if cur:
                    tracks.append(cur)
                parts = line.split()
                if len(parts) < 3:
                    raise CueError(f"TRACK line needs a number and a type: {line!r}")
                try:
                    tnum = int(parts[1])
                except ValueError:
                    raise CueError(f"non-numeric track number {parts[1]!r}") from None
                cur = {"num": tnum, "type": parts[2], "file": cur_file,
                       "start_lba": 0}
            elif line.startswith("INDEX ") and cur is not None:
                parts = line.split()
                if len(parts) < 3:
                    raise CueError(f"INDEX line needs a number and a timestamp: {line!r}")
                try:
                    idx = int(parts[1])
                except ValueError:
                    raise CueError(f"non-numeric index number {parts[1]!r}") from None
                lba = _msf_to_lba(parts[2])
                # INDEX 01 is the track proper; INDEX 00 is pregap. Prefer 01.
                if idx == 1 or "start_set" not in cur:
                    cur["start_lba"] = lba
                    if idx == 1:
                        cur["start_set"] = True
    if cur:
        tracks.append(cur)
    return tracks


def audio_tracks(cue_path):
    """Yield {num, file, start, size} byte ranges for each AUDIO (CD-DA) track,
    bounding each track by the next track's start in the same file (or EOF)."""
    tracks = parse(cue_path)
    byfile = {}
    for t in tracks:
        byfile.setdefault(t["file"], []).append(t)
    for f, ts in byfile.items():
        ts.sort(key=lambda t: t["start_lba"])
        try:
            fsize = os.path.getsize(f)
        except OSError:
            continue
        for i, t in enumerate(ts):
            if not t["type"].upper().startswith("AUDIO"):
                continue
            start = t["start_lba"] * SECTOR
            end = ts[i + 1]["start_lba"] * SECTOR if i + 1 < len(ts) else fsize
            end = min(end, fsize)
            if end > start:
                yield {"num": t["num"], "file": f, "start": start, "size": end - start}
