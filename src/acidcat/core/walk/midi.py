"""Standard MIDI File structural walker: MThd/MTrk decoding with
per-track stats and the optional --frames per-event listing. Mirrors
the event grammar in core/midi.py."""

import os
import struct

from acidcat.core import midi as midimod
from acidcat.core.midi import _read_vlq
from acidcat.core.walk.base import _FRAME_LISTING_CAP, _dtext, _f
from acidcat.util.midi import midi_note_to_name

_MIDI_FORMATS = {0: "single track", 1: "multi-track sync", 2: "independent patterns"}

_META_NAMES = {
    0x00: "sequence number", 0x01: "text", 0x02: "copyright",
    0x03: "track name", 0x04: "instrument", 0x05: "lyric", 0x06: "marker",
    0x07: "cue point", 0x08: "program name", 0x09: "device name",
    0x20: "channel prefix", 0x21: "port", 0x2F: "end of track",
    0x51: "tempo", 0x54: "smpte offset", 0x58: "time sig", 0x59: "key sig",
    0x7F: "sequencer-specific",
}

_VOICE_NAMES = {
    0x80: "note off", 0x90: "note on", 0xA0: "poly aftertouch",
    0xB0: "control change", 0xC0: "program change",
    0xD0: "channel aftertouch", 0xE0: "pitch bend",
}


def _scan_track(trk, ctx, collect=False):
    """Collect display facts from one MTrk payload. Mirrors the event
    grammar in core/midi.py but keeps per-track stats. With ``collect``,
    also returns a per-event row list under the ``events`` key."""
    pos = 0
    running = 0
    ticks = 0
    notes = 0
    nmin = nmax = None
    channels = set()
    tempos = []
    names = []
    time_sig = key_sig = None
    has_eot = False
    events = []
    sysex = []

    def emit(name, detail=""):
        if collect:
            events.append({"tick": ticks, "event": name, "detail": detail})

    while pos < len(trk):
        delta, pos = _read_vlq(trk, pos)
        ticks += delta
        if pos >= len(trk):
            break
        status = trk[pos]
        if status == 0xFF:
            running = 0
            if pos + 2 >= len(trk):
                break
            etype = trk[pos + 1]
            elen, pos = _read_vlq(trk, pos + 2)
            if pos + elen > len(trk):
                break
            edata = trk[pos:pos + elen]
            pos += elen
            detail = ""
            if etype == 0x51 and elen == 3:
                us = (edata[0] << 16) | (edata[1] << 8) | edata[2]
                if us:
                    tempos.append(round(60_000_000 / us, 2))
                    detail = f"{round(60_000_000 / us, 2):g} bpm"
            elif etype == 0x58 and elen == 4:
                time_sig = f"{edata[0]}/{2 ** edata[1]}"
                detail = time_sig
            elif etype == 0x59 and elen == 2:
                sf = struct.unpack(">b", edata[0:1])[0]
                key_sig = f"{sf:+d} {'sharps' if sf >= 0 else 'flats'}" \
                          + (", minor" if edata[1] == 1 else "")
                detail = key_sig
            elif etype == 0x54 and elen == 5:
                # SMPTE offset: hr byte top bits carry the frame rate.
                hr = edata[0]
                fps = {0: 24, 1: 25, 2: 29.97, 3: 30}.get((hr >> 5) & 0x03, "?")
                detail = (f"{hr & 0x1F:02d}:{edata[1]:02d}:{edata[2]:02d}:"
                          f"{edata[3]:02d}.{edata[4]:02d} @ {fps} fps")
            elif etype in (0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07):
                text = _dtext(edata).strip()
                if etype == 0x03 and text:
                    names.append(text)
                detail = text[:48]
            elif etype == 0x2F:
                has_eot = True
            emit("meta " + _META_NAMES.get(etype, f"0x{etype:02x}"), detail)
        elif status in (0xF0, 0xF7):
            running = 0
            slen, dpos = _read_vlq(trk, pos + 1)
            if dpos + slen > len(trk):
                break
            body = trk[dpos:dpos + slen]
            pos = dpos + slen
            mfr = _sysex_mfr(body)
            emit("sysex", f"{mfr}, {slen} bytes")
            sysex.append((mfr, slen, bool(body) and body[0] == 0x7D))
        elif status & 0x80:
            running = status
            pos += 1
            mtype = status & 0xF0
            ch = status & 0x0F
            if mtype in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                if pos + 1 < len(trk):
                    d1, d2 = trk[pos], trk[pos + 1]
                    pos += 2
                    if mtype == 0x90 and d2 > 0:
                        notes += 1
                        channels.add(ch)
                        nmin = d1 if nmin is None else min(nmin, d1)
                        nmax = d1 if nmax is None else max(nmax, d1)
                    emit(_VOICE_NAMES[mtype], _voice_detail(mtype, d1, d2, ch))
                else:
                    break
            elif mtype in (0xC0, 0xD0):
                d1 = trk[pos] if pos < len(trk) else 0
                pos += 1
                emit(_VOICE_NAMES[mtype], f"{d1} ch{ch + 1}")
            else:
                pos += 2
        elif running:
            mtype = running & 0xF0
            ch = running & 0x0F
            d1 = status
            if mtype in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                if pos + 1 < len(trk):
                    d2 = trk[pos + 1]
                    pos += 2
                    if mtype == 0x90 and d2 > 0:
                        notes += 1
                        channels.add(ch)
                        nmin = d1 if nmin is None else min(nmin, d1)
                        nmax = d1 if nmax is None else max(nmax, d1)
                    emit(_VOICE_NAMES[mtype], _voice_detail(mtype, d1, d2, ch))
                else:
                    break
            else:
                pos += 1
                emit(_VOICE_NAMES.get(mtype, "voice"), f"{d1} ch{ch + 1}")
        else:
            pos += 1

    return {"ticks": ticks, "notes": notes, "nmin": nmin, "nmax": nmax,
            "channels": channels, "tempos": tempos, "names": names,
            "time_sig": time_sig, "key_sig": key_sig, "has_eot": has_eot,
            "events": events, "sysex": sysex}


