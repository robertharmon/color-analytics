"""ENTRY - run: python cli.py compute-drift <brand>  (Sinkhorn color-palette drift between archives).

For each of the brand's queries (which map 1:1 to gender), picks the earliest
archive with downsampled clusters as the baseline and computes Sinkhorn CIEDE2000
distance from every later archive back to that baseline. Idempotent: skips
(base, compare) pairs already present in the destination table.
"""

import gc
from collections import defaultdict

import numpy as np
import colormath

# Fix for NumPy 2.0 compatibility with colormath
if not hasattr(np, 'asscalar'):
    np.asscalar = lambda x: x.item() if hasattr(x, 'item') else x

from colormath.color_objects import LabColor
from colormath.color_diff import delta_e_cie2000 as original_delta_e_cie2000
import ot
import cupy as cp

from shared import db

DEST_TABLE = "driftcolor_comp_cluster_fpyolo11l241114_kmeans250218"


def safe_delta_e_cie2000(color1, color2):
    val = original_delta_e_cie2000(color1, color2)
    if np.isscalar(val):
        return val
    elif hasattr(val, "item"):
        return val.item()
    else:
        raise TypeError(f"Unexpected delta_e return type: {type(val)}")


def compute_ciede2000_cost_matrix(X, Y):
    C = np.zeros((len(X), len(Y)))
    total = len(X)
    for i, lab1 in enumerate(X):
        if i % 100 == 0 or i == total - 1:
            print(f"    -> Row {i+1}/{total}...", end="\r", flush=True)
        color1 = LabColor(*lab1)
        for j, lab2 in enumerate(Y):
            color2 = LabColor(*lab2)
            C[i, j] = safe_delta_e_cie2000(color1, color2)
    print("    -> Cost matrix complete.         ")
    return C


def normalize_weights(weights):
    total = sum(weights)
    return [w / total for w in weights] if total > 0 else weights


def fetch_clusters(cur, archive_id_ref):
    cur.execute("""
        SELECT lab_l, lab_a, lab_b, perc
        FROM comp_cluster_fpyolo11l241114_kmeans250218
        WHERE archive_id_ref = %s;
    """, (archive_id_ref,))
    rows = cur.fetchall()
    points = [(row[0], row[1], row[2]) for row in rows]
    weights = [row[3] for row in rows]
    return points, normalize_weights(weights)


def fetch_archives_by_query(cur):
    """Return {query: [(archive_id, received_date), ...]} for archives that have
    downsampled clusters, sorted earliest-first within each query."""
    cur.execute("""
        SELECT DISTINCT c.archive_id_ref, a.query, a.received::date
        FROM comp_cluster_fpyolo11l241114_kmeans250218 c
        JOIN archive a ON c.archive_id_ref = a.archive_id
        ORDER BY a.query, a.received;
    """)
    by_query = defaultdict(list)
    for archive_id, query, received in cur.fetchall():
        by_query[query].append((archive_id, received))
    return dict(by_query)


def fetch_existing_pairs(cur):
    cur.execute(f"SELECT base_archive_id_ref, compare_archive_id_ref FROM {DEST_TABLE};")
    return {(row[0], row[1]) for row in cur.fetchall()}


def _free_gpu():
    gc.collect()
    try:
        cp.get_default_memory_pool().free_all_blocks()
    except Exception:
        pass


def run_drift(brand: str):
    """
    For every query in this brand's archive table, drift each archive against
    the earliest archive of the same query and write a distance row.

    Args:
        brand: Brand name (e.g., 'nike', 'adidas')
    """
    conn, cur = db.connect_to_db(brand, readonly=False)

    try:
        archives_by_query = fetch_archives_by_query(cur)
        existing_pairs = fetch_existing_pairs(cur)

        if not archives_by_query:
            print(f"No downsampled archives found for {brand}. Run 'downsample' first.")
            return

        for query, archives in archives_by_query.items():
            if len(archives) < 2:
                print(f"\n[{query}] only {len(archives)} archive(s); skipping (need >=2).")
                continue

            base_id, base_date = archives[0]
            print(f"\n=== [{query}] baseline archive {base_id} ({base_date}) ===")
            base_points, base_weights = fetch_clusters(cur, base_id)

            for compare_id, compare_date in archives[1:]:
                if (base_id, compare_id) in existing_pairs:
                    print(f"  skip pair (base={base_id}, compare={compare_id}) - already computed")
                    continue

                print(f"\n  -> comparing base {base_id} vs archive {compare_id} ({compare_date})")

                compare_points, compare_weights = fetch_clusters(cur, compare_id)

                print("  ..computing CIEDE2000 cost matrix")
                cost_matrix = compute_ciede2000_cost_matrix(base_points, compare_points)
                C_gpu = cp.asarray(cost_matrix)
                a_gpu = cp.asarray(base_weights, dtype=cp.float64)
                b_gpu = cp.asarray(compare_weights, dtype=cp.float64)

                print("  ..running Sinkhorn")
                transport_plan = ot.sinkhorn(a_gpu, b_gpu, C_gpu, reg=0.1)
                raw_distance = cp.sum(transport_plan * C_gpu).get()
                formatted_distance = float(f"{raw_distance:.8f}")

                cur.execute(
                    f"""
                    INSERT INTO {DEST_TABLE} (
                        distance, base_date, compare_date,
                        base_archive_id_ref, compare_archive_id_ref
                    ) VALUES (%s, %s, %s, %s, %s);
                    """,
                    (formatted_distance, base_date, compare_date, base_id, compare_id)
                )
                conn.commit()
                existing_pairs.add((base_id, compare_id))
                print(f"  ok distance = {formatted_distance:.4f}")

                del cost_matrix, transport_plan, C_gpu, a_gpu, b_gpu
                _free_gpu()
    finally:
        cur.close()
        conn.close()

    print("\nAll drift comparisons complete.\n")
