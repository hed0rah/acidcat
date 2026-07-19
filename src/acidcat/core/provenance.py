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

import json
import os
import re

# ── signature database (data, not code) ────────────────────────────────────
# The writer signatures live in a JSON sidecar so they can be edited without
# touching this module. A user override at ~/.acidcat/provenance_signatures.json
# is merged on top: its `canon` rules are tried FIRST (a user rule can override a
# built-in first-match), and its `chunk_signatures` are appended (adding tools).

_DATA_FILE = os.path.join(os.path.dirname(__file__), "data",
                          "provenance_signatures.json")
_USER_FILE = os.path.join(os.path.expanduser("~"), ".acidcat",
                          "provenance_signatures.json")

_RE_FLAGS = {"i": re.I, "s": re.S, "m": re.M, "x": re.X}


def _load_db():
    with open(_DATA_FILE, encoding="utf-8") as f:
        db = json.load(f)
    db.setdefault("canon", [])
    db.setdefault("chunk_signatures", [])
    try:
        with open(_USER_FILE, encoding="utf-8") as f:
            user = json.load(f)
        # user canon wins first-match -> prepend; user signatures add -> append
        db["canon"] = list(user.get("canon", [])) + db["canon"]
        db["chunk_signatures"] = db["chunk_signatures"] + list(
            user.get("chunk_signatures", []))
    except (OSError, ValueError):
        pass                                    # no/invalid override: ignore
    return db


def _build_canon(entries):
    out = []
    for e in entries:
        flags = 0
        for ch in e.get("flags", ""):
            flags |= _RE_FLAGS.get(ch, 0)
        try:
            out.append((re.compile(e["pattern"], flags), e["template"]))
        except (re.error, KeyError):
            continue                            # a broken user rule can't crash us
    return out


def _build_signatures(entries):
    out = []
    for e in entries:
        chunks = e.get("chunks") or []
        if chunks and e.get("tool"):
            out.append((set(chunks), e["tool"]))
    return out


_DB = _load_db()

# field names (lowercased, as the walkers label them) that hold a writer string.
# deliberately excludes free-text like "comment" -- that catches URLs and notes,
# not tools.
_TELL_FIELDS = {"isft", "software", "encoder", "vendor", "writing_library",
                "originator", "tool", "coding_history", "tracker",
                "tsse", "tenc", "tss"}   # ID3v2 encoder-settings / encoded-by frames

# tracker `tracker` field values that are format defaults, not app identity --
# only distinctive writers (OpenMPT, MilkyTracker, Sk@le) are a real tell.
_TRACKER_DEFAULTS = {"FastTracker v2.00", "ProTracker", "Scream Tracker 3.00",
                     "Impulse Tracker"}

# WAV INFO IART values that name a recording device, not an artist.
_IART_DEVICES = {"portapack": "HackRF PortaPack (SDR capture)"}

# narrow comment prefixes that actually carry tool identity. comment/icmt is kept
# out of _TELL_FIELDS (it catches URLs and free text); this matches only the
# "made with / Modified by / Recorded ... in <tool>" idiom, first line only.
_COMMENT_TELL = re.compile(
    r"^(?:made with|modified by|recorded[^.\r\n]*\bin)\s+([^\r\n]+)", re.I)

# canonicalization rules (regex, template), built from the sidecar DB. \1 in a
# template is the captured version. First match wins.
_CANON = _build_canon(_DB["canon"])


def _canon(raw):
    raw = raw.strip()
    for rx, tmpl in _CANON:
        m = rx.search(raw)
        if m:
            ver = (m.group(1) or "") if m.lastindex else ""
            return re.sub(r"\s+", " ", tmpl.replace(r"\1", ver)).strip()
    return raw


# signature chunk ids -> the tool that writes them, built from the sidecar DB.
# documented, tool-specific chunks reported at "likely" (a structural tell, not a
# stamp). The DB's `not_signatures` records the deliberately-excluded shared
# chunks (AFAn/AFmd/FLLR/_PMX) with the reasoning for each.
_CHUNK_SIGNATURES = _build_signatures(_DB["chunk_signatures"])


def _chunk_signatures(chunks):
    ids = {str(c.get("id", "")).strip() for c in chunks}
    out = []
    for sig, tool in _CHUNK_SIGNATURES:
        hit = sig & ids
        if hit:
            out.append({"tool": tool, "confidence": "likely",
                        "basis": f"{'/'.join(sorted(hit))} chunk"})
    return out


def _comment_tell(chunks):
    """A tool named in a RIFF ICMT / comment via the narrow 'made with ...' idiom.
    Kept separate from the string-tell scan so free-text comments are not mined."""
    out = []
    for c in chunks:
        for f in c.get("fields") or []:
            if str(f.get("name", "")).lower() not in ("comment", "icmt"):
                continue
            m = _COMMENT_TELL.match(str(f.get("value", "")).strip())
            if m:
                tool = _canon(m.group(1).strip(" ."))
                if tool:
                    out.append({"tool": tool, "basis": "comment tell",
                                "confidence": "likely"})
    return out


def _iart_device(chunks):
    """A recording device named in the WAV INFO IART field (e.g. HackRF PortaPack).
    IART is normally the artist, so only a known device brand set is matched."""
    out = []
    for c in chunks:
        for f in c.get("fields") or []:
            if str(f.get("name", "")).lower() != "iart":
                continue
            dev = _IART_DEVICES.get(str(f.get("value", "")).strip().lower())
            if dev:
                out.append({"tool": dev, "basis": "IART device tell",
                            "confidence": "likely"})
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


def _ffmpeg_rf64_junk(chunks):
    """ffmpeg's RF64_AUTO structural tell: it reserves a 28-byte JUNK chunk
    immediately after `fmt ` (the ds64 placeholder, overwritten in place if the
    file grows past 4 GiB). A plain RIFF/WAVE carrying a 28-byte JUNK right after
    fmt is an ffmpeg (or libav-lineage) signature -- a positional fingerprint, not
    a bare chunk-id match, so it lives here rather than in the signature table.
    Returns a single 'likely' signal or None."""
    ids = [str(c.get("id", "")).strip() for c in chunks]
    try:
        fi = ids.index("fmt")
    except ValueError:
        return None
    nxt = chunks[fi + 1] if fi + 1 < len(chunks) else None
    if (nxt and str(nxt.get("id", "")).strip() == "JUNK"
            and nxt.get("size") == 28):
        return {"tool": "FFmpeg (libav)", "confidence": "likely",
                "basis": "28-byte JUNK ds64 placeholder after fmt (RF64_AUTO)"}
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
    if label == "RIFF/WAVE":            # RF64 already has a real ds64, not a placeholder
        ff = _ffmpeg_rf64_junk(chunks)
        if ff:
            out.append(ff)
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
                if name.lower() == "tracker" and str(val).strip() in _TRACKER_DEFAULTS:
                    continue          # format-default stamp, not the writing app
                tool = _canon(str(val))
                if tool:
                    signals.append({"tool": tool, "basis": f"{name} string",
                                    "confidence": "high"})
    signals += _structural(label, chunks, data)
    signals += _comment_tell(chunks)
    signals += _iart_device(chunks)

    # de-dup: keep the first (highest-confidence, string tells come first) per tool
    seen, out = set(), []
    order = {"high": 0, "likely": 1, "guess": 2}
    for s in sorted(signals, key=lambda x: order.get(x["confidence"], 3)):
        key = s["tool"].lower()
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out
