"""
Audio feature extraction for ML analysis.

Extracts 50+ spectral, rhythmic, and timbral features from audio files
using librosa.
"""

# canonical similarity vector: the ordered subset of the extracted features
# that describes *timbre and rhythm*, used for nearest-neighbour search. The
# raw dict also holds sample_rate, audio_length_samples, duration_sec, and
# beat_count -- deliberately EXCLUDED here: they carry file/scale information,
# not sonic character, and (being 10^4-10^6 in magnitude) would dominate a
# cosine over the small-magnitude timbral dims and collapse every result into
# one indistinguishable cluster. Bump FEATURE_SET_VERSION if this list changes,
# so stale vectors can be detected and re-derived.
FEATURE_KEYS = (
    "spectral_centroid_mean", "spectral_centroid_std",
    "spectral_rolloff_mean", "spectral_rolloff_std",
    "spectral_bandwidth_mean", "spectral_bandwidth_std",
    "zcr_mean", "zcr_std",
    "mfcc_1_mean", "mfcc_1_std", "mfcc_2_mean", "mfcc_2_std",
    "mfcc_3_mean", "mfcc_3_std", "mfcc_4_mean", "mfcc_4_std",
    "mfcc_5_mean", "mfcc_5_std", "mfcc_6_mean", "mfcc_6_std",
    "mfcc_7_mean", "mfcc_7_std", "mfcc_8_mean", "mfcc_8_std",
    "mfcc_9_mean", "mfcc_9_std", "mfcc_10_mean", "mfcc_10_std",
    "mfcc_11_mean", "mfcc_11_std", "mfcc_12_mean", "mfcc_12_std",
    "mfcc_13_mean", "mfcc_13_std",
    "chroma_mean", "chroma_std",
    "mel_mean", "mel_std",
    "tempo_librosa",
    "rms_mean", "rms_std",
    "spectral_contrast_mean", "spectral_contrast_std",
    "tonnetz_mean", "tonnetz_std",
)

FEATURE_SET_VERSION = 2   # 1 = pre-vector JSON only; 2 = adds the FEATURE_KEYS vector

FEATURE_DIMS = len(FEATURE_KEYS)


def vector_from_features(feats):
    """Project an extracted-features dict onto the canonical FEATURE_KEYS order,
    returning a plain list of floats (stdlib only; no numpy). Missing/non-finite
    values become 0.0. Returns None if `feats` is falsy."""
    if not feats:
        return None
    out = []
    for k in FEATURE_KEYS:
        v = feats.get(k)
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        if v != v or v in (float("inf"), float("-inf")):   # NaN / inf guard
            v = 0.0
        out.append(v)
    return out


def extract_audio_features(filepath):
    """
    Extract audio features for ML analysis.

    Returns dict with 50+ features (spectral, timbral, rhythmic),
    or None if the file is too short or unreadable.
    """
    import warnings
    warnings.filterwarnings("ignore")

    try:
        import librosa
        import numpy as np
    except ImportError:
        from acidcat.util.deps import require
        require("librosa", "numpy", group="analysis")
        return None

    try:
        y, sr = librosa.load(filepath, sr=None, mono=True)

        if len(y) < 256:
            return None

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
        features['tempo_librosa'] = float(np.atleast_1d(tempo)[0])
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
        return None
