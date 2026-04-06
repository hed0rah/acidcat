# MIDI File Format Internals

Low-level reference for Standard MIDI Files (SMF). Not an audio format
per se, but contains musical metadata (BPM, key, time signature) that
acidcat extracts.

---

## High-Level Structure

A MIDI file is a sequence of chunks: one header chunk followed by
one or more track chunks. Everything is big-endian.

```
+-----------------------------------+
| MThd (header chunk)              |
|   format, tracks, division       |
+-----------------------------------+
| MTrk (track chunk 1)            |
|   delta-time + event pairs       |
+-----------------------------------+
| MTrk (track chunk 2)            |
|   ...                            |
+-----------------------------------+
| ...                              |
+-----------------------------------+
| MTrk (track chunk N)            |
+-----------------------------------+
```

---

## MThd -- Header Chunk

Always first. Always 14 bytes total (6-byte payload).

```
"MThd"
uint32_t size           // always 6

struct mthd {
    uint16_t format;    // 0, 1, or 2
    uint16_t tracks;    // number of MTrk chunks
    uint16_t division;  // timing resolution
};
```

### Format Types

| Value | Meaning          | Track layout                            |
|-------|------------------|-----------------------------------------|
| 0     | Single track     | everything in one MTrk                  |
| 1     | Multi-track sync | track 0 = tempo map, tracks 1+ = parts  |
| 2     | Multi-track async| independent sequences (rare)            |

Format 0 is common in sample pack MIDIs (simple, one-track files).
Format 1 is standard for DAW exports and complex arrangements.

### Division Field

Controls timing resolution. Interpretation depends on bit 15:

```
if bit 15 == 0:
    ticks_per_quarter_note = division & 0x7FFF
    // common values: 96, 120, 240, 480, 960

if bit 15 == 1:
    // SMPTE-based timing (rare in music production)
    smpte_format = -(division >> 8)   // -24, -25, -29, -30
    ticks_per_frame = division & 0xFF
```

Higher division = higher timing precision. 96 ticks/beat is common
in loop packs. 480 and 960 are common in DAW exports.

---

## MTrk -- Track Chunk

Contains a sequence of timed events.

```
"MTrk"
uint32_t size       // length of track data in bytes

// payload: sequence of (delta_time, event) pairs
```

### Delta Time (Variable-Length Quantity)

Time values are encoded as VLQ (variable-length quantity) to save space.
Each byte contributes 7 bits of data; bit 7 indicates continuation.

```
encoding:
    byte: 1MMMMMMM    (bit 7 = 1: more bytes follow)
    byte: 0MMMMMMM    (bit 7 = 0: last byte)

examples:
    0x00          ->  0          (1 byte)
    0x7F          ->  127        (1 byte)
    0x81 0x00     ->  128        (2 bytes)
    0xC0 0x00     ->  8192       (2 bytes)
    0xFF 0x7F     ->  16383      (2 bytes)
    0x81 0x80 0x00 -> 16384      (3 bytes)

decoding:
    value = 0
    loop:
        byte = read()
        value = (value << 7) | (byte & 0x7F)
        if not (byte & 0x80): break
```

Maximum VLQ is 4 bytes (28 bits), encoding values up to 0x0FFFFFFF.

Delta times are relative to the previous event in the same track
(not absolute). To get absolute time, accumulate deltas.

---

## Event Types

Three categories: channel messages, system exclusive, and meta events.

### Channel Messages (status byte 0x80-0xEF)

```
status byte format: SSSS CCCC
    SSSS = message type (8-E)
    CCCC = channel (0-15)
```

| Status  | Type           | Data bytes | Format              |
|---------|----------------|------------|---------------------|
| `8n`    | Note Off       | 2          | key, velocity       |
| `9n`    | Note On        | 2          | key, velocity       |
| `An`    | Aftertouch     | 2          | key, pressure       |
| `Bn`    | Control Change | 2          | controller, value   |
| `Cn`    | Program Change | 1          | program             |
| `Dn`    | Ch. Pressure   | 1          | pressure            |
| `En`    | Pitch Bend     | 2          | LSB, MSB            |

`n` = channel (0-15). Note On with velocity 0 is equivalent to Note Off.

### Running Status

If the status byte is the same as the previous message, it can be
omitted. The receiver reuses the previous status byte. This is why
the parser must track `running_status`.

```
// explicit:
90 3C 7F    // note on, channel 0, C4, velocity 127
90 40 7F    // note on, channel 0, E4, velocity 127

// with running status:
90 3C 7F    // note on, channel 0, C4, velocity 127
   40 7F    // (running status) note on, channel 0, E4, velocity 127
```

A non-status byte (< 0x80) triggers running status. The data byte
is consumed as the first parameter of the running status message.

### System Exclusive (0xF0, 0xF7)

```
F0 <length_vlq> <sysex_data>     // standard sysex
F7 <length_vlq> <data>           // escape / continuation
```

### Meta Events (0xFF)

```
FF <type> <length_vlq> <data>
```

Meta events carry non-musical metadata. They only appear in MIDI files,
not in real-time MIDI streams.

---

## Meta Event Reference

### FF 00 -- Sequence Number

```
FF 00 02 <uint16_be>
```

Identifies which sequence this track represents. Optional.

### FF 01 -- Text Event

```
FF 01 <len> <ascii_text>
```

