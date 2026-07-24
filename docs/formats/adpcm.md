# ADPCM Format Internals

Low-level reference for the two ADPCM codecs acidcat decodes inside a WAV:
**IMA/DVI ADPCM** (format tag `0x0011`) and **Microsoft ADPCM** (`0x0002`). Both
are 4-bit, roughly 4:1 compression, and both are why a WAV sometimes refuses to
play. `acidcat convert --to-pcm` renders either to plain 16-bit PCM.

For the WAV container itself, see `riff_wav.md`.

---

## A short history

The whole family is one long fight over how few bits you can spend per sample.

**PCM** stores the absolute amplitude of every sample. Exact, and dumb about it:
16 bits whether the waveform is a screaming transient or a slow swell.

**DPCM** noticed audio is smooth, so consecutive samples are close. Send the
*difference* from a prediction instead of the sample; small differences need
fewer bits. C. Chapin Cutler patented it at Bell Labs in 1950 (US 2,605,361).
That is the same fact acidcat's statistical detector runs in reverse: audio
compresses because it is predictable, so a blob that compresses like audio
usually is audio.

**ADPCM** made the step *adaptive*. A fixed difference-quantizer is wrong twice,
too coarse for quiet passages and too narrow for loud ones, so scale it to the
signal: shrink the step when the deltas are small, grow it when they spike.
Cummiskey, Jayant, and Flanagan formalized adaptive quantization for speech at
Bell Labs in 1973. The phone network standardized it as **G.721** (1984) and
**G.726** (1990) at 32 kbit/s. G.726 is the codec the Doom 64 Dreamcast port
mislabels its IMA ADPCM as, which is exactly why ffmpeg and VLC gag on those
files.

Multimedia forked it twice:

- **IMA/DVI ADPCM** came out of Intel's DVI (Digital Video Interactive) and was
  blessed by the Interactive Multimedia Association for early-90s CD-ROM. The
  design goal was cheap: table lookups and shifts, no multiplies, so a 486 could
  decode it in real time next to full-motion video. It became WAV `0x0011` and
  QuickTime's `ima4`.
- **Microsoft ADPCM** spent one multiply per sample on a real two-tap predictor
  for slightly better quality. It shipped with the Windows Sound System and is
  what a 90s game or Windows system sound was most likely encoded in.

The game rips in the museum's `ms-adpcm/` folder are pure CD-ROM-era artifact:
quarter the size so the audio fit on the disc and streamed off it.

---

## The core loop

Every ADPCM decoder is the same three steps, once per 4-bit nibble:

```
1. predict   guess this sample from the previous one(s)
2. correct   add the residual the nibble encodes, scaled by the current step
3. adapt     grow or shrink the step for the next nibble
```

The codecs differ only in how they predict and how they adapt:

| | IMA / DVI (`0x0011`) | Microsoft (`0x0002`) |
|---|---|---|
| predictor | last sample (1st order) | linear over the last **two** (2nd order) |
| arithmetic | lookups and shifts | one multiply-add per sample |
| step state | an **index** into an 89-entry step table | a **delta** scaled by an adapt table |
| block header | predictor + index | coefficient set + delta + two priming samples |

A nibble is a sign bit plus three magnitude bits: eight signed levels, `-8..+7`.
Four bits a sample against sixteen for PCM is the 4:1.

---

## IMA / DVI ADPCM (`0x0011`)

IMA predicts that the next sample equals the last one and codes the error. No
coefficients, no multiplies, just an 89-entry step table and a 16-entry index
table.

Per nibble:

```
step = STEP_TABLE[index]
diff = step >> 3
if nibble & 1: diff += step >> 2      // rebuild the magnitude from the three
if nibble & 2: diff += step >> 1      //   bits, bit-weighted
if nibble & 4: diff += step
if nibble & 8: diff = -diff           // sign bit
predictor = clamp16(predictor + diff)
index     = clamp(index + INDEX_TABLE[nibble], 0, 88)
```

`INDEX_TABLE` is `{-1,-1,-1,-1, 2,4,6,8, -1,-1,-1,-1, 2,4,6,8}`. A small residual
(magnitude 1-3) walks the step down; a large one (4-7) walks it up fast. The step
table is geometric, each entry about 1.1x the last from 7 up to 32767, so it
tracks amplitude across five orders of magnitude.

A WAV data chunk is a run of `block_align`-byte blocks. Each block re-primes the
predictor so errors cannot propagate past it:

```
mono block:
  int16  predictor      // exact starting sample, emitted verbatim
  uint8  step_index     // 0..88
  uint8  reserved
  [ nibbles ... ]       // low nibble first, two samples per byte

stereo block:  two 4-byte headers (L then R), then nibbles interleave in
               4-byte words: LLLL RRRR LLLL RRRR ...
```

acidcat also carries a **continuous** variant (`decode_ima_continuous`): no block
structure, predictor starting at zero. That is the path for the mistagged,
block-less streams like the Doom 64 SFX.

---

## Microsoft ADPCM (`0x0002`)

