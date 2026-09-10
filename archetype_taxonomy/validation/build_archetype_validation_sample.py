"""
ENTRY - run: python cli.py archetype-validation-sample  (builds the stratified validation sample JSON).

Validation Sample Generator (Production Version)
==================================================

Generates a stratified random sample of products for manual validation
of the hue-based archetype classification system, balanced across
archetypes and brands. Each product record carries its raw + merged color
clusters and `algo_merge_groups` (the algorithm's per-cluster grouping), so the
same sample drives both archetype validation and merge-rule labeling.

This is the low-level sample step. Most workflows use `archetype-review`, which
classifies (if needed), samples, and renders the tool in one idempotent command.

This script is READ-ONLY:
- Database: Only SELECT queries (no INSERT, UPDATE, DELETE)
- Images: Read-only path resolution (no writes, moves, or modifications)
- CSV: Reads all_products_classified_hsb.csv (never modifies it)
- Output: Creates NEW JSON file in outputs/validation/

Programmatic API: build_validation_sample(...) -> output_path (used by archetype-review).

Usage:
    docker compose run --rm pipeline python cli.py archetype-validation-sample

    # With custom sample sizes
    docker compose run --rm pipeline python cli.py archetype-validation-sample \
        --mono 100 --dom-acc 80 --dual-bal 40 --multi-dom 40 --multi-bal 40
"""

import os
import sys
import json
import math
import hashlib
import argparse
from datetime import datetime
from collections import defaultdict

import numpy as np
import pandas as pd

from shared.db import connect_to_db
from shared.hsb import (
    lab_to_rgb_array,
    compute_hsb_saturation,
    SATURATED_HSB, NEUTRAL_HSB,
)
# Merge primitives are the shared single source of truth; the classifier owns
# the taxonomy pieces (significance filtering + the brand vocabulary).
from shared.palette_merge.palette_merge import (
    merge_clusters_hue_based,
    compute_merge_group_indices,
    get_chroma,
    get_hue_angle,
    hue_difference,
    classify_saturation_lab,
    DEFAULT_THRESHOLDS,
    NEUTRAL_CHROMA_THRESHOLD,
    LOW_CHROMA_MERGE_THRESHOLD,
    LOW_CHROMA_MAX_DELTA_L,
    HUE_MERGE_THRESHOLD,
)
from archetype_taxonomy.classify_archetypes import (
    filter_significant_clusters,
    BRANDS,
    BRAND_LABELS,
)

# ============================================================================
# CONFIGURATION
# ============================================================================

# Default sample targets per archetype
DEFAULT_ARCHETYPE_TARGETS = {
    'MONO': 150,
    'DOM_ACC': 100,
    'DUAL_BAL': 50,
    'MULTI_DOM': 50,
    'MULTI_BAL': 50,
}

ARCHETYPE_NAMES = {
    'MONO': 'Monochrome',
    'DOM_ACC': 'Dominant + Accent',
    'DUAL_BAL': 'Dual Balanced',
    'MULTI_DOM': 'Multi Dominant',
    'MULTI_BAL': 'Multi Balanced',
}

# Paths
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))     # archetype_taxonomy/validation/
_ARCHETYPE_DIR = os.path.dirname(_SCRIPT_DIR)                 # archetype_taxonomy/
_REPO_ROOT = os.path.dirname(_ARCHETYPE_DIR)                  # color-analytics/

# Read the classifier's master CSV from the archetype slice's outputs/
DEFAULT_INPUT_CSV = os.path.join(_ARCHETYPE_DIR, 'outputs', 'all_products_classified_hsb.csv')
DEFAULT_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, 'outputs')

# Images location: prefer the in-repo data store, then the external store.
_CODEBASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_REPO_ROOT)))
_IMAGES_CANDIDATES = [
    os.path.join(_REPO_ROOT, 'images'),
    r'D:\D01_Code_Forge\01_Codebase\Codebase_ColorAnalytics\03_images',
    os.path.join(_CODEBASE_DIR, '03_images'),
]
if os.environ.get("IMAGE_DIR"):
    _IMAGES_CANDIDATES.insert(0, os.environ["IMAGE_DIR"])
DEFAULT_IMAGES_DIR = next((p for p in _IMAGES_CANDIDATES if os.path.exists(p)), _IMAGES_CANDIDATES[1])

# SQL Queries (READ-ONLY)
IMAGE_METADATA_QUERY = """
    SELECT
        i.instance_id,
        i.archive_id_ref,
        i.family_id_ref,
        i.family_rank,
        a.pid,
        i.title
    FROM instance i
    JOIN appendix a ON i.instance_id = a.instance_id_ref
    WHERE i.instance_id IN %s
"""

