# RMID Format Internals

Low-level reference for RMID files: a Standard MIDI File wrapped in a RIFF
container.

---

## Overview

An `.rmid` file is a RIFF container whose form type is `RMID`, carrying a
complete Standard MIDI File (SMF) inside a `data` chunk. It exists so a MIDI
file can travel with RIFF-style metadata (`INFO`, `DISP`) and be handled by
tooling that speaks RIFF. Microsoft defined it; it is rare but real.

The wrapper is little-endian (RIFF); the wrapped SMF is big-endian (MIDI). The
two coexist in one file, which is the whole curiosity of the format.

---

## File Structure

```
+-------------------------------------------+
| RIFF (riff_size) RMID                     |  little-endian wrapper
+-------------------------------------------+
| [ optional DISP / INFO / etc. chunks ]    |
+-------------------------------------------+
| data (data_size)                          |
|   MThd ... MTrk ...  (a full SMF)          |  big-endian inner file
+-------------------------------------------+
```

### Framing

```
offset  size  field
0       4     "RIFF"
4       4     riff_size (uint32 LE), bytes after this field
8       4     "RMID"    (form type)
12      ...   RIFF chunks; the SMF lives in the "data" chunk
```

The `data` chunk's body is a byte-for-byte Standard MIDI File, beginning with
its own `MThd` header.

---

## acidcat inspect

`acidcat inspect FILE.rmid` renders:

1. **RIFF** , the wrapper: `magic`, `riff_size`, and `form` (`RMID`).
2. Any non-`data` wrapper chunks (e.g. `DISP`, `INFO`) as regions.
3. **data** , the wrapped-SMF chunk, with its declared size.
4. The inner SMF, handed to the MIDI walker (`MThd`, `MTrk`, ...) with every
   offset shifted into its wrapped position, so the MIDI detail shows through
   with correct file offsets.

If the `data` chunk is absent it warns (the wrapped MIDI is missing); if the
inner SMF fails to parse it degrades to a warning rather than crashing.

---

## Notes

- Two endiannesses in one file: LE RIFF framing around a BE MIDI file.
- acidcat delegates rather than re-implementing MIDI: the inner bytes are run
  through the same MIDI walker used for a bare `.mid`, then the returned chunk
  offsets are shifted by the `data` chunk's payload offset.
- The sniffer distinguishes RMID from WAV by the form type: `RIFF....WAVE` is a
  WAV, `RIFF....RMID` is this.
- Some RMID files also carry a soundfont (`DLS`) alongside the SMF; acidcat
  reports such extra chunks as regions but does not descend into a DLS.
