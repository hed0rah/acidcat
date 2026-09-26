"""CMF walker: the header, the text strings, the OPL2 patches, the track.

The event stream is SMF track syntax, so the MIDI walker's track scanner
reads it: notes, channels, tempo, End of Track. The instruments are the
part MIDI does not have, sixteen bytes of OPL2 registers each, and each
is a chunk of its own so a patch can be carved.
"""


from acidcat.core.formats import cmf as cmfmod
from acidcat.core.primitives.notes import coverage
from acidcat.core.walk.base import Unsupported as _Unsupported, _open, _size
from acidcat.core.walk.base import _f
from acidcat.core.walk.midi import _scan_track

# The offsets are 16-bit; nothing past 64 KB is reachable by the header.
_CMF_READ_CAP = 64 * 1024
# Instruments listed as chunks. The count field is 16-bit; real files have
# a dozen.
_CMF_INSTRUMENT_LIST_CAP = 128


def inspect_cmf(filepath, deep=False):
    size = _size(filepath)
    with _open(filepath) as fh:
        raw = fh.read(min(size, _CMF_READ_CAP))
    warns = []
    if size > _CMF_READ_CAP:
        warns.append(coverage("file is %d bytes; parsed the first %d, which is all "
                              "a 16-bit offset can reach" % (size, _CMF_READ_CAP)))
    h = cmfmod.parse_header(raw, len(raw))
    if not h["ok"]:
        raise _Unsupported(h["why"])

    scan = _scan_track(raw[h["music_at"]:], {"division": h["ticks_per_quarter"]})
    music_end = h["music_at"] + scan["eot_end"] if scan["eot_end"] is not None else len(raw)
    if not scan["has_eot"]:
        warns.append("the event stream has no End of Track")

    fields = [
        _f(0x00, 4, "magic", "CTMF"),
        _f(0x04, 2, "version", h["version_text"], "BCD"),
        _f(0x06, 2, "instruments_at", "0x%04X" % h["instruments_at"]),
        _f(0x08, 2, "music_at", "0x%04X" % h["music_at"]),
        _f(0x0A, 2, "ticks_per_quarter", h["ticks_per_quarter"]),
        _f(0x0C, 2, "ticks_per_second", h["ticks_per_second"], "the timer rate"),
        _f(0x0E, 2, "title_at", "0x%04X" % h["title_at"] if h["title_at"] else "none"),
        _f(0x10, 2, "composer_at", "0x%04X" % h["composer_at"] if h["composer_at"] else "none"),
        _f(0x12, 2, "remarks_at", "0x%04X" % h["remarks_at"] if h["remarks_at"] else "none"),
        _f(0x14, 16, "channels_used",
           " ".join(str(c + 1) for c in h["channels_used"]) or "none flagged",
           "one byte per MIDI channel, non-zero means used"),
    ]
    if h["version"] >= 0x101:
        fields.append(_f(0x24, 2, "instrument_count", h["instrument_count"]))
        fields.append(_f(0x26, 2, "tempo", "%d BPM" % h["tempo"]))
    else:
        fields.append(_f(None, 0, "instrument_count", h["instrument_count"],
                         "1.0 has no count; this is what fits between the offsets"))
    texts = {}
    for key in ("title", "composer", "remarks"):
        at = h[key + "_at"]
        if not at:
            continue
        if at >= len(raw) or h["music_at"] <= at < music_end:
            # one real file points its title and composer into the event
            # stream; a string read from there is not a string
            warns.append("the %s offset 0x%X lands %s" % (
                key, at, "past the end of the file" if at >= len(raw) else "inside the music"))
            continue
        text, n = cmfmod.cstring(raw, at)
        texts[key] = (at, n, text)
        fields.append(_f(None, 0, key, text or "(empty)", "", xref=at))
    title = texts.get("title", (0, 0, ""))[2]
    by = texts.get("composer", (0, 0, ""))[2]
    chunks = [{"id": "header", "offset": 0, "size": h["header_size"],
               "summary": "CMF %s, %d instruments, %d notes, %s%s" % (
                   h["version_text"], h["instrument_count"], scan["notes"],
                   ("%d BPM" % h["tempo"]) if h["tempo"] else
                   "%d ticks/s" % h["ticks_per_second"],
                   (" -- " + title + (" (" + by + ")" if by else "")) if title else ""),
               "fields": fields, "warnings": [], "payload_base": 0}]
    regions = []
    for key, (at, n, text) in texts.items():
        regions.append({"id": key, "offset": at, "size": n,
                        "summary": text or "(empty)",
                        "fields": [_f(0, n, "text", text, "NUL-terminated")],
                        "warnings": [], "payload_base": at})
    listed = 0
    for i in range(h["instrument_count"]):
        at = h["instruments_at"] + i * cmfmod.INSTRUMENT
        if at + cmfmod.INSTRUMENT > h["music_at"]:
            warns.append("instrument %d would overlap the music; %d fit" % (i, i))
            break
        if listed >= _CMF_INSTRUMENT_LIST_CAP:
            warns.append(coverage("listing the first %d of %d instruments"
                                  % (_CMF_INSTRUMENT_LIST_CAP, h["instrument_count"])))
            break
        listed += 1
        ins = cmfmod.instrument(raw, at)
        if ins is None:
            break
        conn = "additive" if ins["feedback_conn"] & 1 else "FM"
        regions.append({"id": "inst[%d]" % i, "offset": at, "size": cmfmod.INSTRUMENT,
                        "summary": "OPL2 patch, %s, feedback %d, waves %d/%d" % (
                            conn, (ins["feedback_conn"] >> 1) & 7,
                            ins["modulator"]["waveform"] & 3, ins["carrier"]["waveform"] & 3),
                        "fields": [
                            _f(0, 10, "operators", "modulator/carrier: " + ", ".join(
                                "%02X/%02X" % (ins["modulator"][k], ins["carrier"][k])
                                for k in ("ave", "ksl_level", "attack_decay",
                                          "sustain_release", "waveform")),
                               "AM-VIB-EG-KSR-mult, KSL-level, AD, SR, wave; interleaved"),
                            _f(10, 1, "feedback_connection", "0x%02X" % ins["feedback_conn"], conn),
                            _f(11, 5, "reserved", ins["reserved"].hex())],
                        "warnings": [], "payload_base": at})
    track = {"id": "music", "offset": h["music_at"], "size": music_end - h["music_at"],
             "summary": "%s events, %d notes on %d channel%s, %s ticks" % (
                 format(scan["n_events"] or 0, ","), scan["notes"], len(scan["channels"]),
                 "" if len(scan["channels"]) == 1 else "s", format(scan["ticks"], ",")),
             "fields": [_f(None, 0, "notes", scan["notes"]),
                        _f(None, 0, "channels", " ".join(str(c + 1) for c in sorted(scan["channels"])) or "none"),
                        _f(None, 0, "ticks", scan["ticks"],
                           "%.1f s at %d ticks/s" % (scan["ticks"] / h["ticks_per_second"], h["ticks_per_second"])
                           if h["ticks_per_second"] else "")],
             "warnings": [], "payload_base": h["music_at"]}
    if scan["tempos"]:
        track["fields"].append(_f(None, 0, "tempo_events", ", ".join("%g" % t for t in scan["tempos"][:6])))
    regions.append(track)
    regions.sort(key=lambda c: c["offset"])
    pos = h["header_size"]
    for r in regions:
        if r["offset"] > pos:
            chunks.append(_gap(raw, pos, r["offset"] - pos))
        if r["offset"] < pos:
            r["warnings"].append("overlaps the region before it")
        chunks.append(r)
        pos = max(pos, r["offset"] + r["size"])
    if pos < len(raw):
        tail = raw[pos:]
        if tail == b"\xff":
            # 372 of 459 real files end with one 0xFF after End of Track:
            # the writer's own terminator, past the track's
            chunks.append({"id": "terminator", "offset": pos, "size": 1,
                           "summary": "a 0xFF after End of Track, as the writer left it",
                           "fields": [_f(0, 1, "byte", "0xFF")],
                           "warnings": [], "payload_base": pos})
        else:
            chunks.append(_gap(raw, pos, len(raw) - pos))
    return chunks, warns


def _gap(raw, at, n):
    zero = not any(raw[at:at + n])
    return {"id": "padding" if zero else "unwalked", "offset": at, "size": n,
            "summary": ("%d bytes of zero" % n) if zero else
                       "%d bytes no offset reaches" % n,
            "fields": [], "warnings": [], "payload_base": at}
