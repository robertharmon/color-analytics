"""
INTERNAL - imported by visualize_coverage.py (no CLI command of its own).

Coverage Distribution Analysis (HSB Saturation)
================================================

Computes, per (brand, gender, color_class), the distribution of per-product
merged-cluster coverage percentages. Color class is assigned by HSB
(Photoshop-style) saturation:
- Saturated: HSB sat >= 50%
- Muted: 12% <= HSB sat < 50%
- Neutral: HSB sat < 12%

Within each product, clusters closer than COLOR_SIMILARITY_THRESHOLD in LAB
distance are merged (coverage summed) before their coverage is collected, so
the distribution reflects distinct perceived colors rather than raw K-means
clusters.

This module is the COMPUTE half of the coverage slice. It is READ-ONLY on the
database. It exposes compute_coverages(), which visualize_coverage.py calls to
render coverage_distribution.html (the `visualize-coverage` command). The
histogram needs the raw per-product distributions, so this module returns them
in memory rather than persisting any intermediate artifact.
"""

import os
import pandas as pd
import numpy as np
from collections import defaultdict

from shared.db import connect_to_db
from shared.hsb import SATURATED_HSB, NEUTRAL_HSB, compute_hsb_saturation

# ============================================================================
# CONFIGURATION (domain vocabulary — shared with visualize_coverage.py)
# ============================================================================

BRANDS = ['nike', 'adidas', 'puma', 'lulu', 'ua']
BRAND_LABELS = {
    'nike': 'Nike',
    'adidas': 'Adidas',
    'puma': 'Puma',
    'lulu': 'Lululemon',
    'ua': 'Under Armour'
}

GENDERS = ['mens', 'womens']
GENDER_LABELS = {
    'mens': 'Mens',
    'womens': 'Womens'
}

COLOR_CLASSES = ['saturated', 'muted', 'neutral']

# LAB distance threshold for merging similar colors
COLOR_SIMILARITY_THRESHOLD = 60

# Output directory (this slice's outputs/, resolved relative to the module)
OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'outputs'
)

# ============================================================================
# SQL QUERY - READ ONLY
# ============================================================================

QUERY = """
    SELECT
        c.instance_id_ref,
        c.archive_id_ref,
        c.lab_l,
        c.lab_a,
        c.lab_b,
        c.perc,
        a.query
    FROM cluster_fpyolo11l241114_kmeans250218 c
    JOIN archive a ON c.archive_id_ref = a.archive_id
"""

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_color_class_hsb(hsb_sat):
    """Classify a cluster by its HSB saturation value."""
    if hsb_sat >= SATURATED_HSB:
        return 'saturated'
    elif hsb_sat >= NEUTRAL_HSB:
        return 'muted'
    else:
        return 'neutral'


def lab_distance(c1, c2):
    """Calculate Euclidean distance in LAB space between two clusters."""
    return np.sqrt(
        (c1['lab_l'] - c2['lab_l'])**2 +
        (c1['lab_a'] - c2['lab_a'])**2 +
        (c1['lab_b'] - c2['lab_b'])**2
    )


def merge_similar_clusters(clusters, threshold=COLOR_SIMILARITY_THRESHOLD):
    """
    Merge clusters within threshold LAB distance using union-find.
    """
    if len(clusters) <= 1:
        return clusters

    n = len(clusters)
    parent = list(range(n))

    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # Union clusters within threshold distance
    for i in range(n):
        for j in range(i + 1, n):
            if lab_distance(clusters[i], clusters[j]) < threshold:
                union(i, j)

    # Group by root and merge
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(clusters[i])

    # Merge each group: weighted avg LAB, sum coverage
    merged = []
    for group in groups.values():
        total_perc = sum(c['perc'] for c in group)
        if total_perc > 0:
            avg_l = sum(c['lab_l'] * c['perc'] for c in group) / total_perc
            avg_a = sum(c['lab_a'] * c['perc'] for c in group) / total_perc
            avg_b = sum(c['lab_b'] * c['perc'] for c in group) / total_perc
            merged.append({
                'lab_l': avg_l, 'lab_a': avg_a, 'lab_b': avg_b,
                'perc': total_perc,
                'hsb_sat': compute_hsb_saturation(
                    np.array([avg_l]), np.array([avg_a]), np.array([avg_b])
                )[0]
            })

    return merged


