# Native Instruments Kontakt Format Internals

Reverse-engineering notes for Kontakt instrument files (.nki),
cache files (.nkc), and resource containers (.nkr).

**Status: Partially reverse-engineered.** These are proprietary binary
formats with no public documentation. The notes below are from hex
analysis and string extraction of real files.

---

## File Types

| Extension | Purpose                    | Contains                          |
|-----------|----------------------------|-----------------------------------|
| `.nki`    | Kontakt Instrument         | instrument definition, mappings, scripts |
| `.nkc`    | Kontakt Cache              | resource cache (images, text)     |
| `.nkr`    | Kontakt Resource Container | audio samples (monolith format)   |
| `.nkm`    | Kontakt Multi              | multi-instrument configuration    |
| `.nksn`   | Kontakt Snapshot           | preset snapshot                   |

---

## NKI -- Kontakt Instrument

### Header

No consistent magic bytes across Kontakt versions. Observed first bytes:

```
31 5F 00 00 ...     (Kontakt 6.x)
```

The format has changed significantly across Kontakt versions (1 through 7).
Version markers within the file indicate which format revision is used.

### Chunk Markers

NI uses a convention of **reversed ASCII markers** throughout their
binary formats:

| Marker (hex)    | ASCII | Reversed | Probable meaning           |
|-----------------|-------|----------|----------------------------|
| `68 73 69 6E`   | hsin  | nish     | instrument header section  |
| `44 53 49 4E`   | DSIN  | NISD     | instrument data section    |
| `34 4B 49 4E`   | 4KIN  | NIK4     | Kontakt 4+ format marker   |

These markers appear at multiple offsets throughout the file,
suggesting a chunked structure similar to (but not compatible with)
RIFF.

### Observed Marker Positions (sample file)

```
offset    marker    context
0x00C     hsin      first instance, near file start
0x030     DSIN      first data section
0x044     DSIN      second data section
0x058     DSIN      third data section
0x0CA     4KIN      Kontakt 4+ marker
0x0DE     hsin      second instrument header
0x102     4KIN      second K4+ marker
...                 pattern repeats
```

### Extractable Strings

Two encoding types found within NKI files:

**UTF-16LE strings** (null-terminated, 2 bytes per character):
- instrument name: `"Kawaii Sounds vol.2"`
- Kontakt version: `"6.8.0.0"`
- attribute keys: `@color`, `@devicetypeflags`, `@soundtype`,
  `@tempo`, `@verl`, `@verm`, `@visib`
- categories: `"Sound"`, `"instrum"`

**ASCII strings** (embedded in binary data):
- KSP (Kontakt Script Processor) code fragments
- sample names referenced in scripts
- file path fragments

### Attribute Keys

Found as UTF-16LE strings, these are NI's internal metadata tags:

| Key                 | Type    | Meaning                    |
|---------------------|---------|----------------------------|
| `@tempo`            | float?  | instrument tempo (BPM)     |
| `@soundtype`        | string  | sound category             |
| `@color`            | int     | UI color code              |
| `@verl`             | string  | library version            |
| `@verm`             | string  | meta version               |
| `@visib`            | bool    | visibility in browser      |
| `@devicetypeflags`  | int     | device type bitmask        |

The `@tempo` attribute is the most interesting for acidcat -- it may
contain the BPM for rhythmic patches.

### KSP Script Fragments

Kontakt instruments often embed KSP (Kontakt Script Processor) code.
This can be found as ASCII text within the binary:

```
make_perfview
declare $range1 := 24
declare $range2 := 36
set_key_color(0, $KEY_COLOR_RED)
```

Script analysis can reveal:
- key range assignments
- sample zone mappings
- performance controls
- round-robin logic

---

## NKC -- Kontakt Cache

Binary format containing cached resources. Found alongside NKI files.

### Observed Structure

Contains a resource tree with entries like:

```
UTF-16LE strings:
  "Resources"           (root container name)
  "pictures"            (directory entry)
  "Chara.PNG"           (embedded image file)
  "Chara.txt"           (text resource)
  "Mono_Buttan.txt"     (button graphic reference, sic)
```

### File Entry Format (tentative)

```
uint32_t  name_length;       // in characters
char16_t  name[name_length]; // UTF-16LE
uint64_t  offset;            // data offset within NKC
uint32_t  flags;             // unknown
byte      data[];            // file content (PNG, text, etc.)
```

Embedded PNGs can be identified by the PNG magic bytes
(`89 50 4E 47 0D 0A 1A 0A`) within the NKC file.

---

## NKR -- Kontakt Resource Container (Monolith)

Contains the actual audio sample data referenced by NKI instruments.
Used when a library is distributed in "monolith" format (all samples
packed into one file instead of individual WAVs).

### Notes

- `file` command misidentifies NKR files as `ELF 32-bit LSB core file`
  due to coincidental byte patterns at the start
- NKR files can be very large (gigabytes for orchestral libraries)
- the internal structure is undocumented and appears to use a custom
  container with indexed sample data
- some community tools (e.g., NKR extractors) can unpack the audio
  samples, suggesting the format is tractable
- likely stores samples as WAV or FLAC internally, with an index table

---

## Version History

| Kontakt Version | Format Changes                              |
|-----------------|---------------------------------------------|
| Kontakt 1-3     | earlier binary format, no 4KIN markers      |
| Kontakt 4       | 4KIN markers introduced                     |
| Kontakt 5       | encryption support for commercial libraries |
| Kontakt 6       | extended attribute keys                     |
| Kontakt 7       | NKS integration, new snapshot format        |

### Encryption

Commercial Kontakt libraries use NI's encryption (tied to Serial
number / NI Access). Encrypted NKI files cannot be parsed without
the decryption key. Free/user-created instruments are unencrypted.

---

## Extraction Strategy for acidcat

### What we can extract now (without full parsing):
- instrument name (UTF-16LE string scan)
- Kontakt version (UTF-16LE string scan)
- attribute values (@tempo, @soundtype, etc.)
- KSP script fragments (ASCII scan)
- embedded PNG resources from NKC

### What would require deeper work:
- full zone/sample mapping
- modulation routing
- effect chain parameters
- decrypted commercial library data

### Recommended approach:
1. scan for UTF-16LE strings matching known attribute patterns
2. extract @tempo for BPM, @soundtype for categorization
3. count hsin/DSIN/4KIN markers to estimate instrument complexity
4. extract KSP script if present for key mapping analysis
5. flag encrypted files (detect encryption markers) and skip gracefully

---

## Open Source References

These projects have partial Kontakt format implementations:

- various NKI decompilers on GitHub (search "nki parser")
- Kontakt Script Processor (KSP) documentation from NI
- NKR extractor tools (for monolith unpacking)

Note: many of these work only with specific Kontakt versions.
