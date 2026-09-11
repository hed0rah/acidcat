# The anatomy pages exist in two places

**This directory is canonical.** The pages are authored here and mirrored to
the `audio_files_anatomy` directory of the `hed0rah.github.io` repository,
which exists to host them at
<https://hed0rah.github.io/audio_files_anatomy/>.

The direction used to be the other way round, and this file used to say so.
It was reversed once the design work moved into this repo; a page edited on
the site now will be overwritten by the next copy down.

## Why this file exists

The two copies drifted, with real edits in **both** directions, and nothing
detected it. When they were finally compared, each carried content corrections
the other lacked:

| | one copy had | the other had | correct |
|---|---|---|---|
| sf2 root key 76 | E5 | E4 | **E4** -- acidcat's own `midi_note_to_name` uses C3=60 |
| sigmf `r`/`c` prefix | optional | required | **required** -- the walker's regex rejects `i16_le` |
| mp4 lower-case fourCC | user extensions | standard types | **standard types** |
| amiga MED `[12..15]` | one reserved word | `psecnum` / `pseq` | **`psecnum` / `pseq`** |
| 8svx envelope pairs | (duration, volume) | (duration word, 32-bit Fixed volume) | **the latter** |
| rx2 child chunks | `SINF` + `SDAT`, `SLCE` nested | `SLCE` + `SDAT` | **`SLCE` + `SDAT`** -- confirmed on three specimens |
| mp3 LAME preset | 2 bytes, with surround info | id only | **id only** -- confirmed against LAME's `VbrTag.c` |

Both copies were wrong about something, and each was right about something the
other had wrong. A one-way sync in either direction would have destroyed real
work. Naming a canonical side prevents that from recurring; it does not undo
the need to check before copying.

## Keeping them in step

- Edit **here**, then copy up to the site. Never the other way.
- Before copying, **diff the content with styling stripped**. A raw diff is
  useless: the theme CSS and toggle are ~7 KB per page and bury any real
  change.
- Write with byte-level operations, preserving each side's line endings. This
  directory is LF; every file on the site is CRLF. Going through Python's text
  layer without `newline=""` rewrites every line -- a one-line correction
  became a 603-line diff that way.
- Run `python scripts/check_bytemaps.py <dir>` against both copies. It asserts
  every byte in every map is claimed by exactly one field, and it takes
  seconds. `tests/test_anatomy_pages.py` runs the same check over this
  directory in CI.
- A new page is generated from an existing one so the shell cannot drift:
  `python scripts/build_ableton_anatomy.py <src-page> <out-page>`. Generate
  from a page in **this** directory.
- A new page also needs a card and a `.tab` / `.name` colour pair in the
  site's `index.html`. The palette is one distinct colour per format.

## The standard the pages are held to

Every byte in a byte map comes from a real, named specimen, and is verified
back against that file. Three pages have failed this: the tracker IMPS map
disagreed with its own specimen at three bytes, the serum page described a
capability (`zstd` frame identification) that does not exist in the code, and
the midi page stated MIDI 2.0 velocity as 256x MIDI 1.0's when 7 bits to 16
bits is 512x -- in a sentence that named both widths.

## Which walkers do NOT get a page, and why

`docs/adding-a-walker.md` says a format earns a page here once it is walked.
Nine walkers do not have one, and eight of those are a deliberate grouping
rather than a backlog:

    adx  albank  brstm  cdxa  gcm  hps  vag  cue

They are the console and disc-image family, and they are documented together in
`../console_audio_formats.md`, which maps the whole landscape by generation and
says what is shipped, reachable and out of scope. Splitting them into eight
pages would lose the thing that makes them legible, which is where each sits
relative to the others -- the same argument that puts `.asd`, `.adg` and their
siblings on tabs of one Ableton page instead of six.

The ninth is `dmx`, the Doom `DS*` lump: eight bytes of header over unsigned
8-bit PCM, with no magic anywhere. It is described in
`../audio_file_formats.md` under Game / Interactive Audio. A page would be one
byte map of four fields, and the walker's own docstring already records what
the corpus settled and what it could not.

If that reasoning stops holding -- a console format grows enough structure to
need a map of its own -- the rule in `adding-a-walker.md` wins and it gets a
page. This section exists so the gap reads as a decision that can be revisited
rather than as nine pages nobody got round to.