CLUSTER_DATA_QUERY = """
    SELECT
        instance_id_ref,
        lab_l,
        lab_a,
        lab_b,
        perc
    FROM cluster_fpyolo11l241114_kmeans250218
    WHERE instance_id_ref IN %s
"""


# ============================================================================
# COLOR CONVERSION HELPERS
# ============================================================================

def lab_to_hex(L, a, b):
    """Convert single LAB color to hex string."""
    r, g, b_val = lab_to_rgb_array(
        np.array([L], dtype=float),
        np.array([a], dtype=float),
        np.array([b], dtype=float),
    )
    r_int = int(round(r[0] * 255))
    g_int = int(round(g[0] * 255))
    b_int = int(round(b_val[0] * 255))
    return f'#{r_int:02x}{g_int:02x}{b_int:02x}'


def compute_hsb_sat_single(lab_l, lab_a, lab_b):
    """Compute HSB saturation for a single LAB color."""
    sats = compute_hsb_saturation(
        np.array([lab_l], dtype=float),
        np.array([lab_a], dtype=float),
        np.array([lab_b], dtype=float),
    )
    return float(sats[0])


def classify_hsb_single(hsb_sat):
    """Classify a single HSB saturation value."""
    if hsb_sat >= SATURATED_HSB:
        return 'S'
    elif hsb_sat >= NEUTRAL_HSB:
        return 'M'
    else:
        return 'N'


# ============================================================================
# IMAGE PATH RESOLUTION (READ-ONLY)
# ============================================================================

def map_archive_dirs(brand, search_dir):
    """Map archive IDs to folder paths by parsing folder names."""
    archive_dir_map = {}
    brand_lower = brand.lower()

    if not os.path.exists(search_dir):
        return archive_dir_map

    for folder in os.listdir(search_dir):
        folder_path = os.path.join(search_dir, folder)
        if os.path.isdir(folder_path):
            parts = folder.split('_')
            if len(parts) >= 4:
                folder_brand = parts[0].lower()
                archive_id_part = parts[2]
                if folder_brand == brand_lower:
                    archive_dir_map[archive_id_part] = folder_path

    return archive_dir_map


def resolve_image_path(archive_dirs, archive_id, instance_id, family_id, rank, pid):
    """Construct path to original JPG image. READ-ONLY."""
    archive_key = str(archive_id)
    if archive_key not in archive_dirs:
        return None

    archive_folder = archive_dirs[archive_key]
    filename = f"{archive_id}-{instance_id}-{family_id}-{rank}-{pid}.jpg"
    full_path = os.path.join(archive_folder, filename)

    if os.path.exists(full_path):
        return full_path
    return None


def find_segmented_subfolder(archive_folder):
    """Find fpyolo11l241114_* subfolder. READ-ONLY."""
    import glob as glob_module

    if not os.path.exists(archive_folder):
        return None

    for item in os.listdir(archive_folder):
        if item.startswith('fpyolo11l241114_'):
            subfolder = os.path.join(archive_folder, item)
            if os.path.isdir(subfolder):
                return subfolder
    return None


def resolve_segmented_image_path(archive_dirs, archive_id, instance_id,
                                 family_id, rank, pid, seg_folder_cache=None):
    """Find segmented PNG image. READ-ONLY."""
    import glob as glob_module

    archive_key = str(archive_id)
    if archive_key not in archive_dirs:
        return None

    archive_folder = archive_dirs[archive_key]

    if seg_folder_cache is not None and archive_key in seg_folder_cache:
        seg_folder = seg_folder_cache[archive_key]
    else:
        seg_folder = find_segmented_subfolder(archive_folder)
        if seg_folder_cache is not None:
            seg_folder_cache[archive_key] = seg_folder

    if not seg_folder:
        return None

    base_pattern = (
        f"{archive_id}-{instance_id}-{family_id}-{rank}-{pid}"
        f"-fpyolo11l241114"
    )

    seg0_path = os.path.join(seg_folder, f"{base_pattern}-0.png")
    if os.path.exists(seg0_path):
        return seg0_path

    pattern = os.path.join(seg_folder, f"{base_pattern}-*.png")
    matches = glob_module.glob(pattern)
    if matches:
        return matches[0]

    return None


# ============================================================================
# DATABASE ACCESS (READ-ONLY)
# ============================================================================

def fetch_image_metadata_batch(brand, instance_ids):
    """Fetch image path components. READ-ONLY."""
    if not instance_ids:
        return {}

    conn, cur = connect_to_db(brand)
    try:
        cur.execute(IMAGE_METADATA_QUERY, (tuple(instance_ids),))
        rows = cur.fetchall()

        metadata = {}
        for row in rows:
            metadata[row['instance_id']] = {
                'archive_id': row['archive_id_ref'],
                'family_id': row['family_id_ref'],
                'rank': row['family_rank'],
                'pid': row['pid'],
                'title': row['title'] or 'Untitled'
            }
        return metadata
    finally:
        conn.close()


