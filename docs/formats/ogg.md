# OGG Container Format Internals

Low-level reference for the OGG bitstream container. Used for Vorbis,
Opus, FLAC, and other codecs.

---

## Overview

OGG is fundamentally different from chunk-based formats (RIFF, IFF, MP4).
It's a **page-based streaming container** designed for sequential access,
not random access. Think of it as a transport layer, not an archive.

Key properties:
- pages are independently decodable (no global header dependency)
- multiple logical bitstreams can be multiplexed (audio + video + subtitles)
- designed for streaming over unreliable transports
- all integers are little-endian (unlike most network-oriented formats)

---

## Page Structure

Every OGG file is a sequence of pages. Each page is self-contained.

```
struct ogg_page {
    char     capture_pattern[4]; // "OggS" (magic bytes)
    uint8_t  version;            // always 0
    uint8_t  header_type;        // flags (see below)
    int64_t  granule_position;   // codec-specific position (LE)
    uint32_t serial_number;      // identifies logical bitstream
    uint32_t page_sequence;      // page counter per bitstream
    uint32_t crc_checksum;       // CRC-32 of entire page
    uint8_t  num_segments;       // number of segment entries
    uint8_t  segment_table[num_segments]; // segment sizes
    // payload follows: sum(segment_table) bytes
};
```

### Header Type Flags

```
bit 0: 0x01 = continuation page (packet spans from previous page)
bit 1: 0x02 = beginning of stream (BOS) -- first page of a bitstream
bit 2: 0x04 = end of stream (EOS) -- last page of a bitstream
```

### Segment Table

The segment table defines how bytes in the payload are grouped into
packets. Each segment entry is a byte (0-255):

```
- segment value < 255: end of a packet (value = last segment size)
- segment value = 255: packet continues in next segment
- packet size = sum of consecutive segments until one is < 255
```

Examples:
- segment [200]: one packet, 200 bytes
- segment [255, 100]: one packet, 355 bytes
- segment [255, 255, 50]: one packet, 560 bytes
- segment [100, 200]: two packets, 100 and 200 bytes

### Granule Position

Codec-specific timestamp for the last complete packet on the page:

| Codec   | Granule meaning                               |
|---------|-----------------------------------------------|
| Vorbis  | total PCM samples decoded so far              |
| Opus    | total 48kHz samples decoded so far            |
| FLAC    | sample number of first sample on page         |
| Theora  | frame number (encoded with keyframe info)     |

For audio: `duration = granule_position / sample_rate`

---

## Multiplexing

Multiple logical bitstreams share one physical stream, interleaved
at the page level:

```
page (serial=1, audio)
page (serial=2, video)
page (serial=1, audio)
page (serial=2, video)
...
```

Each bitstream has its own serial number, page sequence counter,
and granule position. For audio-only files (.ogg, .opus), there's
typically just one logical bitstream.

BOS pages for all streams appear at the start (grouped). EOS pages
appear at the end.

---

## Vorbis in OGG

Vorbis uses three header packets, all required, in order:

### 1. Identification Header

```
// first byte: 0x01 (type = identification)
struct vorbis_id {
    uint8_t  packet_type;       // 0x01
    char     codec_id[6];      // "vorbis"
    uint32_t version;           // 0
    uint8_t  channels;
    uint32_t sample_rate;       // Hz
    int32_t  bitrate_maximum;   // 0 = unset
    int32_t  bitrate_nominal;   // target bitrate (bits/sec)
    int32_t  bitrate_minimum;   // 0 = unset
    uint8_t  block_sizes;       // two 4-bit fields (exponents)
    uint8_t  framing_flag;      // must be 1
};
```

Block sizes: `block_0 = 2^(low nibble)`, `block_1 = 2^(high nibble)`.
Typical: 256 and 2048, or 512 and 4096.

### 2. Comment Header (Vorbis Comment / metadata)

```
// first byte: 0x03 (type = comment)
struct vorbis_comment {
    uint8_t  packet_type;       // 0x03
    char     codec_id[6];      // "vorbis"
    uint32_t vendor_length;
    char     vendor_string[vendor_length];
    uint32_t num_comments;
    // for each comment:
    //   uint32_t length
    //   char     comment[length]   // "TAG=value" format
};
```

