"""
ENTRY - run: python cli.py consolidate-colors <brand> --gender G  (CIEDE2000 color clustering + per-cluster discount z/t-tests + BH-FDR).

Color Consolidation + Per-Cluster Discount Analysis (CIEDE2000 clusters)
=======================================================================

The essential job is the CIEDE2000 agglomerative clustering that consolidates a
brand/gender's products into pure atomic color clusters (via build_clusters).
The per-cluster discount testing below is a secondary output layered on top.

For each perceptually-pure color cluster (built by build_color_clusters), tests
whether the cluster's discount FREQUENCY and DEPTH differ significantly from the
brand/gender baseline:

- Frequency: two-tailed proportion z-test vs the segment discount rate.
- Depth: one-sample t-test of discounted products' depth vs the segment mean.

Benjamini-Hochberg FDR correction is applied separately to the frequency and
depth p-values to control false discoveries across the many clusters tested.

This is the DISCOUNT/FDR half. It reuses build_color_clusters.build_clusters()
in-memory (so the clustering runs once), then writes the FULL cluster_summary.csv
(geometry + discount stats) that the palette_explorer consumes. READ-ONLY on the
database. The retired frequency/depth bar charts are intentionally not produced.

Outputs (outputs/<brand>_<gender>/): cluster_centroids.csv, product_assignments.csv,
    cluster_summary.csv (with discount stats), run_metadata.csv

Usage:
    python cli.py consolidate-colors
    python cli.py consolidate-colors nike --gender mens
"""

import os
import numpy as np
import pandas as pd
from scipy.stats import norm, ttest_1samp

