# Recovery and rescue (PhotoRec for audio)

Four verbs turn acidcat into a forensic recovery tool for audio: find audio in a
raw blob, cut it out, pull samples out of a bank, and make an odd codec playable.
They are built as coreutils would be, each doing one thing and piping into the
next:

    locate   find the audio regions in a blob        (reports, never writes)
    carve    cut a byte range out to a file          (the extractor)
    extract  pull every sample out of a known bank    (the bulk unpacker)
    convert  transcode a file to a friendlier format  (the transcoder)

`locate` reports regions to stdout; a region's offset/length is exactly a `carve`
range; `carve`'s output is `convert`'s input. So the verbs chain into a rescue
pipeline. Records go to stdout and summaries to stderr, so `locate | carve`
composes cleanly.

## locate: find the audio in a blob

Point it at anything: a disk image, a card dump, a corrupt file, a proprietary
container that embeds PCM. Three engines run: a signature sweep for known
containers, a statistical detector for signatureless raw PCM (audio is smooth, so
it is compressible, so it is detectable), and a frame-cadence scanner for
headerless compressed streams (MP3).

    acidcat locate disk.img                       # a table of located regions
    acidcat locate card.dd --mode aggressive      # every candidate, not just the confident ones
    dd if=/dev/sdcard | acidcat locate -          # straight off a device, via stdin

`--mode` is the forensics dial:

| mode | keeps |
|---|---|
| `strict` | only containers that pass validation (a real FLAC STREAMINFO, a real Ogg page) |
| `normal` | the above, plus high-confidence raw-PCM blobs and streams (default) |
| `aggressive` | every candidate, including marginal blobs |

`--analyze` infers the geometry of each raw blob (width / channels / endianness),
picking the smoothest interpretation. Sample rate is not in the bytes, so it is
reported null with common candidates. `-v` shows the evidence (entropy,
autocorrelation, byte distribution) and any tells (silence, DC offset, clipping):

    acidcat locate dump.bin --mode aggressive --analyze
    acidcat locate dump.bin -v                    # why each region was flagged

Output shapes for piping: `-f table` (default), `-f json`, `-f tsv`.

    acidcat locate disk.img -f json | jq '.[] | select(.kind=="blob")'

## The pipeline: locate -> carve

`carve --batch` consumes `locate` records and cuts every region out to a
directory. This is the "recover my audio" move:

    acidcat locate disk.img -f json | acidcat carve disk.img --batch - -o recovered/

`--batch` reads JSON or TSV records from a file or stdin (`-`), and writes each
region to `recovered/NNNN_0xoffset_kind.ext`, naming the extension by detected
format. The target file is never modified.

Real run against a Dreamcast disc image (a Doom 64 port): `locate` found 341
regions (92 WAV containers + 249 raw-PCM blobs); the pipeline carved the 92 WAVs
in under two seconds.

For a single known region, `carve` is also a surgical byte tool -- pull one chunk,
the trailing data past a container's declared end, or an explicit range:

    acidcat carve loop.wav --chunk data -o audio.raw       # one chunk payload
    acidcat carve suspect.wav --trailing -o hidden.bin     # appended data past the end
    acidcat carve blob.bin --offset 0x1200 --length 0x800  # an explicit range
    acidcat carve blob.bin --at find:RIFF --end 0x4000     # anchored to a byte pattern

## extract: unpack a whole sample bank

Where `locate`/`carve` work on unknown bytes, `extract` understands specific
sampler and tracker formats and pulls every embedded sample out as its own WAV,
in one pass:

    acidcat extract kit.sf2 -o kit_samples/       # every named SoundFont sample
    acidcat extract song.it                       # -> song_samples/ by default
    acidcat extract bank.krz -o krz_out/          # Kurzweil K2000/2500/2600 bank

Formats with a sample extractor: tracker modules (`.mod`, `.xm`, `.it`, `.s3m`),
Gravis UltraSound patches (`.pat`), IFF 8SVX (`.8svx`), NI Compressed Wave
(`.ncw`), SoundFont (`.sf2`/`.sf3`), Bitwig `.multisample`, Kurzweil `.krz`,
E-mu `.e4b`/`.e5b`, and MPC `.snd`. `--json` emits a manifest instead of writing
files; reads from stdin with `-`.

## convert --to-pcm: make an odd codec playable

Some WAVs will not play: ADPCM, or a stream shipped with a wrong format tag. The
Doom 64 SFX above are a case in point -- tagged as G.726 but actually IMA ADPCM,
so ffmpeg and VLC both refuse them. `--to-pcm` decodes them to a plain 16-bit PCM
WAV that plays anywhere:

    acidcat convert weird.wav --to-pcm -o plain.wav        # decode IMA/DVI ADPCM by header
    acidcat convert mistagged.wav --to-pcm --codec ima     # force IMA on a wrong-tagged file

Without `--codec`, a known ADPCM tag decodes by its header and a plain-PCM file is
left alone; an unknown tag is tried as IMA and kept only if the result looks like
audio. `--codec ima` overrides the header outright for the mistagged case.

`convert` also does the non-rescue exports: Bitwig `.bwclip` -> MIDI, NCW -> WAV,
SF2/SF3 -> a folder of samples, 8SVX -> WAV.

## The CTF lens: audio under a reversible transform

`locate --transforms` hunts for audio hidden under a cheap reversible obfuscation:
XOR with a byte key, bit-rotate, or nibble-swap. These are byte permutations, so
they preserve entropy but scramble autocorrelation -- which is exactly how they
slip PCM past the statistical detector. The lens un-applies each candidate and
asks "is it audio now?":

    acidcat locate challenge.bin --transforms
    # located 2 region(s): 0 container(s), 0 stream(s), 1 blob(s), 1 transformed
    #     offset         end  kind         format    conf        length
    # 0x00000e00  0x00005800  transformed  xor:0x33  1.00        18,944
    # 0x00005600  0x00007600  blob         raw-pcm   0.92         8,192

The reported key is a *candidate*, not gospel. Audio is smooth, so the true key
and its bit-inverted twin (`K ^ 0xFF`) leave equally smooth waveforms, and the low
bits are dither-level. What the lens gives you reliably is the region and the
key's high-bit neighbourhood; you refine and listen from there. It is focused:
reads at most 16 MB.
