"""
INTERNAL - imported by consolidate_colors.py (the `consolidate-colors` command). No CLI command of its own; build_clusters()/export_geometry() are the entry points consolidate-colors reuses.

CIEDE2000 Agglomerative Color Clustering
========================================

Builds the perceptually-pure color-cluster structure that the palette_explorer
consumes. For a brand/gender segment it:

1. Loads each product's K-means clusters (+ price fields) from the database.
2. Merges each product's clusters by hue and keeps the dominant merged color
   (one representative color per product).
3. Clusters those dominant colors with GPU-accelerated pairwise CIEDE2000 +
   fastcluster complete-linkage agglomerative clustering (diameter <= CLUSTER_THRESHOLD
   ΔE00), so every product in a cluster is within threshold of every other.

Note (S137, 2026-08-31): the primary linkage call at line ~207 was migrated from
scipy.cluster.hierarchy.linkage to fastcluster.linkage — mirrors the same change
in production/build_ciede2000_clusters.py (old code). scipy internally up-casts a
float32 condensed matrix to float64 (~26 GB copy at n=84k), OOM-killing the
container on 32 GB hosts; fastcluster runs natively in float32. Both codebases
must match for Phase 5.6's consolidate-colors parity diff to be meaningful.
Other new-code linkage sites (assign_zones, build_zones, review_cluster_quality)
stay on scipy — they either need optimal_ordering=True (fastcluster lacks it) or
operate on small centroid inputs where the up-cast is negligible.
4. Computes a weighted LAB centroid per cluster (+ RGB, hex, hue).

This is the COMPUTE/STRUCTURE half. It is READ-ONLY on the database and writes
nothing itself — it exposes two functions:
    build_clusters()   -> (dominant_df, labels, n_clusters, centroids_df, timings)
    export_geometry()  -> writes cluster_centroids.csv + product_assignments.csv

consolidate_colors.py (the `consolidate-colors` command) calls both: it reuses
build_clusters() in-memory, calls export_geometry(), and adds the per-cluster
discount testing + FDR that produces the full cluster_summary.csv + run_metadata.csv.
There is no CLI command here and no standalone entry point — run
`python cli.py consolidate-colors` to produce the CSVs.
"""

import os
import time
import numpy as np
import pandas as pd
import fastcluster
from scipy.cluster.hierarchy import linkage, fcluster

from shared.db import connect_to_db
from shared.hsb import lab_to_rgb_array
from shared.ciede2000 import ciede2000_pairwise, detect_gpu
from shared.palette_merge.palette_merge import merge_clusters_hue_based

# =============================================================================
# CONFIGURATION
# =============================================================================

BRAND_DEFAULT = 'nike'
GENDER_DEFAULT = 'mens'
CLUSTER_THRESHOLD = 8          # CIEDE2000 complete-linkage diameter

# S137 (2026-08-31): parity scope limited to first N archives per brand.
# Fastcluster complete-linkage has O(N^2) working memory beyond the input, so
# at Nike Mens ~52 archives (84k dominant products, 3.5B pairs) peak memory is
# ~41 GB, exceeding a 24 GB WSL cap even with float32 pairwise storage. Scoping
# to first 35 archives brings n down to ~55k, peak to ~15 GB, fits in RAM.
# THIS CONSTANT MUST MATCH production/build_ciede2000_clusters.py for Phase 5.6
# (consolidate-colors) parity diffs to be meaningful. Only applies to CIEDE-derived
# analyses; earlier Phase 2.4 cells (archetypes, distribution tables, coverage)
# use all archives; baseline is intentionally asymmetric across cells.
# Full rationale: documentation/Session137_20260831_ciede2000_float32_fastcluster_migration.md
PARITY_ARCHIVE_LIMIT = 35

QUERY = """
    SELECT
        c.instance_id_ref,
        c.archive_id_ref,
        c.lab_l,
        c.lab_a,
        c.lab_b,
        c.perc,
        c.cluster_rank,
        a.query,
        i.price_std,
        i.price_curr
    FROM cluster_fpyolo11l241114_kmeans250218 c
    JOIN archive a ON c.archive_id_ref = a.archive_id
    JOIN instance i ON c.instance_id_ref = i.instance_id
    WHERE c.archive_id_ref IN (
        SELECT archive_id FROM archive ORDER BY archive_id LIMIT %s
    )
"""

OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'outputs'
)


# =============================================================================
# DATA LOADING
# =============================================================================

