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

A robust parser checks for `ID3`, and otherwise scans forward for the
first valid frame sync (some files carry leading junk or an APE tag).

---

## ID3v2 Tag

Prepended to the front of the file. Header is 10 bytes:

```
struct id3v2_header {
    char     magic[3];      // "ID3"
    uint8_t  version_major; // 3 = ID3v2.3, 4 = ID3v2.4 (2 = ID3v2.2)
    uint8_t  version_minor;
    uint8_t  flags;         // bit 7 unsync, bit 6 extended, bit 4 footer
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

ID3v2.2 frames are smaller: 3-byte id, 3-byte plain size, no flags.

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
| `APIC` | attached picture   |
| `COMM` | comment            |

---

## MPEG Audio Frame Header

Every audio frame starts with a 4-byte header:

```
 syncword          11 bits   always set (0xFFE)
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

`Xing` means true VBR; `Info` means CBR written by LAME. VBRI is a
Fraunhofer-encoder variant at a fixed offset of 32 bytes; less common.

### LAME Tag

Immediately after the Xing fields, LAME-family encoders append a 36-byte
extension beginning with a 9-byte version string (`LAME3.99r`):

| Offset | Field | Notes |
|-------:|-------|-------|
| 0  | encoder version | 9 bytes, e.g. `LAME3.99r` |
| 9  | tag revision + VBR method | low nibble: 1=CBR 2=ABR 3/4/5=VBR |
| 10 | lowpass filter | value x 100 Hz |
| 21 | encoder delay + padding | 12 bits each; the gapless-playback info |

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
    char comment[30];  // last 2 bytes may hold a track number (ID3v1.1)
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
