"""
ENTRY - run: python cli.py review-cluster-quality  (interactive click-to-flag cluster review).

CIEDE2000 Cluster Review Tool
==============================

Interactive HTML tool for manually reviewing CIEDE2000 agglomerative clusters.
Click product cards to flag products that don't belong in their cluster. Export
flagged IDs as JSON. A companion --analyze mode examines where in LAB space
the pollution occurs and its statistical impact.

This script is READ-ONLY:
- Database: Only SELECT queries (for image metadata)
- Images: Only reads image files (no writes)
- Output: Creates NEW files in outputs/diagnostic_cluster_review/

Two modes:
  Generate (default): Build interactive HTML review tool
  Analyze (--analyze): Process exported flagged products JSON

Usage:
    python cli.py review-cluster-quality nike --gender mens
    python review_cluster_quality.py nike --gender mens --analyze path/to/flagged.json
"""

import os
import sys
import json
import glob as glob_module
import base64
import time
import numpy as np
import pandas as pd

from shared.db import connect_to_db
from shared.hsb import lab_to_rgb_array
from shared.ciede2000 import ciede2000_pairwise, detect_gpu, _ciede2000_tile

# =============================================================================
# CONFIGURATION
# =============================================================================

BRAND_DEFAULT = 'nike'
GENDER_DEFAULT = 'mens'
MAX_PRODUCTS_PER_CLUSTER = 20
THUMBNAIL_WIDTH = 400
THUMBNAIL_QUALITY = 80

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_SCRIPT_DIR)
_CODEBASE_DIR = os.path.dirname(os.path.dirname(_PIPELINE_DIR))

# cluster data lives in the slice's outputs/<brand>_<gender>/ (one level up from
# quality/). consolidate-colors writes one subfolder per brand+gender run, so the
# reviewer must target the matching one — the path is built per-invocation from the
# brand+gender args, not fixed at import time.
INPUT_BASE = os.path.join(os.path.dirname(_SCRIPT_DIR), 'outputs')


def _segment_input_dir(brand, gender):
    """Path to the cluster CSVs for one brand+gender run (outputs/<brand>_<gender>/)."""
    return os.path.join(INPUT_BASE, f'{brand}_{gender}')
# review artifacts are scoped per brand+gender run so reviewing one segment does
# not overwrite another's HTML/CSVs — outputs/diagnostic_cluster_review/<brand>_<gender>/.
OUTPUT_BASE = os.path.join(_SCRIPT_DIR, 'outputs', 'diagnostic_cluster_review')


def _segment_output_dir(brand, gender):
    """Path where this brand+gender run's review artifacts are written."""
    return os.path.join(OUTPUT_BASE, f'{brand}_{gender}')

_IMAGES_CANDIDATES = [
    os.path.join(_CODEBASE_DIR, '03_images'),
    r'D:\D01_Code_Forge\01_Codebase\Codebase_ColorAnalytics\03_images',
    os.path.join(_PIPELINE_DIR, 'images'),
]
if os.environ.get("IMAGE_DIR"):
    _IMAGES_CANDIDATES.insert(0, os.environ["IMAGE_DIR"])
IMAGES_DIR = next(
    (p for p in _IMAGES_CANDIDATES if os.path.exists(p)),
    _IMAGES_CANDIDATES[1],
)

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

# GPU state (set once in main, used by _farthest_point_sample)
_USE_GPU = False


# =============================================================================
# DATA LOADING
# =============================================================================

def load_raw_data(input_dir):
    """Load CSV outputs from consolidate-colors."""
    assignments_path = os.path.join(input_dir, 'product_assignments.csv')
    centroids_path = os.path.join(input_dir, 'cluster_centroids.csv')
    summary_path = os.path.join(input_dir, 'cluster_summary.csv')

    for path, name in [(assignments_path, 'product_assignments.csv'),
                       (centroids_path, 'cluster_centroids.csv'),
                       (summary_path, 'cluster_summary.csv')]:
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{name} not found in {input_dir}. "
                "Run consolidate-colors first."
            )

    assignments = pd.read_csv(assignments_path)
    centroids = pd.read_csv(centroids_path)
    summary = pd.read_csv(summary_path)

    for col in ['lab_l', 'lab_a', 'lab_b']:
        assignments[col] = assignments[col].astype(float)
    assignments['is_discounted'] = assignments['is_discounted'].astype(bool)

    return assignments, centroids, summary


# =============================================================================
# IMAGE HELPERS (replicated from gallery per self-containment convention)
# =============================================================================

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
                if parts[0].lower() == brand_lower:
                    archive_dir_map[parts[2]] = folder_path
    return archive_dir_map


def resolve_image_path(archive_dirs, archive_id, instance_id, family_id, rank, pid):
    """Construct path to original JPG image. READ-ONLY."""
    archive_key = str(archive_id)
    if archive_key not in archive_dirs:
        return None
    filename = f"{archive_id}-{instance_id}-{family_id}-{rank}-{pid}.jpg"
    full_path = os.path.join(archive_dirs[archive_key], filename)
    return full_path if os.path.exists(full_path) else None


def find_segmented_subfolder(archive_folder):
    """Find fpyolo11l241114_* subfolder. READ-ONLY."""
    if not os.path.exists(archive_folder):
        return None
    for item in os.listdir(archive_folder):
        if item.startswith('fpyolo11l241114_') and os.path.isdir(
                os.path.join(archive_folder, item)):
            return os.path.join(archive_folder, item)
    return None


def resolve_segmented_image_path(archive_dirs, archive_id, instance_id,
                                 family_id, rank, pid, seg_folder_cache=None):
    """Find segmented PNG image. READ-ONLY."""
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
    return matches[0] if matches else None


def image_to_base64_thumbnail(image_path, is_segmented=False):
    """Read image, resize to thumbnail, return base64 data URI.

    Uses PIL for initial read to handle CMYK and ICC profiles correctly,
    then cv2 for resizing and encoding.
    """
    import cv2
    from PIL import Image

    if is_segmented:
        img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
        if img is None:
            return None
    else:
        try:
            pil_img = Image.open(image_path)
            pil_img = pil_img.convert('RGB')
            img = np.array(pil_img)[:, :, ::-1]  # RGB to BGR for cv2
        except Exception:
            return None

    h, w = img.shape[:2]
    scale = THUMBNAIL_WIDTH / w
    img = cv2.resize(img, (THUMBNAIL_WIDTH, int(h * scale)),
                     interpolation=cv2.INTER_AREA)
    if is_segmented and img.shape[2] == 4:
        success, buf = cv2.imencode('.png', img)
        mime = 'image/png'
    else:
        if len(img.shape) == 3 and img.shape[2] == 4:
            img = img[:, :, :3]
        success, buf = cv2.imencode('.jpg', img,
                                     [cv2.IMWRITE_JPEG_QUALITY, THUMBNAIL_QUALITY])
        mime = 'image/jpeg'
    if not success:
        return None
    b64 = base64.b64encode(buf).decode('ascii')
    return f'data:{mime};base64,{b64}'