Standard tags (case-insensitive):

| Tag           | Meaning                  |
|---------------|--------------------------|
| `TITLE`       | track title              |
| `ARTIST`      | performer                |
| `ALBUM`       | album name               |
| `DATE`        | recording date           |
| `TRACKNUMBER` | track number             |
| `GENRE`       | genre                    |
| `COMMENT`     | free text                |
| `BPM`         | beats per minute         |
| `KEY`         | musical key              |
| `ENCODER`     | encoding software        |
| `ISRC`        | recording code           |
| `LYRICS`      | song lyrics              |
| `REPLAYGAIN_TRACK_GAIN` | loudness normalization |

Tags are UTF-8 encoded. Multiple values for the same tag are allowed
(e.g., multiple `ARTIST` entries).

### 3. Setup Header

Codebook and floor/residue configuration. Needed for decoding but
contains no user-facing metadata.

---

## Opus in OGG

Opus uses a simpler header structure (RFC 7845).

### OpusHead (Identification)

```
struct opus_head {
    char     magic[8];          // "OpusHead"
    uint8_t  version;           // 1
    uint8_t  channel_count;
    uint16_t pre_skip;          // samples to skip at start (LE)
    uint32_t input_sample_rate; // original sample rate (LE)
    int16_t  output_gain;       // dB gain to apply (Q7.8 fixed)
    uint8_t  channel_mapping;   // 0 = mono/stereo, 1 = surround
    // if channel_mapping != 0:
    //   uint8_t stream_count
    //   uint8_t coupled_count
    //   uint8_t channel_mapping_table[channel_count]
};
```

Opus always decodes at 48000 Hz internally. The `input_sample_rate`
records the original rate before encoding (for metadata only).

### OpusTags (Comment)

```
struct opus_tags {
    char     magic[8];          // "OpusTags"
    // followed by Vorbis Comment structure
    // (same format as Vorbis comment header, minus packet_type + "vorbis")
    uint32_t vendor_length;
    char     vendor_string[vendor_length];
    uint32_t num_comments;
    // ...
};
```

Uses the same tag format as Vorbis Comment.

---

## FLAC in OGG

FLAC can be encapsulated in OGG (RFC 5765), though standalone .flac
is more common.

The first OGG packet contains a FLAC header packet with the
STREAMINFO metadata block. Subsequent metadata blocks follow in
separate packets before audio data begins.

---

## Duration Calculation

For audio-only OGG files:

```
1. seek to last page in file (scan backward for "OggS")
2. read granule_position from last page
3. duration = granule_position / sample_rate
```

For Opus: `duration = (last_granule - pre_skip) / 48000`

For Vorbis: `duration = last_granule / sample_rate`

---

## OGG vs Other Containers

| Feature          | OGG              | RIFF/WAV         | MP4              |
|------------------|-------------------|-------------------|-------------------|
| Structure        | page sequence     | flat chunks       | atom tree         |
| Endianness       | little            | little            | big               |
| Random access    | poor (scan pages) | good (seek chunks)| good (seek atoms) |
| Streaming        | excellent         | poor              | good (fMP4)       |
| Multiplexing     | yes (serial#)     | no                | yes (tracks)      |
| Metadata system  | Vorbis Comment    | LIST/INFO         | iTunes ilst       |
| Max file size    | 64-bit granule    | 4GB (RIFF)        | 64-bit atoms      |

---

## Notes

- OGG page headers have a fixed overhead of 27 + num_segments bytes,
  which is why small pages are inefficient
- the CRC-32 polynomial is 0x04C11DB7 (same as Ethernet, but with
  different init/XOR -- use the OGG-specific implementation)
- seeking in OGG files requires a bisection search scanning for page
  boundaries, which is more expensive than chunk-based formats
- for acidcat purposes, the Vorbis Comment metadata is the main
  extraction target -- BPM, KEY, TITLE, ARTIST tags
- `.oga` extension is technically correct for audio-only OGG Vorbis
  (`.ogg` is the legacy extension), `.opus` for Opus
