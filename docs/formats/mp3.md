# MP3 / MPEG Audio Format Internals

Low-level reference for MPEG-1/2/2.5 Audio Layer I/II/III, plus the
ID3v2 and ID3v1 metadata tags that wrap it.

---

## Overview

MP3 is **not a container format**. There is no chunk table, no atom
tree, no global header describing the file. An `.mp3` is just:

```
[ optional ID3v2 tag ]   prepended metadata, variable size
[ MPEG audio frame ]     each frame self-describes via its own 4-byte header
[ MPEG audio frame ]
   ...
[ optional ID3v1 tag ]   appended metadata, fixed 128 bytes
```

You decode it by finding the first frame sync and walking frame by
frame; each frame header carries everything needed to compute that
frame's length and step to the next one. There is no index, so the only
way to count frames or get an exact duration is to scan (or to trust a
Xing/VBRI header in the first frame).

All multi-byte fields in the MPEG frame header are big-endian bitfields.

---

## File Detection

- ID3v2-tagged files start with the ASCII bytes `ID3`.
- Untagged files start with a frame sync: first byte `0xFF`, second byte
  with the top three bits set (`0xE0` mask).

A parser checks for `ID3` first, and otherwise scans forward for the
first valid frame sync (some files carry leading junk or an APE tag).

---

## ID3v2 Tag

Prepended to the front of the file. Header is 10 bytes:

```
struct id3v2_header {
    char     magic[3];      // "ID3"
    uint8_t  version_major; // 3 = ID3v2.3, 4 = ID3v2.4 (2 = ID3v2.2)
    uint8_t  version_minor;
    uint8_t  flags;         // bit 7 unsync, bit 6 extended header,
                            // bit 5 experimental, bit 4 footer
    uint8_t  size[4];       // synchsafe: 28 bits, 7 bits per byte
};
```

The size is **synchsafe**: each byte uses only its low 7 bits (the high
bit is always 0, so the size can never contain a false `0xFF` frame
sync). Decode as `(b0<<21) | (b1<<14) | (b2<<7) | b3`. Total tag size is
`10 + size` (plus another 10 if the footer flag is set). The first
audio frame begins immediately after.

### ID3v2.3 / 2.4 Frames

After the header, frames follow until padding (a run of `0x00`):

```
struct id3v2_frame {
    char     id[4];     // e.g. "TIT2", "TPE1", "APIC"
    uint8_t  size[4];   // v2.4: synchsafe; v2.3: plain big-endian uint32
    uint8_t  flags[2];
    uint8_t  data[size];
};
```

The frame size field differs by version: **v2.3 is a plain big-endian
uint32**, **v2.4 is synchsafe** (same 7-bits-per-byte decode as the tag
size). Reading a v2.4 tag with v2.3 size rules (or vice versa) walks off
the frame boundaries as soon as any frame exceeds 127 bytes.

Two header flags change how the frame data must be read:

- **unsync (bit 7)**: the writer inserted a `$00` after every `$FF` byte
  so tag data can never look like a frame sync. A reader must strip
  those stuffed zeros before interpreting frame sizes; acidcat reverses
  the unsynchronisation up front and reports logical (post-desync)
  offsets.
- **extended header (bit 6)**: an optional variable-size block sits
  between the tag header and the first frame. Its own size field is
  synchsafe in v2.4 (and includes itself) but plain in v2.3 (and
  excludes its own 4 size bytes). acidcat skips it so it is not misread
  as a frame.

Bit 5 is the experimental flag; bit 4 (v2.4) marks a trailing footer.

Text frames (id starts with `T`) begin with an encoding byte:
`0` = Latin-1, `1` = UTF-16 with BOM, `2` = UTF-16BE, `3` = UTF-8.

Common frames acidcat surfaces:

| Frame  | Meaning            |
|--------|--------------------|
| `TIT2` | title              |
| `TPE1` | artist             |
| `TALB` | album              |
| `TCON` | genre              |
| `TBPM` | beats per minute   |
| `TKEY` | initial key        |
| `TYER` / `TDRC` | year      |
| `TRCK` | track number       |
| `TSSE` | encoder settings   |
| `TENC` | encoded by         |
| `APIC` | attached picture   |
| `COMM` | comment            |

