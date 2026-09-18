#!/usr/bin/env python3
"""
ENTRY - run: python cli.py visualize-drift  (per-gender drift charts -> drift_comparison_<gender>.html).

Fetches per-archive Sinkhorn distances from every brand database, splits by
query (mens vs womens), and writes one interactive Plotly chart per gender.
Each chart plots one line per brand, x-axis = compare-archive date,
y-axis = Sinkhorn distance vs that brand's earliest archive for that query.

Output: outputs/drift_comparison_<gender>.html for each gender present.
"""

import os
from collections import defaultdict
from typing import Dict, List, Tuple

import plotly.graph_objects as go

from shared import db


def assign_gender(query_str: str):
    ql = str(query_str).lower()
    if 'womens' in ql:
        return 'womens'
    if 'mens' in ql:
        return 'mens'
    return None


def fetch_distances(brand: str) -> Dict[str, List[Tuple]]:
    """Return {query: [(compare_date, distance), ...]} for one brand,
    sorted by compare_date within each query."""
    conn, cur = db.connect_to_db(brand)
    try:
        cur.execute("""
            SELECT a.query, d.compare_date, d.distance
            FROM driftcolor_comp_cluster_fpyolo11l241114_kmeans250218 d
            JOIN archive a ON d.compare_archive_id_ref = a.archive_id
            ORDER BY a.query, d.compare_date;
        """)
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    by_query: Dict[str, List[Tuple]] = defaultdict(list)
    for query, compare_date, distance in rows:
        by_query[query].append((compare_date, distance))
    return dict(by_query)


def main():
    brands = ["nike", "adidas", "puma", "lulu", "ua"]

    # {gender: {brand: [(date, distance), ...]}}
    by_gender: Dict[str, Dict[str, List[Tuple]]] = defaultdict(dict)
    for brand in brands:
        for query, series in fetch_distances(brand).items():
            gender = assign_gender(query)
            if gender is None:
                continue
            # If a brand has multiple queries mapping to the same gender, keep
            # the longer series (defensive; expect 1:1 in practice).
            existing = by_gender[gender].get(brand)
            if existing is None or len(series) > len(existing):
                by_gender[gender][brand] = series

    if not by_gender:
        print("No drift rows found in any brand DB. Run 'compute-drift <brand>' first.")
        return

    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
    os.makedirs(output_dir, exist_ok=True)

    for gender, brand_series in sorted(by_gender.items()):
        traces = []
        for brand, series in sorted(brand_series.items()):
            x_vals = [d for d, _ in series]
            y_vals = [dist for _, dist in series]
            traces.append(go.Scatter(
                x=x_vals,
                y=y_vals,
                mode="lines+markers",
                name=brand,
            ))

        fig = go.Figure(traces)
        fig.update_layout(
            title=f"Color-Palette Drift ({gender.title()}) - Sinkhorn Distance vs Baseline Archive",
            xaxis_title="Compare archive date",
            yaxis_title="Sinkhorn distance",
            legend_title="Brand",
            yaxis=dict(range=[0, 10]),
        )

        output_path = os.path.join(output_dir, f"drift_comparison_{gender}.html")
        fig.write_html(output_path)
        print(f"Chart saved to {output_path}")


if __name__ == "__main__":
    main()
