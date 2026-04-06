# MP4 / M4A Format Internals

Low-level reference for the ISO Base Media File Format (ISOBMFF),
commonly seen as .mp4, .m4a (audio-only), .m4v, .mov.

---

## Overview

MP4 is built on Apple's QuickTime container. Unlike RIFF/IFF which use
flat chunk lists, MP4 uses a **hierarchical tree of atoms** (also called
boxes). Big-endian throughout.

This is a completely different philosophy from WAV/AIFF:
- WAV/AIFF: flat list of chunks, simple
- MP4: nested tree of boxes, complex but more expressive

---

## Atom (Box) Structure

Every atom follows this layout:

```
struct atom {
    uint32_t size;      // total atom size including header (BE)
    char     type[4];   // ASCII identifier ("ftyp", "moov", etc.)
    byte     data[size - 8];
};
```

### Extended Size

If `size == 1`, the actual size follows as a 64-bit integer:

```
uint32_t size = 1;          // sentinel
char     type[4];
uint64_t extended_size;     // actual size (BE)
byte     data[extended_size - 16];
```

If `size == 0`, the atom extends to end of file (only valid for
the last atom).

### Container vs Leaf Atoms

Container atoms hold other atoms as children. Leaf atoms hold data.
There's no flag distinguishing them -- you must know which types
are containers.

---

## Top-Level Structure

```
[ftyp]                  // file type and compatibility
[moov]                  // movie (metadata container)
 +-- [mvhd]            // movie header (timescale, duration)
 +-- [trak]            // track (one per stream)
 |    +-- [tkhd]       // track header
 |    +-- [mdia]       // media container
 |    |    +-- [mdhd]  // media header (timescale, duration, language)
 |    |    +-- [hdlr]  // handler (soun = audio, vide = video)
 |    |    +-- [minf]  // media information
 |    |         +-- [stbl]  // sample table
 |    |              +-- [stsd]  // sample description (codec info)
 |    |              +-- [stts]  // time-to-sample
 |    |              +-- [stsc]  // sample-to-chunk
 |    |              +-- [stsz]  // sample sizes
 |    |              +-- [stco]  // chunk offsets (32-bit)
 |    |              +-- [co64]  // chunk offsets (64-bit)
 |    +-- [edts]       // edit list (optional)
 +-- [udta]            // user data (optional)
      +-- [meta]       // metadata container
           +-- [ilst]  // iTunes metadata list
[mdat]                  // media data (actual audio/video samples)
```

---

## ftyp -- File Type Box

Identifies the file format and compatible specifications.

```
struct ftyp {
    char     major_brand[4];      // e.g. "M4A ", "isom", "mp42"
    uint32_t minor_version;
    char     compatible_brands[]; // array of 4-byte brand codes
};
```

### Common Brands

| Brand  | Meaning                         |
|--------|---------------------------------|
| `isom` | ISO Base Media (generic)        |
| `iso2` | ISO Base Media v2               |
| `mp41` | MP4 v1                          |
| `mp42` | MP4 v2                          |
| `M4A ` | iTunes AAC audio                |
| `M4B ` | iTunes audiobook                |
| `M4V ` | iTunes video                    |
| `qt  ` | Apple QuickTime                 |
| `dash` | MPEG-DASH                       |

---

## mvhd -- Movie Header

Global properties for the entire file.

```
// version 0:
struct mvhd_v0 {
    uint8_t  version;           // 0
    uint8_t  flags[3];
    uint32_t creation_time;     // seconds since 1904-01-01
    uint32_t modification_time;
    uint32_t timescale;         // time units per second
    uint32_t duration;          // in timescale units
    // ... rate, volume, matrix, etc.
};

// version 1 uses uint64 for times and duration
```

Duration in seconds: `duration / timescale`

---

## mdhd -- Media Header

Per-track timing and language.

```
struct mdhd_v0 {
    uint8_t  version;
    uint8_t  flags[3];
    uint32_t creation_time;
    uint32_t modification_time;
    uint32_t timescale;         // audio: usually sample_rate
    uint32_t duration;          // in timescale units
    uint16_t language;          // ISO 639-2 packed (5 bits x 3)
    uint16_t quality;
};
```

For audio tracks, `timescale` is typically the sample rate (44100, 48000),
making `duration` the total sample count.

### Language Packing

```
// 3 characters packed into 16 bits, 5 bits each
// 'und' (undetermined) = 0x55C4
char[0] = ((language >> 10) & 0x1F) + 0x60
char[1] = ((language >>  5) & 0x1F) + 0x60
char[2] = ((language >>  0) & 0x1F) + 0x60
```

---

## stsd -- Sample Description

