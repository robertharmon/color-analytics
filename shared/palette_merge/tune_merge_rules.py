"""
ENTRY - run: python cli.py tune-merge-rules  (recalibrates the merge thresholds in palette_merge.py).

Merge Rule Discovery / Tuning from Labeled Data
================================================

Recalibration tool for the hue-based palette merge primitive (palette_merge.py).
Reads merge-label exports (CSV) from the archetype labeling tool and scores the
LIVE merge rule against them, proposes improved threshold values, and (optionally)
fits a decision tree.

This script is READ-ONLY on the database (no queries at all).
Input: merge_labels_*.csv exported from the labeling tool (archetype-review).
Output: proposals + visualizations in outputs/merge_rule_discovery/

Scoring reflects the LIVE rule by default: `should_merge_hue_based` from
palette_merge is called on each pair, so "Live rule accuracy" is the accuracy of
the code the pipeline actually runs. The frozen `legacy_replica_should_merge`
(a simplified copy that drops the LOW_CHROMA_MAX_DELTA_L lightness guard on BOTH
the both-low-chroma and both-neutral rules) is always recorded alongside for
historical comparability, and `--legacy-replica` scores it as the headline for
reproducing pre-2026-07 numbers.

Usage:
    python cli.py tune-merge-rules --input merge_labels_all_brands.csv
    python cli.py tune-merge-rules --input merge_labels_all_brands.csv --fit-tree
    python cli.py tune-merge-rules --input merge_labels_all_brands.csv --propose-constants
    python cli.py tune-merge-rules --input merge_labels_nike.csv --legacy-replica
"""

import os
import sys
import math
import argparse

import pandas as pd
import numpy as np
from itertools import combinations

from shared.palette_merge.palette_merge import (
    NEUTRAL_CHROMA_THRESHOLD,
    LOW_CHROMA_MERGE_THRESHOLD,
    LOW_CHROMA_MAX_DELTA_L,
    HUE_MERGE_THRESHOLD,
    get_chroma,
    get_hue_angle,
    hue_difference,
    should_merge_hue_based,
    compute_merge_group_indices,
    DEFAULT_THRESHOLDS,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, 'outputs', 'merge_rule_discovery')


# ---------------------------------------------------------------------------
# Frozen legacy replica (simplified — kept only for historical comparability)
# ---------------------------------------------------------------------------

def legacy_replica_should_merge(c1, c2):
    """FROZEN simplified copy of the merge rule — for historical comparison ONLY.

    This is NOT the live rule. It differs from palette_merge.should_merge_hue_based
    on TWO rules (the original docstring under-reported this as one):
      - Rule 1 (both low chroma): merges UNCONDITIONALLY here; the live rule
        requires |ΔL| <= LOW_CHROMA_MAX_DELTA_L.
      - Rule 2 (both neutral): merges UNCONDITIONALLY here; the live rule also
        requires |ΔL| <= LOW_CHROMA_MAX_DELTA_L.
    Consequently it merges black+white where the live rule keeps them apart.

    Preserved so `--legacy-replica` can reproduce pre-2026-07 accuracy numbers.
    Do NOT use it to score current behavior — score should_merge_hue_based.
    """
    ch1 = get_chroma(c1['lab_a'], c1['lab_b'])
    ch2 = get_chroma(c2['lab_a'], c2['lab_b'])

    # Rule 1: both low chroma → merge (no ΔL guard — divergence from live rule)
    if ch1 < LOW_CHROMA_MERGE_THRESHOLD and ch2 < LOW_CHROMA_MERGE_THRESHOLD:
        return True

    h1 = get_hue_angle(c1['lab_a'], c1['lab_b'])
    h2 = get_hue_angle(c2['lab_a'], c2['lab_b'])

    # Rule 2: both neutral → merge (no ΔL guard — divergence from live rule)
    if h1 is None and h2 is None:
        return True

    # Rule 3: one neutral, one chromatic → don't merge
    if (h1 is None) != (h2 is None):
        return False

    # Rule 4/5: both chromatic → compare hue
    return hue_difference(h1, h2) < HUE_MERGE_THRESHOLD


# ---------------------------------------------------------------------------
# 1c. Load and validate CSV
# ---------------------------------------------------------------------------

def load_merge_labels(csv_path):
    """Read and validate a merge labels CSV.

    Returns:
        DataFrame with validated columns.
    """
    if not os.path.isfile(csv_path):
        print(f"Error: file not found: {csv_path}")
        sys.exit(1)

    df = pd.read_csv(csv_path)

    expected_cols = {
        'instance_id', 'cluster_idx', 'lab_l', 'lab_a', 'lab_b',
        'perc', 'hex', 'group', 'algo_group', 'user_modified',
    }
    missing = expected_cols - set(df.columns)
    if missing:
        print(f"Error: CSV missing columns: {missing}")
        sys.exit(1)

    n_products = df['instance_id'].nunique()
    n_clusters = len(df)
    n_modified = df.groupby('instance_id')['user_modified'].any().sum()

    print(f"Loaded {csv_path}")
    print(f"  Products:  {n_products}")
    print(f"  Clusters:  {n_clusters}")
    print(f"  Modified:  {n_modified} products ({n_modified / n_products * 100:.1f}%)")
    return df


