"""
BPM and key detection.

Combines multiple strategies: RIFF chunk metadata, filename parsing,
and librosa audio analysis with smart validation/fallback.
"""

import os
import re

# ── Filename parsing ───────────────────────────────────────────────

def parse_bpm_from_filename(filepath):
    """Extract BPM from filename using common patterns. Returns int or None."""
    # The EXTENSION is stripped before matching. The bare-number pattern below
    # rejects a digit run followed by a dot, which is there to keep `120.5` from
    # reading as 120 -- but it also rejected the dot that starts the extension,
    # so `Kick_128.wav` parsed as None while `Kick_128_C.wav` gave 128. In this
    # project's own corpus, 135 files are named that way, and the convention is
    # unambiguous: where a file carries both, the take number sits in the middle
    # and the tempo last (`..._loop_01_groovy_80.wav`), and whole folders named
    # "140 bpm" hold files ending in _140.
    #
    # Stripping only the extension keeps the decimal guard intact -- `120.5.wav`
    # becomes `120.5`, where 120 is still followed by a dot and still rejected --
    # and the 60..300 range keeps the drum-machine names out: `Kick_909.wav` and
    # `Snare_808.wav` are above it, `Take_02.wav` below.
    filename = os.path.splitext(os.path.basename(filepath))[0]
    bpm_patterns = [
        r'(\d{2,3})\s*bpm',
        r'bpm\s*(\d{2,3})',
        r'(\d{2,3})bpm',
        # bare 2-3 digit run, not adjacent to digits, decimals, OR letters.
        # Letter-adjacent rejection prevents pack identifiers like '91V_SBH'
        # from matching as BPM 91; the parser falls through to the real
        # tempo marker (e.g. _126_ later in the filename). Zero-width
        # lookarounds so consecutive numbers (e.g. '_03_126_') both surface
        # instead of the first consuming the shared underscore.
        r'(?<![\d.A-Za-z])(\d{2,3})(?![\d.A-Za-z])',
    ]
    # iterate ALL matches of each pattern; a filename like "Pack_03_126_A#"
    # matches "_03_" before "_126_" so we need to consider every occurrence.
    for pattern in bpm_patterns:
        for match in re.findall(pattern, filename, re.IGNORECASE):
            bpm = int(match)
            # 60..300 covers everything from slow ballad to gabber.
            # DnB at 174, hardcore at 220, gabber at 240 all pass.
            if 60 <= bpm <= 300:
                return bpm
    return None


# flat -> sharp normalization for consistency with MIDI note naming.
# Cb/Fb aren't pitch-raising flats; they're enharmonic with B/E.
_FLAT_TO_SHARP = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#",
                  "Bb": "A#", "Cb": "B", "Fb": "E"}


def _normalize_root(note):
    """'eb'/'EB'/'Eb' -> 'D#'; 'f#' -> 'F#'; 'a' -> 'A'."""
    root = note[0].upper() + note[1:].lower()
    return _FLAT_TO_SHARP.get(root, root)


