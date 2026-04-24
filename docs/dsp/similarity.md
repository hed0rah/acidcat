# Similarity and Clustering

How acidcat finds "samples that sound like this one" and groups
libraries into clusters. Covers cosine distance over feature
vectors, k-means and related clustering algorithms, and the design
tradeoffs behind the current and proposed implementations.

Last updated: 2026-04-23

---

## What it is

**Similarity search**: given a reference sample, return the N
closest other samples by some distance measure. Implemented in
`commands/similar.py` and exposed via the MCP `find_similar`
tool.

**Clustering**: given the full feature matrix, partition samples
into groups of perceptually similar content. Exposed via
`acidcat similar CSV cluster`.

Both tasks operate on the feature vectors described in
`feature_pipeline.md`. The math is standard unsupervised machine
learning; the interesting choices are in which features to use,
how to weight them, and how to present results.

---

## The distance question

Given two feature vectors `a` and `b`, each 50-dim, what distance
metric correlates best with "these samples sound alike"?

### Euclidean distance

```
    d(a, b) = sqrt( sum of (a[i] - b[i])^2 )
```

Simple, interpretable, fails when features have different scales.
A 44100 vs 48000 sample rate contributes 3900² to the squared
distance, while a 0.5 vs 0.8 zcr_mean contributes 0.09². The
distance is dominated by whichever features happen to have the
biggest magnitude.

Not used directly. Would require normalization first.

### Cosine similarity

```
    cos(a, b) = (a · b) / (||a|| * ||b||)
```

Angle between the vectors, ignoring magnitude. Scale-invariant per
vector but not per dimension. Still biased toward high-magnitude
dimensions because they dominate both the dot product and the norm.

Currently used. Better than Euclidean without normalization, but
not optimal.

### Standardized cosine

Apply z-score normalization per feature (subtract mean, divide by
std across the indexed population), then cosine.

```
    normalized[i] = (raw[i] - mean[i]) / std[i]
    sim = cos(normalized_a, normalized_b)
```

Each feature contributes equally to the angle. This is what
`acidcat features --ml-ready` outputs. The MCP `find_similar`
tool does NOT currently do this, which is a known gap.

### Weighted distance

Multiply each feature by a weight chosen for the query intent:

```
    weighted[i] = w[i] * normalized[i]
    sim = cos(weighted_a, weighted_b)
```

Allows callers to express "timbre matters more than rhythm" or
"ignore duration." Not currently exposed.

### Mahalanobis distance

```
    d(a, b) = sqrt( (a - b)^T * Σ^-1 * (a - b) )
```

Where `Σ` is the covariance matrix of the feature population. This
accounts for correlations between features (e.g. centroid and
rolloff are highly correlated; Mahalanobis de-emphasizes their
double-count contribution).

Conceptually ideal but computationally expensive. Computing `Σ^-1`
on 50 features with a ~1000 sample library is fine; on a 10000
sample library or a 100-field vector, less so. Could be added as an
opt-in mode.

### Learned embedding distance

Use a pretrained audio model (CLAP, encodec, etc.) to produce an
embedding per sample, then cosine on those. Modern approach, often
superior to hand-crafted features for "sounds like" queries. Adds
large model dependencies and is out of scope for current acidcat.

---

## Current implementation

In `commands/similar.py` (uses sklearn):

```python
from sklearn.metrics.pairwise import cosine_similarity

similarities = cosine_similarity(target_vector.reshape(1, -1), all_vectors)
top_indices = np.argsort(similarities[0])[::-1][:n+1]
# skip index 0 if it's the target itself
results = top_indices[1:n+1] if top_indices[0] == target_index else top_indices[:n]
```

Straightforward:

1. Compute cosine similarity from target to all samples.
2. Sort descending.
3. Return top N (excluding the target itself, which trivially has
   similarity 1).

### What this does well

- Fast even on thousands of samples (matrix multiply is
  well-optimized in numpy/sklearn).
- Returns a clean ranked list.
- Handles missing features by the CSV reader's NaN filling.

### What this does poorly

- No per-feature normalization, so high-magnitude features dominate.
- No weighting, so the caller can't express intent.
- No prefilter by metadata (can return drum hits when searching
  from a tonal loop).
- No magnitude/confidence reporting (top result has similarity 0.99
  vs 0.65, and the caller has to check the value themselves).

---

## Proposed improvements to similarity

### 1. Normalization before scoring

