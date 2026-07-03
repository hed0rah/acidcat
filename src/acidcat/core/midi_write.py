"""Minimal Standard MIDI File writer.

Turns a list of notes (pitch, start, duration in beats, velocity 0..1) into a
type-0 SMF. Used to export DAW clips (e.g. Bitwig .bwclip) back to MIDI.
"""


def _vlq(n):
    """MIDI variable-length quantity."""
    if n <= 0:
        return b"\x00"
    parts = []
    while n:
        parts.append(n & 0x7F)
        n >>= 7
    parts.reverse()
    out = bytearray()
    for i, v in enumerate(parts):
        out.append(v | (0x80 if i < len(parts) - 1 else 0))
    return bytes(out)


def notes_to_smf(notes, bpm=120.0, division=480):
    """Build a type-0 Standard MIDI File (bytes) from notes. Each note is a dict
    {pitch, start, duration, velocity}; start/duration are in beats, velocity is
    0..1 (None -> 64). bpm sets the tempo meta event."""
    events = []  # (tick, order, event_bytes); order 0 (off) sorts before 1 (on)
    for n in notes:
        p = n.get("pitch")
        if p is None or not (0 <= p <= 127):
            continue
        start = n.get("start") or 0.0
        dur = n.get("duration")
        dur = 0.25 if dur is None else dur
        vel = n.get("velocity")
        vel = 64 if vel is None else max(1, min(127, round(vel * 127)))
        on = max(0, round(start * division))
        off = max(on + 1, round((start + dur) * division))
        events.append((on, 1, bytes([0x90, p, vel])))
        events.append((off, 0, bytes([0x80, p, 0])))
    events.sort(key=lambda e: (e[0], e[1]))

    trk = bytearray()
    mpqn = int(round(60_000_000 / bpm)) if bpm else 500_000
    trk += _vlq(0) + b"\xff\x51\x03" + mpqn.to_bytes(3, "big")  # set tempo
    last = 0
    for tick, _, ev in events:
        trk += _vlq(tick - last) + ev
        last = tick
    trk += _vlq(0) + b"\xff\x2f\x00"  # end of track

    header = (b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big")
              + (1).to_bytes(2, "big") + division.to_bytes(2, "big"))
    track = b"MTrk" + len(trk).to_bytes(4, "big") + bytes(trk)
    return header + track
