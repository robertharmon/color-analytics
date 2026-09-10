"""ENTRY - run: python cli.py compute-drift <brand>  (Sinkhorn color-palette drift between archives)."""

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
            print(f"    → Row {i+1}/{total}...", end="\r", flush=True)
        color1 = LabColor(*lab1)
        for j, lab2 in enumerate(Y):
            color2 = LabColor(*lab2)
            C[i, j] = safe_delta_e_cie2000(color1, color2)
    print("    → Cost matrix complete.         ")
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

def get_archive_date(cur, archive_id_ref):
    cur.execute("SELECT received::date FROM archive WHERE archive_id = %s;", (archive_id_ref,))
    row = cur.fetchone()
    return row[0] if row else None

def run_drift(brand: str, base_archive_id=1, compare_archive_ids=None):
    """
    Calculate Sinkhorn distance between color distributions for drift analysis.

    Compares each archive_id in compare_archive_ids to base_archive_id
    and inserts the Sinkhorn distance into the drift table.

    Args:
        brand: Brand name (e.g., 'nike', 'adidas')
        base_archive_id: Reference archive ID for comparison baseline
        compare_archive_ids: List of archive IDs to compare against baseline
    """
    if compare_archive_ids is None:
        compare_archive_ids = [22, 24, 26, 28, 30]

    conn, cur = db.connect_to_db(brand, readonly=False)

    try:
        print(f"\n🔹 Loading base archive {base_archive_id}...")
        base_points, base_weights = fetch_clusters(cur, base_archive_id)
        base_date = get_archive_date(cur, base_archive_id)

        for compare_archive_id in compare_archive_ids:
            print(f"\n=== Comparing base {base_archive_id} to archive {compare_archive_id} ===")

            print("  ↪ Fetching comparison clusters...")
            compare_points, compare_weights = fetch_clusters(cur, compare_archive_id)
            compare_date = get_archive_date(cur, compare_archive_id)

            print("  ↪ Computing CIEDE2000 cost matrix...", end="", flush=True)
            cost_matrix = compute_ciede2000_cost_matrix(base_points, compare_points)
            C_gpu = cp.asarray(cost_matrix)
            a_gpu = cp.asarray(base_weights, dtype=cp.float64)
            b_gpu = cp.asarray(compare_weights, dtype=cp.float64)
            print(" done.")

            print("  ↪ Computing Sinkhorn transport plan...", end="", flush=True)
            transport_plan = ot.sinkhorn(a_gpu, b_gpu, C_gpu, reg=0.1)
            print(" done.")

            cost_matrix_gpu = cp.asarray(cost_matrix)
            raw_distance = cp.sum(transport_plan * cost_matrix_gpu).get()
            formatted_distance = float(f"{raw_distance:.8f}")

            cur.execute(
                f"""
                INSERT INTO {DEST_TABLE} (
                    distance, base_date, compare_date, base_archive_id_ref, compare_archive_id_ref
                ) VALUES (%s, %s, %s, %s, %s);
                """,
                (formatted_distance, base_date, compare_date, base_archive_id, compare_archive_id)
            )

            print(f"  ✔ Drift inserted → distance = {formatted_distance:.4f}")
            print(f"distance is {raw_distance}")

            # Clean up large arrays to prevent memory accumulation
            del cost_matrix
            del transport_plan
            if "cost_matrix_gpu" in locals():
                del cost_matrix_gpu

            import gc
            gc.collect()

            try:
                import cupy
                cupy.get_default_memory_pool().free_all_blocks()
            except ImportError:
                pass

        conn.commit()
    finally:
        cur.close()
        conn.close()

    print("\n✅ All comparisons complete.\n")
