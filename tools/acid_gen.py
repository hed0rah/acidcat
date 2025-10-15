import os
import argparse
import librosa
import numpy as np
import csv

def estimate_metadata(path):
    y, sr = librosa.load(path, sr=None, mono=True)

    # BPM estimate
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(tempo)

    # Key estimate (simple: strongest chroma bin)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = chroma.mean(axis=1)
    note_number = np.argmax(chroma_mean)
    note_names = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
    root_key = note_names[note_number]

    duration_sec = librosa.get_duration(y=y, sr=sr)

    return {
        "filename": os.path.basename(path),
        "estimated_bpm": round(bpm, 2),
        "estimated_key": root_key,
        "duration_sec": round(duration_sec, 3)
    }

def main():
    parser = argparse.ArgumentParser(description="Estimate BPM/key for WAVs without ACID/SMPL metadata")
    parser.add_argument("input_dir", help="Directory of WAV files")
    parser.add_argument("-o", "--output", default="acid_gen_output.csv", help="Output CSV filename")
    args = parser.parse_args()

    rows = []
    for fname in os.listdir(args.input_dir):
        if fname.lower().endswith(".wav"):
            fpath = os.path.join(args.input_dir, fname)
            try:
                rows.append(estimate_metadata(fpath))
            except Exception as e:
                print(f"[WARN] Failed on {fname}: {e}")

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["filename","estimated_bpm","estimated_key","duration_sec"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[INFO] Wrote {len(rows)} entries to {args.output}")

if __name__ == "__main__":
    main()
