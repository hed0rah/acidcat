#!/usr/bin/env python3
"""
acid_cat.py

Full WAV metadata explorer with librosa analysis fallback.

Modes:
- Default (summary): ACID/SMPL summary + expected duration + "Other chunks" list
- --all: Enumerate all chunks, outputting one row per chunk/key/value
- --survey: Count chunk IDs across scanned files and output a summary CSV
- --kinds: List chunk IDs per file (quick "what metadata is here?" mode)
- --has: Filter to files that include any of the given chunk IDs (comma-separated)
- --fallback: Estimate BPM/key with librosa if no ACID/SMPL metadata is found
- -v/--verbose: In --all mode, also print parsed rows to the console (ignored if -q)

Examples:
  python acid_cat.py "D:\\Audio\\Loops" -n 200
  python acid_cat.py "D:\\Audio\\Loops" --all -n 50
  python acid_cat.py "D:\\Audio\\Loops" --survey -n 1000
  python acid_cat.py "D:\\Audio\\Loops" --kinds -n 200
  python acid_cat.py "D:\\Audio\\Loops" --kinds --has acid
  python acid_cat.py "D:\\Audio\\Loops" --all --has acid,smpl -v
  python acid_cat.py "D:\\Audio\\Loops" --fallback -n 100
"""

import argparse
import csv
import os
import re
import struct
import wave
import binascii
from collections import Counter
import librosa
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
import warnings
warnings.filterwarnings("ignore")

def parse_bpm_from_filename(filepath):
    """
    Extract BPM from filename using multiple common patterns.
    Returns None if no BPM found.
    """
    filename = os.path.basename(filepath)

    # Common BPM patterns in filenames
    bpm_patterns = [
        r'(\d{2,3})\s*bpm',           # "140 BPM", "140bpm"
        r'(\d{2,3})\s*BPM',           # "140 BPM", "140BPM"
        r'bpm\s*(\d{2,3})',           # "bpm 140", "bpm140"
        r'BPM\s*(\d{2,3})',           # "BPM 140", "BPM140"
        r'(\d{2,3})bpm',              # "140bpm"
        r'(\d{2,3})BPM',              # "140BPM"
        r'_(\d{2,3})_',               # "_140_"
        r'-(\d{2,3})-',               # "-140-"
        r'\s(\d{2,3})\s',             # " 140 "
    ]

    for pattern in bpm_patterns:
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            bpm = int(match.group(1))
            # Validate reasonable BPM range
            if 60 <= bpm <= 200:
                return bpm

    return None


def parse_key_from_filename(filepath):
    """
    Extract musical key from filename using common patterns.
    Returns None if no key found.
    """
    filename = os.path.basename(filepath).replace('_', ' ').replace('-', ' ')

    # Key patterns: note + quality (maj/min) - ordered from most specific to least
    key_patterns = [
        # Major keys (most specific first)
        r'\b([A-G]#?)\s*major\b',     # "C major", "F# major"
        r'\b([A-G]#?)\s*maj\b',       # "C maj", "F# maj"
        r'\b([A-G]#?)major\b',        # "Cmajor", "F#major"
        r'\b([A-G]#?)maj\b',          # "Cmaj", "F#maj"

        # Minor keys (most specific first)
        r'\b([A-G]#?)\s*minor\b',     # "A minor", "C# minor"
        r'\b([A-G]#?)\s*min\b',       # "A min", "C# min"
        r'\b([A-G]#?)minor\b',        # "Aminor", "C#minor"
        r'\b([A-G]#?)min\b',          # "Amin", "C#min"

        # Single letter designations (least specific)
        r'\b([A-G]#?)\s*M\b',         # "C M", "F# M"
        r'\b([A-G]#?)\s*m\b',         # "A m", "C# m"
        r'\b([A-G]#?)m\b',            # "Am", "C#m"
    ]

    for pattern in key_patterns:
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            note = match.group(1).upper()
            key_text = match.group(0).lower()

            # Determine if major or minor
            if any(x in key_text for x in ['min', 'm']):
                return f"{note}m"
            else:
                return note

    return None


