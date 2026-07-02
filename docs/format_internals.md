# Audio File Format Internals

Reverse-engineering notes and metadata extraction reference for audio-related
file formats. Each format has a dedicated deep-dive document in `formats/`.

Last updated: 2026-07-02

---

## Format Reference Index

### Audio Containers (acidcat parses these)

| Format | File | Status | What acidcat extracts |
|--------|------|--------|-----------------------|
| [RIFF / WAV](formats/riff_wav.md) ([anatomy](formats/wav-anatomy.html)) | `.wav` | Full support | BPM, key, loops, beats, duration, format, chunks, LIST/INFO metadata |
| RF64 (BWF, EBU Tech 3306) | `.wav` | inspect only | Same chunk set as RIFF/WAV, plus ds64: 64-bit riff/data sizes, sample count, chunk-size override table |
| [AIFF / IFF](formats/aiff.md) ([anatomy](formats/aiff-anatomy.html)) | `.aif`, `.aiff` | Full support | Duration, format, name, author, copyright, instrument tuning |
| [MIDI](formats/midi.md) ([anatomy](formats/midi-anatomy.html)) | `.mid`, `.midi` | Full support | BPM, key sig, time sig, tracks, note count/range, channels, duration |

RF64 asymmetry: `acidcat inspect` walks RF64 natively (ds64 resolves the
0xffffffff sentinels, including the per-chunk override table), but the
index sniffer (`_sniff_format`) only matches `RIFF....WAVE`, so an RF64
file sniffs as None on the index path and falls back to extension
dispatch (which routes `.wav` to the RIFF parser).

### Synth Presets (acidcat parses these)

| Format | File | Status | What acidcat extracts |
|--------|------|--------|-----------------------|
| [Serum Presets](formats/serum.md) | `.SerumPreset` | Full support | Preset name, author, description, tags, product version |
| [Arturia Banks](formats/arturia.md) | `.labx` | Research done | Synth engine, preset name, author, tags, parameters |

### Tagged Containers (index/extract path, parsed via mutagen)

Mutagen covers the index and extract paths only. `acidcat inspect` has
its own from-scratch walkers for MP3 and FLAC (see the section below);
OGG, Opus, and MP4 remain mutagen-only.

| Format | File | Status | What acidcat extracts |
|--------|------|--------|-----------------------|
| [MP3](formats/mp3.md) ([anatomy](formats/mp3-anatomy.html)) | `.mp3` | Full support | BPM, key, title, artist, album, genre, comment (ID3v2) |
| [FLAC](formats/flac.md) ([anatomy](formats/flac-anatomy.html)) | `.flac` | Full support | BPM, key, title, artist, album, genre (Vorbis Comment) |
| [OGG](formats/ogg.md) ([anatomy](formats/ogg-anatomy.html)) | `.ogg`, `.oga` | Full support | BPM, key, title, artist (Vorbis Comment) |
| [Opus](formats/ogg.md) | `.opus` | Full support | BPM, key, title, artist (Vorbis Comment) |
| [MP4 / M4A](formats/mp4_m4a.md) ([anatomy](formats/mp4-anatomy.html)) | `.m4a`, `.mp4` | Full support | BPM (tmpo), key, title, artist, album (iTunes atoms) |

### Proprietary Instruments (partial reverse-engineering)

| Format | File | Status | Potential extraction |
|--------|------|--------|-----------------------|
| [Kontakt](formats/kontakt.md) | `.nki`, `.nkc`, `.nkr` | Partial RE | Instrument name, version, @tempo, @soundtype, KSP scripts |

### Not yet documented

| Format | File | Notes |
|--------|------|-------|
| Ableton Live Pack | `.alp` | gzip + custom `pl-a` container, embedded FLAC |
| REX / RX2 | `.rx2` | AIFF internally, proprietary slice chunks |
| SoundFont | `.sf2` | RIFF-based, could reuse chunk parser |
| DLS | `.dls` | RIFF-based, MIDI instrument definition |
| FXP / FXB | `.fxp`, `.fxb` | VST preset/bank, simple header + binary blob |
| Tracker modules | `.mod`, `.xm`, `.s3m`, `.it` | embedded samples, patterns, BPM |

