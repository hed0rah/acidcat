"""What a walked file's nodes can do: play, decode, render.

These are the TUI's own heuristics, moved here unchanged so the contract can
state them as caps (docs/contract/node-v1.md section 8) and every consumer reads
the same answer. Each cap they produce is marked `"source": "inferred"`: it was
worked out from a format label, a chunk id or a field name, not declared by the
walker. Walkers replace them with declared caps one at a time. Since 2.0 the TUI
reads these caps and matches no format names itself.

The rules, as the TUI applied them in 1.8.5:

- render: a SID tune runs its 6510 player; an SPC snapshot runs its SPC700 and
  S-DSP. Decided by the format label (app.py action_play).
- decode: a compressed file ffplay decodes whole (Ogg, Opus, MP3, FLAC, MP4).
  Decided by substrings of the format label (app.py _DECODABLE).
- audio: the chunk whose id says it holds sample data (sniff.AUDIO_SAMPLE_IDS),
  with the rate, channels, bits and float-ness read from `fmt`/`COMM` first,
  else from the first chunk with a `sample_rate` field, clamped to what a real
  header can say (app.py _audio_params / _params_from).
- edit: the metadata editor the write engine has for the file
  (core/write/profiles.py), when `caps` is given the file's first bytes.

File-level caps (render, decode, edit) sit on the first chunk; the TUI reads
them from there for any selection (2.0).

A walker declares a cap by putting it on its chunk, `chunk["caps"] =
{"render": {"engine": "sid"}}`; a declared cap replaces an inferred one of the
same name and is marked `"source": "declared"`.
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


def audio_params(chunks):
    """(rate, channels, bits, float) for playing a walk's bytes as PCM: the
    geometry the file states, else 44100 Hz mono 16-bit."""
    return _audio_params(chunks)[:4]


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


def decodable(head):
    """The sniff id of `head` if a player decodes such bytes whole (a
    compressed stream carved out of something else), else None."""
    from acidcat.core.infra.sniff import sniff_bytes
    try:
        fmt = sniff_bytes(bytes(head))
    except Exception:
        return None
    if fmt and any(k in str(fmt).lower() for k in _DECODABLE):
        return str(fmt)
    return None


# core/write/profiles.py profile name -> the `edit` cap's profile
_EDIT_PROFILE = {"WAV": "wav", "AIFF": "aiff", "tagged": "tagged", "Vital": "vital"}


def ffmpeg_pcm(bits, floating=False, big_endian=False):
    """The codec name ffmpeg gives this PCM layout (node-v1.md section 7:
    `caps.audio.codec` is ffmpeg's vocabulary, so `ffmpeg -f <codec
    minus pcm_>` or `-acodec <codec>` reads the bytes). 8-bit PCM is unsigned
    in the little-endian (RIFF) formats and signed in the big-endian (IFF)
    ones; None for a width ffmpeg has no PCM codec for."""
    if not bits:
        return None
    if floating:
        return "pcm_f%d%s" % (bits, "be" if big_endian else "le") if bits in (32, 64) else None
    if bits == 8:
        return "pcm_s8" if big_endian else "pcm_u8"
    if bits in (16, 24, 32):
        return "pcm_s%d%s" % (bits, "be" if big_endian else "le")
    return None


def _id_of_label(label):
    """The walker id whose label this is, for a caller that has only the
    walk's label (the TUI)."""
    from acidcat.core.walk import _WALKERS
    for fid, (lbl, _fn) in _WALKERS.items():
        if lbl == label:
            return fid
    return None


def _value(chunks, name):
    for c in chunks:
        for f in c.get("fields", []):
            if f.get("name") == name:
                v = f.get("raw", f.get("value"))
                if not isinstance(v, str) or name in ("compression_type", "format_id",
                                                      "format_flags"):
                    return v
    return None


# AU encodings (Sun/NeXT header) -> (ffmpeg codec, bits)
_AU_CODECS = {1: ("pcm_mulaw", 8), 2: ("pcm_s8", 8), 3: ("pcm_s16be", 16),
              4: ("pcm_s24be", 24), 5: ("pcm_s32be", 32), 6: ("pcm_f32be", 32),
              7: ("pcm_f64be", 64), 27: ("pcm_alaw", 8)}


def _pcm_layout(fmt_id, chunks, bits, ch, floating):
    """(codec, bits, channels, float) for a format whose sample data is PCM
    (or G.711) with a layout its header states, else None: an audio cap is a
    claim about how to read the bytes, so a format this cannot state (DSD,
    a MIDI file in a RIFF, a compressed AIFC) gets none."""
    if fmt_id in ("wav", "rf64", "w64", "bw64"):
        codec = ffmpeg_pcm(bits, floating, False)
    elif fmt_id in ("aiff", "aifc"):
        comp = str(_value(chunks, "compression_type") or "NONE").strip()
        if comp in ("NONE", "twos"):
            codec = ffmpeg_pcm(bits, False, True)
        elif comp == "sowt":
            codec = "pcm_s8" if bits == 8 else ffmpeg_pcm(bits, False, False)
        elif comp.lower() in ("fl32", "fl64"):
            bits, floating = (32 if comp.lower() == "fl32" else 64), True
            codec = ffmpeg_pcm(bits, True, True)
        else:
            return None
    elif fmt_id == "8svx":
        if _value(chunks, "sCompression") not in (0, None):
            return None
        codec, bits = "pcm_s8", 8
    elif fmt_id == "au":
        got = _AU_CODECS.get(_value(chunks, "encoding"))
        if got is None:
            return None
        codec, bits = got
        floating = codec.startswith("pcm_f")
    elif fmt_id == "caf":
        if str(_value(chunks, "format_id") or "").strip() != "lpcm":
            return None
        try:
            flags = int(str(_value(chunks, "format_flags") or "0"), 0)
        except ValueError:
            return None
        bits = _value(chunks, "bits_per_channel") or bits
        ch = _value(chunks, "channels_per_frame") or ch
        floating = bool(flags & 1)
        codec = ffmpeg_pcm(bits, floating, not flags & 2)
    else:
        return None
    return (codec, bits, ch, floating) if codec else None


def caps(fmt_id, label, chunks, head=None, name=None):
    """{chunk index: caps} for a walk's chunk list. Audio caps name their
    source fields as (chunk index, field name); the normaliser turns those into
    field addresses once node ids exist. Given the file's first bytes (`head`)
    and `name`, the file's metadata editor is an `edit` cap."""
    out = {}
    lab = (label or "").lower()
    if not chunks:
        return out
    if fmt_id is None:
        fmt_id = _id_of_label(label)
    if head is not None:
        from acidcat.core.write.profiles import profile_for
        prof = profile_for(bytes(head[:16]), name)
        if prof is not None and prof[0] in _EDIT_PROFILE:
            out.setdefault(0, {})["edit"] = {"profile": _EDIT_PROFILE[prof[0]],
                                             "source": "inferred"}
    for needle, engine in _RENDER:
        if needle in lab:
            out.setdefault(0, {})["render"] = {"engine": engine, "source": "inferred"}
    if any(k in lab for k in _DECODABLE):
        out.setdefault(0, {})["decode"] = {"format": fmt_id or lab, "source": "inferred"}
    audio = [i for i, c in enumerate(chunks)
             if str(c.get("id", "")).strip() in AUDIO_SAMPLE_IDS]
    if audio:
        rate, ch, bits, floating, src, used = _audio_params(chunks)
        layout = _pcm_layout(fmt_id, chunks, bits, ch, floating)
        audio = audio if layout is not None else []
        codec, bits, ch, floating = layout or (None, bits, ch, floating)
        for i in audio:
            cap = {"codec": codec,
                   "rate": rate, "channels": ch, "bits": bits,
                   "source": "inferred",
                   "fields": [(src, n) for n in used] if src is not None else []}
            if floating:
                cap["float"] = True
            out.setdefault(i, {})["audio"] = cap
    # what a walker declares on a chunk wins over what was inferred for it
    for i, c in enumerate(chunks):
        for name, payload in (c.get("caps") or {}).items():
            if isinstance(payload, dict):
                out.setdefault(i, {})[name] = dict(payload, source="declared")
    return out
