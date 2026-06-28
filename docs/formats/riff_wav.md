# RIFF / WAV Format Internals

Low-level reference for the RIFF container and WAV audio format,
including ACID loop metadata and SMPL sampler chunks.

Tools in this repo: `acidcat inspect FILE` walks the chunks and lints spec
violations, `acidcat inspect --hex` prints the raw bytes beside each decoded
field, `acidcat chunks FILE` gives the chunk table, and
`acidcat dump FILE acid smpl` hex-dumps named chunks.

---

## Core Structure (RIFF)

The RIFF (Resource Interchange File Format) container is a tagged,
chunk-based binary format. Everything is little-endian.

```
+----------------------------------------------+
| RIFF header (12 bytes)                       |
|   "RIFF"  4 bytes  magic                     |
|   size    4 bytes  uint32 LE (file size - 8) |
|   "WAVE"  4 bytes  form type                 |
+----------------------------------------------+
| chunk 1                                      |
| chunk 2                                      |
| ...                                          |
| chunk N                                      |
+----------------------------------------------+
```

### Byte-Level Map: RIFF Header

```
         0      1      2      3
       +------+------+------+------+
 0x00  | 'R'  | 'I'  | 'F'  | 'F'  |  magic
       +------+------+------+------+
 0x04  |      riff_size (u32 LE)   |  file size - 8
       +---------------------------+
 0x08  | 'W'  | 'A'  | 'V'  | 'E'  |  form type
       +------+------+------+------+
 0x0C  |  first chunk begins here  |
       :                           :
```

### Chunk Layout

Every chunk follows the same envelope:

```
struct chunk {
    char     id[4];       // ASCII identifier, e.g. "fmt ", "data"
    uint32_t size;        // payload size in bytes (LE)
    byte     data[size];  // payload
    // if size is odd, one pad byte follows (not counted in size)
};
```

```
         0      1      2      3
       +------+------+------+------+
 +0x00 |      chunk id (4cc)       |  "fmt ", "data", "acid", ...
       +---------------------------+
 +0x04 |    chunk_size (u32 LE)    |  payload only, pad excluded
       +---------------------------+
 +0x08 |          payload          |
       :       chunk_size bytes    :
       +---------------------------+
       | pad byte iff size is odd  |  word alignment, uncounted
       +---------------------------+
```

### Mental Model

```
RIFF
 +-- fmt          (required: audio format)
 +-- fact         (optional: accurate sample count)
 +-- data         (required: raw audio samples)
 +-- acid         (optional: loop metadata)
 +-- smpl         (optional: sampler/loop points)
 +-- inst         (optional: instrument tuning)
 +-- cue          (optional: cue/marker points)
 +-- LIST/INFO    (optional: text metadata)
 +-- bext         (optional: broadcast wave extension)
 +-- JUNK         (optional: padding/alignment)
```

Key rules:
- chunks are **unordered and repeatable** (in theory)
- `fmt ` must come before `data` (the one ordering guarantee)
- all chunks are **word-aligned** (2 bytes) -- pad byte if odd size
- order is NOT guaranteed except fmt before data

---

## fmt -- Format Chunk

Describes the audio encoding. Required. Always the first real chunk.

```
"fmt "
uint32_t size           // 16 for PCM, 18+ for extended

struct fmt_chunk {
    uint16_t format_tag;        // 1=PCM, 3=IEEE float, 6=A-law,
                                // 7=mu-law, 0xFFFE=extensible
    uint16_t channels;          // 1=mono, 2=stereo, etc.
    uint32_t sample_rate;       // e.g. 44100, 48000, 96000
    uint32_t avg_bytes_per_sec; // sample_rate * block_align
    uint16_t block_align;       // channels * bits_per_sample / 8
    uint16_t bits_per_sample;   // 8, 16, 24, 32
};
```

