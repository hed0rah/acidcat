"""Provenance: identify the tool chain that wrote a file.

Two classes of evidence, and the distinction matters the same way a repair
witness does -- strong evidence is trusted, weak evidence is labelled as such:

  * explicit tells (confidence "high"): a version string the writer stamped into
    the file -- FLAC/Ogg vendor, MP3 LAME/encoder, WAV ISFT/software, bext
    originator, MP4 encoder atom. Canonicalized to a tool name + version.
  * structural tells (confidence "likely"): a fingerprint in the layout itself,
    read when no string is present or to corroborate one -- e.g. MuseScore's SF3
    writer (Ogg-Vorbis samples, and it omits RIFF pad bytes), or a DAW's
    signature chunk set. These are heuristics, never asserted as fact.

``identify`` returns a de-duplicated list of ``{tool, basis, confidence}``.
"""

import re

# field names (lowercased, as the walkers label them) that hold a writer string.
# deliberately excludes free-text like "comment" -- that catches URLs and notes,
# not tools.
_TELL_FIELDS = {"isft", "software", "encoder", "vendor", "writing_library",
                "originator", "tool", "coding_history"}

# raw-string canonicalization: (regex, template). \1 is the captured version.
_CANON = [
    (re.compile(r"Lav[fc]\s*([\d.]+)", re.I), r"FFmpeg (libav \1)"),
    (re.compile(r"libFLAC\s*([\d.]+)", re.I), r"libFLAC \1 (reference FLAC)"),
    (re.compile(r"\bLAME\s*([\d.a-z]+)", re.I), r"LAME \1"),
    (re.compile(r"libsndfile[- ]?([\d.]+)?", re.I), r"libsndfile \1"),
    (re.compile(r"\bqaac\b", re.I), "qaac (Apple AAC)"),
    (re.compile(r"\bafconvert\b|CoreAudio", re.I), "Apple CoreAudio"),
    (re.compile(r"iTunes\s*([\d.]+)?", re.I), r"iTunes \1"),
    (re.compile(r"Pro ?Tools", re.I), "Avid Pro Tools"),
    (re.compile(r"Adobe (Audition|Premiere|Media)", re.I), r"Adobe \1"),
    (re.compile(r"Audacity\s*([\d.]+)?", re.I), r"Audacity \1"),
    (re.compile(r"REAPER", re.I), "Cockos REAPER"),
    (re.compile(r"Logic Pro|GarageBand", re.I), "Apple Logic / GarageBand"),
    (re.compile(r"Ableton|Live \d", re.I), "Ableton Live"),
    (re.compile(r"FL Studio|Image[- ]?Line|Fruity", re.I), "FL Studio"),
    (re.compile(r"Steinberg|Cubase|Nuendo|WaveLab", re.I), "Steinberg (Cubase/WaveLab)"),
    (re.compile(r"\bNero\b", re.I), "Nero"),
    (re.compile(r"WavePad|\bNCH\b", re.I), "NCH WavePad"),
    (re.compile(r"Sound Forge|SoundForge", re.I), "Sony/Magix Sound Forge"),
    (re.compile(r"reaper|izotope|RX \d", re.I), "iZotope RX"),
]


def _canon(raw):
    raw = raw.strip()
    for rx, tmpl in _CANON:
        m = rx.search(raw)
        if m:
            ver = (m.group(1) or "") if m.lastindex else ""
            return re.sub(r"\s+", " ", tmpl.replace(r"\1", ver)).strip()
    return raw


def _structural(label, chunks, data):
    out = []
    try:
        from acidcat.core import sf2 as sf2mod
        if sf2mod.is_sf2(data):
            info = sf2mod.parse_sf2(data)
            if info.get("sf3"):
                out.append({"tool": "MuseScore sf3convert",
                            "basis": "SF3 structure (Ogg-Vorbis samples, no RIFF padding)",
                            "confidence": "likely"})
    except Exception:
        pass
    return out


def identify(label, chunks, data):
    """Identify the writing tool chain. Returns a list of
    ``{tool, basis, confidence}``, most-confident first, de-duplicated by tool."""
    signals = []
    for c in chunks:
        for f in c.get("fields") or []:
            name = str(f.get("name", ""))
            val = f.get("value")
            if name.lower() in _TELL_FIELDS and val and str(val).strip():
                tool = _canon(str(val))
                if tool:
                    signals.append({"tool": tool, "basis": f"{name} string",
                                    "confidence": "high"})
    signals += _structural(label, chunks, data)

    # de-dup: keep the first (highest-confidence, string tells come first) per tool
    seen, out = set(), []
    order = {"high": 0, "likely": 1, "guess": 2}
    for s in sorted(signals, key=lambda x: order.get(x["confidence"], 3)):
        key = s["tool"].lower()
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out
