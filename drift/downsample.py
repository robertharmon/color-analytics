"""ENTRY - run: python cli.py downsample <brand>  (downsample clusters to 1900/archive; feeds drift)."""

import numpy as np
from sklearn.cluster import KMeans

from shared import db

DEST_TABLE = "comp_cluster_fpyolo11l241114_kmeans250218"
INITIAL_QUERY = """
    SELECT lab_l, lab_a, lab_b, perc
    FROM cluster_fpyolo11l241114_kmeans250218
    WHERE archive_id_ref = %s
"""
N_CLUSTERS = 1900

def run_downsample(brand: str):
    """
    Downsample color clusters to fixed size using weighted K-means aggregation.

    Args:
        brand: Brand name (e.g., 'nike', 'adidas')
    """
    conn, cur = db.connect_to_db(brand, readonly=False)

    # Determine which archives which archives need processing.
    unprocessed = db.get_unprocessed_archives(cur, DEST_TABLE)

    for archive_id in unprocessed:

        cur.execute(INITIAL_QUERY, (archive_id,))
        rows = cur.fetchall()

        if len(rows) == 0:
            print(f"Skipping archive_id_ref {archive_id}: no data found")
            continue

        lab_array = np.array([[r[0], r[1], r[2]] for r in rows])
        weights = np.array([r[3] for r in rows])

        if len(rows) > N_CLUSTERS:

            # Normalize weights for sample weighting
            sample_weights = weights / weights.sum()

            print(f"Downsampling archive_id_ref {archive_id}...")
            kmeans = KMeans(n_clusters=N_CLUSTERS, random_state=42)
            kmeans.fit(lab_array, sample_weight=sample_weights)
            labels = kmeans.labels_

            # Aggregate perc values for each new cluster
            cluster_sums = {}
            for label, perc in zip(labels, weights):
                cluster_sums[label] = cluster_sums.get(label, 0) + perc

            # Insert new clusters
            for i in range(N_CLUSTERS):
                lab = kmeans.cluster_centers_[i]
                perc_sum = cluster_sums.get(i, 0)
                cur.execute(
                    f"""
                    INSERT INTO {DEST_TABLE} (
                        archive_id_ref, perc, lab_l, lab_a, lab_b, n_clusters_combined
                    ) VALUES (%s, %s, %s, %s, %s, %s);
                    """, 
                    (archive_id, round(float(perc_sum), 2), int(round(lab[0])), int(round(lab[1])), int(round(lab[2])), N_CLUSTERS)
                )

            print(f"→ Downsampled to {N_CLUSTERS} clusters for archive_id_ref {archive_id}")

        else:
            print(f"Copying {len(rows)} clusters for archive_id_ref {archive_id} (no downsampling)")
            for row in rows:
                cur.execute(
                    f"""
                    INSERT INTO {DEST_TABLE} (
                        archive_id_ref, perc, lab_l, lab_a, lab_b, n_clusters_combined
                    ) VALUES (%s, %s, %s, %s, %s, %s);
                    """, 
                    (archive_id, round(float(row[3]), 2), int(row[0]), int(row[1]), int(row[2]), len(rows))
                )

        conn.commit()
        print(f"→ Done with archive_id_ref {archive_id}\n")

    cur.close()
    conn.close()