def assign_gender(query_str):
    """Assign gender based on query string."""
    query_lower = str(query_str).lower()
    if 'womens' in query_lower:
        return 'womens'
    elif 'mens' in query_lower:
        return 'mens'
    return None


def load_brand_data(brand):
    """Load all cluster data for a brand. READ-ONLY query."""
    print(f"  Loading {brand} data...")
    conn, cur = connect_to_db(brand)

    try:
        cur.execute(QUERY)
        rows = cur.fetchall()
        df = pd.DataFrame(rows, columns=[
            'instance_id', 'archive_id', 'lab_l', 'lab_a', 'lab_b', 'perc', 'query'
        ])
        print(f"    Loaded {len(df):,} clusters from {df['instance_id'].nunique():,} products")
    finally:
        conn.close()

    return df


def extract_merged_coverages_for_segment(df, brand, gender, color_class):
    """
    Extract coverage percentages for a specific brand/gender/color_class segment.

    Uses HSB saturation for classification instead of LAB chroma.
    """
    # Filter to brand/gender first
    segment_df = df[(df['brand'] == brand) & (df['gender'] == gender)].copy()

    if len(segment_df) == 0:
        return np.array([])

    # Filter to this color class using HSB saturation
    if color_class == 'saturated':
        class_df = segment_df[segment_df['hsb_sat'] >= SATURATED_HSB].copy()
    elif color_class == 'muted':
        class_df = segment_df[
            (segment_df['hsb_sat'] >= NEUTRAL_HSB) &
            (segment_df['hsb_sat'] < SATURATED_HSB)
        ].copy()
    else:  # neutral
        class_df = segment_df[segment_df['hsb_sat'] < NEUTRAL_HSB].copy()

    if len(class_df) == 0:
        return np.array([])

    all_coverages = []

    # Group by product and merge similar clusters within the color class
    for instance_id, group in class_df.groupby('instance_id'):
        # Convert to list of dicts for merging function
        # Note: perc may be Decimal from PostgreSQL, convert to float
        subset = group[['lab_l', 'lab_a', 'lab_b', 'perc']].copy()
        subset['perc'] = subset['perc'].astype(float)
        clusters = subset.to_dict('records')

        # Merge similar clusters within this color class
        merged = merge_similar_clusters(clusters, COLOR_SIMILARITY_THRESHOLD)

        # Collect merged coverages
        for c in merged:
            all_coverages.append(c['perc'])

    return np.array(all_coverages)


def extract_all_coverages(df):
    """Extract coverage percentages for all brand/gender/color_class combinations."""
    all_coverages = {}
    for color_class in COLOR_CLASSES:
        for brand in BRANDS:
            for gender in GENDERS:
                key = (brand, gender, color_class)
                all_coverages[key] = extract_merged_coverages_for_segment(df, brand, gender, color_class)
    return all_coverages


def compute_coverages(brands=BRANDS):
    """
    Load every brand, compute HSB saturation + gender, and extract the merged
    per-(brand, gender, color_class) coverage distributions.

    This is the shared compute entry point used by both this module's main()
    (which writes the CSV summaries) and visualize_coverage.py (which needs the
    raw distributions to build the histogram figure).

    Returns:
        (all_coverages, combined_df)
        all_coverages: dict[(brand, gender, color_class)] -> np.ndarray of coverages
        combined_df: prepared cluster DataFrame (valid-gender rows only)
    """
    all_data = []
    for brand in brands:
        df = load_brand_data(brand)
        df['brand'] = brand

        # Convert to float
        df['lab_l'] = df['lab_l'].astype(float)
        df['lab_a'] = df['lab_a'].astype(float)
        df['lab_b'] = df['lab_b'].astype(float)

        # Compute HSB saturation
        df['hsb_sat'] = compute_hsb_saturation(
            df['lab_l'].values, df['lab_a'].values, df['lab_b'].values
        )

        df['gender'] = df['query'].apply(assign_gender)
        all_data.append(df)

    combined_df = pd.concat(all_data, ignore_index=True)

    # Filter to rows with valid gender
    combined_df = combined_df[combined_df['gender'].notna()]

    all_coverages = extract_all_coverages(combined_df)
    return all_coverages, combined_df