# a few common MIDI manufacturer ids (System Exclusive id table); enough to name
# the usual suspects. 0x7d/0x7e/0x7f are the reserved/universal ids.
_MFR = {0x40: "Kawai", 0x41: "Roland", 0x42: "Korg", 0x43: "Yamaha",
        0x44: "Casio", 0x47: "Akai", 0x00: "extended-id"}


def _sysex_mfr(body):
    """Name the SysEx manufacturer from the id byte(s) after F0."""
    if not body:
        return "empty"
    b0 = body[0]
    if b0 == 0x7D:
        return "non-commercial (0x7D)"
    if b0 == 0x7E:
        return "universal non-realtime"
    if b0 == 0x7F:
        return "universal realtime"
    if b0 == 0x00 and len(body) >= 3:
        return f"ext id {body[0]:02X} {body[1]:02X} {body[2]:02X}"
    return _MFR.get(b0, f"mfr 0x{b0:02X}")


def _voice_detail(mtype, d1, d2, ch):
    """Human-readable detail for a channel-voice event."""
    if mtype in (0x80, 0x90):
        verb = "off" if (mtype == 0x80 or d2 == 0) else f"v{d2}"
        return f"{midi_note_to_name(d1)} {verb} ch{ch + 1}"
    if mtype == 0xA0:
        return f"{midi_note_to_name(d1)} {d2} ch{ch + 1}"
    if mtype == 0xB0:
        return f"cc{d1}={d2} ch{ch + 1}"
    if mtype == 0xE0:
        return f"{((d2 << 7) | d1) - 8192:+d} ch{ch + 1}"
    return f"{d1} {d2} ch{ch + 1}"


