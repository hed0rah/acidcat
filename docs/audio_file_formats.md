# Audio File Formats - Comprehensive Reference

## 1. Raw / Headerless Audio

- PCM (.pcm, .raw)
- Signed PCM (8/16/24/32-bit)
- Unsigned PCM
- Floating point PCM (.f32, .f64)

---

## 2. PCM-Based Containers

### 2.1 WAV Family

- WAV (RIFF container)
  - PCM (LPCM)
  - IEEE Float
  - IMA ADPCM
  - MS ADPCM
  - GSM
- WAV64 (Sony, >4GB support)
- RF64 (EBU extended WAV)

### 2.2 AIFF Family

- AIFF (.aiff, .aif)
- AIFC (compressed AIFF)
  - PCM
  - A-law
  - μ-law
  - ADPCM

### 2.3 Professional / Extended

- BWF (Broadcast Wave Format)
- CAF (Core Audio Format)

---

## 3. Lossless Compressed Formats

### 3.1 Common

- FLAC (.flac)
- ALAC (.m4a container)
- WavPack (.wv)
- Monkey's Audio (.ape)
- TAK (.tak)
- OptimFROG (.ofr)
- Shorten (.shn)

### 3.2 Less Common

- LA (Lossless Audio)
- RALF
- TTA (.tta)
- MPEG-4 ALS
- Dolby TrueHD

---

## 4. Lossy Compressed Formats

### 4.1 MPEG Family

- MP1
- MP2 (.mp2)
- MP3 (.mp3)
- AAC
  - AAC-LC
  - HE-AAC v1
  - HE-AAC v2
  - AAC-LD
  - AAC-ELD
- Containers
  - .aac (raw)
  - .m4a / .mp4

### 4.2 Open / Modern

- Opus (.opus)
- Vorbis (.ogg)

### 4.3 Microsoft

- WMA
  - WMA Standard
  - WMA Pro
  - WMA Lossless
  - WMA Voice

### 4.4 Legacy / Niche

- RealAudio (.ra, .rm)
- ATRAC
- Musepack (.mpc)
- TwinVQ
- AMR
  - AMR-NB
  - AMR-WB
- Speex

---

## 5. Container Formats

### 5.1 General Media Containers

- MP4 (.mp4, .m4a)
- Matroska (.mka, .mkv)
- AVI
- MOV

### 5.2 Audio-Focused Containers

- Ogg (.ogg)
  - Vorbis
  - Opus
  - FLAC
- WebM
- CAF
- MXF

---

## 6. Telephony / Speech Codecs

- G.711 (A-law, μ-law)
- G.722
- G.723.1
- G.726
- G.729
- AMR / AMR-WB
- EVRC
- iLBC
- Speex
- Opus (VoIP mode)

---

## 7. Game / Interactive Audio

- FSB (FMOD Sound Bank)
- Wwise (.wem)
- XMA (Xbox Media Audio)
- VAG (PlayStation ADPCM)
- ADX / HCA (CRI Middleware)
- BRSTM / BCSTM (Nintendo)

---

## 8. Tracker / Module Formats

- MOD
- XM
- S3M
- IT
- MTM
- ULT
- 669
- PTM

---

## 9. MIDI and Control Formats

- MIDI (.mid)
- RMID
- KAR (Karaoke MIDI)
- DLS
- SF2 (SoundFont)

---

## 10. Streaming / Adaptive Formats

- HLS (AAC, Opus segments)
- DASH
- Icecast / Shoutcast
- WebRTC (Opus)

---

## 11. Obscure / Historical Formats

- VOC (Creative Labs)
- AU / SND (Sun)
- 8SVX (Amiga IFF)
- IFF
- SD2 (Sound Designer II)
- PT24 (Pro Tools legacy)
- SWA (Shockwave Audio)
- QCP (Qualcomm PureVoice)
- DSF / DFF (DSD)
- SMP / SMPACK
- KWA
- SMAF (Yamaha)

---

## 12. High-Resolution / Audiophile

- DSD
  - DSF
  - DFF
- DXD
- High-resolution PCM (WAV, FLAC, AIFF)

---

## 13. Codec Types (Conceptual Layer)

### 13.1 PCM

- Linear PCM
- Floating point PCM

### 13.2 ADPCM

- IMA ADPCM
- MS ADPCM
- Yamaha ADPCM

### 13.3 Transform Codecs

- MDCT-based (MP3, AAC, Opus)

### 13.4 Predictive / Hybrid

- FLAC (linear prediction)
- WavPack (hybrid)

### 13.5 Perceptual Coding

- MP3
- AAC
- Vorbis
- Opus

---

## 14. Practical Core Formats

- WAV (PCM)
- FLAC
- MP3
- AAC (M4A/MP4)
- Opus
- Ogg Vorbis

