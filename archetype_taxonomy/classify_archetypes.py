"""
ENTRY - run: python cli.py archetypes  (classify palettes into 5 archetypes -> CSVs). Also imported by the visualizer + validation tools.

Hue-Based Archetype Classification (HSB Version)
=================================================

Classifies products using HUE-ANGLE based cluster merging with simplified taxonomy.

Key differences from rule-based version:
1. Merges clusters based on HUE SIMILARITY (a*/b* angle), not LAB euclidean distance
2. Colors with same hue merge regardless of lightness (collapses lighting variation)
3. Colors with different hues NEVER merge (preserves distinct design colors)
4. Simplified taxonomy: 5 categories based on structure, not saturation sequences

HSB saturation = (max(R,G,B) - min(R,G,B)) / max(R,G,B), on 0-100 scale.

Saturation classification for `has_saturated` field:
- Saturated: HSB sat >= 50%
- Muted: 12% <= HSB sat < 50%
- Neutral: HSB sat < 12%

This script is READ-ONLY:
- Database: Only SELECT queries (no INSERT, UPDATE, DELETE)
- Images: No access at all
- Output: Creates NEW files in archetype_taxonomy/outputs/

Usage:
    docker compose run --rm pipeline python cli.py archetypes
"""

import os
import pandas as pd
import numpy as np

from shared.db import connect_to_db
from shared.hsb import (
    SATURATED_HSB, NEUTRAL_HSB,
    compute_hsb_saturation
)
# The hue-based merge primitive + its thresholds are the single source of truth
# in shared.palette_merge; the classifier builds its taxonomy on merged palettes.
from shared.palette_merge.palette_merge import (
    merge_clusters_hue_based,
    hue_difference,
    NEUTRAL_CHROMA_THRESHOLD,
    LOW_CHROMA_MERGE_THRESHOLD,
    HUE_MERGE_THRESHOLD,
)

# ============================================================================
# CONFIGURATION
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

# ======================
# TAXONOMY CONFIG
# ======================

# Dynamic significance thresholds based on hue difference from dominant
# Very different hue (>90) = likely real accent, lower threshold
# Moderately different (>45) = probably real, medium threshold
# Similar hue = likely noise/lighting, standard threshold
SIGNIFICANCE_THRESHOLD_OPPOSITE_HUE = 1   # Hue diff > 90
SIGNIFICANCE_THRESHOLD_DIFFERENT_HUE = 3  # Hue diff > 45
SIGNIFICANCE_THRESHOLD_SIMILAR_HUE = 5    # Hue diff <= 45 or neutral

# Ratio threshold for dominant vs balanced
DOMINANCE_RATIO = 2.0  # ratio >= 2.0 means dominant+accent (e.g., 67/33)

# Total secondary coverage threshold for MULTI classification
# If all secondary colors combined are below this, it's DOM_ACC (dominant + tiny accents)
# If secondary coverage is >= this AND 3+ colors, it's MULTI_*
SECONDARY_COVERAGE_THRESHOLD = 10  # total secondary coverage >= 10% for MULTI

