# Audio File Format Internals

Reverse-engineering notes and metadata extraction reference for audio-related
file formats. Each format has a dedicated deep-dive document in `formats/`.

Last updated: 2026-07-06

---

## Two paths, two sniffers

acidcat has two independent code paths, and they see formats differently:

- **inspect path** (`acidcat inspect`): from-scratch, zero-dependency walkers in
  `core/walk/*`, dispatched by the canonical sniffer in `core/sniff.py`
  (`sniff_bytes` reads 16 bytes). This path never touches mutagen.
- **index/extract path** (`acidcat info`/`scan`/`index`): metadata via mutagen
  plus the native preset parsers, dispatched by `_sniff_format` in
  `core/indexing.py` (also 16 bytes, but a smaller format set: midi, aiff, wav,
  serum, flac, ogg, mp3, mp4, and content-sniffed presets).

RF64 asymmetry: `acidcat inspect` walks RF64 natively (ds64 resolves the
0xffffffff sentinels, including the per-chunk override table), but the index
sniffer `_sniff_format` only matches `RIFF....WAVE`, so an RF64 file sniffs as
None on the index path and falls back to extension dispatch (which routes `.wav`
to the RIFF parser).

---

## Format Reference Index

### Audio containers (inspect walks these natively)

| Format | File | Path | What acidcat extracts |
|--------|------|------|-----------------------|
| [RIFF / WAV](formats/riff_wav.md) ([anatomy](formats/wav-anatomy.html)) | `.wav` | inspect + index | BPM, key, loops, beats, duration, format, chunks, LIST/INFO, bext, cart, iXML |
| [RF64 / BW64](formats/rf64.md) (EBU Tech 3306) | `.wav` | inspect only | Same chunk set as RIFF/WAV, plus ds64: 64-bit riff/data sizes, sample count, chunk-size override table |
| [AIFF / AIFC](formats/aiff.md) ([anatomy](formats/aiff-anatomy.html)) | `.aif`, `.aiff` | inspect + index | Duration, format, name, author, copyright, markers, instrument tuning, Apple Loops |
| [MIDI](formats/midi.md) ([anatomy](formats/midi-anatomy.html)) | `.mid`, `.midi` | inspect + index | BPM, key sig, time sig, tracks, note count/range, channels, duration |
| [RMID](formats/rmid.md) | `.rmid` | inspect only | The RIFF wrapper, then the wrapped Standard MIDI File (delegated to the MIDI walker) |

### Tagged containers (inspect native; index/extract via mutagen)

`acidcat inspect` carries its own from-scratch walkers for MP3, FLAC, OGG/Opus,
and MP4/M4A. The index and extract paths (`info`/`scan`/`index`) read the same
files through mutagen.

| Format | File | Path | What acidcat extracts |
|--------|------|------|-----------------------|
| [MP3](formats/mp3.md) ([anatomy](formats/mp3-anatomy.html)) | `.mp3` | inspect + index | BPM, key, title, artist, album, genre, comment (ID3v2); Xing/LAME |
| [FLAC](formats/flac.md) ([anatomy](formats/flac-anatomy.html)) | `.flac` | inspect + index | BPM, key, title, artist, album, genre (Vorbis Comment); cuesheet, picture |
| [OGG](formats/ogg.md) ([anatomy](formats/ogg-anatomy.html)) | `.ogg`, `.oga` | inspect + index | BPM, key, title, artist (Vorbis Comment); sample rate, channels, duration |
| [Opus](formats/ogg.md) | `.opus` | inspect + index | BPM, key, title, artist (Vorbis Comment); OpusHead/OpusTags |
| [MP4 / M4A](formats/mp4_m4a.md) ([anatomy](formats/mp4-anatomy.html)) | `.m4a`, `.mp4` | inspect + index | BPM (tmpo), key, title, artist, album (iTunes atoms); box tree, codec |

### Synth / DAW presets (inspect walks these natively)

| Format | File | Path | What acidcat extracts |
|--------|------|------|-----------------------|
| [Serum](formats/serum.md) | `.SerumPreset` | inspect + index | Preset name, author, description, tags, product version |
| [VST FXP](formats/fxp.md) | `.fxp` | inspect only | CcnK container, preset kind (FxCk/FPCh), plugin id (FourCC), version, preset name |
| [ReCycle RX2](formats/rx2.md) | `.rx2` | inspect only | CAT/REX2 IFF tree, creator, slice count (recursing the nested slice group) |
| Bitwig | `.bwpreset`, `.bwclip` | inspect + index | Device tree, parameters, clip notes |
| Vital | `.vital` | inspect + index | Patch name, author, tags, modulation matrix |
| Native Instruments | `.nmsv`, `.nabs`, `.nki`, `.ksd`, `.nksf` | inspect + index | Preset metadata, NKS tags, FastLZ subtree (hsin) |
| NCW | `.ncw` | inspect only | NI Compressed Wave header, channel/block info |
| [Bitwig WT](formats/bitwig-wt.md) | `.wt` | inspect only | vawt header: frame count, samples/frame, 16-bit sample block |