```
         0      1      2      3
       +------+------+------+------+
 0x00  | format_tag  |  channels   |  u16 / u16
       +------+------+------+------+
 0x04  |     sample_rate (u32)     |  44100, 48000, ...
       +---------------------------+
 0x08  |  avg_bytes_per_sec (u32)  |  rate * block_align
       +---------------------------+
 0x0C  | block_align | bits/sample |  u16 / u16
       +------+------+------+------+
```

### Format Tags

| Tag      | Codec             | Notes                           |
|----------|-------------------|---------------------------------|
| `0x0001` | PCM (LPCM)        | uncompressed, the default       |
| `0x0003` | IEEE Float        | 32-bit or 64-bit float samples  |
| `0x0006` | A-law             | telephony codec                 |
| `0x0007` | mu-law            | telephony codec                 |
| `0x0011` | IMA ADPCM         | compressed, 4:1 ratio           |
| `0x0002` | MS ADPCM          | Microsoft ADPCM variant         |
| `0x0055` | MPEG Layer III     | MP3 in WAV container            |
| `0xFFFE` | Extensible        | sub-format GUID follows         |

For extensible format (0xFFFE), the fmt chunk extends with:

```
struct fmt_extensible {
    uint16_t cb_size;              // 22 for extensible
    uint16_t valid_bits_per_sample;
    uint32_t channel_mask;         // speaker position bitmask
    byte     sub_format[16];       // GUID identifying actual codec
};
```

### Channel Mask (Extensible)

```
bit 0:  Front Left           (0x001)
bit 1:  Front Right          (0x002)
bit 2:  Front Center         (0x004)
bit 3:  Low Frequency (LFE)  (0x008)
bit 4:  Back Left            (0x010)
bit 5:  Back Right           (0x020)
bit 6:  Front Left of Center (0x040)
bit 7:  Front Right of Center(0x080)
bit 8:  Back Center          (0x100)
bit 9:  Side Left            (0x200)
bit 10: Side Right           (0x400)
```

Common masks: `0x03` = stereo (FL+FR), `0x04` = mono center,
`0x3F` = 5.1 surround.

---

## data -- Audio Data Chunk

Raw interleaved samples. The bulk of the file.

```
"data"
uint32_t size       // total bytes of audio data

// PCM samples, interleaved by channel:
// [L0][R0][L1][R1][L2][R2]...
```

### Sample Encoding

| Bits | PCM encoding          | Range                    |
|------|-----------------------|--------------------------|
| 8    | unsigned integer      | 0..255 (128 = silence)   |
| 16   | signed integer (LE)   | -32768..32767            |
| 24   | signed integer (LE)   | -8388608..8388607        |
| 32   | signed integer (LE)   | -2147483648..2147483647  |
| 32   | IEEE float (tag=3)    | -1.0..1.0 nominal        |
| 64   | IEEE double (tag=3)   | -1.0..1.0 nominal        |

### Duration Calculation

```
duration_sec = data_size / (sample_rate * channels * (bits_per_sample / 8))
```

For non-PCM codecs, prefer the `fact` chunk sample count:

```
duration_sec = fact_sample_length / sample_rate
```

---

## fact -- Fact Chunk

Stores the accurate decoded sample count. Required for non-PCM codecs,
optional (but common) for PCM.

```
"fact"
uint32_t size       // 4

struct fact_chunk {
    uint32_t sample_length;     // total samples per channel
};
```

For compressed formats (ADPCM, MP3-in-WAV), the data chunk size doesn't
directly translate to sample count. The fact chunk is the authoritative
source.

---

## acid -- ACID Loop Metadata

The centerpiece for loop libraries. Written by Sony ACID, FL Studio,
Ableton (on export), and many sample pack producers.