def load_brand_data(brand):
    """Load cluster + archive + instance data for a single brand.

    S137: filters to first PARITY_ARCHIVE_LIMIT archives per brand.
    """
    print(f"  Loading {brand} data from database (scope: first {PARITY_ARCHIVE_LIMIT} archives)...")
    conn, cur = connect_to_db(brand)
    try:
        cur.execute(QUERY, (PARITY_ARCHIVE_LIMIT,))
        rows = cur.fetchall()
        df = pd.DataFrame(rows, columns=[
            'instance_id', 'archive_id', 'lab_l', 'lab_a', 'lab_b',
            'perc', 'cluster_rank', 'query', 'price_std', 'price_curr'
        ])
        print(f"  Loaded {len(df):,} cluster rows")
        return df
    finally:
        conn.close()


# =============================================================================
# DATA PREPARATION
# =============================================================================

def extract_gender(query):
    """Extract gender from query string."""
    if query is None:
        return None
    query_lower = query.lower()
    if 'womens' in query_lower or 'women' in query_lower:
        return 'womens'
    elif 'mens' in query_lower or 'men' in query_lower:
        return 'mens'
    return None


def prepare_data(cluster_df, gender_filter):
    """Type-cast columns, filter by gender, compute discount columns."""
    df = cluster_df.copy()

    df['lab_l'] = df['lab_l'].astype(float)
    df['lab_a'] = df['lab_a'].astype(float)
    df['lab_b'] = df['lab_b'].astype(float)
    df['perc'] = df['perc'].astype(float)

    df['gender'] = df['query'].apply(extract_gender)
    df = df[df['gender'] == gender_filter].copy()

    df['price_std'] = pd.to_numeric(df['price_std'], errors='coerce')
    df['price_curr'] = pd.to_numeric(df['price_curr'], errors='coerce')
    df['is_discounted'] = (
        (df['price_curr'] < df['price_std']) &
        (df['price_std'] > 0)
    )
    df['discount_depth'] = np.where(
        df['is_discounted'] & (df['price_std'] > 0),
        (df['price_std'] - df['price_curr']) / df['price_std'] * 100,
        0
    )

    print(f"  After gender filter ({gender_filter}): {len(df):,} cluster rows")
    return df


def compute_dominant_clusters(df):
    """Merge similar per-product clusters by hue, then extract highest-coverage.

    For each product, applies the archetype hue-based merge logic to combine
    visually similar K-means clusters (e.g. two shades of red become one).
    Returns one row per product with the dominant merged cluster's LAB values
    and combined coverage percentage.
    """
    rows = []
    metadata_cols = ['archive_id', 'query', 'price_std', 'price_curr',
                      'is_discounted', 'discount_depth']

    for instance_id, group in df.groupby('instance_id'):
        # Build cluster dicts for the merge function
        clusters = [
            {'lab_l': float(r['lab_l']), 'lab_a': float(r['lab_a']),
             'lab_b': float(r['lab_b']), 'perc': float(r['perc'])}
            for _, r in group.iterrows()
        ]

        merged = merge_clusters_hue_based(clusters)
        if not merged:
            continue

        # merged[0] is the dominant (already sorted by coverage desc)
        dominant = merged[0]
        first = group.iloc[0]

        rows.append({
            'instance_id': instance_id,
            'lab_l': dominant['lab_l'],
            'lab_a': dominant['lab_a'],
            'lab_b': dominant['lab_b'],
            'perc': dominant['perc'],
            **{col: first[col] for col in metadata_cols},
        })

    return pd.DataFrame(rows)


# =============================================================================
# CLUSTERING
# =============================================================================

def cluster_ciede2000_agglomerative(lab_array, use_gpu):
    """
    Cluster LAB colors using CIEDE2000 pairwise distances + complete linkage.

    Args:
        lab_array: (N, 3) numpy array of LAB values
        use_gpu: whether to use CuPy for CIEDE2000 computation

    Returns:
        (labels, n_clusters, condensed, timings) where timings is a dict
    """
    N = len(lab_array)
    timings = {}

    print(f"\n  Computing CIEDE2000 pairwise distances for {N:,} products...")
    n_pairs = N * (N - 1) // 2
    condensed_gb = n_pairs * 4 / (1024 ** 3)  # float32 = 4 bytes/pair (S137)
    print(f"    {n_pairs:,} pairs ({condensed_gb:.2f} GB condensed, float32)")

    t0 = time.time()
    condensed = ciede2000_pairwise(lab_array, use_gpu)
    timings['pairwise'] = time.time() - t0
    print(f"    Pairwise: {timings['pairwise']:.1f}s")

    print(f"  Running complete-linkage agglomerative clustering (fastcluster, float32)...")
    t0 = time.time()
    Z = fastcluster.linkage(condensed, method='complete')
    timings['linkage'] = time.time() - t0
    print(f"    Linkage: {timings['linkage']:.1f}s")

    labels = fcluster(Z, t=CLUSTER_THRESHOLD, criterion='distance')
    n_clusters = len(np.unique(labels))
    timings['total'] = timings['pairwise'] + timings['linkage']
    print(f"    Clusters: {n_clusters:,} (threshold={CLUSTER_THRESHOLD} ΔE₀₀)")

    del Z
    return labels, n_clusters, condensed, timings