# ---------------------------------------------------------------------------
# 1d. Generate pairwise features
# ---------------------------------------------------------------------------

def generate_pairwise_features(df, rule_fn=should_merge_hue_based):
    """Convert group-level labels into pairwise merge/don't-merge decisions.

    For each product, generate C(n,2) cluster pairs with features and labels.
    `rule_fn` is the merge predicate whose per-pair output lands in the
    `algo_pair_merge` column (default: the LIVE should_merge_hue_based). The
    frozen replica's output is ALWAYS recorded in `legacy_replica` regardless.

    Grouping is by (brand, instance_id) when a `brand` column is present, because
    instance_id is only unique within a brand (each brand is a separate DB).

    Returns:
        DataFrame with one row per pair.
    """
    rows = []

    has_brand = 'brand' in df.columns
    group_keys = ['brand', 'instance_id'] if has_brand else ['instance_id']
    if not has_brand:
        print("  WARNING: no 'brand' column — assuming a single-brand export; "
              "instance_ids may collide across brands.")

    for group_key, grp in df.groupby(group_keys):
        brand_val = group_key[0] if has_brand else None
        instance_id = group_key[-1] if isinstance(group_key, tuple) else group_key
        clusters = grp.to_dict('records')
        for ci, cj in combinations(range(len(clusters)), 2):
            c1 = clusters[ci]
            c2 = clusters[cj]

            # --- Labels ---
            user_merge = c1['group'] == c2['group']
            algo_group_match = c1['algo_group'] == c2['algo_group']

            # --- Raw features ---
            delta_L = abs(c1['lab_l'] - c2['lab_l'])

            h1 = get_hue_angle(c1['lab_a'], c1['lab_b'])
            h2 = get_hue_angle(c2['lab_a'], c2['lab_b'])
            h_diff = hue_difference(h1, h2)

            ch1 = get_chroma(c1['lab_a'], c1['lab_b'])
            ch2 = get_chroma(c2['lab_a'], c2['lab_b'])
            ch_min = min(ch1, ch2)
            ch_max = max(ch1, ch2)

            both_low_chroma = ch1 < LOW_CHROMA_MERGE_THRESHOLD and ch2 < LOW_CHROMA_MERGE_THRESHOLD
            both_neutral = ch1 < NEUTRAL_CHROMA_THRESHOLD and ch2 < NEUTRAL_CHROMA_THRESHOLD
            one_neutral_one_chromatic = (h1 is None) != (h2 is None)
            both_chromatic = h1 is not None and h2 is not None

            delta_chroma = abs(ch1 - ch2)

            p1 = c1['perc']
            p2 = c2['perc']

            lab_euclid = math.sqrt(
                (c1['lab_l'] - c2['lab_l'])**2 +
                (c1['lab_a'] - c2['lab_a'])**2 +
                (c1['lab_b'] - c2['lab_b'])**2
            )

            # Per-pair predictions: the scoring rule (live by default) + the
            # frozen replica (always recorded for historical comparability).
            algo_pair_merge = rule_fn(c1, c2)
            legacy_replica = legacy_replica_should_merge(c1, c2)

            rows.append({
                'brand': brand_val,
                'instance_id': instance_id,
                'idx_1': c1['cluster_idx'],
                'idx_2': c2['cluster_idx'],
                # Labels
                'user_should_merge': user_merge,
                'algo_group_match': algo_group_match,
                'algo_pair_merge': algo_pair_merge,
                'legacy_replica': legacy_replica,
                'user_disagrees': user_merge != algo_group_match,
                # Features
                'delta_L': delta_L,
                'hue_1': h1,
                'hue_2': h2,
                'hue_diff': h_diff,
                'chroma_1': round(ch1, 2),
                'chroma_2': round(ch2, 2),
                'chroma_min': round(ch_min, 2),
                'chroma_max': round(ch_max, 2),
                'both_low_chroma': both_low_chroma,
                'both_neutral': both_neutral,
                'one_neutral_one_chromatic': one_neutral_one_chromatic,
                'both_chromatic': both_chromatic,
                'delta_chroma': round(delta_chroma, 2),
                'perc_1': p1,
                'perc_2': p2,
                'perc_min': min(p1, p2),
                'perc_max': max(p1, p2),
                'lab_euclidean': round(lab_euclid, 2),
            })

    pairs_df = pd.DataFrame(rows)

    print(f"\nGenerated {len(pairs_df)} pairwise comparisons "
          f"from {df['instance_id'].nunique()} products")
    return pairs_df