```
"acid"
uint32_t size       // 24

struct acid_chunk {
    uint32_t type_flags;        // see flag table below
    uint16_t root_note;         // MIDI note number (60 = C4)
    uint16_t unknown1;          // reserved / padding
    float    unknown2;          // reserved
    uint32_t num_beats;         // number of beats in the loop
    uint16_t meter_denominator; // e.g. 4 for x/4 time
    uint16_t meter_numerator;   // e.g. 4 for 4/x time
    float    tempo;             // BPM as IEEE 754 float
};
```

```
         0      1      2      3
       +------+------+------+------+
 0x00  |     type_flags (u32)      |  bit0 one-shot, bit1 root set,
       +---------------------------+  bit2 stretch, bit3 disk-based
 0x04  |  root_note  |  unknown1   |  u16 MIDI note / u16, often 0x8000
       +------+------+------+------+
 0x08  |      unknown2 (f32)       |  observed 0.0 in the wild
       +---------------------------+
 0x0C  |     num_beats (u32)       |  beat count of the loop
       +---------------------------+
 0x10  | meter_denom | meter_numer |  u16 / u16, usually 4 / 4
       +------+------+------+------+
 0x14  |       tempo (f32)         |  BPM
       +---------------------------+
```

Layout provenance: there is no official spec (the chunk is Sonic
Foundry reverse-engineering lore). This field order matches
libsndfile's `wav_read_acid_chunk` and was hex-verified 2026-06-10
against ACIDized WAVs from four different sample vendors: the
`num_beats / duration * 60` cross-check lands on the expected tempo
in every file. Note `num_beats` sits at offset `0x0C`, after the
unknown float -- a parser that reads beats at `0x08` gets 0 on every
conformant file (acidcat itself made exactly that mistake before
v0.5.7).

### Type Flags

```
bit 0:  one-shot (not a loop)
bit 1:  root note is valid
bit 2:  stretch is valid (tempo-based stretching)
bit 3:  disk-based (stream from disk)
bit 4:  (unknown)
bit 5:  (unknown)
bit 6:  root note is valid (redundant, some writers set this instead)
```

Common flag values:
- `0x00` -- basic loop, no root note
- `0x02` -- loop with valid root note
- `0x04` -- tempo-stretch enabled loop
- `0x06` -- loop with root note + stretch
- `0x01` -- one-shot (not a loop)

### Important Notes

- many implementations get the flags slightly wrong; expect inconsistencies
- `root_note` is a MIDI note (0-127). 60 = C4 (middle C)
- `tempo` is the original recorded BPM of the loop
- `num_beats` combined with `tempo` gives expected duration:
  `expected_sec = num_beats / tempo * 60`
- comparing expected vs actual duration reveals truncation or tail silence
- meter fields are sometimes zero even when tempo/beats are valid

### Hex Example (24 bytes)

```
00000000  06 00 00 00  3C 00 00 00  00 00 00 00  00 00 00 00
          ^^^^flags    ^^^^root=C4
00000010  08 00 00 00  04 00 04 00  00 00 BE 42
          ^^^^beats=8  ^^^^meter    ^^^^tempo=95.0
```

---

## smpl -- Sampler Chunk

Defines root key and loop points for samplers. Written by most DAWs
when exporting loops or instrument samples.

```
"smpl"
uint32_t size       // 36 + (num_loops * 24) + sampler_data

struct smpl_chunk {
    uint32_t manufacturer;      // MIDI manufacturer ID (0 = unknown)
    uint32_t product;           // MIDI product ID
    uint32_t sample_period;     // nanoseconds per sample (1e9/sr)
    uint32_t midi_unity_note;   // root key, MIDI note (60 = C4)
    uint32_t midi_pitch_frac;   // pitch fine-tune, fraction of semitone
    uint32_t smpte_format;      // 0, 24, 25, 29, 30
    uint32_t smpte_offset;      // SMPTE time offset
    uint32_t num_sample_loops;  // number of loop definitions
    uint32_t sampler_data;      // size of optional sampler-specific data
};
```