# ======================
# VALIDATION RESULTS (2026-02-26, n=400, 398 evaluated)
# ======================
#
# Overall accuracy: 85.7%
#
# Per-archetype:
#   MULTI_BAL  100.0%  (50/50)
#   MONO        88.0%  (132/150)
#   DOM_ACC     86.7%  (85/98)
#   DUAL_BAL    86.0%  (43/50)
#   MULTI_DOM   62.0%  (31/50)  <-- problem archetype
#
# Top error flows:
#   MONO -> DOM_ACC      (16)  System misses small accent colors (logo/trim);
#                               coverage_1 >= 98% — accent too small for clustering.
#   MULTI_DOM -> MULTI_BAL (13)  System over-weights dominant cluster;
#                               DOMINANCE_RATIO threshold may be too low for 3+ colors.
#   DOM_ACC -> MULTI_DOM   (8)  System treats secondary colors as accents,
#                               humans see them as co-dominant.
#   DUAL_BAL -> MULTI_BAL  (5)  System misses a third significant color.
#
# MULTI_BAL is under-assigned: 100% precision but 20 of 57 errors should
# have been MULTI_BAL. The system is biased toward seeing dominance.
#
# Coverage_1 does not predict misclassification within archetypes —
# errors are in the classification rules, not threshold boundary effects.
#
# Per-brand accuracy (~80 products each, brand-balanced sampling):
#   Nike          92.5%  (6 errors)   No dominant pattern
#   Lululemon     91.2%  (7 errors)   MULTI_DOM->MULTI_BAL dominates (4/7)
#   Under Armour  86.2%  (11 errors)  MULTI_DOM->DOM_ACC top pattern (4/11, unique to UA)
#   Puma          81.2%  (15 errors)  MONO->DOM_ACC dominates (7/15, missed accents)
#   Adidas        76.9%  (18 errors)  Spread: MONO->DOM_ACC (5), MULTI_DOM->MULTI_BAL (4)
#
# Adidas has 3x the error rate of Nike. Puma's errors concentrate on
# missed accent colors; Adidas errors are distributed across multiple patterns.
#
# ----- PER-BRAND DEEP DIVE: NIKE (2026-02-26, n=300, 296 evaluated) -----
#
# Overall Nike accuracy: 87.8%  (260/296)  95% CI [83.6%, 91.1%]
# (Session 80's 92.5% at n=80 was likely an optimistic small-sample draw)
#
# Per-archetype (Nike):
#   MULTI_BAL  98.3%  (57/58)
#   MONO       93.3%  (56/60)
#   DUAL_BAL   85.0%  (51/60)
#   MULTI_DOM  85.0%  (51/60)
#   DOM_ACC    77.6%  (45/58)   <-- weakest for Nike
#
# Top error flows (Nike, 36 total):
#   DOM_ACC -> MULTI_DOM   (9)  System sees dominant+accent, human sees
#                               multiple co-dominant colors.
#   MULTI_DOM -> MULTI_BAL (7)  System sees dominance, human sees balance.
#   DUAL_BAL -> MULTI_DOM  (6)  System sees 2 colors, human sees 3+.
#   MONO -> DOM_ACC        (4)  Small accents missed by clustering.
#   DUAL_BAL -> MULTI_BAL  (3)  System misses a third significant color.
#
# Nike's dominant error pattern is systematically under-counting the number
# of visually significant colors. DOM_ACC is the weakest archetype — the
# system treats multi-color products as dominant+accent when humans see
# co-dominant colors (9 of 36 errors).
#
# 4 UNCERTAIN responses (DOM_ACC: 2, MULTI_BAL: 2), excluded from accuracy.
#
# Population estimates (Nike, 79,231 total products):
#   System counts: MONO 71,217 | DOM_ACC 6,789 | DUAL_BAL 527 | MULTI_DOM 594 | MULTI_BAL 104
#   Applying confusion rates to estimate true distribution:
#     MONO    is 75-88% of Nike products (true count ~60k-70k). Robust.
#     DOM_ACC is a distant second at 6-15% (~5k-11.5k). Reliably second-largest.
#     DUAL_BAL, MULTI_DOM, MULTI_BAL are collectively ~1-5%. Too small and
#       uncertain to rank or size individually at n=60 per archetype.
#   The MONO -> DOM_ACC leak (6.7% of 71k = ~4,750 products) is the dominant
#   redistribution effect, but the CI on that rate is wide [1.8%, 16.2%].
#   Directional findings are robust; specific magnitudes are not.

# ======================
# OUTPUT CONFIG
# ======================

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
# SIMPLIFIED TAXONOMY
# ============================================================================

ARCHETYPE_NAMES = {
    'MONO': 'Monochrome',
    'DOM_ACC': 'Dominant + Accent',
    'DUAL_BAL': 'Dual Balanced',
    'MULTI_DOM': 'Multi Dominant',
    'MULTI_BAL': 'Multi Balanced',
}