def fetch_image_metadata(instance_ids, brand):
    """Fetch pid, family_id, rank, title for sampled products."""
    metadata = {}
    if not instance_ids:
        return metadata
    conn, cur = connect_to_db(brand)
    try:
        cur.execute(IMAGE_METADATA_QUERY, (tuple(instance_ids),))
        rows = cur.fetchall()
        for row in rows:
            metadata[row['instance_id']] = {
                'archive_id': row['archive_id_ref'],
                'family_id': row['family_id_ref'],
                'rank': row['family_rank'],
                'pid': row['pid'],
                'title': row['title'] or 'Untitled',
            }
    finally:
        conn.close()
    return metadata


def resolve_all_images(metadata, brand):
    """Resolve image paths and generate base64 thumbnails."""
    archive_dirs = map_archive_dirs(brand, IMAGES_DIR)
    seg_folder_cache = {}
    image_data = {}
    total = len(metadata)
    found = missing = 0

    for idx, (instance_id, meta) in enumerate(metadata.items()):
        orig_path = resolve_image_path(
            archive_dirs, meta['archive_id'], instance_id,
            meta['family_id'], meta['rank'], meta['pid'])
        seg_path = resolve_segmented_image_path(
            archive_dirs, meta['archive_id'], instance_id,
            meta['family_id'], meta['rank'], meta['pid'], seg_folder_cache)

        orig_b64 = seg_b64 = None
        if orig_path and os.path.exists(orig_path):
            orig_b64 = image_to_base64_thumbnail(orig_path, is_segmented=False)
            found += 1
        else:
            missing += 1
        if seg_path and os.path.exists(seg_path):
            seg_b64 = image_to_base64_thumbnail(seg_path, is_segmented=True)

        image_data[instance_id] = {
            'orig_b64': orig_b64, 'seg_b64': seg_b64, 'title': meta['title'],
        }
        if (idx + 1) % 100 == 0 or idx + 1 == total:
            print(f"      {idx + 1}/{total} images processed")

    print(f"    Images found: {found}, missing: {missing}")
    return image_data


# =============================================================================
# FARTHEST-POINT SAMPLING (replicated from gallery)
# =============================================================================

def _farthest_point_sample(lab_array, max_samples, centroid_lab):
    """Greedy farthest-point sampling for maximum diversity."""
    from scipy.spatial.distance import squareform

    n = len(lab_array)
    if n <= max_samples:
        return np.arange(n)

    condensed = ciede2000_pairwise(lab_array, use_gpu=_USE_GPU, verbose=False)
    dist_matrix = squareform(condensed)
    del condensed

    diffs = lab_array - centroid_lab[np.newaxis, :]
    centroid_dists = np.sqrt(np.sum(diffs ** 2, axis=1))
    first_idx = np.argmin(centroid_dists)

    selected = [first_idx]
    min_dist_to_selected = dist_matrix[first_idx].copy()

    for _ in range(max_samples - 1):
        min_dist_to_selected[selected] = -1
        next_idx = np.argmax(min_dist_to_selected)
        selected.append(next_idx)
        np.minimum(min_dist_to_selected, dist_matrix[next_idx],
                   out=min_dist_to_selected)

    return np.array(selected)


def sample_products(assignments_df, centroids_df, max_per_cluster):
    """Sample diverse products per cluster using farthest-point CIEDE2000."""
    centroid_lookup = {}
    for _, row in centroids_df.iterrows():
        centroid_lookup[int(row['cluster_id'])] = np.array([
            row['lab_l'], row['lab_a'], row['lab_b']])

    sampled = []
    for cluster_id, group in assignments_df.groupby('cluster_id'):
        n = len(group)
        if n == 0:
            continue
        if n <= max_per_cluster:
            sampled.append(group.copy())
            continue
        if n > 500:
            print(f"      Cluster {int(cluster_id)} ({n:,} products)...")
        lab_array = group[['lab_l', 'lab_a', 'lab_b']].values.astype(np.float64)
        centroid_lab = centroid_lookup.get(int(cluster_id), lab_array.mean(axis=0))
        indices = _farthest_point_sample(lab_array, max_per_cluster, centroid_lab)
        sampled.append(group.iloc[indices].copy())

    if not sampled:
        return pd.DataFrame()
    return pd.concat(sampled, ignore_index=True)


# =============================================================================
# RANDOM SAMPLING + SAMPLE SIZE COMPUTATION (swatch mode)
# =============================================================================

def compute_sample_sizes(centroids_df, confidence=0.95, margin=0.10):
    """Compute required sample size per cluster for proportion estimation.

    Uses finite population correction:
        n = (Z^2 * p * (1-p) * N) / (e^2 * (N-1) + Z^2 * p * (1-p))

    Args:
        centroids_df: DataFrame with cluster_id and n_products columns
        confidence: confidence level (default 0.95)
        margin: margin of error as proportion (default 0.10 = +/-10pp)

    Returns:
        dict: cluster_id -> sample_size
    """
    from scipy.stats import norm
    Z = norm.ppf(1 - (1 - confidence) / 2)
    p = 0.5  # worst case (maximizes required n)

    sizes = {}
    for _, row in centroids_df.iterrows():
        N = int(row['n_products'])
        numerator = Z**2 * p * (1 - p) * N
        denominator = margin**2 * (N - 1) + Z**2 * p * (1 - p)
        n_needed = int(np.ceil(numerator / denominator))
        sizes[int(row['cluster_id'])] = min(n_needed, N)
    return sizes


def sample_products_random(assignments_df, sample_sizes):
    """Randomly sample products per cluster based on computed sample sizes.

    Args:
        assignments_df: DataFrame with cluster_id column
        sample_sizes: dict of cluster_id -> n_to_sample

    Returns:
        DataFrame of sampled products
    """
    sampled = []
    for cluster_id, group in assignments_df.groupby('cluster_id'):
        n = sample_sizes.get(int(cluster_id), 20)
        if len(group) <= n:
            sampled.append(group.copy())
        else:
            sampled.append(group.sample(n=n, random_state=42).copy())

    if not sampled:
        return pd.DataFrame()
    return pd.concat(sampled, ignore_index=True)


# =============================================================================
# CLUSTER ORDERING
# =============================================================================

def _order_centroids_by_ciede2000(centroids_df):
    """Order clusters by perceptual similarity using optimal leaf ordering."""
    if len(centroids_df) <= 2:
        return centroids_df.copy().reset_index(drop=True)
    from scipy.cluster.hierarchy import linkage, leaves_list
    lab = centroids_df[['lab_l', 'lab_a', 'lab_b']].values.astype(np.float64)
    condensed = ciede2000_pairwise(lab, use_gpu=False, verbose=False)
    Z = linkage(condensed, method='average', optimal_ordering=True)
    order = leaves_list(Z)
    return centroids_df.iloc[order].reset_index(drop=True)


