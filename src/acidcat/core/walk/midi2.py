"""MIDI Clip File (.midi2 / SMF2CLIP) walker -- the MIDI 2.0 successor to SMF.

Structure is dead simple: an 8-byte ASCII ``SMF2CLIP`` magic, then a contiguous
big-endian, self-delimiting UMP stream (core/ump.py). There are no chunks. The
stream is a Configuration Header (mandatory DCTPQ, optional Set Tempo / Time
Signature / metadata) followed by the Clip Sequence: a Start of Clip UMP-Stream
message, Delta-Clockstamp-prefixed events, and an End of Clip marker (clean EOF).

Timing carries in-band: DCTPQ sets ticks-per-quarter, and a Delta Clockstamp
precedes essentially every message with the ticks since the previous one.

Reference: MMA M2-116-U (MIDI Clip File) v1.0; M2-104-UM (UMP) v1.1.2.
"""

from acidcat.core.primitives.notes import coverage

from acidcat.core.formats import ump
from acidcat.core.walk.base import _f, _open, _size

MAGIC = b"SMF2CLIP"
MAX_CLIP_BYTES = 64 * 1024 * 1024                       # clip files are event streams, not audio


def inspect_midi2(filepath, deep=False):
    """Walk a .midi2 clip file. Returns (chunks, file_warnings)."""
    size = _size(filepath)
    with _open(filepath) as f:                     # capped read: the file must not size the alloc
        data = f.read(min(MAX_CLIP_BYTES, size))
    file_warns = []
    if size > MAX_CLIP_BYTES:
        file_warns.append(coverage(f"clip is {size:,} bytes; walked the first {MAX_CLIP_BYTES:,}"))
    if data[:8] != MAGIC:
        file_warns.append("missing SMF2CLIP magic")

    header = {"id": "SMF2CLIP", "offset": 0, "size": 8, "summary": "MIDI Clip File header",
              "payload_base": 0, "warnings": [],
              "fields": [_f(0, 8, "magic", data[:8].decode("latin-1", "replace"),
                            "8-byte ASCII; the whole rest of the file is a UMP stream")]}

    tpq = tempo_10ns = bpm = timesig = None
    metadata = []
    nticks = 0
    n_events = n_notes = 0
    seen_start = seen_end = False
    data_after_end = False
    rows = []
    truncated = False

    consumed = 8
    for off, words, m in ump.iter_ump(data[8:], endian="big"):
        abs_off = 8 + off
        consumed = abs_off + len(words) * 4
        kind = m["kind"]
        if kind == "delta_clockstamp":
            nticks += m["ticks"]
            continue                                  # timing, not an event row
        if seen_end and kind != "noop":
            data_after_end = True
        if kind == "dctpq":
            tpq = m["value"]
        elif kind == "set_tempo":
            tempo_10ns, bpm = m["tempo_10ns"], m.get("bpm")
        elif kind == "set_time_signature":
            den = 1 << m["denom_pow2"] if m["denom_pow2"] else 0
            timesig = f"{m['numerator']}/{den}" if den else f"{m['numerator']}/?"
        elif kind == "flex_text":
            metadata.append((m.get("status_bank"), m.get("text", "")))
        elif kind == "start_of_clip":
            seen_start = True
        elif kind == "end_of_clip":
            seen_end = True
        else:
            n_events += 1
            if kind in ("note_on", "note_off"):
                n_notes += 1
        if deep and kind not in ("noop",):
            rows.append({"tick": nticks, "event": kind, "detail": _detail(m)})

    if consumed < len(data):
        truncated = True
        file_warns.append(f"{len(data) - consumed} trailing byte(s) after the last "
                           f"complete UMP (truncated packet or extra data)")

    # duration from accumulated ticks + tempo (both optional)
    dur = None
    if tpq and tempo_10ns:
        dur = nticks / tpq * (tempo_10ns * 1e-8)      # 10ns units -> seconds per quarter

    clip = {"id": "clip", "offset": 8, "size": max(0, consumed - 8),
            "payload_base": 8, "warnings": [], "fields": []}
    fl = clip["fields"]
    fl.append(_f(None, 0, "resolution", f"{tpq} ticks/quarter" if tpq else "(no DCTPQ)",
                 "DCTPQ; mandatory per spec"))
    if bpm:
        fl.append(_f(None, 0, "tempo", f"{bpm:.3f} BPM", f"{tempo_10ns} x 10ns per quarter"))
    if timesig:
        fl.append(_f(None, 0, "time_signature", timesig))
    fl.append(_f(None, 0, "events", f"{n_events} ({n_notes} note)"))
    if dur is not None:
        fl.append(_f(None, 0, "duration", f"{dur:.2f} s", f"{nticks} ticks total"))
    for bank, text in metadata[:16]:
        fl.append(_f(None, 0, "meta", text, f"Flex Data text, status bank 0x{(bank or 0):02X}"))

    if not tpq:
        clip["warnings"].append("no DCTPQ resolution message (mandatory)")
    if not seen_start:
        clip["warnings"].append("no Start of Clip message")
    if not seen_end:
        clip["warnings"].append("no End of Clip marker")
    if data_after_end:
        clip["warnings"].append("data after End of Clip (nothing may follow it)")

    clip["summary"] = ", ".join(
        p for p in [f"TPQ {tpq}" if tpq else None,
                    f"{bpm:.0f} BPM" if bpm else None,
                    timesig, f"{n_events} events",
                    f"{dur:.1f}s" if dur is not None else None] if p)
    if deep:
        clip["rows"] = rows

    return [header, clip], file_warns


