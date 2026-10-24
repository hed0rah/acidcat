"""Where a tempo comes from, and which to believe when several disagree.

A WAV can state its tempo in an ID3 TBPM frame, an ACID chunk, a Steinberg
iXML block, a Logic ResU analysis, a TEMPOMAP record in a JUNK chunk, a
Bitwig beat map, an INFO IBPM, or a cue label reading "Tempo: 120"; its name
or its folder can state one too, and the audio can be measured. A claim here
is (source, bpm).

Measured on a 32k-file library (tmp-tests/bench, tmp-tests/census) against
the tempo the file name states:

    ID3 TBPM       818 of 822      ResU         499 of 499
    TEMPOMAP       767 of 767 in four packs; a fifth stamps 74 and 120
    BWBM           171 of 174      INFO IBPM    106 of 107
    cue "Tempo:"   166 of 179, every miss one pack stamping 120
    ACID           82%; 120 is what writers leave behind
    audio          14% exact, 42% counting half and double time

Three things go wrong, and each has a rule:

- **a stamped default.** 120 is the tempo a writer or a DAW session starts
  at. A chunk tempo of exactly 120 yields to a different tempo in the file
  name.
- **a session tempo on a one-shot.** A DAW stamps the session's tempo on
  every file it bounces, hits included, where it describes the session and
  not the sound. So the sources a DAW writes per session (iXML, ResU,
  TEMPOMAP, BWBM, IBPM, cue labels, and a tempo in a folder name) count only
  when the file's length is a whole number of beats at that tempo, a bar or
  more: true of a loop cut to the grid, rarely of a hit. iXML that says how
  many beats the file holds is checked against its length instead.

- **a pack-wide stamp.** A session source that disagrees with the file name
  is a session tempo stamped across a pack, so the name outranks them.

ACID keeps its own benchmarked rule (120 yields to the name; any other value
stands, one-shot or not), and an ID3 TBPM, set by hand per file, stands.
"""

import re

# strongest first. the name sits above the session sources: where the two
# disagree it is a session tempo stamped across a pack (TEMPOMAP 74 on 59 files
# named 86), and a name is typed per file
PRECEDENCE = ("tag", "midi", "basc", "acid", "name", "ixml", "resu",
              "tempomap", "bwbm", "ibpm", "cue", "folder", "audio")

# a chunk tempo of exactly this yields to a different one in the name
DEFAULT_BPM = 120.0

# written per DAW session, so they must fit the file's length
SESSION = frozenset({"ixml", "resu", "tempomap", "bwbm", "ibpm", "cue", "folder"})
STAMPABLE = frozenset({"ixml", "resu", "tempomap", "bwbm", "acid", "ibpm", "cue"})

MIN_BEATS = 4.0                # a bar; no loop measured was shorter, and DAWs
                               # bounce one-shots to exactly two beats
BEAT_TOLERANCE = 0.01          # of one beat; 88% of loops sit inside it


def claim(source, bpm, beats=None):
    """(source, bpm, beats) or None when `bpm` is not a usable tempo."""
    try:
        v = float(bpm)
    except (TypeError, ValueError):
        return None
    if not 20.0 <= v <= 400.0:
        return None
    return (source, round(v, 3), beats)


def fits_length(bpm, duration):
    """True when `duration` seconds is a whole number of beats at `bpm`, two
    or more."""
    if not duration or duration <= 0:
        return False
    beats = duration * bpm / 60.0
    if beats < MIN_BEATS - BEAT_TOLERANCE:
        return False
    return abs(beats - round(beats)) <= BEAT_TOLERANCE


def _admissible(c, duration, named):
    source, bpm, beats = c
    if source in STAMPABLE and bpm == DEFAULT_BPM and named is not None \
            and abs(named - bpm) > 0.5:
        return False
    if source == "ixml" and beats:
        return bool(duration) and abs(beats * 60.0 / bpm - duration) <= 0.02 * duration
    if source in SESSION:
        return fits_length(bpm, duration)
    return True


_CUE_TEMPO = re.compile(r"^\s*tempo\s*[:=]\s*(\d{2,3}(?:\.\d+)?)\s*(?:bpm)?\s*$", re.I)


def cue_tempo(labels):
    """The tempo in a cue label reading "Tempo: 120.0", or None."""
    for text in labels or ():
        m = _CUE_TEMPO.match(text)
        if m:
            return float(m.group(1))
    return None


def path_claims(filepath):
    """The name's tempo, and failing that the nearest folder's (up to two
    levels): a kit folder "Kit 1 - C minor - 100 bpm" holding stems."""
    import os
    from acidcat.core.analysis.detect import parse_bpm_from_filename
    named = parse_bpm_from_filename(filepath)
    if named is not None:
        return [claim("name", named)]
    parent = os.path.dirname(os.path.abspath(filepath))
    for _ in range(2):
        parent, base = os.path.split(parent)
        if not base:
            break
        folder = parse_bpm_from_filename(base + ".wav")
        if folder is not None:
            return [claim("folder", folder)]
    return []


def wav_claims(ctx, filepath, acid_bpm):
    """Every tempo claim a WAV makes, from an inspect_wav ctx and its path."""
    beats = ctx.get("ixml_beats")
    return [
        claim("tag", ctx.get("id3_bpm")),
        claim("ixml", ctx.get("ixml_tempo"), beats=beats),
        claim("resu", ctx.get("resu_bpm")),
        claim("tempomap", ctx.get("tempomap_bpm")),
        claim("bwbm", ctx.get("bwbm_bpm")),
        claim("acid", acid_bpm),
        claim("ibpm", (ctx.get("info") or {}).get("IBPM")),
        claim("cue", cue_tempo(ctx.get("cue_labels"))),
    ] + path_claims(filepath)


def resolve(claims, duration):
    """{"bpm": float|None, "bpm_source": str|None} from a list of claims
    (None entries ignored)."""
    claims = [c for c in claims if c]
    # only the file's own name overrides a stamped 120: that is what the
    # benchmark measured, and a folder speaks for many files at once
    named = next((c[1] for c in claims if c[0] == "name"), None)
    rank = {s: i for i, s in enumerate(PRECEDENCE)}
    for c in sorted(claims, key=lambda c: rank.get(c[0], len(rank))):
        if _admissible(c, duration, named):
            return {"bpm": c[1], "bpm_source": c[0]}
    return {"bpm": None, "bpm_source": None}
