# Bitwig Multisample (.multisample) Format Internals

Low-level reference for Bitwig Studio `.multisample` instrument files, the
zip-based multi-zone sampler format the Sampler device loads.

---

## Overview

A `.multisample` is a **ZIP archive**: a `multisample.xml` manifest plus the
member sample files (WAV, occasionally FLAC/AIFF). The manifest names the
instrument and lists `<sample>` zones, each mapping one file to a key range, a
root note, a velocity range, and a loop. It is how Bitwig packages a
multi-sampled instrument (a drum kit, a sampled synth) into one portable file.

This is the one Bitwig type acidcat does not identify by leading magic: the file
starts with the generic ZIP local-file-header magic (`PK\x03\x04`), shared with
`.bwextension` (a JAR) and any other zip. acidcat therefore peeks inside, a zip
whose archive contains `multisample.xml` is a `.multisample`.

---

## File Structure

```
PK\x03\x04 ...                         standard ZIP archive
  multisample.xml                      the manifest (see below)
  <sample 1>.wav                       member sample files, one per zone
  <sample 2>.wav
  ...
```

### The CRC quirk

Bitwig writes every entry **STORED** (uncompressed, `compress_size == file_size`)
but with a **CRC-32 that does not match the data**. Python's `zipfile.read()`
validates the CRC and raises `BadZipFile` on these files. To read an entry,
seek to its local-file-header offset, skip the 30-byte header plus the filename
and extra fields, and read `compress_size` bytes directly, no CRC check. (The
central directory, and therefore `namelist()`, is unaffected; only per-entry
`read()` validates the CRC.)

---

## multisample.xml

```xml
<multisample name="F9 SN EPROM Snares 1">
  <generator>Translator 7</generator>
  <category>General</category>
  <creator>Translator 7 User</creator>
  <description/>
  <sample file="F9 SN EPROM Snr 01.wav" gain="0.00" reverse="false"
          sample-start="0.000" sample-stop="5208.000" zone-logic="always-play">
    <key high="36" low="36" root="36" track="1.00" tune="0.00"/>
    <velocity/>
    <select/>
    <loop fade="0.0000" mode="off" start="0.000" stop="5208.000"/>
  </sample>
  ...
</multisample>
```

### Manifest fields

| element / attribute | meaning |
|---|---|
| `<multisample name>` | instrument name |
| `<generator>` | tool that wrote the file (Bitwig, or a converter like Translator) |
| `<category>` / `<creator>` / `<description>` | library metadata |
| `<sample file>` | member file this zone plays |
| `<sample sample-start/stop>` | playback window into the file (samples) |
| `<key root/low/high>` | root MIDI note and the key range the zone covers |
| `<key track/tune>` | keyboard tracking and fine tune |
| `<velocity low/high>` | velocity layer range (empty = full range) |
| `<loop mode/start/stop>` | loop mode (`off`/...) and loop points |

A drum kit is typically many single-note zones (`low == high == root`)
laid out chromatically; a sampled instrument uses wider `low..high` ranges
and multiple velocity layers over the same key span.

---

## acidcat inspect

`acidcat inspect FILE.multisample` renders:

1. **multisample.xml** , the manifest: `name`, `generator`, `category`,
   `creator`, the zone count, and the member-file count, with a summary like
   `F9 SN EPROM Snares 1: 24 zone(s), 24 sample file(s)`.
2. **zone** (one per `<sample>`, capped) , the file it plays, its root note,
   key range, velocity range, and loop (when not `off`). Each zone's byte
   `offset` is the member file's real position in the zip.

The walker reads only the manifest and the zip central directory (not the sample
audio), tolerates the bad CRC by seeking, and degrades a malformed
`multisample.xml` to a warning rather than crashing.

---

## Notes

- Pure stdlib: `zipfile` + `xml.etree`. No new dependency.
- The zip-peek in the sniffer is acidcat's only content sniff that opens the
  container; the leading magic alone cannot separate a `.multisample` from any
  other zip.
- The bad-CRC quirk is Bitwig-specific and consistent across files; do not treat
  it as corruption.
- Member files are ordinary WAVs, so each can itself be walked by the WAV walker
  after extraction; the multisample layer is purely the zone map.
