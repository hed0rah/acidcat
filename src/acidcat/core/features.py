"""
Audio feature extraction for ML analysis.

Extracts 50+ spectral, rhythmic, and timbral features from audio files
using librosa.
"""

def extract_audio_features(filepath):
    """
    Extract comprehensive audio features for ML analysis.

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
