# Audio File Format Internals

Reverse-engineering notes and metadata extraction reference for audio-related
file formats. Each format has a dedicated deep-dive document in `formats/`.

Last updated: 2026-04-05

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

### Audio Containers (future targets)

| Format | File | Status | Potential extraction |
|--------|------|--------|-----------------------|
| [MP4 / M4A](formats/mp4_m4a.md) | `.m4a`, `.mp4` | Documented | BPM (tmpo), key, artist, title, album, cover art |
| [OGG](formats/ogg.md) | `.ogg`, `.opus` | Documented | BPM, key, artist, title (Vorbis Comment tags) |

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
- **WAV/RIFF** -- full chunk parsing, ACID/SMPL/inst/cue/LIST/bext
- **AIFF/IFF** -- full chunk parsing, COMM/INST/NAME/AUTH/(c)/ANNO
- **MIDI** -- header + track parsing, meta events, note statistics
- **Serum** -- JSON metadata extraction from XferJson container

### Tier 2: Documented, ready to implement
- **Arturia LABX** -- ZIP + text serialization, ~60 lines
- **MP4/M4A** -- atom tree traversal for iTunes metadata
- **OGG/Opus** -- page scanning for Vorbis Comment tags

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