def filter_significant_clusters(merged_clusters):
    """
    Filter clusters using DYNAMIC significance thresholds.

    Colors with very different hues from the dominant get lower thresholds
    (they're more likely to be real accents, not noise).

    Thresholds:
    - Hue diff > 90 (opposite): 1% threshold
    - Hue diff > 45 (different): 3% threshold
    - One neutral + one chromatic: 3% threshold (treat as different)
    - Both neutral or similar hue: 5% threshold
    """
    if len(merged_clusters) == 0:
        return []

    # Dominant color is first (highest coverage)
    dominant = merged_clusters[0]
    dominant_hue = dominant.get('hue')

    significant = []
    for c in merged_clusters:
        coverage = c['perc']
        c_hue = c.get('hue')

        # Calculate hue difference from dominant
        if dominant_hue is not None and c_hue is not None:
            # Both chromatic - calculate actual hue difference
            hue_diff = hue_difference(dominant_hue, c_hue)
        elif (dominant_hue is None) != (c_hue is None):
            # One neutral, one chromatic - maximally distinct colors
            # Chromatic accent on neutral base is always intentional
            hue_diff = 91  # Synthetic value to trigger OPPOSITE_HUE threshold (1%)
        else:
            # Both neutral - they're similar (shades of gray/black/white)
            hue_diff = 0

        # Determine threshold based on hue difference
        if hue_diff > 90:
            # Very different hue - likely real accent
            threshold = SIGNIFICANCE_THRESHOLD_OPPOSITE_HUE
        elif hue_diff > 45:
            # Moderately different hue (includes neutral+chromatic case)
            threshold = SIGNIFICANCE_THRESHOLD_DIFFERENT_HUE
        else:
            # Similar hue or both neutral - standard threshold
            threshold = SIGNIFICANCE_THRESHOLD_SIMILAR_HUE

        if coverage >= threshold:
            significant.append(c)

    return significant


def classify_structure(merged_clusters):
    """
    Classify into simplified taxonomy based on color count and TOTAL SECONDARY COVERAGE.

    Uses total secondary coverage (all non-dominant colors combined) to determine
    if secondary colors are substantial enough to warrant MULTI classification.

    Categories:
    - MONO: 1 color
    - DOM_ACC: 2 colors with ratio >= 2:1, OR 3+ colors but secondary coverage < 10%
    - DUAL_BAL: 2 colors with ratio < 2:1 (e.g., 55/45)
    - MULTI_DOM: 3+ colors, secondary >= 10%, dominant >= 50%
    - MULTI_BAL: 3+ colors, secondary >= 10%, dominant < 50%
    """
    # Filter using dynamic significance thresholds
    significant = filter_significant_clusters(merged_clusters)

    if len(significant) == 0:
        return 'MONO', 0, []  # Edge case

    n = len(significant)
    coverages = [c['perc'] for c in significant]

    if n == 1:
        return 'MONO', 1, coverages

    # Calculate total secondary coverage (everything except dominant)
    secondary_coverage = sum(coverages[1:])

    if n == 2:
        # Two colors - check ratio for DOM_ACC vs DUAL_BAL
        ratio = coverages[0] / coverages[1] if coverages[1] > 0 else float('inf')
        archetype = 'DOM_ACC' if ratio >= DOMINANCE_RATIO else 'DUAL_BAL'
        return archetype, 2, coverages
    else:
        # 3+ colors - check if secondary coverage is substantial
        if secondary_coverage < SECONDARY_COVERAGE_THRESHOLD:
            # Tiny accents - treat as dominant + accent
            return 'DOM_ACC', n, coverages
        else:
            # Substantial secondary - it's truly multi-color
            archetype = 'MULTI_DOM' if coverages[0] >= 50 else 'MULTI_BAL'
            return archetype, n, coverages


