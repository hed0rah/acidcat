# Arturia Analog Lab Banks (.labx)

Reverse-engineering notes for Arturia preset bank files used by
Analog Lab, V Collection, and related instruments.

---

## Overview

LABX files are **standard ZIP archives** with no compression (store
method). Each file in the archive is an individual preset in Arturia's
text-based serialization format.

This is one of the easiest proprietary preset formats to work with.

---

## Container Structure

### ZIP Layer

```
Magic bytes: PK\x03\x04 (standard ZIP local file header)

Standard ZIP tools work:
  unzip -l preset_bank.labx      # list contents
  unzip preset_bank.labx -d out/ # extract
  python zipfile.ZipFile(path)   # programmatic access
```

No compression is used (method = store), so the presets are directly
readable inside the ZIP without decompression.

### Archive Layout

```
{SynthEngine}/User/{BankName}/{PresetName}
```

Example file listing:

```
DX7/User/RED EYE/Arp 1 - Red Eye
DX7/User/RED EYE/Bass 1 - Red Eye
Prophet-5/User/RED EYE/Bass 3 - Red Eye
Pigments/User/RED EYE/Bell 1 - Red Eye
Jun-6/User/RED EYE/Bass 6 - Red Eye
Jup-8/User/RED EYE/Bass 8 - Red Eye
Pigments/User/RED EYE/Pad 2 - Red Eye
```

The directory structure itself is metadata:
- **top-level directory**: synth engine (maps to Arturia virtual instrument)
- **second level**: always "User" for user presets
- **third level**: bank name
- **filename**: preset name

---

## Synth Engines

The top-level directory identifies which Arturia virtual instrument
the preset targets:

| Directory    | Arturia Instrument         | Original hardware        |
|--------------|----------------------------|--------------------------|
| `DX7`        | DX7 V                      | Yamaha DX7               |
| `Prophet-5`  | Prophet V                  | Sequential Prophet-5     |
| `Pigments`   | Pigments                   | (Arturia original)       |
| `Jun-6`      | Jun-6 V                    | Roland Juno-6/60         |
| `Jup-8`      | Jup-8 V                    | Roland Jupiter-8         |
| `Mini`       | Mini V                     | Moog Minimoog            |
| `CS-80`      | CS-80 V                    | Yamaha CS-80             |
| `SEM`        | SEM V                      | Oberheim SEM             |
| `Wurli`      | Wurli V                    | Wurlitzer EP200          |
| `B-3`        | B-3 V                      | Hammond B-3              |
| `Piano`      | Piano V                    | (acoustic piano models)  |
| `Analog-Lab` | Analog Lab                 | (multi-engine)           |

---

## Preset File Format

Each preset file contains Arturia's text-based serialization format,
based on Boost.Serialization.

### Header

```
22 serialization::archive 10 0 7 0 7
```

- `22` -- archive format version
- `serialization::archive` -- format identifier
- remaining numbers -- Boost.Serialization version/flags

### Field Layout (observed order)

The serialization is positional, not tagged. Fields appear in a fixed
order separated by whitespace:

```
field 1:  <name_len> <preset_name>
field 2:  <bank_len> <bank_name>
field 3:  <author_len> <author>
field 4:  <reserved_len> <reserved>
field 5:  <desc_len> <description>
field 6:  <timestamp>                    // Unix epoch
field 7:  <version_len> <software_version>
field 8:  <chars_len> <characteristics>  // see below
field 9:  <factory_len> <factory_info>
field 10: <subtype_len> <subtype>
field 11: <type_len> <type>
field 12: parameter key-value pairs
```

### Characteristics Field

A structured string using `|` and `;` delimiters:

```
Characteristics,Arpeggiated|Delay|Reverb|Shimmer|;Genres,Hip Hop/Trap|Chill|Ambient|;Styles,Airy|Bright|Evolving|;
```

Structure:
```
{Category},{Value1}|{Value2}|...{ValueN}|;{Category2},{Value1}|...;
```

Categories observed:
- `Characteristics` -- sound attributes (Arpeggiated, Delay, Reverb, etc.)
- `Genres` -- musical genres (Hip Hop/Trap, Chill, Ambient, etc.)
- `Styles` -- timbral qualities (Airy, Bright, Evolving, Dark, etc.)

### Type/Subtype Taxonomy

```
Types:      Bass, Keys, Lead, Pad, Sequence, FX, Organ, Strings, Brass
Subtypes:   Arpeggio, Electric Piano, Synth Bass, Analog Pad, etc.
```

### Parameter Key-Value Pairs

After the metadata fields, the preset contains synthesizer parameters
as space-separated key-value pairs:

```
Algorithm 0.12903225
Arp Hold 0
Arp Rate 0.5
FX Mix 1 0.375
Filter Cutoff 0.72
...
```

Values are normalized floats (0.0-1.0 range, mapped to the parameter's
actual range by the synth engine).

---

## Parsing Strategy

### Python extraction:

```python
import zipfile

with zipfile.ZipFile("bank.labx") as zf:
    for info in zf.infolist():
        path_parts = info.filename.split("/")
        engine = path_parts[0]          # e.g. "DX7"
        bank = path_parts[2]            # e.g. "RED EYE"
        preset = path_parts[3]          # e.g. "Arp 1 - Red Eye"

        content = zf.read(info.filename).decode("utf-8", errors="replace")
        # parse serialization fields from content
```

### Parsing the serialization format:

The format is fragile but parseable with careful positional extraction.
A regex-based approach works for the known field layout. Key challenge
is that string fields are length-prefixed, so a parser must read the
length first, then consume exactly that many characters.

```
# pseudo-parser
def read_field(data, pos):
    # read length (decimal integer terminated by whitespace)
    length_str = ""
    while data[pos].isdigit():
        length_str += data[pos]
        pos += 1
    length = int(length_str)
    pos += 1  # skip space
    value = data[pos:pos + length]
    pos += length
    return value, pos
```

---

## File Size Patterns

Preset sizes vary by synth engine:

| Engine       | Typical size | Reason                              |
|--------------|-------------|--------------------------------------|
| DX7          | 40-60 KB    | algorithmic FM synthesis, small      |
| Prophet-5    | 50-80 KB    | analog modeling, moderate            |
| Jun-6        | 50-80 KB    | similar to Prophet-5                 |
| Pigments     | 4-9 MB      | contains custom wavetable data       |
| Multi-engine | varies      | depends on which engines are used    |

Pigments presets are outliers because they can embed custom wavetable
frames directly in the preset data (similar to Serum).

---

## Notes

- the ZIP-with-no-compression approach means the format is trivially
  accessible -- no custom binary parsing needed for the container layer
- the Boost.Serialization text format is stable across Arturia versions
  observed so far, but may change in future releases
- the Characteristics field is the richest metadata source -- it
  contains the same tags visible in Arturia's preset browser
- bank files (.labx) contain only preset definitions, not audio data
  (the synth engine generates audio in real-time)
- Arturia's "Sound Store" presets may use a different format or
  additional DRM layer
- for acidcat, the most useful extraction targets are: preset name,
  author, engine, type/subtype, characteristics tags, and parameter
  summary statistics