Generic text. Can appear anywhere in a track.

### FF 02 -- Copyright Notice

```
FF 02 <len> <ascii_text>
```

Should appear at time 0 of the first track.

### FF 03 -- Track Name / Sequence Name

```
FF 03 <len> <ascii_text>
```

Name of the track (format 1) or sequence (format 0). Should appear
at time 0.

### FF 04 -- Instrument Name

```
FF 04 <len> <ascii_text>
```

### FF 05 -- Lyric

```
FF 05 <len> <ascii_text>
```

Syllable-level lyrics. Common in karaoke MIDI (.kar) files.

### FF 20 -- MIDI Channel Prefix

```
FF 20 01 <channel>
```

Associates subsequent meta events with a specific channel.

### FF 2F -- End of Track

```
FF 2F 00
```

Required. Must be the last event in every track.

### FF 51 -- Set Tempo

```
FF 51 03 <tt tt tt>
```

Microseconds per quarter note, as a 24-bit big-endian integer.

```
us_per_beat = (byte[0] << 16) | (byte[1] << 8) | byte[2]
bpm = 60,000,000 / us_per_beat
```

Common values:

| BPM | us/beat   | Hex          |
|-----|-----------|--------------|
| 120 | 500000    | `07 A1 20`   |
| 140 | 428571    | `06 8A 1B`   |
| 100 | 600000    | `09 27 C0`   |
| 90  | 666667    | `0A 2C 2B`   |
| 172 | 348837    | `05 53 A5`   |

Multiple tempo events create tempo changes. Track 0 should contain
the tempo map in format 1 files.

### FF 58 -- Time Signature

```
FF 58 04 <nn dd cc bb>
    nn = numerator
    dd = denominator as power of 2 (0=1, 1=2, 2=4, 3=8)
    cc = MIDI clocks per metronome click
    bb = 32nd notes per quarter note (usually 8)
```

Examples:
- 4/4 time: `FF 58 04 04 02 18 08` (4, 2^2=4, 24 clocks, 8)
- 3/4 time: `FF 58 04 03 02 18 08`
- 6/8 time: `FF 58 04 06 03 18 08` (6, 2^3=8)
- 7/8 time: `FF 58 04 07 03 18 08`

### FF 59 -- Key Signature

```
FF 59 02 <sf mi>
    sf = sharps/flats (signed byte)
         -7 = 7 flats (Cb major)
         -1 = 1 flat  (F major)
          0 = no sharps/flats (C major / A minor)
         +1 = 1 sharp (G major)
         +7 = 7 sharps (C# major)
    mi = mode
         0 = major
         1 = minor
```

Key signature to root note mapping:

```
sharps: C  G  D  A  E  B  F# C#     (sf = 0..7)
flats:  C  F  Bb Eb Ab Db Gb Cb     (sf = 0..-7)
```

Minor keys use the relative minor root (e.g., sf=0 mi=1 = A minor).

### FF 7F -- Sequencer-Specific

```
FF 7F <len> <manufacturer_id> <data>
```

Proprietary data. Varies by manufacturer.

---

## Duration Calculation

MIDI doesn't store duration directly. Calculate from tick count:

```
// with tempo:
beats = total_ticks / division
duration_sec = beats * (60.0 / bpm)

// equivalent:
duration_sec = total_ticks * us_per_beat / (division * 1000000)
```

For files with tempo changes, integrate over tempo segments.

For files with no tempo event, the default MIDI tempo is 120 BPM
(500000 us/beat).

---

## Notes for Sample Pack MIDIs

Observations from parsing sample pack MIDI files:

- **Many lack tempo events.** BPM must be inferred from filename
  (e.g., "PSJ2_ethereal2080_172_Bass_Loop" -> 172 BPM).
- **Format 0 is common.** Single track, simple note data.
- **Division of 96** is typical in loop packs (low resolution is fine
  for pre-composed patterns).
- **No key signature events.** Key is in the filename
  (e.g., "_A#m_Descent" -> A# minor).
- **Note range reveals instrument type**: bass (A0-E3), lead (C3-C6),
  drums (channel 10, C1-B2 General MIDI mapping).
- **Useful extracted fields**: note count, note range, channel usage,
  time signature. BPM and key usually come from filename, not metadata.

---

## MIDI Note Numbers

Reference table for common ranges:

```
Oct |  C   C#   D   D#   E    F   F#   G   G#   A   A#   B
----|-----------------------------------------------------
 -1 |  0    1   2    3    4    5    6    7    8    9   10   11
  0 | 12   13  14   15   16   17   18   19   20   21   22   23
  1 | 24   25  26   27   28   29   30   31   32   33   34   35
  2 | 36   37  38   39   40   41   42   43   44   45   46   47
  3 | 48   49  50   51   52   53   54   55   56   57   58   59
  4 | 60   61  62   63   64   65   66   67   68   69   70   71
  5 | 72   73  74   75   76   77   78   79   80   81   82   83
  6 | 84   85  86   87   88   89   90   91   92   93   94   95
  7 | 96   97  98   99  100  101  102  103  104  105  106  107
  8 | 108 109 110  111  112  113  114  115  116  117  118  119
  9 | 120 121 122  123  124  125  126  127
```

Middle C = 60 = C4 (acidcat convention, matching most DAWs).
Some standards use C3 or C5 for MIDI note 60.
