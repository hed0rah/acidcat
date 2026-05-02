"""
MIDI file parser.

Extracts metadata from Standard MIDI Files: tempo, time signature,
key signature, track names, note statistics.
"""

import os
import struct


def is_midi(filepath):
    """Check if file is a Standard MIDI File."""
    try:
        with open(filepath, "rb") as f:
            return f.read(4) == b"MThd"
    except Exception:
        return False


def _read_vlq(data, offset):
    """Read a variable-length quantity. Returns (value, new_offset)."""
    value = 0
    while offset < len(data):
        byte = data[offset]
        value = (value << 7) | (byte & 0x7F)
        offset += 1
        if not (byte & 0x80):
            break
    return value, offset


def parse_midi(filepath):
    """
    Parse a MIDI file and extract metadata.

    Returns dict with: format, tracks, division, tempo_bpm, time_sig,
    key_sig, track_names, duration_ticks, note_count, note_range, channel_count.
    """
    meta = {
        "format": None,
        "tracks": None,
        "division": None,
        "tempo_bpm": None,
        "time_sig": None,
        "key_sig": None,
        "track_names": [],
        "duration_ticks": 0,
        "note_count": 0,
        "note_min": None,
        "note_max": None,
        "channels_used": set(),
    }

    with open(filepath, "rb") as f:
        data = f.read()

    if len(data) < 14 or data[0:4] != b"MThd":
        return meta

    # header
    hdr_len = struct.unpack(">I", data[4:8])[0]
    meta["format"] = struct.unpack(">H", data[8:10])[0]
    meta["tracks"] = struct.unpack(">H", data[10:12])[0]
    meta["division"] = struct.unpack(">H", data[12:14])[0]

    # parse tracks
    offset = 8 + hdr_len
    tempos = []
    total_ticks = 0

    for _ in range(meta["tracks"]):
        if offset + 8 > len(data):
            break
        if data[offset:offset + 4] != b"MTrk":
            break
        trk_len = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        trk_start = offset + 8
        trk_end = trk_start + trk_len
        trk_data = data[trk_start:trk_end]

        pos = 0
        running_status = 0
        track_ticks = 0

        while pos < len(trk_data):
            delta, pos = _read_vlq(trk_data, pos)
            track_ticks += delta

            if pos >= len(trk_data):
                break

            status = trk_data[pos]

            if status == 0xFF:
                # meta event
                if pos + 2 >= len(trk_data):
                    break
                event_type = trk_data[pos + 1]
                event_len, pos = _read_vlq(trk_data, pos + 2)

                if pos + event_len > len(trk_data):
                    break
                event_data = trk_data[pos:pos + event_len]
                pos += event_len

                if event_type == 0x51 and event_len == 3:
                    # tempo
                    us_per_beat = (event_data[0] << 16) | (event_data[1] << 8) | event_data[2]
                    if us_per_beat > 0:
                        bpm = round(60_000_000 / us_per_beat, 2)
                        tempos.append(bpm)

                elif event_type == 0x58 and event_len == 4:
                    # time signature
                    num = event_data[0]
                    den = 2 ** event_data[1]
                    meta["time_sig"] = f"{num}/{den}"

                elif event_type == 0x59 and event_len == 2:
                    # key signature
                    sf = struct.unpack(">b", event_data[0:1])[0]
                    mi = event_data[1]
                    key_names_sharp = ["C", "G", "D", "A", "E", "B", "F#", "C#"]
                    key_names_flat = ["C", "F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb"]
                    if sf >= 0:
                        root = key_names_sharp[min(sf, 7)]
                    else:
                        root = key_names_flat[min(-sf, 7)]
                    quality = "m" if mi == 1 else ""
                    meta["key_sig"] = f"{root}{quality}"

                elif event_type == 0x03:
                    # track name
                    name = event_data.decode("ascii", errors="replace").strip()
                    if name:
                        meta["track_names"].append(name)

                elif event_type == 0x02:
                    # copyright
                    meta["copyright"] = event_data.decode("ascii", errors="replace").strip()

            elif status == 0xF0 or status == 0xF7:
                # sysex. bound the VLQ length against remaining track
                # bytes so a malformed file cannot push pos past the
                # MTrk boundary into the next track's data.
                sysex_len, pos = _read_vlq(trk_data, pos + 1)
                if pos + sysex_len > len(trk_data):
                    break
                pos += sysex_len

            elif status & 0x80:
                # channel message
                running_status = status
                pos += 1
                msg_type = status & 0xF0
                channel = status & 0x0F

                if msg_type in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                    # 2-byte data
                    if pos + 1 < len(trk_data):
                        d1 = trk_data[pos]
                        d2 = trk_data[pos + 1] if pos + 1 < len(trk_data) else 0
                        pos += 2

                        if msg_type == 0x90 and d2 > 0:
                            # note on
                            meta["note_count"] += 1
                            meta["channels_used"].add(channel)
                            if meta["note_min"] is None or d1 < meta["note_min"]:
                                meta["note_min"] = d1
                            if meta["note_max"] is None or d1 > meta["note_max"]:
                                meta["note_max"] = d1
                    else:
                        break
                elif msg_type in (0xC0, 0xD0):
                    # 1-byte data
                    pos += 1
                else:
                    pos += 2  # default 2 data bytes

            else:
                # running status
                if running_status:
                    msg_type = running_status & 0xF0
                    channel = running_status & 0x0F
                    d1 = status
                    if msg_type in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                        if pos + 1 < len(trk_data):
                            d2 = trk_data[pos + 1]
                            pos += 1
                            if msg_type == 0x90 and d2 > 0:
                                meta["note_count"] += 1
                                meta["channels_used"].add(channel)
                                if meta["note_min"] is None or d1 < meta["note_min"]:
                                    meta["note_min"] = d1
                                if meta["note_max"] is None or d1 > meta["note_max"]:
                                    meta["note_max"] = d1
                        else:
                            break
                    elif msg_type in (0xC0, 0xD0):
                        pass  # d1 is the only data byte, already consumed
                    else:
                        pos += 1
                else:
                    pos += 1  # skip unknown

        total_ticks = max(total_ticks, track_ticks)
        offset = trk_end

    meta["duration_ticks"] = total_ticks

    # pick first tempo or fall back to filename
    if tempos:
        meta["tempo_bpm"] = tempos[0]

    # convert channels_used set to sorted list for output
    meta["channels_used"] = sorted(meta["channels_used"])

    # estimate duration in seconds if we have tempo and division
    if meta["tempo_bpm"] and meta["division"] and meta["division"] > 0:
        beats = total_ticks / meta["division"]
        meta["duration_sec"] = round(beats * 60.0 / meta["tempo_bpm"], 2)

    return meta
