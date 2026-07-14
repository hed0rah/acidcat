```
+--------------------------------------------------------------+
|   W A N T E D                                                |
|   rare, vintage, and weird file-format specimens            |
|   dead or alive, compressed or raw                          |
|                                                             |
|   REWARD: rare and uncompressed PCM treasures               |
+--------------------------------------------------------------+
```

*A running bounty. We are always hunting specimens; the list below is what is
open right now.*

**acidcat** is a byte-level dissector for audio and synth/DAW formats. It gets
sharper with real specimens: reverse-engineering an undocumented format needs
ground-truth files, and the odd corners (vintage hardware saves, malformed
files, polyglots) are exactly where a dissection tool earns its keep.

If you have any of the below and can share a small, non-copyrighted example, we
would love it. A sine-wave sample in a rare program format is worth more to us
than any commercial library.

---

## MOST WANTED: hardware-written Akai MPC files

acidcat parses MPC files written by the MPC *software*. Files saved by the
*hardware* may use a different layout, and that gap is our current hunt. We want
on-device saves, copied raw off the floppy / CF / SCSI / Zip, unconverted:

| machine | wanted | bounty (why it matters) |
|---------|--------|-------------------------|
| MPC2000 / 2000XL | a `.PGM` + `.SND` | the top prize: settles hardware-vs-software layout |
| MPC3000 | a `.PGM` + `.SND` | same family as the 2000, or its own dialect? |
| MPC4000 | a program | confirm it is the AKAI/APRG format we already read |
| MPC5000 | a `.PGM` | the extended MPC1000 variant |
| MPC500 | a program + `.50s` | the `.50s` format is completely unmapped |
| MPC60 / 60mkII | a 12-bit sound | completes the lineage |

**The perfect drop:** the *same* simple kit saved on *several* machines.
Identical sample names across formats let us decode unknown bytes on sight.

## ALSO WANTED, ALWAYS

- Native Akai S-series programs (S900 / S1000 / S3000 / S5000 / S6000).
- Any audio or preset format acidcat does not yet recognize: obscure trackers,
  hardware sampler dumps, oddball synth presets, forgotten DAW formats.
- Malformed, truncated, or polyglot files: a WAV that is also a ZIP, a preset
  with trailing junk, anything that breaks a naive parser.

## WHAT MAKES A GOOD SPECIMEN

- **Small and simple.** A few pads, a short sample, plain names (`KICK`, `SNARE`).
- **Not copyrighted.** Synthesize a sine or noise burst -- that is perfect.
- **Raw and unmodified.** The actual bytes the tool or hardware wrote.

## HOW TO TURN ONE IN

Open an issue or a discussion on this repo with the file attached. Tell us what
wrote it (machine, OS/firmware version, how it was saved) if you know.

Every specimen makes the tool read one more corner of the world correctly.