def validate_and_improve_bpm(detected_bpm, filename_bpm, confidence_threshold=20):
    """
    Validate detected BPM against filename BPM and choose the best value.

    Args:
        detected_bpm: BPM detected by librosa
        filename_bpm: BPM parsed from filename
        confidence_threshold: Maximum difference to trust detected BPM

    Returns:
        (final_bpm, source) where source is 'detected', 'filename', or 'corrected'
    """
    if filename_bpm is None:
        return detected_bpm, 'detected'

    if detected_bpm is None:
        return filename_bpm, 'filename'

    # Check if detected BPM is reasonable
    if not (60 <= detected_bpm <= 200):
        return filename_bpm, 'filename'

    # Check for common tempo detection errors
    diff = abs(detected_bpm - filename_bpm)

    # Direct match (within threshold)
    if diff <= confidence_threshold:
        return detected_bpm, 'detected'

    # Check for half-tempo detection
    if abs(detected_bpm * 2 - filename_bpm) <= confidence_threshold:
        return detected_bpm * 2, 'corrected'

    # Check for double-tempo detection
    if abs(detected_bpm / 2 - filename_bpm) <= confidence_threshold:
        return detected_bpm / 2, 'corrected'

    # Check for 2/3 tempo (triplet feel)
    if abs(detected_bpm * 1.5 - filename_bpm) <= confidence_threshold:
        return detected_bpm * 1.5, 'corrected'

    # Check for 3/2 tempo
    if abs(detected_bpm / 1.5 - filename_bpm) <= confidence_threshold:
        return detected_bpm / 1.5, 'corrected'

    # If none match well, prefer filename (it's usually more accurate)
    return filename_bpm, 'filename'


def improve_key_detection(detected_key, filename_key):
    """
    Combine detected key with filename key for better accuracy.

    Args:
        detected_key: Key detected by librosa chroma analysis
        filename_key: Key parsed from filename

    Returns:
        (final_key, source) where source is 'detected', 'filename', or 'combined'
    """
    if filename_key is None:
        return detected_key, 'detected'

    if detected_key is None:
        return filename_key, 'filename'

    # If both agree (accounting for different notations)
    if detected_key == filename_key:
        return detected_key, 'detected'

    # Prefer filename key (usually more reliable than chroma analysis)
    return filename_key, 'filename'

def extract_advanced_audio_features(filepath):
    """
    Extract comprehensive audio features for ML analysis.
    Returns dictionary with spectral, rhythmic, and timbral features.
    """
    try:
        y, sr = librosa.load(filepath, sr=None, mono=True)

        if len(y) < 256:
            return None  # Skip very short clips

        features = {}

        # Basic properties
        features['duration_sec'] = len(y) / sr
        features['sample_rate'] = sr
        features['audio_length_samples'] = len(y)

        # Spectral features
        spectral_centroids = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        features['spectral_centroid_mean'] = np.mean(spectral_centroids)
        features['spectral_centroid_std'] = np.std(spectral_centroids)

        spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
        features['spectral_rolloff_mean'] = np.mean(spectral_rolloff)
        features['spectral_rolloff_std'] = np.std(spectral_rolloff)

        spectral_bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
        features['spectral_bandwidth_mean'] = np.mean(spectral_bandwidth)
        features['spectral_bandwidth_std'] = np.std(spectral_bandwidth)

        # Zero crossing rate
        zcr = librosa.feature.zero_crossing_rate(y)[0]
        features['zcr_mean'] = np.mean(zcr)
        features['zcr_std'] = np.std(zcr)

        # MFCC features (first 13 coefficients)
        mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        for i in range(13):
            features[f'mfcc_{i+1}_mean'] = np.mean(mfccs[i])
            features[f'mfcc_{i+1}_std'] = np.std(mfccs[i])

        # Chroma features
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        features['chroma_mean'] = np.mean(chroma)
        features['chroma_std'] = np.std(chroma)

        # Mel-frequency features
        mel_spectrogram = librosa.feature.melspectrogram(y=y, sr=sr)
        features['mel_mean'] = np.mean(mel_spectrogram)
        features['mel_std'] = np.std(mel_spectrogram)

        # Tempo and rhythm
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
        features['tempo_librosa'] = tempo
        features['beat_count'] = len(beats)

        # RMS energy
        rms = librosa.feature.rms(y=y)[0]
        features['rms_mean'] = np.mean(rms)
        features['rms_std'] = np.std(rms)

        # Spectral contrast
        contrast = librosa.feature.spectral_contrast(y=y, sr=sr)
        features['spectral_contrast_mean'] = np.mean(contrast)
        features['spectral_contrast_std'] = np.std(contrast)

        # Tonnetz (tonal centroid features)
        tonnetz = librosa.feature.tonnetz(y=y, sr=sr)
        features['tonnetz_mean'] = np.mean(tonnetz)
        features['tonnetz_std'] = np.std(tonnetz)

        return features

    except Exception as e:
        print(f"Error extracting features from {filepath}: {e}")
        return None

