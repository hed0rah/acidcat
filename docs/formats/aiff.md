# AIFF / IFF Format Internals

Low-level reference for AIFF (Audio Interchange File Format) and its
compressed variant AIFC. Big-endian counterpart to RIFF/WAV.

---

## Core Structure (IFF / FORM)

AIFF uses the IFF (Interchange File Format) container, originally
from the Amiga. Structurally identical to RIFF but big-endian.

```
+----------------------------------------------+
| FORM header (12 bytes)                       |
|   "FORM"  4 bytes  magic                     |
|   size    4 bytes  uint32 BE (file size - 8) |
|   "AIFF"  4 bytes  form type (or "AIFC")     |
+----------------------------------------------+
| chunk 1                                      |
| chunk 2                                      |
| ...                                          |
| chunk N                                      |
+----------------------------------------------+
```

### Chunk Layout

Same envelope as RIFF, but sizes are big-endian:

```
struct chunk {
    char     id[4];       // ASCII identifier
    uint32_t size;        // payload size in bytes (BE)
    byte     data[size];
    // pad byte if size is odd (word alignment)
};
```

### Mental Model

```
FORM (AIFF or AIFC)
 +-- COMM         (required: format description)
 +-- SSND         (required: audio sample data)
 +-- MARK         (optional: marker positions)
 +-- INST         (optional: instrument parameters)
 +-- NAME         (optional: track name)
 +-- AUTH         (optional: author)
 +-- (c)          (optional: copyright)
 +-- ANNO         (optional: annotation/comment)
 +-- ID3          (optional: ID3v2 tag)
```

Key differences from RIFF/WAV:
- **big-endian** everywhere (sizes, sample data, format fields)
- sample rate stored as **80-bit IEEE 754 extended** float
- no separate `fmt` chunk -- format info is in `COMM`
- string chunks use raw ASCII (not null-terminated in the spec,
  but many writers null-terminate anyway)

---

## COMM -- Common Chunk

The format descriptor. Required. Equivalent to WAV's `fmt ` chunk.

```
"COMM"
uint32_t size       // 18 for AIFF, 22+ for AIFC

struct comm_chunk {
    int16_t  num_channels;       // 1=mono, 2=stereo
    uint32_t num_sample_frames;  // total frames (not samples)
    int16_t  bits_per_sample;    // 8, 16, 24, 32
    byte     sample_rate[10];    // 80-bit IEEE 754 extended float
};
```

### AIFC Extension

When form type is "AIFC", COMM has additional fields:

```
struct comm_aifc_ext {
    char     compression_type[4];   // e.g. "NONE", "sowt", "fl32"
    // followed by Pascal string: compression name
    uint8_t  name_length;
    char     compression_name[name_length];
};
```

### AIFC Compression Types

| Type   | Meaning              | Notes                          |
|--------|----------------------|--------------------------------|
| `NONE` | uncompressed         | same as AIFF                   |
| `sowt` | little-endian PCM    | "twos" reversed, used by macOS |
| `fl32` | 32-bit IEEE float    |                                |
| `fl64` | 64-bit IEEE float    |                                |
| `alaw` | A-law                | telephony                      |
| `ulaw` | mu-law               | telephony                      |
| `ima4` | IMA ADPCM 4:1        | QuickTime                      |

### 80-bit IEEE 754 Extended Float

The sample rate is stored as a 10-byte extended precision float.
This is the most annoying part of AIFF parsing. No standard library
handles it natively.

```
byte layout (10 bytes, big-endian):

byte 0:    S EEEEEEE    (sign bit + exponent high 7 bits)
byte 1:    EEEEEEEE     (exponent low 8 bits)
bytes 2-9: MMMM...      (64-bit mantissa, explicit integer bit)

exponent = ((byte[0] & 0x7F) << 8) | byte[1]
mantissa = bytes 2..9 as uint64 big-endian

if exponent == 0 and mantissa == 0:  value = 0.0
if exponent == 0x7FFF:               value = infinity
else: value = sign * (mantissa / 2^63) * 2^(exponent - 16383)
```

Common values:

| Sample Rate | Hex (10 bytes)                         |
|-------------|----------------------------------------|
| 44100       | `40 0D AC 44 00 00 00 00 00 00`        |
| 48000       | `40 0D BB 80 00 00 00 00 00 00`        |
| 96000       | `40 0F BB 80 00 00 00 00 00 00`        |
| 22050       | `40 0C AC 44 00 00 00 00 00 00`        |

---

## SSND -- Sound Data Chunk

Raw audio samples, big-endian interleaved (unless AIFC `sowt`).

