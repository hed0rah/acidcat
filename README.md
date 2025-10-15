# acid_cat

Advanced WAV metadata explorer and ML-ready audio analysis toolkit.
Scans RIFF chunks to find ACID info (BPM, beats, meter, root), SMPL loop points, BWF `bext`, `LIST/INFO` tags, cues, and more.
Enhanced with comprehensive audio feature extraction, similarity search, and ML analysis capabilities.

## New ML/DL Features

- **Advanced Audio Features**: MFCC, spectral features, chroma, tonnetz, and more
- **ML-Ready Output**: Normalized feature vectors for machine learning
- **Similarity Search**: Find similar samples using cosine similarity and k-NN
- **Clustering**: Group samples by audio characteristics
- **Text Search**: Add descriptions and search samples by text
- **Jupyter Integration**: Comprehensive notebooks for ML experiments
- **Recommendation System**: Content-based sample recommendations

## Core Features

- Robust EOF scanning (won't miss trailing chunks)
- Summary mode: BPM, beats, expected duration, SMPL loop points
- `--all` mode: full per-chunk key/value dump to CSV
- `--kinds` mode: quick overview of what metadata each file has
- `--survey` mode: count chunk types across a directory tree
- `--has acid,smpl,...` filter to target exactly what you want
- `--fallback` mode: estimate BPM/key using librosa if no ACID/SMPL metadata is found
- `--features` mode: extract comprehensive audio features for ML analysis
- `--ml-ready` mode: output normalized features for machine learning
- `-v` verbose printing in `--all` mode (and `-q` to suppress)

## Installation

Clone the repo and install dependencies:

    git clone https://github.com/jrelo/acid_cat.git
    cd acid_cat
    pip install -r requirements.txt

Requirements include:

    librosa==0.10.1
    numpy==1.26.4
    pandas>=2.0.0
    scikit-learn>=1.3.0
    matplotlib>=3.7.0
    seaborn>=0.12.0
    jupyter>=1.0.0

## Basic Usage

### Traditional Metadata Extraction

    # Summary view of acidized files (prints to console + writes CSV)
    python acid_cat.py "D:\Audio\Loops" --has acid -n 20

    # Chunk kinds per file (prints + writes <name>_kinds.csv)
    python acid_cat.py "D:\Audio\Loops" --kinds -n 50

    # Full chunk dump to CSV (quiet)
    python acid_cat.py "D:\Audio\Loops" --all --has acid -n 200 -q

    # Survey a large tree to see what metadata appears where
    python acid_cat.py "D:\Audio" --survey -n 2000

    # Estimate BPM/key using librosa when ACID/SMPL metadata is missing
    python acid_cat.py "D:\Audio\Loops" --fallback -n 100

### ML-Enhanced Analysis

    # Extract comprehensive audio features for ML analysis
    python acid_cat.py "D:\Audio\Loops" --features -n 100

    # Generate ML-ready normalized datasets
    python acid_cat.py "D:\Audio\Loops" --ml-ready -n 100

    # Combine with existing filters
    python acid_cat.py "D:\Audio\Loops" --features --has acid --fallback -n 200

## Advanced ML Tools

### 1. Similarity Search

Find samples similar to a target sample:

    # Find 5 similar samples to sample index 0
    python audio_similarity.py samples_metadata.csv similar 0 -n 5

    # Find similar samples by filename (partial match)
    python audio_similarity.py samples_metadata.csv similar "kick_drum" -n 3

    # Use different similarity metrics
    python audio_similarity.py samples_metadata.csv similar 0 -m euclidean

### 2. Sample Clustering

Group samples by audio characteristics:

    # K-means clustering with 5 clusters
    python audio_similarity.py samples_metadata.csv cluster -k 5

    # DBSCAN clustering (density-based)
    python audio_similarity.py samples_metadata.csv cluster -m dbscan

    # Save clustered results
    python audio_similarity.py samples_metadata.csv cluster -k 5 -o clustered_samples.csv

### 3. Text-Based Search and Tagging

Add descriptions and search by text:

    # Interactive tagging session
    python text_search.py samples_metadata.csv interactive

    # Add description and tags to a sample
    python text_search.py samples_metadata.csv tag "drum_loop" "Energetic 4/4 kick pattern" --tags "drums,electronic,loop"

    # Search by text query
    python text_search.py samples_metadata.csv search "energetic drum"

    # Search by specific tags
    python text_search.py samples_metadata.csv tags "drums,electronic"

## Jupyter Notebook Analysis

Launch the comprehensive analysis notebook:

    jupyter notebook audio_ml_analysis.ipynb

The notebook includes:
- **Data Exploration**: Visualize feature distributions and correlations
- **Dimensionality Reduction**: PCA and t-SNE visualizations
- **Clustering Analysis**: K-means and DBSCAN clustering
- **Similarity Search**: Content-based recommendation system
- **Feature Importance**: Understand which features matter most

## ML Workflow Example

1. **Extract Features**: Generate comprehensive audio features
   ```bash
   python acid_cat.py "my_samples/" --ml-ready -n 1000
   ```

2. **Add Descriptions**: Tag samples with text descriptions
   ```bash
   python text_search.py my_samples_metadata.csv interactive
   ```

3. **Analyze and Cluster**: Explore patterns in your sample library
   ```bash
   python audio_similarity.py my_samples_metadata.csv cluster -k 10 -o clustered.csv
   ```

4. **Find Similarities**: Build recommendation systems
   ```bash
   python audio_similarity.py my_samples_metadata.csv similar "my_favorite_sample" -n 10
   ```

5. **ML Experiments**: Use Jupyter notebook for advanced analysis
   ```bash
   jupyter notebook audio_ml_analysis.ipynb
   ```

## Output Files

- `*_metadata.csv`: Raw audio features and metadata
- `*_ml_ready.csv`: Normalized features for ML (when using `--ml-ready`)
- `*_tags.json`: Text descriptions and tags database
- `*_enhanced.csv`: Combined audio features + text descriptions
- `*_clusters.csv`: Samples with cluster assignments

## Feature Extraction Details

The enhanced `acid_cat` extracts 50+ audio features including:

**Spectral Features**:
- Spectral centroid, rolloff, bandwidth
- Spectral contrast
- Zero crossing rate

**Timbral Features**:
- MFCC coefficients (1-13)
- Chroma features
- Mel-frequency features
- Tonnetz (tonal centroid features)

**Rhythmic Features**:
- Tempo estimation
- Beat tracking
- RMS energy

**Metadata**:
- ACID chunk data (BPM, key, beats)
- SMPL loop points
- Duration and sample rate

## Use Cases

### Music Production
- Find samples that match your track's key and tempo
- Discover similar-sounding samples for layering
- Organize large sample libraries by musical characteristics

### Machine Learning Research
- Train models for genre classification
- Develop automatic music tagging systems
- Study relationships between audio features and perception

### Sample Library Management
- Automatically tag and categorize samples
- Build smart recommendation systems
- Create dynamic playlists based on audio similarity

### Audio Analysis
- Analyze characteristics of different producers/labels
- Study evolution of electronic music styles
- Research correlations between audio features and popularity

## Contributing

Contributions welcome! Areas for improvement:
- Additional audio feature extractors
- More sophisticated similarity metrics
- Integration with music databases
- Real-time analysis capabilities
- Web interface for sample exploration
