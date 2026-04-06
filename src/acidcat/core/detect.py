"""
BPM and key detection.

Combines multiple strategies: RIFF chunk metadata, filename parsing,
and librosa audio analysis with smart validation/fallback.
"""

import os
import re

# ── Filename parsing ───────────────────────────────────────────────

def parse_bpm_from_filename(filepath):
    """Extract BPM from filename using common patterns. Returns int or None."""
    filename = os.path.basename(filepath)
    bpm_patterns = [
        r'(\d{2,3})\s*bpm',
        r'(\d{2,3})\s*BPM',
        r'bpm\s*(\d{2,3})',
        r'BPM\s*(\d{2,3})',
        r'(\d{2,3})bpm',
        r'(\d{2,3})BPM',
        r'_(\d{2,3})_',
        r'-(\d{2,3})-',
        r'\s(\d{2,3})\s',
    ]
    for pattern in bpm_patterns:
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            bpm = int(match.group(1))
            if 60 <= bpm <= 200:
                return bpm
    return None


def parse_key_from_filename(filepath):
    """Extract musical key from filename. Returns string like 'C#m' or None."""
    filename = os.path.basename(filepath).replace('_', ' ').replace('-', ' ')
    key_patterns = [
        r'\b([A-G]#?)\s*major\b',
        r'\b([A-G]#?)\s*maj\b',
        r'\b([A-G]#?)major\b',
        r'\b([A-G]#?)maj\b',
        r'\b([A-G]#?)\s*minor\b',
        r'\b([A-G]#?)\s*min\b',
        r'\b([A-G]#?)minor\b',
        r'\b([A-G]#?)min\b',
        r'\b([A-G]#?)\s*M\b',
        r'\b([A-G]#?)\s*m\b',
        r'\b([A-G]#?)m\b',
    ]
    for pattern in key_patterns:
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            note = match.group(1).upper()
            key_text = match.group(0).lower()
            if any(x in key_text for x in ['min', 'm']):
                return f"{note}m"
            else:
                return note
    return None


# ── Validation / improvement ───────────────────────────────────────

def validate_and_improve_bpm(detected_bpm, filename_bpm, confidence_threshold=20):
    """
    Validate detected BPM against filename BPM and choose the best value.

    Returns:
        (final_bpm, source) where source is 'detected', 'filename', or 'corrected'.
    """
    if filename_bpm is None:
        return detected_bpm, 'detected'
    if detected_bpm is None:
        return filename_bpm, 'filename'
    if not (60 <= detected_bpm <= 200):
        return filename_bpm, 'filename'

    diff = abs(detected_bpm - filename_bpm)

    if diff <= confidence_threshold:
        return detected_bpm, 'detected'
    if abs(detected_bpm * 2 - filename_bpm) <= confidence_threshold:
        return detected_bpm * 2, 'corrected'
    if abs(detected_bpm / 2 - filename_bpm) <= confidence_threshold:
        return detected_bpm / 2, 'corrected'
    if abs(detected_bpm * 1.5 - filename_bpm) <= confidence_threshold:
        return detected_bpm * 1.5, 'corrected'
    if abs(detected_bpm / 1.5 - filename_bpm) <= confidence_threshold:
        return detected_bpm / 1.5, 'corrected'

    return filename_bpm, 'filename'


def improve_key_detection(detected_key, filename_key):
    """
    Combine detected key with filename key for better accuracy.

    Returns:
        (final_key, source) where source is 'detected' or 'filename'.
    """
    if filename_key is None:
        return detected_key, 'detected'
    if detected_key is None:
        return filename_key, 'filename'
    if detected_key == filename_key:
        return detected_key, 'detected'
    return filename_key, 'filename'


# ── Librosa-based estimation ───────────────────────────────────────

def estimate_librosa_metadata(filepath):
    """
    Estimate BPM/key/duration using librosa + filename parsing.

    Returns dict with keys: estimated_bpm, estimated_key, duration_sec,
    bpm_source, key_source, filename_bpm, filename_key, detected_bpm, detected_key.
    """
    import warnings
    warnings.filterwarnings("ignore")

    try:
        import librosa
        import numpy as np
    except ImportError:
        from acidcat.util.deps import require
        require("librosa", "numpy", group="analysis")
        return {}

    try:
        y, sr = librosa.load(filepath, sr=None, mono=True)
        duration_sec = round(len(y) / sr, 4) if sr and len(y) > 0 else None

        if len(y) < 256:
            return {
                "estimated_bpm": "oneshot",
                "estimated_key": None,
                "duration_sec": duration_sec,
                "bpm_source": "oneshot",
                "key_source": None,
            }

        # BPM
        detected_bpm = None
        try:
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            tempos_1 = librosa.beat.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
            tempos_2 = librosa.beat.tempo(y=y, sr=sr, aggregate=None)
            all_tempos = []
            if tempos_1.size > 0:
                all_tempos.extend(tempos_1)
            if tempos_2.size > 0:
                all_tempos.extend(tempos_2)
            if all_tempos:
                detected_bpm = round(float(np.median(all_tempos)), 2)
        except Exception:
            pass

        filename_bpm = parse_bpm_from_filename(filepath)
        final_bpm, bpm_source = validate_and_improve_bpm(detected_bpm, filename_bpm)

        # Key
        detected_key = None
        try:
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
            if chroma.size > 0:
                chroma_median = np.median(chroma, axis=1)
                if np.any(chroma_median > 0):
                    note_number = int(np.argmax(chroma_median))
                    note_names = ["C", "C#", "D", "D#", "E", "F",
                                  "F#", "G", "G#", "A", "A#", "B"]
                    detected_key = note_names[note_number]
        except Exception:
            pass

        filename_key = parse_key_from_filename(filepath)
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
            "detected_key": detected_key,
        }

    except Exception:
        return {
            "estimated_bpm": None,
            "estimated_key": None,
            "duration_sec": None,
            "bpm_source": "failed",
            "key_source": "failed",
            "filename_bpm": parse_bpm_from_filename(filepath),
            "filename_key": parse_key_from_filename(filepath),
            "detected_bpm": None,
            "detected_key": None,
        }
