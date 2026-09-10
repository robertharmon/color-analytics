"""
ENTRY - run: python cli.py assign-zones  (propagate zones to other brands via a random forest).

Auto Zone Assignment
=====================

Uses a Random Forest trained on Nike Mens manual zone groupings to
automatically assign CIEDE2000 clusters into color zones for any brand/gender.

Workflow:
1. Train RF on Nike Mens zone labels (ground truth from cluster_zones.json)
2. Compute pairwise features for target brand/gender's clusters
3. Predict zone groupings via agglomerative clustering on (1 - RF prob)
4. Export cluster_zones.json in target's subdirectory
5. Generate thumbnails (farthest-point diversity sampling)

Self-validation mode: when run on nike/mens, uses cross-validated probabilities
and compares against manual zones without overwriting them.

Usage:
    python cli.py assign-zones nike --gender mens    # self-validation
    python cli.py assign-zones adidas --gender mens  # assign zones
"""

import json
import os
import time

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from shared.ciede2000 import ciede2000_pairwise, detect_gpu
from shared.hsb import compute_hsb_saturation

# =============================================================================
# CONFIGURATION
# =============================================================================

TRAINING_BRAND = 'nike'
TRAINING_GENDER = 'mens'

RF_N_ESTIMATORS = 200
RF_MAX_DEPTH = 8
RF_MIN_SAMPLES_LEAF = 10

LINKAGE_METHOD = 'complete'

# The single merge dial: cluster pairs the RF is at least this confident share a zone
# get merged into one zone. Expressed as a probability because that is what the RF
# emits and how the threshold is reasoned about. scipy's agglomerative linkage merges
# on DISTANCE, not probability, so the (1 - p) conversion is derived inline where the
# linkage is called — never stored as a second constant that could drift out of sync.
CONFIDENT_MERGE_PROB = 0.80
CV_FOLDS = 5

FEATURE_COLS = [
    'ciede2000',
    'delta_L',
    'delta_chroma',
    'delta_hue',
    'mean_L',
    'mean_chroma',
    'min_chroma',
    'max_chroma',
    'mean_hsb_sat',
    'delta_hsb_sat',
    'product_ratio',
]

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_BASE = os.path.join(_SCRIPT_DIR, 'outputs')


# =============================================================================
# DATA LOADING
# =============================================================================

def load_centroids(input_dir):
    """Load cluster centroids CSV."""
    path = os.path.join(input_dir, 'cluster_centroids.csv')
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"cluster_centroids.csv not found in {input_dir}. "
            "Run consolidate-colors first."
        )
    df = pd.read_csv(path)
    for col in ['lab_l', 'lab_a', 'lab_b']:
        df[col] = df[col].astype(float)
    return df


def load_zones(input_dir):
    """Load manual zone definitions. Returns None if file doesn't exist."""
    path = os.path.join(input_dir, 'cluster_zones.json')
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        data = json.load(f)
    return data['zones']


# =============================================================================
# PAIR FEATURE COMPUTATION
# =============================================================================

