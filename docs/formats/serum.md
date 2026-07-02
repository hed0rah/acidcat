# Serum Preset Format Internals

Low-level reference for Xfer Serum synthesizer preset files.

---

## Overview

Serum presets (.SerumPreset) are a hybrid format: a short binary header,
followed by a JSON metadata block, followed by binary wavetable and
modulation data. Serum 2 uses this format; Serum 1 used FXP (VST preset).

---

## File Structure

```
+-------------------------------------------+
| Header (9-11 bytes)                       |
|   "XferJson" + null + flags               |
+-------------------------------------------+
| JSON metadata block (~500-2000 bytes)     |
|   { "presetName": "...", ... }            |
+-------------------------------------------+
| Binary blob (remainder of file)           |
|   wavetable data, modulation, parameters  |
+-------------------------------------------+
```

### Header

```
offset  size  field
0       8     "XferJson"          magic bytes
8       1     0x00                null terminator
9       2     unknown             possibly version/format flags
11+     ...   JSON starts at first '{' byte
```

The JSON doesn't start at a fixed offset. Scan forward from the header
to find the first `{` character.

---

## JSON Metadata Block

The JSON is uncompressed plaintext embedded in the file. It terminates
before the binary wavetable data begins. There is no explicit length
field -- the parser must detect where valid JSON ends.

### Extraction Method

```python
raw = file.read()
json_start = raw.find(b"{")
text = raw[json_start:].decode("utf-8", errors="replace")
try:
    parsed, end = json.JSONDecoder().raw_decode(text)
except (ValueError, RecursionError):
    return {}
```

`raw_decode` scans the text once and stops at the first complete JSON
object, returning both the parsed object and the end index. That end
index is the JSON boundary; no trial-and-error slicing is needed, and
the whole extraction is a single linear pass regardless of how large
the metadata block is.

Two details matter:

- `end` is a character offset into the decoded text, not a byte
  offset. To locate the binary blob that follows, re-encode the parsed
  region: `end_bytes = len(text[:end].encode("utf-8"))`. This is exact
  for valid UTF-8 (which valid JSON is); it is only approximate when
  the JSON region itself held invalid bytes, where any offset is
  best-effort anyway.
- the `RecursionError` guard is deliberate: the json scanner recurses
  once per nesting level, so a forged preset with thousands of nested
  objects blows the stack instead of raising `JSONDecodeError`.

### Known Fields

| Field              | Type     | Description                     | Example                    |
|--------------------|----------|---------------------------------|----------------------------|
| `fileType`         | string   | always "SerumPreset"            | `"SerumPreset"`            |
| `presetName`       | string   | display name of the preset      | `"BASS - Demolish"`        |
| `presetAuthor`     | string   | creator/designer                | `"moonboy.store"`          |
| `presetDescription`| string   | usage notes                     | `"Use Modwheel for Delay"` |
| `product`          | string   | host synthesizer                | `"Serum2"`                 |
| `productVersion`   | string   | synth version                   | `"2.0.18"`                 |
| `version`          | string   | preset format version           | `"7.0"`                    |
| `tags`             | string[] | categorization tags             | `["Wavetable", "Mono"]`    |
| `hash`             | string   | MD5 hash of preset content      | `"a1b2c3d4..."`            |
| `vendor`           | string   | plugin developer                | `"Xfer Records"`           |
| `url`              | string   | vendor website                  | `"https://xferrecords.com/"`|

### Tag Vocabulary (observed)

Tags are standardized across the Serum preset ecosystem:

**Sound type tags:**
- `Wavetable` -- uses wavetable synthesis
- `Embedded-Data` -- wavetable data embedded in preset
- `Mono` / `Stereo` -- voice mode
- `Unison` -- uses unison voices

**Category tags (from preset browsers):**
- `Bass`, `Lead`, `Pad`, `Pluck`, `Keys`, `FX`, `Seq`, `Arp`

---

## Binary Blob

Everything after the JSON is binary data containing:

1. **Wavetable data** -- oscillator wavetables (256 frames x 2048 samples each)
2. **Modulation matrix** -- LFO shapes, envelope settings, mod routing
3. **Effect chain** -- reverb, delay, distortion parameters
4. **Oscillator settings** -- unison, detune, phase, warp mode

### Wavetable Compression

Wavetable data in the binary section is zlib-compressed. Each wavetable
consists of up to 256 frames, each frame 2048 samples of 32-bit float.
Uncompressed size for a full wavetable: 256 * 2048 * 4 = 2MB.

The compressed blocks can be identified by zlib magic (`78 9C` or `78 01`
or `78 DA`) within the binary data.

### Binary Structure (partial, reverse-engineered)

The binary blob's internal structure is undocumented. Known patterns:

- Multiple zlib-compressed blocks at varying offsets
- Parameter values stored as 32-bit floats
- Modulation routing stored as source/destination/amount triples
- Effect parameters grouped by effect type

Full binary parsing would require significant reverse engineering
effort. For metadata purposes, the JSON block contains everything
useful.

---

## Serum 1 vs Serum 2

| Feature       | Serum 1 (.fxp)           | Serum 2 (.SerumPreset)    |
|---------------|--------------------------|---------------------------|
| Container     | FXP (VST preset)         | XferJson + binary         |
| Metadata      | binary only              | JSON (rich metadata)      |
| Tags          | none                     | structured array          |
| Wavetables    | embedded                 | embedded + compressed     |
| Backward compat| Serum 2 reads these     | Serum 1 cannot read       |

---

## File Size Patterns

Preset file sizes correlate with content:

| Size range | Typical content                              |
|------------|----------------------------------------------|
| 5-20 KB    | simple preset, no embedded wavetables        |
| 50-200 KB  | preset with one custom wavetable             |
| 200-500 KB | preset with two custom wavetables            |
| 500KB-2MB  | complex preset with large/many wavetables    |

---

## acidcat inspect

`acidcat inspect FILE.SerumPreset` renders a structural view of the
file as three regions:

1. **magc** -- the 8-byte `XferJson` signature at offset 0
2. **json** -- the metadata block, with the known fields decoded and
   the preset name and key count in the summary
3. **blob** -- everything after the JSON: opaque wavetable and
   modulation data, reported by size only

The walker reads at most 4 MiB of the file; the JSON block always sits
near the front, and the blob region's size is computed from the file
size, so nothing past the cap needs reading. The json/blob boundary
comes from `raw_decode`'s end index, re-encoded from a character
offset to a byte offset as described above. A JSON block that fails to
parse (including via the `RecursionError` guard) is reported as a file
warning rather than crashing the walk.

---

## Notes

- the JSON portion is **not** length-prefixed -- `raw_decode`'s
  returned end index is what marks where the metadata stops and the
  binary blob begins
- all Serum 2 presets use this format regardless of content complexity
- the `tags` field is particularly useful for batch categorization
  of preset libraries
- `presetDescription` often contains performance notes ("use mod wheel",
  "automate macro 1") which could be searchable
- `product` field distinguishes Serum 2 presets from potential future
  Xfer products using the same container format