Microsoft spends the multiply to predict from the last *two* samples:

```
predict = (sample1 * coef1 + sample2 * coef2) / 256
predict += sign_extend(nibble) * delta
predict  = clamp16(predict)
sample2, sample1 = sample1, predict          // slide the window
delta = (ADAPT_TABLE[nibble] * delta) / 256  // never below 16
```

The `/ 256` is fixed-point: the coefficients are Q8 fractions. There is a reader
trap in that division. The C reference decoders (ffmpeg, libsndfile) truncate
toward zero, not floor. For a negative predictor the two differ by one, and since
the predictor feeds back into itself the error would accumulate. acidcat's `_t256`
truncates toward zero, so its output is bit-exact with ffmpeg: verified
sample-for-sample on real mono and stereo files, zero diff across 186,762 and
291,456 samples.

### The coefficient pairs, decoded

A block picks one of seven `(coef1, coef2)` pairs by index, and the pair is the
prediction rule. Read them as Q8 (over 256) and they turn into plain linear
predictors:

| # | (c1, c2) | as fractions | predicts |
|--:|----------|--------------|----------|
| 0 | (256, 0)     | 1.0, 0.0    | "same as the last sample", IMA's rule |
| 1 | (512, -256)  | 2.0, -1.0   | `2*s1 - s2`: continue the slope of the last two |
| 2 | (0, 0)       | 0.0, 0.0    | predict zero, code the raw value |
| 3 | (192, 64)    | 0.75, 0.25  | weighted lean toward the last sample |
| 4 | (240, 0)     | 0.9375, 0.0 | a slightly damped "same as last" |
| 5 | (460, -208)  | 1.80, -0.81 | softened linear extrapolation |
| 6 | (392, -232)  | 1.53, -0.91 | another extrapolation blend |

Pair 1 is the one to understand. `2*sample1 - sample2` is the straight-line
continuation of the last two samples: if the waveform was rising by 100 a step,
predict it keeps rising by 100. On a smooth ramp the residual is near zero and the
nibbles stay tiny. The encoder picks whichever pair leaves the smallest residuals
for the block. The pairs are almost always the standard seven, but the fmt chunk
carries its own table, so acidcat reads it rather than assuming.

`ADAPT_TABLE` is `{230,230,230,230, 307,409,512,614, 768,614,512,409,
307,230,230,230}`, the same shape as IMA's index logic. Small nibbles multiply
the step by `230/256` (about 0.9, decay); the extreme nibble by `768/256` (3.0,
fast attack). Floor 16.

### Block layout

Microsoft front-loads the two priming samples into the header instead of an index:

```
mono block (7-byte header, then data):
  uint8  predictor_index    // 0..6, picks the coefficient pair
  int16  delta              // initial step
  int16  sample1            // more recent priming sample
  int16  sample2            // older priming sample
  [ nibbles ... ]           // high nibble first, two samples per byte

  emit order: sample2, sample1, then the decoded nibbles.

stereo block (14-byte header):
  uint8  predictor[L], predictor[R]
  int16  delta[L], delta[R]
  int16  sample1[L], sample1[R]
  int16  sample2[L], sample2[R]
  [ bytes ... ]             // each byte = one L nibble (high) + one R nibble (low)
```

The priming samples come out older-first (`sample2` then `sample1`) because
`sample2` is chronologically earlier. Reverse them and the block plays a sample
early. `samplesPerBlock` in the fmt extension is
`2 + (block_align - 7*channels) * 2 / channels`; the standard 1024-byte stereo
block gives 1012.

---

## Detecting it

Inside a WAV, the `fmt ` chunk's first `uint16` (the format tag) is the tell:
`0x0011` for IMA, `0x0002` for Microsoft, with `bits_per_sample` of 4. Microsoft
carries an extended fmt chunk (50 bytes): after the standard `WAVEFORMATEX`, a
`cbSize`, then `samplesPerBlock`, `numCoef`, and the coefficient pairs. IMA's
extension is shorter, just `cbSize` and `samplesPerBlock`.

The trap is a mistagged file: really ADPCM, but carrying a wrong tag or bogus
byte-rate and block-align that imply plain PCM. `acidcat locate` still finds the
region as audio by its statistics, and `convert --to-pcm --codec ima|ms` forces
the right decoder when the header lies.

---

## In acidcat

```
acidcat convert weird.wav --to-pcm            # decode by the fmt tag (IMA or MS)
acidcat convert weird.wav --to-pcm -o out.wav # choose the output path
acidcat convert bad.wav  --to-pcm --codec ms  # force MS ADPCM on a mistagged file
acidcat convert bad.wav  --to-pcm --codec ima # force IMA (continuous, block-less)
```

The decoder is `core/adpcm.py`; output is signed 16-bit little-endian PCM,
interleaved for stereo. The MS path is pinned bit-exact against ffmpeg, so a
regression that drifts by even one LSB per sample shows up as a non-zero diff.
acidcat decodes ADPCM but does not encode it: shrinking PCM back into an ADPCM
stream is an encoder's job, out of scope for a dissection tool.