def estimate_librosa_metadata(filepath):
    try:
        y, sr = librosa.load(filepath, sr=None, mono=True)
        duration_sec = round(len(y) / sr, 4) if sr and len(y) > 0 else None

        # Skip absurdly short clips → call it a one-shot
        if len(y) < 256:
            return {
                "estimated_bpm": "oneshot",
                "estimated_key": None,
                "duration_sec": duration_sec,
                "bpm_source": "oneshot",
                "key_source": None
            }

        # --- BPM estimation with filename parsing ---
        detected_bpm = None
        try:
            # Multiple tempo detection methods for better accuracy
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            tempos_1 = librosa.beat.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
            tempos_2 = librosa.beat.tempo(y=y, sr=sr, aggregate=None)

            # Get median tempo from both methods
            all_tempos = []
            if tempos_1.size > 0:
                all_tempos.extend(tempos_1)
            if tempos_2.size > 0:
                all_tempos.extend(tempos_2)

            if all_tempos:
                detected_bpm = round(float(np.median(all_tempos)), 2)
        except Exception:
            pass

        # Parse BPM from filename
        filename_bpm = parse_bpm_from_filename(filepath)

        # Validate and improve BPM detection
        final_bpm, bpm_source = validate_and_improve_bpm(detected_bpm, filename_bpm)

        # --- Key estimation with filename parsing ---
        detected_key = None
        try:
            # Improved chroma analysis
            default_n_fft = 2048  # Larger window for better frequency resolution
            n_fft = min(default_n_fft, max(16, len(y)))

            # Use CQT (Constant-Q Transform) for better pitch detection
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
            if chroma.size > 0:
                # Take median over time for stability
                chroma_median = np.median(chroma, axis=1)
                if np.any(chroma_median > 0):
                    note_number = int(np.argmax(chroma_median))
                    note_names = ["C", "C#", "D", "D#", "E", "F",
                                  "F#", "G", "G#", "A", "A#", "B"]
                    detected_key = note_names[note_number]
        except Exception:
            pass

        # Parse key from filename
        filename_key = parse_key_from_filename(filepath)

        # Combine key detection results
        final_key, key_source = improve_key_detection(detected_key, filename_key)

        return {
            "estimated_bpm": final_bpm,
            "estimated_key": final_key,
            "duration_sec": duration_sec,
            "bpm_source": bpm_source,
            "key_source": key_source,
            "filename_bpm": filename_bpm,
            "filename_key": filename_key,
            "detected_bpm": detected_bpm,
            "detected_key": detected_key
        }

    except Exception:
        # Total failure fallback
        return {
            "estimated_bpm": None,
            "estimated_key": None,
            "duration_sec": None,
            "bpm_source": "failed",
            "key_source": "failed",
            "filename_bpm": parse_bpm_from_filename(filepath),
            "filename_key": parse_key_from_filename(filepath),
            "detected_bpm": None,
            "detected_key": None
        }