# ---------------------------------------------------------------------------
# 1e. Summary statistics
# ---------------------------------------------------------------------------

def print_strata_banner(df):
    """Warn loudly when the labels came from a boundary-refinement sample."""
    if 'strata_mode' not in df.columns:
        print("  (strata_mode absent — assuming archetype-stratified, real-world sample)")
        return
    modes = set(str(m) for m in df['strata_mode'].dropna().unique())
    if modes - {'archetype'}:
        print("\n" + "*" * 66)
        print(f"*** BOUNDARY-REFINEMENT SAMPLE (strata_mode={sorted(modes)}) ***")
        print("Accuracies below are measured on a deliberately boundary-oversampled")
        print("set. They are NOT comparable to the 94.7% real-world figure and must")
        print("never be quoted as an accuracy estimate. Use them only to compare")
        print("candidate constants against each other on the same set.")
        print("*" * 66)


def print_summary_stats(pairs_df, legacy_headline=False):
    """Print summary statistics of merge labels and features.

    Reports three clearly-labeled accuracies: the live rule (what the pipeline
    runs), the frozen legacy replica (the historical figure), and the algo
    group-match rate (transitive union-find vs the user's groups).
    """
    n = len(pairs_df)
    user_merge_rate = pairs_df['user_should_merge'].mean()
    group_match_rate = pairs_df['algo_group_match'].mean()
    disagree_rate = pairs_df['user_disagrees'].mean()

    print("\n" + "=" * 60)
    print("SUMMARY STATISTICS")
    print("=" * 60)
    print(f"Total pairs:            {n}")
    print(f"User merge rate:        {user_merge_rate:.1%} ({pairs_df['user_should_merge'].sum()})")
    print(f"Algo group-match rate:  {group_match_rate:.1%} ({pairs_df['algo_group_match'].sum()})")
    print(f"Disagreement rate:      {disagree_rate:.1%} ({pairs_df['user_disagrees'].sum()})")

    # Three accuracies against the user's pairwise labels
    live_acc = (pairs_df['algo_pair_merge'] == pairs_df['user_should_merge']).mean()
    replica_acc = (pairs_df['legacy_replica'] == pairs_df['user_should_merge']).mean()
    group_acc = (pairs_df['algo_group_match'] == pairs_df['user_should_merge']).mean()

    headline = "  <- headline (--legacy-replica)" if legacy_headline else "  <- headline"
    print(f"\nLive rule pair accuracy:     {live_acc:.1%}"
          f"{'' if legacy_headline else headline}")
    print(f"Legacy replica pair accuracy:{replica_acc:.1%}"
          f"{headline if legacy_headline else ''}")
    print(f"Algo group-match accuracy:   {group_acc:.1%}")
    print("  (pairwise vs group-match differ by construction: union-find grouping"
          " is transitive, and user labels are group-derived.)")

    # Breakdown by chroma category
    print("\n--- Breakdown by chroma category ---")
    categories = [
        ('both_neutral', 'Both neutral (chroma < 8)'),
        ('both_low_chroma', 'Both low chroma (chroma < 15)'),
        ('one_neutral_one_chromatic', 'One neutral, one chromatic'),
        ('both_chromatic', 'Both chromatic'),
    ]
    for col, label in categories:
        subset = pairs_df[pairs_df[col]]
        if len(subset) == 0:
            print(f"  {label}: 0 pairs")
            continue
        merge_rate = subset['user_should_merge'].mean()
        disagree = subset['user_disagrees'].mean()
        print(f"  {label}: {len(subset)} pairs, "
              f"user merge {merge_rate:.1%}, disagree {disagree:.1%}")

    # Feature stats for merge vs don't-merge
    print("\n--- Feature means: merge vs don't-merge (user labels) ---")
    for col in ['delta_L', 'hue_diff', 'chroma_min', 'lab_euclidean']:
        merge_vals = pairs_df.loc[pairs_df['user_should_merge'], col].dropna()
        no_merge_vals = pairs_df.loc[~pairs_df['user_should_merge'], col].dropna()
        if len(merge_vals) > 0 and len(no_merge_vals) > 0:
            print(f"  {col:20s}  merge: mean={merge_vals.mean():.1f} med={merge_vals.median():.1f}"
                  f"  |  don't-merge: mean={no_merge_vals.mean():.1f} med={no_merge_vals.median():.1f}")

    print("=" * 60)


# ---------------------------------------------------------------------------
# 1f. Visualize decision boundary
# ---------------------------------------------------------------------------