def fetch_cluster_data_batch(brand, instance_ids):
    """Fetch cluster data. READ-ONLY."""
    if not instance_ids:
        return {}

    conn, cur = connect_to_db(brand)
    try:
        cur.execute(CLUSTER_DATA_QUERY, (tuple(instance_ids),))
        rows = cur.fetchall()

        clusters = defaultdict(list)
        for row in rows:
            iid = row['instance_id_ref']
            clusters[iid].append({
                'lab_l': row['lab_l'],
                'lab_a': row['lab_a'],
                'lab_b': row['lab_b'],
                'perc': float(row['perc']),
            })

        return dict(clusters)
    finally:
        conn.close()


# ============================================================================
# STRATIFIED SAMPLING
# ============================================================================

def stratified_sample(df: pd.DataFrame, archetype_targets: dict, random_state: int = 42) -> pd.DataFrame:
    """
    Perform stratified sampling with fixed targets per archetype,
    balanced across brands.
    """
    sampled_frames = []

    for archetype, target in archetype_targets.items():
        arch_df = df[df['archetype'] == archetype].copy()

        if len(arch_df) == 0:
            print(f"  WARNING: No products found for {archetype}")
            continue

        if len(arch_df) <= target:
            print(f"  {archetype}: Taking all {len(arch_df)} available (target: {target})")
            sampled_frames.append(arch_df)
            continue

        # Balance across brands
        brand_samples = []
        per_brand_target = max(1, target // len(BRANDS))
        sampled_ids = set()

        for brand in BRANDS:
            brand_df = arch_df[arch_df['brand'] == brand]
            if len(brand_df) == 0:
                continue

            n_sample = min(per_brand_target, len(brand_df))
            sample = brand_df.sample(n=n_sample, random_state=random_state)
            brand_samples.append(sample)
            sampled_ids.update(sample['instance_id'].tolist())

        current_count = sum(len(s) for s in brand_samples)

        # Fill remainder from any brand
        if current_count < target:
            remaining = target - current_count
            available = arch_df[~arch_df['instance_id'].isin(sampled_ids)]
            if len(available) > 0:
                extra = available.sample(n=min(remaining, len(available)), random_state=random_state)
                brand_samples.append(extra)

        if brand_samples:
            arch_sample = pd.concat(brand_samples, ignore_index=True)
            print(f"  {archetype}: Sampled {len(arch_sample)} from {len(arch_df)} available (target: {target})")
            sampled_frames.append(arch_sample)

    if not sampled_frames:
        return pd.DataFrame()

    result = pd.concat(sampled_frames, ignore_index=True)

    # Shuffle the final result
    result = result.sample(frac=1, random_state=random_state).reset_index(drop=True)

    return result


# ============================================================================
# DATA ENRICHMENT (READ-ONLY DATABASE ACCESS)
# ============================================================================

def fetch_raw_clusters(sample_df: pd.DataFrame) -> dict:
    """
    Fetch raw (pre-merge) cluster rows from the database, per brand.

    READ-ONLY: Only SELECT queries. Does NO derivation — that lives in
    derive_cluster_payload so the pair-features path (Phase 5) can enrich a large
    candidate pool cheaply and derive only the selected products.

    Returns:
        Dict mapping (brand, instance_id) -> list of raw cluster dicts.
    """
    brand_instances = defaultdict(list)
    for _, row in sample_df.iterrows():
        brand_instances[row['brand']].append(row['instance_id'])

    raw_by_key = {}
    for brand, instance_ids in brand_instances.items():
        if not instance_ids:
            continue
        print(f"  Fetching clusters for {brand}: {len(instance_ids)} instances...")
        raw_clusters = fetch_cluster_data_batch(brand, instance_ids)
        for instance_id, clusters in raw_clusters.items():
            raw_by_key[(brand, instance_id)] = clusters

    return raw_by_key


def derive_cluster_payload(clusters: list) -> dict:
    """
    Pure derivation from one product's raw cluster list. No DB access.

    Returns {'raw_clusters', 'merged_clusters', 'algo_merge_groups'}, where
    algo_merge_groups is the algorithm's grouping of the raw clusters (one dense
    id per raw cluster, index-aligned to raw_clusters).

    ORDER IS LOAD-BEARING: compute_merge_group_indices must run BEFORE
    merge_clusters_hue_based, which mutates the cluster dicts.
    """
    # Add hex colors, HSB saturation, and chroma to raw clusters
    raw_with_meta = []
    for c in clusters:
        c_copy = dict(c)
        c_copy['hex'] = lab_to_hex(c['lab_l'], c['lab_a'], c['lab_b'])
        c_copy['chroma'] = get_chroma(c['lab_a'], c['lab_b'])
        c_copy['hue'] = get_hue_angle(c['lab_a'], c['lab_b'])
        c_copy['sat_type'] = classify_saturation_lab(c_copy['chroma'])
        # HSB saturation metadata
        hsb_sat = compute_hsb_sat_single(c['lab_l'], c['lab_a'], c['lab_b'])
        c_copy['hsb_sat'] = round(hsb_sat, 1)
        c_copy['hsb_class'] = classify_hsb_single(hsb_sat)
        raw_with_meta.append(c_copy)

    # Algorithm's merge grouping — BEFORE merge mutates the list
    algo_merge_groups = compute_merge_group_indices(clusters)
    assert len(algo_merge_groups) == len(raw_with_meta), (
        "algo_merge_groups must be index-aligned to raw_clusters"
    )

    # Compute merged clusters (adds hex internally via lab_to_hex)
    merged = merge_clusters_hue_based(clusters)
    significant = filter_significant_clusters(merged)

    # Add hex and HSB metadata to merged clusters
    for mc in merged:
        mc['hex'] = lab_to_hex(mc['lab_l'], mc['lab_a'], mc['lab_b'])
        hsb_sat = compute_hsb_sat_single(mc['lab_l'], mc['lab_a'], mc['lab_b'])
        mc['hsb_sat'] = round(hsb_sat, 1)
        mc['hsb_class'] = classify_hsb_single(hsb_sat)

    for sc in significant:
        if 'hex' not in sc:
            sc['hex'] = lab_to_hex(sc['lab_l'], sc['lab_a'], sc['lab_b'])
        if 'hsb_sat' not in sc:
            hsb_sat = compute_hsb_sat_single(sc['lab_l'], sc['lab_a'], sc['lab_b'])
            sc['hsb_sat'] = round(hsb_sat, 1)
            sc['hsb_class'] = classify_hsb_single(hsb_sat)

    return {
        'raw_clusters': raw_with_meta,
        'merged_clusters': significant,
        'algo_merge_groups': algo_merge_groups,
    }


def enrich_with_cluster_data(sample_df: pd.DataFrame) -> dict:
    """
    Fetch raw cluster data from the database and derive the per-product payload
    (raw + merged clusters + algo_merge_groups) with HSB metadata.

    READ-ONLY: Only SELECT queries. Thin wrapper over fetch_raw_clusters +
    derive_cluster_payload; preserves the original external behavior.

    Returns:
        Dict mapping (brand, instance_id) to cluster payload.
    """
    raw_by_key = fetch_raw_clusters(sample_df)
    return {key: derive_cluster_payload(clusters)
            for key, clusters in raw_by_key.items()}


def enrich_with_image_paths(sample_df: pd.DataFrame, images_dir: str, host_images_dir: str = None) -> dict:
    """
    Resolve image paths for sampled products.

    READ-ONLY: Only reads filesystem to find paths.

    Returns:
        Dict mapping (brand, instance_id) to image paths
    """
    # Group by brand for efficient queries
    brand_instances = defaultdict(list)
    for _, row in sample_df.iterrows():
        brand_instances[row['brand']].append(row['instance_id'])

    image_info = {}
    archive_dirs_cache = {}
    seg_folder_cache = {}

    for brand, instance_ids in brand_instances.items():
        if not instance_ids:
            continue

        print(f"  Resolving image paths for {brand}: {len(instance_ids)} instances...")

        # Fetch metadata for path construction
        metadata = fetch_image_metadata_batch(brand, instance_ids)

        if brand not in archive_dirs_cache:
            archive_dirs_cache[brand] = map_archive_dirs(brand, images_dir)
            seg_folder_cache[brand] = {}

        archive_dirs = archive_dirs_cache[brand]

        for instance_id in instance_ids:
            meta = metadata.get(instance_id)
            if not meta:
                image_info[(brand, instance_id)] = {
                    'image_path': None,
                    'segmented_path': None,
                    'title': 'Unknown Product',
                }
                continue

            image_path = resolve_image_path(
                archive_dirs, meta['archive_id'], instance_id,
                meta['family_id'], meta['rank'], meta['pid']
            )

            seg_path = resolve_segmented_image_path(
                archive_dirs, meta['archive_id'], instance_id,
                meta['family_id'], meta['rank'], meta['pid'],
                seg_folder_cache[brand]
            )

            # Convert to host paths if needed (for Docker)
            def to_display_path(path):
                if path is None:
                    return None
                if host_images_dir and images_dir:
                    return path.replace(images_dir, host_images_dir)
                return os.path.abspath(path)

            image_info[(brand, instance_id)] = {
                'image_path': to_display_path(image_path),
                'segmented_path': to_display_path(seg_path),
                'title': meta.get('title', 'Unknown Product'),
            }

    return image_info


# ============================================================================
# JSON EXPORT
# ============================================================================

def compute_sample_fingerprint(strata_mode: str, random_state: int,
                               products: list) -> str:
    """
    Stable 12-hex fingerprint of a sample's identity: strata mode + seed + the
    exact set of (brand, instance_id) it contains. The validator embeds this so a
    resampled JSON opens a fresh label namespace instead of silently re-attaching
    old hand-labels to different products.
    """
    ids = sorted(f"{p['brand']}:{p['instance_id']}" for p in products)
    payload = f"{strata_mode}|{random_state}|" + ",".join(ids)
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]