---

## What `acidcat inspect` Decodes

`inspect` never goes through mutagen. It carries native walkers for
every format it supports and prints the structure with byte offsets:

- **WAV/RIFF** -- fmt including WAVEFORMATEXTENSIBLE (cbSize, valid
  bits, channel mask, subformat GUID), fact, data, acid, smpl, inst,
  cue, LIST (INFO + adtl), bext v0/v1/v2 (UMID, loudness values,
  CodingHistory).
- **RF64** -- same grammar as RIFF, plus ds64: 64-bit riff/data sizes,
  sample count, and the chunk-size override table that resolves any
  other sentinel-sized chunk.
- **AIFF/AIFC** -- COMM (80-bit extended rate, compression types),
  SSND, MARK, INST, basc/cate/trns (Apple Loops), COMT, AESD (AES3
  channel status), APPL, text chunks.
- **MIDI** -- MThd plus a per-track event scan: tempo map, time and
  key signatures, SMPTE offset (frame rate + HH:MM:SS:FR.ff), track
  names, note stats; `--frames` lists every event.
- **MP3** -- ID3v2.2/2.3/2.4 frame enumeration, ID3v1, MPEG frame
  headers, Xing/Info, VBRI (Fraunhofer), and the LAME extension
  (encoder, replay gain, delay/padding); `--frames` walks every
  MPEG frame.
- **FLAC** -- STREAMINFO, VORBIS_COMMENT, SEEKTABLE, APPLICATION,
  CUESHEET (per-track index points), PICTURE, PADDING.
- **Serum** -- XferJson magic, the JSON metadata block, and the
  opaque wavetable/modulation blob boundary.

---

## Extraction Tier Summary

### Tier 1: Implemented in acidcat
- **WAV/RIFF** -- full chunk parsing, ACID/SMPL/inst/cue/LIST/bext.
  64KB chunk read cap (F-05) protects against malformed chunk_size.
- **RF64** -- inspect-only walk with ds64 sentinel resolution. the
  index sniffer does not match the RF64 magic (see the asymmetry note
  above).
- **AIFF/IFF** -- full chunk parsing, COMM/INST/NAME/AUTH/(c)/ANNO.
  AIFC compression types validated against a known set (F-12).
- **MIDI** -- header + track parsing, meta events, note statistics.
  Sysex VLQ length bounded against MTrk remaining (F-06).
- **Serum** -- JSON metadata extraction from XferJson container.
  Linear-pass `raw_decode` parser (F-01).
- **MP3 / FLAC / OGG / Opus / M4A** -- via mutagen on the index and
  extract paths (base dep since v0.5.4). UTF-8 BOM stripped from tag
  values (F-26). inspect uses native walkers for MP3 and FLAC.
- **Format dispatch** -- `_sniff_format` reads first 12 bytes and
  identifies all of the above; extension is fallback only (F-21).

### Tier 2: Documented, ready to implement
- **Arturia LABX** -- ZIP + text serialization, ~60 lines

### Tier 3: Research in progress
- **Kontakt NKI** -- marker scanning + string extraction
- **Ableton ALP** -- undocumented `pl-a` container
- **REX2** -- proprietary AIFF chunks

### Tier 4: Future exploration
- **SoundFont SF2** -- RIFF-based, high value
- **Tracker modules** -- well-documented formats, embedded BPM/samples
- **FXP/FXB** -- VST presets, simple container

---

## Document Conventions

Each format document follows this structure:

1. **Overview** -- what it is, magic bytes, endianness
2. **Structure** -- container layout, chunk/atom/page hierarchy
3. **Field reference** -- struct layouts, field tables, hex examples
4. **Metadata extraction** -- what's useful for acidcat
5. **Notes** -- quirks, edge cases, implementation tips

Struct notation uses C-style types:
- `uint32_t` -- unsigned 32-bit integer
- `int16_t` -- signed 16-bit integer
- `char[N]` -- N-byte ASCII string
- `byte` -- unsigned 8-bit
- `float` -- IEEE 754 32-bit
- LE/BE suffix indicates endianness when ambiguous

Hex examples show raw bytes as they appear in the file.