def visualize_decision_boundary(pairs_df, output_dir):
    """Generate interactive HTML scatter plots of the merge decision boundary."""
    import plotly.graph_objects as go

    os.makedirs(output_dir, exist_ok=True)

    # --- Plot 1: hue_diff vs delta_L (both_chromatic only) ---
    chromatic = pairs_df[pairs_df['both_chromatic']].copy()
    if len(chromatic) > 0:
        fig = go.Figure()

        for merge_val, color, label in [(True, '#2ca02c', 'Merge'), (False, '#d62728', "Don't merge")]:
            subset = chromatic[chromatic['user_should_merge'] == merge_val]
            agree = subset[~subset['user_disagrees']]
            disagree = subset[subset['user_disagrees']]

            # Agreed with algo
            if len(agree) > 0:
                fig.add_trace(go.Scatter(
                    x=agree['hue_diff'], y=agree['delta_L'],
                    mode='markers',
                    marker=dict(
                        color=color, size=agree['perc_min'].clip(lower=2) * 1.2 + 4,
                        opacity=0.6, line=dict(width=0),
                    ),
                    name=f'{label} (agree)',
                    text=[f"inst={r.instance_id} idx={r.idx_1},{r.idx_2}<br>"
                          f"hue_diff={r.hue_diff:.1f} dL={r.delta_L}<br>"
                          f"chroma={r.chroma_1:.0f},{r.chroma_2:.0f} perc={r.perc_1:.1f},{r.perc_2:.1f}"
                          for _, r in agree.iterrows()],
                    hoverinfo='text',
                ))

            # User disagrees with algo
            if len(disagree) > 0:
                fig.add_trace(go.Scatter(
                    x=disagree['hue_diff'], y=disagree['delta_L'],
                    mode='markers',
                    marker=dict(
                        color=color, size=disagree['perc_min'].clip(lower=2) * 1.2 + 4,
                        opacity=0.9,
                        line=dict(width=2, color='black'),
                    ),
                    name=f'{label} (user disagrees)',
                    text=[f"inst={r.instance_id} idx={r.idx_1},{r.idx_2}<br>"
                          f"hue_diff={r.hue_diff:.1f} dL={r.delta_L}<br>"
                          f"chroma={r.chroma_1:.0f},{r.chroma_2:.0f} perc={r.perc_1:.1f},{r.perc_2:.1f}"
                          for _, r in disagree.iterrows()],
                    hoverinfo='text',
                ))

        # Current algo boundary: vertical line at hue_diff=25
        fig.add_vline(x=HUE_MERGE_THRESHOLD, line_dash='dash', line_color='gray',
                       annotation_text=f'Current threshold ({HUE_MERGE_THRESHOLD}°)')

        fig.update_layout(
            title='Merge Decision: Hue Difference vs Lightness Difference (both chromatic)',
            xaxis_title='Hue Difference (degrees)',
            yaxis_title='Delta L (lightness difference)',
            template='plotly_white',
            width=900, height=650,
        )
        path = os.path.join(output_dir, 'hue_diff_vs_delta_l.html')
        fig.write_html(path)
        print(f"  Saved: {path}")

    # --- Plot 2: delta_L vs chroma_min (both_low_chroma only) ---
    low_chroma = pairs_df[pairs_df['both_low_chroma']].copy()
    if len(low_chroma) > 0:
        fig = go.Figure()

        for merge_val, color, label in [(True, '#2ca02c', 'Merge'), (False, '#d62728', "Don't merge")]:
            subset = low_chroma[low_chroma['user_should_merge'] == merge_val]
            agree = subset[~subset['user_disagrees']]
            disagree = subset[subset['user_disagrees']]

            if len(agree) > 0:
                fig.add_trace(go.Scatter(
                    x=agree['chroma_min'], y=agree['delta_L'],
                    mode='markers',
                    marker=dict(
                        color=color, size=8, opacity=0.6, line=dict(width=0),
                    ),
                    name=f'{label} (agree)',
                    text=[f"inst={r.instance_id} idx={r.idx_1},{r.idx_2}<br>"
                          f"dL={r.delta_L} chroma={r.chroma_1:.1f},{r.chroma_2:.1f}<br>"
                          f"perc={r.perc_1:.1f},{r.perc_2:.1f}"
                          for _, r in agree.iterrows()],
                    hoverinfo='text',
                ))

            if len(disagree) > 0:
                fig.add_trace(go.Scatter(
                    x=disagree['chroma_min'], y=disagree['delta_L'],
                    mode='markers',
                    marker=dict(
                        color=color, size=8, opacity=0.9,
                        line=dict(width=2, color='black'),
                    ),
                    name=f'{label} (user disagrees)',
                    text=[f"inst={r.instance_id} idx={r.idx_1},{r.idx_2}<br>"
                          f"dL={r.delta_L} chroma={r.chroma_1:.1f},{r.chroma_2:.1f}<br>"
                          f"perc={r.perc_1:.1f},{r.perc_2:.1f}"
                          for _, r in disagree.iterrows()],
                    hoverinfo='text',
                ))

        # Current algo boundary: unconditional merge below chroma=15
        fig.add_vline(x=LOW_CHROMA_MERGE_THRESHOLD, line_dash='dash', line_color='gray',
                       annotation_text=f'Current low-chroma threshold ({LOW_CHROMA_MERGE_THRESHOLD})')

        fig.update_layout(
            title='Merge Decision: Delta L vs Chroma Min (both low-chroma pairs)',
            xaxis_title='Chroma Min',
            yaxis_title='Delta L (lightness difference)',
            template='plotly_white',
            width=900, height=650,
        )
        path = os.path.join(output_dir, 'delta_l_vs_chroma_min.html')
        fig.write_html(path)
        print(f"  Saved: {path}")

    # --- Plot 3: Combined scatter (all pairs) ---
    all_pairs = pairs_df.copy()
    # Sentinel for neutral pairs: hue_diff = -10 for visibility
    all_pairs['hue_diff_display'] = all_pairs['hue_diff'].fillna(-10)

    category_map = {
        'both_neutral': 'Both neutral',
        'both_low_chroma': 'Both low chroma',
        'one_neutral_one_chromatic': 'Neutral + chromatic',
        'both_chromatic': 'Both chromatic',
    }
    # Assign category (priority: most specific first)
    def assign_category(row):
        if row['both_neutral']:
            return 'Both neutral'
        if row['one_neutral_one_chromatic']:
            return 'Neutral + chromatic'
        if row['both_low_chroma']:
            return 'Both low chroma'
        return 'Both chromatic'

    all_pairs['category'] = all_pairs.apply(assign_category, axis=1)

    symbol_map = {
        'Both neutral': 'diamond',
        'Both low chroma': 'square',
        'Neutral + chromatic': 'x',
        'Both chromatic': 'circle',
    }

    fig = go.Figure()

    for cat, symbol in symbol_map.items():
        for merge_val, color, label in [(True, '#2ca02c', 'Merge'), (False, '#d62728', "Don't merge")]:
            subset = all_pairs[(all_pairs['category'] == cat) & (all_pairs['user_should_merge'] == merge_val)]
            if len(subset) == 0:
                continue
            fig.add_trace(go.Scatter(
                x=subset['hue_diff_display'], y=subset['delta_L'],
                mode='markers',
                marker=dict(
                    color=color, symbol=symbol, size=6, opacity=0.5,
                    line=dict(width=0.5, color='rgba(0,0,0,0.3)'),
                ),
                name=f'{cat} / {label}',
                text=[f"inst={r.instance_id} idx={r.idx_1},{r.idx_2}<br>"
                      f"hue_diff={r.hue_diff} dL={r.delta_L}<br>"
                      f"chroma={r.chroma_1:.0f},{r.chroma_2:.0f}"
                      for _, r in subset.iterrows()],
                hoverinfo='text',
                legendgroup=cat,
            ))

    fig.add_vline(x=HUE_MERGE_THRESHOLD, line_dash='dash', line_color='gray',
                   annotation_text=f'Hue threshold ({HUE_MERGE_THRESHOLD}°)')

    fig.update_layout(
        title='All Pairs: Hue Difference vs Delta L (colored by user merge decision)',
        xaxis_title='Hue Difference (degrees, -10 = neutral sentinel)',
        yaxis_title='Delta L (lightness difference)',
        template='plotly_white',
        width=1000, height=700,
    )
    path = os.path.join(output_dir, 'all_pairs_hue_vs_delta_l.html')
    fig.write_html(path)
    print(f"  Saved: {path}")