# --------------------------
# Helper: MIDI note -> name
# --------------------------
def midi_note_to_name(note_number):
    if note_number is None:
        return None
    NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    octave = (note_number // 12) - 1
    note = NOTES[note_number % 12]
    return f"{note}{octave}"

def safe_basename_for_csv(path_basename):
    """
    Preserve directory portion; slugify only the basename.
    Ensures parent directories exist and .csv suffix is present.
    """
    path_norm = os.path.normpath(path_basename)
    dirpart, base = os.path.split(path_norm)
    base_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", base.strip()) or "output.csv"
    if not base_slug.lower().endswith(".csv"):
        base_slug += ".csv"
    if dirpart:
        os.makedirs(dirpart, exist_ok=True)
        return os.path.join(dirpart, base_slug)
    return base_slug

# --------------------------
# Core RIFF parser
# --------------------------
def parse_riff(filepath, enumerate_all=False):
    """
    Walk through WAV file and parse chunks.
    Returns:
      - results: list of (chunk_id, key, value) entries (used in --all mode)
      - meta:    summary dict with ACID/SMPL fields
      - seen:    ordered list of unique chunk IDs encountered
    """
    results = []
    meta = {
        "bpm": None,
        "acid_beats": None,
        "acid_root_note": None,
        "smpl_root_key": None,
        "smpl_loop_start": None,
        "smpl_loop_end": None,
    }
    seen_order = []
    seen_set = set()

    file_size = os.path.getsize(filepath)

    with open(filepath, "rb") as f:
        riff_header = f.read(12)
        if len(riff_header) < 12 or riff_header[0:4] != b'RIFF':
            return results, meta, seen_order  # Not a WAV

        pos = 12
        while pos < file_size:
            f.seek(pos)
            header = f.read(8)
            if len(header) < 8:
                break
            chunk_id = header[0:4]
            try:
                chunk_size = struct.unpack("<I", header[4:8])[0]
            except struct.error:
                break

            cid_str = chunk_id.decode("ascii", errors="ignore")
            if cid_str and cid_str not in seen_set:
                seen_set.add(cid_str)
                seen_order.append(cid_str)

            chunk_data = f.read(chunk_size)

            # --- Known chunk parsing ---
            if chunk_id == b'acid':
                # ACID chunk: version, root_note, (reserved), beats, meter_den, meter_num, tempo(float)
                try:
                    version, root_note, _, beats, meter_den, meter_num, tempo = struct.unpack("<IHHIII f", chunk_data)
                    meta["acid_root_note"] = root_note
                    meta["acid_beats"] = beats
                    meta["bpm"] = round(tempo, 2)
                    if enumerate_all:
                        results.append(("acid", "bpm", meta["bpm"]))
                        results.append(("acid", "beats", beats))
                        results.append(("acid", "root_note", midi_note_to_name(root_note)))
                        results.append(("acid", "meter", f"{meter_num}/{meter_den}"))
                        results.append(("acid", "version", version))
                except Exception as e:
                    if enumerate_all:
                        results.append(("acid", "error", str(e)))

            elif chunk_id == b'smpl':
                # SMPL chunk: sampler info + loop points
                try:
                    (
                        manufacturer, product, sample_period, midi_unity_note,
                        midi_pitch_fraction, smpte_format, smpte_offset,
                        sample_loops, sampler_data
                    ) = struct.unpack("<IIIIIIiiI", chunk_data[:36])
                    meta["smpl_root_key"] = midi_unity_note
                    if sample_loops > 0 and len(chunk_data) >= 36 + 24:
                        _, _, start, end, _, _ = struct.unpack("<IIIIII", chunk_data[36:60])
                        meta["smpl_loop_start"] = start
                        meta["smpl_loop_end"] = end
                    if enumerate_all:
                        results.append(("smpl", "root_key", midi_note_to_name(midi_unity_note)))
                        results.append(("smpl", "loops", sample_loops))
                        if meta["smpl_loop_start"] is not None:
                            results.append(("smpl", "loop_start", meta["smpl_loop_start"]))
                            results.append(("smpl", "loop_end", meta["smpl_loop_end"]))
                except Exception as e:
                    if enumerate_all:
                        results.append(("smpl", "error", str(e)))

            elif chunk_id == b'inst' and enumerate_all:
                # INST chunk: base note, detune, gain, key range, velocity range
                try:
                    if len(chunk_data) >= 7:
                        base = chunk_data[0]
                        detune = struct.unpack("<b", chunk_data[1:2])[0]
                        gain = struct.unpack("<b", chunk_data[2:3])[0]
                        low_note, high_note, low_vel, high_vel = chunk_data[3], chunk_data[4], chunk_data[5], chunk_data[6]
                        results.append(("inst", "base_note", midi_note_to_name(base)))
                        results.append(("inst", "detune_cents", detune))
                        results.append(("inst", "gain_db", gain))
                        results.append(("inst", "key_range", f"{midi_note_to_name(low_note)}-{midi_note_to_name(high_note)}"))
                        results.append(("inst", "vel_range", f"{low_vel}-{high_vel}"))
                    else:
                        results.append(("inst", "raw", binascii.hexlify(chunk_data).decode()))
                except Exception as e:
                    results.append(("inst", "error", str(e)))

            elif chunk_id == b'fmt ' and enumerate_all:
                # FMT chunk: codec, channels, rate, bits
                try:
                    if len(chunk_data) >= 16:
                        wFormatTag, nChannels, nSamplesPerSec, nAvgBytesPerSec, nBlockAlign, wBitsPerSample = struct.unpack("<HHIIHH", chunk_data[:16])
                        results.append(("fmt ", "format_tag", wFormatTag))
                        results.append(("fmt ", "channels", nChannels))
                        results.append(("fmt ", "sample_rate", nSamplesPerSec))
                        results.append(("fmt ", "bits_per_sample", wBitsPerSample))
                        results.append(("fmt ", "block_align", nBlockAlign))
                    else:
                        results.append(("fmt ", "raw", binascii.hexlify(chunk_data).decode()))
                except Exception as e:
                    results.append(("fmt ", "error", str(e)))

            elif chunk_id == b'fact' and enumerate_all:
                # FACT chunk: for non-PCM, first 4 bytes often sample length
                try:
                    if len(chunk_data) >= 4:
                        sample_length = struct.unpack("<I", chunk_data[:4])[0]
                        results.append(("fact", "sample_length", sample_length))
                    else:
                        results.append(("fact", "raw", binascii.hexlify(chunk_data).decode()))
                except Exception as e:
                    results.append(("fact", "error", str(e)))

            elif chunk_id == b'cue ' and enumerate_all:
                # CUE markers
                try:
                    num_cues = struct.unpack("<I", chunk_data[:4])[0]
                    for i in range(num_cues):
                        base = 4 + i * 24
                        cue_data = chunk_data[base: base + 24]
                        if len(cue_data) == 24:
                            _, _, _, _, _, sample_offset = struct.unpack("<IIIIII", cue_data)
                            results.append(("cue ", f"marker_{i}", sample_offset))
                except Exception as e:
                    results.append(("cue ", "error", str(e)))

            elif chunk_id == b'LIST' and enumerate_all:
                # LIST -> INFO tags (INAM, IART, ICMT, etc.)
                try:
                    if len(chunk_data) >= 4:
                        list_type = chunk_data[:4].decode("ascii", errors="ignore")
                        results.append(("LIST", "type", list_type))
                        pos_in_list = 4
                        while pos_in_list + 8 <= len(chunk_data):
                            sub_id = chunk_data[pos_in_list:pos_in_list+4].decode("ascii", errors="ignore")
                            sub_size = struct.unpack("<I", chunk_data[pos_in_list+4:pos_in_list+8])[0]
                            start = pos_in_list + 8
                            end = start + sub_size
                            if end > len(chunk_data):
                                break
                            sub_val = chunk_data[start:end].decode("ascii", errors="ignore").rstrip("\x00").strip()
                            results.append(("LIST", sub_id, sub_val))
                            pos_in_list = end
                            if sub_size % 2 == 1:
                                pos_in_list += 1
                    else:
                        results.append(("LIST", "raw", binascii.hexlify(chunk_data[:32]).decode()))
                except Exception:
                    results.append(("LIST", "raw", binascii.hexlify(chunk_data[:32]).decode()))

            elif chunk_id == b'bext' and enumerate_all:
                # Broadcast extension (BWF)
                try:
                    desc = chunk_data[0:256].decode("ascii", errors="ignore").rstrip("\x00").strip()
                    origin = chunk_data[256:288].decode("ascii", errors="ignore").rstrip("\x00").strip()
                    date = chunk_data[320:330].decode("ascii", errors="ignore").strip()
                    time = chunk_data[330:338].decode("ascii", errors="ignore").strip()
                    results.append(("bext", "description", desc))
                    results.append(("bext", "originator", origin))
                    results.append(("bext", "datetime", f"{date} {time}".strip()))
                except Exception:
                    results.append(("bext", "raw", binascii.hexlify(chunk_data[:32]).decode()))

            else:
                # Unknown or unparsed chunk
                if enumerate_all:
                    hex_preview = binascii.hexlify(chunk_data[:16]).decode()
                    results.append((cid_str, "raw", hex_preview))

            # Word alignment (chunks are word-aligned)
            pos += 8 + chunk_size
            if chunk_size % 2 == 1:
                pos += 1

    return results, meta, seen_order

# --------------------------
# Duration helpers
# --------------------------
def _duration_from_headers(filepath):
    """
    Header-only duration calc:
      - If 'fact' has sample_length and we know sample_rate: samples / sr
      - Else if PCM-like: data_bytes / (sr * channels * (bits/8))
    Returns float seconds or None.
    """
    try:
        size = os.path.getsize(filepath)
        with open(filepath, "rb") as f:
            hdr = f.read(12)
            if len(hdr) < 12 or hdr[0:4] != b'RIFF' or hdr[8:12] != b'WAVE':
                return None

            sample_rate = None
            channels = None
            bits_per_sample = None
            data_bytes = None
            fact_samples = None

            pos = 12
            while pos + 8 <= size:
                f.seek(pos)
                ch = f.read(8)
                if len(ch) < 8:
                    break
                cid = ch[0:4]
                csz = struct.unpack("<I", ch[4:8])[0]
                payload_off = pos + 8

                if cid == b'fmt ' and csz >= 16:
                    f.seek(payload_off)
                    fmt = f.read(16)
                    wFormatTag, nChannels, nSamplesPerSec, nAvgBytesPerSec, nBlockAlign, wBitsPerSample = struct.unpack("<HHIIHH", fmt)
                    sample_rate = nSamplesPerSec
                    channels = nChannels
                    bits_per_sample = wBitsPerSample
                elif cid == b'fact' and csz >= 4:
                    f.seek(payload_off)
                    fact = f.read(4)
                    fact_samples = struct.unpack("<I", fact)[0]
                elif cid == b'data':
                    data_bytes = csz

                pos += 8 + csz
                if csz % 2 == 1:
                    pos += 1

            # Prefer fact (sample-length) if available
            if fact_samples and sample_rate:
                return round(fact_samples / float(sample_rate), 4)

            # Otherwise compute from raw PCM-ish parameters
            if sample_rate and channels and bits_per_sample and data_bytes is not None and bits_per_sample > 0:
                bytes_per_frame = channels * max(bits_per_sample // 8, 1)
                if bytes_per_frame > 0:
                    frames = data_bytes / float(bytes_per_frame)
                    return round(frames / float(sample_rate), 4)
    except Exception:
        return None
    return None

def get_duration(filepath):
    try:
        with wave.open(filepath, 'rb') as wf:
            return round(wf.getnframes() / float(wf.getframerate()), 4)
    except Exception:
        pass
    # Fallback for non-PCM / unsupported codecs (ADPCM, etc.)
    return _duration_from_headers(filepath)

# --------------------------
# Main
# --------------------------
def main():
    parser = argparse.ArgumentParser(description="WAV Metadata Explorer (ACID/SMPL and more).")
    parser.add_argument("directory", help="Directory containing WAV files.")
    parser.add_argument("-o", "--output", help="Output CSV filename. Default: <dirname>_metadata.csv")
    parser.add_argument("-n", "--num", type=int, default=500, help="Number of WAV files to scan.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress console output.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose console output. In --all mode, echo chunk rows to the console (ignored if -q).")
    parser.add_argument("--all", action="store_true", help="Enumerate all chunks (not just ACID/SMPL).")
    parser.add_argument("--survey", action="store_true", help="Count chunk IDs across scanned files.")
    parser.add_argument("--kinds", "-k", action="store_true", help="Print just chunk kinds per file and write <name>_kinds.csv.")
    parser.add_argument("--has", help="Only include files that contain any of these chunk IDs (comma-separated, e.g. 'acid,smpl,LIST').")
    parser.add_argument("--fallback", action="store_true", help="Estimate BPM/key with librosa if no metadata found")
    parser.add_argument("--features", action="store_true", help="Extract advanced audio features (MFCC, spectral, etc.) for ML analysis")
    parser.add_argument("--ml-ready", action="store_true", help="Output ML-ready CSV with normalized features and embeddings")
    args = parser.parse_args()

    # Normalize output name, preserving directories
    default_base = os.path.basename(os.path.normpath(args.directory))
    output_csv = safe_basename_for_csv(args.output or (default_base + "_metadata.csv"))

    # Parse --has
    wanted = None
    if args.has:
        wanted = set([w.strip().upper() for w in args.has.split(",") if w.strip()])

    # SURVEY MODE
    if args.survey:
        counts = Counter()
        files_scanned = 0

        for root, _, files in os.walk(args.directory):
            for file in files:
                if file.lower().endswith(".wav"):
                    filepath = os.path.join(root, file)
                    _, _, seen = parse_riff(filepath, enumerate_all=False)
                    if wanted:
                        upper_seen = {s.upper() for s in seen}
                        if not (upper_seen & wanted):
                            continue
                    counts.update(seen)
                    files_scanned += 1
                    if files_scanned >= args.num:
                        break
            if files_scanned >= args.num:
                break

        print("\n== Chunk ID Survey ==")
        for cid, c in counts.most_common():
            print(f"{cid:6s} : {c} files")
        print(f"\n[INFO] Scanned {files_scanned} file(s). Found {len(counts)} unique chunk ID(s).")

        survey_csv = safe_basename_for_csv((args.output or (default_base + "_survey.csv")))
        with open(survey_csv, "w", newline="") as csvfile:
            w = csv.writer(csvfile)
            w.writerow(["chunk_id", "files_with_chunk"])
            for cid, c in counts.most_common():
                w.writerow([cid, c])
        print(f"[INFO] Wrote survey to {survey_csv}")
        return

    # KINDS MODE (quick list of metadata kinds per file)
    if args.kinds:
        kinds_rows = []
        count = 0
        for root, _, files in os.walk(args.directory):
            for file in files:
                if file.lower().endswith(".wav"):
                    filepath = os.path.join(root, file)
                    _, _, seen = parse_riff(filepath, enumerate_all=False)
                    if wanted:
                        upper_seen = {s.upper() for s in seen}
                        if not (upper_seen & wanted):
                            continue
                    kinds_rows.append({"filename": filepath, "chunks": ",".join(seen)})
                    if not args.quiet:
                        print(f"{os.path.basename(filepath)} : {', '.join(seen) if seen else '(none)'}")
                    count += 1
                    if count >= args.num:
                        break
            if count >= args.num:
                break

        kinds_csv = safe_basename_for_csv((args.output or (default_base + "_kinds.csv")))
        with open(kinds_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["filename", "chunks"])
            writer.writeheader()
            writer.writerows(kinds_rows)
        print(f"\n[INFO] Wrote kinds for {len(kinds_rows)} files to {kinds_csv}")
        return

    # REGULAR / --ALL MODES
    rows = []
    count = 0

    for root, _, files in os.walk(args.directory):
        for file in files:
            if file.lower().endswith(".wav"):
                filepath = os.path.join(root, file)
                all_chunks, meta, seen = parse_riff(filepath, enumerate_all=args.all)

                # Filter by --has if requested
                if wanted:
                    upper_seen = {s.upper() for s in seen}
                    if not (upper_seen & wanted):
                        continue

                duration = get_duration(filepath)

                if args.all:
                    if all_chunks:
                        for cid, key, val in all_chunks:
                            rows.append({"filename": filepath, "chunk": cid, "key": key, "value": val})

                        if args.verbose and not args.quiet:
                            print(f"\n=== {os.path.basename(filepath)} ===")
                            for cid, key, val in all_chunks:
                                print(f"  {cid}.{key} = {val}")
                    else:
                        rows.append({"filename": filepath, "chunk": "NONE", "key": "", "value": ""})
                        if args.verbose and not args.quiet:
                            print(f"\n=== {os.path.basename(filepath)} ===")
                            print("  (no parseable chunks)")
                else:
                    # Print file header first
                    if not args.quiet:
                        print(f"\n=== {os.path.basename(filepath)} ===")

                    expected = diff = None
                    if meta["bpm"] and meta["acid_beats"] is not None and meta["acid_beats"] > 0:
                        expected = round((meta["acid_beats"] / meta["bpm"]) * 60, 4)
                        diff = round(duration - expected, 4) if duration else None

                    row = {
                        "filename": filepath,
                        "bpm": meta["bpm"],
                        "acid_root_note": midi_note_to_name(meta["acid_root_note"]),
                        "acid_beats": meta["acid_beats"],
                        "smpl_root_key": midi_note_to_name(meta["smpl_root_key"]),
                        "smpl_loop_start": meta["smpl_loop_start"],
                        "smpl_loop_end": meta["smpl_loop_end"],
                        "duration_sec": duration,
                        "expected_duration": expected,
                        "duration_diff": diff,
                        "other_chunks": ",".join([c for c in seen if c not in ("RIFF","WAVE","fmt ","data","acid","smpl")])
                    }

                    # --- Extract advanced audio features if requested ---
                    if args.features or args.ml_ready:
                        if not args.quiet:
                            print(f"   [Feature Extraction] Processing {os.path.basename(filepath)}...")

                        features = extract_advanced_audio_features(filepath)
                        if features:
                            row.update(features)
                        else:
                            if not args.quiet:
                                print(f"   [Warning] Could not extract features from {os.path.basename(filepath)}")

                    # --- Enhanced BPM/Key detection with filename parsing ---
                    if args.fallback:
                        estimates = estimate_librosa_metadata(filepath)
                        dur_sec = estimates.get("duration_sec")
                        bpm_val = estimates.get("estimated_bpm")
                        key_val = estimates.get("estimated_key")
                        bpm_source = estimates.get("bpm_source", "unknown")
                        key_source = estimates.get("key_source", "unknown")
                        filename_bpm = estimates.get("filename_bpm")
                        filename_key = estimates.get("filename_key")
                        detected_bpm = estimates.get("detected_bpm")
                        detected_key = estimates.get("detected_key")

                        # Update row with enhanced values (always use enhanced values when fallback is enabled)
                        if bpm_val is not None:
                            row["bpm"] = bpm_val
                        if key_val is not None:
                            row["smpl_root_key"] = key_val
                        if dur_sec is not None:
                            row["duration_sec"] = dur_sec

                        # Add additional metadata fields for analysis
                        if args.features or args.ml_ready:
                            row["bpm_source"] = bpm_source
                            row["key_source"] = key_source
                            row["filename_bpm"] = filename_bpm
                            row["filename_key"] = filename_key
                            row["detected_bpm"] = detected_bpm
                            row["detected_key"] = detected_key

                        if not args.quiet:
                            if bpm_val == "oneshot":
                                dur_str = f"{round(dur_sec, 3)} sec" if dur_sec else "(unknown)"
                                print(f"   [Enhanced] One-shot detected! Duration: {dur_str} | Key: {key_val or 'N/A'}")
                            else:
                                dur_str = f"{round(dur_sec, 3)} sec" if dur_sec else "(unknown)"
                                bpm_info = f"BPM: {bpm_val} ({bpm_source})"
                                key_info = f"Key: {key_val or 'N/A'} ({key_source})" if key_source != "unknown" else f"Key: {key_val or 'N/A'}"
                                print(f"   [Enhanced] {bpm_info}, {key_info}, Duration: {dur_str}")

                                # Show parsing details if filename parsing was used
                                if filename_bpm or filename_key:
                                    details = []
                                    if filename_bpm:
                                        details.append(f"Filename BPM: {filename_bpm}")
                                    if filename_key:
                                        details.append(f"Filename Key: {filename_key}")
                                    if detected_bpm and detected_bpm != bpm_val:
                                        details.append(f"Detected BPM: {detected_bpm}")
                                    if detected_key and detected_key != key_val:
                                        details.append(f"Detected Key: {detected_key}")
                                    if details:
                                        print(f"   [Details] {', '.join(details)}")

                    # Fallback for cases where no enhanced detection is used
                    elif not (meta["bpm"] is not None or meta["smpl_root_key"] is not None):
                        if not args.quiet:
                            print(f"   [Info] No ACID/SMPL metadata found. Use --fallback for enhanced detection.")

                    # Print final summary (after all processing is done)
                    if not args.quiet:
                        # Get final values from the row (after all processing)
                        bpm_val = row.get("bpm")
                        key_val = row.get("smpl_root_key")
                        dur_val = row.get("duration_sec")

                        # Summary line
                        if bpm_val is not None and bpm_val != "oneshot":
                            print(f"   BPM: {bpm_val} | Beats: {meta['acid_beats']} | Duration: {dur_val} sec")
                            if expected is not None:
                                print(f"   Expected: {expected} sec | Diff: {diff}")
                        elif bpm_val == "oneshot":
                            print(f"   One-shot sample | Duration: {dur_val} sec | Key: {key_val or 'N/A'}")
                        else:
                            print(f"   No ACID/SMPL metadata found. Duration: {dur_val} sec")

                        # Additional metadata
                        if key_val and bpm_val != "oneshot":
                            print(f"   Root Key: {key_val}")
                        if meta["smpl_loop_start"] is not None:
                            print(f"   Loop Points: {meta['smpl_loop_start']} -> {meta['smpl_loop_end']} samples")

                        # Other chunks found
                        others = [c for c in seen if c not in ("RIFF", "WAVE", "fmt ", "data", "acid", "smpl")]
                        if others:
                            print("   Other chunks:", ", ".join(others))

                    rows.append(row)

                    count += 1
                    if count >= args.num:
                        break

    # Determine fieldnames dynamically based on data
    if args.all:
        fieldnames = ["filename", "chunk", "key", "value"]
    else:
        # Base fieldnames
        base_fieldnames = ["filename", "bpm", "acid_root_note", "acid_beats", "smpl_root_key",
                          "smpl_loop_start", "smpl_loop_end", "duration_sec",
                          "expected_duration", "duration_diff", "other_chunks"]

        # If features were extracted, get all possible fieldnames from the data
        if (args.features or args.ml_ready) and rows:
            all_keys = set()
            for row in rows:
                all_keys.update(row.keys())
            # Keep base fields first, then add feature fields in sorted order
            feature_keys = sorted([k for k in all_keys if k not in base_fieldnames])
            fieldnames = base_fieldnames + feature_keys
        else:
            fieldnames = base_fieldnames

    # Write to CSV
    if args.ml_ready and not args.all:
        # For ML-ready output, also create a normalized version
        df = pd.DataFrame(rows)

        # Save raw features CSV
        df.to_csv(output_csv, index=False)

        # Create normalized version for ML
        ml_csv = output_csv.replace('.csv', '_ml_ready.csv')

        # Select only numeric columns for normalization
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        non_numeric_cols = [col for col in df.columns if col not in numeric_cols]

        if numeric_cols:
            scaler = StandardScaler()
            df_normalized = df.copy()
            df_normalized[numeric_cols] = scaler.fit_transform(df[numeric_cols])
            df_normalized.to_csv(ml_csv, index=False)

            print(f"\n[INFO] Wrote raw features for {len(rows)} entries to {output_csv}")
            print(f"[INFO] Wrote ML-ready normalized features to {ml_csv}")
        else:
            df.to_csv(output_csv, index=False)
            print(f"\n[INFO] Wrote metadata for {len(rows)} entries to {output_csv}")
    else:
        # Standard CSV output
        with open(output_csv, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        print(f"\n[INFO] Wrote metadata for {len(rows)} entries to {output_csv}")

if __name__ == "__main__":
    main()
