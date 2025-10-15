#!/usr/bin/env python3
"""
audio_similarity.py

Advanced audio similarity search and recommendation system for acid_cat.
Provides functions for finding similar samples, clustering, and recommendations.
"""

import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.metrics.pairwise import cosine_similarity, euclidean_distances
from sklearn.neighbors import NearestNeighbors
from sklearn.cluster import KMeans, DBSCAN
from sklearn.decomposition import PCA
import warnings
warnings.filterwarnings('ignore')


class AudioSimilarityEngine:
    """
    Advanced similarity search engine for audio samples.
    """

    def __init__(self, csv_path=None, df=None):
        """
        Initialize with either a CSV file path or pandas DataFrame.
        """
        if csv_path:
            self.df = pd.read_csv(csv_path)
        elif df is not None:
            self.df = df.copy()
        else:
            raise ValueError("Must provide either csv_path or df parameter")

        self.feature_cols = None
        self.scaler = StandardScaler()
        self.features_scaled = None
        self.nn_model = None
        self._prepare_features()

    def _prepare_features(self):
        """Identify and prepare audio features for analysis."""
        # Identify numeric columns (excluding metadata)
        numeric_cols = self.df.select_dtypes(include=[np.number]).columns.tolist()

        # Exclude metadata columns and keep only audio features
        exclude_patterns = ['unnamed', 'index', 'expected_duration', 'duration_diff']
        self.feature_cols = [col for col in numeric_cols
                           if not any(pattern in col.lower() for pattern in exclude_patterns)]

        if not self.feature_cols:
            raise ValueError("No suitable audio features found in the dataset")

        # Prepare and scale features
        X = self.df[self.feature_cols].fillna(0)
        self.features_scaled = self.scaler.fit_transform(X)

        # Initialize nearest neighbors model
        n_neighbors = min(10, len(self.df))
        self.nn_model = NearestNeighbors(n_neighbors=n_neighbors, metric='cosine')
        self.nn_model.fit(self.features_scaled)

        print(f"Initialized with {len(self.df)} samples and {len(self.feature_cols)} features")

    def find_similar_samples(self, target_sample, n_similar=5, method='cosine'):
        """
        Find samples similar to target sample.

        Args:
            target_sample: Can be sample index (int), filename (str), or feature vector (array)
            n_similar: Number of similar samples to return
            method: Similarity metric ('cosine', 'euclidean', 'knn')

        Returns:
            DataFrame with similar samples and similarity scores
        """
        # Get target features
        if isinstance(target_sample, int):
            target_idx = target_sample
            target_features = self.features_scaled[target_idx].reshape(1, -1)
        elif isinstance(target_sample, str):
            # Find by filename
            matches = self.df[self.df['filename'].str.contains(target_sample, case=False)]
            if matches.empty:
                raise ValueError(f"No sample found matching filename: {target_sample}")
            target_idx = matches.index[0]
            target_features = self.features_scaled[target_idx].reshape(1, -1)
        else:
            # Assume it's a feature vector
            target_features = np.array(target_sample).reshape(1, -1)
            target_features = self.scaler.transform(target_features)
            target_idx = None

        # Calculate similarities
        if method == 'cosine':
            similarities = cosine_similarity(target_features, self.features_scaled)[0]
        elif method == 'euclidean':
            distances = euclidean_distances(target_features, self.features_scaled)[0]
            similarities = 1 / (1 + distances)  # Convert distance to similarity
        elif method == 'knn':
            distances, indices = self.nn_model.kneighbors(target_features, n_neighbors=n_similar+1)
            if target_idx is not None:
                # Remove target sample from results
                mask = indices[0] != target_idx
                indices = indices[0][mask][:n_similar]
                similarities = 1 - distances[0][mask][:n_similar]
            else:
                indices = indices[0][:n_similar]
                similarities = 1 - distances[0][:n_similar]

            results = self.df.iloc[indices].copy()
            results['similarity_score'] = similarities
            return results[['filename', 'bpm', 'duration_sec', 'similarity_score']]

        # Get top similar samples (excluding target if it's in the dataset)
        if target_idx is not None:
            similarities[target_idx] = -1  # Exclude target sample

        similar_indices = np.argsort(similarities)[::-1][:n_similar]

        results = self.df.iloc[similar_indices].copy()
        results['similarity_score'] = similarities[similar_indices]

        return results[['filename', 'bpm', 'duration_sec', 'similarity_score']]

    def cluster_samples(self, n_clusters=5, method='kmeans'):
        """
        Cluster audio samples based on their features.

        Args:
            n_clusters: Number of clusters (for kmeans)
            method: Clustering method ('kmeans', 'dbscan')

        Returns:
            DataFrame with cluster assignments
        """
        if method == 'kmeans':
            clusterer = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
            cluster_labels = clusterer.fit_predict(self.features_scaled)
        elif method == 'dbscan':
            clusterer = DBSCAN(eps=0.5, min_samples=2)
            cluster_labels = clusterer.fit_predict(self.features_scaled)
        else:
            raise ValueError("Method must be 'kmeans' or 'dbscan'")

        # Add cluster labels to dataframe
        result_df = self.df.copy()
        result_df['cluster'] = cluster_labels

        return result_df

    def get_cluster_characteristics(self, clustered_df, key_features=None):
        """
        Analyze characteristics of each cluster.

        Args:
            clustered_df: DataFrame with cluster assignments
            key_features: List of features to analyze (uses defaults if None)

        Returns:
            DataFrame with cluster characteristics
        """
        if key_features is None:
            key_features = ['bpm', 'duration_sec', 'spectral_centroid_mean',
                          'mfcc_1_mean', 'rms_mean', 'zcr_mean']

        # Filter to features that exist in the data
        available_features = [f for f in key_features if f in clustered_df.columns]

        if not available_features:
            available_features = self.feature_cols[:6]  # Use first 6 features as fallback

        cluster_chars = clustered_df.groupby('cluster')[available_features].agg(['mean', 'std'])
        cluster_counts = clustered_df['cluster'].value_counts().sort_index()

        return cluster_chars, cluster_counts

    def recommend_by_criteria(self, target_bpm=None, target_duration=None,
                            target_key=None, bpm_tolerance=10,
                            duration_tolerance=2.0, n_recommendations=5):
        """
        Recommend samples based on musical criteria.

        Args:
            target_bpm: Target BPM (allows ±bpm_tolerance)
            target_duration: Target duration in seconds (allows ±duration_tolerance)
            target_key: Target musical key
            bpm_tolerance: BPM tolerance for matching
            duration_tolerance: Duration tolerance for matching
            n_recommendations: Number of recommendations to return

        Returns:
            DataFrame with recommended samples
        """
        candidates = self.df.copy()

        # Filter by BPM
        if target_bpm is not None and 'bpm' in candidates.columns:
            candidates = candidates[
                (candidates['bpm'] >= target_bpm - bpm_tolerance) &
                (candidates['bpm'] <= target_bpm + bpm_tolerance) &
                (candidates['bpm'].notna())
            ]

        # Filter by duration
        if target_duration is not None and 'duration_sec' in candidates.columns:
            candidates = candidates[
                (candidates['duration_sec'] >= target_duration - duration_tolerance) &
                (candidates['duration_sec'] <= target_duration + duration_tolerance) &
                (candidates['duration_sec'].notna())
            ]

        # Filter by key
        if target_key is not None:
            key_matches = pd.Series(False, index=candidates.index)

            if 'smpl_root_key' in candidates.columns:
                key_matches |= (candidates['smpl_root_key'] == target_key)

            if 'acid_root_note' in candidates.columns:
                key_matches |= (candidates['acid_root_note'] == target_key)

            candidates = candidates[key_matches]

        # Return top matches
        return candidates.head(n_recommendations)

    def get_feature_importance(self, n_components=None):
        """
        Analyze feature importance using PCA.

        Args:
            n_components: Number of PCA components (uses all if None)

        Returns:
            Dictionary with PCA results and feature importance
        """
        if n_components is None:
            n_components = min(len(self.feature_cols), len(self.df))

        pca = PCA(n_components=n_components)
        pca.fit(self.features_scaled)

        # Feature importance for first few components
        feature_importance = {}
        for i in range(min(3, n_components)):
            component_features = list(zip(self.feature_cols, pca.components_[i]))
            component_features.sort(key=lambda x: abs(x[1]), reverse=True)
            feature_importance[f'PC{i+1}'] = component_features[:10]  # Top 10 features

        return {
            'explained_variance_ratio': pca.explained_variance_ratio_,
            'cumulative_variance': np.cumsum(pca.explained_variance_ratio_),
            'feature_importance': feature_importance,
            'n_components_95': np.argmax(np.cumsum(pca.explained_variance_ratio_) >= 0.95) + 1
        }