# ---------------------------------------------------------------------------
# 1g. Fit decision tree
# ---------------------------------------------------------------------------

def fit_decision_tree(pairs_df, output_dir, max_depth=3):
    """Fit a shallow decision tree to discover interpretable merge rules.

    Prints the tree rules, accuracy comparison, and confusion matrix.
    Saves rules to merge_rules_discovered.txt.
    """
    from sklearn.tree import DecisionTreeClassifier, export_text
    from sklearn.metrics import confusion_matrix, accuracy_score

    os.makedirs(output_dir, exist_ok=True)

    # Feature matrix
    feature_cols = [
        'delta_L', 'chroma_min', 'chroma_max', 'delta_chroma',
        'both_low_chroma', 'both_neutral', 'one_neutral_one_chromatic',
        'lab_euclidean',
    ]

    # hue_diff needs None → 0 fill for neutrals
    df = pairs_df.copy()
    df['hue_diff_filled'] = df['hue_diff'].fillna(0)
    feature_cols_with_hue = ['hue_diff_filled'] + feature_cols

    X = df[feature_cols_with_hue].values.astype(float)
    y = df['user_should_merge'].values.astype(int)

    # Fit tree
    clf = DecisionTreeClassifier(max_depth=max_depth, min_samples_leaf=20, random_state=42)
    clf.fit(X, y)

    # Tree rules
    tree_text = export_text(clf, feature_names=feature_cols_with_hue, decimals=1)

    print("\n" + "=" * 60)
    print("DECISION TREE RULES")
    print("=" * 60)
    print(tree_text)

    # Accuracy
    tree_pred = clf.predict(X)
    tree_acc = accuracy_score(y, tree_pred)

    algo_pred = df['algo_pair_merge'].values.astype(int)
    algo_acc = accuracy_score(y, algo_pred)

    print(f"\nTree accuracy (training):       {tree_acc:.1%}")
    print(f"Scored rule accuracy:           {algo_acc:.1%}  (live rule unless --legacy-replica)")
    print(f"Improvement:                    {tree_acc - algo_acc:+.1%}")

    # Confusion matrices
    print("\n--- Tree confusion matrix ---")
    print("               Predicted")
    print("              Don't-merge  Merge")
    cm_tree = confusion_matrix(y, tree_pred)
    print(f"  Don't-merge   {cm_tree[0, 0]:>6d}    {cm_tree[0, 1]:>6d}")
    print(f"  Merge         {cm_tree[1, 0]:>6d}    {cm_tree[1, 1]:>6d}")

    print("\n--- Current algo confusion matrix ---")
    print("               Predicted")
    print("              Don't-merge  Merge")
    cm_algo = confusion_matrix(y, algo_pred)
    print(f"  Don't-merge   {cm_algo[0, 0]:>6d}    {cm_algo[0, 1]:>6d}")
    print(f"  Merge         {cm_algo[1, 0]:>6d}    {cm_algo[1, 1]:>6d}")

    # Feature importances
    print("\n--- Feature importances ---")
    for name, imp in sorted(zip(feature_cols_with_hue, clf.feature_importances_),
                             key=lambda x: -x[1]):
        if imp > 0.01:
            print(f"  {name:30s} {imp:.3f}")

    # Save rules
    rules_path = os.path.join(output_dir, 'merge_rules_discovered.txt')
    with open(rules_path, 'w') as f:
        f.write("Merge Rule Discovery - Decision Tree Rules\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"max_depth={max_depth}, min_samples_leaf=20\n")
        f.write(f"Training samples: {len(y)}\n")
        f.write(f"Tree accuracy:    {tree_acc:.1%}\n")
        f.write(f"Algo accuracy:    {algo_acc:.1%}\n")
        f.write(f"Improvement:      {tree_acc - algo_acc:+.1%}\n\n")
        f.write("Tree Rules:\n")
        f.write("-" * 50 + "\n")
        f.write(tree_text)
        f.write("\n\nFeature Importances:\n")
        f.write("-" * 50 + "\n")
        for name, imp in sorted(zip(feature_cols_with_hue, clf.feature_importances_),
                                 key=lambda x: -x[1]):
            if imp > 0.01:
                f.write(f"  {name:30s} {imp:.3f}\n")
    print(f"\n  Saved rules: {rules_path}")

    print("=" * 60)


# ---------------------------------------------------------------------------
# Constant-shape proposals (sweep the named constants; sklearn-free)
# ---------------------------------------------------------------------------

# (MergeThresholds field, human constant name, candidate values)
CONSTANT_SWEEPS = [
    ('hue_merge',              'HUE_MERGE_THRESHOLD',        list(range(10, 46))),
    ('low_chroma_merge',       'LOW_CHROMA_MERGE_THRESHOLD', list(range(5, 31))),
    ('low_chroma_max_delta_l', 'LOW_CHROMA_MAX_DELTA_L',     list(range(10, 61))),
    ('neutral_chroma',         'NEUTRAL_CHROMA_THRESHOLD',   list(range(2, 21))),
]


def reconstruct_products(df):
    """From a merge-labels df, rebuild per-product cluster dicts + user groups.

    Groups by (brand, instance_id) when brand is present. Clusters keep cluster_idx
    order so pairwise indices line up with the user's `group` labels. The CSV
    already carries lab_l/lab_a/lab_b, so no DB access is needed.
    """
    has_brand = 'brand' in df.columns
    keys = ['brand', 'instance_id'] if has_brand else ['instance_id']
    products = []
    for _, grp in df.groupby(keys):
        grp = grp.sort_values('cluster_idx')
        clusters = grp[['lab_l', 'lab_a', 'lab_b', 'perc']].to_dict('records')
        products.append({'clusters': clusters, 'user_groups': grp['group'].tolist()})
    return products


def _group_accuracy(products, thresholds):
    """Pairwise accuracy of the TRANSITIVE grouping (what the pipeline applies)."""
    correct = total = 0
    for p in products:
        clusters = [dict(c) for c in p['clusters']]  # copy: fn adds chroma/hue
        pred = compute_merge_group_indices(clusters, thresholds)
        ug = p['user_groups']
        for i, j in combinations(range(len(ug)), 2):
            correct += (pred[i] == pred[j]) == (ug[i] == ug[j])
            total += 1
    return correct / total if total else 0.0


def _pair_accuracy(products, thresholds):
    """Pairwise accuracy of the raw (non-transitive) predicate — for continuity."""
    correct = total = 0
    for p in products:
        clusters, ug = p['clusters'], p['user_groups']
        for i, j in combinations(range(len(ug)), 2):
            correct += should_merge_hue_based(clusters[i], clusters[j], thresholds) == (ug[i] == ug[j])
            total += 1
    return correct / total if total else 0.0


def _with_field(thresholds, field, value):
    return thresholds._replace(**{field: value})


def _mcnemar(products, base_t, cand_t):
    """Count pairs the grouping flips between base and candidate; exact binomtest."""
    from scipy.stats import binomtest
    n_fixed = n_broken = 0
    for p in products:
        cl = p['clusters']; ug = p['user_groups']
        gb = compute_merge_group_indices([dict(c) for c in cl], base_t)
        gc = compute_merge_group_indices([dict(c) for c in cl], cand_t)
        for i, j in combinations(range(len(ug)), 2):
            user = ug[i] == ug[j]
            b_ok = (gb[i] == gb[j]) == user
            c_ok = (gc[i] == gc[j]) == user
            if b_ok and not c_ok:
                n_broken += 1
            elif c_ok and not b_ok:
                n_fixed += 1
    n_changed = n_fixed + n_broken
    p_val = binomtest(n_fixed, n_changed, 0.5).pvalue if n_changed else 1.0
    return n_changed, n_fixed, n_broken, p_val


def _cv_best_gain(products, field, candidates, base_t, seed=42, folds=5):
    """Grouped k-fold: pick best value on train, score held-out. Returns
    (mean held-out acc at current value, mean held-out acc at per-fold best)."""
    import random
    idx = list(range(len(products)))
    random.Random(seed).shuffle(idx)
    fold_of = {p: (k % folds) for k, p in enumerate(idx)}
    cur = getattr(base_t, field)
    held_cur, held_best = [], []
    for f in range(folds):
        train = [products[i] for i in idx if fold_of[i] != f]
        test = [products[i] for i in idx if fold_of[i] == f]
        if not train or not test:
            continue
        best_v, best_a = cur, -1.0
        for v in candidates:
            a = _group_accuracy(train, _with_field(base_t, field, v))
            if a > best_a:
                best_a, best_v = a, v
        held_cur.append(_group_accuracy(test, _with_field(base_t, field, cur)))
        held_best.append(_group_accuracy(test, _with_field(base_t, field, best_v)))
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    return mean(held_cur), mean(held_best)


def propose_constants(df, output_dir):
    """Sweep each named constant against the labels; emit constant-shape proposals.

    Headline metric is group_accuracy (the transitive rule the pipeline runs).
    Never writes palette_merge.py — the operator applies proposals by hand.
    """
    os.makedirs(output_dir, exist_ok=True)
    products = reconstruct_products(df)
    base_t = DEFAULT_THRESHOLDS

    print("\n" + "=" * 66)
    print("CONSTANT-SHAPE PROPOSALS  (headline = group accuracy = the live rule)")
    print("=" * 66)
    base_group = _group_accuracy(products, base_t)
    base_pair = _pair_accuracy(products, base_t)
    print(f"Current: group_acc {base_group:.1%} | pair_acc {base_pair:.1%} "
          f"over {len(products)} products")

    lines, csv_rows = [], []
    per_constant_best = {}
    for field, name, candidates in CONSTANT_SWEEPS:
        cur = getattr(base_t, field)
        best_v, best_a = cur, base_group
        for v in candidates:
            a = _group_accuracy(products, _with_field(base_t, field, v))
            csv_rows.append((name, v, round(a, 4)))
            if a > best_a + 1e-12:
                best_a, best_v = a, v
        per_constant_best[field] = best_v

        if best_v == cur:
            line = f"{name:<28} {cur} -> {cur}   no improvement (best candidate ties current)"
        else:
            held_cur, held_best = _cv_best_gain(products, field, candidates, base_t)
            n_ch, n_fx, n_br, pval = _mcnemar(
                products, base_t, _with_field(base_t, field, best_v))
            flag = "" if (held_best - held_cur) > 0 else "  NOT RECOMMENDED (in-sample only)"
            line = (f"{name:<28} {cur} -> {best_v}   "
                    f"group_acc {base_group:.1%} -> {best_a:.1%} ({best_a-base_group:+.1%})  "
                    f"held-out {held_best-held_cur:+.1%}  "
                    f"{n_ch} changed ({n_fx} fixed / {n_br} broken, p={pval:.3f}){flag}")
        print("  " + line)
        lines.append(line)

    # Joint greedy (coordinate descent) — higher overfit risk
    joint = base_t
    for _ in range(3):
        for field, name, candidates in CONSTANT_SWEEPS:
            cur = getattr(joint, field)
            best_v, best_a = cur, _group_accuracy(products, joint)
            for v in candidates:
                a = _group_accuracy(products, _with_field(joint, field, v))
                if a > best_a + 1e-12:
                    best_a, best_v = a, v
            joint = _with_field(joint, field, best_v)
    joint_acc = _group_accuracy(products, joint)
    print(f"\n  joint (greedy, higher overfit risk): group_acc "
          f"{base_group:.1%} -> {joint_acc:.1%}")
    print(f"    {dict(joint._asdict())}")

    # Hand-edit instructions
    print("\n  To apply, edit shared/palette_merge/palette_merge.py by hand:")
    for field, name, _ in CONSTANT_SWEEPS:
        cur = getattr(base_t, field); best = per_constant_best[field]
        if best != cur:
            print(f"      {name} = {best}   # was {cur}")
    print("  This tool never writes palette_merge.py.")

    # Files
    txt = os.path.join(output_dir, 'merge_constant_proposals.txt')
    with open(txt, 'w', encoding='utf-8') as f:
        f.write("Merge Constant Proposals\n" + "=" * 40 + "\n")
        f.write(f"Current group_acc {base_group:.4f} | pair_acc {base_pair:.4f}\n\n")
        f.write("\n".join(lines) + "\n\n")
        f.write(f"joint (greedy): group_acc {joint_acc:.4f}  {dict(joint._asdict())}\n")
    pd.DataFrame(csv_rows, columns=['constant', 'value', 'group_acc']).to_csv(
        os.path.join(output_dir, 'merge_constant_sweep.csv'), index=False)
    print(f"\n  Saved: {txt}")
    print("=" * 66)


# ---------------------------------------------------------------------------
# 1h. Export pairwise CSV
# ---------------------------------------------------------------------------

def export_pairwise_csv(pairs_df, output_dir):
    """Save pairwise features to CSV for external analysis."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, 'pairwise_features.csv')
    pairs_df.to_csv(path, index=False)
    print(f"  Saved: {path} ({len(pairs_df)} rows)")


# ---------------------------------------------------------------------------
# 1i. Entry point
# ---------------------------------------------------------------------------

def main(brand=None):
    parser = argparse.ArgumentParser(
        description='Score the live merge rule against labeled data and propose thresholds.')
    parser.add_argument('--input', required=True,
                        help='Path to merge_labels_*.csv')
    parser.add_argument('--fit-tree', action='store_true',
                        help='Fit a decision tree (requires scikit-learn) AND propose constants')
    parser.add_argument('--propose-constants', action='store_true',
                        help='Sweep the named constants and propose values (sklearn-free)')
    parser.add_argument('--no-propose', action='store_true',
                        help='Suppress constant proposals under --fit-tree')
    parser.add_argument('--legacy-replica', action='store_true',
                        help='Score the FROZEN replica instead of the live rule '
                             '(reproduce pre-2026-07 numbers only)')
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_DIR,
                        help=f'Output directory (default: {DEFAULT_OUTPUT_DIR})')
    args, _ = parser.parse_known_args()

    rule_fn = legacy_replica_should_merge if args.legacy_replica else should_merge_hue_based
    if args.legacy_replica:
        print("\n" + "!" * 66)
        print("SCORING THE FROZEN REPLICA, NOT THE LIVE RULE")
        print("For reproducing pre-2026-07 numbers only. The replica drops the")
        print("ΔL guard on both-low-chroma and both-neutral merges.")
        print("!" * 66)

    df = load_merge_labels(args.input)
    print_strata_banner(df)
    pairs_df = generate_pairwise_features(df, rule_fn=rule_fn)
    print_summary_stats(pairs_df, legacy_headline=args.legacy_replica)
    export_pairwise_csv(pairs_df, args.output_dir)

    print("\nGenerating visualizations...")
    visualize_decision_boundary(pairs_df, args.output_dir)

    if args.fit_tree:
        fit_decision_tree(pairs_df, args.output_dir)

    # Constant proposals: under --fit-tree (unless --no-propose) or standalone.
    if (args.fit_tree and not args.no_propose) or args.propose_constants:
        propose_constants(df, args.output_dir)

    print(f"\nAll outputs saved to: {args.output_dir}")


if __name__ == '__main__':
    main()