Implementation:

```python
# once per index
vectors = load_feature_matrix()
scaler = StandardScaler().fit(vectors)
# cache scaler parameters in a metadata row

# per query
target_normalized = scaler.transform(target.reshape(1, -1))
all_normalized = scaler.transform(vectors)
sim = cosine_similarity(target_normalized, all_normalized)[0]
```

The scaler can be cached in the `meta` table or in a dedicated
`features_scaler` table so every query doesn't refit. Incremental
updates when new samples are indexed require rerunning fit or
using an incremental scaler.

Expected accuracy improvement: large for heterogeneous libraries
with samples across wildly different durations, sample rates, or
mix levels.

### 2. Kind prefilter

Before computing similarity, filter to samples of compatible kind:

```python
def find_similar_with_kind(target, kind=None, ...):
    target_kind = infer_kind(target)
    kind_filter = kind or target_kind
    candidates = samples.filter(kind=kind_filter)
    sim = cosine_similarity(target, candidates)
    ...
```

See `dsp/kind_inference.md` for the loop/one-shot/ambiguous
taxonomy. When searching from a loop, return only loops. When
searching from a one-shot, return only one-shots. Drops the
"tonal loop returned drum hits" failure mode.

### 3. Weighted similarity

Expose per-family weights:

```python
find_similar(target,
             weights={
                 'timbre': 2.0,      # mfcc, contrast
                 'spectrum': 1.0,    # centroid, rolloff, bandwidth
                 'rhythm': 0.0,      # tempo, beat_count
                 'tonal': 0.0,       # chroma, tonnetz, key
             })
```

The agent or user picks weights based on intent:

| query intent | weights |
|--------------|---------|
| "sounds like this" | timbre=2, spectrum=1, rhythm=0, tonal=0 |
| "would fit this groove" | rhythm=2, timbre=1, spectrum=0, tonal=0 |
| "layers harmonically" | tonal=2, rhythm=1, timbre=0.5, spectrum=0 |

Implementation: multiply feature vectors by a weight vector before
cosine. Zero-weight features are effectively removed from the
metric.

### 4. Confidence reporting

Along with each result, include the similarity score AND a
confidence estimate. A similarity of 0.95 with a standard deviation
of 0.05 across the top 10 is a strong match. A similarity of 0.65
with the top 20 all at 0.64-0.66 is essentially noise.

A simple confidence metric:

```python
top_score = similarities[top_index]
top_10_mean = np.mean(similarities[top_indices[:10]])
top_10_std = np.std(similarities[top_indices[:10]])
confidence = (top_score - top_10_mean) / (top_10_std + 1e-9)
```

High confidence: top match clearly separates from the rest. Low:
flat similarity landscape, results are ambiguous.

---

## Clustering

Clustering partitions the library into groups of similar samples.
Uses the same feature vectors, but applies a grouping algorithm
instead of pairwise comparison.

### k-means

Currently implemented via sklearn:

```python
from sklearn.cluster import KMeans
km = KMeans(n_clusters=k, random_state=42, n_init=10)
labels = km.fit_predict(vectors)
```

Properties:

- Fast: O(N * k * iterations) per fit.
- Requires k as input: caller must pick cluster count.
- Sensitive to initialization: `n_init=10` mitigates this by
  running from 10 starts and picking the best.
- Produces convex clusters: won't find elongated or crescent-shaped
  groups.
- Sensitive to feature scaling: same normalization concerns as
  similarity.

### Choosing k

Common heuristics:

- **Elbow method**: plot inertia (within-cluster variance) vs k.
  Look for the point where the curve flattens. `acidcat` doesn't
  currently automate this but could output a silhouette or elbow
  metric with cluster results.
- **Silhouette analysis**: for each sample, compute how well it fits
  its assigned cluster vs the next-best cluster. Higher average
  silhouette means better clustering.
- **Rule of thumb**: sqrt(N / 2) for libraries without obvious
  structure.

For sample libraries, k in the range of 5-20 tends to produce
musically meaningful clusters (percussion vs tonal vs ambient vs
sustained vs whatever).

### Alternatives to k-means