def compute_pair_features(centroids_df, condensed_distances):
    """Compute features for ALL cluster pairs (no labels).

    Returns DataFrame with cluster_a, cluster_b, and 11 feature columns.
    """
    dist_matrix = squareform(condensed_distances)

    cid_to_idx = {}
    for idx, row in centroids_df.iterrows():
        cid_to_idx[int(row['cluster_id'])] = idx

    cluster_ids = sorted(cid_to_idx.keys())
    n = len(cluster_ids)

    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            cid_a = cluster_ids[i]
            cid_b = cluster_ids[j]
            idx_a = cid_to_idx[cid_a]
            idx_b = cid_to_idx[cid_b]

            row_a = centroids_df.iloc[idx_a]
            row_b = centroids_df.iloc[idx_b]

            ciede = dist_matrix[idx_a, idx_b]

            l_a, a_a, b_a = row_a['lab_l'], row_a['lab_a'], row_a['lab_b']
            l_b, a_b, b_b = row_b['lab_l'], row_b['lab_a'], row_b['lab_b']

            chroma_a = np.sqrt(a_a**2 + b_a**2)
            chroma_b = np.sqrt(a_b**2 + b_b**2)
            hue_a = np.degrees(np.arctan2(b_a, a_a)) % 360
            hue_b = np.degrees(np.arctan2(b_b, a_b)) % 360

            delta_l = abs(l_a - l_b)
            delta_chroma = abs(chroma_a - chroma_b)

            hue_diff = abs(hue_a - hue_b)
            delta_hue = min(hue_diff, 360 - hue_diff)

            mean_l = (l_a + l_b) / 2
            mean_chroma = (chroma_a + chroma_b) / 2
            min_chroma = min(chroma_a, chroma_b)
            max_chroma = max(chroma_a, chroma_b)

            n_prod_a = int(row_a['n_products'])
            n_prod_b = int(row_b['n_products'])
            product_ratio = min(n_prod_a, n_prod_b) / max(n_prod_a, n_prod_b)

            hsb_a = float(compute_hsb_saturation(
                np.array([l_a]), np.array([a_a]), np.array([b_a])
            )[0])
            hsb_b = float(compute_hsb_saturation(
                np.array([l_b]), np.array([a_b]), np.array([b_b])
            )[0])
            mean_hsb = (hsb_a + hsb_b) / 2
            delta_hsb = abs(hsb_a - hsb_b)

            pairs.append({
                'cluster_a': cid_a,
                'cluster_b': cid_b,
                'ciede2000': round(ciede, 3),
                'delta_L': round(delta_l, 2),
                'delta_chroma': round(delta_chroma, 2),
                'delta_hue': round(delta_hue, 2),
                'mean_L': round(mean_l, 1),
                'mean_chroma': round(mean_chroma, 1),
                'min_chroma': round(min_chroma, 1),
                'max_chroma': round(max_chroma, 1),
                'mean_hsb_sat': round(mean_hsb, 1),
                'delta_hsb_sat': round(delta_hsb, 1),
                'product_ratio': round(product_ratio, 3),
                'n_products_a': n_prod_a,
                'n_products_b': n_prod_b,
            })

    return pd.DataFrame(pairs)


def add_pair_labels(pairs_df, zones):
    """Add same_zone labels to a pairs DataFrame."""
    cluster_to_zone = {}
    for zone_idx, zone in enumerate(zones):
        for cid in zone['cluster_ids']:
            cluster_to_zone[cid] = zone_idx

    labels = []
    for _, row in pairs_df.iterrows():
        zone_a = cluster_to_zone.get(int(row['cluster_a']), -1)
        zone_b = cluster_to_zone.get(int(row['cluster_b']), -2)
        labels.append(1 if (zone_a == zone_b and zone_a >= 0) else 0)

    pairs_df = pairs_df.copy()
    pairs_df['same_zone'] = labels
    return pairs_df


# =============================================================================
# MODEL TRAINING
# =============================================================================

def train_rf(X, y):
    """Train Random Forest on labeled pairs."""
    rf = RandomForestClassifier(
        n_estimators=RF_N_ESTIMATORS,
        max_depth=RF_MAX_DEPTH,
        min_samples_leaf=RF_MIN_SAMPLES_LEAF,
        class_weight='balanced',
        random_state=42,
        n_jobs=-1,
    )
    rf.fit(X, y)
    return rf


def cross_validate_rf(X, y):
    """Get cross-validated merge probabilities (unbiased)."""
    cv = StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=42)
    rf_kwargs = dict(
        n_estimators=RF_N_ESTIMATORS, max_depth=RF_MAX_DEPTH,
        min_samples_leaf=RF_MIN_SAMPLES_LEAF, class_weight='balanced',
        random_state=42, n_jobs=-1,
    )
    y_prob = cross_val_predict(
        RandomForestClassifier(**rf_kwargs), X, y, cv=cv, method='predict_proba'
    )[:, 1]
    return y_prob


# =============================================================================
# ZONE FORMATION
# =============================================================================

def form_zones_from_probs(centroids_df, pairs_df, prob_col,
                          distance_threshold, linkage_method):
    """Form zones via agglomerative clustering on (1 - merge_prob).

    Returns array of zone labels aligned with centroids_df.
    """
    cluster_ids = sorted(centroids_df['cluster_id'].tolist())
    n = len(cluster_ids)
    cid_to_pos = {cid: i for i, cid in enumerate(cluster_ids)}

    # Build full distance matrix from (1 - prob)
    dist_matrix = np.ones((n, n))
    np.fill_diagonal(dist_matrix, 0.0)

    for _, row in pairs_df.iterrows():
        i = cid_to_pos[int(row['cluster_a'])]
        j = cid_to_pos[int(row['cluster_b'])]
        d = 1.0 - row[prob_col]
        dist_matrix[i, j] = d
        dist_matrix[j, i] = d

    condensed = squareform(dist_matrix, checks=False)
    Z = linkage(condensed, method=linkage_method)
    labels = fcluster(Z, t=distance_threshold, criterion='distance')

    return labels, cluster_ids