def find_similar_samples_cli(csv_path, target_sample, n_similar=5, method='cosine'):
    """
    Command-line interface for finding similar samples.

    Args:
        csv_path: Path to CSV file with audio features
        target_sample: Sample to find similarities for (index or filename)
        n_similar: Number of similar samples to return
        method: Similarity method ('cosine', 'euclidean', 'knn')
    """
    engine = AudioSimilarityEngine(csv_path=csv_path)

    try:
        # Try as integer index first
        target = int(target_sample)
    except ValueError:
        # Use as filename
        target = target_sample

    results = engine.find_similar_samples(target, n_similar=n_similar, method=method)

    print(f"\nSamples similar to: {target}")
    print("=" * 60)
    print(results.to_string(index=False))

    return results


def cluster_samples_cli(csv_path, n_clusters=5, method='kmeans', output_path=None):
    """
    Command-line interface for clustering samples.

    Args:
        csv_path: Path to CSV file with audio features
        n_clusters: Number of clusters
        method: Clustering method ('kmeans', 'dbscan')
        output_path: Optional path to save clustered results
    """
    engine = AudioSimilarityEngine(csv_path=csv_path)
    clustered_df = engine.cluster_samples(n_clusters=n_clusters, method=method)

    cluster_chars, cluster_counts = engine.get_cluster_characteristics(clustered_df)

    print(f"\nClustering Results ({method}, k={n_clusters})")
    print("=" * 60)
    print(f"Samples per cluster:")
    print(cluster_counts)

    print(f"\nCluster characteristics:")
    print(cluster_chars)

    if output_path:
        clustered_df.to_csv(output_path, index=False)
        print(f"\nSaved clustered results to: {output_path}")

    return clustered_df


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Audio similarity search and clustering")
    parser.add_argument("csv_path", help="Path to CSV file with audio features")

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Similar command
    similar_parser = subparsers.add_parser('similar', help='Find similar samples')
    similar_parser.add_argument("target", help="Target sample (index or filename)")
    similar_parser.add_argument("-n", "--num", type=int, default=5,
                               help="Number of similar samples to find")
    similar_parser.add_argument("-m", "--method", choices=['cosine', 'euclidean', 'knn'],
                               default='cosine', help="Similarity method")

    # Cluster command
    cluster_parser = subparsers.add_parser('cluster', help='Cluster samples')
    cluster_parser.add_argument("-k", "--clusters", type=int, default=5,
                               help="Number of clusters")
    cluster_parser.add_argument("-m", "--method", choices=['kmeans', 'dbscan'],
                               default='kmeans', help="Clustering method")
    cluster_parser.add_argument("-o", "--output", help="Output path for clustered CSV")

    args = parser.parse_args()

    if args.command == 'similar':
        find_similar_samples_cli(args.csv_path, args.target,
                                args.num, args.method)
    elif args.command == 'cluster':
        cluster_samples_cli(args.csv_path, args.clusters,
                           args.method, args.output)
    else:
        parser.print_help()