ARCHETYPE_SAMPLING_NOTE = (
    "Archetype-stratified. Accuracy figures computed from this sample are "
    "real-world estimates (comparable to the historical 94.7% figure)."
)


def build_sample_json(sample_df: pd.DataFrame, cluster_info: dict, image_info: dict,
                      archetype_targets: dict, random_state: int,
                      strata_mode: str = 'archetype',
                      sampling_note: str = ARCHETYPE_SAMPLING_NOTE,
                      extra_metadata: dict = None) -> dict:
    """
    Build the complete JSON structure for the validation sample.

    strata_mode / sampling_note travel into metadata AND (downstream) into every
    exported label row, so a boundary-refinement sample can never be silently
    treated as a real-world accuracy estimate.
    """
    products = []

    for idx, row in sample_df.iterrows():
        brand = row['brand']
        instance_id = row['instance_id']
        key = (brand, instance_id)

        clusters = cluster_info.get(key, {})
        images = image_info.get(key, {})

        product = {
            'sample_id': idx + 1,
            'instance_id': int(instance_id),
            'brand': brand,
            'brand_label': BRAND_LABELS.get(brand, brand.title()),
            'gender': row.get('gender', ''),
            'archetype': row['archetype'],
            'archetype_name': ARCHETYPE_NAMES.get(row['archetype'], row['archetype']),
            'n_colors': int(row.get('n_colors', 0)),
            'has_saturated': bool(row.get('has_saturated', False)),
            'coverage_1': float(row.get('coverage_1', 0)),
            'title': images.get('title', 'Unknown Product'),
            'image_path': images.get('image_path'),
            'segmented_path': images.get('segmented_path'),
            'raw_clusters': clusters.get('raw_clusters', []),
            'merged_clusters': clusters.get('merged_clusters', []),
            'algo_merge_groups': clusters.get('algo_merge_groups', []),
        }
        if 'pair_stratum' in row:
            product['pair_stratum'] = row['pair_stratum']

        products.append(product)

    # Summary stats
    archetype_counts = sample_df['archetype'].value_counts().to_dict()
    brand_counts = sample_df['brand'].value_counts().to_dict()
    sat_counts = sample_df['has_saturated'].value_counts().to_dict()

    metadata = {
        'generated_at': datetime.now().isoformat(),
        'source_csv': 'all_products_classified_hsb.csv',
        'strata_mode': strata_mode,
        'sampling_note': sampling_note,
        'sample_fingerprint': compute_sample_fingerprint(strata_mode, random_state, products),
        'merge_thresholds': DEFAULT_THRESHOLDS._asdict(),
        'total_sampled': len(products),
        'random_seed': random_state,
        'archetype_targets': archetype_targets,
        'archetype_counts': archetype_counts,
        'brand_counts': brand_counts,
        'saturation_counts': {
            'has_saturated': int(sat_counts.get(True, 0)),
            'no_saturated': int(sat_counts.get(False, 0)),
        },
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    return {'metadata': metadata, 'products': products}


def export_sample_json(data: dict, output_path: str):
    """Write sample data to JSON file."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"  Saved to: {output_path}")


def sample_filename(strata: str = 'archetype', brand: str = None) -> str:
    """Canonical JSON filename for a (strata, brand) sample.

    Distinct filenames per strata mode are a structural guarantee that a
    pair-features (boundary-refinement) sample can never overwrite the
    archetype sample.
    """
    base = 'validation_sample_pairfeatures' if strata == 'pair-features' else 'validation_sample'
    if brand:
        base += f'_{brand}'
    return base + '.json'


# ============================================================================
# ORCHESTRATION (callable API; main() is a thin shim over this)
# ============================================================================

def build_validation_sample(input_csv=DEFAULT_INPUT_CSV, output_dir=DEFAULT_OUTPUT_DIR,
                            images_dir=DEFAULT_IMAGES_DIR, host_images_dir=None,
                            random_seed=42, archetype_targets=None,
                            brand=None, strata='archetype',
                            pair_strata_targets=None, candidate_pool=5000,
                            boundary_band=None) -> str:
    """
    Build a validation sample and write it to <output_dir>/<sample_filename>.
    Returns the output path.

    strata='archetype' (default): archetype-stratified, brand-balanced — the
        real-world sample used for classifier validation and routine merge tuning.
    strata='pair-features': boundary-oversampled for merge-rule tuning (Phase 5);
        ADDITIVE ONLY — never comparable to a real-world accuracy estimate.
    brand=None: global all-brands sample; a brand string scopes to that brand.
    """
    archetype_targets = archetype_targets or dict(DEFAULT_ARCHETYPE_TARGETS)

    # Auto-detect Docker
    if host_images_dir is None and images_dir.startswith('/app/'):
        host_images_dir = os.environ.get("IMAGE_DIR", r'D:\D01_Code_Forge\01_Codebase\Codebase_ColorAnalytics\03_images')
        print("Docker detected - mapping paths to Windows host")

    if not os.path.exists(input_csv):
        raise FileNotFoundError(
            f"Input CSV not found: {input_csv}. Run archetypes first "
            "(python cli.py archetypes)."
        )

    print(f"\n[1/4] Loading classified products...")
    df = pd.read_csv(input_csv)
    if brand:
        df = df[df['brand'] == brand].copy()
        print(f"  Scoped to brand '{brand}': {len(df):,} products")
    print(f"  Loaded {len(df):,} products")

    if strata == 'pair-features':
        return _build_pair_features_sample(
            df, output_dir, images_dir, host_images_dir, random_seed, brand,
            pair_strata_targets, candidate_pool, boundary_band)

    # ---- archetype strata (default) ----
    total_target = sum(archetype_targets.values())
    print(f"\n[2/4] Stratified sampling (archetype-balanced, target {total_target})...")
    sample_df = stratified_sample(df, archetype_targets, random_seed)
    print(f"  Total sampled: {len(sample_df)}")
    if len(sample_df) == 0:
        raise ValueError("No products sampled. Check archetype names in CSV.")

    print(f"\n[3/4] Enriching with cluster and image data (READ-ONLY)...")
    cluster_info = enrich_with_cluster_data(sample_df)
    image_info = enrich_with_image_paths(sample_df, images_dir, host_images_dir)

    print(f"\n[4/4] Exporting to JSON...")
    sample_data = build_sample_json(
        sample_df, cluster_info, image_info, archetype_targets, random_seed,
        strata_mode='archetype', sampling_note=ARCHETYPE_SAMPLING_NOTE)

    output_path = os.path.join(output_dir, sample_filename('archetype', brand))
    export_sample_json(sample_data, output_path)

    meta = sample_data['metadata']
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"  Strata mode:  {meta['strata_mode']}")
    print(f"  Fingerprint:  {meta['sample_fingerprint']}")
    print(f"  Total sampled: {meta['total_sampled']}")
    print(f"  Archetype distribution:")
    for arch, count in meta['archetype_counts'].items():
        print(f"    {arch}: {count}")
    print(f"  Brand distribution:")
    for b, count in meta['brand_counts'].items():
        print(f"    {BRAND_LABELS.get(b, b)}: {count}")
    print(f"\n  Output: {output_path}")
    print("=" * 70)

    return output_path


# ============================================================================
# MAIN
# ============================================================================

# ============================================================================
# PAIR-FEATURES STRATIFICATION (ADDITIVE — boundary-refinement, NOT real-world)
# ============================================================================

DEFAULT_PAIR_STRATA_TARGETS = {
    'chroma_boundary': 60,   # near a chroma threshold: flips which merge RULE applies
    'hue_boundary': 80,      # both chromatic, hue_diff near HUE_MERGE_THRESHOLD
    'dl_boundary': 60,       # low-chroma/neutral, ΔL near the guard
    'clear': 40,             # unambiguous pairs (reference)
}
# Priority for assigning a product to the most informative stratum among its pairs.
_STRATUM_PRIORITY = ['chroma_boundary', 'hue_boundary', 'dl_boundary', 'clear']

PAIR_FEATURES_SAMPLING_NOTE = (
    "BOUNDARY-REFINEMENT SAMPLE - deliberately oversamples ambiguous color pairs "
    "near the merge decision boundary. Any accuracy computed on it is NOT a "
    "real-world estimate and is not comparable to the archetype-stratified 94.7% "
    "figure. Use only to compare candidate merge thresholds against each other."
)


def _classify_pair_stratum(c1, c2, band):
    """Classify one cluster pair into the boundary stratum it most informs.

    band applies to hue-degrees and ΔL; a tighter chroma band (band/2, min 2) is
    used because chroma thresholds are lower-magnitude.
    """
    chroma_band = max(2.0, band / 2.0)
    ch1 = get_chroma(c1['lab_a'], c1['lab_b'])
    ch2 = get_chroma(c2['lab_a'], c2['lab_b'])
    ch_min, ch_max = min(ch1, ch2), max(ch1, ch2)

    # chroma_boundary: either chroma sits near a threshold that decides the rule
    for ch in (ch_min, ch_max):
        if (abs(ch - LOW_CHROMA_MERGE_THRESHOLD) <= chroma_band or
                abs(ch - NEUTRAL_CHROMA_THRESHOLD) <= chroma_band):
            return 'chroma_boundary'

    h1 = get_hue_angle(c1['lab_a'], c1['lab_b'])
    h2 = get_hue_angle(c2['lab_a'], c2['lab_b'])
    both_chromatic = h1 is not None and h2 is not None
    both_low = ch1 < LOW_CHROMA_MERGE_THRESHOLD and ch2 < LOW_CHROMA_MERGE_THRESHOLD
    both_neutral = h1 is None and h2 is None

    if both_chromatic:
        hd = hue_difference(h1, h2)
        if hd is not None and abs(hd - HUE_MERGE_THRESHOLD) <= band:
            return 'hue_boundary'

    if both_low or both_neutral:
        if abs(abs(c1['lab_l'] - c2['lab_l']) - LOW_CHROMA_MAX_DELTA_L) <= band:
            return 'dl_boundary'

    return 'clear'


def _product_stratum(clusters, band):
    """Highest-priority boundary stratum among a product's cluster pairs."""
    from itertools import combinations
    best = 'clear'
    best_rank = _STRATUM_PRIORITY.index('clear')
    for i, j in combinations(range(len(clusters)), 2):
        s = _classify_pair_stratum(clusters[i], clusters[j], band)
        rank = _STRATUM_PRIORITY.index(s)
        if rank < best_rank:
            best, best_rank = s, rank
            if best_rank == 0:
                break
    return best


def _build_pair_features_sample(df, output_dir, images_dir, host_images_dir,
                                random_seed, brand, pair_strata_targets,
                                candidate_pool, boundary_band) -> str:
    """Boundary-oversampled sample for merge-rule tuning (ADDITIVE ONLY).

    Flow (reordered vs archetype): draw a brand-balanced candidate pool -> fetch
    raw clusters (DB) -> assign each product a pair-stratum -> sample per stratum
    -> derive payload + image paths for the SELECTED products only.
    """
    targets = pair_strata_targets or dict(DEFAULT_PAIR_STRATA_TARGETS)
    band = boundary_band if boundary_band is not None else 8.0

    print("\n  ** pair-features (boundary-refinement) sampling **")
    print(f"     band={band}  targets={targets}")

    # 1. Candidate pool (brand-balanced, seeded)
    from archetype_taxonomy.classify_archetypes import BRANDS
    if brand:
        pool_df = df.sample(n=min(candidate_pool, len(df)), random_state=random_seed)
    else:
        per_brand = max(1, candidate_pool // len(BRANDS))
        parts = []
        for b in BRANDS:
            bdf = df[df['brand'] == b]
            if len(bdf):
                parts.append(bdf.sample(n=min(per_brand, len(bdf)), random_state=random_seed))
        pool_df = pd.concat(parts, ignore_index=True) if parts else df.iloc[0:0]
    print(f"  [2/4] Candidate pool: {len(pool_df)} products; fetching clusters...")

    # 2. Fetch raw clusters for the pool (DB, no image paths yet)
    raw_by_key = fetch_raw_clusters(pool_df)

    # 3. Assign each pooled product its pair-stratum
    stratum_of = {}
    for key, clusters in raw_by_key.items():
        if len(clusters) < 2:
            continue  # a single color has no pair to inform the merge rule
        stratum_of[key] = _product_stratum(clusters, band)

    by_stratum = defaultdict(list)
    for key, s in stratum_of.items():
        by_stratum[s].append(key)

    # 4. Sample per stratum to targets (seeded, deterministic order)
    import random as _random
    rng = _random.Random(random_seed)
    selected_keys = []
    print(f"  [3/4] Stratum coverage:")
    stratum_counts = {}
    for s in _STRATUM_PRIORITY:
        pool_keys = sorted(by_stratum.get(s, []))  # sorted for determinism
        want = targets.get(s, 0)
        take = min(want, len(pool_keys))
        rng.shuffle(pool_keys)
        chosen = pool_keys[:take]
        selected_keys.extend(chosen)
        stratum_counts[s] = take
        print(f"       {s:>16}: {take:>4} selected (of {len(pool_keys)} available, target {want})")

    if not selected_keys:
        raise ValueError("pair-features: no products selected (empty pool or no pairs).")

    # 5. Build a sample_df for the selected keys; derive payload + images (selected only)
    sel_set = set(selected_keys)
    sel_rows = pool_df[pool_df.apply(
        lambda r: (r['brand'], r['instance_id']) in sel_set, axis=1)].copy()
    sel_rows['pair_stratum'] = sel_rows.apply(
        lambda r: stratum_of.get((r['brand'], r['instance_id']), 'clear'), axis=1)
    sel_rows = sel_rows.sample(frac=1, random_state=random_seed).reset_index(drop=True)

    print(f"  [4/4] Deriving payload + image paths for {len(sel_rows)} selected products...")
    cluster_info = {key: derive_cluster_payload(raw_by_key[key]) for key in selected_keys}
    image_info = enrich_with_image_paths(sel_rows, images_dir, host_images_dir)

    sample_data = build_sample_json(
        sel_rows, cluster_info, image_info,
        archetype_targets={}, random_state=random_seed,
        strata_mode='pair-features', sampling_note=PAIR_FEATURES_SAMPLING_NOTE,
        extra_metadata={'pair_strata_targets': targets,
                        'boundary_band': band,
                        'stratum_counts': stratum_counts})

    output_path = os.path.join(output_dir, sample_filename('pair-features', brand))
    export_sample_json(sample_data, output_path)

    meta = sample_data['metadata']
    print("\n" + "=" * 70)
    print("Summary (pair-features / BOUNDARY-REFINEMENT)")
    print("=" * 70)
    print(f"  Fingerprint:  {meta['sample_fingerprint']}")
    print(f"  Total:        {meta['total_sampled']}")
    print(f"  {PAIR_FEATURES_SAMPLING_NOTE}")
    print(f"\n  Output: {output_path}")
    print("=" * 70)
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Generate stratified validation sample balanced by archetype and brand (READ-ONLY)"
    )
    parser.add_argument('--input-csv', default=DEFAULT_INPUT_CSV,
                        help="Path to classified products CSV")
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_DIR,
                        help="Output directory for JSON file")
    parser.add_argument('--images-dir', default=DEFAULT_IMAGES_DIR,
                        help="Base directory containing product images")
    parser.add_argument('--host-images-dir', default=None,
                        help="Windows host path to images (for Docker)")
    parser.add_argument('--random-seed', type=int, default=42,
                        help="Random seed for reproducibility")

    # Sample size overrides
    parser.add_argument('--mono', type=int, default=DEFAULT_ARCHETYPE_TARGETS['MONO'],
                        help="Sample size for MONO archetype")
    parser.add_argument('--dom-acc', type=int, default=DEFAULT_ARCHETYPE_TARGETS['DOM_ACC'],
                        help="Sample size for DOM_ACC archetype")
    parser.add_argument('--dual-bal', type=int, default=DEFAULT_ARCHETYPE_TARGETS['DUAL_BAL'],
                        help="Sample size for DUAL_BAL archetype")
    parser.add_argument('--multi-dom', type=int, default=DEFAULT_ARCHETYPE_TARGETS['MULTI_DOM'],
                        help="Sample size for MULTI_DOM archetype")
    parser.add_argument('--multi-bal', type=int, default=DEFAULT_ARCHETYPE_TARGETS['MULTI_BAL'],
                        help="Sample size for MULTI_BAL archetype")

    args, _ = parser.parse_known_args()

    archetype_targets = {
        'MONO': args.mono,
        'DOM_ACC': args.dom_acc,
        'DUAL_BAL': args.dual_bal,
        'MULTI_DOM': args.multi_dom,
        'MULTI_BAL': args.multi_bal,
    }

    print("=" * 70)
    print("Validation Sample Generator (Production, HSB)")
    print("=" * 70)
    print("\nThis script is READ-ONLY:")
    print("  - Database: SELECT queries only")
    print("  - Images: Read-only path resolution")
    print("  - CSV: Read-only access")
    print("  - Output: Creates NEW JSON file")

    try:
        build_validation_sample(
            input_csv=args.input_csv,
            output_dir=args.output_dir,
            images_dir=args.images_dir,
            host_images_dir=args.host_images_dir,
            random_seed=args.random_seed,
            archetype_targets=archetype_targets,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"\nERROR: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