def _hsb_class_order_zones(zones, centroids_df):
    """Order zones by HSB color class: neutral by L*, muted/saturated by hue."""
    product_counts = {}
    for _, row in centroids_df.iterrows():
        product_counts[int(row['cluster_id'])] = int(row['n_products'])

    cid_to_lab = {}
    for _, row in centroids_df.iterrows():
        cid_to_lab[int(row['cluster_id'])] = (
            row['lab_l'], row['lab_a'], row['lab_b']
        )

    zone_centroids = []
    for zone in zones:
        cids = zone['cluster_ids']
        weights = np.array([product_counts.get(cid, 1) for cid in cids],
                           dtype=float)
        labs = np.array([cid_to_lab.get(cid, (50, 0, 0)) for cid in cids])
        w = weights / weights.sum()
        wc = (labs * w[:, None]).sum(axis=0)
        zone_centroids.append(wc)

    zone_centroids = np.array(zone_centroids)
    hsb_sat = compute_hsb_saturation(
        zone_centroids[:, 0], zone_centroids[:, 1], zone_centroids[:, 2],
    )

    from shared.hsb import SATURATED_HSB, NEUTRAL_HSB

    neutral_idx = np.where(hsb_sat < NEUTRAL_HSB)[0]
    muted_idx = np.where((hsb_sat >= NEUTRAL_HSB) & (hsb_sat < SATURATED_HSB))[0]
    saturated_idx = np.where(hsb_sat >= SATURATED_HSB)[0]

    neutral_order = neutral_idx[np.argsort(zone_centroids[neutral_idx, 0])]

    def _hue_sorted(idx):
        if len(idx) == 0:
            return idx
        hue = np.degrees(np.arctan2(
            zone_centroids[idx, 2], zone_centroids[idx, 1],
        ))
        hue = (hue + 180) % 360
        return idx[np.argsort(hue)]

    muted_order = _hue_sorted(muted_idx)
    saturated_order = _hue_sorted(saturated_idx)

    order = list(neutral_order) + list(muted_order) + list(saturated_order)
    return [zones[i] for i in order]


def build_zones_json(centroids_df, labels, cluster_ids, pairs_df,
                     prob_col, brand, threshold):
    """Build a cluster_zones.json-compatible dict from labels.

    Singletons with no confident merge partner are placed in 'unassigned'.
    """
    zone_clusters = {}
    for cid, label in zip(cluster_ids, labels):
        zone_clusters.setdefault(int(label), []).append(int(cid))

    product_counts = {}
    for _, row in centroids_df.iterrows():
        product_counts[int(row['cluster_id'])] = int(row['n_products'])

    cluster_max_prob = {}
    for _, row in pairs_df.iterrows():
        cid_a = int(row['cluster_a'])
        cid_b = int(row['cluster_b'])
        p = row[prob_col]
        cluster_max_prob[cid_a] = max(cluster_max_prob.get(cid_a, 0), p)
        cluster_max_prob[cid_b] = max(cluster_max_prob.get(cid_b, 0), p)

    zones = []
    unassigned = []
    for _, cids in zone_clusters.items():
        n_products = sum(product_counts.get(cid, 0) for cid in cids)
        zones.append({
            'cluster_ids': sorted(cids),
            'n_clusters': len(cids),
            'n_products': n_products,
        })

    zones = _hsb_class_order_zones(zones, centroids_df)

    for rank, zone in enumerate(zones, 1):
        zone['name'] = f'Group {rank}'

    total_products = sum(z['n_products'] for z in zones)

    return {
        'zones': zones,
        'unassigned': sorted(unassigned),
        'total_clusters': len(cluster_ids),
        'total_products': total_products,
        'metadata': {
            'method': 'rf_agglomerative',
            'linkage': LINKAGE_METHOD,
            'distance_threshold': threshold,
            'prob_threshold_equiv': round(1.0 - threshold, 2),
            'trained_on': f'{TRAINING_BRAND}_{TRAINING_GENDER}',
            'applied_to': brand,
            'n_zones': len(zones),
            'n_unassigned': len(unassigned),
        },
    }