def compute_saturation_metadata_hsb(merged_clusters, original_clusters):
    """
    Compute saturation metrics using HSB saturation.

    The has_saturated field uses HSB sat >= 50% (Photoshop-style saturation)
    instead of LAB chroma >= 30.
    """
    if len(merged_clusters) == 0:
        return {
            'max_hsb_sat': 0,
            'has_saturated': False,
            'saturated_coverage': 0,
            'muted_coverage': 0,
            'neutral_coverage': 0,
            'saturation_sequence': '',
        }

    # Compute HSB saturation for each merged cluster
    lab_l = np.array([c['lab_l'] for c in merged_clusters])
    lab_a = np.array([c['lab_a'] for c in merged_clusters])
    lab_b = np.array([c['lab_b'] for c in merged_clusters])

    hsb_sats = compute_hsb_saturation(lab_l, lab_a, lab_b)

    sat_data = []
    for i, c in enumerate(merged_clusters):
        hsb_sat = hsb_sats[i]
        if hsb_sat >= SATURATED_HSB:
            sat_type = 'S'
        elif hsb_sat >= NEUTRAL_HSB:
            sat_type = 'M'
        else:
            sat_type = 'N'
        sat_data.append((sat_type, c['perc'], hsb_sat))

    return {
        'max_hsb_sat': max(d[2] for d in sat_data),
        'has_saturated': any(d[0] == 'S' for d in sat_data),
        'saturated_coverage': sum(d[1] for d in sat_data if d[0] == 'S'),
        'muted_coverage': sum(d[1] for d in sat_data if d[0] == 'M'),
        'neutral_coverage': sum(d[1] for d in sat_data if d[0] == 'N'),
        'saturation_sequence': ''.join(d[0] for d in sat_data),
    }


def classify_product(clusters):
    """
    Classify a single product using hue-based merging and simplified taxonomy.

    Returns dict with archetype and metadata.
    Uses HSB saturation for has_saturated field.
    """
    # Merge using hue-based logic
    merged = merge_clusters_hue_based(clusters)

    # Classify structure
    archetype, n_colors, coverages = classify_structure(merged)

    # Compute saturation metadata using HSB
    sat_meta = compute_saturation_metadata_hsb(merged, clusters)

    return {
        'archetype': archetype,
        'archetype_name': ARCHETYPE_NAMES[archetype],
        'n_colors': n_colors,
        'coverages': coverages,
        **sat_meta
    }