# =============================================================================
# HTML GENERATION
# =============================================================================

def generate_review_html(sampled_df, centroids_df, summary_df, image_data,
                         metadata, brand):
    """Generate interactive HTML review tool with click-to-flag."""
    from datetime import datetime

    centroids_sorted = _order_centroids_by_ciede2000(centroids_df)
    cluster_ids = centroids_sorted['cluster_id'].tolist()

    summary_lookup = {}
    for _, row in summary_df.iterrows():
        summary_lookup[int(row['cluster_id'])] = row

    total_products = int(centroids_df['n_products'].sum())

    # Build product data for JS (needed for export)
    product_data_js = {}
    for _, product in sampled_df.iterrows():
        iid = int(product['instance_id'])
        product_data_js[iid] = {
            'instance_id': iid,
            'archive_id': int(product['archive_id']),
            'cluster_id': int(product['cluster_id']),
            'lab_l': round(float(product['lab_l']), 2),
            'lab_a': round(float(product['lab_a']), 2),
            'lab_b': round(float(product['lab_b']), 2),
        }

    html = []
    html.append(f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>CIEDE2000 Cluster Review Tool ({brand.title()})</title>
<style>
* {{ box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #1a1a1a; color: #e0e0e0; margin: 0; padding: 20px 30px;
}}
h1 {{ color: #F97316; margin-bottom: 5px; }}
.subtitle {{ color: #888; margin-bottom: 10px; font-size: 14px; }}

/* Sticky header */
.toolbar {{
    position: sticky; top: 0; z-index: 100;
    background: #1a1a1a; padding: 10px 0 10px; border-bottom: 1px solid #333;
    display: flex; align-items: center; gap: 15px; flex-wrap: wrap;
}}
.toolbar .flag-count {{
    font-size: 14px; font-weight: 600; color: #F97316;
}}
.toolbar button {{
    padding: 8px 16px; border: 2px solid #F97316; border-radius: 6px;
    background: transparent; color: #F97316; font-weight: 600; cursor: pointer;
    font-size: 13px;
}}
.toolbar button:hover {{ background: #F97316; color: #1a1a1a; }}
.toolbar .help {{ color: #555; font-size: 11px; }}

/* Tab navigation */
.tab-bar {{ display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 20px; }}
.tab-btn {{
    padding: 6px 12px; border: 2px solid #444; border-radius: 6px;
    cursor: pointer; font-size: 12px; font-weight: 600;
    transition: all 0.15s; background: #2a2a2a; color: #ccc;
    position: relative;
}}
.tab-btn:hover {{ border-color: #888; }}
.tab-btn.active {{ border-color: white; color: white; }}
.tab-swatch {{
    display: inline-block; width: 12px; height: 12px; border-radius: 3px;
    margin-right: 5px; vertical-align: middle; border: 1px solid rgba(255,255,255,0.3);
}}
.tab-badge {{
    position: absolute; top: -6px; right: -6px;
    background: #EF4444; color: white; font-size: 9px; font-weight: 700;
    padding: 1px 5px; border-radius: 10px; display: none;
}}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}

/* Cluster header */
.cluster-header {{
    display: flex; align-items: center; gap: 20px;
    margin-bottom: 15px; padding: 15px; background: #222; border-radius: 8px;
}}
.color-swatch-large {{
    width: 80px; height: 80px; border-radius: 8px;
    border: 2px solid rgba(255,255,255,0.2); flex-shrink: 0;
}}
.cluster-title {{ font-size: 22px; font-weight: 700; color: #fff; }}
.cluster-lab {{ font-size: 13px; color: #999; margin-top: 4px; font-family: monospace; }}

/* Image grid */
.image-grid {{
    display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px;
}}
@media (max-width: 1200px) {{ .image-grid {{ grid-template-columns: repeat(4, 1fr); }} }}
@media (max-width: 900px) {{ .image-grid {{ grid-template-columns: repeat(3, 1fr); }} }}

.product-card {{
    background: #252525; border-radius: 8px; overflow: hidden;
    cursor: pointer; transition: all 0.15s; border: 3px solid transparent;
    position: relative;
}}
.product-card:hover {{ transform: scale(1.02); }}
.product-card.flagged {{
    border-color: #EF4444;
}}
.product-card.flagged::after {{
    content: 'FLAGGED'; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(239, 68, 68, 0.15);
    display: flex; align-items: center; justify-content: center;
    color: #EF4444; font-weight: 700; font-size: 14px; pointer-events: none;
}}
.image-pair {{ display: flex; gap: 3px; height: 320px; }}
.img-container {{
    flex: 1; background: #333; display: flex; align-items: center;
    justify-content: center; overflow: hidden; position: relative;
}}
.img-container img {{ max-width: 100%; max-height: 100%; object-fit: contain; }}
.img-container.seg {{ background: #2a2a2a; }}
.img-label {{
    position: absolute; bottom: 2px; left: 3px;
    background: rgba(0,0,0,0.65); color: #aaa; font-size: 8px;
    padding: 1px 4px; border-radius: 2px;
}}
.card-info {{ padding: 8px; font-size: 11px; color: #aaa; line-height: 1.4; }}
.dominant-swatch {{
    display: inline-block; width: 14px; height: 14px; border-radius: 3px;
    border: 1px solid rgba(255,255,255,0.3); vertical-align: middle;
    margin-right: 4px;
}}
.discount-badge {{
    display: inline-block; padding: 1px 5px; border-radius: 3px;
    font-size: 9px; font-weight: 600; background: #EF4444; color: white;
}}
.price-info {{ font-family: monospace; font-size: 10px; }}
.price-curr {{ color: #fff; font-weight: 600; }}
.price-std {{ color: #666; text-decoration: line-through; margin-left: 4px; }}
.card-id {{ font-family: monospace; font-size: 9px; color: #555; margin-top: 2px; }}
.card-lab {{ font-family: monospace; font-size: 9px; color: #666; }}
.card-title {{ margin-top: 4px; font-size: 10px; color: #777;
    max-height: 2.6em; overflow: hidden; }}
.no-products {{ color: #666; font-style: italic; padding: 20px; }}

.footer {{
    text-align: center; color: #555; margin-top: 40px; padding-top: 15px;
    border-top: 1px solid #333; font-size: 11px;
}}
</style>
</head>
<body>

<h1>CIEDE2000 Cluster Review Tool</h1>
<p class="subtitle">
    {brand.title()} | {len(cluster_ids)} clusters |
    {total_products:,} total products |
    {len(sampled_df)} sampled for review |
    Click products that don't belong in their cluster
</p>

<div class="toolbar">
    <span class="flag-count" id="flag-counter">0 products flagged</span>
    <button onclick="exportFlags()">Export Flagged (JSON)</button>
    <button onclick="clearCurrentCluster()">Clear Current Cluster</button>
    <span class="help">Click card = toggle flag | &larr;&rarr; = navigate clusters | E = export</span>
</div>
''')

    # Tab bar
    html.append('<div class="tab-bar">')
    for i, cid in enumerate(cluster_ids):
        crow = centroids_sorted[centroids_sorted['cluster_id'] == cid].iloc[0]
        r = int(round(float(crow['r']) * 255))
        g = int(round(float(crow['g']) * 255))
        b = int(round(float(crow['b']) * 255))
        n = int(crow['n_products'])
        active = ' active' if i == 0 else ''
        html.append(
            f'<div class="tab-btn{active}" onclick="showTab({cid})" '
            f'id="tab-btn-{cid}" data-cluster="{cid}">'
            f'<span class="tab-swatch" style="background:rgb({r},{g},{b})"></span>'
            f'C{cid} ({n})'
            f'<span class="tab-badge" id="badge-{cid}"></span>'
            f'</div>'
        )
    html.append('</div>')

    # Tab content
    for i, cid in enumerate(cluster_ids):
        crow = centroids_sorted[centroids_sorted['cluster_id'] == cid].iloc[0]
        active = ' active' if i == 0 else ''

        r = int(round(float(crow['r']) * 255))
        g = int(round(float(crow['g']) * 255))
        b = int(round(float(crow['b']) * 255))
        n_products = int(crow['n_products'])
        lab_l, lab_a, lab_b = crow['lab_l'], crow['lab_a'], crow['lab_b']
        hex_color = crow['hex_color']
        hue_angle = crow['hue_angle']

        html.append(f'<div class="tab-content{active}" id="tab-{cid}">')

        # Header
        html.append(f'''<div class="cluster-header">
    <div class="color-swatch-large" style="background:rgb({r},{g},{b})"></div>
    <div>
        <div class="cluster-title">Cluster C{cid} &mdash; {n_products:,} products</div>
        <div class="cluster-lab">
            L*={lab_l:.1f} &nbsp; a*={lab_a:.1f} &nbsp; b*={lab_b:.1f}
            &nbsp;|&nbsp; RGB({r}, {g}, {b})
            &nbsp;|&nbsp; {hex_color}
            &nbsp;|&nbsp; Hue: {hue_angle:.1f}&deg;
        </div>
    </div>
</div>''')

        # Products
        products = sampled_df[sampled_df['cluster_id'] == cid]
        if len(products) == 0:
            html.append('<div class="no-products">No sampled products</div>')
            html.append('</div>')
            continue

        html.append('<div class="image-grid">')
        for _, product in products.iterrows():
            iid = int(product['instance_id'])
            aid = int(product['archive_id'])
            img = image_data.get(iid, {})
            orig_b64 = img.get('orig_b64')
            seg_b64 = img.get('seg_b64')
            title = img.get('title', '')
            if title and len(title) > 55:
                title = title[:52] + '...'

            is_disc = bool(product['is_discounted'])
            price_std = product.get('price_std')
            price_curr = product.get('price_curr')

            p_lab_l = float(product['lab_l'])
            p_lab_a = float(product['lab_a'])
            p_lab_b = float(product['lab_b'])
            p_r, p_g, p_b = lab_to_rgb_array(
                np.array([p_lab_l]), np.array([p_lab_a]), np.array([p_lab_b]))
            p_r8 = int(round(float(p_r[0]) * 255))
            p_g8 = int(round(float(p_g[0]) * 255))
            p_b8 = int(round(float(p_b[0]) * 255))
            perc = float(product['perc'])

            swatch_html = (
                f'<span class="dominant-swatch" '
                f'style="background:rgb({p_r8},{p_g8},{p_b8})" '
                f'title="L*={p_lab_l:.0f} a*={p_lab_a:.0f} '
                f'b*={p_lab_b:.0f} ({perc:.0f}%)"></span>')

            orig_html = (f'<img src="{orig_b64}" alt="Original" loading="lazy">'
                         if orig_b64 else
                         '<span style="color:#555;font-size:10px">No image</span>')
            seg_html = (f'<img src="{seg_b64}" alt="Segmented" loading="lazy">'
                        if seg_b64 else
                        '<span style="color:#555;font-size:10px">No seg</span>')

            disc_html = '<span class="discount-badge">SALE</span> ' if is_disc else ''
            price_html = ''
            if pd.notna(price_curr):
                price_html = f'<span class="price-curr">${float(price_curr):.0f}</span>'
                if is_disc and pd.notna(price_std):
                    price_html += f'<span class="price-std">${float(price_std):.0f}</span>'

            html.append(f'''<div class="product-card" data-iid="{iid}"
    data-cluster="{cid}" onclick="toggleFlag(this)">
    <div class="image-pair">
        <div class="img-container">{orig_html}<span class="img-label">Orig</span></div>
        <div class="img-container seg">{seg_html}<span class="img-label">Seg</span></div>
    </div>
    <div class="card-info">
        {swatch_html}{disc_html}<span class="price-info">{price_html}</span>
        <div class="card-id">i:{iid} a:{aid}</div>
        <div class="card-lab">L*={p_lab_l:.1f} a*={p_lab_a:.1f} b*={p_lab_b:.1f}</div>
        <div class="card-title">{title}</div>
    </div>
</div>''')

        html.append('</div>')  # image-grid
        html.append('</div>')  # tab-content

    # JavaScript
    html.append(f'''
<script>
const STORAGE_KEY = 'ciede2000_cluster_review_flags';
const PRODUCT_DATA = {json.dumps(product_data_js)};
const CLUSTER_IDS = {json.dumps(cluster_ids)};
let currentClusterIdx = 0;

// --- LocalStorage ---
function loadFlags() {{
    try {{
        const stored = localStorage.getItem(STORAGE_KEY);
        return stored ? JSON.parse(stored) : {{}};
    }} catch(e) {{ return {{}}; }}
}}
function saveFlags(flags) {{
    localStorage.setItem(STORAGE_KEY, JSON.stringify(flags));
}}

// --- Flag toggling ---
function toggleFlag(card) {{
    const iid = card.dataset.iid;
    const flags = loadFlags();
    if (flags[iid]) {{
        delete flags[iid];
        card.classList.remove('flagged');
    }} else {{
        flags[iid] = PRODUCT_DATA[iid] || {{ instance_id: parseInt(iid) }};
        card.classList.add('flagged');
    }}
    saveFlags(flags);
    updateUI();
}}

// --- UI updates ---
function updateUI() {{
    const flags = loadFlags();
    const total = Object.keys(flags).length;
    document.getElementById('flag-counter').textContent =
        total + ' product' + (total !== 1 ? 's' : '') + ' flagged';

    // Update per-cluster badges
    const clusterCounts = {{}};
    for (const [iid, data] of Object.entries(flags)) {{
        const cid = data.cluster_id;
        clusterCounts[cid] = (clusterCounts[cid] || 0) + 1;
    }}
    for (const cid of CLUSTER_IDS) {{
        const badge = document.getElementById('badge-' + cid);
        if (badge) {{
            const count = clusterCounts[cid] || 0;
            badge.textContent = count;
            badge.style.display = count > 0 ? 'inline-block' : 'none';
        }}
    }}
}}

// --- Restore flags on load ---
function restoreFlags() {{
    const flags = loadFlags();
    document.querySelectorAll('.product-card').forEach(card => {{
        if (flags[card.dataset.iid]) {{
            card.classList.add('flagged');
        }}
    }});
    updateUI();
}}

// --- Tab navigation ---
function showTab(clusterId) {{
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + clusterId).classList.add('active');
    document.getElementById('tab-btn-' + clusterId).classList.add('active');
    currentClusterIdx = CLUSTER_IDS.indexOf(clusterId);
}}

// --- Export ---
function exportFlags() {{
    const flags = loadFlags();
    const flagList = Object.values(flags);
    const exportData = {{
        exported_at: new Date().toISOString(),
        total_flagged: flagList.length,
        total_clusters: CLUSTER_IDS.length,
        flags: flagList
    }};
    const blob = new Blob([JSON.stringify(exportData, null, 2)],
                          {{ type: 'application/json' }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'flagged_products.json';
    a.click();
    URL.revokeObjectURL(url);
}}

// --- Clear current cluster ---
function clearCurrentCluster() {{
    const cid = CLUSTER_IDS[currentClusterIdx];
    const flags = loadFlags();
    const tab = document.getElementById('tab-' + cid);
    if (!tab) return;
    tab.querySelectorAll('.product-card.flagged').forEach(card => {{
        delete flags[card.dataset.iid];
        card.classList.remove('flagged');
    }});
    saveFlags(flags);
    updateUI();
}}

// --- Keyboard ---
document.addEventListener('keydown', function(e) {{
    if (e.key === 'ArrowLeft') {{
        currentClusterIdx = Math.max(0, currentClusterIdx - 1);
        showTab(CLUSTER_IDS[currentClusterIdx]);
    }} else if (e.key === 'ArrowRight') {{
        currentClusterIdx = Math.min(CLUSTER_IDS.length - 1, currentClusterIdx + 1);
        showTab(CLUSTER_IDS[currentClusterIdx]);
    }} else if (e.key === 'e' || e.key === 'E') {{
        exportFlags();
    }}
}});

// Init
restoreFlags();
</script>
''')

    html.append(f'''<div class="footer">
    Generated {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} |
    {brand.title()} | {len(cluster_ids)} clusters |
    {len(sampled_df)} products sampled |
    Max {MAX_PRODUCTS_PER_CLUSTER} per cluster
</div>
</body>
</html>''')

    return ''.join(html)


# =============================================================================
# SWATCH REVIEW HTML GENERATION
# =============================================================================

def generate_swatch_review_html(sampled_df, centroids_df, summary_df, brand):
    """Generate lightweight HTML review tool with color swatches instead of images."""
    from datetime import datetime

    centroids_sorted = _order_centroids_by_ciede2000(centroids_df)
    cluster_ids = centroids_sorted['cluster_id'].tolist()

    total_products = int(centroids_df['n_products'].sum())
    total_sampled = len(sampled_df)

    # Precompute RGB for all sampled products
    all_r, all_g, all_b = lab_to_rgb_array(
        sampled_df['lab_l'].values.astype(float),
        sampled_df['lab_a'].values.astype(float),
        sampled_df['lab_b'].values.astype(float))
    sampled_df = sampled_df.copy()
    sampled_df['r8'] = (all_r * 255).round().astype(int)
    sampled_df['g8'] = (all_g * 255).round().astype(int)
    sampled_df['b8'] = (all_b * 255).round().astype(int)

    # Build product data for JS export
    product_data_js = {}
    for _, product in sampled_df.iterrows():
        iid = int(product['instance_id'])
        product_data_js[iid] = {
            'instance_id': iid,
            'archive_id': int(product['archive_id']),
            'cluster_id': int(product['cluster_id']),
            'lab_l': round(float(product['lab_l']), 2),
            'lab_a': round(float(product['lab_a']), 2),
            'lab_b': round(float(product['lab_b']), 2),
        }

    html = []
    html.append(f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>CIEDE2000 Swatch Review ({brand.title()})</title>
<style>
* {{ box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #1a1a1a; color: #e0e0e0; margin: 0; padding: 20px 30px;
}}
h1 {{ color: #F97316; margin-bottom: 5px; }}
.subtitle {{ color: #888; margin-bottom: 10px; font-size: 14px; }}

.toolbar {{
    position: sticky; top: 0; z-index: 100;
    background: #1a1a1a; padding: 10px 0 10px; border-bottom: 1px solid #333;
    display: flex; align-items: center; gap: 15px; flex-wrap: wrap;
}}
.toolbar .flag-count {{ font-size: 14px; font-weight: 600; color: #F97316; }}
.toolbar button {{
    padding: 8px 16px; border: 2px solid #F97316; border-radius: 6px;
    background: transparent; color: #F97316; font-weight: 600; cursor: pointer;
    font-size: 13px;
}}
.toolbar button:hover {{ background: #F97316; color: #1a1a1a; }}
.toolbar .help {{ color: #555; font-size: 11px; }}

.tab-bar {{ display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 20px; }}
.tab-btn {{
    padding: 6px 12px; border: 2px solid #444; border-radius: 6px;
    cursor: pointer; font-size: 12px; font-weight: 600;
    transition: all 0.15s; background: #2a2a2a; color: #ccc;
    position: relative;
}}
.tab-btn:hover {{ border-color: #888; }}
.tab-btn.active {{ border-color: white; color: white; }}
.tab-swatch {{
    display: inline-block; width: 12px; height: 12px; border-radius: 3px;
    margin-right: 5px; vertical-align: middle; border: 1px solid rgba(255,255,255,0.3);
}}
.tab-badge {{
    position: absolute; top: -6px; right: -6px;
    background: #EF4444; color: white; font-size: 9px; font-weight: 700;
    padding: 1px 5px; border-radius: 10px; display: none;
}}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}

.cluster-header {{
    display: flex; align-items: center; gap: 20px;
    margin-bottom: 15px; padding: 15px; background: #222; border-radius: 8px;
}}
.color-swatch-large {{
    width: 80px; height: 80px; border-radius: 8px;
    border: 2px solid rgba(255,255,255,0.2); flex-shrink: 0;
}}
.cluster-title {{ font-size: 22px; font-weight: 700; color: #fff; }}
.cluster-lab {{ font-size: 13px; color: #999; margin-top: 4px; font-family: monospace; }}
.cluster-sample-info {{ font-size: 12px; color: #666; margin-top: 4px; }}

/* Swatch grid */
.swatch-grid {{
    display: flex; flex-wrap: wrap; gap: 4px; padding: 10px 0;
}}
.swatch {{
    width: 50px; height: 50px; border-radius: 4px;
    border: 3px solid transparent; cursor: pointer;
    transition: all 0.1s; position: relative;
}}
.swatch:hover {{ transform: scale(1.15); z-index: 10; }}
.swatch.flagged {{
    border-color: #EF4444;
    box-shadow: 0 0 8px rgba(239, 68, 68, 0.6);
}}
.swatch.flagged::after {{
    content: 'X'; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    display: flex; align-items: center; justify-content: center;
    color: #EF4444; font-weight: 900; font-size: 20px;
    text-shadow: 0 0 4px rgba(0,0,0,0.8);
}}

.no-products {{ color: #666; font-style: italic; padding: 20px; }}
.footer {{
    text-align: center; color: #555; margin-top: 40px; padding-top: 15px;
    border-top: 1px solid #333; font-size: 11px;
}}
</style>
</head>
<body>

<h1>CIEDE2000 Swatch Review</h1>
<p class="subtitle">
    {brand.title()} | {len(cluster_ids)} clusters |
    {total_products:,} total products |
    {total_sampled:,} randomly sampled |
    Click swatches that don't match the cluster centroid
</p>

<div class="toolbar">
    <span class="flag-count" id="flag-counter">0 products flagged</span>
    <button onclick="exportFlags()">Export Flagged (JSON)</button>
    <button onclick="clearCurrentCluster()">Clear Current Cluster</button>
    <span class="help">Click swatch = toggle flag | Left/Right = navigate clusters | E = export</span>
</div>
''')

    # Tab bar
    html.append('<div class="tab-bar">')
    for i, cid in enumerate(cluster_ids):
        crow = centroids_sorted[centroids_sorted['cluster_id'] == cid].iloc[0]
        r = int(round(float(crow['r']) * 255))
        g = int(round(float(crow['g']) * 255))
        b = int(round(float(crow['b']) * 255))
        n = int(crow['n_products'])
        active = ' active' if i == 0 else ''
        html.append(
            f'<div class="tab-btn{active}" onclick="showTab({cid})" '
            f'id="tab-btn-{cid}" data-cluster="{cid}">'
            f'<span class="tab-swatch" style="background:rgb({r},{g},{b})"></span>'
            f'C{cid} ({n})'
            f'<span class="tab-badge" id="badge-{cid}"></span>'
            f'</div>'
        )
    html.append('</div>')

    # Tab content
    for i, cid in enumerate(cluster_ids):
        crow = centroids_sorted[centroids_sorted['cluster_id'] == cid].iloc[0]
        active = ' active' if i == 0 else ''

        r = int(round(float(crow['r']) * 255))
        g = int(round(float(crow['g']) * 255))
        b = int(round(float(crow['b']) * 255))
        n_products = int(crow['n_products'])
        lab_l, lab_a, lab_b = crow['lab_l'], crow['lab_a'], crow['lab_b']
        hex_color = crow['hex_color']

        products = sampled_df[sampled_df['cluster_id'] == cid]
        n_sampled = len(products)

        html.append(f'<div class="tab-content{active}" id="tab-{cid}">')

        html.append(f'''<div class="cluster-header">
    <div class="color-swatch-large" style="background:rgb({r},{g},{b})"></div>
    <div>
        <div class="cluster-title">Cluster C{cid} &mdash; {n_products:,} products</div>
        <div class="cluster-lab">
            L*={lab_l:.1f} &nbsp; a*={lab_a:.1f} &nbsp; b*={lab_b:.1f}
            &nbsp;|&nbsp; {hex_color}
        </div>
        <div class="cluster-sample-info">Showing {n_sampled} of {n_products:,} (random sample)</div>
    </div>
</div>''')

        if n_sampled == 0:
            html.append('<div class="no-products">No sampled products</div>')
            html.append('</div>')
            continue

        html.append('<div class="swatch-grid">')
        for _, product in products.iterrows():
            iid = int(product['instance_id'])
            pr = int(product['r8'])
            pg = int(product['g8'])
            pb = int(product['b8'])
            p_lab_l = float(product['lab_l'])
            p_lab_a = float(product['lab_a'])
            p_lab_b = float(product['lab_b'])

            html.append(
                f'<div class="swatch" data-iid="{iid}" data-cluster="{cid}" '
                f'onclick="toggleFlag(this)" '
                f'style="background:rgb({pr},{pg},{pb})" '
                f'title="i:{iid} L*={p_lab_l:.0f} a*={p_lab_a:.0f} b*={p_lab_b:.0f}">'
                f'</div>'
            )

        html.append('</div>')  # swatch-grid
        html.append('</div>')  # tab-content

    # JavaScript (same logic as image review, different STORAGE_KEY)
    html.append(f'''
<script>
const STORAGE_KEY = 'ciede2000_swatch_review_flags';
const PRODUCT_DATA = {json.dumps(product_data_js)};
const CLUSTER_IDS = {json.dumps(cluster_ids)};
let currentClusterIdx = 0;

function loadFlags() {{
    try {{
        const stored = localStorage.getItem(STORAGE_KEY);
        return stored ? JSON.parse(stored) : {{}};
    }} catch(e) {{ return {{}}; }}
}}
function saveFlags(flags) {{
    localStorage.setItem(STORAGE_KEY, JSON.stringify(flags));
}}

function toggleFlag(el) {{
    const iid = el.dataset.iid;
    const flags = loadFlags();
    if (flags[iid]) {{
        delete flags[iid];
        el.classList.remove('flagged');
    }} else {{
        flags[iid] = PRODUCT_DATA[iid] || {{ instance_id: parseInt(iid) }};
        el.classList.add('flagged');
    }}
    saveFlags(flags);
    updateUI();
}}

function updateUI() {{
    const flags = loadFlags();
    const total = Object.keys(flags).length;
    document.getElementById('flag-counter').textContent =
        total + ' product' + (total !== 1 ? 's' : '') + ' flagged';
    const clusterCounts = {{}};
    for (const [iid, data] of Object.entries(flags)) {{
        const cid = data.cluster_id;
        clusterCounts[cid] = (clusterCounts[cid] || 0) + 1;
    }}
    for (const cid of CLUSTER_IDS) {{
        const badge = document.getElementById('badge-' + cid);
        if (badge) {{
            const count = clusterCounts[cid] || 0;
            badge.textContent = count;
            badge.style.display = count > 0 ? 'inline-block' : 'none';
        }}
    }}
}}

function restoreFlags() {{
    const flags = loadFlags();
    document.querySelectorAll('.swatch').forEach(el => {{
        if (flags[el.dataset.iid]) el.classList.add('flagged');
    }});
    updateUI();
}}

function showTab(clusterId) {{
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + clusterId).classList.add('active');
    document.getElementById('tab-btn-' + clusterId).classList.add('active');
    currentClusterIdx = CLUSTER_IDS.indexOf(clusterId);
}}

function exportFlags() {{
    const flags = loadFlags();
    const flagList = Object.values(flags);
    const exportData = {{
        exported_at: new Date().toISOString(),
        total_flagged: flagList.length,
        total_clusters: CLUSTER_IDS.length,
        flags: flagList
    }};
    const blob = new Blob([JSON.stringify(exportData, null, 2)],
                          {{ type: 'application/json' }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'flagged_swatches.json';
    a.click();
    URL.revokeObjectURL(url);
}}

function clearCurrentCluster() {{
    const cid = CLUSTER_IDS[currentClusterIdx];
    const flags = loadFlags();
    const tab = document.getElementById('tab-' + cid);
    if (!tab) return;
    tab.querySelectorAll('.swatch.flagged').forEach(el => {{
        delete flags[el.dataset.iid];
        el.classList.remove('flagged');
    }});
    saveFlags(flags);
    updateUI();
}}

document.addEventListener('keydown', function(e) {{
    if (e.key === 'ArrowLeft') {{
        currentClusterIdx = Math.max(0, currentClusterIdx - 1);
        showTab(CLUSTER_IDS[currentClusterIdx]);
    }} else if (e.key === 'ArrowRight') {{
        currentClusterIdx = Math.min(CLUSTER_IDS.length - 1, currentClusterIdx + 1);
        showTab(CLUSTER_IDS[currentClusterIdx]);
    }} else if (e.key === 'e' || e.key === 'E') {{
        exportFlags();
    }}
}});

restoreFlags();
</script>
''')

    html.append(f'''<div class="footer">
    Generated {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} |
    {brand.title()} | {len(cluster_ids)} clusters |
    {total_sampled:,} products sampled (random, +/-10pp 95% CI)
</div>
</body>
</html>''')

    return ''.join(html)


# =============================================================================
# ANALYZE MODE
# =============================================================================

def analyze_flagged(flagged_json_path, input_dir, output_dir):
    """Analyze exported flagged products to find pollution patterns."""
    print("=" * 70)
    print("ANALYZE FLAGGED PRODUCTS")
    print("=" * 70)

    # Load flagged data
    with open(flagged_json_path, 'r') as f:
        export_data = json.load(f)

    flags = export_data['flags']
    if not flags:
        print("  No flagged products found in JSON.")
        return

    flagged_df = pd.DataFrame(flags)
    print(f"  Loaded {len(flagged_df)} flagged products from {flagged_json_path}")

    # Load cluster data
    assignments, centroids, summary = load_raw_data(input_dir)
    centroids_lab = centroids[['cluster_id', 'lab_l', 'lab_a', 'lab_b']].copy()

    # GPU detection
    use_gpu, device_info = detect_gpu()
    print(f"  GPU: {device_info}")

    # For each flagged product: distance to own centroid and nearest alternative
    print(f"\n  Computing distances to centroids...")
    flagged_lab = flagged_df[['lab_l', 'lab_a', 'lab_b']].values.astype(np.float64)
    all_centroids_lab = centroids[['lab_l', 'lab_a', 'lab_b']].values.astype(np.float64)
    centroid_ids = centroids['cluster_id'].values

    xp = np
    if use_gpu:
        try:
            import cupy as cp
            xp = cp
            f_gpu = cp.asarray(flagged_lab)
            c_gpu = cp.asarray(all_centroids_lab)
            dist_matrix = _ciede2000_tile(f_gpu, c_gpu, xp)
            dist_matrix = cp.asnumpy(dist_matrix)
            del f_gpu, c_gpu
            cp.get_default_memory_pool().free_all_blocks()
        except Exception:
            dist_matrix = _ciede2000_tile(flagged_lab, all_centroids_lab, np)
    else:
        dist_matrix = _ciede2000_tile(flagged_lab, all_centroids_lab, np)

    results = []
    for i, (_, row) in enumerate(flagged_df.iterrows()):
        own_cid = int(row['cluster_id'])
        own_idx = np.where(centroid_ids == own_cid)[0]
        dist_to_own = float(dist_matrix[i, own_idx[0]]) if len(own_idx) > 0 else np.nan

        dists = dist_matrix[i].copy()
        dists[own_idx] = np.inf
        nearest_idx = np.argmin(dists)
        nearest_cid = int(centroid_ids[nearest_idx])
        dist_to_nearest = float(dists[nearest_idx])

        lab_l = float(row['lab_l'])
        lightness_zone = 'dark' if lab_l < 30 else ('mid' if lab_l < 60 else 'light')

        results.append({
            'instance_id': int(row['instance_id']),
            'cluster_id': own_cid,
            'lab_l': row['lab_l'],
            'lab_a': row['lab_a'],
            'lab_b': row['lab_b'],
            'dist_to_own_centroid': round(dist_to_own, 2),
            'nearest_alt_cluster': nearest_cid,
            'dist_to_nearest_alt': round(dist_to_nearest, 2),
            'delta': round(dist_to_own - dist_to_nearest, 2),
            'lightness_zone': lightness_zone,
        })

    results_df = pd.DataFrame(results)

    # Aggregate patterns
    print(f"\n  {'='*60}")
    print(f"  FLAGGED PRODUCT ANALYSIS")
    print(f"  {'='*60}")
    print(f"  Total flagged: {len(results_df)}")

    # By lightness zone
    print(f"\n  By lightness zone:")
    for zone in ['dark', 'mid', 'light']:
        count = (results_df['lightness_zone'] == zone).sum()
        pct = count / len(results_df) * 100
        print(f"    {zone:>6}: {count:>4} ({pct:.1f}%)")

    # Most common cluster pairs (flagged in cluster A, nearest is cluster B)
    pair_counts = results_df.groupby(['cluster_id', 'nearest_alt_cluster']).size()
    pair_counts = pair_counts.sort_values(ascending=False)
    print(f"\n  Top 10 cluster confusion pairs (flagged in -> closest to):")
    for (cid, alt_cid), count in pair_counts.head(10).items():
        print(f"    C{cid} -> C{alt_cid}: {count} products")

    # Mean distance to own centroid vs nearest alt
    print(f"\n  Distance summary:")
    print(f"    Mean dist to own centroid:    {results_df['dist_to_own_centroid'].mean():.2f}")
    print(f"    Mean dist to nearest alt:     {results_df['dist_to_nearest_alt'].mean():.2f}")
    print(f"    Products closer to alt (δ>0): "
          f"{(results_df['delta'] > 0).sum()} / {len(results_df)}")

    # Discount impact per cluster
    print(f"\n  Discount impact of removing flagged products:")
    flagged_ids = set(results_df['instance_id'].tolist())
    impact_rows = []
    for cid, group in assignments.groupby('cluster_id'):
        flagged_in_cluster = group[group['instance_id'].isin(flagged_ids)]
        if len(flagged_in_cluster) == 0:
            continue
        clean = group[~group['instance_id'].isin(flagged_ids)]
        freq_all = group['is_discounted'].mean() * 100
        freq_clean = clean['is_discounted'].mean() * 100 if len(clean) > 0 else np.nan
        impact_rows.append({
            'cluster_id': cid,
            'n_products': len(group),
            'n_flagged': len(flagged_in_cluster),
            'discount_freq_all': round(freq_all, 2),
            'discount_freq_clean': round(freq_clean, 2) if not np.isnan(freq_clean) else np.nan,
            'delta_freq_pp': round(freq_clean - freq_all, 2) if not np.isnan(freq_clean) else np.nan,
        })

    impact_df = pd.DataFrame(impact_rows)
    if len(impact_df) > 0:
        print(f"\n  {'Cluster':>8} {'Products':>9} {'Flagged':>8} "
              f"{'Freq All':>9} {'Freq Cln':>9} {'Δ Freq':>8}")
        print(f"  {'-'*56}")
        for _, row in impact_df.iterrows():
            delta_str = f"{row['delta_freq_pp']:+.1f}pp" if not np.isnan(row['delta_freq_pp']) else "n/a"
            freq_cln = f"{row['discount_freq_clean']:.1f}%" if not np.isnan(row['discount_freq_clean']) else "n/a"
            print(f"  C{int(row['cluster_id']):>6} {int(row['n_products']):>9,} "
                  f"{int(row['n_flagged']):>8} "
                  f"{row['discount_freq_all']:.1f}%{' ':>4} {freq_cln:>9} {delta_str:>8}")

    # Export
    os.makedirs(output_dir, exist_ok=True)
    analysis_path = os.path.join(output_dir, 'flagged_analysis.csv')
    results_df.to_csv(analysis_path, index=False)
    print(f"\n  Saved: {analysis_path}")

    if len(impact_df) > 0:
        impact_path = os.path.join(output_dir, 'flagged_cluster_impact.csv')
        impact_df.to_csv(impact_path, index=False)
        print(f"  Saved: {impact_path}")

    print(f"\n{'='*70}")
    print(f"  Complete.")
    print(f"{'='*70}")


# =============================================================================
# MAIN
# =============================================================================

def main(brand=None, gender=None, mode='images'):
    """Generate interactive cluster review HTML.

    Args:
        brand: brand to analyze (default: nike)
        gender: gender segment to review (default: mens). Selects which
            consolidate-colors run to load — outputs/<brand>_<gender>/.
        mode: 'images' (farthest-point sampling, product images) or
              'swatches' (random sampling, color swatch grid)
    """
    global _USE_GPU
    brand = brand or BRAND_DEFAULT
    gender = gender or GENDER_DEFAULT
    input_dir = _segment_input_dir(brand, gender)
    output_dir = _segment_output_dir(brand, gender)

    is_swatch = mode == 'swatches'
    mode_label = 'SWATCH' if is_swatch else 'IMAGE'

    print("=" * 70)
    print(f"CIEDE2000 CLUSTER REVIEW TOOL ({mode_label} MODE)")
    print("=" * 70)
    print(f"  Brand: {brand}")
    print(f"  Gender: {gender}")
    print(f"  Mode: {mode}")

    _USE_GPU, device_info = detect_gpu()
    print(f"  GPU: {device_info}")

    # Step 1: Load CSVs
    print(f"\n[1/3] Loading CSVs from consolidate-colors ({brand}_{gender})...")
    assignments, centroids, summary = load_raw_data(input_dir)
    print(f"  {len(centroids)} clusters, {len(assignments):,} product assignments")

    if is_swatch:
        # Swatch mode: random sampling, no images needed
        print(f"\n[2/3] Computing sample sizes (+/-10pp, 95% CI) and sampling...")
        sample_sizes = compute_sample_sizes(centroids, confidence=0.95, margin=0.10)
        total_needed = sum(sample_sizes.values())
        print(f"  Total samples needed: {total_needed:,}")
        sampled = sample_products_random(assignments, sample_sizes)
        print(f"  Sampled {len(sampled):,} products")

        print(f"\n[3/3] Generating swatch review HTML...")
        html_content = generate_swatch_review_html(
            sampled, centroids, summary, brand)

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, 'swatch_review.html')
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"  Saved: {output_path} ({file_size_mb:.1f} MB)")

    else:
        # Image mode: farthest-point sampling, product images
        print(f"\n[2/5] Farthest-point CIEDE2000 sampling "
              f"({MAX_PRODUCTS_PER_CLUSTER} per cluster)...")
        sampled = sample_products(assignments, centroids, MAX_PRODUCTS_PER_CLUSTER)
        print(f"  Sampled {len(sampled)} products")

        instance_ids = sampled['instance_id'].unique().tolist()
        print(f"\n[3/5] Fetching image metadata ({len(instance_ids)} instances)...")
        metadata = fetch_image_metadata(instance_ids, brand)
        print(f"  Metadata for {len(metadata)} products")

        print(f"\n[4/5] Generating thumbnails...")
        print(f"    Images dir: {IMAGES_DIR}")
        image_data = resolve_all_images(metadata, brand)

        print(f"\n[5/5] Generating review HTML...")
        html_content = generate_review_html(
            sampled, centroids, summary, image_data, metadata, brand)

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, 'cluster_review.html')
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"  Saved: {output_path} ({file_size_mb:.1f} MB)")

    print(f"\n{'='*70}")
    filename = 'swatch_review.html' if is_swatch else 'cluster_review.html'
    print(f"  Complete. Open {filename} in a browser.")
    print(f"  Click {'swatches' if is_swatch else 'products'} that don't belong, "
          f"then export flagged JSON.")
    print(f"  Analyze with: python review_cluster_quality.py "
          f"{brand} --gender {gender} --analyze <flagged.json>")
    print(f"{'='*70}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='CIEDE2000 Cluster Review Tool')
    parser.add_argument('brand', nargs='?', default=None,
                        help=f'Brand to analyze (default: {BRAND_DEFAULT})')
    parser.add_argument('--gender', default=None,
                        help=f'Gender segment (default: {GENDER_DEFAULT})')
    parser.add_argument('--analyze', type=str, default=None,
                        help='Path to exported flagged products JSON (analysis mode)')
    args = parser.parse_args()

    if args.analyze:
        _brand = args.brand or BRAND_DEFAULT
        _gender = args.gender or GENDER_DEFAULT
        analyze_flagged(args.analyze,
                        _segment_input_dir(_brand, _gender),
                        _segment_output_dir(_brand, _gender))
    else:
        main(brand=args.brand, gender=args.gender)