def parse_key_from_filename(filepath):
    """Extract musical key from filename. Returns string like 'C#m' or None.

    Flats are accepted and normalized to sharps ('Eb minor' -> 'D#m').
    A bare capital-M suffix is the major marker used by Beatport /
    Mixed In Key / Serato / Rekordbox ('F#M' -> F# major); lowercase
    'm' is minor. The worded suffixes (maj/major/min/minor) stay
    case-insensitive.
    """
    filename = os.path.basename(filepath).replace('_', ' ').replace('-', ' ')
    key_patterns = [
        r'\b([A-G][#b]?)\s*major\b',
        r'\b([A-G][#b]?)\s*maj\b',
        r'\b([A-G][#b]?)major\b',
        r'\b([A-G][#b]?)maj\b',
        r'\b([A-G][#b]?)\s*minor\b',
        r'\b([A-G][#b]?)\s*min\b',
        r'\b([A-G][#b]?)minor\b',
        r'\b([A-G][#b]?)min\b',
        # The mode suffix, split three ways because a bare capital M is
        # ambiguous. `([A-G][#b]?)\s*[mM]\b` under re.IGNORECASE used to cover
        # all of it, and read FM as F major: on a real 2,331-file synth corpus
        # 10 of the 14 major labels it produced -- 71% -- were that false
        # positive. `_BARE_KEY_TOKEN` below already rejected capital M for the
        # same reason, so the two parsers disagreed with each other.
        #
        # Lowercase m may hug the letter; that spelling is unambiguous.
        r'\b([A-G][#b]?)m\b',
        # An accidental disambiguates: no English word contains "F#" or "Bb",
        # so "F#M" is safely Beatport-style F# major even unspaced.
        r'\b([A-G][#b])M\b',
        # Without one, capital M must be separated. "FM", "AM", "GM", "BM" are
        # ordinary words in sample names, and unspaced they are far more likely
        # to be FM synthesis or AM radio than a key.
        r'\b([A-G])\s+M\b',
    ]
    # The last two are matched CASE-SENSITIVELY. Telling "Am" from "AM" is the
    # whole point of them, and re.IGNORECASE erases exactly that distinction --
    # which is why the fix above did nothing until this line changed. The
    # spellings above are words ("major", "min") where case carries no meaning.
    case_sensitive = set(key_patterns[-3:])
    for pattern in key_patterns:
        flags = 0 if pattern in case_sensitive else re.IGNORECASE
        match = re.search(pattern, filename, flags)
        if match:
            note = _normalize_root(match.group(1))
            key_text = match.group(0)
            lowered = key_text.lower()
            # classify minor vs major: 'min' or 'minor' anywhere; 'maj'
            # anywhere is major; otherwise the bare one-letter suffix
            # decides by case (M = major, m = minor).
            if "min" in lowered:
                return f"{note}m"
            if "maj" in lowered:
                return note
            if key_text.rstrip().endswith("M"):
                return note
            return f"{note}m"
    return None


# whole-token key regex: A, A#, Ab, Am, A#m, Gbm, etc.
# Lowercase m for minor; capital M rejected to avoid false positives (file "SCREAM").
_BARE_KEY_TOKEN = re.compile(r"^([A-G])([#b]?)(m)?$")


def parse_bare_key_token(token):
    """If `token` is a whole-token musical key (e.g. 'A#', 'Em', 'Bbm'), return
    the normalized 'C#m' / 'Eb' form. Otherwise None.
    """
    m = _BARE_KEY_TOKEN.match(token)
    if not m:
        return None
    note = m.group(1).upper()
    accidental = m.group(2) or ""
    minor = "m" if m.group(3) else ""
    root = _normalize_root(note + accidental)
    return root + minor


# chord-quality tokens that fix the mode of a bare key letter sitting beside them
# (e.g. "min_C", "Maj7_C" -- quality-before-letter, which parse_key_from_filename's
# letter-first patterns miss). Deliberately strict: min/maj plus an optional chord
# extension and nothing else, so "Minimal"/"Magic" don't read as a mode.
_MODE_MIN_TOKEN = re.compile(r"^min(or)?(add)?\d*$", re.I)
_MODE_MAJ_TOKEN = re.compile(r"^maj(or)?(add)?\d*$", re.I)


def _mode_from_neighbour(tokens, i):
    """'min'/'maj' implied by a chord-quality token immediately beside tokens[i]."""
    for j in (i - 1, i + 1):
        if 0 <= j < len(tokens):
            if _MODE_MIN_TOKEN.match(tokens[j]):
                return "min"
            if _MODE_MAJ_TOKEN.match(tokens[j]):
                return "maj"
    return None


def parse_key_from_path(filepath, max_parent_depth=3):
    """Robust key extraction across filename + parent folders.

    Tries parse_key_from_filename first (matches 'Am', 'C minor', etc.),
    then falls back to whole-token bare-key matches in the filename and
    up to `max_parent_depth` parent folders. Returns the first hit or None.

    Whole-token matching avoids false positives like "Analog" matching 'A'.
    """
    hit = key_from_path_detail(filepath, max_parent_depth)
    return hit[0] if hit else None