# =============================================================================
# COMPARISON (self-validation only)
# =============================================================================

def compare_zones(auto_labels, manual_zones, cluster_ids, centroids_df):
    """Compare auto zones to manual zones. Returns ARI, NMI, zone counts."""
    cluster_to_manual = {}
    for zone_idx, zone in enumerate(manual_zones):
        for cid in zone['cluster_ids']:
            cluster_to_manual[cid] = zone_idx

    manual_labels = np.array([cluster_to_manual.get(cid, -1) for cid in cluster_ids])

    ari = adjusted_rand_score(manual_labels, auto_labels)
    nmi = normalized_mutual_info_score(manual_labels, auto_labels)

    return {
        'ari': ari,
        'nmi': nmi,
        'n_manual_zones': len(manual_zones),
        'n_auto_zones': len(set(auto_labels)),
    }


# =============================================================================
# THUMBNAIL GENERATION
# =============================================================================

def generate_thumbnails(brand, output_dir):
    """Generate farthest-point sampled thumbnails for all clusters."""
    from palette_explorer.build_zones import _load_product_images

    summary_path = os.path.join(output_dir, 'cluster_summary.csv')
    assignments_path = os.path.join(output_dir, 'product_assignments.csv')

    summary = pd.read_csv(summary_path)
    assignments = pd.read_csv(assignments_path)

    _load_product_images(assignments, summary, brand, output_dir=output_dir)


# =============================================================================
# MAIN
# =============================================================================

