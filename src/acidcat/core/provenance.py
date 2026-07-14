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
                "originator", "tool", "coding_history",
                "tsse", "tenc", "tss"}   # ID3v2 encoder-settings / encoded-by frames

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
    (re.compile(r"Digital Performer", re.I), "MOTU Digital Performer"),
    (re.compile(r"Ableton|Live \d", re.I), "Ableton Live"),
    (re.compile(r"FL Studio|Image[- ]?Line|Fruity", re.I), "FL Studio"),
    (re.compile(r"\bEdison\b", re.I), "Image-Line Edison (FL Studio)"),
    (re.compile(r"Steinberg|Cubase|Nuendo|WaveLab", re.I), "Steinberg (Cubase/WaveLab)"),
    (re.compile(r"\bNero\b", re.I), "Nero"),
    (re.compile(r"WavePad|\bNCH\b", re.I), "NCH WavePad"),
    (re.compile(r"Sound Forge|SoundForge", re.I), "Sony/Magix Sound Forge"),
    (re.compile(r"iZotope|RX \d", re.I), "iZotope RX"),
    # rip / convert / library tools (explicit strings)
    (re.compile(r"Exact Audio Copy|\bEAC\b", re.I), "Exact Audio Copy"),
    (re.compile(r"dBpoweramp|dbpa", re.I), "dBpoweramp"),
    (re.compile(r"\bXLD\b|X Lossless", re.I), "XLD (X Lossless Decoder)"),
    (re.compile(r"foobar2000", re.I), "foobar2000"),
    (re.compile(r"fre:ac|freac", re.I), "fre:ac"),
    (re.compile(r"\bMax\b \d|MediaHuman", re.I), "MediaHuman / Max"),
    (re.compile(r"\bsox\b", re.I), "SoX"),
    (re.compile(r"GoldWave", re.I), "GoldWave"),
    (re.compile(r"Ocenaudio", re.I), "Ocenaudio"),
    (re.compile(r"Twisted Wave|TwistedWave", re.I), "TwistedWave"),
    (re.compile(r"Serato", re.I), "Serato"),
    (re.compile(r"Traktor", re.I), "Native Instruments Traktor"),
]


def _canon(raw):
    raw = raw.strip()
    for rx, tmpl in _CANON:
        m = rx.search(raw)
        if m:
            ver = (m.group(1) or "") if m.lastindex else ""
            return re.sub(r"\s+", " ", tmpl.replace(r"\1", ver)).strip()
    return raw


# signature chunk ids -> the tool that writes them. these are documented,
# tool-specific chunks, reported at "likely" (a structural tell, not a stamp).
_CHUNK_SIGNATURES = [
    ({"regn", "minf", "elm1"}, "Avid Pro Tools"),
    ({"DGDA"}, "Avid Pro Tools (Digidesign)"),
    ({"LGWV"}, "Apple Logic Pro"),
    # corpus: ResU is overwhelmingly Logic (288/303), not Steinberg as the web
    # research guessed -- do not reassign to Steinberg without a corroborating tell.
    ({"ResU"}, "Apple Logic Pro"),
    ({"dprn", "dpte", "dpas", "dpam"}, "MOTU Digital Performer"),
    ({"BWBM"}, "Bitwig Studio"),
    ({"SMED"}, "Steinberg (Cubase/Nuendo/WaveLab)"),
    ({"AFsp"}, "AFsp audio library (SoX / afconvert lineage)"),
    ({"umid"}, "a broadcast/production tool (SMPTE UMID)"),
]
# deliberately NOT signatures: AFAn/AFmd (shared macOS CoreAudio -- Logic AND Digital
# Performer AND generic Mac renders) and FLLR (padding shared with Apple APIs). Too
# ambiguous to attribute to one app; use only to corroborate a string tell.


def _chunk_signatures(chunks):
    ids = {str(c.get("id", "")).strip() for c in chunks}
    out = []
    for sig, tool in _CHUNK_SIGNATURES:
        hit = sig & ids
        if hit:
            out.append({"tool": tool, "confidence": "likely",
                        "basis": f"{'/'.join(sorted(hit))} chunk"})
    return out


def _mp3_lame(chunks):
    """Enrich a LAME MP3 with its encode settings from the Xing/LAME tag: the
    VBR method, lowpass, bitrate/quality. Turns 'LAME 3.100' into a detailed
    encode signature. Returns a single high-confidence signal, or None."""
    for c in chunks:
        fields = {str(f.get("name", "")): f for f in (c.get("fields") or [])}
        enc = fields.get("encoder")
        if not enc:
            continue
        v = str(enc.get("value", ""))
        if not v.upper().startswith(("LAME", "L3.9", "GOGO")):
            continue
        parts = []
        vbr = fields.get("vbr_method")
        if vbr and vbr.get("note"):
            parts.append(str(vbr["note"]))
        lp = fields.get("lowpass")
        if lp and lp.get("value"):
            parts.append(f"lowpass {lp['value']}")
        br = fields.get("bitrate")
        if br and br.get("value") and not str(br["value"]).startswith("0 "):
            parts.append(str(br["value"]))
        tool = _canon(v)
        if parts:
            tool += " (" + ", ".join(parts) + ")"
        return {"tool": tool, "basis": "LAME tag", "confidence": "high"}
    return None


def _structural(label, chunks, data):
    out = []
    if "MP3" in label or "MPEG" in label:
        lame = _mp3_lame(chunks)
        if lame:
            out.append(lame)
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
    if label in ("RIFF/WAVE", "RF64/WAVE", "IFF/AIFF", "IFF/AIFC"):
        out += _chunk_signatures(chunks)
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
                # LAME is enriched with its tag detail in _structural; skip the
                # bare version string here so it is not listed twice
                if name.lower() == "encoder" and \
                        str(val).upper().startswith(("LAME", "L3.9", "GOGO")):
                    continue
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
