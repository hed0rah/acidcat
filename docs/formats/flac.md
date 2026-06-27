# FLAC Format Internals

Low-level reference for the native FLAC stream (`.flac`). For FLAC
carried inside an OGG container, see `ogg.md`.

---

## Overview

FLAC (Free Lossless Audio Codec) is a clean **chunked container**: a
four-byte magic, a run of self-describing metadata blocks, then the
audio frames.

```
[ "fLaC" ]                4-byte stream marker
[ METADATA_BLOCK ]        STREAMINFO is always first and mandatory
[ METADATA_BLOCK ]        VORBIS_COMMENT, SEEKTABLE, PICTURE, PADDING, ...
   ...                    (the last block sets its last-block flag)
[ audio frame ]           encoded audio begins right after the last block
   ...
```

Almost everything is big-endian and bit-packed. The one exception is the
VORBIS_COMMENT payload, which is little-endian (it was lifted wholesale
from the OGG Vorbis spec).

Detection: the file starts with the ASCII bytes `fLaC`.

---

## Metadata Block Header

Every metadata block starts with a 4-byte header:

```
 last-block flag    1 bit    1 = this is the final metadata block
 block type         7 bits   see table below
 length            24 bits   big-endian, payload size (excludes this header)
```

Block types:

| Type | Name           | Contents                              |
|-----:|----------------|---------------------------------------|
| 0    | STREAMINFO     | sample rate, channels, bits, samples, MD5 |
| 1    | PADDING        | zeroed filler for later edits         |
| 2    | APPLICATION    | app id + opaque app data              |
| 3    | SEEKTABLE      | array of seek points                  |
| 4    | VORBIS_COMMENT | vendor string + tag list (metadata)   |
| 5    | CUESHEET       | embedded cue sheet (tracks/indices)   |
| 6    | PICTURE        | cover art (same fields as ID3 APIC)   |

A parser walks blocks until it sees the one with the last-block flag,
then the audio frames begin.

---

## STREAMINFO (type 0)

Always the first block, exactly 34 bytes:

```
struct streaminfo {
    uint16_t min_block_size;   // samples
    uint16_t max_block_size;   // samples
    uint24_t min_frame_size;   // bytes (0 = unknown)
    uint24_t max_frame_size;   // bytes (0 = unknown)
    // next 8 bytes are bit-packed:
    uint20_t sample_rate;      // Hz
    uint3_t  channels_minus_1; // actual channels = value + 1
    uint5_t  bits_minus_1;     // actual bits/sample = value + 1
    uint36_t total_samples;    // per channel; 0 = unknown
    uint8_t  md5[16];          // MD5 of the unencoded audio (0 = unset)
};
```

The packed 8-byte field is decoded from a big-endian `uint64`:
`rate = bits 63..44`, `channels-1 = bits 43..41`, `bps-1 = bits 40..36`,
`total_samples = bits 35..0`. The MD5 lets you verify a decode is
bit-exact against the original.

---

## VORBIS_COMMENT (type 4)

The metadata block. **Lengths are little-endian here**, unlike the rest
of FLAC:

```
struct vorbis_comment {
    uint32_t vendor_length;              // LE
    char     vendor[vendor_length];      // e.g. "reference libFLAC 1.3.2"
    uint32_t comment_count;              // LE
    // for each comment:
    //   uint32_t length                 // LE
    //   char     text[length]           // UTF-8 "FIELD=value"
};
```

Same tag vocabulary as OGG Vorbis (case-insensitive `TITLE`, `ARTIST`,
`ALBUM`, `DATE`, `GENRE`, `BPM`, `KEY`, `COMMENT`, `REPLAYGAIN_*`).
Multiple values for one field are allowed.

---

## Other Blocks

### PICTURE (type 6)

```
uint32_t picture_type;     // 3 = front cover, 4 = back cover, ...
uint32_t mime_length;  char mime[mime_length];   // e.g. "image/jpeg"
uint32_t desc_length;  char description[desc_length];  // UTF-8
uint32_t width; uint32_t height; uint32_t depth; uint32_t colors;
uint32_t data_length;  uint8_t data[data_length];
```

All big-endian. Same structure as the ID3v2 `APIC` frame.

### SEEKTABLE (type 3)

An array of 18-byte seek points, each: `uint64 sample_number`,
`uint64 stream_offset`, `uint16 frame_samples`. A sample number of
`0xFFFFFFFFFFFFFFFF` marks a placeholder point.

### APPLICATION (type 2)

A 4-byte registered application id followed by opaque app-specific data.

---

## Audio Frames

After the last metadata block, audio frames begin. Each frame has its
own sync code, a header (block size, sample rate, channel assignment),
subframes per channel, and a CRC. For metadata purposes the frames are
opaque; STREAMINFO already gives everything needed for duration.

---

## Duration Calculation

```
duration = total_samples / sample_rate      # both from STREAMINFO
```

No frame walk required, unlike MP3 or OGG. If `total_samples` is 0
(streamed/unknown), fall back to scanning frames.

---

## FLAC vs Other Containers

| Feature        | FLAC            | MP3              | OGG              |
|----------------|------------------|-------------------|-------------------|
| Structure      | metadata blocks  | frame stream      | page sequence     |
| Endianness     | big (mostly)     | big (frame hdr)   | little            |
| Metadata       | Vorbis Comment   | ID3v2/ID3v1       | Vorbis Comment    |
| Duration       | STREAMINFO field | scan / Xing       | last granule      |
| Integrity      | per-frame + MD5  | none              | per-page CRC      |
| Lossless       | yes              | no                | codec-dependent   |

---

## Notes

- STREAMINFO must be the first block and must be present; a file that
  violates either is malformed.
- watch the endianness trap: FLAC is big-endian except VORBIS_COMMENT,
  whose lengths are little-endian.
- `acidcat inspect FILE.flac` walks every metadata block (STREAMINFO,
  VORBIS_COMMENT, PICTURE, SEEKTABLE, APPLICATION, PADDING), decodes the
  STREAMINFO fields, lists the Vorbis tags, and reports the audio-frame
  extent. It lints the STREAMINFO-first and last-block-flag rules.
  `--frames` is a no-op for FLAC: the audio frames have no per-element
  metadata worth dumping.
- for indexing, acidcat reads the Vorbis Comment tags through mutagen.