### ID3v2.2 Frames

ID3v2.2 frames are smaller: 3-byte id, 3-byte plain big-endian size, no
flags. The frame ids are 3 characters instead of 4; acidcat maps and
decodes the common ones just like their v2.3/2.4 counterparts:

| Frame | Meaning          | v2.3/2.4 equivalent |
|-------|------------------|---------------------|
| `TT2` | title            | `TIT2`              |
| `TP1` | artist           | `TPE1`              |
| `TAL` | album            | `TALB`              |
| `TCO` | genre            | `TCON`              |
| `TBP` | beats per minute | `TBPM`              |
| `TKE` | initial key      | `TKEY`              |
| `TYE` | year             | `TYER`              |
| `TRK` | track number     | `TRCK`              |
| `TEN` | encoded by       | `TENC`              |
| `COM` | comment          | `COMM`              |

---

## MPEG Audio Frame Header

Every audio frame starts with a 4-byte header:

```
 syncword          11 bits   all ones (0x7FF); first byte is 0xFF, next 3 bits set
 version            2 bits   00=MPEG2.5 01=reserved 10=MPEG2 11=MPEG1
 layer              2 bits   00=reserved 01=Layer III 10=Layer II 11=Layer I
 protection         1 bit    0 = 16-bit CRC follows the header
 bitrate index      4 bits   table lookup (depends on version+layer)
 samplerate index   2 bits   table lookup (depends on version)
 padding            1 bit    frame is 1 byte (L1: 4 bytes) larger
 private            1 bit
 channel mode       2 bits   00=stereo 01=joint 10=dual 11=mono
 mode extension     2 bits   joint-stereo parameters
 copyright          1 bit
 original           1 bit
 emphasis           2 bits   00=none 01=50/15ms 11=CCITT J.17
```

### Bitrate Table (kbps)

Index 0 is the "free" format, index 15 is invalid.

| idx | V1 L1 | V1 L2 | V1 L3 | V2 L1 | V2 L2/L3 |
|----:|------:|------:|------:|------:|---------:|
| 1   | 32    | 32    | 32    | 32    | 8        |
| 5   | 160   | 80    | 64    | 80    | 40       |
| 9   | 288   | 160   | 128   | 144   | 80       |
| 14  | 448   | 384   | 320   | 256   | 160      |

(V2 covers MPEG2 and MPEG2.5.)

### Sample Rate Table (Hz)

| index | MPEG 1 | MPEG 2 | MPEG 2.5 |
|------:|-------:|-------:|---------:|
| 0     | 44100  | 22050  | 11025    |
| 1     | 48000  | 24000  | 12000    |
| 2     | 32000  | 16000  | 8000     |

### Samples per Frame and Frame Length

| Layer | Samples/frame (MPEG1) | Samples/frame (MPEG2/2.5) |
|-------|----------------------:|--------------------------:|
| I     | 384                   | 384                       |
| II    | 1152                  | 1152                      |
| III   | 1152                  | 576                       |

```
Layer I:        frame_len = (12 * bitrate / samplerate + padding) * 4
Layer II/III:   frame_len = (samples/8) * bitrate / samplerate + padding
```

(bitrate in bits/sec, integer division). A parser steps `frame_len`
bytes to reach the next sync.

---

## Xing / Info / VBRI (VBR Headers)

VBR files cannot derive duration from a single bitrate, so the encoder
writes a header into the **first frame** (after the side-information
block) recording the true frame and byte counts.

The Xing/Info tag sits at a version- and channel-dependent offset from
the frame start (4-byte header + side info):

| | mono | stereo / joint / dual |
|---|----:|----:|
| MPEG 1     | 21 | 36 |
| MPEG 2/2.5 | 13 | 21 |

When the frame is CRC-protected (protection bit = 0), the 2 CRC bytes
sit between the 4-byte header and the side info, so **add 2** to every
offset above.