def key_from_path_detail(filepath, max_parent_depth=3):
    """parse_key_from_path with provenance: (key, where, mode_known) or None.

    `where` is "name" for the file's own name and "folder" for a parent
    directory. `mode_known` is False for a bare letter ("Bass_A_123BPM"):
    that names a root and says nothing about major or minor, though the key
    string reads "A" either way.
    """
    existing = parse_key_from_filename(filepath)
    if existing is not None:
        return existing, "name", True

    # walk filename basename + parent dirs outward
    segments = []
    stem = os.path.splitext(os.path.basename(filepath))[0]
    segments.append(stem)
    cur = os.path.dirname(filepath)
    for _ in range(max_parent_depth):
        if not cur or cur in ("/", "\\"):
            break
        parent = os.path.basename(cur)
        if parent:
            segments.append(parent)
        new_cur = os.path.dirname(cur)
        if new_cur == cur:
            break
        cur = new_cur

    # brackets split too: "808 Alert (C)" names its key in parentheses
    token_re = re.compile(r"[_\-\.\s()\[\]]+")
    for n, seg in enumerate(segments):
        where = "name" if n == 0 else "folder"
        tokens = [t for t in token_re.split(seg) if t]
        for i, token in enumerate(tokens):
            key = parse_bare_key_token(token)
            if key is None:
                continue
            if key.endswith("m"):
                return key, where, True
            # parse_bare_key_token yields a bare major letter here (minor tokens
            # like 'Am' are caught earlier by parse_key_from_filename). A chord-
            # quality marker beside it fixes the mode: 'min_C' -> 'Cm'.
            mode = _mode_from_neighbour(tokens, i)
            if mode == "min":
                return key + "m", where, True
            return key, where, mode == "maj"
    return None


# ── Validation / improvement ───────────────────────────────────────

def validate_and_improve_bpm(detected_bpm, filename_bpm, confidence_threshold=20):
    """
    Validate detected BPM against filename BPM and choose the best value.

    Returns:
        (final_bpm, source) where source is 'detected', 'filename', or 'corrected'.
    """
    # The range check runs FIRST. It used to sit below the early return, so it
    # only fired when a filename BPM was already available -- i.e. only when the
    # answer was already known. With no filename tempo, anything shipped: over
    # 400 real files, 71 of the 277 unlabelled ones (25.6%) came back outside
    # this window, up to 304 BPM on a 0.4-second snare.
    if detected_bpm is not None and not (60 <= detected_bpm <= 200):
        # out of range and nothing to fall back on: say nothing rather than
        # something wrong. A missing tempo is recoverable; a confident wrong
        # one silently poisons every downstream sort.
        return (filename_bpm, 'filename') if filename_bpm is not None \
            else (None, 'rejected')
    if filename_bpm is None:
        return detected_bpm, ('detected' if detected_bpm is not None else None)
    if detected_bpm is None:
        return filename_bpm, 'filename'

    diff = abs(detected_bpm - filename_bpm)

    if diff <= confidence_threshold:
        return detected_bpm, 'detected'
    if abs(detected_bpm * 2 - filename_bpm) <= confidence_threshold:
        return detected_bpm * 2, 'corrected'
    if abs(detected_bpm / 2 - filename_bpm) <= confidence_threshold:
        return detected_bpm / 2, 'corrected'
    if abs(detected_bpm * 1.5 - filename_bpm) <= confidence_threshold:
        return detected_bpm * 1.5, 'corrected'
    if abs(detected_bpm / 1.5 - filename_bpm) <= confidence_threshold:
        return detected_bpm / 1.5, 'corrected'

    return filename_bpm, 'filename'


def improve_key_detection(detected_key, filename_key):
    """
    Combine detected key with filename key for better accuracy.

    Returns:
        (final_key, source) where source is 'detected' or 'filename'.
    """
    if filename_key is None:
        return detected_key, 'detected'
    if detected_key is None:
        return filename_key, 'filename'
    if detected_key == filename_key:
        return detected_key, 'detected'
    return filename_key, 'filename'


# ── Krumhansl-Schmuckler key finding ───────────────────────────────

# Krumhansl-Kessler key profiles: per-pitch-class "fit" weights from the
# probe-tone experiments, indexed from the tonic (index 0 = tonic). Major and
# minor have different shapes, so correlating a piece's pitch-class distribution
# against all 24 rotations names the key AND its mode -- which chroma argmax
# (strongest single pitch) cannot.
_KS_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_KS_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
_PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# minimum K-S correlation to trust an audio-derived key. A clear tonal passage
# correlates ~0.8+; a weakly-tonal or ambiguous input (an isolated chord, a drum
# loop) scores lower and its winning key is unreliable, so below this we emit no
# key rather than a confident-sounding wrong one. A wrong key misroutes harmonic
# search; "unknown" does not.
KEY_CONF_MIN = 0.75

