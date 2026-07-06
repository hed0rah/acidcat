# ReCycle RX2 Format Internals

Low-level reference for Propellerhead (Reason Studios) ReCycle `.rx2` loop files.

---

## Overview

An `.rx2` file is an IFF-style container: a top-level `CAT ` group whose form
type is `REX2`, holding big-endian chunks. ReCycle slices a drum or instrument
loop into transient-aligned segments; the `.rx2` stores the audio plus the slice
markers so a host can retrigger each slice and follow tempo changes.

**All multi-byte fields are big-endian** (IFF convention). The chunk internals
beyond the creator/name strings and the slice markers are proprietary, so
acidcat reports them as regions and decodes the parts that are legible.

---

## File Structure

```
CAT (size) REX2               top-level IFF group, form type REX2
  HEAD ...                    header / global info
  CREI ...                    creator string (e.g. "ReCycle ...")
  GLOB ...                    global: tempo, bars/beats, ...
  NAME ...                    loop name
  RECY ...                    ReCycle settings
  CAT (size) SLCL             nested group: the slice list
    SLCE ...                  one slice marker (repeated per slice)
    SLCE ...
  SD   ...                    sample data (audio)
```

### Chunk framing

```
offset  size  field
0       4     chunk id (FourCC, e.g. "CAT ", "SLCE")
4       4     chunk size (uint32 BE), bytes of body that follow
8       ...   body (padded to even length)
```

The top-level `CAT ` body begins with a 4-byte form id (`REX2`) before its
child chunks. A nested `CAT ` (form `SLCL`) holds the slice markers, so a flat
top-level walk misses them, the slice count requires descending into the
sub-group.

---

## acidcat inspect

`acidcat inspect FILE.rx2` renders:

1. **CAT ** , the container: `container` magic, group `size`, and `form`
   (`REX2`).
2. The child chunks in order (`HEAD`, `CREI`, `GLOB`, `NAME`, ...), with the
   creator and name strings decoded where present.
3. A derived **slice count**, computed by recursing into the nested `CAT `/`SLCL`
   group and counting `SLCE` markers.

The walker reads up to 4 MiB, bounds every chunk against the file length, and
warns on a chunk that runs past EOF (returning it as a truncated region rather
than crashing).

---

## Notes

- Big-endian IFF, like AIFF's `FORM`, but the group keyword is `CAT ` (with a
  trailing space) and the form type is `REX2`.
- The slice markers living in a nested `CAT `/`SLCL` sub-group is the one
  structural gotcha; the recursion depth is bounded (guard + max depth) so a
  malformed deeply-nested file cannot hang the walk.
- The `SD` sample data is the actual audio; it is reported by size, not decoded.
- `CREI` typically carries a "ReCycle" version string, useful provenance for a
  loop library.