```
struct xing {
    char     id[4];      // "Xing" (VBR) or "Info" (CBR encoded by LAME)
    uint32_t flags;      // bit0 frames, bit1 bytes, bit2 TOC, bit3 quality
    uint32_t frame_count;   // if flags bit 0
    uint32_t byte_count;    // if flags bit 1
    uint8_t  toc[100];      // if flags bit 2, seek table
    uint32_t quality;       // if flags bit 3, 0=best 100=worst
};
```

`Xing` means true VBR; `Info` means CBR written by LAME.

### VBRI (Fraunhofer)

The Fraunhofer encoder writes a `VBRI` header instead. Unlike
Xing/Info, it sits at a **fixed** offset: frame start + 36 (32 bytes
after the 4-byte header), independent of version, channel mode, and
side-info size. All fields are big-endian:

| Offset (from `VBRI`) | Field | Size |
|-------:|-------------|-----:|
| 0  | id `"VBRI"`  | 4 |
| 4  | version      | 2 |
| 10 | byte_count   | 4 |
| 14 | frame_count  | 4 |

A frame carries at most one of Xing/Info/VBRI. acidcat parses VBRI and
labels the stream VBR, using its frame_count for the duration exactly
as it would a Xing count.

### LAME Tag

Immediately after the Xing fields, LAME-family encoders append a 36-byte
extension beginning with a 9-byte version string (`LAME3.99r`):

| Offset | Field | Notes |
|-------:|-------|-------|
| 0  | encoder version | 9 bytes, e.g. `LAME3.99r` |
| 9  | tag revision + VBR method | low nibble: 1=CBR 2=ABR 3/4/5=VBR |
| 10 | lowpass filter | value x 100 Hz |
| 15 | replay gain | 16-bit word, big-endian (see below) |
| 20 | bitrate | 1 byte, kbps: minimum for VBR, target for ABR |
| 21 | encoder delay + padding | 12 bits each; the gapless-playback info |

The 16-bit replay-gain word packs, high bits first: a 3-bit name code
(1 = radio, 2 = audiophile), a 3-bit originator code, a sign bit, and a
9-bit magnitude in units of 0.1 dB. All zeros means unset. acidcat
decodes both the replay-gain word and the bitrate byte.

The encoder delay/padding pair is what lets gapless players trim the
codec's priming and trailing samples.

---

## ID3v1 Tag

The original tag format: a fixed 128-byte block at the very end of the
file.

```
struct id3v1 {
    char magic[3];     // "TAG"
    char title[30];
    char artist[30];
    char album[30];
    char year[4];
    char comment[30];  // ID3v1.1: byte 28 is 0x00 and byte 29 is the track number
    uint8_t genre;     // index into a fixed genre table
};
```

Fields are space- or null-padded Latin-1. ID3v2 supersedes it, but many
files carry both.

---

## Duration Calculation

```
samples_per_frame depends on version+layer (see table above)

if a Xing/Info frame_count is present:
    duration = frame_count * samples_per_frame / sample_rate   # exact, cheap
else for CBR:
    duration = audio_bytes * 8 / bitrate
else:
    walk every frame, count them, then use the frame_count formula
```

CBR-vs-VBR is decided by the presence of a `Xing` header, or by whether
a full frame walk sees more than one distinct bitrate.

---

## Notes

- the syncword is only 11 bits, so false positives happen; validate the
  whole header (reserved version/layer, free/invalid bitrate, reserved
  sample rate) before trusting a sync.
- `acidcat inspect FILE.mp3` walks the ID3v2 frame list, fully decodes
  the first MPEG frame, reads the Xing/LAME header, summarizes the frame
  run as CBR/VBR with a derived duration, and reports an ID3v1 trailer.
  `--frames` adds a per-frame listing (offset, bitrate, sample rate,
  mode, size), which exposes the per-frame bitrate switching of a VBR
  stream.
- for indexing, acidcat reads ID3v2 tags through mutagen; `inspect` is
  the hand-rolled structural view that also lints spec violations.
- a stray ID3/APE chunk mid-stream will break a naive frame walk; scan
  forward for the next valid sync rather than aborting.