# Minimum margin over the best differently-rooted candidate before we name a
# key. This replaced the raw-correlation gate above and dominates it on both
# axes, measured over 298 key-labelled files:
#
#     correlation >= 0.75    62.9% root accuracy, answers 20.8% of files
#     margin      >= 0.15    68.0% root accuracy, answers 57.7% of files
#
# More accurate while answering nearly three times as many files, because a
# high correlation is routinely shared by a key and its relative -- the raw fit
# cannot tell "clearly C" from "C or Am, take your pick", and the margin can.
#
# THOSE NUMBERS ARE CQT-ONLY. They were measured on librosa's constant-Q
# chroma, which is the only path that reaches this gate when librosa is
# installed. The numpy fallback (`_key_without_librosa`) builds its chroma from
# an STFT, whose margins are systematically smaller -- median 0.08 against this
# 0.15 threshold on a 70-file sample -- so the same gate answers roughly 18% of
# files there, not 57.7%. It is a coverage mismatch, not an accuracy claim:
# the gate is doing its job, it is simply calibrated for a different
# distribution. Giving the STFT path its own threshold needs a labelled corpus
# pass; until then the number above must not be read as applying to it.
KEY_MARGIN_MIN = 0.15

# A key needs more than one pitch class to be a claim at all. The bar is set
# just above zero on purpose: a first attempt used a fifth of the peak, and
# measurement showed it threw away the BEST material -- the files it rejected
# scored 80.0% root accuracy against 49.8% for the ones it kept, because a
# tonal one-shot has a sparse chroma and an obvious key. Real audio spreads
# some energy into every bin through leakage and partials, so at 2% this
# rejects nothing real and still refuses a synthetic single-bin spike, which
# carries no key information at all.
_CHROMA_MIN_CLASSES = 3
_CHROMA_PRESENT = 0.02


def _pearson(a, b):
    """Pearson correlation of two equal-length sequences; 0 when either is flat."""
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return 0.0
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / (va ** 0.5 * vb ** 0.5)


def estimate_key_ks(chroma12):
    """Krumhansl-Schmuckler key finding over a 12-bin pitch-class distribution.

    ``chroma12`` is 12 energies, one per pitch class starting at C (bin 0 = C,
    as librosa's chroma is laid out), summed/averaged over time. Correlates it
    against the major and minor profile rotated to each of the 12 tonics and
    returns ``(key, confidence)`` -- key is 'C'/'C#'/... for major or 'Cm'/... for
    minor, confidence is the winning Pearson correlation (0..1). Returns
    ``(None, 0.0)`` for an empty or silent (flat/zero) distribution.

    Pure stdlib, so it runs anywhere the 12-bin chroma is available."""
    if not chroma12 or len(chroma12) != 12 or sum(chroma12) <= 0:
        return None, 0.0
    best_key, best_r = None, -2.0
    for tonic in range(12):
        maj = [_KS_MAJOR[(i - tonic) % 12] for i in range(12)]
        minr = [_KS_MINOR[(i - tonic) % 12] for i in range(12)]
        r_maj = _pearson(chroma12, maj)
        if r_maj > best_r:
            best_r, best_key = r_maj, _PITCH_CLASSES[tonic]
        r_min = _pearson(chroma12, minr)
        if r_min > best_r:
            best_r, best_key = r_min, _PITCH_CLASSES[tonic] + "m"
    return best_key, round(max(best_r, 0.0), 3)


# Temperley's revision of the Krumhansl-Kessler weights, fitted to notated music
# rather than to probe-tone experiments. The literature prefers them, and on this
# corpus they are clearly WORSE: 23.0% root accuracy against 39.0% for the 1982
# originals on the same 200 files and the same chroma. Kept selectable and
# labelled so the result is not quietly re-discovered, but `ks` is the default
# for a measured reason.
_TEMPERLEY_MAJOR = (5.0, 2.0, 3.5, 2.0, 4.5, 4.0, 2.0, 4.5, 2.0, 3.5, 1.5, 4.0)
_TEMPERLEY_MINOR = (5.0, 2.0, 3.5, 4.5, 2.0, 4.0, 2.0, 4.5, 3.5, 2.0, 1.5, 4.0)