### Not yet implemented

| Format | File | Notes |
|--------|------|-------|
| Arturia Banks | `.labx` | ZIP + text serialization; research done, ~60 lines to implement |
| Ableton Live Pack | `.alp` | gzip + custom `pl-a` container, embedded FLAC |
| Kontakt (deep) | `.nki`, `.nkc`, `.nkr` | partial RE: instrument name, version, @tempo, KSP scripts |
| SoundFont | `.sf2` | RIFF-based, could reuse the chunk parser |
| DLS | `.dls` | RIFF-based, MIDI instrument definition |
| Tracker modules | `.mod`, `.xm`, `.s3m`, `.it` | embedded samples, patterns, BPM |

---

## What `acidcat inspect` Decodes

`inspect` never goes through mutagen. It carries native walkers (`core/walk/*`)
for every format it supports and prints the structure with byte offsets:

- **WAV/RIFF** -- fmt including WAVEFORMATEXTENSIBLE (cbSize, valid bits, channel
  mask, subformat GUID), fact, data, acid, smpl, inst, cue, LIST (INFO + adtl),
  bext v0/v1/v2 (UMID, loudness values, CodingHistory), cart (AES46 radio
  automation), iXML (field-recorder metadata), BWBM (Bitwig beat map).
- **RF64** -- same grammar as RIFF, plus ds64: 64-bit riff/data sizes, sample
  count, and the chunk-size override table that resolves any sentinel-sized chunk.
- **AIFF/AIFC** -- COMM (80-bit extended rate, compression types incl. sowt/fl32),
  SSND, MARK, INST, basc/cate/trns (Apple Loops), COMT, AESD, APPL, text chunks.
- **MIDI** -- MThd plus a per-track event scan: tempo map, time and key
  signatures, SMPTE offset, track names, note stats; `--frames` lists every event.
- **RMID** -- the RIFF wrapper, then the wrapped SMF handed to the MIDI walker
  with offsets shifted into place.
- **MP3** -- ID3v2.2/2.3/2.4 frame enumeration (id, size, flags, encoding), ID3v1,
  MPEG frame headers, Xing/Info, VBRI (Fraunhofer), and the LAME extension
  (encoder, replay gain, delay/padding); `--frames` walks every MPEG frame.
- **FLAC** -- STREAMINFO, VORBIS_COMMENT, SEEKTABLE, APPLICATION, CUESHEET
  (per-track index points), PICTURE, PADDING.
- **OGG** -- page structure, the identification header (sample rate, channels),
  Vorbis Comment / OpusTags, and duration from the last granule position.
- **MP4/M4A** -- the box tree, codec info, and iTunes atoms (tmpo, trkn/disk as
  index/total, freeform); `--anomalies` flags an mdat coverage gap.
- **Serum** -- XferJson magic, the JSON metadata block, and the opaque
  wavetable/modulation blob boundary.
- **Bitwig WT** -- the `vawt` header (samples per single-cycle wave, frame
  count, data offset) and the frame-major int16 sample block.
- **VST FXP** -- the CcnK container, preset kind (FxCk/FPCh), plugin id, version
  fields, preset name, and the opaque plugin chunk as a region.
- **ReCycle RX2** -- the CAT/REX2 IFF chunk tree, creator string, and slice count.
- **Bitwig / Vital / Native Instruments / NCW** -- device/parameter trees, the
  Vital modulation matrix, NKS tags and the FastLZ subtree, and the NCW header;
  `--verbose` expands these.

---

## Extraction Tier Summary

### Tier 1: Implemented (native inspect walkers)

- **WAV/RIFF, RF64, AIFF/AIFC, MIDI, RMID** -- full chunk/event parsing with
  bounded reads (a payload cap protects against a forged chunk_size).
- **MP3, FLAC, OGG/Opus, MP4/M4A** -- native inspect walkers (no mutagen on the
  inspect path); mutagen still serves the index/extract paths.
- **Serum, VST FXP, ReCycle RX2, Bitwig, Vital, Native Instruments, NCW** --
  native preset/instrument walkers.
- **Format dispatch** -- `core/sniff.py` (inspect) and `_sniff_format` in
  `core/indexing.py` (index) both read 16 bytes and trust magic over extension.

### Tier 2: Documented, ready to implement
- **Arturia LABX** -- ZIP + text serialization, ~60 lines

### Tier 3: Research in progress
- **Kontakt NKI (deep)** -- marker scanning + string extraction beyond the hsin walker
- **Ableton ALP** -- undocumented `pl-a` container

### Tier 4: Future exploration
- **SoundFont SF2 / DLS** -- RIFF-based, could reuse the chunk parser
- **Tracker modules** -- well-documented, embedded BPM/samples

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
