"""The transform lens -- find audio hiding under a simple reversible obfuscation.

CTF challenges (and the odd game asset) scramble PCM with a cheap reversible op:
XOR with a byte key, bit-rotate, nibble-swap. These are byte permutations, so they
PRESERVE entropy but SCRAMBLE autocorrelation -- which is exactly why XOR'd audio
slips past the statistical detector (it stays moderate-entropy but looks
uncorrelated). The lens un-applies each candidate transform to a window and asks
"does it become audio now?", flagging e.g. "region at 0x4000 is audio under XOR 0x5A".

The reported key is a CANDIDATE, not gospel: audio is smooth, so un-XOR by the
true key and by its bit-inverted twin (K ^ 0xFF) leave equally smooth waveforms,
and the low bits are dither-level -- polarity and the bottom bits cannot be pinned
from smoothness alone. What the lens gives you reliably is the region and the key's
high-bit neighbourhood; you refine and listen from there.

It is deliberately gated: transforms are tried only on the *suspicious* windows
(moderate entropy, not already audio, not pure random), so it stays fast, and it
reads at most `read_cap` -- a focused tool, not a whole-disk sweep.
"""

from acidcat.core.audioscan import (DEFAULT_STEP, DEFAULT_WINDOW,
                                    audio_score, window_features)

_READ_CAP = 16 * 1024 * 1024
_MIN_SCORE = 0.40                # a transformed window must score at least this
_MIN_RUN = 6                     # ...across this many windows of the same key
_GAP = 5                         # bridge up to this many below-threshold windows
_HOLD = 0.6                      # the refined key must recover this fraction of a region

# only transforms that actually HIDE audio (scramble autocorrelation) are worth
# trying. reverse and add-constant leave a waveform a waveform, so the plain
# detector already finds them -- including them here would only add false hits.


def _apply(win, kind, param):
    if kind == "xor":
        return bytes(b ^ param for b in win)
    if kind == "nibble":
        return bytes(((b << 4) | (b >> 4)) & 0xFF for b in win)
    if kind == "rot":
        return bytes(((b << param) | (b >> (8 - param))) & 0xFF for b in win)
    return win


def _candidates():
    for k in range(1, 256):
        yield "xor", k
    yield "nibble", 0
    for r in range(1, 8):
        yield "rot", r


def _label(kind, param):
    return {"xor": f"xor:0x{param:02x}", "nibble": "nibble-swap",
            "rot": f"rot:{param}"}[kind]


def _roughness(seg):
    """Mean absolute first-difference of the signed-8 reading -- the smoothness
    proxy that ranks a candidate key: real audio is smooth, so the true key
    minimises this."""
    if len(seg) < 2:
        return 1e9
    prev = seg[0] - 256 if seg[0] > 127 else seg[0]
    total = 0
    for b in seg[1:]:
        v = b - 256 if b > 127 else b
        total += abs(v - prev)
        prev = v
    return total / (len(seg) - 1)


def _refine_key(seg, kind, cap=4096):
    """Pick the transform parameter that leaves the smoothest waveform over a
    detected region. XOR/rot have a param to pin (up to the inherent polarity and
    low-bit ambiguity of audio); nibble-swap has none."""
    seg = seg[:cap]
    if kind == "xor":
        return min(range(256), key=lambda k: _roughness(bytes(b ^ k for b in seg)))
    if kind == "rot":
        return min(range(1, 8), key=lambda r: _roughness(_apply(seg, "rot", r)))
    return 0


def find_transformed_audio(data, window=DEFAULT_WINDOW, step=DEFAULT_STEP,
                           min_score=_MIN_SCORE, min_run=_MIN_RUN, read_cap=_READ_CAP):
    """Find runs of windows that become audio under *some* reversible transform,
    then pin one key per region. Detection is family-level (is there audio hidden
    here at all?) so a single stream is not fragmented across the true key's low-bit
    neighbours; the exact key is then refined by smoothness. The reported key is a
    CANDIDATE -- polarity (K vs K^0xFF) and the low bits are not recoverable from
    audio smoothness alone. Returns records (kind='transformed', transform='xor:0x5a')."""
    from collections import Counter
    if read_cap and len(data) > read_cap:
        data = data[:read_cap]
    n = len(data)

    # per suspicious window: the best-scoring transform family, if any recovers audio
    hidden = []                                          # (offset, family, score)
    off, last = 0, n - window
    while off <= last:
        win = data[off:off + window]
        feat = window_features(win)
        if audio_score(feat) >= 0.25 or feat["entropy"] > 7.7 or feat["entropy"] < 2.0:
            off += step
            continue
        best = None
        for kind, param in _candidates():
            score = audio_score(window_features(_apply(win, kind, param)))
            if score >= min_score and (best is None or score > best[1]):
                best = (kind, score)
        if best:
            hidden.append((off, best[0], best[1]))
        off += step

    # merge consecutive hidden-audio windows (bridging brief dips) into regions --
    # detection ignores which exact key won, so the stream stays whole
    runs, run = [], None
    for hoff, family, score in hidden:
        if run and hoff - run["last"] <= step * _GAP:
            run["end"], run["last"], run["n"] = hoff + window, hoff, run["n"] + 1
            run["fam"].append(family)
            run["score"] = max(run["score"], score)
        else:
            if run and run["n"] >= min_run:
                runs.append(run)
            run = {"start": hoff, "end": hoff + window, "last": hoff,
                   "n": 1, "fam": [family], "score": score}
    if run and run["n"] >= min_run:
        runs.append(run)

    out = []
    for r in runs:
        family = Counter(r["fam"]).most_common(1)[0][0]   # dominant family in the run
        param = _refine_key(data[r["start"]:r["end"]], family)
        # validate: does this ONE refined key recover audio across the region? real
        # hidden audio holds under a single key; a chance-transform FP (code, packed
        # data) is a different key every window and fails this.
        hold = tot = 0
        for wo in range(r["start"], r["end"] - window + 1, step):
            tot += 1
            if audio_score(window_features(_apply(data[wo:wo + window], family, param))) >= min_score:
                hold += 1
        if not tot or hold / tot < _HOLD:
            continue
        out.append({
            "kind": "transformed", "format": None, "transform": _label(family, param),
            "offset": r["start"], "end": r["end"], "length": r["end"] - r["start"],
            "confidence": round(r["score"], 2), "inspectable": False, "evidence": None,
        })
    return out
