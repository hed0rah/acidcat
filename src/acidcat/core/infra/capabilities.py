"""What a walked file's nodes can do: play, decode, render.

These are the TUI's own heuristics, moved here unchanged so the contract can
state them as caps (docs/contract/node-v1.md section 8) and every consumer reads
the same answer. Each cap they produce is marked `"source": "inferred"`: it was
worked out from a format label, a chunk id or a field name, not declared by the
walker. Walkers replace them with declared caps one at a time, and the TUI stops
matching strings when it reads caps instead (2.2).

The rules, as the TUI applied them in 1.8.5:

- render: a SID tune runs its 6510 player; an SPC snapshot runs its SPC700 and
  S-DSP. Decided by the format label (app.py action_play).
- decode: a compressed file ffplay decodes whole (Ogg, Opus, MP3, FLAC, MP4).
  Decided by substrings of the format label (app.py _DECODABLE).
- audio: the chunk whose id says it holds sample data (sniff.AUDIO_SAMPLE_IDS),
  with the rate, channels, bits and float-ness read from `fmt`/`COMM` first,
  else from the first chunk with a `sample_rate` field, clamped to what a real
  header can say (app.py _audio_params / _params_from).
"""

from acidcat.core.infra import fieldcodec
from acidcat.core.infra.sniff import AUDIO_SAMPLE_IDS

# app.py action_play: the two formats with an in-house engine
_RENDER = (("sid tune", "sid"), ("spc700 sound snapshot", "spc"))

# app.py _DECODABLE: formats ffplay decodes whole
_DECODABLE = ("ogg", "opus", "mp3", "flac", "m4a", "mp4", "vorbis", "oga")

# app.py _params_from: bounds a real header stays inside
_RATE_RANGE = (1000, 768000)
_CH_RANGE = (1, 64)
_BITS_VALID = (8, 16, 24, 32, 64)


def prefers_be(fmt_id, label):
    """Whether this format's multi-byte fields are big-endian, as the TUI's
    editor decides it (fieldcodec._BE_FMTS, keyed on the label until the
    format registry keys it on the id)."""
    return label in fieldcodec._BE_FMTS


def _params_from(chunk, rate, ch, bits, floating, used):
    for f in chunk.get("fields", []):
        n, v = f.get("name", ""), f.get("raw", f.get("value"))
        try:
            if n == "sample_rate":
                lo, hi = _RATE_RANGE
                if lo <= int(v) <= hi:
                    rate = int(v)
                    used.append(n)
            elif n in ("channels", "num_channels"):
                lo, hi = _CH_RANGE
                if lo <= int(v) <= hi:
                    ch = int(v)
                    used.append(n)
            elif n == "bits_per_sample":
                if int(v) in _BITS_VALID:
                    bits = int(v)
                    used.append(n)
            elif n == "format_tag" and "float" in str(f.get("note", "")).lower():
                floating = True
                used.append(n)
        except (ValueError, TypeError):
            pass
    return rate, ch, bits, floating


def _audio_params(chunks):
    """(rate, ch, bits, float, source chunk index, field names used)."""
    rate, ch, bits, floating = 44100, 1, 16, False
    for i, c in enumerate(chunks):
        if str(c.get("id", "")).strip() in ("fmt", "COMM"):
            used = []
            return _params_from(c, rate, ch, bits, floating, used) + (i, used)
    for i, c in enumerate(chunks):
        if any(f.get("name") == "sample_rate" for f in c.get("fields", [])):
            used = []
            return _params_from(c, rate, ch, bits, floating, used) + (i, used)
    return rate, ch, bits, floating, None, []


def caps(fmt_id, label, chunks):
    """{chunk index: caps} for a walk's chunk list. Audio caps name their
    source fields as (chunk index, field name); the normaliser turns those into
    field addresses once node ids exist."""
    out = {}
    lab = (label or "").lower()
    if not chunks:
        return out
    for needle, engine in _RENDER:
        if needle in lab:
            out.setdefault(0, {})["render"] = {"engine": engine, "source": "inferred"}
    if any(k in lab for k in _DECODABLE):
        out.setdefault(0, {})["decode"] = {"format": fmt_id or lab, "source": "inferred"}
    audio = [i for i, c in enumerate(chunks)
             if str(c.get("id", "")).strip() in AUDIO_SAMPLE_IDS]
    if audio:
        rate, ch, bits, floating, src, used = _audio_params(chunks)
        for i in audio:
            cap = {"codec": ("pcm_f" if floating else "pcm_s") + str(bits),
                   "rate": rate, "channels": ch, "bits": bits,
                   "source": "inferred",
                   "fields": [(src, n) for n in used] if src is not None else []}
            if floating:
                cap["float"] = True
            out.setdefault(i, {})["audio"] = cap
    return out