```
         0      1      2      3
       +------+------+------+------+
 0x00  |    manufacturer (u32)     |  MIDI manufacturer id, 0 = none
       +---------------------------+
 0x04  |      product (u32)        |
       +---------------------------+
 0x08  |   sample_period (u32)     |  ns per sample = 1e9 / rate
       +---------------------------+
 0x0C  |  midi_unity_note (u32)    |  root key; 0 = unset sentinel
       +---------------------------+
 0x10  |  midi_pitch_frac (u32)    |  fine tune, fraction of semitone
       +---------------------------+
 0x14  |    smpte_format (u32)     |  0, 24, 25, 29, 30
       +---------------------------+
 0x18  |    smpte_offset (u32)     |
       +---------------------------+
 0x1C  | num_sample_loops (u32)    |
       +---------------------------+
 0x20  |    sampler_data (u32)     |  trailing vendor blob size
       +---------------------------+
 0x24  |  loop entries follow ...  |  24 bytes each
       :                           :
```

### Loop Entry (24 bytes each, follows smpl header)

```
struct smpl_loop {
    uint32_t cue_point_id;      // matches cue chunk ID
    uint32_t type;              // 0=forward, 1=alternating, 2=reverse
    uint32_t start;             // loop start, in samples
    uint32_t end;               // loop end, in samples
    uint32_t fraction;          // fractional sample position
    uint32_t play_count;        // 0 = infinite loop
};
```

```
         0      1      2      3
       +------+------+------+------+
 +0x00 |   cue_point_id (u32)      |  links to cue chunk
       +---------------------------+
 +0x04 |       type (u32)          |  0 fwd, 1 ping-pong, 2 reverse
       +---------------------------+
 +0x08 |       start (u32)         |  sample frames, not bytes
       +---------------------------+
 +0x0C |        end (u32)          |
       +---------------------------+
 +0x10 |     fraction (u32)        |
       +---------------------------+
 +0x14 |    play_count (u32)       |  0 = loop forever
       +---------------------------+
```

### Loop Types

| Value | Type        | Behavior                        |
|-------|-------------|---------------------------------|
| 0     | Forward     | start -> end -> start -> ...    |
| 1     | Alternating | start -> end -> start (ping-pong) |
| 2     | Reverse     | end -> start -> end -> ...      |

### Notes