PROFILE_SETS = {
    "ks": (_KS_MAJOR, _KS_MINOR),
    "temperley": (_TEMPERLEY_MAJOR, _TEMPERLEY_MINOR),
}


def _relative_pair(a, b):
    """True if two keys are each other's relative major/minor (Am vs C)."""
    if a is None or b is None or a.endswith("m") == b.endswith("m"):
        return False
    major, minor = (b, a) if a.endswith("m") else (a, b)
    tonic_maj = _PITCH_CLASSES.index(major)
    tonic_min = _PITCH_CLASSES.index(minor[:-1])
    return (tonic_min + 3) % 12 == tonic_maj


def estimate_key_detailed(chroma12, profiles="ks"):
    """Key finding that separates how sure we are of the ROOT from the MODE.

    A single confidence number hides the failure this task actually has. The
    relative major and minor of a key share all seven notes, so their profiles
    correlate almost identically with the same chroma -- the root is often solid
    while major-vs-minor is a coin flip. Reporting one number forces a choice
    between overstating the mode and discarding a good root.

    So the runners-up are kept, and confidence is the margin between the best
    fit and the best fit of a *different* root -- a relative-key runner-up says
    nothing about whether the root is right. Measured on 298 key-labelled files:
    ranking by this margin gives 78.0% root accuracy over the most-confident
    fifth against 62.7% for the raw correlation, and 74.8% vs 63.9% over the
    most-confident two fifths. Identical at full coverage, as it must be -- the
    detector is unchanged, only the ordering of trust.

    A symmetric mode_confidence (margin against the same root's other mode) was
    built and then removed: it measured *backwards*, 55.5% mode accuracy when
    confident against 66.3% when not. The available ground truth cannot settle
    why -- filename labels write a bare "C" for both C major and C minor -- and
    shipping a confidence that anti-correlates with being right is worse than
    shipping none. `ambiguous_with` is kept because it states a fact about the
    scores rather than a claim about accuracy.

    Returns a dict: key, root, mode, confidence, root_confidence, correlation,
    ambiguous_with, runners_up.
    """
    empty = {"key": None, "root": None, "mode": None, "confidence": 0.0,
             "root_confidence": 0.0,
             "correlation": 0.0, "ambiguous_with": None, "runners_up": []}
    if not chroma12 or len(chroma12) != 12 or sum(chroma12) <= 0:
        return empty
    # A key is a claim about which seven pitch classes are in play, so one or
    # two of them cannot support it: an isolated A fits A major, A minor, D
    # major and F# minor alike. The profiles will still rank *something* first
    # and the margin can look healthy, so this has to be refused up front
    # rather than left to the confidence.
    peak = max(chroma12)
    if sum(1 for v in chroma12 if v >= peak * _CHROMA_PRESENT) < _CHROMA_MIN_CLASSES:
        return empty
    major, minor = PROFILE_SETS.get(profiles, PROFILE_SETS["ks"])

    scored = []
    for tonic in range(12):
        name = _PITCH_CLASSES[tonic]
        maj = [major[(i - tonic) % 12] for i in range(12)]
        minr = [minor[(i - tonic) % 12] for i in range(12)]
        scored.append((_pearson(chroma12, maj), name, name, "major"))
        scored.append((_pearson(chroma12, minr), name + "m", name, "minor"))
    scored.sort(key=lambda t: -t[0])

    best_r, best_key, best_root, best_mode = scored[0]
    if best_r <= 0:
        return empty

    # margin against the best candidate rooted elsewhere -- a relative-key
    # runner-up says nothing about whether the root is right
    other_root = next((s for s in scored[1:] if s[2] != best_root), None)
    root_conf = max(0.0, best_r - other_root[0]) if other_root else best_r

    runner = scored[1]
    ambiguous = runner[1] if _relative_pair(best_key, runner[1]) else None

    return {
        "key": best_key, "root": best_root, "mode": best_mode,
        "confidence": round(max(best_r, 0.0), 3),      # legacy: the raw fit
        "root_confidence": round(root_conf, 3),
        "correlation": round(best_r, 3),
        "ambiguous_with": ambiguous,
        "runners_up": [{"key": k, "correlation": round(r, 3)}
                       for r, k, _root, _mode in scored[1:4]],
    }