Describes the codec. For audio, contains codec-specific atoms.

### AAC (mp4a)

```
[stsd]
 +-- [mp4a]                 // MPEG-4 audio
      +-- [esds]            // elementary stream descriptor
           // contains AudioSpecificConfig:
           //   object type (2 = AAC-LC, 5 = HE-AAC)
           //   frequency index
           //   channel configuration
```

### ALAC

```
[stsd]
 +-- [alac]                 // Apple Lossless
      // contains ALACSpecificConfig:
      //   frame_length, compatible_version, bit_depth,
      //   rice_history_mult, rice_initial_history,
      //   rice_limit, channels, max_run, max_frame_bytes,
      //   avg_bit_rate, sample_rate
```

---

## iTunes Metadata (ilst)

iTunes-style metadata lives at `moov/udta/meta/ilst`. Each entry
is a box whose type is a well-known key.

```
[ilst]
 +-- [nam]  (title)
 |    +-- [data]
 |         type + locale + payload
 +-- [ART]  (artist)
 +-- [alb]  (album)
 +-- [gen]  (genre)
 +-- [trkn] (track number)
 +-- [disk] (disc number)
 +-- [day]  (year)
 +-- [covr] (cover art)
 +-- [tmpo] (BPM)
 +-- [cprt] (copyright)
 +-- [cmt]  (comment)
 +-- [too]  (encoding tool)
 +-- [wrt]  (composer)
```

### Data Atom Format

Each metadata value is wrapped in a `data` atom:

```
struct data_atom {
    uint32_t size;
    char     type[4];       // "data"
    uint32_t data_type;     // 1=UTF-8, 2=UTF-16, 13=JPEG, 14=PNG, 21=int
    uint32_t locale;        // usually 0
    byte     value[];       // actual metadata value
};
```

### Data Types

| Type | Meaning     | Used by            |
|------|-------------|--------------------|
| 1    | UTF-8 text  | title, artist, etc |
| 2    | UTF-16 text | (rare)             |
| 13   | JPEG image  | cover art          |
| 14   | PNG image   | cover art          |
| 21   | signed int  | track#, disc#, BPM |
| 0    | implicit    | (type-dependent)   |

### BPM (tmpo)

```
[tmpo]
 +-- [data]
      type = 21 (integer)
      value = uint16_t BPM (BE)
```

This is the standard way to store BPM in M4A/MP4 files. iTunes,
Apple Music, and most taggers write this field.

---

## Audio Codec Details

### AAC Profiles

| Object Type | Profile   | Typical Use            |
|-------------|-----------|------------------------|
| 2           | AAC-LC    | standard music files   |
| 5           | HE-AAC   | streaming, low bitrate |
| 29          | HE-AAC v2| stereo streaming       |
| 23          | AAC-LD    | real-time communication|

### Sample Rate Index

```
0: 96000    4: 44100    8: 16000    12: 7350
1: 88200    5: 32000    9: 12000    13-14: reserved
2: 64000    6: 24000   10: 11025    15: explicit
3: 48000    7: 22050   11:  8000
```

---

## Fragmented MP4 (fMP4)

Used for streaming (DASH, HLS). Splits media data across multiple
`moof` + `mdat` pairs:

```
[ftyp]
[moov]          // initialization segment (no samples)
[moof]          // movie fragment header
[mdat]          // fragment samples
[moof]
[mdat]
...
```

---

## MP4 vs WAV for acidcat

| Feature           | MP4/M4A              | WAV                  |
|-------------------|----------------------|----------------------|
| BPM storage       | ilst/tmpo (integer)  | acid chunk (float)   |
| Key storage       | ilst/key (text)      | acid/smpl (MIDI note)|
| Text metadata     | rich (ilst)          | minimal (LIST/INFO)  |
| Cover art         | embedded (covr)      | not standard         |
| Compression       | AAC/ALAC (lossy/less)| PCM (none)           |
| Max file size     | 64-bit (exabytes)    | 4GB (32-bit RIFF)    |
| Loop points       | not standard         | smpl chunk           |
| Complexity        | high (nested tree)   | low (flat chunks)    |

---

## Notes

- MP4 parsing is significantly more complex than RIFF. Consider using
  a library (mutagen, ffprobe) rather than raw parsing.
- The `moov` atom can appear before or after `mdat`. When at the end
  (common for streaming-optimized files), the entire file must be read
  to find metadata. "Fast start" / "web optimized" puts moov first.
- Apple's `.m4a` is just `.mp4` with an audio-only brand.
- The 1904 epoch for timestamps is a QuickTime legacy. Convert:
  `unix_time = qt_time - 2082844800`