```
"SSND"
uint32_t size

struct ssnd_chunk {
    uint32_t offset;        // byte offset to first sample (usually 0)
    uint32_t block_size;    // block alignment (usually 0)
    byte     data[];        // interleaved samples, big-endian
};
```

### Sample Encoding

| Bits | Encoding                 | Notes                         |
|------|--------------------------|-------------------------------|
| 8    | signed integer (BE)      | unlike WAV which uses unsigned|
| 16   | signed integer (BE)      | -32768..32767                 |
| 24   | signed integer (BE)      | 3 bytes per sample            |
| 32   | signed integer (BE)      | or float if AIFC fl32         |

Key difference from WAV: 8-bit AIFF samples are **signed** (-128..127),
while WAV 8-bit are **unsigned** (0..255). This matters for conversion.

---

## MARK -- Marker Chunk

Defines named positions in the audio. AIFF's equivalent to WAV's cue chunk.

```
"MARK"
uint32_t size

struct mark_chunk {
    uint16_t num_markers;
};
```

### Marker Entry

```
struct marker {
    int16_t  id;            // unique marker ID
    uint32_t position;      // sample frame position
    // Pascal string follows:
    uint8_t  name_length;
    char     name[name_length];
    // pad byte if name_length is even (to maintain word alignment)
};
```

Markers are referenced by ID from the INST chunk's loop definitions.

---

## INST -- Instrument Chunk

Sampler/instrument parameters. Similar concept to WAV's inst + smpl.

```
"INST"
uint32_t size       // 20

struct inst_chunk {
    int8_t   base_note;         // MIDI note (60 = C4)
    int8_t   detune;            // cents, -50 to +50
    uint8_t  low_note;          // lowest MIDI note
    uint8_t  high_note;         // highest MIDI note
    uint8_t  low_velocity;      // min velocity
    uint8_t  high_velocity;     // max velocity
    int16_t  gain;              // dB (signed, BE)

    // sustain loop:
    int16_t  sustain_loop_mode; // 0=none, 1=forward, 2=pingpong
    int16_t  sustain_loop_begin;// marker ID (references MARK chunk)
    int16_t  sustain_loop_end;  // marker ID

    // release loop:
    int16_t  release_loop_mode;
    int16_t  release_loop_begin;
    int16_t  release_loop_end;
};
```

### Loop Modes

| Value | Mode        | Behavior                     |
|-------|-------------|------------------------------|
| 0     | No loop     | play once                    |
| 1     | Forward     | start -> end -> start -> ... |
| 2     | Ping-pong   | start -> end -> start (alternating) |

Note: loop begin/end reference **marker IDs**, not sample positions
directly. Resolve through the MARK chunk.

---

## Text Chunks

AIFF has dedicated chunk types for text metadata, unlike WAV's LIST/INFO
sub-chunk approach.

### NAME -- Track Name

```
"NAME"
uint32_t size
char     name[size];    // ASCII, may be null-terminated
```

### AUTH -- Author

```
"AUTH"
uint32_t size
char     author[size];
```

### (c)  -- Copyright

```
"(c) "                  // note the space -- chunk ID is 4 chars
uint32_t size
char     copyright[size];
```

### ANNO -- Annotation

```
"ANNO"
uint32_t size
char     annotation[size];
```

Multiple ANNO chunks are allowed (unlike NAME/AUTH/(c) which should
appear once).

---

## ID3 -- ID3v2 Tag

Some AIFF files contain an embedded ID3v2 tag (same format as MP3).
This is an unofficial extension but widely supported.

```
"ID3 "
uint32_t size
byte     id3_data[size];    // standard ID3v2.x tag
```

The ID3 tag can contain artist, title, album, year, genre, cover art,
and any other ID3 frame. Parsing requires a full ID3v2 implementation.

---

## REX / RX2 Files

Propellerhead REX files are **AIFF internally**. The `file` command
identifies them as `IFF data, AIFF audio`.

REX adds proprietary chunks for slice data (loop slicing information),
but the audio portion is standard AIFF. Parsing COMM, INST, and MARK
chunks from a REX file works as expected.

REX2 (.rx2) may use a slightly different structure but still within
the IFF container.

---

## AIFF vs WAV Quick Reference

| Feature          | AIFF              | WAV               |
|------------------|--------------------|--------------------|
| Endianness       | big-endian         | little-endian      |
| Container        | IFF (FORM)         | RIFF               |
| Format chunk     | COMM               | fmt                |
| Sample rate      | 80-bit float       | uint32             |
| 8-bit samples    | signed             | unsigned           |
| Text metadata    | NAME/AUTH/(c)/ANNO | LIST/INFO          |
| Loop points      | INST + MARK        | smpl               |
| BPM metadata     | (none standard)    | acid               |
| Platform origin  | Apple/Mac          | Microsoft/Windows  |