def _key_without_librosa(filepath):
    """Audio-derived key using only numpy, or None.

    The weaker path by design -- an STFT chroma cannot resolve low notes as
    cleanly as a constant-Q one, and it measures 39.0% root accuracy against
    51.0%. It exists because the alternative when librosa is absent is no
    audio key detection at all, only the filename.
    """
    try:
        from acidcat.core.analysis import chroma as chromamod, pcm
        planes, rate = pcm.load(filepath)
        vec = chromamod.chroma12(planes, rate, harmonic=True)
    except Exception:
        return None
    if not vec:
        return None
    detail = estimate_key_detailed(vec)
    return detail["key"] if detail["root_confidence"] >= KEY_MARGIN_MIN else None


def _tempo(librosa, **kw):
    """Tempo estimation across librosa versions.

    `librosa.beat.tempo` is a deprecated alias for
    `librosa.feature.rhythm.tempo` and is slated for removal in librosa 1.0.
    The caller sits inside `except Exception: pass`, so when the alias
    disappears the AttributeError would be swallowed and BPM would quietly go
    filename-only while still reporting `bpm_source: "detected"` -- a silent
    capability loss on a version bump. pyproject pins only `librosa>=0.10.1`
    with no upper bound, and the one test covering this path monkeypatches
    `librosa.beat.tempo`, so it would keep passing.
    """
    feature = getattr(librosa, "feature", None)
    # `librosa.feature.tempo` is the re-export and is what features.py already
    # calls; `librosa.feature.rhythm` is a submodule that is NOT auto-imported,
    # so a plain getattr for it returns None until someone imports it.
    fn = getattr(feature, "tempo", None)
    if fn is None:
        rhythm = getattr(feature, "rhythm", None)
        fn = getattr(rhythm, "tempo", None)
    if fn is None:                       # last resort: the deprecated alias
        fn = getattr(getattr(librosa, "beat", None), "tempo", None)
    if fn is None:                       # pragma: no cover -- no known version
        raise AttributeError("librosa exposes no tempo estimator")
    return fn(**kw)


# ── Librosa-based estimation ───────────────────────────────────────