def main(brand=None, gender=None):
    brand = brand or TRAINING_BRAND
    gender = gender or TRAINING_GENDER
    is_self = (brand.lower() == TRAINING_BRAND and gender.lower() == TRAINING_GENDER)

    print("=" * 70)
    print(f"AUTO ZONE ASSIGNMENT — {brand.upper()} {gender.upper()}")
    print("=" * 70)

    use_gpu, device_info = detect_gpu()
    print(f"  GPU: {device_info}")
    print(f"  Training: {TRAINING_BRAND}_{TRAINING_GENDER}")
    print(f"  Target:   {brand}_{gender}")
    print(f"  Self-validation: {is_self}")

    training_dir = os.path.join(OUTPUT_BASE, f'{TRAINING_BRAND}_{TRAINING_GENDER}')
    target_dir = os.path.join(OUTPUT_BASE, f'{brand}_{gender}')

    # --- Step 1: Load training data ---
    print(f"\n[1/6] Loading training data ({TRAINING_BRAND}_{TRAINING_GENDER})...")
    train_centroids = load_centroids(training_dir)
    train_zones = load_zones(training_dir)
    if train_zones is None:
        raise FileNotFoundError(
            f"cluster_zones.json not found in {training_dir}. "
            "Manual zone assignments required for training."
        )
    print(f"  {len(train_centroids)} clusters, {len(train_zones)} zones")

    # --- Step 2: Compute training pair features ---
    print(f"\n[2/6] Computing training pair features...")
    t0 = time.time()
    train_lab = train_centroids[['lab_l', 'lab_a', 'lab_b']].values.astype(np.float64)
    train_condensed = ciede2000_pairwise(train_lab, use_gpu=use_gpu)
    train_pairs = compute_pair_features(train_centroids, train_condensed)
    train_pairs = add_pair_labels(train_pairs, train_zones)
    n_same = (train_pairs['same_zone'] == 1).sum()
    n_diff = (train_pairs['same_zone'] == 0).sum()
    print(f"  {len(train_pairs):,} pairs ({n_same:,} same, {n_diff:,} diff)")
    print(f"  Done in {time.time() - t0:.1f}s")

    # --- Step 3: Train RF ---
    print(f"\n[3/6] Training Random Forest...")
    t0 = time.time()
    X_train = train_pairs[FEATURE_COLS].values
    y_train = train_pairs['same_zone'].values
    rf = train_rf(X_train, y_train)
    print(f"  Done in {time.time() - t0:.1f}s")

    # --- Step 4: Score target pairs ---
    if is_self:
        print(f"\n[4/6] Self-validation: computing CV probs...")
        t0 = time.time()
        cv_probs = cross_validate_rf(X_train, y_train)
        train_pairs['cv_prob'] = np.round(cv_probs, 4)
        target_pairs = train_pairs
        target_centroids = train_centroids
        prob_col = 'cv_prob'
        print(f"  Done in {time.time() - t0:.1f}s")
    else:
        print(f"\n[4/6] Loading target ({brand}_{gender}) and scoring pairs...")
        t0 = time.time()
        target_centroids = load_centroids(target_dir)
        target_lab = target_centroids[['lab_l', 'lab_a', 'lab_b']].values.astype(np.float64)
        target_condensed = ciede2000_pairwise(target_lab, use_gpu=use_gpu)
        target_pairs = compute_pair_features(target_centroids, target_condensed)
        X_target = target_pairs[FEATURE_COLS].values
        target_pairs['rf_prob'] = np.round(rf.predict_proba(X_target)[:, 1], 4)
        prob_col = 'rf_prob'
        print(f"  {len(target_pairs):,} pairs scored")
        print(f"  Done in {time.time() - t0:.1f}s")

    # --- Step 5: Form zones ---
    merge_distance = 1.0 - CONFIDENT_MERGE_PROB  # scipy linkage merges on distance, not prob
    print(f"\n[5/6] Forming zones (prob >= {CONFIDENT_MERGE_PROB:.2f}, "
          f"{LINKAGE_METHOD} linkage)...")
    t0 = time.time()
    auto_labels, cluster_ids = form_zones_from_probs(
        target_centroids, target_pairs, prob_col,
        merge_distance, LINKAGE_METHOD,
    )
    zones_json = build_zones_json(
        target_centroids, auto_labels, cluster_ids, target_pairs,
        prob_col, brand, merge_distance,
    )
    n_zones = zones_json['metadata']['n_zones']
    n_unassigned = zones_json['metadata']['n_unassigned']
    print(f"  {n_zones} zones formed, {n_unassigned} clusters unassigned")
    print(f"  Done in {time.time() - t0:.1f}s")

    # --- Step 6: Export + thumbnails ---
    print(f"\n[6/6] Exporting results...")

    if is_self:
        # Self-validation: compare and write to auto_cluster_zones.json (don't overwrite manual)
        comparison = compare_zones(auto_labels, train_zones, cluster_ids, target_centroids)
        print(f"\n  === Self-Validation Results ===")
        print(f"  ARI:  {comparison['ari']:.4f}")
        print(f"  NMI:  {comparison['nmi']:.4f}")
        print(f"  Manual zones: {comparison['n_manual_zones']}")
        print(f"  Auto zones:   {n_zones} (+{n_unassigned} unassigned)")

        auto_path = os.path.join(target_dir, 'auto_cluster_zones.json')
        with open(auto_path, 'w') as f:
            json.dump(zones_json, f, indent=2)
        print(f"\n  Saved: {auto_path}")
        print(f"  (Manual cluster_zones.json NOT overwritten)")
    else:
        # Normal mode: write cluster_zones.json unless it already exists (avoid
        # clobbering hand-edits). If present, write to auto_cluster_zones.json
        # instead — same safety pattern as the self-validation branch above.
        zones_path = os.path.join(target_dir, 'cluster_zones.json')
        if os.path.exists(zones_path):
            auto_path = os.path.join(target_dir, 'auto_cluster_zones.json')
            with open(auto_path, 'w') as f:
                json.dump(zones_json, f, indent=2)
            print(f"  ⚠ Existing cluster_zones.json preserved (may contain hand-edits).")
            print(f"  Saved: {auto_path}")
            print(f"  To adopt these assignments, manually replace cluster_zones.json.")
        else:
            with open(zones_path, 'w') as f:
                json.dump(zones_json, f, indent=2)
            print(f"  Saved: {zones_path}")

        print(f"\n  Generating thumbnails...")
        generate_thumbnails(brand, target_dir)

    # Generate zone builder HTML for manual review/editing
    print(f"\n  Generating zone builder...")
    from palette_explorer.build_zones import main as build_zones_html
    build_zones_html(brand=brand, gender=gender)

    print(f"\n{'=' * 70}")
    print(f"  Complete. Output: {target_dir}")
    print(f"{'=' * 70}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Auto-assign clusters to zones')
    parser.add_argument('brand', nargs='?', default=None)
    parser.add_argument('--gender', default=None)
    args = parser.parse_args()
    main(brand=args.brand, gender=args.gender)