# ============================================================================
# DATA LOADING (READ-ONLY)
# ============================================================================

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


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main(brand=None):
    """
    Classify every product across all brands and write the master CSV +
    per-brand/gender distribution CSVs. `brand` is accepted for CLI uniformity
    but ignored — this analysis is inherently multi-brand.
    """
    print("=" * 70)
    print("Hue-Based Archetype Classification (HSB Saturation)")
    print("=" * 70)
    print("\nThis script is READ-ONLY:")
    print("  - Database: SELECT queries only")
    print("  - No image access")
    print("  - Output: NEW files in archetype_taxonomy/outputs/")

    print(f"\nHSB Saturation thresholds for has_saturated:")
    print(f"  Saturated: HSB sat >= {SATURATED_HSB}%")
    print(f"  Muted: {NEUTRAL_HSB}% <= HSB sat < {SATURATED_HSB}%")
    print(f"  Neutral: HSB sat < {NEUTRAL_HSB}%")

    print(f"\nMerge Configuration:")
    print(f"  Neutral chroma threshold: < {NEUTRAL_CHROMA_THRESHOLD}")
    print(f"  Low chroma merge threshold: < {LOW_CHROMA_MERGE_THRESHOLD}")
    print(f"  Hue merge threshold: < {HUE_MERGE_THRESHOLD} degrees")
    print(f"  Dominance ratio: >= {DOMINANCE_RATIO}")
    print(f"  Secondary coverage threshold: >= {SECONDARY_COVERAGE_THRESHOLD}% (for MULTI classification)")
    print(f"  Significance thresholds: opposite hue >{SIGNIFICANCE_THRESHOLD_OPPOSITE_HUE}%, different >{SIGNIFICANCE_THRESHOLD_DIFFERENT_HUE}%, similar >{SIGNIFICANCE_THRESHOLD_SIMILAR_HUE}%")

    print(f"\nSimplified Taxonomy:")
    for code, name in ARCHETYPE_NAMES.items():
        print(f"  {code}: {name}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load data
    print("\n[1/3] Loading data from all brands...")
    all_data = []

    for brand in BRANDS:
        df = load_brand_data(brand)
        df['brand'] = brand
        df['gender'] = df['query'].apply(assign_gender)
        all_data.append(df)

    combined_df = pd.concat(all_data, ignore_index=True)
    combined_df = combined_df[combined_df['gender'].notna()]

    print(f"\nTotal clusters: {len(combined_df):,}")

    # Classify each product
    print("\n[2/3] Classifying products...")

    products = []

    for (instance_id, brand, gender), group in combined_df.groupby(['instance_id', 'brand', 'gender']):
        # Note: perc may be Decimal from PostgreSQL, convert to float
        subset = group[['lab_l', 'lab_a', 'lab_b', 'perc']].copy()
        subset['perc'] = subset['perc'].astype(float)
        clusters = subset.to_dict('records')

        classification = classify_product(clusters)

        # Build coverage columns
        coverages = classification['coverages']
        coverage_cols = {f'coverage_{i+1}': coverages[i] if i < len(coverages) else 0
                        for i in range(5)}

        products.append({
            'instance_id': instance_id,
            'brand': brand,
            'brand_label': BRAND_LABELS[brand],
            'gender': gender,
            'gender_label': GENDER_LABELS[gender],
            'archetype': classification['archetype'],
            'archetype_name': classification['archetype_name'],
            'n_colors': classification['n_colors'],
            **coverage_cols,
            'max_hsb_sat': round(classification['max_hsb_sat'], 1),
            'has_saturated': classification['has_saturated'],
            'saturated_coverage': round(classification['saturated_coverage'], 1),
            'muted_coverage': round(classification['muted_coverage'], 1),
            'neutral_coverage': round(classification['neutral_coverage'], 1),
            'saturation_sequence': classification['saturation_sequence'],
        })

    products_df = pd.DataFrame(products)
    print(f"  Classified {len(products_df):,} products")

    # Save outputs
    print("\n[3/3] Saving outputs...")

    # Master CSV
    master_path = os.path.join(OUTPUT_DIR, 'all_products_classified_hsb.csv')
    products_df.to_csv(master_path, index=False)
    print(f"  Saved: {master_path}")

    # Distribution by brand/gender
    all_distributions = {}

    for brand in BRANDS:
        for gender in GENDERS:
            subset = products_df[
                (products_df['brand'] == brand) &
                (products_df['gender'] == gender)
            ]

            if len(subset) == 0:
                continue

            # Calculate distribution
            dist = subset.groupby(['archetype', 'archetype_name']).agg({
                'instance_id': 'count',
                'n_colors': 'first',
                'has_saturated': 'sum',  # Count of products with saturated
            }).reset_index()
            dist.columns = ['archetype', 'archetype_name', 'n_products', 'n_colors', 'n_with_saturated']
            dist['pct_products'] = (dist['n_products'] / len(subset) * 100).round(2)
            dist['pct_with_saturated'] = (dist['n_with_saturated'] / dist['n_products'] * 100).round(1)
            dist = dist.sort_values('n_products', ascending=False)

            # Save
            filename = f'{brand}_{gender}_distribution_hsb.csv'
            filepath = os.path.join(OUTPUT_DIR, filename)
            dist.to_csv(filepath, index=False)
            print(f"  Saved: {filepath}")

            all_distributions[f"{brand}_{gender}"] = dist

    # Print summary
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)

    print(f"\nTotal products classified: {len(products_df):,}")

    print("\nArchetype distribution (all brands/genders):")
    overall = products_df.groupby(['archetype', 'archetype_name']).size().reset_index(name='n')
    overall['pct'] = (overall['n'] / len(products_df) * 100).round(1)
    overall = overall.sort_values('n', ascending=False)

    for _, row in overall.iterrows():
        print(f"  {row['archetype_name']:20s}: {row['pct']:5.1f}% ({row['n']:,} products)")

    print("\nSaturation breakdown (HSB-based):")
    n_with_sat = products_df['has_saturated'].sum()
    print(f"  Products with saturated color (HSB >= {SATURATED_HSB}%): {n_with_sat:,} ({n_with_sat/len(products_df)*100:.1f}%)")
    print(f"  Products without saturated:    {len(products_df)-n_with_sat:,} ({(1-n_with_sat/len(products_df))*100:.1f}%)")

    print("\n" + "=" * 70)
    print(f"Output directory: {OUTPUT_DIR}")
    print("=" * 70)

    return products_df, all_distributions


if __name__ == '__main__':
    main()
