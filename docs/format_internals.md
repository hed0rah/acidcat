# Audio File Format Internals

Reverse-engineering notes and metadata extraction reference for audio-related
file formats. Each format has a dedicated deep-dive document in `formats/`.

Last updated: 2026-05-02

---

## Format Reference Index

### Audio Containers (acidcat parses these)

| Format | File | Status | What acidcat extracts |
|--------|------|--------|-----------------------|
| [RIFF / WAV](formats/riff_wav.md) | `.wav` | Full support | BPM, key, loops, beats, duration, format, chunks, LIST/INFO metadata |
| [AIFF / IFF](formats/aiff.md) | `.aif`, `.aiff` | Full support | Duration, format, name, author, copyright, instrument tuning |
| [MIDI](formats/midi.md) | `.mid`, `.midi` | Full support | BPM, key sig, time sig, tracks, note count/range, channels, duration |

### Synth Presets (acidcat parses these)

| Format | File | Status | What acidcat extracts |
|--------|------|--------|-----------------------|
| [Serum Presets](formats/serum.md) | `.SerumPreset` | Full support | Preset name, author, description, tags, product version |
| [Arturia Banks](formats/arturia.md) | `.labx` | Research done | Synth engine, preset name, author, tags, parameters |

### Tagged Containers (acidcat parses these via mutagen)

| Format | File | Status | What acidcat extracts |
|--------|------|--------|-----------------------|
| MP3 | `.mp3` | Full support | BPM, key, title, artist, album, genre, comment (ID3v2) |
| FLAC | `.flac` | Full support | BPM, key, title, artist, album, genre (Vorbis Comment) |
| OGG | `.ogg`, `.oga` | Full support | BPM, key, title, artist (Vorbis Comment) |
| Opus | `.opus` | Full support | BPM, key, title, artist (Vorbis Comment) |
| MP4 / M4A | `.m4a`, `.mp4` | Full support | BPM (tmpo), key, title, artist, album (iTunes atoms) |

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

## Extraction Tier Summary

### Tier 1: Implemented in acidcat
- **WAV/RIFF** -- full chunk parsing, ACID/SMPL/inst/cue/LIST/bext.
  64KB chunk read cap (F-05) protects against malformed chunk_size.
- **AIFF/IFF** -- full chunk parsing, COMM/INST/NAME/AUTH/(c)/ANNO.
  AIFC compression types validated against a known set (F-12).
- **MIDI** -- header + track parsing, meta events, note statistics.
  Sysex VLQ length bounded against MTrk remaining (F-06).
- **Serum** -- JSON metadata extraction from XferJson container.
  Linear-pass `raw_decode` parser (F-01).
- **MP3 / FLAC / OGG / Opus / M4A** -- via mutagen (base dep since
  v0.5.4). UTF-8 BOM stripped from tag values (F-26).
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
