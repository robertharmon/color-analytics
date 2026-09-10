"""ENTRY - run: python cli.py extract-colors <brand>  (foundation stage 2: LAB k-means color extraction)."""

import os
import re
import glob
import cv2
import numpy as np
from multiprocessing import Pool, cpu_count
from sklearn.cluster import KMeans
from collections import Counter
from skimage.color import rgb2lab
from tqdm import tqdm

from shared import db

DEST_TABLE = "cluster_fpyolo11l241114_kmeans250218"
SEARCH_DIR = os.environ.get("IMAGE_DIR", "./images")
# Mirror of segment.SEG_OUTPUT_ROOT — when set, look for segmentation folders
# under <SEG_OUTPUT_ROOT>/<archive_folder_basename>/ instead of inside the
# archive JPEG folder. Must match the value used when segment ran.
SEG_OUTPUT_ROOT = os.environ.get("SEG_OUTPUT_ROOT")
INITIAL_QUERY = """
    SELECT archive_id_ref, instance_id_ref, segment_id
    FROM segment_fpyolo11l241114
    WHERE archive_id_ref IN %s;
"""
RANDOM_SEED = 42
NUM_CLUSTERS = 5

def extract_segment_module(query):
    match = re.search(r'FROM\s+(\S+)', query, re.IGNORECASE)
    name = match.group(1) if match else None
    if not name:
        raise ValueError("Could not extract table name")
    return name[len("segment_"):] if name.startswith("segment_") else name

def get_seg_dir(archive_path, segment_module):
    if SEG_OUTPUT_ROOT:
        seg_parent = os.path.join(SEG_OUTPUT_ROOT, os.path.basename(os.path.normpath(archive_path)))
    else:
        seg_parent = archive_path
    pattern = os.path.join(seg_parent, f"{segment_module}_*")
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(f"No segmentation folder in {seg_parent}")
    return max(matches, key=os.path.getmtime)

def load_image_and_mask(image_path):
    image = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"Image not found: {image_path}")
    if image.shape[2] == 4:
        alpha = image[:, :, 3]
        mask = alpha > 0
        image_rgb = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2RGB)
    else:
        mask = np.ones(image.shape[:2], dtype=bool)
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return image_rgb, mask

def cluster_image_compute(task):
    """Pure computation — no DB access. Safe for multiprocessing."""
    archive_id, instance_id, segment_id, seg_dir = task

    pattern = os.path.join(seg_dir, f"{archive_id}-{instance_id}-*.png")
    files = glob.glob(pattern)
    if not files:
        return None, seg_dir

    image_path = files[0]
    try:
        image, mask = load_image_and_mask(image_path)
    except Exception:
        return None, seg_dir

    pixels = rgb2lab((image[mask].astype(np.float32) / 255.0).reshape(1, -1, 3)).reshape(-1, 3)
    if len(pixels) < NUM_CLUSTERS:
        return None, seg_dir

    kmeans = KMeans(n_clusters=NUM_CLUSTERS, random_state=RANDOM_SEED)
    kmeans.fit(pixels)
    centers_lab = kmeans.cluster_centers_

    counts = Counter(kmeans.labels_)
    total = len(pixels)
    clusters = [
        (cid, centers_lab[cid].tolist(), (cnt / total) * 100)
        for cid, cnt in counts.items()
    ]

    rows = []
    for rank, (cid, lab_val, perc) in enumerate(sorted(clusters, key=lambda x: -x[2]), start=1):
        l, a, b = lab_val
        rows.append((archive_id, instance_id, segment_id, int(round(l)), int(round(a)), int(round(b)), round(perc, 2), rank))

    return rows, seg_dir

def run_clustering(brand: str):
    """
    Extract LAB color clusters from segmented garment images using K-means.

    Args:
        brand: Brand name (e.g., 'nike', 'adidas')
    """
    archive_dir_map = db.map_archive_dirs(brand, SEARCH_DIR)
    print(f"Found {len(archive_dir_map)} archive directories.")
    conn, cur = db.connect_to_db(brand, readonly=False)
    print("Connected to database.")
    try:
        unprocessed = db.get_unprocessed_archives(cur, DEST_TABLE)
        print(f"Found {len(unprocessed)} unprocessed archives.")
        cur.execute(INITIAL_QUERY, (tuple(unprocessed),))
        rows = cur.fetchall()
        print(f"Fetched {len(rows)} segments to cluster.")

        # Group rows by archive and resolve seg_dirs
        segment_module = extract_segment_module(INITIAL_QUERY)
        tasks = []
        seg_dir_cache = {}
        for row in rows:
            key = str(row['archive_id_ref'])
            archive_path = archive_dir_map.get(key)
            if not archive_path:
                continue
            if archive_path not in seg_dir_cache:
                seg_dir_cache[archive_path] = get_seg_dir(archive_path, segment_module)
            seg_dir = seg_dir_cache[archive_path]
            tasks.append((row['archive_id_ref'], row['instance_id_ref'], row['segment_id'], seg_dir))
        seg_dirs = set(seg_dir_cache.values())

        print(f"Built {len(tasks)} tasks across {len(seg_dirs)} seg dirs.")
        error_counts = {d: 0 for d in seg_dirs}

        # Parallel compute, sequential DB writes
        num_workers = max(1, cpu_count() - 1)
        print(f"Starting pool with {num_workers} workers...")
        with Pool(processes=num_workers) as pool:
            for result in tqdm(pool.imap_unordered(cluster_image_compute, tasks, chunksize=32), total=len(tasks), desc="Clustering"):
                cluster_rows, seg_dir = result

                if cluster_rows is None:
                    error_counts[seg_dir] += 1
                    continue

                for r in cluster_rows:
                    cur.execute(
                        f"""
                        INSERT INTO {DEST_TABLE} (
                            archive_id_ref, instance_id_ref, segment_id_ref, lab_l, lab_a, lab_b, perc, cluster_rank
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s);
                        """,
                        r
                    )

                print(f"Clustering completed for archive {cluster_rows[0][0]}, instance {cluster_rows[0][1]}.")

        conn.commit()
    finally:
        cur.close()
        conn.close()

    # Write error logs
    for seg_dir, count in error_counts.items():
        with open(os.path.join(seg_dir, "clustering_error_log.txt"), "w") as f:
            f.write(f"Number of records with errors: {count}\n")