# =============================================================================
# CENTROIDS
# =============================================================================

def compute_cluster_centroids(dominant_df, labels, n_clusters):
    """
    Compute weighted-average LAB centroid per cluster, convert to RGB + hue.

    Args:
        dominant_df: DataFrame with one row per product (must have lab_l/a/b, perc)
        labels: cluster label array (1-indexed from fcluster)
        n_clusters: number of clusters

    Returns:
        DataFrame with columns: cluster_id, lab_l, lab_a, lab_b, r, g, b,
                                 hex_color, hue_angle, n_products
    """
    df = dominant_df.copy()
    df['cluster_id'] = labels

    centroids = []
    for cid in range(1, n_clusters + 1):
        mask = df['cluster_id'] == cid
        group = df[mask]
        n = len(group)
        if n == 0:
            continue

        weights = group['perc'].values
        w_sum = weights.sum()
        if w_sum > 0:
            lab_l = np.average(group['lab_l'].values, weights=weights)
            lab_a = np.average(group['lab_a'].values, weights=weights)
            lab_b = np.average(group['lab_b'].values, weights=weights)
        else:
            lab_l = group['lab_l'].mean()
            lab_a = group['lab_a'].mean()
            lab_b = group['lab_b'].mean()

        # LAB to RGB
        r, g, b = lab_to_rgb_array(
            np.array([lab_l]), np.array([lab_a]), np.array([lab_b])
        )
        r, g, b = float(r[0]), float(g[0]), float(b[0])
        hex_color = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

        # Hue angle from a*, b*
        hue_angle = np.degrees(np.arctan2(lab_b, lab_a)) % 360

        centroids.append({
            'cluster_id': cid,
            'lab_l': round(lab_l, 2),
            'lab_a': round(lab_a, 2),
            'lab_b': round(lab_b, 2),
            'r': round(r, 4),
            'g': round(g, 4),
            'b': round(b, 4),
            'hex_color': hex_color,
            'hue_angle': round(hue_angle, 1),
            'n_products': n,
        })

    return pd.DataFrame(centroids)


# =============================================================================
# ORCHESTRATION + EXPORT
# =============================================================================

def build_clusters(brand, gender, use_gpu=None):
    """Run the full clustering pipeline in-memory (no file writes).

    Returns:
        (dominant_df, labels, n_clusters, centroids_df, timings)
    """
    if use_gpu is None:
        use_gpu, device_info = detect_gpu()
        print(f"  GPU: {device_info}")

    cluster_df = load_brand_data(brand)
    cluster_df = prepare_data(cluster_df, gender)

    dominant_df = compute_dominant_clusters(cluster_df)
    print(f"  Dominant clusters: {len(dominant_df):,} products (1 per product)")

    lab_array = dominant_df[['lab_l', 'lab_a', 'lab_b']].values.astype(np.float64)
    labels, n_clusters, condensed, timings = cluster_ciede2000_agglomerative(
        lab_array, use_gpu
    )
    del condensed  # Free memory

    print(f"\n  Computing cluster centroids...")
    centroids_df = compute_cluster_centroids(dominant_df, labels, n_clusters)

    return dominant_df, labels, n_clusters, centroids_df, timings


def export_geometry(centroids_df, dominant_df, labels, output_dir):
    """Write the per-cluster centroids + per-product assignments CSVs.

    These are identical whether or not discount testing runs, so both
    build_color_clusters.main() and consolidate_colors.main() call this.
    """
    os.makedirs(output_dir, exist_ok=True)

    centroids_path = os.path.join(output_dir, 'cluster_centroids.csv')
    centroids_df.to_csv(centroids_path, index=False)
    print(f"  Saved: {centroids_path}")

    assignments_df = dominant_df.copy()
    assignments_df['cluster_id'] = labels
    assign_cols = [
        'instance_id', 'archive_id', 'cluster_id',
        'lab_l', 'lab_a', 'lab_b', 'perc',
        'is_discounted', 'discount_depth',
        'price_std', 'price_curr',
    ]
    assign_path = os.path.join(output_dir, 'product_assignments.csv')
    assignments_df[assign_cols].to_csv(assign_path, index=False)
    print(f"  Saved: {assign_path}")