def inspect_midi(filepath, deep=False):
    """Walk a Standard MIDI File and return (chunks, file_warnings).
    With ``deep``, each MTrk carries a per-event listing."""
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read(midimod.MAX_SMF_BYTES)
    chunks = []
    file_warns = []
    if file_size > len(data):
        file_warns.append(
            f"file is {file_size:,} bytes; parsed the first "
            f"{len(data):,} (cap)")
        file_size = len(data)

    hdr_len = struct.unpack(">I", data[4:8])[0]
    fmt, ntrks, division = struct.unpack(">HHH", data[8:14])
    fields = [
        _f(0x00, 2, "format", fmt, _MIDI_FORMATS.get(fmt, "unknown")),
        _f(0x02, 2, "ntrks", ntrks),
    ]
    ctx = {"division": division}
    if division & 0x8000:
        fps = -struct.unpack(">b", bytes([(division >> 8) & 0xFF]))[0]
        tpf = division & 0xFF
        shown = 29.97 if fps == 29 else fps
        fields.append(_f(0x04, 2, "division", f"0x{division:04x}",
                         f"SMPTE: {shown} fps, {tpf} ticks/frame",
                         enc=">H", raw=division))
        ctx["ticks_per_sec"] = (29.97 if fps == 29 else fps) * tpf
    else:
        fields.append(_f(0x04, 2, "division", division, "ticks per quarter note"))
    hdr_warns = []
    if hdr_len > 6:
        fields.append(_f(0x06, hdr_len - 6, "extra_header",
                         f"{hdr_len - 6} bytes", "legal, skipped"))
    elif hdr_len < 6:
        # a negative extra_header length would reach --hex as
        # read(negative), i.e. the whole file. the six header bytes
        # were still decoded above (best effort).
        hdr_warns.append(
            f"MThd declares {hdr_len} bytes, spec minimum is 6")
    summary = f"format {fmt}, {ntrks} track(s)"
    chunks.append({"id": "MThd", "offset": 0, "size": hdr_len,
                   "summary": summary, "fields": fields,
                   "warnings": hdr_warns})

    offset = 8 + hdr_len
    found = 0
    first_tempo = None
    max_ticks = 0
    while offset + 8 <= file_size and found < ntrks:
        if data[offset:offset + 4] != b"MTrk":
            file_warns.append(
                f"expected MTrk at 0x{offset:08x}, found "
                f"{data[offset:offset + 4]!r}; stopping"
            )
            break
        trk_len = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        trk = data[offset + 8:offset + 8 + trk_len]
        entry = {"id": "MTrk", "offset": offset, "size": trk_len,
                 "summary": "", "fields": [], "warnings": []}
        if len(trk) < trk_len:
            entry["warnings"].append(
                f"declares {trk_len:,} bytes but only {len(trk):,} remain"
            )
        st = _scan_track(trk, ctx, collect=deep)
        if deep:
            entry["rows"] = st["events"][:_FRAME_LISTING_CAP]
            if len(st["events"]) > _FRAME_LISTING_CAP:
                entry["warnings"].append(
                    f"event listing capped at {_FRAME_LISTING_CAP:,}"
                )
        flds = entry["fields"]
        if st["names"]:
            flds.append(_f(None, 0, "name", st["names"][0]))
        flds.append(_f(None, 0, "events_ticks", st["ticks"]))
        if st["notes"]:
            flds.append(_f(None, 0, "notes", st["notes"],
                           f"{midi_note_to_name(st['nmin'])}-"
                           f"{midi_note_to_name(st['nmax'])}"))
            flds.append(_f(None, 0, "channels",
                           ",".join(str(c + 1) for c in sorted(st["channels"]))))
        if st["tempos"]:
            shown = ", ".join(f"{t:g}" for t in st["tempos"][:4])
            more = f" (+{len(st['tempos']) - 4} more)" if len(st["tempos"]) > 4 else ""
            flds.append(_f(None, 0, "tempo", shown + more, "BPM"))
            if first_tempo is None:
                first_tempo = st["tempos"][0]
        if st["time_sig"]:
            flds.append(_f(None, 0, "time_sig", st["time_sig"]))
        if st["key_sig"]:
            flds.append(_f(None, 0, "key_sig", st["key_sig"]))
        if not st["has_eot"]:
            entry["warnings"].append("no end-of-track meta event")
        for mfr, slen, reserved in st["sysex"]:
            if reserved or slen > 256:
                flds.append(_f(None, 0, "sysex", f"{mfr}, {slen:,} bytes"))
                why = ("uses the non-commercial manufacturer id, no synth acts "
                       "on it" if reserved else f"oversized ({slen:,} bytes)")
                entry["warnings"].append(f"SysEx {why}: possible payload cavity")
        max_ticks = max(max_ticks, st["ticks"])

        bits = []
        if st["names"]:
            bits.append(f"'{st['names'][0]}'")
        bits.append(f"{st['notes']} notes" if st["notes"] else "no notes")
        if st["tempos"]:
            bits.append(f"{st['tempos'][0]:g} bpm")
        entry["summary"] = ", ".join(bits)
        chunks.append(entry)

        found += 1
        offset += 8 + trk_len

    if found < ntrks:
        file_warns.append(f"MThd declares {ntrks} tracks, found {found}")
    if not (division & 0x8000) and first_tempo is None and found:
        file_warns.append("no tempo event in any track; players assume 120 bpm")

    return chunks, file_warns