def estimate_librosa_metadata(filepath):
    """
    Estimate BPM/key/duration using librosa + filename parsing.

    Returns dict with keys: estimated_bpm, estimated_key, duration_sec,
    bpm_source, key_source, filename_bpm, filename_key, detected_bpm, detected_key.
    """
    import warnings
    warnings.filterwarnings("ignore")

    try:
        import librosa
        import numpy as np
    except ImportError:
        # no audio analysis available, but filename/path parsing is pure Python:
        # still answer from the name rather than losing the capability entirely.
        from acidcat.util.deps import require
        require("librosa", "numpy", group="analysis")
        fn_bpm = parse_bpm_from_filename(filepath)
        fn_key = parse_key_from_path(filepath)
        # numpy alone can still read a key out of the audio -- less well than
        # the CQT path (39.0% vs 51.0% root accuracy on the same files), but the
        # alternative here is no audio key detection at all
        det_key = _key_without_librosa(filepath)
        return {
            "estimated_bpm": fn_bpm,
            "estimated_key": fn_key or det_key,
            "duration_sec": None,
            "bpm_source": "filename" if fn_bpm is not None else None,
            "key_source": ("filename" if fn_key else
                           ("audio-numpy" if det_key else None)),
            "filename_bpm": fn_bpm,
            "filename_key": fn_key,
            "detected_bpm": None,
            "detected_key": det_key,
        }

    try:
        y, sr = librosa.load(filepath, sr=None, mono=True)
        duration_sec = round(len(y) / sr, 4) if sr and len(y) > 0 else None

        if len(y) < 256:
            return {
                "estimated_bpm": "oneshot",
                "estimated_key": None,
                "duration_sec": duration_sec,
                "bpm_source": "oneshot",
                "key_source": None,
            }

        # BPM
        detected_bpm = None
        try:
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            # librosa.feature.rhythm.tempo, not librosa.beat.tempo: the latter
            # is a deprecated alias slated for removal in librosa 1.0, and the
            # `except Exception: pass` below would have swallowed the resulting
            # AttributeError -- BPM would have gone silently filename-only while
            # still reporting bpm_source "detected". features.py already uses
            # the new spelling; this was the last caller of the old one.
            tempos_1 = _tempo(librosa, onset_envelope=onset_env, sr=sr, aggregate=None)
            tempos_2 = _tempo(librosa, y=y, sr=sr, aggregate=None)
            all_tempos = []
            if tempos_1.size > 0:
                all_tempos.extend(tempos_1)
            if tempos_2.size > 0:
                all_tempos.extend(tempos_2)
            if all_tempos:
                detected_bpm = round(float(np.median(all_tempos)), 2)
        except Exception:
            pass

        filename_bpm = parse_bpm_from_filename(filepath)
        final_bpm, bpm_source = validate_and_improve_bpm(detected_bpm, filename_bpm)

        # Key. Correlate the piece's pitch-class distribution against the
        # Krumhansl-Schmuckler major + minor profiles (estimate_key_ks): unlike
        # chroma argmax, comparing the distribution *shape* against two templates
        # names the mode (major vs minor), so we can emit a real key from audio.
        # chroma_cqt is more tuning-robust than chroma_stft for key finding.
        # HPSS-separated chroma was tried here (percussion smears the pitch
        # classes, so removing it should help) and measured WORSE on this
        # corpus: 67.4% root accuracy against 71.7%, at 6.5x the cost. CQT's
        # long low-frequency windows already suppress transients, so the
        # separation only costs signal. Left as plain chroma_cqt on purpose.
        detected_key, detected_key_conf = None, 0.0
        try:
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr)         # 12 x frames, bin 0 = C
            profile = [float(x) for x in chroma.mean(axis=1)]
            detail = estimate_key_detailed(profile)
            detected_key = detail["key"]
            # Report the number the gate keys on, not the raw fit. This
            # exported `correlation` while gating on `root_confidence` -- so
            # the confidence a consumer sorted by was the one the code argues
            # against. Measured over 258 answered files: ranking by
            # root_confidence gives 78.4% root accuracy over the top fifth
            # against 72.5% for correlation, and 45.1% over the bottom fifth
            # against 58.8% -- it separates, and correlation barely does.
            detected_key_conf = detail["root_confidence"]
            # gate on the margin over the best *differently-rooted* candidate,
            # not on the raw fit: at equal coverage that ranks 78.0% correct
            # over the top fifth against 62.7% for the correlation
            if detail["root_confidence"] < KEY_MARGIN_MIN:
                detected_key = None
        except Exception:
            pass

        filename_key = parse_key_from_filename(filepath)
        final_key, key_source = improve_key_detection(detected_key, filename_key)

        return {
            "estimated_bpm": final_bpm,
            "estimated_key": final_key,
            "duration_sec": duration_sec,
            "bpm_source": bpm_source,
            "key_source": key_source,
            "filename_bpm": filename_bpm,
            "filename_key": filename_key,
            "detected_bpm": detected_bpm,
            "detected_key": detected_key,
            "detected_key_confidence": detected_key_conf,
        }

    except Exception:
        # librosa could not decode this file. That is a statement about the
        # AUDIO, not about the name -- and the name may still carry the answer.
        #
        # This branch parsed filename_bpm/filename_key and then returned
        # estimated_* = None, so `128_Am.wav` with a corrupt payload reported
        # bpm null while holding 128 in the record it just built. Evidence
        # computed and thrown away. Worse, the sibling ImportError branch a
        # hundred lines up does the right thing, so the file got a BETTER answer
        # with librosa absent than with it installed.
        #
        # parse_key_from_path, not parse_key_from_filename: the folder-aware
        # parser (basename + 3 parents, so `.../Am/loop.wav` resolves) was wired
        # only into the no-librosa branch. Same evidence, two different readers.
        fn_bpm = parse_bpm_from_filename(filepath)
        fn_key = parse_key_from_path(filepath)
        # acidcat's own PCM decoder is not librosa's, and reads WAVs librosa
        # chokes on -- so it is worth one guarded attempt. Returns None safely.
        det_key = _key_without_librosa(filepath)
        return {
            "estimated_bpm": fn_bpm,
            "estimated_key": fn_key or det_key,
            "duration_sec": None,
            # "failed" only where nothing was recovered: the decode failing and
            # the answer being unknown are different facts.
            "bpm_source": "filename" if fn_bpm is not None else "failed",
            "key_source": ("filename" if fn_key else
                           ("audio-numpy" if det_key else "failed")),
            "filename_bpm": fn_bpm,
            "filename_key": fn_key,
            "detected_bpm": None,
            "detected_key": det_key,
        }
