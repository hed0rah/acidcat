# Bitwig Wavetable (.wt) Format Internals

Low-level reference for Bitwig Studio wavetable files, as used by Polymer,
the Sampler in wavetable mode, and other wavetable-capable devices.

---

## Overview

A `.wt` file is a `vawt` container: a fixed 12-byte little-endian header
followed by raw signed 16-bit PCM samples. It holds a wavetable, one or more
single-cycle waveforms ("frames") stacked end to end, that a wavetable
oscillator scans through. Bitwig writes one of these whenever you drop a WAV
onto Polymer or another wavetable device; the WAV's audio is sliced into frames
and rewritten in this format.

It is its own container, not a RIFF chunk. The `vawt` magic sits at byte 0.
(Do not confuse it with the `BWBM` beat-map chunk Bitwig stores *inside* WAV
files; that is a chunk within RIFF, this is a standalone format.)

Reverse-engineered from 5636 factory and third-party `.wt` files; every one
conformed to the structure below with zero exceptions.

---

## File Structure

```
+-------------------------------------------+
| Header (12 bytes, little-endian)          |
|   "vawt" + frame_samples + frame_count    |
|   + data_offset                           |
+-------------------------------------------+
| Sample data                               |
|   frame_count * frame_samples int16 LE,   |
|   frame-major (wave 0, then wave 1, ...)  |
+-------------------------------------------+
```

There is no footer. The file size is always exactly
`12 + frame_count * frame_samples * 2`.

### Header

```
offset  size  field           notes
0       4     "vawt"          magic bytes (ASCII)
4       4     frame_samples   uint32 LE: samples in one single-cycle wave
8       2     frame_count     uint16 LE: number of waves in the table
10      2     data_offset     uint16 LE: byte offset of sample data (always 12)
```

```c
struct wt_header {          // 12 bytes, little-endian
    char     magic[4];      // "vawt"
    uint32_t frame_samples; // samples per single-cycle wave (256 / 1024 / 2048)
    uint16_t frame_count;   // number of waves stacked in the table
    uint16_t data_offset;   // always 12 (= header size; where samples begin)
};
```

---

## Sample Data

Immediately after the header: `frame_count * frame_samples` signed 16-bit
little-endian samples, laid out **frame-major**. Frame 0's complete cycle comes
first, then frame 1's, and so on. There is no per-frame header and no interleave.

- Format: signed 16-bit PCM, little-endian, full-scale (observed range spans the
  full `-32768..+32767`).
- Per-frame byte size: `frame_samples * 2`.
- To read frame `i`: seek to `12 + i * frame_samples * 2`, read `frame_samples`
  int16 values.

### Observed frame sizes

`frame_samples` is not fixed; three values appear in the wild:

| frame_samples | share of corpus | note |
|---|---|---|
| 2048 | ~91% (5122/5636) | the common Bitwig/Serum-lineage resolution |
| 256  | ~9% (513/5636)   | lower-resolution tables |
| 1024 | 1 file           | rare |

`frame_count` ranges from 1 (a single-cycle waveform) to 256 and beyond (a WAV
dropped in becomes as many frames as its length divides into).

---

## acidcat inspect

`acidcat inspect FILE.wt` renders two regions:

1. **vawt** , the 12-byte header, with `magic`, `frame_samples`, `frame_count`,
   and `data_offset` decoded, and a summary like
   `Bitwig wavetable, 256 frame(s) x 2048 samples, 16-bit`.
2. **samples** , the sample block, reported by count
   (`524,288 int16 LE samples, frame-major`).

The walker reads only the 12-byte header; the sample region's size is derived
from the file size. It warns if `data_offset` is not 12 or if the file size does
not match `12 + frame_count * frame_samples * 2` (a truncated or padded table).

---

## Notes

- The format is honest and self-describing: header plus a flat int16 array, no
  compression, no footer, no length ambiguity. Contrast with Serum's
  `.SerumPreset`, where wavetable data is zlib-compressed 32-bit float.
- Frame-major layout means a single wave is contiguous, so slicing out one
  cycle is a plain seek + read; no de-interleave step.
- Endianness is little-endian throughout, including the samples, unlike AIFF's
  big-endian PCM.
- A round trip through Bitwig (drop a WAV in, export the table) is lossy to
  16-bit if the source was float, and re-frames the audio to the device's frame
  size.
