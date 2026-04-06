"""
acidcat similar -- find similar audio samples or cluster them.
"""

import os
import sys

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.metrics.pairwise import cosine_similarity, euclidean_distances
from sklearn.neighbors import NearestNeighbors
from sklearn.cluster import KMeans, DBSCAN
from sklearn.decomposition import PCA

from acidcat.core.formats import output

import warnings
warnings.filterwarnings('ignore')


class AudioSimilarityEngine:
    """Similarity search engine for audio samples."""

    def __init__(self, csv_path=None, df=None):
        if csv_path:
            self.df = pd.read_csv(csv_path)
        elif df is not None:
            self.df = df.copy()
        else:
            raise ValueError("Must provide either csv_path or df")

        self.feature_cols = None
        self.scaler = StandardScaler()
        self.features_scaled = None
        self.nn_model = None
        self._prepare_features()

    def _prepare_features(self):
        numeric_cols = self.df.select_dtypes(include=[np.number]).columns.tolist()
        exclude_patterns = ['unnamed', 'index', 'expected_duration', 'duration_diff']
        self.feature_cols = [
            col for col in numeric_cols
            if not any(p in col.lower() for p in exclude_patterns)
        ]
        if not self.feature_cols:
            raise ValueError("No suitable audio features found in the dataset")

        X = self.df[self.feature_cols].fillna(0)
        self.features_scaled = self.scaler.fit_transform(X)

        n_neighbors = min(10, len(self.df))
        self.nn_model = NearestNeighbors(n_neighbors=n_neighbors, metric='cosine')
        self.nn_model.fit(self.features_scaled)

    def find_similar(self, target_sample, n_similar=5, method='cosine'):
        """Find samples similar to target (index, filename, or feature vector)."""
        if isinstance(target_sample, int):
            target_idx = target_sample
            target_features = self.features_scaled[target_idx].reshape(1, -1)
        elif isinstance(target_sample, str):
            matches = self.df[self.df['filename'].str.contains(target_sample, case=False)]
            if matches.empty:
                raise ValueError(f"No sample found matching: {target_sample}")
            target_idx = matches.index[0]
            target_features = self.features_scaled[target_idx].reshape(1, -1)
        else:
            target_features = np.array(target_sample).reshape(1, -1)
            target_features = self.scaler.transform(target_features)
            target_idx = None

        if method == 'cosine':
            similarities = cosine_similarity(target_features, self.features_scaled)[0]
        elif method == 'euclidean':
            distances = euclidean_distances(target_features, self.features_scaled)[0]
            similarities = 1 / (1 + distances)
        elif method == 'knn':
            distances, indices = self.nn_model.kneighbors(target_features, n_neighbors=n_similar + 1)
            if target_idx is not None:
                mask = indices[0] != target_idx
                indices = indices[0][mask][:n_similar]
                similarities = 1 - distances[0][mask][:n_similar]
            else:
                indices = indices[0][:n_similar]
                similarities = 1 - distances[0][:n_similar]
            results = self.df.iloc[indices].copy()
            results['similarity_score'] = similarities
            return results[['filename', 'bpm', 'duration_sec', 'similarity_score']]
        else:
            raise ValueError(f"Unknown method: {method}")

        if target_idx is not None:
            similarities[target_idx] = -1
        similar_indices = np.argsort(similarities)[::-1][:n_similar]
        results = self.df.iloc[similar_indices].copy()
        results['similarity_score'] = similarities[similar_indices]
        return results[['filename', 'bpm', 'duration_sec', 'similarity_score']]

    def cluster(self, n_clusters=5, method='kmeans'):
        """Cluster samples by audio features."""
        if method == 'kmeans':
            clusterer = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        elif method == 'dbscan':
            clusterer = DBSCAN(eps=0.5, min_samples=2)
        else:
            raise ValueError(f"Unknown method: {method}")

        labels = clusterer.fit_predict(self.features_scaled)
        result_df = self.df.copy()
        result_df['cluster'] = labels
        return result_df

    def feature_importance(self, n_components=None):
        """PCA-based feature importance analysis."""
        if n_components is None:
            n_components = min(len(self.feature_cols), len(self.df))
        pca = PCA(n_components=n_components)
        pca.fit(self.features_scaled)

        importance = {}
        for i in range(min(3, n_components)):
            pairs = list(zip(self.feature_cols, pca.components_[i]))
            pairs.sort(key=lambda x: abs(x[1]), reverse=True)
            importance[f'PC{i+1}'] = pairs[:10]

        return {
            'explained_variance_ratio': pca.explained_variance_ratio_.tolist(),
            'cumulative_variance': np.cumsum(pca.explained_variance_ratio_).tolist(),
            'feature_importance': importance,
            'n_components_95': int(np.argmax(np.cumsum(pca.explained_variance_ratio_) >= 0.95) + 1),
        }


def register(subparsers):
    p = subparsers.add_parser("similar", help="Find similar samples or cluster by features.")
    p.add_argument("csv_path", help="CSV file with audio features.")

    sub = p.add_subparsers(dest="subcmd")

    # find
    find_p = sub.add_parser("find", help="Find similar samples.")
    find_p.add_argument("target", help="Target sample (index or filename).")
    find_p.add_argument("-n", "--num", type=int, default=5, help="Number of results.")
    find_p.add_argument("-m", "--method", choices=["cosine", "euclidean", "knn"],
                        default="cosine", help="Similarity method.")

    # cluster
    cluster_p = sub.add_parser("cluster", help="Cluster samples.")
    cluster_p.add_argument("-k", "--clusters", type=int, default=5, help="Number of clusters.")
    cluster_p.add_argument("-m", "--method", choices=["kmeans", "dbscan"],
                           default="kmeans", help="Clustering method.")
    cluster_p.add_argument("-o", "--output", help="Output CSV with cluster labels.")

    p.add_argument("-f", "--format", default="table", choices=["table", "json", "csv"])
    p.set_defaults(func=run)


def run(args):
    engine = AudioSimilarityEngine(csv_path=args.csv_path)
    subcmd = getattr(args, 'subcmd', None)
    fmt_name = getattr(args, 'format', 'table')

    if subcmd == "find":
        try:
            target = int(args.target)
        except ValueError:
            target = args.target
        results = engine.find_similar(target, n_similar=args.num, method=args.method)
        print(f"\nSamples similar to: {args.target}")
        print("=" * 60)
        print(results.to_string(index=False))
        return 0

    elif subcmd == "cluster":
        clustered = engine.cluster(n_clusters=args.clusters, method=args.method)
        counts = clustered['cluster'].value_counts().sort_index()
        print(f"\nClustering ({args.method}, k={args.clusters})")
        print("=" * 60)
        print("Samples per cluster:")
        print(counts.to_string())
        if getattr(args, 'output', None):
            clustered.to_csv(args.output, index=False)
            print(f"\nSaved to: {args.output}")
        return 0

    else:
        print("Usage: acidcat similar CSV_PATH {find|cluster} ...", file=sys.stderr)
        return 1