- `midi_unity_note` is the pitch at which the sample plays at original speed
- `sample_period` = 1,000,000,000 / sample_rate (e.g. 22675 for 44100 Hz)
- loop start/end are **sample frame offsets** (not byte offsets)
- many files have `num_sample_loops = 0` but still provide root key
- when both `acid` and `smpl` provide root note, `smpl` is generally
  more reliable (it's the actual sampler tuning)

---

## inst -- Instrument Chunk

Defines playback range constraints for samplers and virtual instruments.

```
"inst"
uint32_t size       // 7

struct inst_chunk {
    int8_t  base_note;      // MIDI root note (same as smpl unity note)
    int8_t  detune;         // cents, -50 to +50
    int8_t  gain;           // dB adjustment
    uint8_t low_note;       // lowest playable MIDI note
    uint8_t high_note;      // highest playable MIDI note
    uint8_t low_velocity;   // minimum trigger velocity
    uint8_t high_velocity;  // maximum trigger velocity
};
```

```
         0      1      2      3
       +------+------+------+------+
 0x00  | base | detun| gain | low_n|  i8 / i8 cents / i8 dB / u8
       +------+------+------+------+
 0x04  |high_n| lo_v | hi_v |       |  u8 / u8 / u8 (7 bytes total,
       +------+------+------+       |  pad byte follows: odd size)
```

### Notes

- often duplicates `smpl.midi_unity_note` as `base_note`
- key range (low_note..high_note) defines the playable zone on a keyboard
- velocity range defines the dynamic layer this sample belongs to
- used by multi-sample instruments to map samples across the keyboard

---

## cue -- Cue Points Chunk

Defines named marker positions within the audio.

```
"cue "
uint32_t size       // 4 + (num_cues * 24)

struct cue_chunk {
    uint32_t num_cue_points;
};
```

### Cue Point Entry (24 bytes each)

```
struct cue_point {
    uint32_t id;                // unique identifier
    uint32_t position;          // sample position (for playlist ordering)
    char     fcc_chunk[4];      // "data" (which chunk the position references)
    uint32_t chunk_start;       // byte offset of chunk start (usually 0)
    uint32_t block_start;       // byte offset within block (usually 0)
    uint32_t sample_offset;     // actual sample position in audio
};
```

```
         0      1      2      3
       +------+------+------+------+
 +0x00 |        id (u32)           |  unique, referenced by LIST/adtl
       +---------------------------+
 +0x04 |     position (u32)        |  playlist ordering
       +------+------+------+------+
 +0x08 | 'd'  | 'a'  | 't'  | 'a'  |  fcc of the chunk referenced
       +------+------+------+------+
 +0x0C |    chunk_start (u32)      |  usually 0
       +---------------------------+
 +0x10 |    block_start (u32)      |  usually 0
       +---------------------------+
 +0x14 |   sample_offset (u32)     |  the actual marker position
       +---------------------------+
```

Cue labels are stored in a separate `LIST/adtl` chunk, linked by cue ID.

---

## LIST -- List Chunk (INFO and adtl)

A meta-container holding sub-chunks. Two common types:

### LIST/INFO -- Text Metadata

```
"LIST"
uint32_t size
"INFO"              // list type (4 bytes)
// sub-chunks follow, each null-terminated strings:
```

| Sub-chunk | Meaning     | Example                  |
|-----------|-------------|--------------------------|
| `INAM`    | Title       | "Drum Loop 95 BPM"      |
| `IART`    | Artist      | "Producer Name"          |
| `ICMT`    | Comment     | "Recorded at 44.1k"     |
| `ISFT`    | Software    | "FL Studio 21"           |
| `ICRD`    | Date        | "2024-01-15"             |
| `IGNR`    | Genre       | "Electronic"             |
| `ICOP`    | Copyright   | "(c) 2024 Label"         |
| `IKEY`    | Keywords    | "drums;breakbeat;funky"  |
| `ISBJ`    | Subject     | "One-shot kick"          |
| `IENG`    | Engineer    | "Mix engineer name"      |
| `ITCH`    | Technician  | "Mastering engineer"     |
| `IPRD`    | Product     | "Sample Pack Vol. 3"     |

### LIST/adtl -- Associated Data (Cue Labels)

Links text labels to cue point IDs:

```
"LIST"
uint32_t size
"adtl"
// sub-chunks:
//   "labl" -- cue point label
//   "note" -- cue point note/comment
//   "ltxt" -- labeled text range
```

Label sub-chunk:

```
struct labl {
    uint32_t cue_id;        // matches cue_point.id
    char     text[];        // null-terminated label
};
```

---

## bext -- Broadcast Wave Extension

EBU Tech 3285 standard. Used in broadcast, film, and professional
audio production. Adds origin metadata and a precise timestamp.

```
"bext"
uint32_t size       // 602+

struct bext_chunk {
    char     description[256];          // free text
    char     originator[32];            // creator
    char     originator_reference[32];  // unique reference
    char     origination_date[10];      // "YYYY-MM-DD"
    char     origination_time[8];       // "HH:MM:SS"
    uint64_t time_reference_low;        // sample count since midnight
    uint64_t time_reference_high;       // (low + high<<32 = total)
    uint16_t version;                   // BWF version (0, 1, or 2)
    uint8_t  umid[64];                  // SMPTE UMID (v1+)
    int16_t  loudness_value;            // EBU R128 (v2+)
    int16_t  loudness_range;            // (v2+)
    int16_t  max_true_peak_level;       // (v2+)
    int16_t  max_momentary_loudness;    // (v2+)
    int16_t  max_short_term_loudness;   // (v2+)
    char     reserved[180];             // (v2+)
    char     coding_history[];          // variable-length, CR/LF delimited
};
```

### Time Reference

The `time_reference` fields encode position on a timeline (e.g., timecode
for film sync). Combined as a 64-bit sample count since midnight:

```
time_reference = time_reference_low + (time_reference_high << 32)
timecode_sec = time_reference / sample_rate
```

### Coding History

Free-text field recording the processing chain, e.g.:

```
A=PCM,F=48000,W=24,M=stereo,T=Pro Tools
A=PCM,F=44100,W=16,M=stereo,T=SRC
```

Format: `A=codec,F=sample_rate,W=bit_depth,M=mode,T=text`

---

## JUNK / PAD -- Padding Chunks

Alignment filler. No metadata value. DAWs insert these to reserve
space for later in-place metadata updates without rewriting the file.

```
"JUNK" or "PAD "
uint32_t size
byte     padding[size];     // all zeros or garbage
```

---

## RF64 / WAV64 -- Extended WAV

Standard RIFF has a 4GB file size limit (uint32 size field). Two
extensions exist:

### RF64 (EBU Tech 3306)

```
"RF64"                      // instead of "RIFF"
uint32_t size = 0xFFFFFFFF  // sentinel: "check ds64 chunk"
"WAVE"

// first chunk must be ds64:
"ds64"
uint32_t size
uint64_t riff_size;         // actual file size
uint64_t data_size;         // actual data chunk size
uint64_t sample_count;      // replaces fact chunk
uint32_t table_length;      // number of size-override entries
```

### WAV64 (Sony)

Uses GUIDs instead of 4-byte chunk IDs. 64-bit sizes throughout.
Less common than RF64.

---

## Chunk Discovery Order

Real-world chunk ordering varies by DAW. Common patterns:

```
FL Studio:    JUNK fmt fact data smpl inst acid LIST
Ableton:      fmt  data acid
ACID Pro:     fmt  data acid smpl
Audacity:     fmt  data LIST
Pro Tools:    JUNK fmt  data bext
iZotope RX:  fmt  fact data
```

acidcat's `survey` command can map chunk ordering across entire
sample libraries to identify which DAW produced the files.

---

## Validation Checks (acidcat)

Things acidcat checks or could check:

- **BPM sanity**: acid.tempo in reasonable range (40-300 BPM)
- **Duration match**: actual duration vs expected (beats/tempo * 60)
- **Root note agreement**: acid.root_note vs smpl.midi_unity_note
- **Loop bounds**: smpl.loop_start < smpl.loop_end < total_samples
- **Bit depth / format tag consistency**: tag=3 should have 32-bit
- **Truncation detection**: duration_diff reveals trimmed or padded loops
- **Missing metadata**: has data but no acid/smpl (un-tagged sample)

---

## Edge cases & malformations

The legal-but-weird and the outright broken, in one place. RIFF is old and
permissive, so a real-world walker meets all of these.

- **odd chunk size**: a chunk with an odd `size` is followed by one pad byte
  that is **not** counted in `size`. Skip it or every subsequent offset is off
  by one.
- **stale `riff_size`**: the header's size field is widely left wrong by
  writers and ignored by players. acidcat does not trust it for parsing, only
  lints the mismatch against the real file length.
- **root note 0**: in both `acid` and `smpl`, a root note of 0 is the documented
  "unset" sentinel, not MIDI note C-1. Treat 0 as absent.
- **one-shot flag lies**: the `acid` one-shot bit is set on real loops by some
  batch taggers, and boilerplate 8-beat / 120-bpm values are left in true
  one-shots. acidcat only trusts `num_beats` when the one-shot bit is clear, or
  when beats reconcile with the actual duration within 15 percent.
- **`fmt ` after `data`**: violates the single ordering rule. Linted.
- **RF64 sentinels**: a `0xFFFFFFFF` size means "read the real 64-bit size from
  `ds64`". A sentinel with no `ds64` override is malformed (linted), and `ds64`
  must be the first chunk.
- **non-PCM in WAV**: format tag `0x55` (MP3-in-WAV) or ADPCM means the `data`
  byte count does not map to a sample count. Use `fact` for duration.
- **declared size past EOF**: see Security below.

---

## Security & abuse

Every classic RIFF exploit is the same mistake: trusting a length, offset, or
count that came from the file. The chunk envelope hands a parser a 32-bit size
on every chunk, and a naive reader uses it to allocate, copy, or seek.

### acidcat's threat model

The famous RIFF bugs are memory-corruption and remote code execution in C and
C++ readers. **None of that class reaches acidcat as written.** acidcat is
Python: there is no manual allocation to overflow and no stack to smash. The
same trusted-length mistake reappears in a managed reader as two milder
problems, and those are the ones worth defending:

- **denial of service**: a file-controlled size or count that drives an
  unbounded read/allocation or a multi-billion-iteration loop.
- **wrong output**: a value derived from a field the file lied about (the
  overrun-duration bug fixed below).

So read the C exploits below as the origin of the bug class, not as something
acidcat could suffer verbatim. acidcat's goal is to never turn a hostile length
or count into a hang, a memory spike, or a confidently wrong number.

### The worked example: ANI cursor (CVE-2007-0038)

An animated cursor (`.ani`) is RIFF with the form type `ACON` and an `anih`
animation-header chunk that is supposed to be 36 bytes:

```
52 49 46 46  ss ss ss ss  41 43 4F 4E   "RIFF" <size> "ACON"
61 6E 69 68  LL LL LL LL  .. .. .. ..   "anih" <len>  <payload>
```

Windows `LoadAniIcon` read `LL` and copied that many bytes into a 36-byte stack
struct with no check that `LL <= 36`. Set `LL = 0x100` and the saved return
address is overwritten: a cursor became remote code execution. The bitter part:
MS05-002 had already fixed one `anih` length bug two years earlier; this was a
second unchecked copy of the same field. One field, two CVEs, because nobody
bounded it against the chunk that actually contained it.

### How acidcat resists the class

acidcat walks the identical `<id><uint32 size><payload>` envelope, so the same
field is in its hands on every chunk. The defenses:

- **bounded reads**: payload reads are capped at 64 KB, so a huge `size` never
  drives an allocation or a copy. The skip arithmetic still uses the real size
  to find the next chunk, but nothing is ever read or allocated from it blindly.
- **declared vs available lint**: every chunk's `size` is checked against the
  bytes that actually remain. `acidcat inspect` warns
  `chunk 'data' claims N bytes but only M remain`.
- **count caps**: file-controlled counts are clamped to the payload before any
  loop. A `cue ` chunk claiming `0xFFFFFFFF` cue points is bounded by
  `(payload - 4) // 24`, not iterated four billion times.
- **magic-gated dispatch**: `inspect` only parses RIFF whose form type is
  `WAVE`. An `.ani` (form `ACON`) is refused outright, so acidcat is never lured
  into the `anih` path in the first place.
- **derive from reality, not the field**: a chunk whose `size` overruns the file
  no longer contributes a duration computed from the lie. Before this fix a
  52-byte WAV with a `data` size of `0x7fffffff` reported a 24347-second
  duration; now it reports the duration of the bytes actually present and labels
  the declared size. Regression-tested as `test_overrun_data_size_not_trusted`.

The recurring lesson across the media-file exploit history (ANI, Stagefright,
the FFmpeg decoder CVEs): validate every size against the bytes that remain,
never preallocate from a file-controlled number, and bound every loop by the
real payload rather than the declared count.