def _detail_midi1(m):
    """One MIDI 1.0 Channel Voice message, in its own two 7-bit data bytes."""
    k, ch = m["kind"], m["channel"] + 1
    d1, d2 = m["data1"], m["data2"]
    if k in ("note_on", "note_off"):
        return f"note {d1} vel {d2} ch{ch} grp{m['group'] + 1} (MIDI 1.0)"
    if k == "control_change":
        return f"cc{d1}={d2} ch{ch} (MIDI 1.0)"
    if k == "program_change":
        return f"program {d1} ch{ch} (MIDI 1.0)"
    if k == "pitch_bend":
        return f"{(d2 << 7) | d1} ch{ch} (MIDI 1.0)"
    if k == "poly_pressure":
        return f"note {d1} pressure {d2} ch{ch} (MIDI 1.0)"
    if k == "channel_pressure":
        # one data byte in MIDI 1.0; data2 is not part of the message
        return f"{d1} ch{ch} (MIDI 1.0)"
    return f"{k} ch{ch} (MIDI 1.0)"


def _detail(m):
    k = m["kind"]
    if m.get("mt") == 0x2:
        # MIDI 1.0 Channel Voice. It shares every `kind` below with the MIDI 2.0
        # family and none of their fields: the resolution is the whole
        # difference between the two, so a 1.0 message carries data1/data2
        # where a 2.0 one carries note/velocity/program/bank. Rendering it
        # through the 2.0 branches raised KeyError on five of the six statuses,
        # and mt 0x2 is legal in a clip file, so a valid document reached it.
        return _detail_midi1(m)
    if k in ("note_on", "note_off"):
        return (f"note {m['note']} vel {m['velocity']} ch{m['channel'] + 1} grp{m['group'] + 1}"
                + (f" attr {m['attr_type']:#04x}" if m.get("attr_type") else ""))
    if k == "control_change":
        return f"cc{m['index']}={m['data']} ch{m['channel'] + 1}"
    if k == "program_change":
        return f"program {m['program']} bank {m['bank']} ch{m['channel'] + 1}"
    if k in ("rpn", "nrpn"):
        return f"bank {m['bank']} param {m['param']} = {m['data']} ch{m['channel'] + 1}"
    if k == "pitch_bend":
        return f"{m['data']} ch{m['channel'] + 1}"
    if k in ("sysex7", "sysex8"):
        return f"{m['status']}, {m['nbytes']} bytes"
    if k == "set_tempo":
        return f"{m.get('bpm', 0):.2f} BPM"
    return m.get("kind", "")
