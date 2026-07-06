# VST2 FXP Preset Format Internals

Low-level reference for VST 2 `.fxp` single-preset files (and `.fxb` banks).

---

## Overview

An `.fxp` file is a `CcnK` container: a fixed header followed by a preset
payload that is either a list of float parameters or an opaque plugin blob.
It is the interchange format Steinberg's VST 2 SDK defines for saving a single
program (`.fxp`) or a whole bank (`.fxb`). **All multi-byte fields are
big-endian**, unusual on little-endian hosts and a common source of bugs.

The payload is plugin-specific and undocumented per plugin, so acidcat decodes
the container and identifies the plugin, but reports the payload as a region.

---

## File Structure

```
+-------------------------------------------+
| Header (28 bytes, big-endian)             |
|   "CcnK" + sizes + fxMagic + plugin id    |
+-------------------------------------------+
| Preset name (28 bytes, single preset)     |
+-------------------------------------------+
| Payload                                   |
|   FxCk: numParams * float32               |
|   FPCh: chunk_size + opaque plugin blob   |
+-------------------------------------------+
```

### Header

```
offset  size  field           notes
0       4     "CcnK"          container magic
4       4     byte_size       uint32 BE: bytes after this field
8       4     fxMagic         preset kind (see below)
12      4     version         uint32 BE: format version
16      4     plugin_id       FourCC the plugin registers (e.g. "XfsX" = Serum)
20      4     plugin_version  uint32 BE
24      4     num_programs    uint32 BE
28      28    preset_name     null-padded ASCII (single-preset variants only)
```

### fxMagic (preset kind)

| fxMagic | meaning |
|---|---|
| `FxCk` | regular preset, float parameters |
| `FPCh` | opaque-chunk preset (plugin serializes its own state) |
| `FxBk` | regular bank |
| `FBCh` | opaque-chunk bank |

For opaque-chunk variants the payload is length-prefixed: a `uint32 BE`
`chunk_size` at offset 56 (single) or 156 (bank, after 128 reserved bytes),
then that many opaque bytes.

---

## acidcat inspect

`acidcat inspect FILE.fxp` renders:

1. **CcnK** , the header, decoding `byte_size`, `fx_magic` (with its human
   meaning), `version`, `plugin_id` (annotated with the plugin name for known
   FourCCs like `XfsX` = Xfer Serum, `NiMs` = NI Massive), `plugin_version`,
   `num_programs`, and the 28-byte `preset_name`.
2. **chunk** (opaque-chunk variants only) , the length-prefixed plugin blob,
   reported by size.

The walker warns if the `CcnK` magic is missing or the header is under 28 bytes.

---

## Notes

- Big-endian everywhere. A little-endian reading of `byte_size`/`num_programs`
  yields garbage; this is the classic FXP parsing mistake.
- The plugin id is how you tell what synth wrote the preset without opening it;
  it is a FourCC the plugin chooses (not standardized across vendors).
- Serum 1 shipped presets as FXP; Serum 2 moved to the `XferJson` container
  (see serum.md). An `.fxp` with plugin id `XfsX` is therefore a Serum 1 preset.
- The payload is not portable across plugins and is not decoded here; two files
  with the same `fxMagic` can have completely different internal layouts.
