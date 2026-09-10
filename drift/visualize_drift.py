#!/usr/bin/env python3
"""
ENTRY - run: python cli.py visualize-drift  (multi-brand drift chart -> drift_comparison.html).

visualize_drift.py

Fetches per-archive Sinkhorn 'distance' values from every brand database and
plots them together on an interactive Plotly line chart. A fast screen for
"has a brand's palette shifted dramatically?" without opening the full
palette_explorer. Output: outputs/drift_comparison.html.
"""

import os
import psycopg2
from typing import List, Dict
import plotly.graph_objects as go
from shared import db

def fetch_distances(brand: str) -> List[float]:
    """
    Connect to brand database and retrieve all 'distance' values
    from the driftcolor_comp_cluster_fpyolo11l241114_kmeans250218 table.

    Args:
        brand: Brand name (e.g., 'nike', 'adidas')

    Returns:
        List of distances in the order returned by the query.
    """
    conn, cur = db.connect_to_db(brand)

    # Execute query
    cur.execute(
        "SELECT distance "
        "FROM driftcolor_comp_cluster_fpyolo11l241114_kmeans250218;"
    )
    rows = cur.fetchall()

    # Clean up
    cur.close()
    conn.close()

    # Extract floats from query result tuples
    return [row[0] for row in rows]

def main():
    """
    Orchestrates the full flow:
      1. Defines brand list
      2. Fetches distance lists
      3. Builds and renders a Plotly line chart
    """
    # 1) Define brands to visualize
    brands = ["nike", "adidas", "puma", "lulu", "ua"]

    # 2) Fetch raw distances from each brand database
    distances_by_brand: Dict[str, List[float]] = {
        brand: fetch_distances(brand)
        for brand in brands
    }

    # 3) Build Plotly traces, each with a leading zero
    traces = []
    for alias, distances in distances_by_brand.items():
        # Prepend the initial zero point (y=0)
        y_vals = distances
        # x indices 0, 1, 2, … matching y_vals
        x_vals = list(range(len(y_vals)))

        traces.append(
            go.Scatter(
                x=x_vals,
                y=y_vals,
                mode="lines+markers",
                name=alias
            )
        )

    # 4) Create and display the figure
    fig = go.Figure(traces)
    fig.update_layout(
        title="Distance Drift Comparison Across Databases",
        xaxis_title="Index (0 = initial point)",
        yaxis_title="Distance",
        legend_title="Database Alias",
        yaxis=dict(range=[0, 10])
    )

    # Save to HTML file instead of opening browser (Docker-compatible).
    # Write into this slice's outputs/ dir, resolved relative to the module.
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "drift_comparison.html")
    fig.write_html(output_path)
    print(f"Chart saved to {output_path}")

if __name__ == "__main__":
    main()