from palette_explorer.build_color_clusters import (
    build_clusters,
    export_geometry,
    OUTPUT_DIR,
    BRAND_DEFAULT,
    GENDER_DEFAULT,
    CLUSTER_THRESHOLD,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

MIN_PRODUCTS_PER_CLUSTER = 14     # Normal approximation for z-test (n*p >= 5)
MIN_DISCOUNTED_PER_CLUSTER = 10   # For depth t-test
FDR_ALPHA = 0.05                  # Benjamini-Hochberg significance level


# =============================================================================
# BASELINE + STATISTICAL TESTING
# =============================================================================

def compute_baseline(dominant_df):
    """Compute overall discount rate and mean depth for the segment."""
    n_total = len(dominant_df)
    n_discounted = dominant_df['is_discounted'].sum()
    freq = n_discounted / n_total * 100 if n_total > 0 else 0

    discounted_mask = dominant_df['is_discounted']
    depths = dominant_df.loc[discounted_mask, 'discount_depth']
    depth = depths.mean() if len(depths) > 0 else 0

    return {
        'n_total': n_total,
        'n_discounted': int(n_discounted),
        'freq_pct': freq,
        'depth_pct': depth,
    }


def test_clusters(dominant_df, labels, centroids_df, baseline):
    """
    Test each cluster's discount frequency and depth against baseline.

    Frequency: proportion z-test (two-tailed)
    Depth: one-sample t-test against baseline mean depth

    Returns DataFrame with per-cluster test results.
    """
    df = dominant_df.copy()
    df['cluster_id'] = labels

    p0 = baseline['freq_pct'] / 100  # baseline proportion
    mu0 = baseline['depth_pct']       # baseline mean depth

    results = []
    for _, centroid in centroids_df.iterrows():
        cid = centroid['cluster_id']
        group = df[df['cluster_id'] == cid]
        n = len(group)

        row = {
            'cluster_id': cid,
            'n_products': n,
            'lab_l': centroid['lab_l'],
            'lab_a': centroid['lab_a'],
            'lab_b': centroid['lab_b'],
            'hex_color': centroid['hex_color'],
            'hue_angle': centroid['hue_angle'],
        }

        # --- Frequency test ---
        if n >= MIN_PRODUCTS_PER_CLUSTER:
            n_disc = group['is_discounted'].sum()
            p_hat = n_disc / n
            row['freq_pct'] = p_hat * 100
            row['freq_deviation_pp'] = (p_hat - p0) * 100
            row['n_discounted'] = int(n_disc)

            # Proportion z-test: z = (p_hat - p0) / sqrt(p0*(1-p0)/n)
            se = np.sqrt(p0 * (1 - p0) / n) if p0 > 0 and p0 < 1 else 1e-10
            z = (p_hat - p0) / se
            p_val = 2 * norm.sf(abs(z))
            row['freq_z'] = z
            row['freq_pval'] = p_val
            row['freq_testable'] = True
        else:
            row['freq_pct'] = np.nan
            row['freq_deviation_pp'] = np.nan
            row['n_discounted'] = np.nan
            row['freq_z'] = np.nan
            row['freq_pval'] = np.nan
            row['freq_testable'] = False

        # --- Depth test ---
        discounted = group[group['is_discounted']]
        n_disc_for_depth = len(discounted)
        if n_disc_for_depth >= MIN_DISCOUNTED_PER_CLUSTER:
            depths = discounted['discount_depth'].values
            row['depth_pct'] = depths.mean()
            row['depth_deviation_pp'] = depths.mean() - mu0
            row['n_discounted_depth'] = n_disc_for_depth

            t_stat, p_val = ttest_1samp(depths, mu0)
            row['depth_t'] = t_stat
            row['depth_pval'] = p_val
            row['depth_testable'] = True
        else:
            row['depth_pct'] = np.nan
            row['depth_deviation_pp'] = np.nan
            row['n_discounted_depth'] = n_disc_for_depth
            row['depth_t'] = np.nan
            row['depth_pval'] = np.nan
            row['depth_testable'] = False

        results.append(row)

    return pd.DataFrame(results)


# =============================================================================
# FDR CORRECTION
# =============================================================================

def benjamini_hochberg(pvals, alpha=0.05):
    """
    Benjamini-Hochberg FDR correction.

    Args:
        pvals: 1D array of p-values (no NaNs)
        alpha: FDR significance level

    Returns:
        reject: boolean array, True where null hypothesis is rejected
    """
    m = len(pvals)
    if m == 0:
        return np.array([], dtype=bool)

    sorted_indices = np.argsort(pvals)
    sorted_pvals = pvals[sorted_indices]

    # BH thresholds: (rank / m) * alpha
    thresholds = np.arange(1, m + 1) / m * alpha

    # Find largest rank where p <= threshold
    below = sorted_pvals <= thresholds
    if not np.any(below):
        return np.zeros(m, dtype=bool)

    max_rank = np.max(np.where(below)[0])

    reject = np.zeros(m, dtype=bool)
    reject[sorted_indices[:max_rank + 1]] = True
    return reject


def apply_fdr(results_df):
    """Apply BH FDR correction separately for frequency and depth tests."""
    df = results_df.copy()

    # Frequency FDR
    freq_mask = df['freq_testable'].fillna(False).astype(bool)
    df['freq_significant'] = False
    if freq_mask.sum() > 0:
        pvals = df.loc[freq_mask, 'freq_pval'].values
        reject = benjamini_hochberg(pvals, FDR_ALPHA)
        df.loc[freq_mask, 'freq_significant'] = reject

    # Depth FDR
    depth_mask = df['depth_testable'].fillna(False).astype(bool)
    df['depth_significant'] = False
    if depth_mask.sum() > 0:
        pvals = df.loc[depth_mask, 'depth_pval'].values
        reject = benjamini_hochberg(pvals, FDR_ALPHA)
        df.loc[depth_mask, 'depth_significant'] = reject

    return df


# =============================================================================
# EXPORT
# =============================================================================

def export_results(results_df, centroids_df, dominant_df, labels, baseline,
                   brand, gender, timings, output_dir):
    """Write geometry CSVs + the full (discount-tested) cluster_summary + metadata."""
    os.makedirs(output_dir, exist_ok=True)

    # Geometry (centroids + per-product assignments) — shared with build step
    export_geometry(centroids_df, dominant_df, labels, output_dir)

    # Full cluster summary (geometry + discount test results)
    summary_path = os.path.join(output_dir, 'cluster_summary.csv')
    results_df.to_csv(summary_path, index=False)
    print(f"  Saved: {summary_path}")

    # Run metadata
    n_freq_sig = results_df['freq_significant'].sum() if 'freq_significant' in results_df else 0
    n_depth_sig = results_df['depth_significant'].sum() if 'depth_significant' in results_df else 0
    n_testable_freq = results_df['freq_testable'].sum() if 'freq_testable' in results_df else 0
    n_testable_depth = results_df['depth_testable'].sum() if 'depth_testable' in results_df else 0

    meta = pd.DataFrame([{
        'brand': brand,
        'gender': gender,
        'cluster_threshold': CLUSTER_THRESHOLD,
        'min_products_per_cluster': MIN_PRODUCTS_PER_CLUSTER,
        'min_discounted_per_cluster': MIN_DISCOUNTED_PER_CLUSTER,
        'fdr_alpha': FDR_ALPHA,
        'baseline_freq_pct': baseline['freq_pct'],
        'baseline_depth_pct': baseline['depth_pct'],
        'n_products': baseline['n_total'],
        'n_discounted': baseline['n_discounted'],
        'n_clusters': len(centroids_df),
        'n_testable_freq': int(n_testable_freq),
        'n_testable_depth': int(n_testable_depth),
        'n_significant_freq': int(n_freq_sig),
        'n_significant_depth': int(n_depth_sig),
        'time_pairwise_s': timings.get('pairwise', 0),
        'time_linkage_s': timings.get('linkage', 0),
        'time_total_s': timings.get('total', 0),
    }])
    meta_path = os.path.join(output_dir, 'run_metadata.csv')
    meta.to_csv(meta_path, index=False)
    print(f"  Saved: {meta_path}")


# =============================================================================
# MAIN
# =============================================================================

def main(brand=None, gender=None, archive_limit=None):
    # Also accept --archive-limit N when invoked through the CLI dispatcher,
    # which routes brand/gender as kwargs but leaves other flags in sys.argv.
    import argparse
    _p = argparse.ArgumentParser(add_help=False)
    _p.add_argument('--archive-limit', type=int, default=None,
                    help='Cap the DB read to the N most recent archives per brand '
                         '(default: use all archives). Guard against >20 GB memory '
                         'in the fastcluster linkage step for very large brands.')
    _known, _ = _p.parse_known_args()
    if _known.archive_limit is not None:
        archive_limit = _known.archive_limit

    brand = brand or BRAND_DEFAULT
    gender = gender or GENDER_DEFAULT

    print("=" * 70)
    print(f"CIEDE2000 AGGLOMERATIVE DISCOUNT ANALYSIS — {brand.upper()} {gender.upper()}")
    print("=" * 70)
    print(f"  Threshold: {CLUSTER_THRESHOLD} ΔE₀₀")
    print(f"  Min products/cluster: {MIN_PRODUCTS_PER_CLUSTER}")
    print(f"  FDR α: {FDR_ALPHA}")
    if archive_limit is not None:
        print(f"  Archive scope: most recent {archive_limit} archives (--archive-limit)")
    else:
        print(f"  Archive scope: all archives (default)")

    # Build the cluster structure (clustering runs once, in-memory)
    dominant_df, labels, n_clusters, centroids_df, timings = build_clusters(
        brand, gender, archive_limit=archive_limit)

    # Compute baseline
    baseline = compute_baseline(dominant_df)
    print(f"\n  Baseline ({brand} {gender}):")
    print(f"    Discount frequency: {baseline['freq_pct']:.1f}% "
          f"({baseline['n_discounted']:,} / {baseline['n_total']:,})")
    print(f"    Discount depth: {baseline['depth_pct']:.1f}% off (among discounted)")

    # Statistical testing
    print(f"  Running statistical tests...")
    results_df = test_clusters(dominant_df, labels, centroids_df, baseline)

    # Merge centroid RGB into results for visualization
    rgb_cols = ['r', 'g', 'b']
    results_df = results_df.merge(
        centroids_df[['cluster_id'] + rgb_cols],
        on='cluster_id', how='left'
    )

    # FDR correction
    results_df = apply_fdr(results_df)

    # Summary
    n_freq_testable = results_df['freq_testable'].sum()
    n_freq_sig = results_df['freq_significant'].sum()
    n_depth_testable = results_df['depth_testable'].sum()
    n_depth_sig = results_df['depth_significant'].sum()

    print(f"\n  Results:")
    print(f"    Total clusters: {n_clusters:,}")
    print(f"    Frequency: {n_freq_sig} significant / {n_freq_testable} testable")
    print(f"    Depth:     {n_depth_sig} significant / {n_depth_testable} testable")

    # Export
    output_dir = os.path.join(OUTPUT_DIR, f'{brand}_{gender}')
    print(f"\n  Exporting results to {output_dir}...")
    export_results(
        results_df, centroids_df, dominant_df, labels, baseline,
        brand, gender, timings, output_dir
    )

    print(f"\n{'=' * 70}")
    print(f"  Complete. Total time: {timings['total']:.1f}s")
    print(f"{'=' * 70}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    parser.add_argument('brand', nargs='?', default=None,
                        help=f'Brand to analyze (default: {BRAND_DEFAULT})')
    parser.add_argument('--gender', default=None,
                        help=f'Gender filter (default: {GENDER_DEFAULT})')
    parser.add_argument('--archive-limit', type=int, default=None,
                        help='Cap the DB read to the N most recent archives per '
                             'brand (default: all archives). Use to keep the '
                             'fastcluster linkage step under 20 GB for very '
                             'large brands.')
    args = parser.parse_args()
    main(brand=args.brand, gender=args.gender, archive_limit=args.archive_limit)