- **DBSCAN**: density-based, finds arbitrary-shaped clusters, no k
  required. Needs an `eps` distance parameter instead. Handles
  outliers (doesn't force them into a cluster). Good for libraries
  with a few distinct types and a lot of singleton oddities.
- **Agglomerative / hierarchical**: builds a dendrogram, can cut at
  any level to produce different cluster counts. Useful for
  exploratory work where you want to see the structure at multiple
  scales.
- **Spectral clustering**: builds a similarity graph, uses its
  eigenvectors to partition. Can find non-convex clusters. More
  expensive but often produces more musically-meaningful groupings.
- **HDBSCAN**: like DBSCAN but varies density automatically. State
  of the art for unsupervised clustering of heterogeneous data.

For acidcat, k-means is the reasonable default because it's fast,
well-understood, and produces consistent results. HDBSCAN would
be worth adding for libraries where the user wants the algorithm
to determine cluster count.

---

## Cluster interpretation

After clustering, each cluster is a set of sample paths with a
centroid (mean feature vector). Interpretation requires inspecting
what's in each cluster.

### Per-cluster summary

Useful statistics per cluster:

```python
for cluster_id, samples in clusters.items():
    centroid = mean feature vector
    size = len(samples)
    avg_duration = mean duration of cluster members
    avg_centroid_khz = mean spectral centroid / 1000
    avg_bpm = mean tempo
    common_tags = most frequent tags across members
    sample_filenames = first few filenames for scanning
```

Let the agent (or user) eyeball the clusters to name them. Good
names are descriptive:

```
cluster 0: "dark percussive loops"   (low centroid, high bpm, high rms, short)
cluster 1: "bright synth stabs"      (high centroid, short duration)
cluster 2: "sustained pads"          (low zcr, low beat_count, long)
cluster 3: "drum hits"               (very short, high variance mfcc)
cluster 4: "tonal loops"             (moderate duration, stable tonnetz)
```

### LLM-driven cluster naming

An agent can take cluster summaries and propose human-readable tag
names. This is exactly the "tag taxonomy learning" workflow
described in the direction doc:

```
agent:
  1. reindex_features to populate vectors
  2. cluster_samples(k=10) to get partition
  3. for each cluster: request_cluster_summary, then propose a name
  4. on user approval: tag_sample all members with the name
```

None of this is workflow logic in the server. The server exposes
primitives (`reindex_features`, hypothetical `cluster_samples`,
`tag_sample`). The agent composes them.

A `cluster_samples` primitive is on the potential-future-tool list.
Current workflow requires going through `acidcat features ... CSV`
then `acidcat similar CSV cluster`, which is CSV-mediated and not
ideal for agent use.

---

## Performance characteristics

### Similarity

- Feature matrix load: O(N) where N is indexed samples.
- Cosine computation: O(N * D) where D is feature dim.
- Sort: O(N log N).
- Total: O(N * D + N log N).

For N=1000, D=50, a query takes milliseconds. For N=100000 it's
still under a second. The bottleneck is loading the feature matrix
from SQLite/JSON, not the math.

Optimization: keep the feature matrix in memory for a long-lived
server process. The MCP server doesn't currently do this because
of the fresh-connection-per-call pattern. A small cache would help.

### Clustering

- k-means: O(N * k * D * iterations).
- For N=1000, k=10, D=50, iterations~=100: ~50 million ops.
  Sub-second on modern hardware.
- For N=100000, k=20, D=50: still tractable, few seconds.

For libraries past ~1M samples, mini-batch k-means becomes
preferable.

---

## Integration with the rest of the system

### Similarity as a secondary filter

Most useful similarity queries combine metadata filter + similarity
ranking:

```
1. search_samples({bpm: [118, 128], kind: 'loop'}) -> 200 candidates
2. find_similar among those candidates -> 10 best matches
```

Metadata filter narrows the candidate set cheaply; similarity scores
the remaining few hundred. Currently `find_similar` operates over
the full index, which is wasteful for intent queries. A future
version should accept a pre-filtered candidate list.

### Chaining with compatibility

```
1. find_compatible(target) -> 50 harmonically compatible samples
2. find_similar within that 50 -> best timbral matches that also
   harmonically fit
```

This is the kind of compound workflow the agent composes, not
something the server should bake in.

### Clustering for tag generation

```
1. cluster the library into 10-20 groups
2. agent proposes names for each
3. agent tags every sample with its cluster's name
```

Now the library has a descriptive tag namespace generated from its
own content, not imposed from outside. Search queries using those
tags hit clusters.

---

## Gotchas

### Similarity is not transitive

If A is similar to B, and B is similar to C, A may not be similar
to C. Feature space distance isn't an equivalence relation.

Practical consequence: recommending "samples like the samples you
liked" naively can drift. If the user liked sample A, and we
surface B (similar to A), and then they click B, surfacing
"similar to B" can wander far from A's character.

Mitigation: anchor recommendations to the original query, not to
the user's click history.

### Cluster membership is hard at the boundary

k-means assigns every sample to exactly one cluster. Samples near
cluster boundaries get arbitrary assignments. A kick drum that's a
little tonal might end up in the "drum hits" cluster or the "tonal
short samples" cluster depending on initialization.

Mitigation: use soft clustering (Gaussian mixture models) that
assign probabilities instead of hard labels. More expressive but
more expensive, and users need to know the scores to interpret.

### Feature importance depends on the library

A library of mostly drum samples has low variance in tonal
features. In that library, tonal features add nothing to
similarity (because everyone is similar on those axes). A library
of mostly pads has low variance in rhythm features.

Mitigation: compute feature importance (variance) per library and
surface it. Low-variance features shouldn't dominate distance
metrics. Could be automated via variance-weighted similarity.

### Cold-start for new samples

A newly-indexed sample has no feature vector until `reindex_features`
runs. It can't be compared by `find_similar` until then.

Mitigation: make feature extraction part of the normal `index`
flow (opt-in via `--features` flag, which already exists). For
agent-driven workflows, add a `get_or_compute_features` helper.

### Normalization drifts as library grows

If scaler parameters are cached, adding new samples to the library
means the cached scaler is slightly stale. For small additions
this doesn't matter. For large additions (doubling library size),
recomputing the scaler is worth it.

Mitigation: track scaler freshness via a row count, recompute when
it drifts past a threshold (e.g. 20% growth since last fit).

---

## A small worked example

Library of 500 samples. User queries find_similar on a specific
kick drum. Trace:

```
1. target = kick_808_heavy.wav
   feature_vector = [0.6 s, 44100, ..., -1.2, 0.8, ..., 0.4, 0.01]
                    dur    sr         mfcc     chroma    zcr  ...

2. load all 500 feature vectors into a 500x50 matrix V

3. compute cosine(target, V) -> 500-dim similarity vector
   target is trivially 1.0

4. sort descending, take top 10 (skipping target)
   results:
     kick_sub_long.wav           0.94  # dark, long, similar mfcc
     kick_808_hipass.wav         0.89  # similar timbre, brighter
     sub_bass_pluck.wav          0.87  # not a kick but similar feature profile
     808_tail_short.wav          0.83
     low_tom.wav                 0.78
     dark_snare.wav              0.71  # borderline
     noise_hit_low.wav           0.65  # starting to diverge
     ...

5. agent sees top 3 are very close (0.94, 0.89, 0.87) then a drop;
   reports the top 3 with high confidence, mentions the rest with
   lower confidence
```

If normalization and kind filtering were applied:

```
with kind='one_shot' prefilter:
  candidates reduce to ~150 one-shots

with StandardScaler normalization:
  the "sub_bass_pluck.wav" sample (0.87 raw) may drop to 0.79
  because its feature spread differs from a kick
  the "kick_sub_long.wav" (0.94 raw) stays high because its shape
  really does match

final result: tighter, more kick-specific top 5
```

---

## References

- Aggarwal, C. C., et al. (2001). "On the surprising behavior of
  distance metrics in high dimensional space." *International
  Conference on Database Theory*. Why cosine beats Euclidean in
  high dimensions.
- Pedregosa, F., et al. (2011). "Scikit-learn: Machine learning in
  Python." *JMLR* 12: 2825-2830. The similarity and clustering
  library acidcat uses.
- Lloyd, S. (1982). "Least squares quantization in PCM." *IEEE
  Transactions on Information Theory* 28(2): 129-137. The
  k-means algorithm.
- Campello, R. J., Moulavi, D., & Sander, J. (2013).
  "Density-based clustering based on hierarchical density
  estimates." *PAKDD*. The HDBSCAN algorithm worth adding.
- Rousseeuw, P. J. (1987). "Silhouettes: a graphical aid to the
  interpretation and validation of cluster analysis." *Journal of
  Computational and Applied Mathematics* 20: 53-65. For evaluating
  cluster quality.
- See `dsp/feature_pipeline.md` for the feature vector this
  operates over.
- See `dsp/kind_inference.md` for the loop/one-shot heuristic that
  can prefilter candidates.
