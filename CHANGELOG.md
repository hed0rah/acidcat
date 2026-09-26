# Changelog

All notable changes to acidcat. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project will
adopt [Semantic Versioning](https://semver.org/spec/v2.0.0.html) at 1.0.

## [Unreleased]

### Added

- **A description of every walk as one document (contract v1).**
  `core/infra/contract.py` turns a walk into the Document that
  `docs/contract/node-v1.md` specifies: a node tree with stable ids, fields
  located absolutely with a machine value beside the display string and the
  storage type they were read as, the play/decode/render capabilities, and one
  findings list. Nothing reads it yet; `inspect --json` is unchanged.

- **One `Limits` object** (`core/infra/limits.py`) records what a walk ran
  under on its Document, and `hit(name, limit, used, message)` announces a cap:
  the note names the limit, the bound and how much the file asked for, and the
  Document lists it in `limits.hit` with a `cap.*` finding.
  `contract.walk()` takes `limits=`.
- **Finding codes.** Every warning can carry a stable code consumers key on
  instead of its wording (`core/infra/findings.py`): `size.overrun`,
  `pointer.dangling`, `magic.mismatch`, `parse.failed`, `checksum.mismatch`,
  `sibling.missing` and others, 100 walker sites so far. The rest report as
  `legacy` and are counted. Forensic findings (`inspect --anomalies`,
  `audit --json`) gain a `code` key.
- **Layers.** A packed YM's unpacked tune is layer 1 of its Document: the
  LHA body is decoded through a registry of decoders (`core/infra/layers.py`,
  `-lh5-` and stored `-lh0-`) and checked against the member's CRC-16, and the
  tune is walked region by region inside it, so its frame count has a byte
  range (`1:lh5/header#frames`). A Pack-Ice SNDH is layered the same way:
  the image must fill exactly the length its header states, and its entry
  branches, tags and player are walked inside it; on 298 real packed SNDH
  files the layer is the verified image. A PSF's zlib program is layer 1
  too, checked by zlib's own Adler-32 and the length the walk measured; the
  GBA and DS program header is walked inside it (entry point, load offset,
  ROM byte count), and any other machine's program is one region. On 250
  real GSF and 2SF files the layer is the inflated program. An Ableton
  document (.als, .alc, .adg, .adv, .agr) is gzip over XML: the XML is layer
  1, checked by gzip's CRC-32 and length trailer, and the root element's
  attributes (Creator, versions) are placed on their value bytes.
- **`carve --layer N`** writes a layer's bytes, decoded and checked the same
  way; `--layer 0` is the file.
- **`docs/contract/cli-2.0.md`**: every 1.8 command and flag, what it becomes
  in 2.0, and which old spellings keep working through 2.x. A test fails when
  the parser grows a flag the page does not map.

### Changed

- **TUI: the bytes pane shows the file around the selection.** Selecting a
  field lights its bytes inside their neighbours, with the selected node's
  fields tinted, instead of showing the selection alone on an empty pane.
  PgDn/PgUp page through the file (they paged inside the selection, 1,024
  bytes at a time), and up/down on the focused pane move it a row. The tree
  takes 35% and the bytes 65%, so the hex is 16 bytes a row from 120 columns
  (it folded to 8 below 156). A status line under the panes names the layer,
  the selection's offset and length, the finding count and the actions the
  selected node offers. Keys are unchanged.
- **TUI: what a node can do comes from its caps.** `p` plays a tune on the
  engine its `render` cap names (`core/codecs/engines.py`), decodes a file
  with a `decode` cap whole, and reads PCM with the geometry of the `audio`
  cap; `e` on the file opens its tag editor when it has an `edit` cap, and on
  a field edits the value as before; `X` writes out a node with a `carve` cap
  when there are no regions. Nothing in `tui_app/` chooses by format name any
  more: the edit profiles moved to `core/write/profiles.py` and the PS1 disc
  catalog to `core/containers/psxdisc.py`. A walker can declare a cap on its
  chunk (`chunk["caps"]`), and the TUI acts on it with no change of its own.
- **TUI: layers.** `enter` on a node that opens a layer (a packed YM's LHA
  body) shows the decoded image as a view of its own, read-only, named in the
  breadcrumb (`tune.ym > unpacked YM5`) and the status line (`layer 1`); its
  fields have byte ranges there and light up in its bytes. `u` comes back to
  the file. In the file view, a field of the packed tune says which layer holds
  its bytes and how to open it, instead of "no byte range".
- **TUI: a field inspector and a data inspector.** The field inspector
  (replacing the detail box) gives the selected field's type and where it
  came from, offset, length, bytes, value, meaning, note and pointer target;
  `enter` on a pointer follows it, like `x`. The data inspector reads the
  bytes at the cursor as u8 to u64, i8 to i64 and f32/f64, little- and
  big-endian side by side, with their ASCII and bits.
- **TUI: a byte strip, generated help, and an open dialog that remembers.**
  A row under the panes draws the whole layer to scale as the nodes that
  hold its bytes: a node's header bytes as header, bytes no walker described
  as gaps, the selection lit, and a small chunk between big ones still keeps
  the cell it starts in. The help (`?`) is generated from the key bindings,
  grouped by area (move, bytes, play, edit, regions, file), so a rebound key
  cannot leave it wrong; it scrolls, and a long line hangs under itself. The
  open dialog (`o`) starts where a file was last opened, lists recent files
  above the tree, and hides dot files and folders; it remembers in
  `<acidcat home>/tui.json`. The data inspector shows only where it fits
  without wrapping, and the field inspector fits its bytes to the pane
  instead of running past its edge on a narrow terminal. Keys are unchanged.
- **Every walker warning has a code.** The remaining plain-string warnings
  in the walkers and format decoders (about 440 sites) carry a finding code,
  so no warning a walk produces reaches a Document or `audit` as `legacy`.
  Sixteen codes were added where none of the existing ones fitted (a count
  that disagrees with its payload, two fields that disagree, a value the spec
  does not allow, an unterminated or malformed text field, an address outside
  the machine's window, a required chunk missing, chunks out of order, an
  unknown id, a reference to nothing, stray bytes, a sibling that changed, a
  value a reader has to assume, a layout no specimen showed, a convention
  that looks like damage, a part walked past undecoded, and a file no walker
  reads). Message wording is unchanged. Some findings change kind with their
  code: a caught exception is now `error`/`walker.error`, and conventions,
  assumed values and undecoded parts are `info`, not defects.
- **TUI: the tree keeps its height.** The data inspector under it shows the
  four readings most fields are (u16, u32, i32, f32) and `i` shows all ten, so
  the tree has about 13 rows at 120x36 instead of 7. Both byte orders of a
  reading are written the same way: in decimal, or both in hex when either is
  too wide. The info box lays its facts out as whole phrases ("2 chunks", "[u
  back (1)]" never split across lines), and a finding's message wraps under
  its number. Inside a decoded layer the file is named as in the breadcrumb
  (`tune.ym > unpacked YM5`), never by the temp file that holds the layer.
- **Cap hits are never defects.** Twenty-two places reported crossing one of
  acidcat's own limits as a plain warning, which `audit` counted against the
  file: the 8SVX, SMUS, VOC, DMX and BFD read and chunk caps, eight E-mu
  listings, the MIDI read cap and event listing, the MP3 frame listing, the
  SigMF annotation, tracker sample and MPC pad listings, and the generic
  triage's read window and chunk listing. They are coverage notes now, with the
  same text. A coverage note cannot be made without naming its limit, so a new
  cap cannot be misfiled.
- **A missing sibling is not a defect.** A PSF whose library, a cue sheet
  whose BIN, or a SigMF recording whose sidecar is not beside it is now an
  `environment` finding: still printed, but `audit` exits 0 for it. Read from
  memory, PSF and cue say the sibling was not looked for.
- **`inspect --sandbox` keeps each warning's kind.** The sandbox returned its
  walk as JSON and every coverage note came back a defect.
- The forced parse (`inspect --force`, the TUI's force view) picks each
  walker's complaint by code, not by the words "magic" or "spec says".
- **Walkers read a Source, not a path.** A walk maps the file once, and bytes
  in memory walk exactly as a file does: `walk_bytes` no longer writes a temp
  file (it cost 1.9x the walk at 300 bytes and 400x at 64 MB), and an RMID's
  wrapped SMF is walked in memory rather than through one. The helpers the
  walkers share take a path or a Source, and open a plain path as before.
  Checked: every seed and `data/` fixture walks to identical output from a
  path before and after, deep and shallow; from bytes the output is the same
  except where a file beside it would be checked (a cue sheet's BIN, a PSF's
  library), which cannot happen without a directory.

### Removed

- `acidcat.core.forensics.forced._MAGIC_COMPLAINT`, the words the forced parse
  matched; it matches finding codes.
- `acidcat.core.primitives.notes.coverage(text)`. Use
  `acidcat.core.infra.limits.hit(name, limit, used, text)`; `Note(text,
  "coverage")` without a `cap` now raises `ValueError`.

### Fixed

- **Every 12-bit AIFF and WAV was reported as damaged.** Both formats store
  a sample in whole bytes, so 12-bit takes two, but the size checks divided
  the bit depth by eight and rounded down. AIFF warned that SSND held twice
  the audio its frame count implied, and WAV that a correct `block_align` was
  wrong. Found on two 1991 Prosonus AIFFs in the specimen library.
- **A cut MDX was not recognised as MDX.** Sixteen real modules are
  truncated rips whose offset tables point past the end of the file. The
  header (title terminator, bank name, a 9- or 16-channel table) identifies
  them, so they now sniff as MDX and the walk reports each offset that
  dangles. No other file in the corpus has such a header.
- **76 X68000 sample banks were not recognised.** Their writer emitted only
  the slots it filled, a table shorter than one 96-slot bank, so reading a
  whole bank read sample data as slots. A short table is now accepted when
  it accounts for the file exactly: every slot inside it, the samples laid
  end to end to the last byte. None of 333,922 other files in the corpus
  passes that test.
- **Two walkers found wrong by public test files.** McGill's AU and AIFF
  sample sets, now in the corpus, caught a Sun/NeXT file whose data chunk
  claimed 172,032 bytes of an 86,044-byte file (the chunk now owns what is
  there, with a `size.overrun` finding) and an AIFF-C `APPL` chunk whose
  first data byte was read as a Pascal-string length of 71 in a 12-byte
  chunk (the name is decoded only when it fits).

## [1.8.6] - 2026-09-25

### Fixed

- **Seven chunks whose fields pointed at the wrong bytes.** Every field is
  now read back from the bytes it claims, on every seed and on the hunt
  corpus, and seven walkers failed: their fields were inside the chunk, so
  every bounds check passed, but at the wrong place. KRZ objects (4 bytes
  off, and their sample, keymap and program bodies by a further header),
  the VGM GD3 tag (12), DSDIFF's FRM8 (12), the RMID data chunk (8) and the
  PSF tag block (5) measured from the chunk start while declaring a payload
  base past their header; GF1 patches declared no base, so the default
  (offset + 8) moved every field 8 bytes; Ogg placed its derived codec name
  on the page's capture pattern. DSDIFF's PROP sample rate spanned its whole
  local chunk, so its `>I` annotation could never be verified or edited.
  What the TUI highlighted, `od` annotated and the editor would have
  written for all of these was the neighbouring bytes.
- **Four more, found only on real files.** Running the same read-back over
  the real-file corpora caught cases the seeds never reach. The Ableton
  sidecar's overview values sat on the sentinel they are found by, not the
  words before it, and its warp markers were all placed at the chunk's
  first byte (the marker count is now marked as derived, since it counts
  decoded records). A Kurzweil object's name field was shorter than its
  stored name when the name had leading spaces, and its block size is now
  declared as the signed word it is. An SPC xid6 integer sub-chunk spanned
  its header as well as its value. An empty 8SVX text chunk now has no
  position rather than a zero-length one at offset 0.
- **An anomaly finding in a text field gave a relative offset.** The
  control-bytes rule reported the field's offset within its chunk as if it
  were a file offset; it now points at the tag.
- **Two test modules failed instead of skipping without the `analysis`
  extra** (`test_concealment`, and two `test_detect_fallback` cases that
  test librosa's decode-failure path).

### Changed

- **One way to say where a field is.** A field in its chunk's own header
  (an id, a size word) has a negative offset from the payload base instead
  of being unpositioned with an `xref`; a field that describes a chunk but
  is stored elsewhere (a MOD or XM sample's header, far from its PCM) is
  positioned and marked `remote`; `off=None` is the only spelling of "no
  position" (69 derived values were written at offset 0, length 0); and
  `xref` only ever means "points to". `inspect` prints a header field's
  offset as `-0x0008`. New fleet tests pin all four rules and a ledger of
  the nine fields whose value is a transform of their bytes (a count stored
  minus one, 16.16 fixed point, ASCII digits), which may only shrink.

## [1.8.5] - 2026-09-24

### Added

- **SPC playback: the SPC700 and the S-DSP, run.** An .spc holds no audio,
  only the sound unit frozen mid-tune, so `p` in the TUI now resumes the
  CPU where the dump stopped and turns what its driver writes into sound:
  all 256 opcodes with their cycle counts, the three timers, the DSP's
  BRR decode, Gaussian interpolation, ADSR and GAIN envelopes, noise,
  pitch modulation and echo, at 32 kHz stereo. Checked sample by sample
  against blargg's Snes_Spc on 200 random Modland snapshots: median
  correlation 1.0000, 192 above 0.99, none failing to run. The phases a
  dump does not carry (the timer base clock, the envelope rate counter)
  follow the established players, since a driver that seeds a random
  number from a timer read plays a different tune otherwise. The dumped
  echo ring is played as found rather than cleared. A 30-second preview
  renders in about 20 seconds.
- **YM: the Atari ST's register dumps, and the LHA they ship in.** A YM is
  what a player routine wrote to the YM2149 each frame, stored column by
  column and packed in an LHA archive. acidcat now reads LHA level-0
  members and decodes -lh5- itself (LZSS with per-block Huffman tables,
  no new dependency), and refuses any body whose CRC-16 does not match.
  YM2, YM3, YM3b, YM5 and YM6 are decoded: frames, clock and rate, loop,
  digidrums, the three strings, which voices ever sound; a bare YM is laid
  out region by region, a packed one walks the LHA header field by field
  and describes the tune inside. Modland's 4,958: all but five walk and
  tile, every body matching its CRC; the five are ST-Sound's MIX1 and
  YMT1/YMT2 sample types, which are not register dumps and are refused by
  name. YM4 has no specimen and is refused rather than guessed at.
  Anatomy page.
- **SNDH: Atari ST music as its own 68000 player, and Pack-Ice.** The
  three entry branches (with their targets), the tag header from SNDH to
  HDNS, then the player. Pack-Ice 2.3/2.4 ('ICE!') is decoded here, read
  backwards from the end of the file, and the result must come out at the
  stated length with SNDH where it belongs; byte for byte the same as an
  independent depacker's on all 5,430 packed files of Modland's 5,484, all
  of which walk and tile. The tags are read the
  way files write them, which is not always the spec's way: the default
  subtune is '!#' not '#!', subtune names are offsets from the tag's
  start (after a pad byte when the tag sits on an odd address), and a
  header from before v2 has no HDNS and simply stops where the code
  begins. Earlier 'Ice!' streams are refused. Anatomy page.

### Fixed

- **Six anatomy pages drew some fields uncoloured and unnamed.** The
  CMF, HES, KSS, PT3, S98 and VGM maps used field kinds (ptr, size, pad)
  the page engine does not know; they are now structural or reserved,
  and a fleet test refuses a kind the engine cannot draw.

## [1.8.4] - 2026-09-18

### Added

- **CMF: the Creative Music File, a MIDI track with its OPL2 patches in
  front.** A little-endian header of offsets, the sixteen-byte FM patches
  (two operators, interleaved, then feedback and connection), and an SMF
  track the MIDI walker's own scanner reads: notes, channels, tempo, End
  of Track. Each patch is a chunk, so it can be carved. Three findings
  from Modland's 459: 372 end with one stray 0xFF after End of Track,
  which the writer left and the walker names; the three text strings sit
  wherever their offsets say, and one file points its title and composer
  into the event stream, which is reported rather than read as text; and
  a 1.0 header has no instrument count, so the count is what fits between
  the offsets. All 459 tile. Anatomy page.

## [1.8.3] - 2026-09-17

### Added

- **S98: the PC-98 register log, and the X1's and the OPM's.** The VGM idea
  from the Japanese PC scene: a header of timer and offsets, a v3 device
  table naming the chips and their clocks, a dump of writes and syncs
  walked to its end marker, and a `[S98]` tag of key=value lines in
  Shift-JIS. v1 keeps a plain title before the dump and runs the dump to
  the end of the file; v3 puts the tag after; the tag offset says which.
  The sync count after 0xFE is a 7-bit varint in every version: a reading
  of "one byte in v1" walks 200 real files into the middle of a command.
  5,109 of Modland's 5,112, all tiling; the twenty with no end marker are
  said. Anatomy page from a Sharp X1 tune.

### Changed

- **The suite runs in three minutes instead of thirty.** `pytest-xdist` is
  in a new `dev` extra and the rule is `pytest -n 8`; every TUI pilot is
  pinned to one worker and the memory profiles to another (twenty-four
  pilots at once timed out, and some hung), one test parametrised over a
  set is now sorted so workers agree on what they collected, and one
  fixture that wrote a fixed temp-file name now takes its own. A `slow`
  marker names the thirty tests over ten seconds; `scripts/tests_for.py`
  maps a diff to the tests that can see it and says why; and
  `scripts/corpus_env.sh` exports every corpus path from one root. The
  rule, in ARCHITECTURE.md: scoped in the edit loop, quick before a
  commit, full on the release commit and in CI.

### Fixed

- **Two geometry slips the fleet sweep found on the new pulls.** SPC's
  xid6 sub-chunk fields carried offsets relative to the chunk instead of
  its payload (the recurring bug class), and a sub-chunk that declared
  more than the block held spanned the declared length; an Oktalyzer
  chunk that declared more than the file held kept its declared size and
  reached past the file in silence. Each now owns what is there and says
  what was declared. Found the first time `scripts/corpus_env.sh` pointed
  the whole-corpus geometry sweep at everything on the hunt drive.

- **A forensics finding with no offset crashed the TUI.** The anomaly
  runner reports a check that could not run as a finding about the whole
  file, offset None. The findings panel formatted it as a hex address and
  died; the jump key would have too. Found by the parallel run, which
  pushed a check into a MemoryError and produced exactly that finding.
  The panel shows a dash and the jump says what it cannot jump to.

## [1.8.2] - 2026-09-16

### Fixed

- **Seventeen anatomy pages had lost their shell.** Every page built by a
  generator script since SID was missing the acidcat toggle (the mascot
  and the theme cycler), because the first script dropped it from the
  title block and each later script copied that block; four of them
  (HES, KSS, PT3, VGM) also had their region blocks inside the flex intro,
  which rendered them as columns with the byte maps on end. The builders
  are fixed, the pages regenerated, and a fleet test now refuses a page
  without the toggle, without the colour key, or with a region in the
  intro.

### Added

- **STC: the ZX Spectrum Sound Tracker module, the ancestor of PT2 and
  PT3.** No magic. Twenty-seven bytes of pointers, then samples (99 bytes
  each), a positions block, ornaments (33 each), a 0xFF-ended pattern
  table of three stream pointers per pattern, and the streams. The blocks
  come in a fixed order with fixed record sizes, so identification is the
  arithmetic: whole records up to the positions block, a positions block
  that ends exactly at the ornaments, a table that ends at 0xFF, every
  stream pointer past it. Records carry their own numbers, so gaps in the
  numbering are legal. Two block orders exist: most compilers put the
  positions before the ornaments, 24 of Modland's 3,636 the other way
  round, and each block ends where the next begins in both. A line of
  author text sometimes sits before the pattern table, reported as a
  comment. 3,627 of 3,636 identified, all tiling; the nine refused have
  a pointer outside the file. On the PT3 anatomy page as its own section.

## [1.8.1] - 2026-09-16

### Fixed

- **GBS: a file bigger than the address space is banked, not impossible;
  a timer modulo of 0 is the slowest period, not an error.** Both warnings
  came from reading the header as if the Game Boy had 64 KB and nothing
  else. The spec puts everything past $7FFF into 16 KB ROM banks switched
  in at $4000 through the cartridge's $2000 register, and the timer
  reloads to TMA, so its period is 256 - TMA. On Modland's 916 GBS files
  (1.8.0 was verified on 36) the two warnings fired 124 and 181 times,
  every one on a legal file. The walker now reports the bank count and
  the play rate the two bytes work out to.

### Added

- **HES and KSS: the PC Engine and the MSX, NSF-shaped.** A HES is
  sixteen bytes naming the call address and presetting the HuC6280's
  eight page registers, then a DATA block: tag, size, address, the bytes.
  The address field holds 0x20 in every one of Modland's 421 files, which
  is where the block's own bytes begin, so it is reported as the field and
  not read as a ROM address; seven blocks declare a whole cartridge and
  hold a page. A KSS is sixteen bytes of addresses, a bank byte (bit 7 set
  means 8 KB banks, and read the other way a third of real files stop
  adding up) and chip flags, then the init data and the banks; a file may
  end inside its last bank and the player zero-fills, which 170 of 392 do,
  so it is a fact on the bank and not a warning. KSSX adds a sixteen-byte
  extension that is zero in nearly every file and is shown, not named.
  Both tile on every Modland file. Anatomy pages.

- **PT3: the ZX Spectrum's AY module, ProTracker 3 and Vortex Tracker.**
  A score for a chip with three square waves: samples are tables of
  per-tick settings and ornaments tables of semitone offsets, there is no
  PCM anywhere, and everything is reached by absolute 16-bit pointers
  because the file was played in place at a known address. That makes
  the walk the sorted set of pointers: each region begins where one says
  and ends where the next begins, named by everything that points at it
  (two patterns can share a stream; the empty sample is shared by all).
  Samples and ornaments declare their own size and the walk checks it
  fits. The position list stores pattern numbers times three, because
  the Z80 used them as row offsets into the six-byte pattern table. On
  Modland's 7,376: every file tiles, header to end, no gaps. Anatomy
  page from one 2,048-byte module. **Pro Tracker 2** is read by the same
  walker: the header the other way round, 3-byte sample rows, and no
  signature, so it is identified by arithmetic under its extension (the
  counts agree, every pointer is inside the file, the first region begins
  where the header ends); 2,700 of 2,706 real files, the six refused cut
  short.

- **VGM: the sound-chip register log, and .vgz around it.** Neither a
  score nor a recording: every byte a game wrote to its sound chips, in
  order, with the waits between, so a player with the same chips plays
  it back exactly. The header is a table of chip clocks where a non-zero
  clock is the only presence flag, and the version byte says how much of
  the table exists (1.00 has two chips, 1.71 has forty); every offset in
  it is relative to its own field. The command stream is a byte code the
  walker decodes to its end marker, counting waits and writes per chip
  and lifting out each data block (the PCM a chip's memory was loaded
  with) as a chunk of its own; the spec fixes the length of every
  reserved opcode, so the walk always reaches the end, and on real files
  it lands exactly on the GD3 tag, whose eleven UTF-16 strings end
  exactly at EOF. Header sample counts and stream waits agree on all but
  two of the first 1,583 Modland files; the two say so. Anatomy page from
  a Master System tune. A .vgz is identified by the magic inside the gzip
  and walked as one chunk with the same facts, since its offsets belong
  to the inflated image and not the file.

- **The original Soundtracker module: 15 instruments, no magic.** The
  ancestor of MOD, from 1987, and the one file the tracker walker could
  not open because there is nothing at offset 1080 to read. Identification
  is arithmetic, the way MDX and PDX are found: 600 bytes of header, 1,024
  per pattern (counted over all 128 order slots, since a tenth of real
  files keep patterns past the song's end), then the samples, and the
  total lands on the file size. Zero false positives over 214,483 files
  that are not one; 1,914 of Modland's 1,935 accepted, the rest truncated
  or ProTracker files filed there. Two things the layout does differently
  and the corpus confirmed: the byte after the song length is Ultimate
  Soundtracker's tempo (120, in every file that has one), and a sample's
  repeat point is in bytes, not words (858 loops fit only that way). Sniffs
  as `mod`; `inspect` says which layout; `extract` pulls the ST-01 disk
  samples out by name.

- **PSF: Nintendo DS (2SF, version 0x24) and Nitro Composer (NCSF, 0x25).**
  Found by a census of the Modland index against what acidcat sniffs: the
  second-largest directory the reader did not open held 31,118 files, and
  the reason was one missing row in the version table. Then, on 4,210 of
  them: the DS program header is the GBA one without its entry point
  (offset, length, ROM; consistent on every file that inflates), and 2SF
  is the one machine that uses the reserved area, as a SAVE block (zlib
  size, CRC32, zlib) that inflates to another (offset, length, data): a
  patch into the emulator's save state, four bytes for a mini. Two real
  minis carry a patch, a tag and no program at all, which the walker used
  to call a program that does not inflate. Inflation is now streamed and
  counted rather than held, so a 64 MB DS library reads in a megabyte of
  memory and the cap can be the largest cartridge instead of a guess.

- **`extract` decodes PDX banks.** The X68000's samples are OKI MSM6258
  ADPCM, and 1.7.0 located them without decoding them because there was
  nothing independent to check a decoder against. There is now: ffmpeg's
  `adpcm_ima_oki` reads the same nibbles as WAVE format 0x0010, and with
  ffmpeg's rounding our decoder is bit-exact against it on 200 real
  samples, which pins the nibble order (low first), the 12-bit clamp, the
  index table and the scale. The one thing the oracle could not settle it
  does differently: the step delta. ffmpeg computes `((2d+1)*step) >> 3`;
  the datasheet sums `step/8 + step/4 + step/2 + step` with each term
  truncated. The predictor integrates, so they drift 9% RMS apart on real
  audio, and the corpus picks: with the datasheet's form a recorded drum
  hit ends within a hundred units of silence, with the other it ends up to
  1,900 off. The encoders of 1990 modelled the chip. Samples come out at
  the chip's 15.6 kHz default with a note that the song may set another;
  aliased slots are reported once.

## [1.8.0] - 2026-09-15

Three new formats, one of them eight platforms wide, all verified on Modland
corpora pulled the same night. And a correction to the method: a walker
written from a specification is verified only against the specification.
The SPC one below was wrong three times before a single real file was read,
and right after four hundred.

### Added

- **SPC: the Super Nintendo's sound chip, frozen.** A 256-byte tag, 64 KB of
  SPC700 RAM, the DSP's 128 registers. A player loads the image and lets the
  program run, so there is no score to parse -- and what a reader CAN do is
  find the samples. DSP register DIR names the page of RAM holding the sample
  directory, and the eight voices' SRCN registers say which entries are real:
  the directory names 256 and a sound engine leaves most of them stale,
  pointing into code and into each other (219 of 332 files). The walker
  carves only what the voices are set to play, and each is a BRR sample the
  codec already in the tree decodes. Three places the published spec is
  wrong, each found by counting on the corpus: the tag flag at byte 0x23
  (real files carry 0x1A, a DOS EOF marker, and a full tag regardless); the
  date as the tell between the two tag spellings (real dumpers leave it
  empty; the seconds slot tells); and the emulator byte, which is a text
  DIGIT in the text spelling; and the magic itself, which the spec gives as
  ending v0.30 and 1,497 real files end v0.10, v0.20 or a bare 0.10 instead. The
  binary spelling's layout -- artist one byte
  earlier at 0xB0 -- was verified on 2,654 real files, which the spec's own
  table, with its known typo, could not do. 36,871 of 36,872 files identified (the one is a
  damaged magic), zero crashes, zero untrustworthy geometry. Anatomy page.

- **PSF: Portable Sound Format, eight consoles in one container.** Neill
  Corlett's 2002 container for PlayStation dumps, adopted by everyone who
  needed to ship a program and its RAM for another chip: the version byte
  names the machine (PSF1, PSF2, Saturn, Dreamcast, N64, GBA, SNES, QSound)
  and nothing else differs. Sixteen bytes of header, a zlib program with a
  CRC32 of its own, an optional [TAG] block. The checksum is the gift: every
  one of 2,757 real files matches, and every program inflates. Minis and
  libraries: a soundtrack shares one engine and sample set, so each track is
  a few bytes patched over a library the `_lib` tag names -- on 669 real GBA
  minis, one or two bytes, the song number. Only the GBA program header is
  decoded, because only it was documented and verified (509 of 509 files,
  zero mismatches); every other machine's program is reported as what the
  container proves and no more. The specification has fallen off the
  internet; the layout came from a reader that plays the files. Anatomy
  page, showing a whole 221-byte file.

- **GBS: the Game Boy Sound System.** A 112-byte header and a code blob --
  three addresses, a stack pointer, two timer bytes, three 32-byte text
  slots -- the same shape as NSF with nothing reserved, so it lives beside
  NSF and SAP in the chiptune walker. The load, init and play addresses are
  checked against the cartridge window the spec names. 36 real files.
  Anatomy page.

- **`census` keeps five example paths per chunk id**, not one. One path
  finds a specimen; measuring an undocumented chunk needs several, and with
  one recorded every such investigation was a fresh corpus walk.

### Fixed

- **S3M: an empty instrument slot is not a missing tag.** Type 0 is the
  spec's own word for EMPTY, and an empty slot carries no SCRS tag because
  there is nothing to tag. The walker warned on every one, and the first 73
  files of Modland's archive produced 162 such warnings, all the same legal
  structure presented as damage. And an odd order count, which Scream
  Tracker never writes, now names the writer that does: every one in 540
  files came from Impulse Tracker exporting S3M.

- **One S3M in 11,131 sniffed as a SNES cartridge.** The ROM test is a
  checksum and its complement at 0x7FC0, a 1-in-65,536 coincidence, and it
  ran before the S3M test, which is a four-byte magic at a fixed offset.
  One real file won the coincidence. The magic goes first.

### Measured

Modland, pulled with `mirror_modland.sh` (index-driven, four workers; a
crawl fetched 4,000 directory listings before its first file): Nintendo SPC
36,872 files (one with a damaged magic), Gameboy Sound Format 23,775 (which are GSF, not GBS -- the
directory name is not the format, the first bytes are), Screamtracker 3 11,132.
All three walkers hold on every file that has landed.

## [1.7.1] - 2026-09-14

One new format, and what doubling a corpus finds. Every walker here was run
over a real archive twice the size of the one it was verified on, and the
two bugs that turned up were both a limit derived from one sample presented
as a property of the format.

### Added

- **PMD: the compiled PC-98 Professional Music Driver score (`.M`).** The
  driver M. Kajihara wrote for the NEC PC-9801's YM2608, and the format most
  of the PC-98's game and doujin music was composed in -- Falcom, ZUN, Ryu
  Umemoto. A composer writes MML and MC.EXE compiles it to this; the score's
  own `#Title`, `#Composer`, `#Arranger` and sample-bank directives survive as
  a memo table the driver's author documents slot by slot. The layout was read
  from the driver source (pmdmini, an oracle and not a source) and three
  things it caught would each have produced plausible numbers rather than an
  error: every offset counts from byte 1; the part table is twelve words, not
  eleven, which left a two-byte hole in front of every first part until fixed;
  and the memo slot order depends on a tag byte through arithmetic that had to
  be reproduced rather than read, because getting it wrong puts the composer
  in the arranger field. Instruments are 26-byte records the driver walks by
  number, operators stored in the chip's own 1, 3, 2, 4 order. Verified on
  1,515 files from Modland's archive and the MXDRV Complete set: all
  identified, zero crashes, every one tiled to the byte. An anatomy page,
  with every byte from one real tune.

- **Five undocumented RIFF chunks measured on thirty specimens each, and
  four hold nothing.** `CDif`, `SAUR`, `chrp` and `Fake` are byte-identical
  or all-zero in every file measured, and are reported as exactly that:
  "constant in every specimen" is a complete description, and each says so if
  a specimen ever differs. `tlst` is a trigger list and is decoded -- a count,
  then fixed 24-byte records, every one naming a `cue ` point. The obvious
  reading of its second word as the cue id was wrong (it reads 0 where the
  file's own cue chunk says 1, in all thirty files) and the cross-check caught
  it; the field is called `selector` and claims no more. Named-only chunk
  ids: 8 to 3.

- **A DSD corpus test whose tiling check can fail.** Five SACD rips, 49
  files, including the first 5.1 specimen, which carries channel type 7 and
  exercises the layout table acidcat reads from Sony's spec on a real file
  rather than only against MediaInfoLib's transposed copy. The first draft of
  the check could not fail -- a container spanning the file tiled it
  trivially -- so three sabotages sit beside it.

### Fixed

- **PDX banks stack past eight, and the cap was pretending otherwise.**
  `MAX_BANKS` was set from the first corpus's largest table. A second corpus
  twice the size had 9, 10, 12, 13 and 17-bank files, and rejected all 22
  with a message blaming the file. Raised to 32; the format declares no
  ceiling. Also: seventeen banks put their first sample at 1,024 rather than
  768 with the 256 bytes between all zero -- a writer rounding to a power of
  two -- and are accepted as padding, only when the padding is zero.

- **An MDX with no voices is not damaged.** 416 of 54,178 modules have no
  voice block at all: they play only their ADPCM channel. The walker warned
  "0 bytes, too short for a 27-byte voice", which reads as damage and is a
  fact about the tune. Empty is silent; short-but-not-empty still warns.

- **Eighty modules from one publisher are a different driver's files, and
  say so.** They have no title, so the sniff said "no title terminator",
  which reads as damage. All eighty open with a 64-byte table of sixteen
  32-bit slots with exactly nine filled -- the X68000's channel count -- and
  zero of 54,738 real MDX match the shape. Which driver is not known and is
  not guessed at; the shape is named so the answer is "a different driver's
  file" rather than "damaged".

### Measured

MXDRV Complete, 63,547 files: MDX 54,629 of 54,738 identified, PDX 8,547 of
8,769, zero crashes and zero untrustworthy geometry across both. Modland's
PMD archive, 1,509 files, plus six from the X68000 set: 1,515 of 1,515.

## [1.7.0] - 2026-09-13

Five formats that were on the drives and unreadable, a metadata layer that
reads through the same table it writes through, and four defects that only a
real corpus could have shown. The method did not change: walk a real library,
count what comes back unparsed, and count SEPARATELY what the walker names and
cannot open. Every number below was measured, and where a reading disagrees
with a published account the reason is in the code next to it.

### Added

- **DSD: Sony DSF and Philips DSDIFF.** The two containers behind SACD, written
  from their published specifications and then checked against real encoders,
  which is where the surprises were. `COMT` and `DIIN` were carrying the
  metadata all along. The `DST` sound chunk is walked into rather than reported
  as a size. Two readings deliberately follow Sony's spec over MediaInfoLib,
  whose channel-type table has 4 and 5 transposed -- both are four channels, so
  a file plays either way and the centre channel comes out of a back speaker.

- **The Sharp X68000 sample bank (`.pdx`), 3,418 files that nothing opened.**
  An MDX names a bank and carries no samples, so a tune without its bank is
  half a file, and acidcat read one half of the pair and nothing of the other.
  The format is a pointer table and nothing else: 96 rows of big-endian offset
  and length, 768 bytes, then the data. Banks STACK -- more than 96 samples
  repeats the table, and nothing declares how many, so the count comes out the
  way MDX's channel count does: the first sample must begin exactly where the
  table ends. The row index IS the sample number, so empty rows are kept; and
  rows alias only exactly (947 duplicate pairs measured, never a window into
  another sample), so samples are emitted per distinct region. 3,255 of 3,418
  identified, zero false positives across 132,305 other files. The samples are
  OKI MSM6258V ADPCM and are located, not decoded: there is no independent
  decoder here to check one against, and `extract` should not offer audio
  nobody has verified.

- **The Akai S1000/S3000 program (`.s3p`), deferred twice as "no anchors".** It
  has an eight-byte magic. What made it look anchorless is that it is not a
  file format: the S1000 had no program file, only a MIDI System Exclusive
  dump, so a program was captured message by message with a length in front of
  each. Two consequences a file reader does not expect. The payload is
  NIBBLE-SPLIT, because SysEx cannot carry a byte with bit 7 set -- skip that
  step and you get small numbers that look like plausible parameters and names
  that decode to strings of the digit 0, a silent failure. And three fields
  look like pointers and are not: `KGRP1@` reads 150 in ordinary files, which
  is exactly the block size. What settles the layout is that the program
  block's own `GROUPS` count equals the number of keygroup messages around it,
  in 1,670 of 1,670 files. 51,212 velocity zones decoded, and a name on every
  program where there was none.

- **Scream Tracker 2 (`.stm`)**, the ancestor of S3M and the one tracker module
  on these drives acidcat could not open.

- **One metadata field vocabulary, pinned to the writers that use it.** 23
  canonical fields with a table saying, per format, where each one lands and
  whether it can be read, written, or both. The reader now goes through the
  same bindings as the writer, which is how `track` was found to be
  unreadable from every tagged format: the binding held the writer's spelling
  where the reader emits another. `acidcat formats --fields FMT` prints the
  map, including fields that share a destination and fields you can set and
  not read back.

- **`plst`, the playlist chunk the RIFF spec defines and almost nothing
  writes**, with a check that its segments name cue points that exist. And
  `PEAK`, which the anatomy page already explained and the walker did not read.

### Fixed

- **Every iPhone video was reported as malformed.** ISO-BMFF says `meta` is a
  FullBox, with four bytes of version and flags before its children.
  QuickTime says it is a plain box. Apple writes the QuickTime form in `.MOV`,
  so acidcat was landing four bytes into the first child, whose size then read
  as zero -- "overruns its parent", and the whole metadata tree behind it
  discarded. The two are told apart by looking: the four bytes after the header
  are a box TYPE in one form and a box SIZE in the other, and a type is
  printable. Overrun reports across 393 real files: 8 to 0. `mp4-anatomy.html`
  had described this fork all along, down to "a parser must sniff rather than
  assume"; the knowledge was written down and the walker never implemented it.

- **A malformed MP4 box claimed 1.2 GB inside a 27 MB file.** Its DECLARED size
  was passed through as the chunk's extent. It now claims only the bytes that
  remain and reports the declared value as what it is: evidence, not an extent.

- **Ten MP4 boxes were named and left closed.** `mvhd`, `tkhd`, `mdhd`,
  `hdlr`, `elst`, `stts`, `stsz`, `smhd` and `dref` carried no fields, so the
  tree said `trak` three times and nothing about which one was the audio. All
  decoded, at both box versions -- version 1 widens the times to 64 bits, and
  reading it with the version-0 offsets lands every field in the wrong place
  while still producing numbers.

- **28,226 XMP packets read as nothing, correctly.** XMP is RDF/XML and these
  packets are genuinely malformed: a sample-library tagger writes attribute
  names like `dc:description:2`, and a QName may hold at most one colon. Of
  400 packets sampled, 333 failed and every single failure was those two
  names. There is now exactly one repair -- rewrite `a:b:c=` to `a:b_c=`,
  re-parse, and warn -- after which 400 of 400 parse. The extra colon becomes
  an underscore rather than being dropped, so a recovered `dc:title_2` cannot
  silently overwrite a real `dc:title`, and the warning says the values are
  recovered rather than as written. Anything else malformed stays reported and
  unread.

- **440 MDX modules were packed, and 18 more put their voices first.** Both
  read as "unrecognized file". The packers of the era compressed a module from
  the offset table onward and left the title and sample-bank name in the clear,
  so the header parses and the table is compressor output -- 12,312 channels,
  in the file that turned this up. Separately, the channel count comes from
  where the offset table ends, and that was taken to be the first MML offset,
  which assumes the channel streams are written before the voice block. It is
  a convention, not a rule. Coverage went from 26,689 of 27,166 to 27,147, and
  the anatomy page, which asserted the wrong rule as fact, was corrected.

- **A MIDI format 2 file is patterns in sequence, not tracks in parallel**, and
  was being summarised as the latter.

- **Lowercase `junk` and `filr` are padding too**, which 28,520 files said and
  the walker did not. And a damaged chunk id is no longer rendered as a
  shorter real-looking one.

### Changed

- **Seven chunk ids gain an attribution rather than a guess.** A chunk that
  only ever appears beside chunks acidcat already identifies was written by the
  same tool, which names the WRITER without claiming a layout. From a
  867,703-file census: `SNDM` and `ovwf` (Soundminer), `DIGI` (Pro Tools),
  `str2`, `bmrk` and `dtbt` (ACID/Sound Forge), `coll` (Apple Loops). Measured
  by walking each chunk id's own example file and asking whether any fields
  came back, the named-only list went from 16 ids to 8, and the chunks behind
  them from about 110,000 to about 35,000.

- **`census` keeps an example path for every chunk id**, not just the flagged
  ones, and reads the other half of the IFF family. Without that there was no
  way to find a specimen for an unknown chunk, which is what the whole exercise
  above depends on.

- **`acidcat formats` gains an Edit column.** The tool could edit metadata in
  thirteen formats and the capability matrix, whose entire job is answering
  "what can acidcat do with format X", did not mention editing at all.

## [1.6.0] - 2026-09-12

A base-coverage pass over the formats everything else is built on. One method,
six formats: walk a real library, count what the walker calls unparsed, and
then count separately what it NAMES and cannot decode. The second list is where
the work was -- a walker that prints a chunk's own name back at the reader
looks like coverage and is not.

Every layout derived here was checked against an oracle nobody on this project
wrote.

### Added

- **AIFF reads five chunks it was reporting as unparsed bytes.** A 4,015-file
  library named nine chunk ids the walker could not open. Three of them acidcat
  already knew how to read -- inside a WAV. An `AFAn` Apple typedstream was
  named in a RIFF and called unparsed in an AIFF 241 times; a Logic `ResU`,
  which holds the tempo someone worked to, 53 times; and `CHAN`, which is
  CoreAudio's AudioChannelLayout, 214 times, while the byte-identical CAF
  `chan` was decoded. Those structures now live in `core/walk/apple.py` and
  three walkers call in. A structure read in three places is how the same bytes
  get an answer in one container and silence in the next.

- **The Apple Loops transient table (`trns`).** Where the slice points are: a
  76-byte header whose last four bytes are the record count, then fixed 24-byte
  records holding a flag and a position. The layout holds on every file
  measured with no mismatch. The positions are sample frames rather than bytes,
  and the evidence for that is external to the tool: across files carrying a
  tempo in their own filename, the median gap between transients is a
  sixteenth note at that tempo, four in five of them exactly. A transient
  falling past the frame count `COMM` declares is now a warning, which some
  files earn.

- **Apple Loops category labels (`cate`).** What the loop says it is, read as
  NUL-padded ASCII on a 50-byte grid. Only the labels are claimed: two payload
  shapes exist and the larger puts a gap where a flat array of slots would not,
  so the record structure around them is left undecoded rather than guessed.

- **XMP packets in RIFF (`_PMX`).** Adobe's metadata, as RDF/XML, and a sound
  library writes its whole catalogue record into it -- creator tool,
  description, publisher, artist, genre. acidcat was printing `unparsed, first
  bytes: 3c 3f 78 70...` at an XML document. Read through
  `core/formats/xmp.py`, because the same packet turns up in an MP4 `uuid` box
  and a JPEG APP1 segment and one place should know how. The reader names
  namespaces, never property names: a property whitelist is how a reader
  silently drops the one field that mattered. An `rdf:Alt` keeps its first
  entry, being one value in several languages; an `rdf:Bag` keeps all of them,
  being several values.

- **Undefined MIDI meta events are named.** SMF tells a reader to skip meta
  types it does not know, which is exactly why they are worth naming: a writer
  can put anything in one and every player stays silent about it. One library
  opens every track in 42 files with the same five `FF 4B` events,
  byte-identical -- a constant preamble rather than data. The track entry now
  carries a line per undefined type with a count and the first payload bytes,
  and names no vendor, because nothing in the file does.

- **Pro Tools chunks are named, not decoded.** `umid`, `regn`, `elm1`, `elmo`
  and `DGDA` get a name, a size, and the readable runs inside them -- a `DGDA`
  names its own record types in the clear, and a `regn` carries the region's
  name. There is no published layout for any of them and a field map guessed
  from one vendor's files is a guess that reads like a fact. `minf` is the
  exception: sixteen bytes whose first eight read as a Windows FILETIME. The
  reading is shown rather than asserted, and it is offered because it is the
  one that produces sane answers -- every value lands in the years those files
  were made, and no other common reading does.

- **FLAC checks its PADDING.** Padding is supposed to be zero, and padding that
  is not is space a writer overwrote in place with something shorter: the tail
  of whatever used to be there, a find rather than filler. RIFF has reported
  that for a while and it earns its keep. FLAC printed the block's size and
  stopped. The reader is now shared from `walk/base.py`.

### Fixed

- **One ID3v2 reader for the three containers that embed a tag.** There were
  two, and AIFF reached the MP3 one by writing each chunk to a TEMP FILE and
  reading it back -- a file per tag, and a failure wherever the filesystem is
  not writable. Worse than the duplication, they disagreed: a tag whose size is
  written little-endian, which a real writer does, read correctly in a RIFF
  chunk and not in an AIFF one, because that path never learned about it.

- **The Ogg comment header is reported whenever it exists**, not only when it
  holds a tag. Most Ogg files hold none: in a 1,200-file walk the header was
  present in 1,176 and reported in 18. What those files carry is the vendor
  string -- the encoder naming itself down to its build date -- and gating on
  the tag count threw that away on every ordinary file. The header is mandatory
  in Vorbis; an empty tag list was never a missing header.

- **An MP3 whose head was zeroed is still an MP3.** Files turn up that are
  ordinary MPEG audio behind a run of NUL bytes, a head clobbered by a failed
  write or reserved and never filled. The frame sync is not at offset 0, so
  every magic test missed it and the file was refused outright while two
  minutes of intact audio sat behind the hole. The rule is narrow on purpose,
  because scanning forward for any sync would call half a disk an MP3: every
  byte before the sync must be zero, the sync must decode, and a second header
  must sit exactly one frame length on. Measured across 68,268 files, it fires
  on four, and all four are real MPEG audio.

- **Chunk-level coverage notes reach the file-level warnings** in the AIFF and
  MIDI walkers, as the RIFF and CAF walkers already did. A caller reading only
  those was being told a capped listing was complete.

## [1.5.0] - 2026-09-09

Two formats the tool could name and could not open, a working `acidcat-lab`,
and a cross-check that measures our byte facts against a specification nobody
here wrote.

### Added

- **Sony Wave64 and Apple Core Audio Format walkers.** `docs/formats` had
  carried a full Wave64 anatomy page -- byte map, GUID arithmetic, alignment
  rule -- for a format `census` could count and no walker could open. Both are
  RIFF's grammar with substitutions, and every substitution is a place a RIFF
  reader returns a plausible wrong answer rather than an error: Wave64 ids are
  16-byte GUIDs, its sizes count the 24-byte chunk header where RIFF counts
  payload only, and it aligns to 8 with the padding outside the declared size.
  CAF is big-endian in the container but its samples follow a flag in `desc`,
  its sizes are *signed* s64 where -1 is legal and means "to the end of the
  file", and it has no alignment rule at all. Payloads are unchanged, so Wave64
  reuses the existing WAV parsers outright. Both were verified against
  libsndfile 1.2.2 output rather than against our own bytes.

- **The `acidcat-lab` CLI is wired.** 1.4.1 shipped the binary as a 35-line stub
  whose every path returned 0. `cavity` (embed/extract/analyze), `polyglot`
  (build/verify) and `stego` (embed/extract/capacity) now dispatch to the
  construction library. The carrier format is sniffed, `--into` overrides it,
  and writes refuse to clobber the input, reusing acidcat's own outpath guard.

- **`stego --method match` and `--method adaptive`.** `match` sets the low bit
  by +/-1 rather than overwriting it, the textbook counter to value-histogram
  attacks; `adaptive` writes only where the low bits are already noisy. Both
  16-bit. Measured and stated plainly: on real audio `adaptive` never worsens
  the detector reading, but a naive fill is rarely caught to begin with, so the
  practical margin over plain replace is narrow. A research tool, not an
  invisibility claim.

- **`probe lsb`.** A CLI verb for the sample-LSB entropy of a PCM WAV, to spot
  LSB steganography. The analysis already existed in the forensics layer but had
  no command surface: `audit` does not report it, so nothing short of calling the
  library exposed it. `probe lsb` draws the per-window entropy as a braille curve
  and reports the mean and how many windows carry signal, with `--json` for the
  full window array. It states a reading, never a verdict: a uniformly high LSB
  floor is consistent with an encrypted payload and equally with dithered or
  field-recorded audio, and entropy alone cannot separate them. A clean payload
  written into silence lights up; whitened stego in real noise is called out as
  indistinguishable, on purpose.

- **32 WAV format tags, up from nine.** A FLAC stream muxed into RIFF came back
  as `unknown 0xf1ac` from a tool whose whole subject is telling you what bytes
  are. Added the ADPCM family, GSM, the G.7xx codecs, MPEG Layer I/II beside the
  Layer III entry already there, WMA, AAC, Ogg Vorbis and FLAC. Deliberately not
  the whole 265-entry registry: most of the rest are codecs for hardware that
  has not shipped since the 1990s, and importing them would be bulk rather than
  knowledge. Anything outside the table still reports `unknown 0x....`, which
  claims nothing false. Two entries print both readings rather than picking a
  side, because `0x0039` and `0x2000` genuinely mean two things.

- **The byte facts are checked against a specification nobody here wrote.** A
  cross-check against the Kaitai Struct spec library compares acidcat's tables
  to upstream's in both directions, so a value we invented and a value we missed
  each fail. Every audio spec the library carries is now compared, and the
  formats it cannot reach (aiff, flac, sf2, caf, wave64, rf64 and mp3 have no
  `.ksy` upstream) are named in a test rather than left for the next reader to
  re-derive. It found six `au` encoding codes the walker was calling
  undocumented, and the synchsafe defect below.

- **Every registered walker has a seed.** Fifty-one more seeds across three
  batches. The differential sweep that once covered one of 52 formats reaches
  all 67 walker labels in a clone, and the "are we still only fuzzing one
  format" question is closed. Eight walker bugs fell out, each the moment a seed
  first reached the walker it was in.

- **Opt-in real-corpus sweeps of the extract and repair seams**
  (`ACIDCAT_HUNT_CORPUS`), so those paths are exercised on real files and not
  only on seeds.

### Changed

- **`acidcat-lab` is a separate distribution and no longer installs with
  acidcat.** It never was gated, whatever the packaging said: the `lab` extra
  was an empty list, `packages.find` shipped `acidcat_lab` inside the engine's
  wheel unconditionally, and a console script cannot be gated by an extra at
  all. So every `pip install acidcat` since 1.4.1 has installed the
  construction tooling and put an `acidcat-lab` binary on the PATH, for a tool
  that appears in no README, no ARCHITECTURE section and no documentation of
  any kind. The comments in `pyproject.toml` described the opposite arrangement
  and had done since the extra was written.

  The engine's wheel now contains the engine. `acidcat_lab` lives in `lab/`
  with its own project file and is installed with `pip install acidcat-lab`,
  which depends on `acidcat>=1.5.0,<2`. The dependency was always one-way -- the
  lab reaches the engine only through its public facade and the engine never
  imports the lab, which `tests/test_lab_boundary.py` has asserted all along --
  so nothing had to be untangled, only unbundled. The empty `lab` extra is gone
  rather than repointed, because nothing documented it.

  **If you were using `acidcat-lab`, note that it is not on PyPI yet.**
  Upgrading acidcat alone removes the binary, and until the new project is
  registered the way to get it back is from the repo:

      pip install "acidcat-lab @ git+https://github.com/hed0rah/acidcat#subdirectory=lab"

  One more thing worth knowing if you pinned the old extra: `acidcat[lab]` no
  longer exists, and pip does not treat an unknown extra as an error. It warns
  and installs the base package, so a requirements file asking for it will
  succeed and quietly give you no lab.

### Fixed

- **A synchsafe integer is seven bits a byte, and two of three decoders did not
  mask.** The high bit of every byte in a synchsafe length is defined zero --
  that is the entire reason the encoding exists, so a length can never contain a
  `0xFF` a decoder would mistake for a frame sync. Read as a plain big-endian
  u4, a 414-byte file declared a 268,435,466-byte ID3v2 tag and the walker
  skipped a quarter-gigabyte forward past the MPEG frame sitting at offset 10.
  The same four bytes had two readings inside one tool, 268 MB apart, and
  nothing said so. The third decoder sat in the field-edit path, where it
  returned values its own encoder refused as out of range, so decode and encode
  were not a round trip.

- **A chunk's end is its extent, not its payload.** The trailing-data check
  measured a container's end with `offset + size`, but `size` is the payload by
  contract, so every format whose chunks declare a header was short by that
  header on the final chunk and reported bytes that were not there. Thirteen
  seeded formats claimed trailing data; six actually have it.

- **A recognized file is no longer called unrecognized.** `walk` answered "not a
  recognized audio or preset file" for an N64 ROM the sniffer had just
  identified, which is false about the tool's own state and sends the owner away
  from `extract`, the verb that would have worked. Three formats are sniffable
  with no walker on purpose -- a ROM or a disc image is not a chunk tree -- and
  they are now listed rather than inferred, with a test that keeps the list
  exactly the set of sniffable-but-unwalkable formats.

- **An MP3 frame that runs past the end of the file says so.** A frame header
  states its own length and a truncated file can state one longer than the bytes
  that follow: 417 bytes reported in a 414-byte file, marked invalid, with no
  warning saying why. Latent since 1.0.0 and unreachable until the synchsafe
  mask let the walker find a frame in such a file at all.

- **Eight walker bugs the new seeds surfaced.** Two geometry overshoots: the
  RMID wrapped-SMF data chunk counted its 8-byte header twice, and the DMX
  header chunk inherited the RIFF `offset + 8` default it does not have. A MOD
  sample-header field-offset bug, now unpositioned with an xref as the XM walker
  already was. A zip local-header `OSError` escaping a zip-backed walker, now a
  clean `ValueError` the `.xpn` walker skips the corrupt entry on. `labx` and
  `multisample` let that same `ValueError` escape at four further call sites,
  and a corrupt entry in `multisample` is now reported unpositioned rather than
  at offset 0, since 0 is a position a carve would follow to the front of the
  archive. `midi2` rendered MIDI 1.0 Channel Voice messages through the MIDI 2.0
  field names, raising `KeyError` on five of six statuses from a valid document.
  `amxd` declared size as the extent while also declaring a `payload_base`, so
  every chunk's payload ran 8 bytes long and the last left the file. `rx2` gave
  two chunks absolute field offsets where the rule wants them relative to
  `payload_base` -- the same defect `mod`, `au`, `voc` and `krz` carried. And
  `ni` used an unhashable MessagePack key.

- **`anomalies.scan` walks the file itself when given only a path**, instead of
  requiring pre-walked chunks. The four-argument form is unchanged.

## [1.4.1] - 2026-09-05

### Fixed

- **A field offset is relative to its chunk, not to the file.** Three walkers
  handed `_f` an absolute cursor while their chunk declared a `payload_base`,
  so the renderer added the base to an offset that already contained it and
  every field pointed at twice the distance in. The SAP `binary` block and the
  NSF2 appended metadata shipped that way in 1.3.2; the MPC2000 `.pgm` slot
  table had the same defect, and its last slots escaped the chunk that
  declared them. All three were caught the day a test first reached a real
  file of the format, by a containment check that had existed the whole time.

- **The Kurzweil PCM region claimed eight bytes past the end of every bank.**
  The raw, headerless region after the object walk declared no `payload_base`,
  so geometry substituted the RIFF `offset + 8` default: the fourth format
  with the 1.4.0 "a byte belongs to one chunk" defect. It showed as an
  overshoot rather than an overlap only because PCM is the last chunk. 39 of
  40 real Sweetwater banks, silently; the existing corpus test asserts only
  that the walker never raises, which is why it never saw this.

### Added

- **The geometry invariants can reach a real corpus.** `ACIDCAT_HUNT_CORPUS`
  names one or more local roots of real specimens. Every file under them is
  sniffed and the first 40 per format walked (`ACIDCAT_HUNT_PER_FORMAT`
  overrides), bounded so a large tree stays finite. A walker that raises on a
  real file fails a test that names the file instead of being skipped. The
  floor the ratchet asserts is unchanged: a run with the corpus is stronger
  evidence, never a stricter assertion. Its first run found the Kurzweil and
  MPC2000 defects above in under a minute.

- **Eleven more seeds** (au, nsf, nsfe, sap, cdxa, cue, vag, sid, mdx, adx,
  hps), lifted from the test modules that had each kept one privately. The
  geometry invariants reach 21 formats in a fresh clone instead of 5, the
  fuzz sweep picks the new seeds up automatically, and the floor is asserted
  at 20 so reach can only rise on purpose. This widening is what exposed the
  SAP and NSF2 defect.

- **A pointer field has to point somewhere real.** A field marked `xref` is
  followed by the TUI and resolved by the forensics layer; one aimed past the
  end of the file is now an invariant failure, with the same rule as the
  overshoot check: a dangling pointer the walker announced, in a truncated
  module for instance, is damage described correctly.

- **`scripts/sync_anatomy_mirror.py`** publishes the anatomy pages from canon
  to the site mirror, CRLF there and LF here, and refuses to invent an index
  card for a page that has none.

## [1.4.0] - 2026-09-05

### Added

- **Sun/NeXT audio (`.au`, also `.snd`).** The self-describing form of the raw
  PCM that came before it, and the format where the `.snd` magic and the mu-law
  codec of early Unix and NeXT workstations were written down. A fixed 24-byte
  big-endian header carries everything a decoder needs in six 32-bit fields,
  then an optional annotation, then samples.

  Two things in it are easy to get wrong and both are handled. The data size may
  be `0xFFFFFFFF`, meaning "not known" for a stream written before its own length
  was, so reading that literal as a byte count is the first mistake available.
  And the encoding CODE, not a bit-depth field, names the sample format: the
  companded telephone codecs are eight bits on disk but are not linear PCM, so
  they are named and flagged rather than passed off as samples that would play
  as noise.

  Duration is reported for every encoding whose on-disk sample width is fixed,
  which includes the companded pair. The ADPCM tail is framed and named but its
  duration stays unknown rather than plausible, because those coded widths have
  not been checked against a real file.

  An anatomy page documents the layout, and `convert` turns an `.au` into a WAV
  through a new G.711 codec written against Sun's reference and checked at its
  fixed points. That codec is new rather than reused: the VOC walker only
  *labels* mu-law and A-law, and `audioop`, which could decode them, was removed
  in Python 3.13.

  Formats 67 -> 68.

### Fixed

- **A byte belongs to one chunk.** A chunk that never declared `payload_base`
  had `offset + 8` substituted for it, the RIFF convention of an eight-byte
  chunk header, in formats that have no such header. Its extent then ran exactly
  eight bytes past its own size, into whatever started next: the VOC header
  claimed 26 bytes and occupied 34, over the top of the first sound block, and
  the Serum json run overlapped its blob.

  Invisible from every angle but one. Each chunk's `size` was correct, the
  trustworthy check passed for both members of the pair, and nothing left the
  file, so the existing overshoot test saw nothing either. Only comparing
  extents across siblings shows it, and that comparison is now an invariant at
  the walker contract rather than a fix in two walkers.

- **Six robustness findings from an adversarial audit**, all reachable from the
  CLI with files under 100 KB:

  - four walkers raised `struct.error` on input shorter than their own headers,
    breaking the degrade-with-warnings promise; one was reachable from ordinary
    sniff dispatch
  - `geometry.normalize` ran outside the walk boundary's guard, so a geometry
    edge produced by a walker bug escaped the net that exists to contain walker
    bugs
  - the sniffer inflated a whole zip member to keep 40 bytes of it, upstream of
    every walker cap: a 61 KB file peaked at 144 MB
  - the codec layer behind `extract` and `convert` trusted header counts the
    walkers refuse to trust, including a block size of zero that never advanced
    a decode loop
  - a forged MP4 sample-table count took `repair`, `validate` and `audit` down
    with an uncaught `struct.error`, and two of those are batch entry points, so
    one hostile file ended a whole corpus sweep
  - a pad byte after an odd nested LIST was consumed by the leaf parser but not
    the container parser, so repair wrote a master size ending before the audio.
    Fifty readable frames before, zero after, exit code 0

- **A WAV written to a pipe is not a damaged WAV.** A writer streaming to stdout
  cannot know its length, so it writes a placeholder. All three in circulation
  were reported in the words a truncated file gets, and the zero case was worse
  than noisy: it reported a file holding real audio as empty, silently. A
  genuinely wrong size still complains, and a zero size with no bytes after it
  is still an empty chunk.

### Changed

- Punctuation reformatted across the anatomy pages and the two builders that
  generate them, so a regenerated page matches the rest. The leader mark before
  a tree annotation is gone entirely: the span already styles the text as a
  remark, so the mark was duplicating what the styling said. A full prose and
  styling pass over the fleet is deferred to the next release.

## [1.3.2] - 2026-08-31

### Added

- **NES Sound Format (`.nsf`, NSF2), NSFe, and Slight Atari Player (`.sap`).**
  None of the three describes music. Each ships the original 6502 program that
  produced it, plus enough metadata to start it, so a walker can honestly report
  where the code goes and where the entry points are and nothing about how it
  sounds.

  That lineage is stated by the format's own author: NSF is "somewhat sorta
  based on the PSID file format for C64 music/sound". The literal `<?>` for an
  unknown author appears in NSF, SAP and PSID alike.

  Three things in these layouts do not mean what they appear to. An NSF load
  address stops being an address when the file is banked -- the low twelve bits
  become a count of padding bytes -- and nothing flags the change, because the
  all-zero bank array *is* the flag. The three 32-byte text slots are fixed
  width **and** NUL-terminated inside, and either half alone gets you a title
  that runs into the artist. And an NSFe chunk header is length-then-FourCC, the
  reverse of RIFF and IFF, so plumbing borrowed from either reads a FourCC as a
  size and still produces output that looks like a parse.

  NSF has only ever had version bytes 1 and 2. There is no 1.01/1.02/1.03
  ladder; those numbers belong to the specification document's own revision
  history, and to PSID. A version byte of 3 or higher is damage.

  A SAP block's end address is inclusive, so a block holds `end - start + 1`
  bytes; reading it as `end - start` loses the last byte of every block and the
  file still parses. SAP also defines no end-of-header marker at all, so the
  rule that the text stops at the first `FF FF` is derived from the permitted
  character set rather than quoted from the specification, and is labelled that
  way.

  Verified across 21,060 real files: 13,042 `.nsf`, 1,682 `.nsfe` and 6,335
  `.sap`. Nothing raised, nothing produced untrustworthy geometry, every byte
  accounted for, and 98.8% walked with no warning at all. That last figure is
  the one that mattered: a check firing on a third of a real corpus is not a
  check, it is noise that teaches a reader to skip the warning line, and no
  amount of synthetic testing can measure it.

  Three things the specifications left unmeasured are now measured. Every one of
  the 1,682 NSFe files carries the `NEND` chunk both specs call mandatory, so
  its absence can be reported as damage rather than as a tolerated omission.
  SAP's `TYPE R` does not occur at all, which resolves a contradiction in the
  specification about whether it exists. And the derived `FF FF` boundary rule
  split every SAP file in the right place.

- **Anatomy pages for NSF and SAP.** Both open with the chip the format exists
  to drive -- the 2A03's five voices, POKEY's four dividers and polynomial
  counters -- because the container only makes sense as a way of feeding it.

### Fixed

- **`inspect_nsf` checked its length and not its magic.** Both sibling walkers
  checked theirs, so any file of at least 128 bytes parsed as an NSF and
  reported a confident title, artist and load address out of bytes that were
  never a header.

  The corpus held 50 files wearing a `.nsf` extension that are HTML error pages
  from failed downloads and macOS AppleDouble resource forks. Every one of them
  walked clean, and between them they generated around 180 structural complaints
  about files that are not NSFs at all. An invariant test now asserts all three
  walkers verify their signature.

## [1.3.1] - 2026-08-30

### Added

- **Sharp X68000 MXDRV tunes (`.mdx`).** A Music Macro Language score for the
  YM2151 (OPM) FM chip, with optional ADPCM samples in a separate `.PDX` file
  the MDX names. Big-endian throughout, which for once follows the machine: the
  X68000 is a 68000.

  The format has **no magic number**. A file opens with its title in Shift-JIS,
  so identification is arithmetic: a `0D 0A 1A` terminator, a NUL-terminated
  sample-bank name, then an offset table that must resolve to 9 or 16 channels
  with every offset inside the file.

  Every offset is relative to the position of the voice-offset *word*, not to
  the start of the file. The title and sample-bank name are both variable
  length, so that base moves per file; read them as absolute and a tune points
  nowhere useful, by an amount that grows with its title.

  The channel count is derived rather than stored: `(first_offset - 2) / 2`.
  That works only because the first channel's data begins immediately after the
  table, which nothing in the format states but which holds in every file
  measured, with the voice block always placed after the channel data. Both are
  asserted, since a file breaking either would make its own count underivable.

  Verified against 27,166 tunes from the X68000 MDX Master Library: 26,689
  identified, zero crashes, zero untrustworthy geometry, and every identified
  file accounted for byte for byte. An anatomy page documents the layout.

- **Console stream formats: CRI ADX, Nintendo BRSTM, HAL HPS, Sony VAG.**
  Decoders for all four already existed and `extract` was using them to rip
  audio, so the structure was being computed and thrown away. `inspect` refused
  to describe files the tool could already open.

  One module, one shared vocabulary: codec, channels, sample rate, frame count
  and duration render in the same order with the same names whichever console
  wrote the file, so comparing two of them is not also translating between two
  sets of words for the same number. What is deliberately *not* shared is the
  chunk structure, because it genuinely differs -- a header and a body, a header
  pointing at named blocks, a linked list, and a header with a name in the
  middle of it.

  HPS is the one where a shortened read shortens an answer rather than a search:
  its block chain is walked through the buffer, so a file read short reports
  fewer blocks than it has.

- **CUE sheets and GameCube disc images.** Same story: `extract` parsed the
  layout to find the tracks and discarded it.

  A CUE is the odd one in the whole set. Its positions are in *sectors*, and
  they are relative to a `.bin` the sheet only names, so it is the only format
  here whose offsets point outside the file being walked. Whether that binary is
  actually beside the sheet is therefore reported rather than assumed.

  The GameCube walker reads the 0x440 header and the file-system table and
  nothing else; the image is gigabytes and none of it is loaded.

- **Raw CD sector images (CD-XA).** There is no header at all. A CD image is a
  flat array of 2352-byte sectors, each carrying its own sync mark, and what the
  image *is* lives in a byte repeated across every one of them.

  XA audio is not a file in a directory either: it is interleaved through the
  data track and tagged per sector, so a stream is every sector sharing a
  (file, channel) pair, scattered across the disc. The scan is capped, because a
  PlayStation disc is over 300,000 sectors, and the walk states how far it got.
  A shortened search reports "2 streams" in exactly the words a complete one
  would.

### Changed

- **Every anatomy page is now reachable by keyboard.** The byte maps bound
  `mouseenter` and `click` and nothing else, so the decode -- which is the whole
  point of the pages -- was pointer-only. Field rows now take focus, drive the
  same highlight on focus as on hover, pin on Enter or Space, unpin on Escape,
  and move with the arrow keys.

  The byte cells deliberately stay unfocusable: forty tab stops to cross one
  header would bury the field list, and the grid is an index of that list rather
  than a peer of it.

  The focus ring existed as `button:focus-visible`, which is an element
  selector. The rows are `div`s carrying `role=button`, so the rule never
  matched them and focus had no visible position.

  The colour key was `display:none` below 720px while the colours it explains
  stayed on screen, leaving a phone reader four colours and no way to decode
  them. It reflows into a horizontal strip instead.

- **SID combined waveforms are now a bitwise AND, which is what the chip does.**
  Bob Yannes, who designed it: "The combination was actually a logical ANDing of
  the bits of each waveform, which produced unpredictable results, so I didn't
  encourage this." Each generator is evaluated as its real 12-bit value and the
  selected ones are ANDed, so a combined waveform is quieter and more lopsided
  than either component.

  **This changes rendered audio.** Averaging was wrong for triangle plus
  sawtooth, which correlate only 0.77. Measured across 203 tunes rendered both
  ways, not one gained or lost sound and the median RMS is unchanged; across
  112 compared sample for sample, 111 differ but the median difference is 0.0%.
  Only tunes that actually combine waveforms are affected.

  Ring modulation follows the same source: it substitutes the previous
  oscillator's accumulator MSB into the triangle generator's EXOR rather than
  scaling the triangle by a sign, which is why ring mod is audible only on a
  triangle.

### Fixed

- **The MIDI anatomy page stated MIDI 2.0 velocity as 256x MIDI 1.0's.** It is
  512x: 7 bits is 128 values, 16 bits is 65,536. The sentence gave both widths
  and got the ratio between them wrong.

- **A SID pulse width of 0 is a 0% duty cycle**, not a 100% one. A square wave
  is correct under either reading of the comparison, so the existing 50% test
  could never have caught the inversion; the new test covers 0, 25, 50 and 75%.

### Documentation

- The SID page gains three facts from the specification: that `$07E8` is
  `$0400` plus the 1,000 bytes of default screen RAM, that the `speed` field is
  a redefinition of one that never worked as intended on PlaySID for Amiga, and
  that its bits should still be set when nothing reads them.

- It also now says where it disagrees with its own source. The specification
  states "the speed specified for tune 32 is the same as tune 1", but its
  preceding sentence gives subtune 1 to bit 0, leaving subtune 32 holding bit
  31. Read literally that strands bit 31 and shifts everything above it by one.
  The wrap begins at subtune 33.

- `docs/formats/README.md` said the published site was canonical and this
  directory a mirror. The direction reversed when the design work moved into
  this repository, so it was telling readers to edit the copy that gets
  overwritten.

## [1.3.0] - 2026-08-27

### Added

- **Commodore 64 SID tunes (`.sid`), and playback by running them.** A SID file
  holds no audio: it is a big-endian header describing a 6502 music player,
  followed by that player and its data as a raw C64 memory image. The walker
  reports the structure; playback means executing it. acidcat ships a 6510
  interpreter and a SID synthesiser, calls the tune's init routine, then its
  play routine once per emulated frame, and turns the resulting register writes
  into PCM. `p` in the TUI plays a `.sid`.

  Verified against the whole High Voltage SID Collection, 61,157 tunes: every
  one sniffs, walks, produces trustworthy geometry and is accounted for end to
  end, with zero spec violations. The songlength hash matched all 61,157
  entries of the HVSC database. 57,122 (93.4%) can be driven from a play
  address; of a random 2,309-tune sample rendered for eight seconds each, 99.4%
  produced sound and none crashed.

  Three things in the format are traps rather than details, and all three are
  handled: the header is big-endian on a file describing a little-endian 6502;
  a `loadAddress` of 0 means the address is the first two bytes of the C64 data,
  little-endian (which every real tune does); and a 32-byte string field holding
  32 characters has no terminator, so a C-string read runs into the next field.
  RSID is a restricted PSID whose four pinned fields a reader must reject on.

  What will not play is declined with a reason rather than rendered as silence:
  a tune that installs its own interrupt handler (every RSID), or a Compute!'s
  Sidplayer MUS payload with no player in it. The render is deliberately
  approximate -- pitch, oscillators and envelopes are right, the analog filter
  is a straight-line stand-in for a curve that differs between chip revisions --
  and the result carries an `approximate` flag rather than leaving a listener to
  discover it.

- **A per-format specimen corpus, generated rather than gathered.** Twenty-five
  formats now have a minimal valid file built at test time, so CI exercises 36
  format walkers instead of 21 and sweeps 1,252 mutated inputs instead of 880.
  Sixteen of those specimens come from builders the walker tests already had.

### Fixed

- **Eight walker bugs, all in formats that had never had a specimen.** Among
  them: a decompressor that let `zlib.error` escape a walk, several field
  offsets read as absolute when the contract is relative to `payload_base`, and
  `channels: 0` reported as a fact rather than as impossible.

- **An MP4 box length overwrote the file's own length.** A local variable named
  `size` held `os.path.getsize`, and three hundred lines later a box header
  loop reassigned it. Harmless until something read it afterwards, at which
  point a 254 KB file was measured against a 3,531-byte box and the structure
  was reported as explaining 1.0% of it.

- **An Ableton `.asd` with a zeroed grid tail raised `TypeError` out of a
  walk.** The sample rate comes from the largest step between grid positions
  and the duration from the last position; zeroing the tail keeps the first and
  destroys the second. Both were guarded on the rate alone, so `None` reached a
  `:.3f` format.

- **An N64 `.ctl` bank whose declared counts outlive its length let
  `struct.error` escape.** Four sites checked the bytes each pointer pointed at
  while trusting that the pointer itself was readable.

- **A wall texture is not a table of contents.** The fixed-width TOC detector
  accepted a run of repeated names as an index.

### Changed

- The publish workflow now calls the test workflow rather than a copy of it.
  The copy had drifted into being weaker than the thing it stood for: one job,
  on a Python the matrix does not test, with neither ffmpeg nor bubblewrap, so
  the least-covered run in the repo was the one deciding what reached PyPI.

## [1.2.2] - 2026-08-23

### Fixed

- **A GameCube stream decoded every sample with the wrong channel's scale
  factor.** DTK frames give each channel its own header byte carrying a scale
  exponent, and the low nibble of each data byte belongs to the left channel,
  the high nibble to the right. This read them the other way round.

  That is not a channel swap. Because each channel has its own header, reading
  the nibbles backwards pairs every sample with the OTHER channel's scale
  exponent -- and a scale exponent is a power of two. The music keeps its shape
  while each sample is off by a factor of 2^n, which is why it sounds like
  damage rather than like a mix-up. Against a reference decoder: 1.7% of samples
  correct, error louder than the signal.

  The predictor history was also carried as a clamped 16-bit sample. The
  hardware keeps six fractional bits and does not clamp it; this is a recursive
  filter, so rounding its state every step feeds the error back into the next
  prediction.

  Now bit-exact across all 16 scale exponents and all 16 predictor indices.

- **The one test covering the broken nibble order asserted the bug.** It was
  written from the implementation rather than from the format, so it agreed
  with the defect and held it in place. Rewritten from the console's own
  arithmetic, with an independent reference decoder in the test file so
  agreement is evidence rather than a tautology.

- **Two decoders started their predictor at silence regardless of where the
  audio started.** `dsp.decode` takes the initial history and its own docstring
  passes it; the BRSTM and HPS callers did not. Both formats store it -- BRSTM
  behind the coefficient table, HPS in each block header -- so it was read past
  rather than absent. DSP-ADPCM is recursive, so a stream that does not begin
  at silence decodes its opening frames against the wrong state.

- **An archive index read at half its stride reported twice the entries.** Every
  other record was real and the ones between were a leftover buffer field
  mistaken for a name, so the false reading was self-consistent and longer --
  and length was what picked the winner. Distinctness could not separate them:
  half the names being real puts it at 0.509 against a floor of 0.5, which is a
  coincidence rather than a margin.

  How much of a table ONE repeated name accounts for separates them cleanly.
  Measured over 1,644 candidate readings of 32 shipped archives: the worst
  genuine index is 0.077, every false reading of a real directory 0.38 to 0.52.

- **Zero-size entries were treated as overlapping.** Only an entry with an
  extent can collide with its neighbour; marker entries that delimit a run and
  carry no data point at nothing, and counting them as conflicts rejected the
  index that contained them.

- **A wall of texture bytes was read as a table of contents,** and a run of
  0xFF bytes as an MPEG audio frame. The sync-word density that a real MPEG
  stream never reaches now separates them: five times the worst real reading,
  seven times under the false one.

- **Playback assumed RIFF.** Any format whose sample rate lives outside a `fmt`
  or `COMM` chunk played at the wrong rate; the parameters are now taken from
  whichever chunk declares them.

### Added

- **Creative Voice (`.voc`) walker.** The sample format of the DOS era: block
  types 01, 02, 05 and 09, the time-constant to sample-rate conversion, and the
  one-byte terminator. Verified over 896 shipped specimens.

- **Doom DMX (`DS*` lump) walker.** Eight bytes of header over raw samples and
  no magic string anywhere, so identification is arithmetic rather than a
  signature: the declared count plus the header must equal the lump's own
  length, exactly. That held for all 1,181 shipped lumps. The rate is read
  rather than assumed -- three quarters of the corpus is not 11025 -- and the
  lead-in padding is reported rather than trimmed, because 168 of those lumps
  do not have it and cutting a padding that is assumed removes real audio.

- **Filtering and sorting in the TUI region list.** Type to narrow by name or
  format; Tab focuses the header, left and right pick a column, Enter sorts and
  flips. Sorting reorders the view, so the row-to-region mapping is now explicit
  and tested as a bijection across every column in both directions -- a
  permutation bug there would extract one file twice and another never, and it
  would not look like an error.

### Changed

- The corpus test over real archives now reports every disagreement instead of
  the first. The assertion used to fire inside the loop, and because pytest
  stops at the first one, a run over 32 archives reported ONE and looked like it
  had cleared the other 31; eleven of them were also wrong. It is now a ratchet
  that can only go down.

## [1.2.1] - 2026-08-20

### Fixed

- **A test that waits for the tree to stop moving is still guessing.** 1.2.0
  replaced the TUI suite's fixed pauses with a poller that waits for the node
  count to settle. That is a proxy, and it has a hole: a Textual worker that has
  been dispatched and has not yet delivered leaves the count perfectly still, so
  the poller hands back a half-built tree that merely looks finished.

  It failed the publish gate on one platform with four nodes of an eventual
  twelve, reporting a missing codec field as a product defect -- the same shape
  of wrong answer the previous fix was meant to end.

  Four call sites now wait for the condition they actually need rather than for
  stillness, and the waiting itself is pinned by tests with a fake pilot. A
  poller that started returning early would otherwise be invisible: every call
  site would revert to sampling a half-built tree and would still pass on a fast
  machine, with the failure reappearing only on a loaded runner.

  Four call sites now wait for the condition they actually need rather than for
  stillness, and the waiting itself is pinned by tests with a fake pilot. A
  poller that started returning early would otherwise be invisible: every call
  site would revert to sampling a half-built tree and would still pass on a fast
  machine, with the failure reappearing only on a loaded runner.

- **And one test had no condition to wait for, so it had to ask the workers.**
  The tree-settles test asks whether expanding an already-complete tree adds
  nodes. Completion is the only thing it can wait on, which left a stability
  window as the only tool available -- and a window short enough to keep the
  suite quick is one a loaded runner outruns. It read 4 nodes of an eventual 12.

  Textual's `WorkerManager` is iterable and sized, so "is anything still
  running" has an exact answer. The suite now asks that instead of inferring it
  from the tree, which can only report a worker as finished after its result has
  already arrived -- the gap every earlier version fell through. The wait is
  bracketed by two pauses, one so `expand()` can spawn its workers before they
  are counted and one so their results can reach the tree, and it gives up
  rather than hanging, so a stuck worker fails an assertion instead of stalling
  the job.

  No product code changed in any of this. The suite runs 2,869 tests, and the
  deep-nesting file alone went from 3m56s to 3m14s: waiting on the workers ends
  each round as soon as they drain instead of always spending a fixed pause.

## [1.2.0] - 2026-08-20

### Added

- **The table-of-contents detector reads the other half of the family: an index
  written as an array of C structs.** It could already read a length-prefixed
  index, the shape a modern serializer emits, where every name carries its own
  length. Everything written before that convention existed puts the name in a
  fixed-width character field, NUL-padded, followed by integers, repeated at a
  constant stride with nothing marking where a record begins. Reading one does
  not read the other, and the second is most of the archives that exist.

  Measured against the shipped data of six games, none of which agreed on the
  layout:

  | archive | stride | name at | integers | index at |
  |---|---|---|---|---|
  | Quake, Quake II, Hexen II `.pak` | 64 | +0, width 56 | offset and size | tail |
  | Duke Nukem 3D, Shadow Warrior `.grp` | 16 | +0, width 12 | size only | head |
  | Half-Life `.wad3` | 32 | +16, width 16 | before the name | tail |
  | Doom `.wad` | 16 | +8, width 8 | before the name | tail |

  So the stride, the field width, which side of the name the integers sit on,
  and whether the archive stores offsets at all are discovered rather than
  assumed. Eleven archives now read exactly, against counts taken from their own
  headers: 339, 85, 3,307, 696, 523, 456, 693, 3,116, 3,163, 3,610, 266 entries.
  That is **2,155 audio files** -- Quake II's 546 WAVs, Shadow Warrior's 565
  VOCs, Duke Nukem's 373 -- reachable by name and exact extent instead of not at
  all. In freedoom1.wad all 69 DMX sounds land on their headers, where the
  statistical sweep had managed 68 with 175 false positives beside them.

  A wrong table is worse than no table, so what settles a hypothesis is never
  its shape. A compiled symbol table inside SW.GRP is longer and tidier than the
  archive's own directory and loses because it places no payloads; a General
  MIDI instrument list inside a Doom WAD looks exactly like an index and loses
  the same way. Where nothing carries a checkable extension -- a Doom WAD names
  its lumps and gives them no suffix at all -- the evidence is arithmetic the
  archive writer must have performed: payload extents that do not overlap and
  that end where the index begins. Over 1,200 real audio files this placed no
  table at all. It did report eight, all declined rather than placed, and they
  turned out to be real: Logic EXS24 instruments carry a genuine 188-byte zone
  table, one record per chromatic note.

- **`read_toc` looks at both ends of a file.** An index is usually written last,
  because appending payloads as they are produced and writing the directory
  afterwards means never seeking backwards -- Quake, Doom and Half-Life all put
  theirs within 450 KB of the tail. The TUI passed the first 2 MB, which is not
  a small oversight for this family so much as a window pointed away from it.

### Fixed

- **Four TUI tests measured the clock instead of the app.** Each pressed a key,
  waited one event-loop tick, and asserted on state the keypress was supposed to
  produce. That holds while a runner is fast and stops holding when it is not:
  two of them failed on `main`, on different platforms in different runs, one
  job out of five each time.

  The shape is always the same, and it is not "the pause was too short". `X`
  extracts what `space` marked, and declines when nothing is marked -- so a
  `space` that had not landed yet made `X` decline, and the failure read as
  `got == {}`, which looks like `X` is broken. Likewise `ctrl+e` refuses unless
  the cursor is on an editable node, and `z` zooms whichever pane has focus, so
  pressing it before `tab` has moved focus zooms the wrong one. In every case
  the test reported a missed keystroke as a defect in the feature.

  They now wait for the condition the next step actually needs, and say which
  condition never arrived when it does not. A fifth, the tree-settles test,
  snapshotted a node count while the tree was still growing -- it read 4 of an
  eventual 12 -- and then reported the tree as "still growing" when what was
  still growing was its own first measurement. It waits for the count to stop
  moving before taking it.

  The poller had been written once already, in `test_tui_force.py`, with a
  docstring naming this exact lesson. It now lives in `tests/conftest.py`
  alongside a `settled` companion, because the third copy is the point at which
  a helper is a convention rather than a local fix.

- **The table-of-contents detector was confidently wrong on image and audio
  data.** Asked whether a run of bytes was a filename, it checked only for a
  dot, slash or backslash. Those are byte values before they are punctuation:
  0x5C is an ordinary pixel and an ordinary quiet sample, so a wall texture in
  freedoom1.wad satisfied "looks like a Windows path" and chained into a
  100-entry table reported at **confidence 0.84**. A detector built to find
  audio inside unknown containers was being fooled by the audio.

  A name now has to span at least 32 byte values, because a filename draws on
  several ASCII classes (a dot at 0x2E, digits at 0x30, capitals at 0x41,
  lowercase at 0x61) while 8-bit image and audio data crawls through a narrow
  contiguous band -- the second false table freedoom produced spanned NINE, `[`
  through `d`. It must also be mostly alphanumeric and have its separator doing
  a separator's job. Confidence now weighs whether the names read like names;
  it was chain length alone, which only proves a layout is self-consistent, and
  smooth bytes are extremely self-consistent.

  The 187 MB archive this was built against still reads all 266 entries. The
  regression specimen is 2,048 real bytes of that texture rather than generated
  data: the first version of the test used a synthetic ramp, and deleting the
  fix left every assertion passing, because synthetic ramps trip the other
  guards. Only the real bytes reach the hole.

- **`develop` is retired.** Squash-merging a long-lived branch into `main`
  guarantees divergence every time: squash discards the source commits for a new
  one with a different hash, so the branches can only be reconciled by hand
  afterwards, after every merge. It already cost something real -- `develop`
  kept a file `main` had fixed, and a release cut from it would have shipped the
  old version back. Short-lived branches off `main`, merged and deleted, are
  what squash-merge is for and what this repo had already drifted into using.

- **CI was running a suite 85 tests smaller than anyone's local one.**
  `data/test_formats/` is gitignored and 16 MB, so any test naming a path
  inside it skipped on every runner: 90 skips beside a green tick, 85 of them
  that one cause. The suite gating a release was 85 tests smaller than the
  suite anybody ran locally, and the gap was invisible from both sides. It is
  what shipped a release whose CI was red on all five platforms while the
  local run reported 2,826 passing.

  Four committed fixtures now stand in, each for a distinct header path
  rather than a distinct encoding: 24-bit sample width, WAVE_FORMAT_EXTENSIBLE
  with a channel mask, big-endian COMM/SSND, and bit depth inside FLAC's
  STREAMINFO bitfield. Measured on the ubuntu-3.13 runner, 2,743 passed and 90
  skipped becomes 2,831 passed and 9 skipped: 88 tests that had never run on
  any runner now run on all five platforms.

  A ratchet fails, by file and line, if a test reaches into the gitignored
  tree again, and checks the stand-ins are COMMITTED against `git ls-files`
  rather than the filesystem -- the filesystem being exactly what lied the
  first time.

- **The gate on publishing was the weakest run in the repo.** `publish.yml`
  carried its own copy of the test job, and the copy had drifted: one job, on
  ubuntu, on a Python the matrix does not test, with neither ffmpeg nor
  bubblewrap installed, verifying 113 fewer tests than an ordinary pull
  request. It now calls `test.yml` directly, so the irreversible action is
  gated by the full five-platform matrix and there is one definition of "the
  tests pass" rather than two that can diverge.

- **Every check ran twice.** A pull request from `develop` matched both the
  `push` and `pull_request` triggers, costing ten jobs and about 35 minutes of
  runner time per commit. Grouped on the commit now, so the second trigger
  supersedes the first; a push with no pull request still gets its own run.

- **Two tests carried absolute paths from one machine.** The sdist ships
  `tests/`, so one of them put an account name into every release from 0.55.0
  onward. Both are opt-in environment variables now
  (`ACIDCAT_KRZ_CORPUS`, `ACIDCAT_TMOD_SPECIMEN`). Found by scanning the built
  artifact rather than the working tree, which is the only place it was ever
  visible.

## [1.1.1] - 2026-08-16

1.1.0 was tagged and never published. Its own CI run was red on all five
platforms, and the publish workflow tests the tree it is about to ship, so the
tag could not become a release. `refs/tags/v*` is protected against deletion and
non-fast-forward, which is the rule working: a release tag that has been seen is
not a draft to be corrected. 1.1.1 is 1.1.0 plus the fixes below, and is the
first published build of that work.

### Fixed

- **A machine's home directory is not a constant.** Two tests carried
  absolute paths from the machine they were written on, and the sdist ships
  `tests/`. Both are environment variables now. This is in 1.1.1 because the
  tag sits on that commit.

- **Three tests that only passed on the machine that wrote them.** No product
  code changes here; all three were defects in the tests.

  The chunk-geometry ratchet asserted it had walked at least 40 files across 7
  formats. That was measured against a `data/` carrying `data/test_formats/`,
  which is gitignored: a clone walks 7 files across 5. It passed where it was
  written and failed everywhere else, which is a local reading reported as a
  fact about the repo, inside the one test whose stated job is catching that.
  The floor is the committed corpus now. Its glob was also relative to the
  working directory, so running pytest from anywhere but the repo root matched
  nothing, skipped the fixture, and reported four assertions as passing having
  examined no bytes at all.

  The pipe test decoded correct output wrongly. `scan` reconfigures stdout to
  UTF-8 deliberately, while `text=True` decodes with the locale encoding,
  cp1252 on the Windows runner. The katakana filename was right on the wire the
  whole time and arrived as mojibake.

  The forced-parse tests waited a flat half second on a worker that tries every
  walker in turn. How long that takes is a property of the machine, so it held
  on the three fastest platforms and failed on the two slowest. They poll now.

## [1.1.0] - 2026-08-16 [unpublished]

The reverse-engineering half of the TUI. A tree that stops at a fixed depth
cannot follow a container into a container, and this one did -- three levels,
then nothing, on a file that had five. Fixing that turned up the reason it was
never noticed: several places reported a limit of ours as a fact about the file.

### Changed

- **BREAKING: `scan` and `features` write CSV to stdout, not to a file nobody
  asked for.** `acidcat scan DIR` printed nothing and wrote
  `<dirname>_metadata.csv` into whatever directory you were standing in,
  overwriting a file of that name without asking. Both now pipe, and write a
  file only when `-o` names one. A script that ran `scan DIR` and then opened
  `DIR_metadata.csv` wants `-o DIR_metadata.csv`.

  The suite held both positions on this at once: one test carried an xfail
  calling it a defect "documented for 1.0.1", another asserted "the default is
  load-bearing". `features` disagreed with itself too -- `features DIR --json`
  already piped while its CSV path wrote a file.

### Added

- **The tree goes as deep as the file does.** `core/forensics/explore.py` asks
  one question -- what is inside these bytes -- at every level, with no depth
  ceiling. Engines are alternatives, first hit wins the range: a real walker
  (with generic structural triage inside it), then the audio sweep. It descends
  into a chunk's PAYLOAD, not its extent, which is what keeps the hierarchy:
  through the extent the sweep finds the audio anyway and deletes the layer it
  sat in.
- **Regions are tree nodes, and selection works where you are looking.** `space`
  marks, `A` marks all, `X` extracts what is marked, `E` extracts everything --
  spelled the same in the tree and in the region list.
- **Colour themes.** `ACIDCAT_THEME=killengn` or `faterally` beside the default.
  The stylesheets are templated from the active palette, so a theme reaches the
  borders and backgrounds rather than only the text.
- **A table-of-contents detector**, so an archive that carries its own index is
  read rather than guessed at -- 266 named entries on the specimen this was
  built against.
- **A fuzz seed registry** (`tests/seeds.py`). The differential fuzzer covered
  one walker of 52, not by choice: building a valid input was reinvented in 56
  test files. Seven formats now, shallow and deep; an 84,000-walk soak with
  multi-edit mutations found no stray exceptions.

### Fixed

- **A cap we crossed is no longer a defect the file committed.** A walker that
  stopped at an internal limit appended a warning, `anomalies.scan` turned every
  walker warning into a `structure` finding, and findings drive `audit`'s exit
  code -- so a structurally perfect file exited 1 for being large, and a script
  doing `audit f || quarantine f` quarantined it. This was live across fourteen
  walker sites that already announced their caps, which is why the other forty
  were held back rather than added.

  Warnings now carry a kind. `coverage` says our walk stopped early; anything
  else says the file has something to answer for, and a plain string stays a
  defect so nothing silently drops out of the findings a user relies on. The
  kind travels on the warning rather than being read out of its text: matching
  prose would make the wording of a human-readable string load-bearing across a
  module boundary, which is the defect fixed in 1.0.0 where the anomaly checks
  dispatched on a display label.

  Coverage notes are still reported -- as `info`/`coverage`, sorted below every
  real finding. A bounded run that says nothing is the thing this project is
  named for. It just no longer blames the file.

  The mechanism is a `str` subclass, so all 427 existing warning sites and every
  consumer keep working untouched; only the fourteen sites that needed
  classifying were changed. This unblocks the forty deferred cap announcements
  listed in `tests/test_cap_announcements.py`.

### Planned for 1.0.1

- ~~**Walker cap warnings will stop counting as findings.**~~ Done, above.
  Original note kept for the reasoning: A walker that stops at
  an internal limit appends a warning, `anomalies.scan` turns every walker
  warning into a `structure` finding, and findings drive `audit`'s exit code --
  so a structurally perfect file that merely crossed one of our own caps exits
  1, and `audit f || quarantine f` quarantines it. This is recorded here before
  1.0.0 ships because the README already promises the correct behaviour
  ("a bounded run is not a failed one -- it says so on stderr and exits by what
  it actually found"), which makes the change conformance to the published
  contract rather than a surprise reversal.

  It was not done for 1.0.0 because distinguishing a coverage note from a real
  defect needs walker warnings to carry a kind. Doing it by matching the text of
  the message would reintroduce exactly the prose-dispatch bug fixed in this
  release, where the wording of a display string was load-bearing across a
  module boundary. That is a walker-contract change and deserves its own
  release.

  Fourteen further cap sites are held behind the same change, and the cap ledger
  in `tests/test_cap_announcements.py` lists every one so none is forgotten.

- **Ogg songs were cut at 16 MB scan boundaries.** Each segment was analysed
  blind to its neighbours, so a stream crossing an edge was seen twice and the
  partial page between them was claimed by neither. On a 187 MB archive of 64
  songs: 75 regions, 11 of them unplayable halves, and 120,045 bytes of audio
  that no region claimed. Rejoined on shared bitstream serial -- the format's
  own statement of identity, not proximity. Now byte-exact against the archive's
  index.
- **`p` played noise instead of music.** The check asked whether the OPEN FILE
  was decodable; inside an archive it is not, so an Ogg region was reinterpreted
  as raw PCM. It asks about the selected range now, which also means nested
  audio plays at any depth.
- **A chunk states two ranges.** RIFF's `size` is the payload; MP4's counts its
  own header. Nine sites across six modules re-derived the difference in four
  spellings, and 28 of 275 chunks put their payload past the end of the file.
  `core/infra/geometry.py` normalizes extent, payload and provenance at the walk
  boundary, and never repairs what it cannot verify.
- **Generic triage misplaced its own header fields by eight bytes** (`magic`
  reported the signature while pointing at the byte after the size field), and
  **its 4 MB read window went undisclosed** -- a 6 MB container with 12 chunks
  reported "8 chunk(s)" flat.
- **FLAC said nothing about truncation.** A PADDING block claiming 8,192 bytes
  inside a 200-byte file walked in silence, while RIFF has always reported the
  same damage in the file's own numbers.
- **A chunk covering its parent recursed forever**, and once that was fixed the
  top level -- built by a different path -- hung one quiet duplicate of itself.
  An arrow means there is something under a node; it does not mean its bytes may
  be walked.
- **A stale exploration result could delete a live tree node.** The guard
  assumed removing a node from a rebuilt tree raises; it does not, and
  `TreeNode._remove` ends by unregistering whichever live node inherited the
  stale id.
- **Quitting leaked every frame's temp files** -- one carved region per descend.
- **Forcing a walker froze the app for 16.5 seconds** on a large file, with no
  way to quit. It runs on a worker now.
- **Six single letters meant two different things** depending on which screen
  you were on. One was dangerous: `s` was the shape column in the region list
  and STRIP METADATA in the tree.
- **The expand arrows were clipped**: Textual's defaults are East Asian Width
  Ambiguous and one has an emoji presentation, so terminals draw them two cells
  wide into a one-cell slot.
- The forced-parse screen titled itself "None"; the region list forgot the
  cursor on every mark; a nested walker's pointer field was not rebased, so
  following it jumped to the wrong bytes.

## [1.0.0] - 2026-08-10

The 1.0 release. Everything below landed after the 1.0.0b2 beta, and the theme
is the same one that runs through the whole 1.0 cycle: **a tool that reports a
partial answer as a whole one is worse than a tool that declines.** Most of what
follows is either a check learning to say what it did not look at, or a CLI
shape being fixed while it is still free to fix.

### Breaking

- **`probe` takes its operands last: `probe SUBVERB [OPTIONS] FILE...`.** The
  old `probe FILE SUBVERB` could not survive a wildcard -- the shell turns
  `probe *.wav strings` into `probe a.wav b.wav c.wav strings` before acidcat
  sees it, and there was no reading of that which worked. No long-lived tool
  puts an operand between a command and its subcommand. Two things fall out:
  `probe strings --help` now works without naming a file, and multi-file output
  is labelled per file, the grep and file(1) rule, so single-file output still
  pipes unchanged. `probe diff` takes exactly two operands, like diff(1).

- **Per-file report verbs accept many files and directories.** `audit`, `info`
  and `chunks` took exactly one file and no directory, while `inspect` beside
  them took a list and no directory: four arities across verbs that all do the
  same thing. `audit *.wav` was a usage error. The rule now is testable rather
  than aesthetic -- a verb takes `FILE...` when its output is per-file and
  self-labelling, and one file only when it has arguments meaningless across
  files, which is why `od`, `carve` and probe's offset sub-verbs stay singular.

### Added

- **A skill ships with the MCP server, and the README says so.** `skills/acidcat/`
  has been in the repo since before 1.0 and nothing pointed at it, so nobody
  found it -- a skill nobody is told about is a file, not a feature. Its MCP
  half was also a bare list of nineteen tool names, which teaches a model what
  can be called but not the order calls go in. It now carries the cost tiers,
  the preference order (metadata before analysis), the security note on the
  HTTP transport, and the register-then-reindex sequence. A test keeps its
  factual claims tied to the code: every extra, console script, CLI verb and
  MCP tool name it mentions must exist. Three claims were already wrong,
  including an install line naming two extras that have never existed.

- **`discover_libraries` says when a count is a floor.** Its `audio_count` is
  bounded by `max_depth` (default 3) and was reported as the pack's file count.
  A pack nesting one level deeper came back as 520 against a true 657 with
  nothing suggesting the walk had stopped early, so an agent quoted the short
  number as fact. A candidate whose count was cut now carries
  `audio_count_is_a_floor`, the result carries a note, and the CLI marks the
  same counts with `+`. The flag fires only where audio genuinely sits below
  the cap, so a deep folder of artwork does not raise it.

- **The TUI's graph views take a scale and a scope (`S` and `r`).** The entropy
  plot was pinned to its ceiling on nearly every real file: audio sits around
  7.9 of a theoretical 8, so the axis spent 99% of its height on a range no
  audio file occupies and the differences worth seeing were compressed into the
  top two percent. `S` switches the entropy axis to the range actually present,
  and gives the byte histogram linear, log and clipped axes so one dominant bin
  -- 0x00 in any padded bank -- stops flattening the other 255. `r` points any
  graph at the selected chunk instead of the whole file, which is the only way
  to see a 40-byte header that occupies one column of a whole-file plot.

  A rescaled chart looks exactly like an absolute one, so the caption always
  names the axis and the span it chose. That is the point of the feature and
  the risk it carries, in the same sentence.

  A region-scoped graph follows the selection as it moves, so walking the tree
  redraws the chart per chunk. Without that, `r` took a snapshot: the caption
  named a chunk, you moved off it, and the picture stayed -- a stale chart
  under a live caption, with nothing on screen saying the two disagreed.

  With a graph focused, the arrow keys drive it, mapped to the axis they move
  along: up and down are the vertical axis, so they change the scale; left and
  right are the horizontal one, so they move the selection along the file,
  which a region-scoped graph then follows. They are live only while the byte
  pane holds focus and is showing a graph, so the tree keeps its arrows and the
  hex dump keeps its scrolling.

- **Entropy over a small region states the ceiling it cannot pass.** Shannon
  entropy over n bytes cannot exceed log2(n), and scoping a graph made small
  regions reachable for the first time. A 16-byte `fmt` chunk got one window
  per byte, and entropy of a single byte is 0 by definition, so the header read
  `min 0.00 mean 0.00 max 0.00` -- indistinguishable from a region of one
  repeated byte. A 900-byte region at 16 bytes per window tops out at 4.0 bits,
  so uniformly random data reported `max 4.09` against an axis labelled 0-8 and
  read as structured. Neither was a wrong calculation; both were correct
  numbers printed without the one fact that makes them interpretable. A region
  too small to plot says so instead of drawing a flat line at zero, and a
  coarse one names its ceiling and whether the region or the pane set it.

- **Colour carries magnitude in the byte views.** Bars were drawn from the
  eight-stop brand ramp, or in the histogram's case one flat colour, so the
  hue said nothing a length was not already saying. The ramp is interpolated
  now and tracks the drawn height.

- **A ledger of every bound in the tree, and a conservation law over directory
  verbs.** Nine instances of one defect were fixed by hand this cycle: a cap
  applied, and the shortened result presented as the whole answer. These are the
  mechanism for the tenth. Every module-level constant whose name claims it
  bounds something must sit in exactly one bucket -- swept by a test, exempt
  with a reason from a closed set, or recorded as debt that may only shrink --
  so a new cap cannot be added without someone deciding which. Separately, every
  file handed to a directory verb must land in a named bucket and be counted:
  `validate` is asserted to account for all of them, and the twelve verbs that
  do not yet are listed rather than forgotten. The ledger found two constants on
  its first run that had been missed while building its own list by hand.

- **`validate --deep` verifies the checksums a format carries about itself.**
  FLAC frame CRC-8 and CRC-16, and MP3 frame validity. Neither needs a decoder.
  A failure is proof rather than inference. acidcat previously parsed FLAC's
  STREAMINFO MD5, displayed it, and never checked it -- `repair` called a file
  "already consistent" that ffmpeg refuses to decode. Off by default because it
  costs a full read. Worth recording: ffmpeg does not verify FLAC frame CRCs by
  default either, so this class of damage passes quietly through the most
  obvious tool for the job -- and `-err_detect crccheck`, the flag documented
  for it, does not reliably catch it. Measured across the CI matrix: ffmpeg
  exits 0 on a FLAC whose frame CRCs do not match, with the flag and without,
  on every build tested.

- **`audit --signal` reports CD ripper concealment.** Where a rip wrote silence,
  a held sample, an interpolation or a repeated block over a sector it could not
  read. This speaks to a file's ORIGIN rather than its damage: CD players
  conceal errors in the playback path by design and say nothing, while the drives
  used for ripping generally do not, so concealment reaching a file is evidence
  it came off a disc that would not read cleanly. Measured against real material
  at 0.0 to 0.4 percent false positives for the three structural strategies.
  Raw uncorrected data is deliberately NOT claimed: measured over one CD sector,
  real audio and random bytes overlap by 40 percent on the obvious statistic, so
  detecting it would mean a false positive on roughly six files in ten.

### Fixed

- **`register_library` and `discover_libraries` said nothing about the step
  that fills a library.** Both create the registry row and stop; `reindex`
  walks the files. `register_library` documented that, `discover_libraries`
  never mentioned it, and `reindex` said "only call when the user explicitly
  asks to refresh" -- which told a model not to finish the job it had just
  started. An agent following those descriptions registered four libraries,
  reported success, and left four empty shells. The batch tool now says it does
  not populate and names the step that does, and `reindex` distinguishes
  completing a registration the user asked for from re-walking a library that
  is already full.

- **A byte histogram could omit its tallest bar.** The braille plotter
  point-sampled its input, so drawing 256 bins across a 69-column pane looked
  at 138 of them and never read the other 118. A distribution spiking at one of
  the unread values -- a fill byte, a single-byte XOR key -- rendered as a flat
  chart with nothing to indicate a bar had been skipped, in the view whose
  whole job is showing the outlier. Columns now report the maximum over the
  range they cover, so nothing that would have been visible disappears.

- **The forensics panel could not be read without a mouse.** `#idbox` is six
  rows holding a legend plus one line per finding, and `tab` cycled only the
  tree and the hex dump, so on a file with more than about four findings the
  rest were unreachable from the keyboard -- over ssh, which is where this gets
  used, that is the rest of the list. It joined the tab cycle, and `f` now
  scrolls the panel so the finding it just jumped to is on screen rather than
  marking one nobody can see.

- **A library's schema version came from a cached copy, and a migration that
  rebuilt the index said nothing while it ran.** The cached number was only
  rewritten by a full indexing run, so a library migrated by being *opened* --
  what a query or an MCP call does -- reported its old version forever. With
  the migration itself silent, the visible result was a client that paused and
  a registry insisting nothing had happened, which is a bug report for a
  deadlock that does not exist. `index --refresh-stats`, the command whose job
  is repairing exactly this, carried a drifted duplicate of the read that never
  touched the field; it delegates now.

- **Four places a cap, or a first answer, was reported as the answer.** The TUI
  byte search stopped at an undisclosed 4,096 and printed that as the match
  count, so 100,000 matches read as 4,096 with the rest of the file unreachable
  and unmentioned. The pending-changes screen -- the one consulted before a save
  -- stopped its scan at 201 so it could print ".. 1 more regions", making 201
  the largest number it could ever report: 1,000 changed regions rendered as
  201. Ableton's `derived_tempo` returned the first warp span, so a clip warped
  120 / 60 / 200 reported 120 with nothing saying the tempo moved, in a format
  whose warp markers exist precisely to encode tempo that moves. And `m` in the
  TUI did nothing and said nothing on an unrecognised file, which is
  indistinguishable from a broken build on exactly the files the tool is for.

- **Anomaly checks dispatched on the display label, so renaming a string could
  turn a check off.** Seven checks branched on the walker's human-readable
  label -- `fmt_label.startswith("Ogg")`, `"AIFF" in fmt_label` -- which made
  the wording of a presentation string load-bearing across a module boundary.
  Retitling `RIFF/WAVE` would have silently disabled two forensic checks, with
  nothing at the definition site to warn anyone and no test to catch it.
  Dispatch is on the sniff id now. One latent bug fell out of the conversion: a
  guard that suppresses the spurious "embedded Ogg" finding on ordinary Ogg
  files also required the label to be non-empty, so a walker returning `""`
  would have had the guard fail open.

- **Directory walks skipped supported formats, silently.** Eight commands each
  had their own `os.walk` and their own extension list, so one directory holding
  a FLAC, an MP3, an AIFF and a WAV was seen as 4, 3, 1 or 0 files depending
  which verb you asked, and none of them said so. `detect` finds BPM and key,
  matched `.wav` only, and reported nothing at all for a library of FLACs while
  `detect a.flac` on the same file worked. There is one shared expander now: a
  named file is never filtered, and a directory walk reports what it passed over.

- **`od` on a directory produced a raw traceback** -- the only uncaught one
  across 21 verbs and four kinds of bad input.

- **`-` reads stdin on every verb that takes a file.** `shape`, `audit` and
  `validate` had no stdin handling at all.

- **The TUI's hex row lost two columns to a scrollbar it did not reserve.** The
  width was measured from the pane minus its border, ignoring padding and the
  scrollbar -- and because scrollbar presence depends on content height, which
  depends on the width being chosen, the measurement fed back into itself and
  landed one layout behind. That is why the wrapping looked intermittent and why
  moving to a shorter field and back appeared to fix it.

- **`tab` in the TUI handed focus to a pane the zoom had hidden**, so the arrow
  keys drove a cursor nobody could see and the hex view jumped to a field the
  user had not chosen. From outside that reads as "I cannot change fields",
  which is the opposite of what was happening.

- **The TUI's multi-pane view stated the same facts up to three times** on one
  line -- offset, size, then both again -- putting a 110-character line into a
  66-column pane. The two columns are symmetric now, and the byte views redraw
  when the pane changes size instead of keeping their old dimensions until you
  cycled away and back.

- **A test wrote into the repository root** on every run, and asserted nothing.

### Internal

- CI went red on all five platforms from a guard that could never fire:
  `subprocess.run` raises `FileNotFoundError` when a binary is absent rather
  than returning non-zero, so `if result.returncode: skip()` never reached the
  skip. There is one `have_tool()` helper now, verified by running the whole
  suite with ffmpeg removed from PATH.


## [1.0.0b2] - 2026-08-08

A hardening pass ahead of the 1.0 release candidate. Almost every entry below
has one shape: **work was skipped and the result was reported as complete.** A
cap, a swallowed exception or a filter would drop part of the job, and the
summary line counted what it had looked at rather than what it had been asked
to look at -- so a partial answer was indistinguishable from a whole one. Six of
these were introduced by the 1.0 restructure itself.

### Breaking
- **Python 3.9 is no longer supported.** The floor is 3.10. The code already
  used 3.10 syntax; the metadata claimed 3.8+, so `pip` installed a package that
  could not import on the versions it advertised. CI now runs the versions the
  package actually claims.
- **Exit codes are now one convention across every verb**, following `grep` and
  `diff`: `0` it worked, `1` it ran fine and the answer is no, `2` it could not
  run. Sixteen verbs disagreed, and the disagreements were not cosmetic:
  `locate` exited 0 having found nothing, so `locate --json | carve --batch -`
  on a blob with no audio in it succeeded end to end and a recovery script
  carried on with an empty output directory; `validate` exited 0 for files it
  never modelled, giving a clean bill of health to anything it did not
  understand, on the same byte where `audit` had findings; `audit` always
  exited 0, so the forensic verb could not gate anything; `repair --dry-run`
  exited 0 over a list of pending repairs; a missing file was 1 in eleven verbs
  and 2 in three; and `carve --chunk ZZZZ` was 2 (you typed it wrong) where
  `dump FILE ZZZZ` was 1 (it is not in this file) for the identical question.
  The convention is documented in the README and pinned by
  `tests/test_exit_codes.py`, parametrized over every verb that takes a path --
  the previous version of that test asserted `in (1, 2)` across three verbs,
  which encoded the disagreement rather than catching it. **If you script
  acidcat, check any `&&` / `||` chain and any `$?` branch.**

### Fixed
- **Bitwig wavetables: the `.wt` header word at offset 10 is a flags field, not
  a data offset.** Read as an offset, 151 of 152 real wavetables were reported
  corrupt. The flags say whether the payload is int16 or float32, whether the
  file is a one-shot sample, and whether `<wtmeta>` XML follows. All 152 walk
  clean. The synthetic test specimen had encoded the same misunderstanding,
  which is why nothing caught it.
- **`audit` trusted a declared PCM size past the end of the file**, reading
  beyond the buffer instead of clamping to the bytes actually present.
- **The TUI loudness guard never fired on compressed files**, and its prompt
  could not be answered from the keyboard.
- **`repair` could destroy audio, and exited 0.** On a WAV whose container size
  had been truncated, the repairer rewrote the file to the size the header
  declared -- orphaning 882,000 bytes of perfectly readable samples and
  reporting success. `repair` now refuses to write when a fix would strand audio
  data past the container, and says which bytes it would have lost.
- **`carve -o` could destroy its own input.** Carving a region back over the
  source path truncated the file to the region (2,044 bytes in, 4 bytes out).
  The output path is now checked against the input, including through symlinks
  and case-insensitive filesystems.
- **`locate` could not find 16-bit PCM** -- the headline capability. The
  statistical detector scored bytes only as 8-bit samples, so 16-bit little- and
  big-endian raw audio, the two most common cases in a card dump, read as noise.
  It now folds three decimated views (8-bit, 16-bit LE, 16-bit BE) per block and
  keeps the best-scoring one.
- **`locate` ranked silence above real audio.** The confidence score rewarded
  low variance, so a run of zeros beat a drum loop. Digital silence is now
  rejected outright rather than promoted.
- **The MCP server was dead on every fresh install.** `mcp` 2.0 removed the
  low-level `Server.list_tools` / `call_tool` decorators this server is built
  on, and the dependency was unpinned -- so `pip install acidcat[mcp]` resolved
  2.0 and `acidcat-mcp` died on startup with an `AttributeError`. Pinned to
  `mcp>=1.0,<2`. Supporting the 2.x API is a port, not a version bump.
- **MCP DNS-rebinding protection was off.** The SDK treats an omitted
  `TransportSecuritySettings` as opt-*out*, so the HTTP transport accepted any
  `Host`/`Origin` -- a page in the user's browser could drive the server. It is
  now on by default, with the loopback origins allowed and a warning when bound
  to a non-loopback address. Result limits are clamped so a client cannot ask
  for an unbounded row count.
- **`census` silently dropped 1.65% of files on Windows.** `os.open` without
  `O_BINARY` opens in text mode, where `os.read` stops at the first `0x1A`
  byte -- so any file with a `^Z` in its first block was truncated mid-scan and
  counted as clean.
- **`detect` invented tempos and keys.** A 0.4-second snare reported 304 BPM.
  Tempo is now range-checked before it is reported, key detection gates on the
  margin between the best and second-best profile rather than on raw
  correlation, and `bandwidth.py`'s documented accuracy figures were corrected
  (they had been tuned against white noise, which is not representative:
  measured recall is 47%).
- **`audit --signal` crashed on a WAV declaring a sample rate of 0.** The signal
  analyzers ran outside the guard that protected the decode, so a division by
  zero took the whole verb down with a traceback. A check that cannot run is now
  reported as "NOT screened", not omitted.
- **`od` was unbounded** -- a 285 MB input produced a 1.4 GB dump over five and
  a half minutes with no way to stop it.
- **`audioscan` peaked at 46x the input size** (32 MB in, 1.4 GB resident). The
  vectorized path materialized whole-buffer slices per view and retained a dict
  per block; it now decimates per block and accumulates running sums.
- **`query` reported different errors on different machines.** `--bpm zzz`
  checked the registry before it checked the argument, so the same bad command
  exited 1 ("no libraries") on a fresh machine and 2 ("bad value") on a
  populated one. Arguments are validated before any state is consulted.
- **Empty results are now valid machine output.** `query --json` with no matches
  emitted zero bytes, so `jq` and `json.loads` both failed on an ordinary empty
  answer.
- **`scan --json` and `features DIR --json` did nothing.** Both registered
  `--output-format` and then ignored it, always writing a CSV file -- so the
  flag was accepted, CSV came out, and neither command could be the left side of
  a pipe. The default (a CSV file) is unchanged.
- Malformed input produces a message and a usable exit code rather than a
  traceback, across the verbs where hostile bytes reached an unguarded parse.
- **`audit` died with a TypeError on any file triggering a "check_failed"
  anomaly.** Four rules deliberately report `offset: None` -- they are the ones
  whose job is to say "this rule could not run, the file was NOT screened" --
  and the sort ordered on offset directly, so the moment such a finding shared a
  severity with a positioned one, Python compared None against an int. A 3-byte
  `ID3` was the smallest trigger; any ordinary corrupt file that crashes one
  rule while another warns hit it.
- **`extract` raised IndexError on an Impulse Tracker file of 48-51 bytes.**
  `parse_it` was the one tracker parser without an upfront length guard: the
  fields at 32..47 use `unpack_from` (the clean short-read signal every caller
  handles) but gvol/mvol/speed/tempo at 48..51 were bare indices.
- **`write -o` pointed at its own input edited in place and made no backup** --
  that path is not a copy, and it took the branch that skips the `_original`, so
  the one guaranteed-recoverable path was lost exactly when a script templates
  output == input.
- **MCP `reindex_features` wrote feature vectors `find_similar` could never
  read**, tagging them version 1 (pre-vector) while the search filters on the
  current version. It reported `{"processed": N, "failed": 0}` and the library
  then returned a population of 0 -- and could not repair itself, because the
  "remaining" count keyed on rows being absent rather than stale.
- **The tag filter was the one case-SENSITIVE filter** in the shared query
  builder, so `--tag wavetable` returned nothing against 85 rows stored as
  `Wavetable`. The same builder backs the MCP search tool.
- **`locate` ranked a statistical guess above a signature-validated container.**
  A real WAV whose magic had been checked came back at confidence 0.900 and a
  headerless region inferred from autocorrelation came back at 1.000, so
  `--min-confidence 0.90` filtered out the containers and kept the guesses. On a
  compressed proprietary container (byte entropy 7.8, which acidcat's own
  `probe entropy` calls "encrypted or compressed") that meant four megabytes of
  noise reported as raw PCM at confidence 1.00 with no threshold able to reject
  it. Blobs now occupy `[0, 0.89]` and only a checked magic number reaches 0.90.
  Rescaled rather than clipped, and the `normal`-mode detection gate moved onto
  the new scale with it, so what `locate` *finds* is unchanged.
- **JSON records could not locate their own bytes.** `dump --json` reported
  `offset` for the 8-byte chunk header while `size` and `hex` described the
  payload, so feeding a record into `carve --offset` read 8 bytes early and
  returned the ASCII chunk id; `inspect --json` had the same skew between
  `chunk.offset` and `field.off`. Format-dependent, so a script tuned on
  trackers (no header, no skew) broke silently on RIFF. Chunks now carry
  `payload_base` / `payload_offset` and fields carry `abs`, which `--full` has
  always emitted.
- **`inspect --force` and `--resync` emitted no JSON at all** under `--json` --
  the human table verbatim, so `jq` failed on the two verbs you reach for when
  no walker claims a file.
- **`extract` counted samples it recovered nothing from.** A MOD declaring a
  4,096-byte sample starting at EOF was listed at its declared size, counted,
  and written as a 44-byte WAV header with no audio.
- **`shape` answered with silence for files it cannot walk**, so
  `shape mystery.ch1` printed nothing and a sweep of one unknown format gave an
  empty histogram. A file you *name* now gets a row; directory recursion still
  filters, so a tree sweep does not sprout a row per README.
- **`census --limit` made whole-corpus claims from a prefix.** `--json` carried
  no truncation marker, and the "rare chunks" section reported a chunk occurring
  1,178 times as rare because only 20 files had been opened.
- `extract` leaked the temp path it buffers stdin into, naming a file the user
  never asked about.

### Added
- **The Ableton sidecar family is walked.** `.asd` analysis files, the Live Set
  family, Max for Live devices and `.agr` grooves. The object tree's type
  dictionary is decoded rather than guessed at, so typed values come out as
  values; warp markers are decoded and the tempo derived from them, since `.asd`
  does not store a tempo field. A sidecar that no longer describes its audio is
  reported as such. The overview bin size is read from the file (128) rather
  than inferred (64), and the "schema generations" the format appeared to have
  turned out to be optional sections.
- **Visualisation over a whole file, not a prefix.** `viz` gained a streamed
  file-wide entropy pass and a 64x64 Hilbert map covering the entire file at a
  flat read cost. Both say when they sampled; neither captions a cap as a fact.
- **Two more anomaly rules, and fixtures that fire every one of them.** Two of
  the thirteen shipped rules were referenced by no test at all -- a detector
  nobody has made fire is a claim, not a check. Sixteen rules now, none untested.
- **`probe.annotate()` and `od --marks`.** Per-byte tags (`class:*`, `mark:size`,
  `mark:table`) for the hex path with no walker behind it -- `--offset`, a carved
  region, an unknown header. Tags are strings, never colours, so the terminal,
  the TUI and the HTML explorer each choose their own styling. The overlay is
  bounded to 256 KB and its summary line says which bytes it actually scanned.
- **A TUI you can drive.** A pane can take the whole screen (`z`), the hex row
  width follows the pane instead of folding below ~154 columns, `tab` moves
  focus, and the layout gives the hex view half the screen.
- **One rendering rule across every verb.** There were six different
  output-format sets: seven verbs offered `table/json/csv` but not `tsv`, two
  offered `tsv` but not `csv`, three declared a private `--json` boolean that
  bypassed the shared registry entirely (so `--output-format json` -- which
  worked on 26 other verbs -- was an *error* on the forensic verb, the recovery
  verb and the RE verb), and twelve had no machine-readable output at all. The
  rule now: a verb whose output is flat records offers **table, json, csv and
  tsv**; a verb whose output is nested (`inspect`'s chunk tree, `census`'s
  histograms, `dump`'s native hex) offers table and json, because csv has no
  honest representation for a tree. Three invariant tests read each verb's
  declared choices straight off its parser and assert the rule holds.
- **`shape`, `validate`, `repair` and `write` gained machine output.** `shape`
  *is* the data verb -- its whole output is records built for `sort | uniq -c` --
  and TSV was hardcoded with no route to a JSON consumer. `validate` is the
  CI-gate verb: you could branch on its exit code but not read *which* file
  failed or *why* without scraping the human table. `repair` and `write` change
  your files and could not report what they changed or where the backup went;
  `write --dry-run --json` is now a usable preview rather than prose. Defaults
  are unchanged throughout -- `shape` stays TSV and stays headerless, because
  `sort | uniq -c` would count a header as data.
- **`acidcat probe FILE table AT`** -- walk a discovered offset table into
  carve-ready regions. This is the gap between acidcat-as-hex-viewer and
  acidcat-as-RE-workbench: an audit reverse-engineered a real proprietary
  container in about eight acidcat commands (`od`, `probe read`,
  `probe entropy`, cross-specimen `carve --encoding hex`) and then stopped,
  because nothing let it express what it had learned. `--struct` decodes one
  fixed record and cannot take a count from a field it just read, so carving the
  375 frames meant leaving the tool and computing offsets in Python.
  `--count-at` reads the entry count out of the file, `--base after-table`
  handles the common layout where entries are relative to the byte just past the
  table, and the records come out in `locate` shape so `--json` pipes straight
  into `carve --batch -` with no jq in between. A count read from the file is
  hostile by definition, so it is bounded by what the file can hold and the
  report says when it was clamped rather than quoting the file's claim back.
- **`probe --json`** on `read`, `scan`, `find`, `strings`, `diff` and `entropy`.
  probe is the verb you live in while reverse-engineering an unknown format and
  it had no machine output at all, so scripting `probe find` meant
  `tail -n +2 | tr -d ' '`. The human summary lines also moved to stderr, so the
  plain output pipes cleanly too.
- **`acidcat wrap`** -- a filter that puts a WAV header on raw PCM read from
  stdin (`--rate`, `--channels`, `--bits`, `--endian`, `--float`). This closes
  the recovery chain: a region `locate` finds and `carve` cuts is now playable
  without a detour through Python or sox.
- **`carve --wrap`** does the same in bulk, so `locate --json | carve --batch -`
  produces playable WAVs directly instead of headerless blobs.
- **`acidcat classify`** -- a triage verdict for a file before committing to a
  walker: single format, container, damaged, or not audio. It also names the
  formats acidcat can identify but does not walk, rather than calling them
  unknown.
- **`inspect --resync`** rebuilds chunk structure from a damaged container by
  scanning for plausible `[id][size]` records and keeping the ones that chain
  end-to-start, showing what a corrupt size field costs the normal walk.
- **`inspect --format`, `--region` and `--force`** -- parse as a named format
  regardless of the magic bytes; walk the Nth region `locate` reported inside a
  larger blob; on a file no walker claims, try every walker and report what each
  made of it (leads, not identifications).
- **`acidcat od`** dumps any bytes, not just a chunk, with `--offset` / `--at` /
  `--region` to scope it.
- **Lossy-transcode detection without librosa.** `core/analysis/bandwidth.py`
  identifies a WAV that is really a decoded MP3 from the steepness of the
  spectral edge rather than from a cutoff frequency, using numpy only.
- **`python -m acidcat.mcp_server`** as an entry point alongside the
  `acidcat-mcp` console script.

### Performance
- **`scripts/bench.py` -- a committed performance baseline.** One tracked number
  per verb, driven through the real dispatcher, so a regression is visible
  instead of felt. Runs with no arguments on a bare clone (it generates the
  suite's synthetic corpus, so two machines can compare) or `--corpus DIR` for
  real-world figures; `--json` writes a baseline and `--compare` diffs against
  one. Reports cold and warm, min/median/max rather than best-of, and ms/file
  alongside MB/s -- the walkers cap their reads, so their MB/s flatters them.
  Interpreter startup is measured separately, being a per-invocation constant.
- `audioscan` 22.9 s -> 1.4 s (16x) on large blobs: a vectorized feature path,
  then batching to fix the memory blowup it introduced.
- `framescan` 0.8 -> 67 MB/s (79x) on adversarial sync-byte runs.
- `locate` 1.6x on the statistical scan, byte-identical output.

### Internal
- **A fresh clone can now reproduce the test results.** The suite depended on
  gitignored corpora with no generation script anywhere, so sixteen test files
  and the entire TUI suite skipped on CI while passing locally.
  `tests/make_corpus.py` generates 23 synthetic specimens (15 KB, license-clean,
  deterministic) from nothing.
- **`scripts/preflight.py` reproduces CI locally** by hiding the gitignored
  corpora and clearing `ACIDCAT_*` before running pytest. Three of seven pushes
  had gone red from machine-only dependencies; the first preflight predicted
  1,521 passed / 78 skipped against CI's 1,526 / 73.
- **The test suite no longer writes into the real home directory.** `conftest`
  now points `HOME`/`USERPROFILE` at a temp path instead of deleting the
  variables, so a test run cannot touch a user's registry.
- **`publish.yml` runs the tests before publishing.** It previously built,
  ran `twine check`, and uploaded to PyPI without executing the suite at all.
- CI gates `develop` as well as `main`, and pytest is configured in
  `pyproject.toml` (`-rs`, so skips explain themselves) rather than depending on
  the caller's flags.

### Documentation
- `README`, `CHEATSHEET.md` and `ARCHITECTURE.md` were re-derived from the
  build. Between them they documented a `-f/--format` output flag that now
  errors, omitted seven registered verbs, gave `audit`'s section count three
  different ways, and reported 24 verbs / 48 modules against an actual 29 / 193.
  `inspect`'s own docstring named 13 formats as though they were the whole set;
  it now points at `acidcat formats`, which asks the binary.

## [1.0.0b1] - 2026-07-31

The 1.0 beta. Nothing here changes what acidcat can do -- the verb list, the
walker registry, the extractable formats, and the public API are identical to
0.90.0, verified by diffing the live registries rather than by inspection. What
changed is the shape: a `core/` that had grown to 75 loose modules is now eleven
packages whose order is the dependency direction, which is what makes the
install tiers real rather than a promise in the README. The CLI vocabulary was
tightened so a flag name tells you what it does, and the interactive surface --
previously the least-tested part of the tree -- is now fully covered.

Breaking, with shims: `--format json` is an error (use `--json`); `--format` now
selects a *file* format. `carve --format` became `carve --encoding`. The old
spellings still work and warn.

### Changed
- **One word, one meaning across the CLI.** `--format` was doing three unrelated jobs -- picking output rendering, filtering by file type, and choosing a byte encoding -- so a flag's name no longer told you what it did. Each axis now owns a word, matching how every tool that handles both file formats and output formats disambiguates them (ffprobe `-f` vs `-of`, exiftool "file type" vs `-json`, tshark dissector vs `-T`): **format** = the file's container/codec, **output** (`-o`) = where bytes go, **output-format** = how records render, **encoding** = how carved bytes are serialized. Everyday rendering is `--json` / `--csv`; `--output-format` takes the full list. The old `-f json` spelling still works and warns, but `--format json` is now an error -- `--format` selects a file format. `carve --format` became `carve --encoding` (old flag kept, hidden); `formats --format-out` became `--output-format` (ditto).
- **`detect` reports failure when it could not detect.** A missing analysis stack that the filename could not cover now exits 1 instead of printing all-nulls with exit 0, so `acidcat detect f && ...` cannot proceed on an empty answer. With librosa present, an undetectable file remains a legitimate exit 0.
- **`validate` follows the grep/diff exit-code family end to end**: 0 = every checked file is consistent, 1 = a file has a violation (ran fine), 2 = a named input could not be accessed. Previously a missing named file was swallowed as 0, breaking `&&` chains. A missing file *inside* a walked directory is still a skip, not an error.
- **`--color=auto|always|never` everywhere it applies.** `probe`'s odd `--no-color` boolean is now `--color` (old flag kept as a deprecated alias), and `inspect`/`od` share one implementation. `probe map` lost its `-o` short form for `--order`, since `-o` means "output file" in seventeen other places.

### Added
- **Pipe a file into the byte-analysis commands.** `chunks`, `dump`, `od`, `detect`, and `probe` now accept `-` (or piped stdin), joining `info`, `carve`, and `locate`: `cat track.wav | acidcat chunks -`. stdin is buffered so the byte-level parsers can still seek, and it is reported as `<stdin>` rather than a temp path.
- **Output formats are a registry.** `render.register_format(name, fn)` adds a rendering to every command's `--output-format` at once, rather than editing fourteen call sites. `tsv` is registered alongside table/json/csv; csv and tsv now emit `\n` instead of `\r\n`, so they pipe cleanly.
- **BPM/key from a filename now works without librosa.** That parsing is pure-Python regex but sat behind the librosa import gate, so a base install lost a zero-dependency capability. `tests/test_lean_install.py` pins the tier boundary: a static invariant that no `core/` module imports an optional stack at module level, plus checks that the core verbs run with those stacks blocked and a gated verb prints `pip install acidcat[analysis]` instead of a traceback.

### Fixed
- `detect --json` leaked the internal temp-file path into its `filename` field when reading from stdin.
- `od` ignored the `NO_COLOR` environment variable (it checked only whether stdout was a TTY).
- The TUI's metadata-save path referenced an unimported name -- a latent crash on the edit screen, surfaced by a static undefined-name sweep while splitting the module.

### Internal
- **The flat `core/` package is now organized by concern**, from 76 top-level modules to 3: `codecs/`, `containers/`, `formats/`, `walk/`, `forensics/`, `analysis/`, `catalogue/`, `write/`, `extract/`, `infra/`, `primitives/`. Duplicated primitives (entropy, PCM coherence, ADPCM sample math, stereo interleave, WAV emission, zip offsets) were extracted and shared. `mcp_server.py` (1623 lines) and `tui_app.py` (2732) were split into packages along their real seams. `infra/formats.py` became `infra/render.py`, ending a three-way name collision. Behaviour-preserving throughout: the public API is unchanged and the suite stayed green at every step.

## [0.90.0] - 2026-07-30

### Changed
- **Audio features are now analyzed at a fixed 22050 Hz.** Feature extraction previously ran at each file's native rate, which (a) left high-rate files slow and (b) made vectors from different sample rates incomparable -- a 96kHz file's spectral centroid is computed over a different frequency range than a 44.1kHz file's, so their cosine was meaningless. Resampling every file to the MIR-standard 22050 Hz gives a **uniform, comparable feature space** and a speed win (~1.6x on 44.1kHz, ~3.3x on 96kHz). The reported `sample_rate` still shows the file's true native rate.
- **Feature vectors are versioned properly now.** `find_similar` only compares vectors of the current `FEATURE_SET_VERSION` (bumped to 3), so an old vector is invisible to similarity rather than silently mixed into a different feature space, and a stale reference is re-extracted live. `index --features` auto-refreshes stale/missing features on unchanged files (no `--force` needed).

### Upgrading
- **Re-run `acidcat index <DIR> --features` on any library you use for similarity search** to re-derive vectors at the new analysis rate. Until you do, `find_similar` treats the old vectors as stale and returns nothing for that library. Re-featuring is much faster now (see 0.88.0/0.89.0).

## [0.89.0] - 2026-07-30

### Changed
- **Feature extraction during indexing now runs in parallel.** `acidcat index --features` extracts librosa features across a process pool instead of one file at a time -- the CPU-bound work is embarrassingly parallel across files, while the SQLite writes stay on the single main-process connection. New `--jobs/-j` flag (default: CPU count - 1; `1` = serial). Workers are pinned to single-threaded BLAS so more workers don't oversubscribe the cores. Measured ~3.9x on 12 workers over a 104-file pack (small sets are capped by each worker's one-time librosa cold start; large libraries amortize it and scale toward the core count). Combined with 0.88.0, a library that took ~1.5s/file now indexes at a small fraction of that.

## [0.88.0] - 2026-07-30

### Changed
- **Feature extraction is ~3x faster, with identical feature vectors.** `features.extract_audio_features` spent ~75% of its time in `librosa.beat.beat_track`'s dynamic-programming beat search, but only the tempo goes in the similarity vector -- so it now calls the tempo estimator directly (bit-identical tempo) and estimates the display-only `beat_count` from tempo x duration. It also computes one magnitude STFT and shares it across the spectral-centroid/rolloff/bandwidth/contrast features instead of recomputing four identical transforms. The similarity vectors are unchanged to storage precision, so existing indexes stay valid (no re-featuring needed). ~1.5s/file -> ~0.5s/file.

## [0.87.0] - 2026-07-29

### Added
- **Audio key detection (Krumhansl-Schmuckler).** acidcat can now hear a key from the audio, not just parse it from filenames. `detect.estimate_key_ks` correlates a track's 12-bin pitch-class distribution (librosa chroma) against the Krumhansl-Kessler major and minor key profiles across all 24 keys and names the winner -- so unlike chroma argmax it identifies the *mode* (major vs minor). Wired into the librosa `--deep` path (`estimate_librosa_metadata`), filling in a key for unlabeled tonal material; filename keys stay authoritative when present. Pure-stdlib core (correlation over 12 bins), so the key finder itself needs no numpy.
- Results are confidence-gated (`KEY_CONF_MIN`, default 0.75): below the threshold acidcat emits no key rather than a confident-sounding wrong one. Strongest on harmonically-rich content (pads, chords, melodic loops); **sparse bass-dominant or percussive loops are unreliable** (sub-bass/octave artifacts smear the chromagram) and are usually gated out. Future refinement: better-tuned profiles (Temperley / Albrecht-Shanahan) and harmonic-percussive separation.

## [0.86.0] - 2026-07-29

### Fixed
- **The analysis MCP tools now work over stdio.** `find_similar`, `detect_bpm_key`, and `analyze_sample` hung or crashed the connection when called through the stdio server -- the interface an LLM actually uses. Three causes, all fixed: (1) numpy's first *import* deadlocks the asyncio stdio loop on Windows, so the analysis stack is now pre-warmed (numpy + a librosa JIT warm-up) before the loop starts; (2) tool dispatch runs off the event-loop thread so blocking librosa work can't stall the pipe; (3) analysis tools returned `numpy.float32` values the response serializer could not encode, crashing the whole connection -- results are now coerced to plain Python types. Trade-off: stdio server startup is a few seconds slower (warming librosa) when the `[analysis]` extra is installed.
- **`search_samples` (and every tool) now tolerate `null` for optional arguments.** LLM clients routinely send null for unused optionals; the framework's schema validation rejected them before the handler ran. Optional params are now declared nullable centrally. Found by dogfooding the MCP catalogue.

## [0.85.0] - 2026-07-29

### Fixed
- **Key detection now reads the mode from a quality-before-letter name.** Many sample packs name chords `<quality>_<letter>` (e.g. `Pad_Access_min_C` = C minor, `Pad_Zenith_Maj7_C` = C major), which the letter-first filename patterns missed, so the bare-token fallback stored a bare major letter -- cataloguing C-minor content as C major and giving **wrong harmonic-compatibility results**. `parse_key_from_path` now reads min/maj (plus chord extensions like `min7`/`minadd9`) from a chord-quality token sitting beside the key letter, while still rejecting ordinary words that merely start with min/maj (`Minimal`, `Magic`). Found by dogfooding the MCP catalogue on a real chord library.

## [0.84.0] - 2026-07-29

### Changed
- **The audio-container format table is now defined once** in `core/sniff.py` (`AUDIO_CONTAINERS`: id -> magic + file extension). `carve` (naming a carved region) and `locate` (which magics to sweep, which sniffed ids to accept) both derive from it instead of each keeping their own copy, so the two can no longer drift apart. Behavior is unchanged; a test pins the wiring.

## [0.83.0] - 2026-07-29

### Added
- **`acidcat formats [FMT] [-f table|json|tsv]`** prints the capability matrix -- inspect / extract / convert / repair support per format -- the single answer to "what does acidcat do with format X", which was previously spread across separate dispatch tables.

### Changed
- **Format-dispatch is now guarded.** acidcat keys several independent tables (walkers, sample extractors, convert, repair) on the format-id strings `core/sniff.py` returns, with nothing checking a table key is a real id (a typo failed as a silent dict-miss). New `sniff.KNOWN_FORMATS` declares the id namespace as data, and tests assert every dispatch-table key is a known id, that `KNOWN_FORMATS` matches the ids sniff actually returns, and that the convert/repair capability sets match the live dispatch (which caught the repair set under-listing FLAC and the IFF family).

## [0.82.0] - 2026-07-29

### Added
- **`acidcat extract <rom.sfc>`** recovers SNES BRR samples from a ROM without parsing the game's sample table. SNES BRR carries no codebook (its four filters are fixed second-order predictors), so `core/snesrip.py` walks the ROM for end-flag-terminated runs of valid 9-byte blocks and keeps the ones that decode to loud, coherent audio -- loud measured by RMS (sustained energy), not just peak, so a lone spike over near-silence is not mistaken for a sample. This is the container-agnostic approach used for N64, minus the codebook-pairing step. New `core/brr.py` is the S-DSP BRR decoder (15-bit clip domain, standard integer filter coefficients). ROMs are detected by their internal cartridge-header checksum/complement (LoROM/HiROM, copier-header aware). Verified on Chrono Trigger, Super Mario RPG, and Mega Man X.


## [0.81.0] - 2026-07-28

### Added

- **N64 audio recovery (container-agnostic).** `acidcat extract <rom.z64>` now
  rips VADPCM samples out of an N64 ROM without parsing the game's bank format.
  Each N64 game wraps VADPCM in its own container (classic ALBank, SM64
  ALSeqFile, Zelda AudioTable, Camelot PtrTables, and more), so instead of a
  per-game parser, new `core/n64rip.py` finds the codebooks + audiotable by
  structure and pairs them by decoding to loud-and-coherent audio (peak AND
  autocorrelation -- autocorrelation alone is fooled by silence). Byte order is
  normalized off the ROM magic (z64/n64/v64). This is "PhotoRec for audio"
  applied to a ROM: it rescues the samples, not the instrument tree. Verified
  across different container formats (Wave Race, GoldenEye). Builds on the
  `core/vadpcm.py` codec.

## [0.80.0] - 2026-07-27

### Added

- **N64 VADPCM + audio bank.** New `core/vadpcm.py` decodes Nintendo 64 vector
  ADPCM -- the RSP codec: 9-byte frames, a per-sample codebook of predictor
  vectors, order-N history, and CLAMP16 to match the hardware (the SDK C tool
  doesn't clamp; the RSP does). Verified against the SDK algorithm: order-1
  bit-exact plus order-2 property tests isolating every term. `acidcat inspect`
  now walks the classic libultra **audio bank** (`.ctl` / ALBankFile): the
  ALBank tree (instruments, sounds, wavetables) with each waveform's codebook,
  detected by the 0x4231 revision confirmed against a sane sample rate. Both the
  classic ALBank and the newer SM64-style engine share this codec; sample
  extraction from a full ROM is game-specific (the `.tbl` base is not
  self-describing) and is a follow-up. Layouts verified against libultra
  libaudio.h + the N64 SDK VADPCM decoder.

## [0.79.0] - 2026-07-27

### Added

- **MIDI 2.0 / UMP.** New `core/ump.py` decodes Universal MIDI Packets -- the
  MIDI 2.0 wire/file primitive, self-delimiting by the message-type nibble:
  MIDI 1.0/2.0 channel voice, utility (JR clock/timestamp, Delta Clockstamp,
  DCTPQ), SysEx7/8, Flex Data (tempo / time signature / text), and UMP Stream
  messages. `acidcat inspect` now walks the **MIDI Clip File** (`.midi2` /
  SMF2CLIP): the 8-byte magic then a big-endian UMP stream, surfacing resolution,
  tempo, time signature, metadata, and -- with `--frames` -- the tick-stamped
  event list. Classic `.mid` (SMF 1.0) is unaffected. Layouts verified against
  MMA M2-104-UM (UMP) and M2-116-U (Clip File).

## [0.78.1] - 2026-07-26

### Fixed

- **fxp:** the VST2 field at offset 0x18 is `numPrograms` only for a bank
  (fxSet / FxBk / FBCh); for a single preset (fxProgram / FxCk / FPCh) it is
  `numParams`. The `.fxp` walker labeled it `num_programs` universally.

## [0.78.0] - 2026-07-26

### Added

- **Wii disc audio.** `acidcat extract <disc.iso>` on a Wii disc image decrypts
  the data partition, walks its filesystem, and decodes every BRSTM music stream
  to WAV. New `core/brstm.py` (RSTM container over Nintendo DSP-ADPCM, reuses
  `core/dsp.py`) and `core/wiidisc.py` (AES-128-CBC partition decryption, cluster
  cache, FST walk). Verified end-to-end on a retail disc (81 streams, decoded to
  coherent audio).
- **`[crypto]` extra.** `pip install acidcat[crypto]` adds `cryptography` for the
  Wii partition AES. Kept optional so the base install stays dependency-light and
  the DRM-adjacent crypto is opt-in; without it, `extract` on a Wii disc prints a
  one-line install hint. Standalone `.brstm` files decode with no extra.

## [0.77.0] - 2026-07-25

Game-disc audio extraction. `acidcat extract <disc>` now rips soundtracks and
sound effects off console disc images across five codecs, each reverse-engineered
from real discs and verified by decoding to coherent audio.

### Added

- **PlayStation / CD-XA.** Detect a raw CD sector image (Mode1/2/2352), walk its
  ISO 9660 filesystem, and decode the audio: each `.STR`/`.XA` movie's CD-XA
  ADPCM soundtrack (named by file), and SPU-ADPCM sound banks from `.VB`/`.BD`/
  `.VAG` or any file whose content matches. Banks are split into individual named
  samples via the VAB `.VH`/`.HD` header. Falls back to decoding the raw XA
  channel and splitting it on silence when there is no filesystem. New
  `core/cdxa.py`, `core/vag.py`, `core/iso9660.py`.
- **CD-DA (Red Book) from `.cue` sheets.** Extract each audio track to WAV -- raw
  16-bit LE stereo 44100 PCM, no codec -- handling both split (one `.bin` per
  track) and single-`.bin` (cumulative MSF) layouts, with the pregap skipped. How
  PS1, Sega CD, Neo Geo CD, PC-Engine CD and countless discs carry music. New
  `core/cue.py`.
- **GameCube.** Walk a GameCube disc (`.iso`) filesystem and decode its audio:
  HAL `.hps` streams, CRI `.adx`, and DTK `.adp`. New `core/gcm.py`,
  `core/dsp.py` (Nintendo DSP-ADPCM), `core/hps.py`, `core/dtk.py`.
- **CRI ADX** decoder (`core/adx.py`) -- the middleware behind two decades of
  Dreamcast / PS2 / GameCube / Wii / arcade audio. Standard linear types (2, 3);
  AHX and encrypted raise.
- **TUI region browser.** A blob or disc image opens into a live, segmented
  `locate` scan (space pauses, enter keeps, esc discards) with a browsable region
  table: descend into a region as its own file, extract one/all, cycle the
  forensics mode, toggle the transform lens, carve a range, search raw bytes.
- **TUI disc audio browser.** A PS1/CD-XA disc opens straight into its audio
  catalog -- `.STR` tracks and SPU banks from the ISO -- to audition (a decoded
  preview through ffplay) and extract in place.
- **Ranked codec-vs-PCM detection** (`audioscan.classify`). Instead of labelling
  everything audio-shaped a "raw-pcm blob", rank the possibilities: PS1 SPU-ADPCM
  (a codec -- decode, don't play as PCM) vs linear PCM at a geometry, flagging a
  low-confidence guess as uncertain. Surfaced in the region browser.

### Documentation

- Anatomy pages enriched across the fleet: WAV loop-metadata (the `smpl`
  inclusive-`dwEnd` interop bug) and 32-bit float (the non-unit Cool Edit
  variant); verified corrections and small format details on ~15 pages; fixed a
  doubled-IIFE bug that had left the fxp, rx2, and rmid byte-maps dead.

## [0.76.0] - 2026-07-23

### Added

- **`convert --to-pcm` now decodes Microsoft ADPCM (`0x0002`).** The other common
  "won't play everywhere" WAV codec alongside IMA. A block predicts each sample
  from the two previous ones (one of seven coefficient pairs per block, read from
  the fmt chunk) plus a per-nibble adaptive delta; mono and stereo (interleaved
  nibbles) both handled. Decodes automatically by format tag, or `--codec ms`
  forces it on a mistagged file. Verified **bit-exact against ffmpeg** on real
  mono and stereo specimens (186,762 / 291,456 samples, zero diff). New
  `core/adpcm.py` MS decoder.

### Documentation

- **`docs/formats/adpcm.md`: an ADPCM deep-dive.** History (DPCM to adaptive
  quantization to G.721/726 to IMA/DVI and Microsoft), the predict/correct/adapt
  loop, both codecs' step and coefficient tables and block layouts, and the reader
  traps (nibble order, truncate-toward-zero division, priming-sample order).
- **`docs/format_internals.md`: index refresh.** Arturia `.labx`, SoundFont, and
  tracker modules were still listed "not yet implemented" long after shipping; add
  the sampler and hardware-bank walkers (Kurzweil, E-mu, Akai, Gravis, 8SVX, Amiga,
  BFD, Analog Lab, SigMF), a codecs table, and S3M in the tracker row.

## [0.75.0] - 2026-07-23

### Added

- **`locate --transforms`: find audio hidden under a reversible byte transform.**
  The CTF/RE obfuscation lens. XOR-with-a-key, bit-rotate and nibble-swap are byte
  permutations -- they preserve entropy but scramble autocorrelation, which is
  exactly how they slip PCM past the statistical detector. `--transforms`
  un-applies each candidate to the suspicious windows and asks "is it audio now?".
  Detection is family-level, so one stream is not fragmented across the true key's
  low-bit neighbours; the key is then refined per region by minimum roughness and
  validated -- the single refined key must recover audio across most of the region,
  which yields 0 false positives on noise, repeated code, and source. The reported
  key is a *candidate*: audio is smooth, so the true key and its bit-inverted twin
  (`K ^ 0xFF`) leave equally smooth waveforms and the low bits are dither-level, so
  polarity and the low bits are not recoverable from smoothness alone. Focused:
  reads at most 16 MB. New `core/transforms.py`.

### Documentation

- **`docs/recovery.md`: the "PhotoRec for audio" rescue workflow.** The four
  recovery verbs shipped without narrative coverage; the new doc walks the pipeline
  end to end -- `locate` (signature sweep, statistical PCM detector, headerless MPEG
  cadence) -> `carve --batch` (cut every located region to a directory) -> `extract`
  (unpack a known sampler/tracker bank) -> `convert --to-pcm` -- with worked
  examples, plus the `--transforms` lens. README and CHEATSHEET gain the `locate`
  and `extract` commands, the `carve --batch` / `convert --to-pcm` additions, and a
  recovery section pointing to the doc.

## [0.74.0] - 2026-07-23

### Added

- **`convert --to-pcm` decodes ADPCM WAVs to plain, playable 16-bit PCM.** Games
  and old software ship WAVs in ADPCM that many players reject -- and sometimes
  mistag them (the Doom 64 DC port tags IMA ADPCM as G.726, so ffmpeg/VLC try
  G.726 and fail). New `core/adpcm.py` gives a native IMA/DVI ADPCM decoder
  (`0x0011`, block mono + stereo, plus a continuous variant for block-less /
  mistagged streams). `--to-pcm` decodes a `0x0011` WAV by its header; for an
  unknown/mistagged tag it tries continuous IMA and keeps it only if the result
  is smooth audio, and `--codec ima` forces it. Verified: the mistagged Doom 64
  SFX now decodes to `pcm_s16le` that plays anywhere. (Microsoft ADPCM `0x0002`
  is a planned follow-up.)

## [0.73.0] - 2026-07-23

### Added

- **`locate` gains a third engine: headerless compressed-stream detection.**
  Compressed audio with no container is invisible to the signature sweep (no
  magic) and the statistical detector (high entropy) -- but a codec stream is a
  chain of self-describing frames. The new `core/framescan.py` finds MPEG audio
  (MP1/2/3) by frame-sync **cadence**: an 11-bit sync, a computable frame length
  (reusing `core/mp3.py`), the next valid frame exactly that far ahead, repeated.
  A run of >=12 consecutive valid frames with a stable version/layer/sample-rate
  is a stream (`kind='stream'`); random data almost never chains that far. Runs in
  every mode (fast -- 0.22s on 1 MB of pure sync bytes), so `--mode strict` finds
  headerless streams too. Verified on a real MP3 with its ID3 stripped and buried
  in noise (found at conf 0.99, correctly read as MPEG-1 Layer III 44100), with
  zero false positives on noise. The CTF / asset-dump case `strings` can't touch.

## [0.72.0] - 2026-07-23

### Added

- **`locate` detects float PCM.** Float audio (in-memory buffers, modern engines)
  has high byte-entropy from its mantissa, so the integer detector missed it
  entirely -- a fundamental blind spot. A cheap sampled in-range check gates a
  full float32/float64 probe (random bytes read as float almost never sit in
  `[-1,1]`), so float regions are now found and tagged.
- **`locate --analyze` reports debug tells** per region -- silence (flat),
  dc_offset (biased), clipping (pinned at the rails) -- plus float width/endian.
  The "why is my audio buffer wrong" triage layer.
- **`locate -v/--verbose`** surfaces the evidence behind each region (entropy,
  autocorrelation lags, distribution), so false positives are debuggable.

## [0.71.0] - 2026-07-23

### Changed

- **BREAKING (pre-1.0): the `recover` verb is now `locate`, and it no longer
  extracts.** This splits discovery from extraction into two honest, composable
  verbs. `locate` *finds and reports* audio regions in a blob and never writes
  (`recover` implied a restoration it didn't do once extraction moved out, and
  `search` was already taken by compatible-sample search). New on `locate`:
  `--analyze` (infer each raw blob's PCM width/channels/endianness -- sample rate
  is not in the bytes, so it's reported null with common candidates) and
  `-f table|json|tsv` (records to stdout, summary to stderr). `--mode strict` now
  skips the statistical pass entirely, so a signature-only run is fast even on a
  multi-hundred-MB image.

### Added

- **`carve --batch -`** reads `locate`'s JSON/TSV records and extracts each region
  from the target into `-o DIR`. The pipeline is now two verbs composing:

  ```
  acidcat locate disk.img -f json | acidcat carve disk.img --batch - -o out/
  ```

  Verified on a real Dreamcast disc image (Doom 64 DC port, 700 MB): the pipeline
  lifted all 92 embedded SFX WAVs in under 2 seconds.

## [0.70.0] - 2026-07-22

### Added

- **`extract` decodes IT-compressed samples (IT214/215).** Implements Impulse
  Tracker's LSB-first variable-bit-width delta compression (8- and 16-bit,
  single- and double-delta, chosen by the file's `cmwt`), so compressed `.it`
  samples now extract instead of being skipped. Verified against real IT214 and
  IT215 modules -- every sample decodes to its exact declared length.

### Fixed

- **IT sample extraction read the wrong (S3M) field names** (`pcm_off`/`is_pcm`),
  so it silently extracted nothing; now uses the IT sample's `data_off` /
  `length` / `bits16` / `c5_speed`.

## [0.69.0] - 2026-07-22

### Added

- **`extract` gains Akai MPC2000 `.snd`.** A 38/42-byte header then 16-bit signed
  LE PCM at 44100 Hz (stereo stored non-interleaved, interleaved on output).
  `extract` now covers 13 formats: MOD/XM/IT/S3M, 8SVX, NCW, SF2, multisample,
  KRZ, GF1 patch, E-mu E4B, E-mu E5B, and MPC2000 `.snd`.

## [0.68.0] - 2026-07-22

### Added

- **`extract` gains E-mu Emulator X / Proteus X (`.ebl`/`.exb`).** Each E5S1
  sample is a fixed `0xb8`-byte header (inline UTF-16LE name, rate at +0x6a) then
  16-bit signed little-endian mono PCM; the walker locates the chunks and the
  extractor slices past the header. `.exb` banks carry only presets + links, so
  the samples come from the `.ebl` libraries (both sniff as `e5b`). Verified on
  real Emulator X libraries. `extract` now covers MOD/XM/IT/S3M, 8SVX, NCW, SF2,
  multisample, KRZ, GF1 patch, E-mu E4B, and E-mu E5B.

## [0.67.0] - 2026-07-22

### Added

- **`extract` gains E-mu Emulator 4 / EOS (`.e4b`).** Each E3S1 chunk is a
  94-byte header then 16-bit signed little-endian mono PCM; the walker locates the
  chunks and rate, and each sample renders to a WAV. Verified on a real bank
  (1,283 samples). (E5B/`.exb` keeps its PCM in sibling `.ebl` files, and Akai's
  `.akp` references external `.wav` samples, so neither is an embedded-extract
  case.) `extract` now covers MOD/XM/IT/S3M, 8SVX, NCW, SF2, multisample, KRZ,
  GF1 patch, and E-mu E4B.

## [0.66.0] - 2026-07-22

### Added

- **`extract` gains ScreamTracker S3M and Gravis UltraSound GF1 patch (.PAT).**
  S3M: the walker already located every sample; wire it (unsigned PCM, stereo
  stored as L-block then R-block, interleaved on output) -- verified on real
  modules. GF1 patch: a **new walker** (`core/walk/gf1pat.py`, magic `GF1PATCH`;
  fixed header -> instruments -> layers -> per-sample header with data size, rate,
  and a modes byte for 8/16-bit + signed/unsigned) plus sniff and extraction --
  it has no chunk grid, so it needs a dedicated walker. Verified on real GUS
  patches. `extract` now covers MOD/XM/IT/S3M, 8SVX, NCW, SF2, multisample, KRZ,
  and GF1 patch; `inspect` gains .PAT.

## [0.65.0] - 2026-07-22

### Added

- **`extract` gains Bitwig `.multisample` and Kurzweil KRZ.** A `.multisample` is
  a zip of WAVs -- each member streams out verbatim (read from the path so a
  multi-hundred-MB pack is not held in memory; verified on a 214 MB, 258-WAV
  Orchestral Strings pack). A KRZ Sample object addresses a word range in the
  bank's one contiguous 16-bit big-endian PCM region; the walker locates the
  region and objects, then each sample is sliced, byteswapped, and rendered to a
  WAV at its rate (verified on a Sweetwater bank -- real full-range audio).
  `extract` now covers MOD/XM/IT, 8SVX, NCW, SF2, multisample, and KRZ.

## [0.64.0] - 2026-07-22

### Added

- **`acidcat extract` -- pull embedded samples out of a bank/module as WAVs.**
  One verb over every walked sample-bearing format: tracker modules (MOD raw
  8-bit, XM 8/16-bit delta, IT PCM), 8SVX, NCW, SoundFont (SF2 PCM; SF3 = Ogg
  verbatim). Decodes where there's a codec, copies verbatim where the samples are
  already PCM. `-o DIR`, `--json` manifest, `-`/stdin; read-only on the source.
  `inspect` shows the samples are in there -- `extract` gets them out. Verified on
  real modules (a MOD gave 11 full-range 16-bit WAVs). Formats acidcat walks but
  cannot yet extract -- Kurzweil KRZ, E-mu E4B/E5B, Akai, Bitwig multisample, S3M,
  RX2, BFD .bfdlac, IT-compressed samples -- await specimens and/or codec work.

### Changed

- **`carve` gains surgical typed extraction** (the raw byte-range path is
  unchanged; carve stays read-only). `--type u8..i64/f32/f64/Ns/cstr` (optional
  be/le) decodes a range as a value instead of raw bytes; `--count N` reads an
  array; `--endian both` prints each interpretation (the endian guess). `--at
  EXPR` anchors the offset so you never hand-count: `0xNN`, `end[-N]`,
  `find:STR|0xHEX[+N]`, or `chunk:ID[+N]` (any walked format). `--struct '@OFF
  name:type ...'` decodes a labeled record in one shot. `--field NAME` prints a
  walker-decoded field by name (as `inspect` shows it). `--format
  raw|value|hex|c|py|b64` shapes the output.

## [0.63.0] - 2026-07-22

### Added

- **`acidcat recover` -- "PhotoRec for audio."** Recover audio from an unknown
  blob (a disk image, a chip dump, a proprietary file that embeds samples) with
  two engines: a signature sweep for known containers (RIFF/WAVE, FORM/AIFF/8SVX,
  fLaC, OggS, ID3-anchored MP3) and a statistical detector for signatureless raw
  PCM. The statistical detector reads a blob as 8-bit signed PCM and scores each
  window on entropy + the *shape* of autocorrelation across lags (audio is smooth
  -- the same property that makes it compressible is what makes it detectable) +
  value distribution, calibrated on a labeled corpus (real audio vs code / text /
  random / compressed). Three forensics levels (`--mode strict|normal|aggressive`),
  `--json`, `-`/stdin, and `-x/--extract DIR` (fuses recover -> carve). A recovery
  record's offset/length is a `carve` range and a recovered container is an
  `inspect`/`convert` input, so the verbs chain into a rescue pipeline. Corrupt
  container extents are bounded to the real audio, never greedy. Known limits:
  pure-Python scan is slow on multi-GB images; compressed and raw-16-bit
  headerless audio are out of scope for the statistical engine (by design).
- **BFD `.bfdlac` (BFDC) walker.** FXpansion BFD's per-hit compressed-audio
  format: a big-endian IFF-style container (`fmt`/`BFDi`/`Indx`/`data`) wrapping
  an undocumented lossless codec. Surfaces the audio descriptor (bit depth, rate,
  channels, duration), the pack id, and the block seek index. Confirmed uniform
  across 181,696 real files.
- **Generic structural triage.** When no format-specific walker matches, acidcat
  now recognizes an unknown *chunked container* (and guesses whether it holds
  audio) from universal signals -- a magic that opens an IFF/RIFF-style
  `[tag][size]` grid tiling the file, audio-indicative tags, and payload entropy
  -- instead of a flat "unrecognized structural format." Surfaces the chunk grid
  (which doubles as the reverse-engineering starting point for a new walker), and
  stays conservative enough that random/non-container data still falls through.

## [0.62.0] - 2026-07-22

### Added

- **`convert` learns IFF 8SVX -> 16-bit WAV.** The Amiga sampled-voice format,
  alongside the existing NCW -> WAV and SF2 -> WAVs paths. An 8SVX BODY is 8-bit
  signed PCM, optionally Fibonacci-delta compressed (VHDR.sCompression == 1): two
  4-bit codes per byte, each indexing a fixed delta table added to a running
  accumulator (DPCM's grandfather, the same lineage as the NCW codec). `core/svx.py`
  reconstructs the samples and renders a standard 16-bit mono WAV so a 1988 voice
  plays in any modern tool; multi-octave voices keep the high octave (oneShot +
  repeat). Decode, not access control -- the walker describes the bytes, this turns
  them into audio. Field-tested byte-identical to a standalone reference decoder on
  raw and Fibonacci-delta specimens from the Amiga corpus.

## [0.61.1] - 2026-07-21

### Fixed

- **MP3 Xing/Info VBR-header location on CRC-protected frames.** The tag offset
  is 4 (header) + side-info size (36/21/21/13); the walker wrongly added 2 more
  when the frame carried a CRC. LAME's VbrTag.c deliberately holds the tag at the
  CRC-absent offset (so Xing/Info readers find it), so the +2 walked past the tag
  and missed the VBR header on a CRC-protected first frame. Now `base = 4`
  unconditionally. Found via the acidcat-mp3 research page review, verified
  against VbrTag.c and mp3guessenc; regression test added.

## [0.61.0] - 2026-07-20

### Added

- **`--sandbox` bwrap profile** (Linux + bubblewrap) -- the namespace-isolation
  layer on top of the `limits` worker. Runs the parse inside a bubblewrap
  namespace with **no network** and a filesystem holding only the read-only
  Python runtime and the one input (bind-mounted); the user's home and data are
  never mounted. Hardened with `--new-session`, `--clearenv`, and a tmpfs. The
  `limits` resource caps ride along (inherited across exec), so `bwrap` is a
  strict superset. `--sandbox-profile {auto,limits,bwrap}` selects it (auto =
  strongest available), resolved up front and fail-loud; availability probes
  that unprivileged user namespaces actually work. CI exercises the real bwrap
  path.

## [0.60.0] - 2026-07-20

### Added

- **`acidcat inspect --sandbox`** (experimental, Linux only) -- parse untrusted
  input in a resource-limited fork so a memory- or CPU-bomb file takes down only
  a short-lived worker, not the tool. The `limits` profile caps the worker's
  address space, CPU time, and file-write size via `setrlimit` and returns the
  walk result over a pipe, with a parent-side wall-clock timeout and result-size
  cap. `--sandbox-mem MB` / `--sandbox-timeout S` tune the caps. It fails loud
  (never silently runs unsandboxed) where it cannot run. This is the foundation
  for stronger `bwrap` (namespace) and `strict` (seccomp) profiles on the same
  worker plumbing.

## [0.59.0] - 2026-07-19

### Added

- **Amiga music-format walkers** (`core/walk/amiga.py`), from the acidcat-cassie
  corpus: **SMUS** (IFF `FORM/SMUS`, the Sonix score and Deluxe Music
  Construction Set save format -- SHDR tempo/volume/tracks, NAME, INS1
  instruments, TRAK tracks); **Oktalyzer** (`OKTASONG` -- CMOD channel-split
  giving 4-8 voices, the SAMP sample table); and recognize-plus-header for
  **MED/OctaMED** (`MMD0`-`MMD3`) and **Future Composer** (`SMOD`/`FC14`). All
  magic-sniffed, never-raise; field-tested on 29 corpus specimens (0 crashes).
  The magic-less 15-instrument Ultimate Soundtracker (`mod15`) is intentionally
  not sniffed (it would require guessing).

## [0.58.0] - 2026-07-19

### Added

- **IFF 8SVX walker** (`core/walk/svx.py`) -- the Amiga 8-bit sampled-voice
  format, a direct big-endian ancestor of RIFF/WAVE. Decodes the `VHDR` voice
  header (rate, octave count, raw vs Fibonacci-delta compression, 16.16 volume),
  the `NAME`/`AUTH`/`ANNO`/`(c) ` text set (`ANNO` is the Amiga-side `ISFT`
  authoring-tool tell), `ATAK`/`RLSE` envelopes, `CHAN`, and `BODY` with derived
  duration; flags the FORM-size undercount writer bug. Field-tested on 492 real
  Amiga specimens (0 crashes). Plus a mirrored 8SVX anatomy datasheet in
  `docs/formats/`.
- `clm ` chunk recognized as **Xfer Serum** wavetable export (corpus-confirmed
  against 43 files, all wavetables, none carrying an `acid` chunk -- correcting
  the old "Sonic Foundry" web lore). Format tag **0x0039** named Roland RDAC
  (RFC 2361), noting mmreg.h's unregistered Crystal IMA ADPCM squat at the same
  value.

## [0.57.0] - 2026-07-19

### Added

- **`acidcat census`** -- a scaled-up, read-only chunk-ID histogram and
  open-question survey over a corpus of RIFF-family files (containers, format
  tags, LIST types, fact sizes, bext versions, and flags for the rare/
  undocumented chunks). Engineered for millions of files: an explicit-stack
  `os.scandir` traversal that never stats a file it will not open (extension +
  `d_type` off the dirent), is loop-safe and boundary-aware (unconditional
  `(st_dev, st_ino)` directory dedup catches symlink loops and same-device bind
  mounts; autofs mountpoints read from the mount table, never probed; optional
  `--one-file-system`), positioned `pread` reads of chunk headers only (no audio
  payload), with `posix_fadvise` readahead suppression + `DONTNEED` so a scan
  does not evict the page cache, and an SSD/HDD-aware reader thread pool. Fixes
  two bugs carried by the prototype: the bext version was read at payload offset
  602 instead of 346, and non-printable FOURCCs from corrupt files were emitted
  raw (now grouped as `hex:` tokens so the JSON stays well-formed). RF64/BW64
  data-chunk sizes are resolved from the `ds64` chunk, so the walk reaches
  trailing chunks past the `0xFFFFFFFF` sentinel instead of stopping at `data`.

## [0.56.1] - 2026-07-19

### Fixed

- **Kurzweil `.KRZ` keymap sample references.** The keymap decoder assumed the
  modern 5-byte entry layout (method `0x13`: tuning, sampleID, subSample) and
  always read the referenced sampleID at entry offset +2. Real Sweetwater
  soundset banks use method `0x03`: a 3-byte entry with no tuning prefix, so the
  sampleID sits at offset 0 and was misread (e.g. sample 200 reported as 256).
  The decoder now locates the sampleID after the optional 2-byte tuning prefix,
  keyed on the method `0x10` bit, with a bounds-guarded read; `entry_size`
  remains the authoritative stride. Correct for every no-tuning method
  (`0x01`-`0x0f`); tuning methods (`0x11`-`0x19`) unchanged. Swept the Sweetwater
  corpus (0 crashes); verified against real banks (e3_bass -> sample 200,
  angkorw -> 201/203/204).

### Docs

- Added the Kurzweil `.KRZ` anatomy datasheet (`docs/formats/krz-anatomy.html`):
  a 7-tab interactive byte map (PRAM header, object framing, Sample, Keymap,
  Program, SROM) built from real specimen bytes. Homogenized the anatomy fleet
  onto one canonical renderer and added the color-key legend to the emu and mpc
  sheets.

## [0.56.0] - 2026-07-19

### Docs

- Regenerated `docs/codebase_explorer.html` (was a v0.9.0 fossil): reframed from a
  WAV metadata explorer to the current byte-level dissection / reverse-engineering
  tool, updated to 23 CLI verbs, 26 walkers, the dissection surface
  (inspect/probe/carve/shape + the repair/validate/audit constraint model), the
  grammar engine, and provenance -- in the locked amp-orange aesthetic with the
  hover margin cards.

### Changed

- Provenance writer signatures now live in a sidecar data file
  (`core/data/provenance_signatures.json`) instead of being hardcoded in
  `provenance.py` -- signatures are data, editable without touching code. A user
  override at `~/.acidcat/provenance_signatures.json` is merged on top (its
  `canon` rules win first-match, its `chunk_signatures` are appended), so
  signatures can be added with no code change or re-release.

### Added

- New WAV writer signatures, validated against a 807,394-file corpus census:
  `strc`/`str2`/`SyLp` (Sony/Magix ACID / Sound Forge), `SNDM` (Soundminer),
  `LGBM` (Logic, folded in with LGWV/ResU), `DIGI` (Digidesign, with DGDA),
  `RLND` (Roland), `Cr8r` (GoPro CineForm / Adobe). `_PMX` (Adobe XMP) is
  recorded as a deliberately-shared, corroborate-only tell, not a standalone
  attribution.
- ffmpeg RF64_AUTO structural fingerprint: a 28-byte `JUNK` chunk immediately
  after `fmt ` (the ds64 placeholder) is detected positionally and attributed to
  FFmpeg/libav at "likely" confidence.

## [0.55.1] - 2026-07-18

### Fixed

- `acidcat info` (and bare-path `acidcat file.ext`) no longer mis-parses a
  non-WAV structural format as a headerless WAV (the misleading "Chunks (none)"
  output). Any format the walkers decode but `info` has no dedicated builder for
  -- Kurzweil `.KRZ`, E-mu, Akai, ReCycle, VST FXP, trackers, SF2, the MPC
  family, synth presets, RF64, ... -- now gets a walker-backed summary (format
  label, top-level summary, region count) and a pointer to `acidcat inspect` for
  the full decode. Previously only a hardcoded preset list was handled.

### Changed

- Package description aligned with the project's direction: "Binary analysis and
  reverse-engineering for audio file formats and music-production hardware".

## [0.55.0] - 2026-07-18

### Added

- Kurzweil K2000 / K2500 / K2600 (VAST) `.KRZ` bank walker (`core/walk/krz.py`).
  Decodes the flat object-database container (big-endian `PRAM` header,
  negative-blocksize object walk, `type<<10 | id` hashing, the int32 end marker,
  and the trailing 16-bit PCM region) and the Sample (root key, rate from
  samplePeriod, loop/one-shot flag, PCM word offsets), Keymap (method, referenced
  sample ids), and Program (VAST layer count) object bodies. Setup / Master /
  Studio-FX and other object types are surfaced by type/id/name. `SROM`
  effects/sample-ROM files (same `.krz` extension) are recognized header-only.
  Reverse-engineered from the mpc2emu GPL reference + the KurzFiler source and
  verified structurally against 242 real Sweetwater soundset banks (zero
  crashes). The VAST program's per-parameter decode and the CAL keymap-ref offset
  are follow-ups.

## [0.54.0] - 2026-07-18

### Added

- `acidcat similar FILE` -- an index-backed similarity CLI verb, replacing the
  retired CSV-era one. It fans out across every registered library and ranks by
  z-standardized cosine over the stored feature vectors, filtered to the target's
  kind (loop/one-shot) by default. The scoring logic moved into
  `core/search.py`, so this verb and the MCP `find_similar` tool now call one
  shared implementation instead of the tool owning private logic -- the CLI and
  MCP can no longer drift. If the reference is not indexed, its vector is
  extracted live (needs `[analysis]`).

- Descriptor-driven fuzzing (`tests/test_descriptor_fuzz.py`): a generator
  derives the walker's decision points mechanically from the WAVE grammar
  descriptor (every `Valid` range edge, every `Switch` case at each window size
  around its `min_window`, every `min_len` truncation edge, each
  `Requires`/`Order` rule) and asserts the walker, the interpreter, and the
  strict `structure` parser agree at each one. It reaches boundary-exact inputs
  the seeded-random differential fuzz cannot, and its interpreter-vs-walker
  parity check catches a walker regression even when the walker swallows it into
  a per-chunk warning (a class the never-raise contract otherwise hides). This
  is the first non-test consumer of the grammar interpreter.
- Grammar engine, FLAC foundation: the declarative descriptor now reproduces
  FLAC STREAMINFO byte-for-byte against the walker (fields + summary, validated
  across the fixtures and a descriptor-derived fuzz sweep). This is the second
  container format and the first non-RIFF one, and it introduces three engine
  primitives WAV never needed: a second container strategy (`flac_blocks`, over
  `core/flac.iter_metadata_blocks`), the `Codec` type (the 3-byte big-endian
  frame sizes via `u24be`) and `Raw` type (the md5 signature), and the
  `BitGroup` construct for the overlapping bit-packed fields
  (sample_rate/channels/bits/total_samples share one 8-byte word). Opt-in and
  test-only, like the WAV descriptor. The other FLAC block types stay
  walker-side until the repeat-over-records construct lands.

### Fixed

- The "degrade with warnings, never raise" walker contract is now enforced
  structurally, at the `walk_file` boundary every consumer shares: a walker
  bug on hostile input degrades to a walker-error warning instead of a
  traceback out of `od`, `audit`, the TUI, or the public `acidcat.walk()`
  (previously only `inspect` and `shape` carried their own catch-all).
  `ACIDCAT_WALKER_RAISE=1`, set by the test suite, re-raises so a walker
  defect stays a loud CI failure.
- Two walker conventions became mechanically enforced invariants
  (`tests/test_walker_invariants.py`): no argless `.read()` in `core/walk/`
  (the class behind the historical sf2/rmid memory-amplification bugs), and
  every semantic ctx key the wav/aiff/midi walkers publish must be registered
  in `vocab.CTX_KEYS` (so a walker rename cannot silently desynchronize from
  the scan path). The ctx check immediately surfaced three unregistered MIDI
  keys (`division`, `format`, `tracks`), now registered.

### Removed

- The CSV-era `similar` and `search` verbs (pandas/sklearn pipelines over
  one-off scan CSVs, untested and index-unaware; the index-backed
  `find_similar` MCP tool is the real implementation) and the `--ml-ready`
  flag on `scan`/`features`, whose only consumer they were. The `[ml]`,
  `[viz]`, and `[notebook]` extras are gone with them; `[all]` now means
  `[analysis,mcp-http,tui]`.
- The legacy parsers the 2026-07 walker unification left behind:
  `parse_riff`, `get_duration` (core/riff.py), `parse_aiff`, `parse_midi`,
  `parse_serum_preset`. All had zero production callers except `parse_riff`,
  whose one caller (`acidcat chunks`) now reads parsed fields from the
  inspect walker, so every format is decoded in exactly one place. Their
  malformed-input safety tests (cue-count bomb, acid layout/padding/
  truncation, running status, SMPTE division, key signatures, AIFC
  compression codes, non-finite 80-bit rates) were retargeted at the
  walkers before deletion.

### Changed

- `acidcat chunks` parsed-field output now comes from the WAV walker:
  every decoded field with its real name (previously a fixed subset of
  acid/smpl/fmt/cue/LIST/bext fields).

## [0.53.0] - 2026-07-18

### Added

- E-MU Emulator X / Proteus X (E5B0) voice DSP decode in verbose mode. `inspect
  --verbose` / `-F` now dumps each voice's front-panel controls: the filter
  (type, normalized frequency, resonance from `E5Fl`), the three envelopes
  (Amp/Filter/Aux, six stages with levels as percent from `LIST/EvL ` -> `E5Ev`),
  and the active modulation cords (source -> destination -> amount from
  `LIST/CrdL` -> `E5Cd`, with a partial index -> name table). The default view
  stays compact. Every parameter is anchored to a named `E5V1` sub-chunk; the two
  nonlinear scales (filter frequency, envelope rate) are shown as the raw
  normalized float rather than a fabricated physical value. All offsets verified
  by save-and-diff against Emulator X. The anatomy sheet gains an `E5V1` tab.

## [0.52.0] - 2026-07-17

### Fixed

- MP3 frame walker performance: on a sync loss it rescanned with a seek + read
  per byte, so `acidcat audit` on a crafted MP3 (a valid frame then a long run
  of `0xFF`) took ~21 s for 30 MB. It now scans a buffered window in memory and
  bounds the resync distance; the same input is ~1 s.
- MIDI System Common / Real-Time message advance: 0xF1 (MTC) and 0xF3 (song
  select) carry 1 data byte and 0xF2 (song position) carries 2, but all were
  skipped by a blind 2 bytes, misaligning the events that followed (wrong track
  stats). Fixed in both the walker and the legacy parser.
- Command-layer memory: `audit`, `od`, and `probe` read the whole input with
  `f.read()`, so peak memory scaled with file size (~96 MB on a 48 MB file).
  They now memory-map the input (`core/mapped.map_file`), keeping peak memory
  flat regardless of size (audit 96 -> 0.2 MB, od 96 -> 0.03 MB, probe 48 ->
  0.04 MB) with no loss of full-file coverage. The mmap-safety fixes this
  surfaced are included: memoryview element access in `structure`, and
  copy-on-read paths in `mp4repair`/`countrepair`/`repairers`.

### Added

- E-mu Emulator X / Proteus X (E5B0) voice decode: the walker now reads each
  voice's `LIST`/`TWL ` crossfade windows and reports the per-voice key range
  and (when narrowed) velocity window on the E5P1 preset. It also stops
  deduping by sample index, so a multisample preset shows its full
  keyboard/velocity map instead of collapsing voices that reuse a sample. The
  window byte layout (lo at [4], hi at [7]) was confirmed by save-and-diff
  against Emulator X.
- A differential + round-trip fuzz harness (`tests/test_differential_fuzz.py`):
  seeded mutations of a hermetic IFF file assert the lenient walker degrades and
  the strict `structure.parse`/`emit` either round-trips byte-exactly or raises
  `StructError`, exercising the strict/lenient split the audit noted was untested.

## [0.51.0] - 2026-07-17

### Fixed

- Robustness hardening, driven by an external audit (untrusted-input safety).
  Crashes that violated the "degrade with warnings, never raise" contract now
  degrade: the tracker walkers (`.xm`/`.it`/`.s3m`) on a truncated header, and
  the SigMF walker on a `.sigmf-meta` sidecar whose JSON has the wrong types
  (non-object top level, non-dict `global`/captures/annotations, or a string
  where a number is expected). Memory-amplification gaps closed with read caps:
  RMID, SF2 (now validated before the whole-file read), the `.xpn`
  `Expansion.xml` member (streamed), the NI `.ksd` edit path (inflate cap), the
  Serum metadata reader, and the MIDI `--frames` event listing (now bounded
  while collecting rather than built in full then sliced). A cross-walker
  truncated-magic test guards the crash class against regression.

## [0.50.0] - 2026-07-17

### Added

- E-MU E5B0 (Emulator X / Proteus X) preset voice/zone decode: each `E5P1`
  preset now reports its voice count, zone count, and the SamplePool sample each
  zone plays (1-based index) with its root key, by walking the nested
  `E5VL` / `E5V1` / `E5ZL` / `Zhdr` chunk tree. The root key is verified against
  named sample pitches; the interior chunk walk uses the same adaptive
  word-alignment as the top level (the Proteus module banks do not pad odd
  interior chunks), and a desync or cap surfaces as a warning instead of
  degrading silently. Corpus: 169 banks, 0 crashes, 225,578 zones decoded.

## [0.49.0] - 2026-07-16

### Added

- E-MU sampler bank walker (`.E4B`, `.exb`, `.ebl`): reads both the EOS hardware
  bank (`FORM E4B0`) and the Emulator X / Proteus X software bank + sample
  library (`FORM E5B0`). Surfaces the container, the table of contents (with an
  offset cross-check against the chunk chain that flags corruption), presets
  (resolving voice/zone sample references back to names), and samples (name,
  rate, loop). Validated on ~19k real Emulator X / Proteus X files and 48 real
  EOS banks carved from a native E-MU CD-ROM; adaptive chunk word-alignment
  handles the non-padding Proteus-module banks, and CD-streamed banks (no EMSt,
  padded tails) are reported as informational notes rather than corruption.
- Format-anatomy datasheet for the E-MU family, `docs/formats/emu-anatomy.html`
  (interactive byte-level register maps for FORM/TOC2/E5P1/E5S1/E5SL/E4B0).

## [0.48.0] - 2026-07-14

### Added

- New format walkers: Arturia Analog Lab bank (`.labx`), ScreamTracker 3
  (`.s3m`), SigMF recordings and bare IQ captures (`.sigmf-meta`/`.sigmf-data`,
  `.cu8`, GQRX `.raw`, PortaPack `.C16`+`.TXT`), Akai S5000/S6000 programs
  (`.akp`), and the Akai MPC family: `.mpcpattern` sequences, `.xpm` keygroup
  programs, `.xpn` expansion packages, `.xtd` kits, plus the vintage MPC1000/2500
  and MPC2000 `.pgm` programs and MPC2000 `.snd` sounds. The tracker, SigMF, and
  vintage MPC formats expose their samples/segments as carveable byte regions.
- Provenance: DAW structural chunk-signatures (Apple Logic, MOTU Digital
  Performer, Bitwig, Avid/Digidesign) and narrow comment / device / tracker
  writer tells, corpus-verified.
- MP3: ID3 picture-frame (APIC/PIC) image data is surfaced as a carveable
  `<id>:image` region with an xref, flagging a complete embedded file
  (JPEG/PNG/GIF) and warning on non-padding bytes past its terminator.
- Vital: unknown top-level JSON keys and trailing bytes after the preset are
  flagged as unvalidated side-channels instead of being silently accepted or
  rejected.

- Public library API for tools built on acidcat: `edit_metadata(path, changes)`
  (plus `EditError` / `EditResult`), `read_tags(path)`, `read_id3v2(path)`,
  `list_id3v2_frames(path)`, and the `tui_theme` brand-palette module -- so
  downstream tools use public names instead of reaching into `commands` / `core`
  internals. The metadata-edit dispatcher moved from `commands/write` to
  `core/edits.edit_metadata`.
- TUI (`acidcat tui`): byte-view modes (`b` cycles hex / entropy / hilbert /
  histogram of the file), region audition (`p` plays the selected chunk as raw
  PCM via ffplay, `.` stops), and per-field hex tinting in the chunk view.

### Changed

- `core.viz.byte_class(b)` now returns `(glyph, class_name)` instead of
  `(glyph, hex_color)`; the byte-class -> color map lives in
  `tui_theme.BYTE_CLASS` so the CLI `probe map` and the TUI byte-map views share
  one palette (they had drifted). Breaking for any direct `byte_class` caller.

### Fixed

- `.multisample` zone and manifest chunks pointed at the ZIP local-file header,
  so `carve` yielded the header instead of the sample; they now use the entry's
  real data offset, and a STORED sample carves to the literal WAV/FLAC.
- MP4: re-read the `moov` box when a faststart layout overruns the head-read
  window, so a large-file encoder tag no longer goes missing.

## [0.47.0] - 2026-07-12

### Added

- Grammar engine, Phase 1: the declarative engine now reproduces the full WAV
  walker vocabulary byte-for-byte -- the complete `fmt ` chunk (every format-tag
  variant: PCM, MS/IMA ADPCM, MPEGLAYER3, and WAVE_FORMAT_EXTENSIBLE, dispatched
  by a `Switch` construct) plus the `inst` and `acid` regions. Adds the guard
  vocabulary (`Cmp` / `Remaining`), `Valid` plausibility warnings, note-sources,
  `Hex` / `Float` types, decode/summary helpers, format-level rules, and the
  per-region partition with a walk-scope dataflow rule. Verified byte-for-byte
  against the hand-written walker across the full WAV corpus (6,998 checks).
  Still opt-in and test-only; the walkers remain the oracle and the default.
- `acidcat shape DIR` -- a fast one-line structural fingerprint per file
  (format, key summary, chunk-id set) for specimen-hunting across a large
  library: pipe to `sort | uniq -c` to surface the rare or malformed shapes.
  Flags: `--fast` (header-only, no field parse), `--anomalies` (append the
  forensic anomaly type), `--format FMT` (filter), plus
  `--coarse` / `--no-path` / `--warn-only`.
- `acidcat od FILE` -- an objdump-x-style colored hex view: header bytes plus
  per-field offset / hex / decoded-value lines, opaque payloads dimmed.
  `--color` auto/always/never, `--width N`.

### Changed

- Faster scanning: the WAV and AIFF walkers no longer read the audio payload
  they never parse -- `inspect_wav` read up to 64 KB of the `data` chunk that
  `_parse_data` ignores, and `inspect_aiff` read 64 KB of `SSND` for an 8-byte
  header. Output is byte-for-byte identical; this drops an unnecessary read
  plus a transient allocation per file, which matters most on cold-cache scans
  of large libraries.

## [0.46.0] - 2026-07-11

### Added

- Declarative grammar engine, v1 walking skeleton (`core/grammar`): format
  descriptors as pure data plus one interpreter that emits the hand-written
  walkers' exact chunk/field model. Ships the Int/Enum type layer, a lenient
  `iff` container strategy with walker-equivalent traversal semantics (yields
  EOF-overrunning chunks with their declared size plus a warning, degrades
  instead of raising), and a WAV descriptor covering the `fmt ` chunk --
  proven byte-exact against `walk/wav` field-for-field across the 2,327-WAV
  corpus. Experimental and opt-in: nothing imports it on the `import acidcat`
  path, `walk_file` dispatch is unchanged, and the walkers remain the oracle
  and the default.

## [0.45.0] - 2026-07-11

### Added

- Public library API. `import acidcat` now exposes a stable, documented engine
  surface -- `walk`, `probe` (byte dissection), `viz` (entropy/Hilbert), `analyze`
  / `repair` (constraints), `anomalies_scan`, `Report`/`Violation`, `Unsupported`
  -- so consumers (the acidcat-playground, any dissection tool) import from the
  package root instead of reaching into `acidcat.core.*` internals. Importing the
  package pulls only the zero-optional-dependency core; tagging (mutagen), the TUI
  (textual), and librosa load only when their commands run. This is the engine
  boundary that makes the acidcat-as-library architecture solid.

## [0.44.0] - 2026-07-11

### Added

- `acidcat probe entropy` and `acidcat probe map`: byte-visualization dissection
  views (`core/viz.py`, zero-dependency terminal primitives). `entropy` plots a
  Shannon-entropy curve across the file plus a byte histogram, flagging spans that
  read as encrypted or compressed (>= 7.2 bits/byte). `map` draws a binvis-style
  Hilbert byte-class map -- adjacent cells are adjacent bytes, so headers, PCM,
  and appended/cavity regions show up as distinct blocks -- in truecolor on a TTY
  or byte-class glyphs otherwise. Both upstreamed from the playground's `viz.py`,
  since seeing a file's shape is a dissection capability.

## [0.43.0] - 2026-07-11

### Added

- `acidcat probe`: low-level byte dissection, the RE-tool surface. Read a file as
  raw bytes the way a reverse engineer does -- `read` (typed read at an offset,
  pwndbg `x`), `scan` (find every offset holding a value, in both byte orders,
  Cheat-Engine style), `find` (byte-pattern search), `strings`, `hexdump`, and
  `diff` (changed byte ranges between two files). The scalpel's edge over
  `xxd`+`grep`: an address can be a raw offset (`0x2c`) OR a structural name
  resolved through the walker -- a chunk id (`data`) or a chunk field
  (`fmt.sample_rate`) -- so you dissect by structure, not by counting bytes.
  `core/probe.py` holds the primitives; the `write`/`repair` edit path stays as
  the experimentation bench it was meant to be, with `probe` the read-only
  dissection face.

## [0.42.0] - 2026-07-11

### Added

- `acidcat audit` now surfaces MP3 truncation: it deep-walks an MP3 (only) so the
  Xing/VBRI declared frame count is cross-checked against the frames actually
  present. A big divergence -- the tell of a truncated or clipped MP3 -- appears
  in the FORENSICS section. The walker already had the check; audit now triggers
  it, thoroughly, without deep-walking other formats.

## [0.41.0] - 2026-07-11

### Changed

- Re-enabled `acidcat write` for Bitwig and Native Instruments presets. The
  editors (Bitwig meta splice, NI nksf/ksd/hsin, each with its own size cascade)
  were built and round-trip-tested but gated behind a "not enabled" refusal
  pending verification; that refusal is removed. The write routes through the
  same `writer.commit` sink (a `_original` backup, atomic write, read-back
  verify), and the format is labelled "(experimental)" with a caution to confirm
  the preset reloads in its app. Editing proprietary preset metadata is now
  possible from the CLI, not just readable.

## [0.40.0] - 2026-07-11

### Added

- COUNT-kind repair, the fourth and final derived-field kind. `acidcat repair`
  and `validate` now clamp a RIFF table-count that claims more records than the
  payload can physically hold -- WAV `cue ` (`num_cue_points`) and `smpl`
  (`num_sample_loops`) -- which a reader would otherwise trust and walk off the
  end of the chunk. The witness is payload capacity; only the over-capacity
  direction is repaired (a count smaller than capacity is left alone, since a
  chunk may carry trailing padding). Length-preserving; never touches audio.
- The constraint framework now runs **every** applicable repairer and aggregates,
  so a file with more than one kind of violation (a stale size and an
  over-capacity count) is fully repaired in one pass.

## [0.39.0] - 2026-07-11

### Added

- LAME tag detail in provenance: a LAME-encoded MP3 now reports its encode
  settings, not just the version -- e.g. "LAME 3.100 (VBR (mtrh), lowpass 20500
  Hz)" from the Xing/LAME tag the walker already decodes. The bare version string
  is no longer listed separately.
- ID3v2 encoder frames (`TSSE`, `TENC`) are now provenance tells, so an MP3 whose
  encoder wrote its name there (e.g. ffmpeg's `Lavf`) is identified.

## [0.38.0] - 2026-07-11

### Added

- Integrity widened to MP4/M4A: a duration-consistency check. The sample table
  (`stts`) sums to a media duration that must match the media header (`mdhd`); a
  mismatch means the header and the samples disagree -- a truncation or a re-mux
  that updated one and not the other. Pure timescale math, no codec knowledge,
  single-track. Surfaces in the `audit` INTEGRITY section. Verified: a healthy
  m4a is consistent; halving the `mdhd` duration is flagged (declared vs
  sample-table seconds).

## [0.37.0] - 2026-07-11

### Added

- Provenance depth: DAW/tool structural fingerprints and more converters. `audit`
  now identifies a writing tool from the signature chunks it leaves even when no
  encoder string is present -- Avid Pro Tools (`regn`/`minf`/`elm1`), Steinberg
  (`SMED`), the AFsp library, a SMPTE-UMID broadcast tool -- reported at "likely"
  (a structural tell, not a stamp). The encoder-string table gained the common
  rip/convert tools (Exact Audio Copy, dBpoweramp, XLD, foobar2000, fre:ac, SoX,
  GoldWave, Ocenaudio, TwistedWave, Serato, Traktor). Broadcast-Wave `bext`
  originator and CodingHistory already flow into provenance, so field-recorder
  and mastering chains are covered.

## [0.36.0] - 2026-07-11

### Added

- Integrity (effective bit depth / fake hi-res) now covers AIFF as well as WAV.
  AIFF is big-endian signed PCM read from the SSND chunk (past its offset and
  blockSize fields) with the declared depth from COMM; the same lowest-set-bit
  witness detects a 24-bit AIFF that is really upsampled 16-bit. AIFC is left
  alone (it may be compressed).

## [0.35.0] - 2026-07-11

### Added

- HIDDEN section in `acidcat audit`: concealed or appended data, split out from
  the general forensic findings and made actionable. Trailing blobs past the
  container, polyglots (an appended ZIP/PDF/PNG/etc), non-zero cavity content,
  FLAC APPLICATION data, and MP4 mdat coverage gaps are each reported with an
  exact `acidcat carve` command to extract the region. `audit` now gives the full
  four-part verdict -- STRUCTURE, INTEGRITY, HIDDEN, PROVENANCE -- with the rest
  of the anomaly findings under FORENSICS. `--json` splits `hidden` from
  `forensics`.

## [0.34.0] - 2026-07-11

### Added

- Integrity checks in `acidcat audit` (`core/integrity.py`): does the header match
  the audio. The anchor check is effective bit depth (fake hi-res): a file can
  declare 24-bit while every sample's low byte is zero, meaning it was upsampled
  from 16-bit. The witness is the PCM itself -- the lowest set bit across all
  samples tells how many bits actually carry data. Handles WAVE_FORMAT_EXTENSIBLE
  (resolves the real format tag from the sub-format GUID), skips float and
  compressed codecs and pure silence, and caps the read. A new INTEGRITY section
  in the audit report and `--json`.

## [0.33.0] - 2026-07-11

### Added

- Provenance fingerprinting in `acidcat audit` (`core/provenance.py`): identify
  the tool chain that wrote a file, with honest confidence levels that mirror the
  repair-witness discipline. Explicit version strings (FLAC/Ogg vendor, MP3
  LAME/encoder, WAV ISFT/software, bext originator, MP4 encoder) are canonicalized
  to a tool + version at "high" confidence; structural fingerprints (e.g.
  MuseScore's SF3 writer, revealed by Ogg-Vorbis samples and omitted RIFF padding)
  are reported at "likely" and never asserted as fact. A file can carry both -- an
  SF3 shows the original SoundFont editor from its string and MuseScore from its
  structure. `audit --json` includes the full signal list.

## [0.32.0] - 2026-07-11

### Added

- FLAC structural validate/repair/audit -- the third container grammar under the
  constraint model (a chain of metadata blocks, after the IFF tree and the MP4
  box). Two witnessed derived fields: the last-metadata-block flag (witnessed by
  the audio frame sync -- a flag set too early or missing is corrected) and
  PADDING body zero-fill (witnessed by the spec, the FLAC analog of the RIFF pad
  byte). Length-preserving; the audio frames are never touched. Fields that would
  need the audio decoded to witness them (STREAMINFO MD5/total-samples, seektable
  offsets) are out of scope -- acidcat bundles no FLAC decoder. `acidcat validate`
  now covers `.flac`, and `audit` reports its structure and encoder provenance.

## [0.31.0] - 2026-07-11

### Added

- `acidcat audit`: a read-only forensic verdict on a file, composing three views
  the constraint model and walkers already produce -- STRUCTURE (the derived-field
  violations `repair` would fix), FORENSICS (the anomaly detector's findings:
  polyglots, cavities, trailing data, high-entropy regions), and PROVENANCE (the
  writer/tool tells the file carries: encoder, software, vendor). `--json` for a
  machine-readable report.
- TUI validate/repair surface (`v`): the constraint model's face inside the
  interactive tool. `v` opens a panel listing every derived-field violation with
  the witness that makes it fixable; `r` applies the witnessed repairs to the
  working copy (unsaved until `ctrl+s`, original untouched). This completes the
  "surface it" phase: the model is now visible and usable interactively, not just
  from the CLI.

## [0.30.0] - 2026-07-11

### Added

- `acidcat validate`: read-only structural checking with an exit code. Runs the
  same analysis `repair` uses but writes nothing, over files or a whole directory
  tree, so it fits a CI check or a sweep to find broken files before they bite.
  Exit 0 when every checked file is consistent, 1 when any has a violation.
- The constraint framework (`core/constraints.py`, `core/repairers.py`): a
  `Violation` (a derived field disagreeing with its function) is now a first-class
  object carrying its witness and one of four kinds (size, offset, count, zero).
  Every verb is a move over violations -- `analyze` (read-only: validate) vs
  `apply` (fix: repair) -- and the IFF size cascade and MP4 offset rebuild are
  both expressed through the shared protocol, so the command layer is
  format-agnostic and adding a repairer wires it into both verbs at once.

### Changed

- `acidcat repair` now dispatches through the constraint framework; behavior is
  unchanged, and it reports a non-witnessed violation as "left as-is" rather than
  silently touching it.

## [0.29.0] - 2026-07-11

### Added

- `acidcat repair` extended beyond IFF to its first non-RIFF format and its
  first offset-kind fix: it rebuilds a broken MP4/M4A `stco`/`co64` chunk-offset
  table (the classic result of a re-mux or metadata insertion that moved `mdat`
  without patching the table, which a player hears as silence or a crash). The
  correct offsets are derived from `mdat`'s real position plus the sample sizes
  (`stsz`) and sample-to-chunk map (`stsc`) -- an independent witness, so it is a
  rebuild and not a guess. Conservative by design: single media track only, fires
  only when the stored table actually points outside `mdat`, and refuses unless
  the rebuilt table provably fits. The patch is length-preserving and never
  touches a byte of `mdat`. This is the second field kind (OFFSET) under the
  constraint model, the step that takes `repair` off the IFF grammar.

## [0.28.0] - 2026-07-11

### Added

- `acidcat repair`: fix structural inconsistencies in RIFF/WAVE, RF64, and
  AIFF/AIFC containers without touching a byte of audio. Recomputes stale
  container sizes (the common "riff_size says X, file is Y" left by a crash or
  a tool that appended without adjusting) and normalizes a non-zero pad byte,
  driven by a new generic IFF structural model (`core/structure.py`). Data
  appended past the container is preserved; the audio payload is compared
  before and after as a hard guard. Sits on the same `writer.commit` backup +
  atomic + read-back-verify sink as `write`. `--dry-run`, `-o`, `--keep-pad`.
- `core/structure.py`: an IFF container model whose bedrock invariant is
  byte-exact round-trip (validated on 2,358 real corpus files, zero false
  positives). This is the first piece of the declarative-structure direction:
  write and repair become one operation, re-satisfying the size cascade after
  a mutation.

## [0.27.0] - 2026-07-11

### Added

- SoundFont 3 (.sf3) support: MuseScore's Ogg-Vorbis-compressed soundfont.
  `acidcat inspect` maps every sample as a carveable Ogg stream (byte range in
  smpl), and `acidcat convert font.sf3` extracts each sample as a playable
  `.ogg` (decoding Vorbis to PCM needs a codec acidcat does not bundle). Same
  sfbk RIFF as SF2, with shdr start/end repurposed as byte offsets and
  sample-type bit 0x10 marking compression; the chunk walker now tolerates the
  MuseScore writer's omitted RIFF pad bytes.
- Tracker-module support: `acidcat inspect` maps ProTracker MOD, FastTracker
  II XM, and Impulse Tracker IT down to the byte offset of every embedded
  sample, so each sample is a carveable region (`carve --offset`). Header
  fields, pattern order, and per-sample descriptors are all decoded.
- Pointer (xref) annotation extended to three more pointer-table structures,
  all followable in the TUI with `x` and bounds-checked for dangling targets:
  - IT on-disk offset tables (instrument/sample/pattern pointers) and each
    IMPS sample header's SamplePointer, a two-level pointer chain.
  - MP4/ISO-BMFF `stco` / `co64` chunk-offset boxes; entries pointing past
    end-of-file (a re-muxed or truncated `mdat` tell) are counted and warned.
  - WAV `cue ` markers resolved from sample-frame index to a byte offset in
    the data chunk (`data_off + frame*block_align`) for uncompressed PCM.

## [0.26.0] - 2026-07-11

### Added

- SoundFont 2 (.sf2) support: `acidcat inspect font.sf2` shows the font
  metadata and every named sample (rate, duration, loop) with its real byte
  offset, and `acidcat convert font.sf2` extracts all samples to a folder of
  WAVs. Open, uncompressed sampler format -- the first SoundFont support.
- TUI byte map (`m`): where the file's bytes actually go, top-level regions
  biggest first with a proportional bar, unaccounted bytes called out.
- TUI pointer navigation (`x`): follow a pointer field to its target and flag
  a dangling (out-of-bounds) one. FLAC SEEKTABLE points are wired up, with an
  out-of-bounds seek offset also warned at inspect.
- TUI pending-changes diff (`d`): review every changed byte region (offset,
  old->new) between the working copy and the original before saving.
- FLAC: a metadata-like block after the last-metadata-block flag is flagged as
  data hidden past the block table; WAV: an implausible sample rate or channel
  count (structurally valid, physically impossible) is flagged.

### Changed

- TUI edits scale to large files: undo/redo store a minimal byte-range delta
  instead of a whole-file snapshot, and the dirty check no longer does a
  whole-file compare on every edit. App-global shortcuts are disabled while a
  modal is open.

### Fixed

- `inspect` on a VBR MP3 no longer walks every frame when the Xing/VBRI header
  already carries the frame count; the walk (and its cross-check) is a
  `--frames`/deep diagnostic now.

## [0.25.0] - 2026-07-10

### Added

- `acidcat carve` -- extract a structurally-identified byte range to a file or
  stdout. `--offset X [--length N | --end Y]` for an explicit range (any
  format), `--trailing` for the blob past the declared container end (the
  appended data a polyglot finding flags; RF64-sentinel aware), `--chunk ID`
  for a RIFF/AIFF chunk payload. Read-only on the source. The general
  extraction primitive behind sample and blob carving.
- `acidcat convert FILE.ncw` -- decode Native Instruments' NCW (Kontakt's
  lossless codec: DPCM + bit-truncation + mid/side) to WAV, and
  `acidcat convert DIR` to batch-convert a whole library (recursive,
  `--skip-existing`). NCW is compression, not access control -- no key,
  nothing bypassed. Verified against the public reference decoder and real
  Kontakt samples, with the bit-unpacking proven invertible and ground-truth
  round-trip tests for every mode.
- TUI navigation: `g` goto-offset, `/` search (fzf-style over field
  names/values, or `0x..`/`"ascii"` raw-byte search), `n`/`N` to cycle,
  `f` jump-to-forensics-finding, `y` yank hex to clipboard, `ctrl+r` redo.
  The forensics panel is now numbered, has a severity legend, and scrolls
  (findings past the eighth were previously unreachable).

### Fixed

- Forensics: RF64/BW64 files silently skipped the trailing-data and
  appended-magic scans -- the `0xFFFFFFFF` sentinel size made the container
  end compute as ~4.29 GB. The true end is now resolved from the `ds64`
  chunk, so a PDF/PNG/ELF appended to an RF64 is detected.
- TUI: pressing edit on an MP3 bitrate/sample_rate field crashed the app
  (a missing import); a malformed file crashed the session on open (only
  one exception type was caught); the stale-source save prompt named the
  wrong key (`s` strips, the force-save is `ctrl+s`).

### Changed

- New forensic tells, each calibrated to zero false positives across the
  local corpus: non-zero odd-chunk pad bytes (a covert channel), duplicate
  structural chunks, APEv2 tags on non-MP3 files, and byte-entropy
  characterization of cavities ("entropy X.X/8, encrypted or compressed
  payload") so a hidden ciphertext blob reads differently from benign
  metadata.
- `info` and `scan` now decode through the inspect walkers like `index`
  already did, completing the one-decoder-per-format unification; the dead
  legacy wrapper functions and imports were removed.

## [0.24.0] - 2026-07-10

### Changed

- Library indexing now decodes each file once. The scan-row extraction for
  WAV, AIFF, MIDI, and Serum is driven by the inspect walkers (via a shared
  `ctx` dict) instead of a second parser re-reading the same bytes, ending
  the double-maintenance that let the two paths drift (the `smpl` signedness
  bug in 0.22.0 had to be fixed in both). Verified row-identical to the
  previous extractor across the local corpus (2,328 WAV, 270 MIDI, 85 Serum,
  4 AIFF). Tagged audio (mp3/flac/ogg/m4a) intentionally stays on mutagen,
  which owns the on-disk tag spec.
- MIDI key signatures resolve through one shared `key_signature_name`
  helper, so the inspector and the library index can no longer disagree on a
  key: the structural view now shows the real key name ("D", "Bm") where it
  previously showed the raw signature ("+2 sharps").

### Fixed

- A MIDI file with no tempo event no longer stores a duration derived from
  the assumed 120 bpm default in the library index (it would be a wrong
  number to filter on); the inspector still shows the estimate, clearly
  labeled.

## [0.23.0] - 2026-07-10

### Added

- MP4 `stsd` descent: sample entries and their codec-config boxes join the
  box tree. The esds descriptor chain decodes down to the AAC
  AudioSpecificConfig (object type, frequency index including the 24-bit
  escape, channel configuration, SBR/PS extension rate), so the codec line
  names the exact profile ("AAC LC", "SBR (HE-AAC)") instead of "AAC".
  ALAC magic cookies and dOps decode fully; QuickTime `wave` wrappers are
  flattened; freeform `----` atoms (Serato, MusicBrainz, iTunNORM) surface
  as namespace:name tags (#56).
- WAV `fmt ` extension decode per format tag: MS ADPCM samples-per-block
  and predictor coefficients (the standard 7-pair set recognized), IMA
  ADPCM samples-per-block, MPEGLAYER3WAVEFORMAT fields; cue points show
  play order and compressed-data chunk/block starts (#56).
- MIDI wall-clock duration on the MThd chunk, tempo-independent for SMPTE
  division and honestly annotated for PPQ (approximation on tempo changes,
  the SMF-default-120 case called out) (#56).
- FLAC SEEKTABLE points listed (sample @ +offset, frame samples), with
  placeholders counted; AIFF COMT timestamps rendered from the 1904 Mac
  epoch and FVER decoded; MP3 VBRI encoder delay, quality, and seek-TOC
  geometry (#56).
- Free-format MP3 (bitrate index 0): the constant frame length is measured
  from sync spacing, the derived bitrate reported, and a lone free-format
  sync without a matching twin is treated as a false sync. New fixture
  specimen at ~91.9 kbps, a rate no table entry can express (#58).

### Changed

- The field value/bytes codec engine moved from the TUI into
  `core/fieldcodec.py` with no behavior change; it no longer requires the
  `[tui]` extra, and the codec test suite runs on a bare install (#57).

### Fixed

- Capped file reads are clamped to the file size: `read(N)` pre-allocates
  the full N-byte buffer, so the 256 MB MIDI read cap cost ~50 ms per file
  regardless of size. Library scans are ~19x faster (measured 17.7 s to
  0.91 s over a 2,615-file corpus) (#59).

## [0.22.0] - 2026-07-10

### Fixed

- A crafted `.nksf` MessagePack header claiming ~4 billion array/map elements
  could hang `inspect` and unattended `index` scans and exhaust memory; forged
  counts are now rejected and degrade to the normal warning path (#53).
- ID3v2 COMM/USLT (and v2.2 COM/ULT) frames decode to their text instead of a
  byte count; v2.3/v2.4 per-frame format flags are honored, so group ids,
  data-length indicators, and per-frame unsynchronisation no longer corrupt
  the payload decode, and compressed/encrypted frames are labeled rather than
  rendered as garbage; numeric TCON genre references resolve against the
  ID3v1 table (#54).
- MP4 `gnre` genre atoms (how older iTunes stored genre) resolve to the genre
  name instead of raw bytes; QuickTime version-2 audio sample entries report
  their real channel count and sample rate instead of v0-offset constants (#54).
- Ogg Opus durations subtract the pre-skip priming samples and report the
  48 kHz decode rate (the encoder input rate is shown separately); duration
  is scoped to the first logical bitstream, and chained/muxed files warn (#54).
- WAV `smpl` SMPTE fields and `inst` base note are read unsigned per spec (#54).
- Writes are read back from disk and verified before "saved" is reported; a
  commit failure (locked file, full disk) prints a per-file error instead of
  a traceback; a pre-existing `_original` file is reported as "existing
  backup kept" rather than passing silently as a fresh backup (#53).
- The TUI refuses to save over a source file that changed on disk since it
  was opened (press save again to force), so external edits are never
  silently clobbered and the first-save backup always captures the bytes
  that were actually being edited (#53).
- Tag edits and strips on MP3/FLAC/OGG/Opus/M4A (including cover-art
  changes) now verify the audio payload survived the rewrite, matching the
  guarantee WAV/AIFF edits already had (#55).
- A file truncated inside the MIDI MThd header degrades to a warning instead
  of a parse error (#53).

## [0.21.0] - 2026-07-10

### Added

- `acidcat tui` (new `[tui]` extra): an interactive terminal inspector and
  byte-level metadata editor built on textual, imported lazily so the core
  stays dependency-light. Chunk/field/row tree with a hex pane and forensics
  panel, a file browser, a metadata form, and a layered field editor:
  variable-length text fields route through the write engine (so lengths can
  change); numeric fields value-edit via a verified encoding (struct formats,
  ID3 synchsafe, AIFF 80-bit float, u24be, bit-packed fields, enum bit-fields
  editable by name, and context-dependent enums such as MP3 bitrate and
  sample rate, whose value tables depend on the version/layer bits); anything
  else hex-edits in place. Every walker-declared encoding is trusted only
  after it re-encodes to the field's actual on-disk bytes, so a wrong
  annotation can never write blind. All edits apply to a temp working copy;
  nothing touches the original until ctrl+s, which makes a pristine
  `_original` backup. Undo, unsaved-changes prompts, a help overlay, and
  per-field editability hints. The cursor and expansion state survive the
  tree rebuild after each edit.
- `write --strip` (and the TUI `s` key): remove identifying metadata (WAV
  LIST/bext/iXML/cart/ID3/XMP, AIFF NAME/AUTH/ANNO/copyright/ID3/APPL, all
  tags on MP3/FLAC/OGG/M4A, Vital author/comments) while preserving the audio
  byte-for-byte, verified after the rewrite.
- Walker coverage pass: formatted and bit-packed fields across WAV, MP3,
  AIFF, FLAC, MIDI, RX2, and FXP carry verified encoding annotations;
  composite fields (MP3 gapless and replay_gain, AIFF AESD channel status,
  WAV smpl loops) split into editable subfields; FLAC STREAMINFO is fully
  value-editable via a read-modify-write that preserves neighbouring
  bit-fields.

### Changed

- MP3 inspect output decodes more of the frame header in place: `version`
  and `layer` are their own fields, `crc_protected` reports
  `protected`/`unprotected` instead of a boolean, and
  `bitrate`/`sample_rate`/`channel_mode`/`emphasis` carry the header word's
  real offset and length instead of null. New fields: LAME
  `replay_gain_type`/`replay_gain_sign`/`replay_gain_mag`,
  `encoder_delay`/`encoder_padding`, AIFF `aes_*` status subfields, and WAV
  `loop[N]_type`. Consumers parsing inspect JSON for MP3 will see the new
  shape.

### Fixed

- inspect JSON (plain and `--full`) no longer leaks the editor-only
  `enc`/`raw` field keys; the field-level `raw` also collided with the
  chunk-level `raw` hex bytes that `--full` emits.

## [0.20.0] - 2026-07-08

### Changed

- Per-library schema bumped to v3. `samples` gains an explicit `id INTEGER
  PRIMARY KEY` (a VACUUM-stable rowid alias) and the `samples_fts` mirror is
  re-keyed to it. The per-path FTS refresh now deletes by rowid (one index
  lookup) instead of `DELETE ... WHERE path = ?`, which scanned the whole FTS
  index for a matching column, making a `--force` full rebuild O(n^2). Existing
  v1/v2 DBs migrate in place on first open, inside the single atomic
  transaction from 0.19.1; an interruption rolls back to the prior version.
  Benchmarked on a 32k-row library: full FTS rebuild dropped from minutes to
  under a second; migration is a one-time ~1s pass.
- `find_similar` similarity is now meaningful, not a near-1.0 cluster. It scores
  a fixed timbral/rhythmic vector (core/features.py `FEATURE_KEYS`) and
  **z-standardizes each dimension across the candidate population** before the
  cosine, so the small-magnitude timbral dims are no longer buried by the
  10^3-10^6 spectral ones. The vector excludes non-sonic scale fields
  (sample_rate, audio_length_samples, duration, beat_count).
- Feature vectors are stored as a packed float32 BLOB (`features.feature_vec`,
  schema v3) so `find_similar` unpacks them directly instead of JSON-parsing
  every candidate. Scoring is numpy-vectorized when the analysis extra is
  present and falls back to an identical pure-Python path otherwise, so scoring
  a shared index needs no numpy. Existing feature rows are backfilled from their
  stored JSON during the v3 migration (no librosa re-extraction).

## [0.19.1] - 2026-07-07

### Added

- Interactive anatomy datasheets for the newer formats: Bitwig wavetable (`.wt`),
  Bitwig multisample, VST FXP, ReCycle RX2, and RMID
  (`docs/formats/*-anatomy.html`).

### Changed

- Anatomy datasheets: the colour legend moved from prose ("green is a value,
  clay is a flag, ...") into the panel beside the lede as a visual key, a
  dark/light box pair per field kind (the selected and resting appearance)
  naming the colour and its meaning, showing only the kinds each page uses. The
  five new pages carry distinct per-format accents.

## [0.19.0] - 2026-07-07

### Added

- `query --compatible-with FILE`: find samples that mix with a reference
  (harmonic key via the Camelot wheel + compatible tempo including
  half/double-time) from the CLI, matching the MCP `find_compatible` tool.
  Reads the reference's key/BPM/kind from the index or by parsing the file.
- `inspect` walks Bitwig wavetable `.wt` files (the `vawt` container Bitwig
  writes from Polymer and other wavetable devices): frame count, samples per
  single-cycle wave, and the 16-bit sample block. Reverse-engineered and
  documented in docs/formats/bitwig-wt.md.
- Format reference docs (docs/formats/) for the previously-undocumented native
  walkers: RF64/BW64, RMID, VST FXP, and ReCycle RX2.
- `inspect` walks Bitwig `.multisample` files (a ZIP with a `multisample.xml`
  zone map plus member samples): per-zone file, root note, key/velocity range,
  and loop. Content-sniffed by peeking the zip for `multisample.xml`. Reads
  entries by seeking past the local header, since Bitwig writes a mismatched
  CRC. stdlib only (zipfile + xml.etree), no new dependency. See
  docs/formats/bitwig-multisample.md.

### Changed

- MCP server: a process-lifetime read-connection cache (keyed by db_path,
  opened `check_same_thread=False`, every use serialized under a lock)
  replaces re-opening every library DB on each tool call. A warm fan-out
  query dropped from ~20ms to ~2ms across 18 libraries. Scoped queries now
  open only the in-scope libraries (was: open all, discard the rest). WAL
  means cached readers see committed writes; the cache is evicted on
  register/forget/reindex and revalidated on borrow. Thread-safe if tool
  dispatch ever moves off the event-loop thread.
- The filter SQL (bpm/duration/key/format/device/category/creator/product/
  tags/text) is now built once in `core/query_sql.py`, shared by the CLI
  `query` and the MCP `search_samples` tool instead of two drifting copies.
- The compatible-sample engine (`find_compatible` + `infer_kind`) moved into
  `core/search.py`, shared by the MCP tool and the new CLI command. The MCP
  `find_compatible` gained half/double-time matching (`half_double`) and a
  per-result compatibility note; keyless references now match only keyless
  samples on both surfaces. Key matching is spelling-robust (normalized via
  camelot), so 'A minor' and 'Am' match.
- Index/DB tuning: the case-insensitive filters (`key`/`format`/`device`/
  `category`/`creator`/`product`) now hit `LOWER()`-expression indexes instead
  of a full scan (a per-filter lookup on a 32k-row library dropped from ~5.5ms
  to ~0.02ms); read connections are tuned (`synchronous=NORMAL` under WAL,
  larger page cache + mmap + in-memory temp store); `PRAGMA optimize` runs after
  a walk so index choices are stats-driven. Additive and idempotent, no
  schema-version bump; existing libraries pick the indexes up on the next index.
- MCP: tool-execution failures now return a `CallToolResult` with `isError: true`
  (and a handler that returns an `{"error": ...}` dict is flagged the same way),
  so clients and the model see errors as errors, not as a successful payload that
  happens to contain an error string.
- MCP: successful tool calls now return `structuredContent` (the machine-readable
  result object) alongside the JSON text block, and every tool advertises a
  human-readable `title`. Previously-bare input fields gained descriptions.

### Fixed

- Index schema migration is now atomic and re-entry-safe: the whole step runs
  in one transaction that rolls back on error, and each `ADD COLUMN` is guarded
  against a pre-existing column, so a migration interrupted midway can no longer
  wedge a DB with `duplicate column name` on the next open.

## [0.18.0] - 2026-07-06

### Added

- `inspect` walks RMID (RIFF-wrapped MIDI): reports the RIFF wrapper and hands
  the inner Standard MIDI File to the MIDI walker (offsets shifted into place),
  so the MThd/MTrk detail shows through.
- `inspect` decodes the WAV `cart` chunk (AES46 radio automation: title, artist,
  cut id, category, start/end, producer app, level reference, post-timers, url)
  and the `iXML` chunk (field-recorder metadata: project, scene, take, tape,
  note, track count), previously shown as unparsed.

## [0.17.0] - 2026-07-04

### Added

- `inspect` walks VST2 `.fxp` presets: the `CcnK` container, its preset kind
  (`FxCk`/`FPCh`), the plugin id (a FourCC, e.g. `XfsX` = Serum), version fields,
  the preset name, and the opaque plugin chunk as a region.
- `inspect` walks Propellerhead ReCycle `.rx2` loops: the `CAT`/`REX2` IFF
  chunk tree, the creator string, and the slice count (recursing into the
  nested slice-list group).

### Changed

- Internal: the library-indexing engine and the discovery helpers moved from
  `commands/index.py` into a new `core/indexing.py`; `mcp_server.py` no longer
  imports any command internals (core never imports commands). Pure relocation,
  no behavior change.

## [0.16.0] - 2026-07-04

### Added

- `inspect --anomalies` flags an Ogg file carrying more than one logical
  bitstream (multiple BOS serials), several codecs multiplexed into one file,
  where a single-codec player surfaces only one and the others ride along hidden.
- `inspect` decodes the manufacturer id of MIDI SysEx events, and `--anomalies`
  warns when a SysEx uses the non-commercial id 0x7D (no synth acts on it) or
  carries an oversized payload, a MIDI payload-cavity tell.
- `inspect --anomalies` flags non-zero content in a RIFF JUNK/PAD chunk (spec'd
  as ignorable padding, and the RF64/BW64 ds64 placeholder), a WAV cavity.
- `inspect --anomalies` flags an MP4/M4A `mdat` coverage gap: bytes inside `mdat`
  that no `stsz` sample references (a payload grown onto the box's tail while the
  sample tables still validate), a container cavity most tools miss.
- `inspect --anomalies` flags non-zero bytes in an ID3v2 tag's padding region
  (after the last frame, within the declared tag size), a cavity, not trailing data.
- `inspect --anomalies` flags dual-endianness 16-bit PCM: audio engineered so
  both the little- and big-endian readings are structured (a WAV/AIFF twin that
  plays a different sound each way). Real audio is structured only one way.

### Fixed

- Ogg files now report `duration`, computed from the last page's granule
  position (Opus granules are counted at 48 kHz). Was previously absent.
- Every ID3 `T***` text frame now decodes to its value; frames outside a
  hardcoded set (e.g. `TPE2` album artist, `TCOM` composer) previously showed
  as a raw byte count. All `T***` frames share the same text structure per spec.
- MP3 duration is now the gapless/playable length: the LAME encoder delay and
  padding are subtracted (was ~48 ms long): the standard
  encoder-delay-adjusted sample count, matching ffprobe.
- MP4/M4A `trkn` and `disk` atoms decode to `index/total` (or `index`) instead
  of a raw byte count.

### Changed

- Docs: describe acidcat on its own terms (dropped the tool comparisons from
  the README tagline, package description, and command help); refreshed a tight,
  current SECURITY.md.

## [0.15.0] - 2026-07-03

### Added

- `acidcat explore FILE [-o out.html]` builds the standalone interactive HTML
  byte-explorer as a first-class command. The explorer (previously the
  unpackaged repo-root `build_explorer.py`) now ships inside the package; the
  root script stays as a back-compat shim for the `inspect --full | ...` pipe.

### Fixed

- MessagePack codec in `core/ni.py` (reads/writes `.nksf`): the decoder now
  handles the full int / uint / float / bin family and map32/array32, a real
  `.nksf` with any integer field above 127 previously failed to read. The
  encoder emits correct signed and 64-bit ints, floats, and str32 (it had
  wrapped negatives to unsigned and truncated large values). Adds test_ni.py
  covering the codec, FastLZ, and the hsin walker.

- The "unsupported file" error and `inspect` help now list Ogg and Native
  Instruments (they were supported but omitted from the message).

### Changed

- Internal: the thirteen format walkers moved out of `commands/inspect.py`
  (2,878 lines) into `core/walk/*` behind a registry, with a canonical format
  sniffer in `core/sniff.py`; `commands/inspect.py` (~400 lines) now holds only
  rendering, selection, and the CLI. Adding a format is one magic + one walker +
  one registry entry. No behavior change: byte-for-byte identical `inspect`
  output (verified across formats and output modes), and the suite stayed green
  at every step.

## [0.14.1] - 2026-07-03

### Added

- `inspect --anomalies` detects an appended ZIP on ANY format via a universal
  end-of-central-directory scan near EOF, not just containers with a total-size
  header. Catches mp3/flac/ogg polyglots the size-based trailing check missed.

### Fixed

- `inspect --anomalies` no longer raises a false "possible LSB-stego" alert on
  ordinary recordings. A uniformly high low-bit-entropy floor is consistent with
  an embedded payload but equally with a mic/preamp noise floor, dither, or a
  high-bit-depth capture (real TASCAM field recordings tripped it). It is now a
  NOTICE describing the entropy, not an alert claiming stego; entropy alone
  cannot separate the cases (a sample-pair/chi-square test is the future path).

## [0.14.0] - 2026-07-03

### Added

- `inspect --anomalies`: a forensic scan that flags trailing data past the
  declared container end, appended-format magic (polyglot detection: ZIP/PDF/
  PNG/... after the audio), structural size mismatches (surfaced from the
  walker), and control bytes smuggled into text fields. Findings carry a
  severity, byte offset, and rule; also emitted in `-f json`. Also flags duplicate ID3 frames, non-zero content in spec-ignorable padding/free regions, and FLAC APPLICATION blocks.
- `cover` command: extract, embed, or remove embedded cover art across MP3,
  FLAC, MP4/M4A, and Ogg (`acidcat cover FILE -o art.jpg`, `--set art.png`,
  `--remove`); embed/remove are atomic with a `_original` backup.
- Custom ID3 frames in `write`: `--set txxx:NAME=value` (and `wxxx:NAME=url`)
  set user-defined frames; on FLAC/Ogg the name becomes a Vorbis comment, on
  M4A a freeform atom. `inspect` decodes TXXX/WXXX as `description = value`.
- LSB-steganography detection: `--anomalies` computes the per-window entropy of
  the low bit-plane of PCM WAV samples and flags a uniform-high floor (the tell
  of an encrypted hidden payload; natural audio dips low in quiet passages).
  `inspect --full` emits the entropy map and `build_explorer.py` renders it as a
  color heat-map in the byte explorer.

### Fixed

- MP4/M4A: a large `mdat` (or any box) whose contents extend past the inspector's
  read window was wrongly flagged as overrunning its parent. Box sizes are now
  reconciled against the real file size, so a valid large box reads as "content
  beyond read window", not an error. (Found by `--anomalies` on a real ALAC file.)

## [0.13.0] - 2026-07-03

### Added

- MCP: a **streamable-HTTP transport** (`acidcat-mcp --transport http`, mounted at
  /mcp, stateless), the modern replacement for SSE; stdio stays the default. New
  `mcp-http` extra (starlette + uvicorn).
- A distributable **Claude skill** under `skills/acidcat/` (copy to
  `~/.claude/skills`) covering inspect/write/convert/index/query, build_explorer,
  and the MCP server.
- Ogg: `inspect` now decodes the identification header, reporting `sample_rate`
  and `channels` (previously only the comment header was read).

### Fixed

- `info` on a Bitwig/NI/Vital preset silently parsed it as a headerless WAV; it
  now detects presets and points to `acidcat inspect`.
- build_explorer: the hover highlight holds briefly so dragging across the gaps
  between byte cells reads as continuous; the dark/light toggle applies on load
  without pinning the OS preference in localStorage.
- Docs: README overview + Supported Formats table now list Bitwig/NI/Vital/NCW/
  MP4; CHEATSHEET documents `--verbose` and the `mcp-http` extra. Removed an
  unreachable code branch in `write`.

## [0.12.0] - 2026-07-03

### Added

- Native Instruments preset support (`inspect`): the NISound `hsin` container
  (Massive `.nmsv`, Absynth `.nabs`, FM8, Reaktor, modern Kontakt `.nki`), the
  older `.ksd` (KORE/Absynth, zlib+XML), and `.nksf` (NKS, RIFF+MessagePack).
  Reads product, name, author, vendor, category, tags; `--verbose` FastLZ-
  decompresses the hsin subtree. Pure Python, from byte-level facts only.
- Ogg (`inspect`): page structure + the Vorbis/Opus comment header (vendor,
  tags), bounds-checked.
- Bitwig deep deconstruction (`inspect --verbose`): the device/module tree, the
  named parameter table with values, the Grid wiring paths, the reference graph,
  and the embedded-asset zip unzipped with each file identified. `.bwclip` note
  clips report bpm + beat length and read every note (pitch / position /
  duration / velocity), reverse-engineered via a known-plaintext attack.
- Vital deep deconstruction: oscillators + wavetables, LFO inventory, effects
  chain, and the modulation matrix (source -> destination with amounts).
- AIFF: decode the embedded ID3v2 chunk (as bandcamp and some tools write it).
- `convert` command: export a DAW clip's notes to a Standard MIDI File
  (`acidcat convert clip.bwclip -o out.mid`).
- `write` command: edit metadata in place (exiftool-style) after a `_original`
  backup, or a `-o` copy; `--dry-run` and batch. Covers WAV (INFO tags, acid
  bpm/key, bext, smpl root), AIFF (NAME/AUTH/ANNO), MP3/FLAC/OGG/M4A (via
  mutagen), and Vital presets. Atomic writes; refuses RF64/malformed; verifies
  audio is byte-identical after a WAV rewrite. (Bitwig/NI preset writing is
  implemented but held as experimental pending in-app reload verification.)
- Index / query / MCP: synth/DAW preset metadata (device, product, creator,
  category, tags) is indexed and searchable (`query --device/--category/
  --creator/--product`, full-text, and the MCP `search_samples` tool). Schema v2
  with a safe v1 -> v2 migration.
- `inspect --pretty` (human-friendly metadata view) and `--verbose` (deep
  deconstruction). CHEATSHEET.md.

### Changed

- MIDI note names now use the DAW octave convention (middle C = C3), matching
  Bitwig / Ableton / FL / Cubase / Logic (previously scientific, C4).

### Fixed

- inspect decoded WAV/AIFF text metadata (INFO tags, NAME/AUTH/ANNO, comments,
  MIDI text events) as ASCII with errors='replace', mangling every non-Latin tag
  (Korean, CJK, mixed-script) into U+FFFD. Now decodes UTF-8 with a latin-1
  fallback, so non-ASCII metadata displays correctly.
- Hardening from two adversarial pre-release reviews of the new code: bounded the
  Bitwig note/parameter/path/wiring scanners and the NI .ksd / FastLZ-subtree
  scanners (a crafted preset could force quadratic or multi-second scans); capped
  the embedded-zip asset reader to a per-entry prefix (zip-bomb memory guard);
  guarded the note reader against NaN/Inf fields and an out-of-range pitch footer;
  added a recursion-depth limit to the hsin walker; gave `convert` proper error
  handling. Write path: RIFF/AIFF no longer fold trailing bytes into the container
  size, and the tagged-file editor fsyncs its temp file before re-reading it.

## [0.11.0] - 2026-07-02

### Added

- New format walkers for `inspect`, all pure-Python and bounds/DoS-hardened:
  - MP4/M4A (ISO-BMFF): walks the box tree (bounds- and depth-checked) and
    decodes ftyp brands, movie duration, the audio codec (AAC / Apple Lossless /
    Opus / ...) with channels and rate from stsd, and the iTunes metadata under
    udta > meta > ilst (title, artist, album, bpm, cover-art, ...). moov is
    found even when it sits at the end of a non-faststart file.
  - Bitwig `.bwpreset` / `.bwclip`: the BtWg tagged meta block (device, creator,
    category, tags, description, version) plus a note for any embedded-asset zip.
  - Vital `.vital`: the bare-JSON preset metadata (preset_name, author, comments,
    style, synth_version, macros).
  - NI Compressed Wave `.ncw` (Kontakt samples): header audio parameters
    (channels, bits, sample rate, sample count, duration).
- Bitwig WAV bounces: the `BWBM` beat-map chunk (beats, duration, derived bpm)
  and the `IBPM` tempo tag are decoded.
- `inspect --pretty`: a human-friendly view of the decoded tags and metadata
  (no byte offsets), for presets and tagged files.

## [0.10.1] - 2026-07-02

### Fixed

- The CLI forces UTF-8 output. acidcat printed decoded tags with the platform
  default stdout encoding, so on Windows or any non-UTF-8 locale (or a
  redirected pipe) a file with non-Latin metadata (a Korean artist tag, for
  example) raised an uncaught UnicodeEncodeError. stdout and stderr are now
  reconfigured to UTF-8 with errors=replace at CLI entry.

## [0.10.0] - 2026-07-02

### Added

- `inspect` accepts multiple files. With more than one, each is printed under a
  readelf-style `File:` banner and JSON output becomes NDJSON (one record per
  line). A missing or undecodable file is reported to stderr and skipped, and
  the exit code reflects any failure. A broken downstream pipe exits quietly.
- `inspect --only` / `--exclude` select or drop chunks by id (comma-separated,
  case-insensitive). Composing `--only NAME --hex` gives a focused hexdump.
- `inspect --full` emits a self-contained structural dump (implies JSON): each
  chunk with its raw region bytes and every field's absolute byte offset.
- `build_explorer.py`, a standalone script that renders a `--full` dump to a
  self-contained interactive HTML byte explorer (a hex grid with the decoded
  fields tinted over the bytes).
- Native decode of many previously-opaque structures: WAV `fmt ` extensible
  (sub-format GUID, `channel_mask` speaker names, `cbSize`) and `bext` v1/v2
  (UMID, EBU R128 loudness, coding history); the RF64 `ds64` size-override
  table; FLAC CUESHEET; AIFF COMT/AESD/APPL; MP3 ID3v2.2 frames, VBRI headers,
  LAME replay-gain and bitrate, and full ID3v1.1 with the standard genre table;
  MIDI SMPTE-offset meta events.

### Fixed

- `inspect --hex` read the wrong bytes for FLAC, MP3, and Serum, which do not
  share the RIFF 8-byte chunk-header layout. Each chunk now carries a payload
  base, so `--hex` and the `--full` byte ranges are correct across every format.
- ID3v2 unsynchronisation is de-escaped for v2.2/v2.3 before frame sizes are
  read (v2.4 per-frame unsync is left intact); the extended header is skipped
  rather than misread as the first frame.
- The Xing/Info side-info offset accounts for the two CRC bytes present on a
  CRC-protected MPEG frame.

### Changed

- Unrecognized arguments print the chosen subcommand's usage rather than the
  top-level usage.

## [0.9.7] - 2026-07-02

### Fixed

- `inspect` no longer false-warns `frame 'APIC' size N overruns tag` on MP3s
  whose ID3v2 tag carries embedded cover art. The frame-overrun check compared
  each frame against the 64 KB read buffer rather than the tag's declared size,
  so any tag with art larger than 64 KB tripped the warning and stopped
  enumerating frames early. The tag is now read up to a 16 MB cap and overrun
  is tested against the declared tag size; a frame that genuinely exceeds the
  tag still warns.
- `inspect --color` renders de-emphasized text (offsets, notes, table headers)
  as bright-black instead of the faint attribute. Terminals implement faint by
  blending the foreground toward the background, which turned muddy on any
  non-black terminal background; bright-black is a palette slot the theme
  defines, so it stays legible everywhere.

## [0.9.6] - 2026-07-02

### Fixed

- `inspect` no longer crashes on three malformed inputs that reached a read
  past a buffer: a truncated MP3 Xing header (uncaught `struct.error`), a
  deeply nested Serum preset (uncaught `RecursionError`), and an `MThd` shorter
  than 6 bytes under `--hex`. Each now degrades to a warning.
- Key detection corrected. A minor MIDI key signature now names the relative
  minor (an A-minor signature reports `Am`, not `Cm`), and filename key parsing
  accepts flats (`Eb minor` becomes `D#m`) and the capital-M major marker
  (`F#M` becomes `F#`). This flows into `info`, the index, and Camelot matching.
- RF64 duration: the `fact` chunk's `0xFFFFFFFF` sentinel is resolved through
  the `ds64` 64-bit sample count instead of being taken literally (which
  reported durations of tens of thousands of seconds).
- MP3 frames carrying a LAME `Info` tag are labeled CBR, not VBR; only a `Xing`
  tag denotes VBR.
- `acid` chunks padded beyond 24 bytes are decoded instead of silently dropping
  BPM and beats.
- `info` renders a SMPTE MIDI division as frames-per-second and ticks-per-frame
  rather than a meaningless "ticks/beat".
- AIFF sample rates whose 80-bit extended value is non-finite are treated as
  unset instead of degrading the COMM chunk to a parse error.

### Changed

- Format sniffing in `index` recognizes all MPEG audio layers and versions (it
  previously matched Layer III only), reusing the frame-header validator that
  also rejects ADTS AAC.
- Hardening: `inspect` lints an RF64 `ds64` data size larger than the file,
  validates FLAC PICTURE string lengths before slicing, and caps the MIDI
  whole-file read at 256 MB.

## [0.9.5] - 2026-07-01

### Fixed

- `inspect` no longer dispatches an ID3-wrapped non-MP3 container as MP3. A file
  that opens with an ID3v2 tag is treated as MP3 only when the tag does not wrap
  a RIFF/AIFF/FLAC/MIDI container; otherwise it is cleanly rejected instead of
  emitting bogus "no MPEG frame" warnings.
- `inspect` flags an AIFF COMM `num_sample_frames` that implies more audio than
  the file holds (its duration is then untrustworthy), gated to uncompressed so
  AIFC packet counts are not false-flagged.
- `inspect` flags an SSND `offset` that exceeds the chunk payload, which
  previously degraded silently to a reported 0 bytes.

### Changed

- SECURITY.md documents that acidcat performs no eval/exec/deserialization/
  subprocess on parsed content, closing the metadata-reader code-injection class
  (e.g. CVE-2021-22204 in another tool).
- Format-anatomy pages normalized to a single background palette.

## [0.9.4] - 2026-06-29

### Fixed

- `inspect` derives WAV duration from the `fact` chunk's sample count for
  non-PCM audio instead of `bytes / block_align`. ADPCM packs many samples
  per block, so a data chunk previously reported ~0.000 s; it now reports the
  true duration. The PCM and overrun paths are unchanged.
- `inspect` labels AIFC compressed duration as approximate. `num_sample_frames`
  counts packets, not sample frames, for compressed codecs (e.g. ima4), so a
  `frames / rate` figure is only a lower bound; it now shows `~N s (approx)`
  with a warning. Uncompressed AIFC (NONE/sowt/twos/float) stays exact.
- `inspect` sanity-checks the MP3 Xing/VBRI `frame_count` against the frames
  actually walked and warns on a wild divergence; a bogus VBR count otherwise
  yields a wrong duration silently.

## [0.9.3] - 2026-06-28

### Fixed

- `inspect` no longer misidentifies ADTS AAC as MP3. The no-ID3 dispatch
  accepted any 11-bit frame sync (0xFFE mask), which ADTS (sync 0xFFF, layer
  bits 00) passed; a forward scan then locked onto a coincidental MPEG frame
  and reported a ~16 s AAC as a ~3 s Layer II MP3. Dispatch now requires a
  fully valid MPEG frame at offset 0.
- `inspect` lints a FLAC metadata block whose declared length overruns the
  file, mirroring the WAV/AIFF overrun checks. A truncated FLAC whose PADDING
  block claimed 8192 bytes previously warned about nothing.
- `inspect` no longer flags `avg_bytes_per_sec` on non-PCM `fmt ` chunks. The
  identity `avg = sample_rate * block_align` is PCM-only; ADPCM (tag 0x0002 /
  0x0011) tripped it. Gated to `tag == 1`, matching the `block_align` check.

### Changed

- The interactive format-anatomy pages (`docs/formats/*-anatomy.html`) share a
  reworked inspector layout: the field-detail panel sits flush with the byte
  diagram and no longer reflows on hover, and hovering or clicking a byte/bit
  square highlights its field both ways.
- Format-internals docs corrected against the specs: MIDI SMPTE decode and a
  tempo-table hex typo, the WAV ACID hex example and bext field widths, the
  MP3 frame-sync value, the AIFF 80-bit float sign and AIFC COMM minimum size,
  and the WAV `format_tag` table gained `0x0002` (MS ADPCM).

## [0.9.2] - 2026-06-28

### Added

- `acidcat inspect --color {auto,always,never}` syntax-highlights the table
  dump. Default `auto` colors only when stdout is a TTY and honors the
  `NO_COLOR` env var; explicit `always`/`never` override both. The palette
  encodes role like syntax highlighting: cyan for structure (chunk ids,
  format label), green for decoded values, dim for offsets/sizes/notes, red
  for warnings. JSON output is unaffected.

## [0.9.1] - 2026-06-28

### Fixed

- `inspect` no longer derives metrics from a chunk size larger than the
  file. A WAV `data` or AIFF `SSND` chunk that overruns the file is linted
  as before, but frames/duration and the reported payload now come from the
  bytes actually present, not the declared size. A 52-byte WAV claiming a
  2 GB `data` chunk previously reported a 24347-second duration.

### Added

- `docs/formats/riff_wav.md` gains edge-case and robustness sections.
- Interactive single-file format references under `docs/formats/`:
  `mp3-anatomy.html`, `wav-anatomy.html`, `flac-anatomy.html`,
  `aiff-anatomy.html`, `midi-anatomy.html`. Hover a field to highlight its
  bytes or bits and read the decode; click to open lookup tables. Byte
  content is verified against acidcat's own parsers.

## [0.9.0] - 2026-06-27

### Added

- `inspect --frames` (`-F`): a per-element deep dump for formats whose
  elements carry their own structure. For MP3 it lists every MPEG frame
  (offset, bitrate, sample rate, channel mode, size), surfacing the
  per-frame bitrate switching that the default summary collapses to a
  range. For MIDI it lists every event (tick, type, decoded detail:
  note names with velocity and channel, tempo/meter/key meta, control
  changes, pitch bend). WAV/AIFF/FLAC carry no per-element structure
  (uniform headerless PCM or opaque codec frames), so the flag is a
  no-op there and says so. Listings are emitted in both table and JSON
  output and capped defensively at 100k rows.

## [0.8.0] - 2026-06-26

### Added

- `inspect` now walks **MP3** and **FLAC**, decoded by hand with no
  mutagen dependency so the dump shows real byte offsets and flags any
  spec violations the tag libraries would paper over. MP3 reports the
  ID3v2 header and frame list, the first MPEG frame fully decoded
  (version/layer/bitrate/sample rate/channel mode/CRC), the Xing/Info
  VBR header with the LAME extension (encoder, VBR method, lowpass,
  gapless delay/padding), a CBR-vs-VBR frame-run summary, and an ID3v1
  trailer when present. FLAC walks the metadata blocks (STREAMINFO,
  VORBIS_COMMENT, PICTURE, SEEKTABLE, APPLICATION, PADDING) plus the
  audio-frame extent, linting the STREAMINFO-first and last-block-flag
  rules.

## [0.7.1] - 2026-06-11

### Fixed

- **Re-registering a pre-0.5.4 library no longer crashes** (or worse).
  The central db filename scheme changed in 0.5.4 (label hash 8 -> 12
  chars), so re-registration computed a different db_path for the same
  root, slipped past the db_path upsert, and hit the root_path UNIQUE
  constraint; without the crash it would have attached a fresh DB and
  orphaned the old one with its tags and descriptions. The registry
  now treats the root as the library's identity and reuses the stored
  db_path (explicit central/in-tree transitions still re-key), and the
  CLI adopts the canonical path the registry returns. Found when a
  `--force` reindex of a v0.5.0-era library crashed.

### Added

- `inspect` walks RF64 (ds64 64-bit size overrides per EBU Tech 3306,
  with sentinel and ordering lints) and Xfer Serum presets (signature,
  decoded JSON metadata, blob extent). Every format acidcat parses
  natively is now inspectable.

## [0.7.0] - 2026-06-11

Apple Loops support and the tagged-format tags gap.

### Added

- **Apple Loops `basc` parsing**: AIFF rows gain their first
  chunk-level tempo and key source. No official spec exists; the
  layout was field-verified against 103 indexed Apple Loops (derived
  bpm `beats / duration * 60` matched the filename bpm on every
  file). Indexing derives bpm and root pitch class ahead of filename
  fallbacks; `inspect` decodes basc and labels the companion
  cate / trns / coll / FLLR chunks. The scale enum is surfaced raw
  pending a verified mapping.
- **Tagged-format genre frames populate the tags table**, so
  `query --tag house` works against mp3/flac/ogg libraries.
  Multi-genre strings split on `,` `;` `/`.

Existing libraries pick these up with `acidcat index DIR --force`.

## [0.6.0] - 2026-06-11

Hardening release closing the deferred-to-v0.6 list from 0.5.5, plus
the inspect verb growing AIFF and MIDI walkers.

### Added

- `acidcat index --force`: re-extract metadata even for files whose
  mtime and size are unchanged. Use after a parser upgrade; preserves
  tags, descriptions, and features (unlike `--rebuild`, which wipes
  them). Mirrored as a `force` param on the MCP `reindex` tool.
- `acidcat inspect` now walks AIFF/AIFC (COMM with the 80-bit rate
  and AIFC compression, SSND with a COMM-frames cross-check, MARK
  enumeration, the 20-byte INST with sustain/release loops, text
  chunks) and Standard MIDI Files (both division forms decoded,
  per-track stats, lints for length lies, missing end-of-track,
  missing tempo, declared-vs-found track counts).

### Fixed

- MCP `locate_sample` and `list_tags` LIKE patterns escape user
  input (`_`/`%` were wildcards); `remove_root`'s legacy LIKE
  fallback escapes the root path the same way. The escape helper
  moved to `core/index.py` as `escape_like`.
- MCP `search_samples` adopts the shared `fts5_syntax_message`
  wording for FTS5 syntax errors.
- Scope filters in `query`, the MCP server, and `--discover` compare
  paths case-insensitively on Windows; `compare_path` now also
  lowercases on macOS (APFS/HFS+ default case-insensitive).
- `_extract_for_index` logs the exception class and message for
  failed files instead of making programming bugs look identical to
  corrupt files.
- The index walk commits before `prune_missing`, so a prune failure
  cannot roll back the trailing batch of upserts.

## [0.5.9] - 2026-06-11

### Fixed

- **acid_beats vetting**: the 0.5.7 layout fix surfaced real beat
  counts, which exposed a second-order problem: batch taggers leave
  boilerplate beats/tempo (8 / 120) in files whose one-shot flag is
  set, and surfacing those made kind inference read 0.1s one-shots
  as loops. New shared helper `effective_acid_beats` vets the field:
  trust beats when the one-shot bit is clear (field-measured ~93%
  reliable), otherwise keep them only when they reconcile with the
  actual duration within 15% (vendors sometimes set the bit on real
  loops with accurate counts). `parse_riff` now also surfaces
  `acid_one_shot`. Wired into info, scan, and the indexer.

## [0.5.8] - 2026-06-11

P1 slice 2: AIFF and MIDI deep-verified against their specs, three
spec-conformance fixes, byte-level diagrams for both formats.

### Fixed

- **MIDI SMPTE division**: files with bit 15 of the division field
  set use SMPTE timing (negative frame rate in the high byte, ticks
  per frame in the low byte). Duration is now computed as
  ticks/(fps*tpf) per SMF 1.0 instead of feeding the raw division
  into the ppqn formula; -29 maps to 29.97 drop-frame.
- **MIDI running status**: meta and sysex events now cancel running
  status per SMF 1.0, so malformed data bytes after a meta event no
  longer decode as phantom notes through the stale status.
- **AIFC `raw ` compression**: the compression 4cc is now matched
  with its trailing space intact, so raw-PCM AIFC reports `raw`
  instead of `unknown:raw`.

### Documentation

- `docs/formats/aiff.md`: byte-ruler maps for COMM, SSND, MARK
  entries and INST, a bit-level diagram of the 80-bit extended
  float, and a corrected sample-rate hex table (the previous
  44100/48000/22050 encodings were one exponent too low).
- `docs/formats/midi.md`: MThd map, division bit diagram for both
  timing forms, status-byte bit split, a worked VLQ example, and
  the running-status cancellation rules.

## [0.5.7] - 2026-06-10

First slice of the format-internals work: a High-severity parser fix
found by verifying the docs against primary sources, byte-level format
documentation, and a new readelf-style inspect verb.

### Fixed

- **acid chunk misparse**: `acid_beats` was read from the unknown
  float at offset 8 (0 in every spec-conformant file) instead of the
  real `num_beats` at offset 12, and the meter fields were read as two
  uint32s spanning the wrong bytes. Verified against libsndfile and
  hex dumps of ACIDized packs from four vendors. The long-standing
  "acid_beats is usually 0" behavior was this bug, and kind inference
  never saw a real beat count. **Reindex libraries to refresh stored
  `acid_beats` values.**

### Added

- `acidcat inspect FILE`: readelf-style structural dump. Chunk table
  with offsets and summaries, decoded per-field breakdown for fmt
  (incl. extensible), data, fact, acid, smpl, inst, cue, LIST and
  bext, `--hex` for raw bytes next to each field, `-f json` for
  machines, and lint warnings for spec violations (riff_size lies,
  loop points past EOF, acid beat/duration drift, cue count lies,
  fmt-after-data ordering).

### Documentation

- `docs/formats/riff_wav.md`: ELF/TCP-style byte-ruler diagrams for
  the RIFF header, chunk envelope, fmt, acid, smpl header and loop
  entries, cue entries, and inst, plus layout provenance notes for
  the acid chunk.

## [0.5.6] - 2026-06-10

Docs and repo hygiene release. No code changes; 263 tests unchanged.

### Documentation

- `docs/codebase_explorer.html` rebuilt for v0.5.x: ingest and fan-out
  flow diagrams, module table with filter tabs, 49 hover cards with
  code snippets covering DSP internals (MFCC, chroma, tempo
  estimation, Camelot math) and format internals (RIFF chunks, 80-bit
  AIFF floats, MIDI running status).
- Added `docs/audio_file_formats.md`, a coverage map of audio formats
  for future readelf-style expansion.

### Housekeeping

- Internal working documents (handover notes, raw audit report)
  removed from the repo; the explorer is the maintained reference.
- `.gitignore` covers bug-hunter state, logo design sources, and a
  local `.stash/` scratch directory.

## [0.5.5] - 2026-05-20

Bug-hunt followup release. Closes all 8 findings from the 2026-05-11
adversarial bug hunt plus the related output-stream encoding cleanup
surfaced in the 2026-05-19 broad review. 263 tests pass (8 new), up
from 254 in 0.5.4.

### Fixed

- **B-1**: MIDI running-status branch advanced `pos` by one fewer
  byte than expected, desynching the parser on any file emitted by
  Ableton, Logic, FL Studio, Cubase or Reaper. `note_count`,
  `note_min`, `note_max` and `duration_ticks` were all wrong on those
  files. Two-byte messages now advance `pos += 2`; one-byte messages
  (program change, channel pressure) advance `pos += 1`.
- **B-2**: `rebuild_fts_for_path` no longer wraps its DELETE + INSERT
  in `with conn:`. Python's sqlite3 connection context manager
  committed the active transaction on normal exit, so the deliberate
  `_COMMIT_EVERY_N_FILES = 100` batching in `_walk_and_upsert` was
  paying a commit + fsync per file. Noticeably faster reindexes on
  HDD-backed sample drives and network mounts.
- **B-3**: Camelot parser no longer lowercases the mode suffix, so
  `CM`, `DM`, `EM` etc. from Beatport, Mixed In Key, Serato and
  Rekordbox resolve to major instead of being mis-classified as
  minor. `find_compatible` returned harmonically wrong neighbors
  for any sample tagged this way.
- **B-4**: librosa key detection returns `None` when chroma cannot
  determine major vs minor mode, letting the filename parser (which
  carries mode explicitly) win instead of always emitting bare-letter
  keys that downstream code interpreted as major. Affected `--deep`
  on files with no filename key hint.
- **B-5**: `acidcat scan` no longer emits `C-1` for samples whose
  SMPL chunk has `root_key=0` (the documented "unset" sentinel).
  Now matches the info and index paths. New shared helpers
  `smpl_root_or_none` / `acid_root_or_none` in `core/riff.py`
  consolidate the three call sites.
- **B-6**: FTS5 syntax errors in `acidcat query --text` (e.g.
  `(foo`, `NOT`, `foo OR`) now surface a single helpful stderr
  message and exit code 1, instead of silently zeroing the result
  set across every library. New `FTSQueryError` and
  `fts5_syntax_message` helpers in `core/index.py` let the MCP server
  share the wording when it adopts them.
- **B-7**: CUE chunk parser caps `num_cues` against payload size, so
  a corrupt or malicious WAV claiming `num_cues=0xFFFFFFFF` no longer
  spins ~4 billion iterations before producing zero output. Reachable
  via `acidcat chunks` / `acidcat survey` walking a bad file.
- **B-8**: `_import_tags` LIKE pattern now escapes `_` and `%` so a
  legacy tags-json entry for `kick_126.wav` cannot accidentally land
  on `kickX126.wav`. New `_escape_like` helper paired with
  `ESCAPE '\\'`. The two read-only LIKE sites in `mcp_server.py`
  (`locate_sample`, `list_tags`) carry the same pattern and will be
  fixed in the next MCP touch.

### Changed

- Output streams in `info`, `chunks`, `survey`, `detect`, `features`
  now open with `encoding='utf-8'`. `scan` and `query` already did;
  the others used the locale default (cp1252 on Windows), mangling
  non-ASCII tag values via the `-o` path.

### Deferred to v0.6

- Unifying `_sniff_format` / `_detect_format` / extension-set checks
  into one canonical `core.detect.classify(filepath) -> kind`. B-5
  proves the drift exists but the per-site fix is enough for 0.5.5.
- Adopting `FTSQueryError` / `_escape_like` in `mcp_server.py`.
- Deprecating the legacy CSV `commands/search.py` in favor of `query`.
- Tagged-format `tags` table population (genre frames currently only
  reach the FTS index).
- macOS APFS case-insensitive overlap-check parity.

---

## [0.5.4] - 2026-05-02

Audit-driven correctness, hardening, and PyPI prep release. 14 stacked
commits closing all 26 actionable findings from the 2026-05-02
codebase review. Test count grew from 232 to 254.

### Breaking

- MCP tool `describe_sample` renamed to `set_sample_description`. The
  old name read like a getter but wrote the description column. Any
  saved MCP client session referencing the old name will break.
- MCP tool `discover_libraries` default flipped from `dry_run=false`
  to `dry_run=true`. A forgetful caller that omits the flag now gets
  a preview rather than a destructive registry mutation. Existing
  callers that pass `dry_run` explicitly are unaffected.
- `mutagen` moves from the optional `[tags]` extra to base
  dependencies. `pip install acidcat[tags]` is no longer valid; use
  `pip install acidcat`. The motivation is that `_extract_for_index`
  routes mp3/flac/ogg/m4a through this module, and a fresh user
  without mutagen would see those files silently skipped on indexing.

### Fixed

- **F-01**: serum preset parser replaces an O(n^2) progressive-slice
  JSON scan with `json.JSONDecoder().raw_decode` for a single linear
  pass.
- **F-02**: `discover_libraries` MCP default now `dry_run=true` (see
  Breaking).
- **F-04**: `analyze_sample` and `detect_bpm_key` now declare
  `idempotentHint=false` so MCP clients do not coalesce or cache
  repeat calls (the underlying file may change between calls).
- **F-05**: WAV parser caps chunk reads at 64 KB. Unbounded
  `f.read(chunk_size)` could OOM on a malformed WAV claiming a 2 GB
  chunk.
- **F-06**: MIDI sysex VLQ length is now bounded against remaining
  track bytes. A malformed SMF could previously push past the MTrk
  boundary into the next track's data, scrambling output.
- **F-07**: FTS5 syntax errors in `search_samples` text now surface as
  a clean `ToolError` with a helpful message, instead of leaking SQL
  internals through the catch-all dispatcher.
- **F-08**: `find_compatible` with a keyless target (drum loops,
  percussion) now restricts results to other keyless samples instead
  of returning random-key samples that are musically nonsensical to
  layer with drums.
- **F-13**: Path comparison is now case-insensitive on Windows so
  `C:/MyLib` and `c:/mylib` cannot both register as separate
  libraries. Stored paths are not mutated; only comparisons change.
- **F-14**: `rebuild_fts_for_path` is now wrapped in an explicit
  transaction. Previously an early return could leave the FTS table
  out of sync with the samples table.
- **F-15**: `register_library` opens a `BEGIN IMMEDIATE` for the
  duration of the no-overlap check + insert, closing a TOCTOU race
  where two concurrent registrations could both pass the check.
- **F-21**: format dispatch now sniffs magic bytes before consulting
  the file extension. Double-suffixed files (e.g. AIFF renamed to
  `foo.aiff.wav`) route by content, not by suffix.
- **F-22**: `_apply_schema` raises `SchemaVersionError` /
  `RegistrySchemaVersionError` on a version mismatch instead of
  silently running old SQL against a future schema.
- **F-25**: label-fallback hash in `--discover` now incorporates the
  candidate root path, so two unrelated roots that both default to
  the same `base_label` no longer collide on the deterministic hash.
- **F-26**: tagged.py strips a leading UTF-8 BOM from ID3v2 / Vorbis
  tag values so it does not leak into the FTS index.

### Changed

- **F-09**: `analyze_sample` description now reads `"SLOW (~1-10s
  after warm-up; first call ~30-60s due to librosa import)"` so the
  LLM and user can set expectations about the cold-start tax.
- **F-10**: filename BPM ceiling raised from 200 to 300. DnB at 174,
  hardcore at 220, gabber at 240 all pass cleanly now.
- **F-12**: AIFC compression types are validated against a known set;
  unknown codes surface as `unknown:<raw>` rather than being silently
  treated as PCM.
- **F-16**: `_close_all` logs close failures to stderr instead of
  swallowing them silently. Past sessions have masked database-locked
  and corruption signals from this exact path.
- **F-17**: destructive MCP tools (`register_library`,
  `forget_library`, `tag_sample`, `set_sample_description`) lead with
  `"Destructive."` in parallel with the existing `"Fast."` /
  `"SLOW."` cost-prefix scheme.
- **F-20**: removed unused `deep` parameter from `analyze_sample`
  schema (handler ignored it).
- **F-23**: `path_hash` widened from 8 hex chars (32 bits, birthday
  collision near 65k) to 12 hex chars (48 bits, near 16M). Existing
  libraries keep their 8-char filenames because the registry stores
  `db_path` explicitly.
- **F-24**: `_walk_and_upsert` passes `followlinks=False` explicitly
  to `os.walk` for clarity.
- **F-29**: comment on `_REGISTRY_PATH` clarifies set-once intent.

### Documentation

- Added `docs/codebase_explorer.html`: a self-contained LaTeX-style
  reference with margin cards and hover details for every module,
  MCP tool, and audit finding.
- `docs/architecture.md` rewritten for the v0.5 per-library + registry
  layout. Previously described the v0.4 single-DB model.

### Verified, no change needed

- **F-03** (claimed `register_library` not idempotent): `idx.open_db`
  opens existing DBs without modification; annotation was correct.
- **F-11** (claimed SMPL note 0 = phantom key on index path):
  `commands/index.py:877-880` already filtered via `if not smpl`.
- **F-19** (claimed `infer_kind` mis-bins 1s loops): review misread
  `or` as `and`.
- **F-27** (claimed RIFF chunk padding bug): pos arithmetic at
  `riff.py:213-215` already adds 1 for odd `chunk_size`.

### PyPI prep

- `pyproject.toml` gains `[project.urls]` (Homepage, Repository,
  Issues), `authors`, expanded classifier set (Development Status
  bumped to `4 - Beta`, per-minor python 3.9..3.13, `OS Independent`),
  and an explicit readme `content-type`.
- `python -m build` and `twine check dist/*` both green.

---

## [0.5.3] - 2026-04-29

CLI safety + post-discover stats + migration helper. Single-PR release.

- CLI collision guard: passing both a target and a management flag
  (e.g. `acidcat index DIR --list`) now errors instead of silently
  ignoring one.
- `--discover` now pre-touches the per-library DBs so list output
  shows accurate sample counts immediately rather than `?`.
- New `acidcat index --refresh-stats` command to populate stale `?`
  sample counts in `--list` for libraries registered before v0.5.1.

## [0.5.2] - 2026-04-28

`--discover` walker for bulk library registration.

- `acidcat index --discover ROOT [--min-samples N --max-depth D]`
  walks a tree and registers every qualifying subfolder as its own
  library.
- New `discover_libraries` MCP tool wraps the same helper.

## [0.5.1] - 2026-04-25

Bug-fix release from real-world testing.

- BPM filename parser regex tightened: `91V_SBH_126_*` now correctly
  parses as 126 BPM, not 91.
- `register_library` now refreshes cached counts when re-attaching
  to an existing per-library DB.
- `find_similar` gains `kind_filter` plus percentile / relative
  scoring.
- CLI walker commits every 100 files instead of once at the end so
  large indexes survive interruption with partial progress.

## [0.5.0] - 2026-04-23

Per-library SQLite indexes + global registry + fan-out queries.
Replaces the v0.4 single global DB.

- Per-library DBs at `~/.acidcat/libraries/<label>_<hash>.db`
  (central default) or `<library>/.acidcat/index.db` (in-tree opt-in).
- Global registry at `~/.acidcat/registry.db` lists every library.
- Mandatory labels, no nested libraries, ambient orphan handling.
- 18-tool MCP surface (read + write + index management).

## [0.4.x]

The single-global-DB era. Documented at this version for historical
reference; not maintained.
