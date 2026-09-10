"""
INTERNAL - a*b* row-grouping primitive (find_ab_groups); imported by palette_explorer.py and tune_hue_family_rows.py. Not run directly.

Hue-family row grouping (a*b* complete-linkage)
===============================================

Local primitive for the palette_explorer's row layout: given the cluster
centroids of a column, group the ones that sit close together in the a*b*
(chroma/hue) plane into a single "hue family" row, leaving the rest as
singletons. Grouping is complete-linkage — every pair inside a group is within
`radius` a*b* units of every other (no chaining) — so a family stays visually
coherent regardless of lightness.

Used by palette_explorer.py (builds the rows) and tune_hue_family_rows.py
(sweeps `radius` to calibrate the layout). Keep this the single definition of
the grouping so the explorer and its tuner never drift.

Centroids are dicts with at least 'a' and 'b' keys (LAB a*/b*).
"""

import math


def ab_distance(c1, c2):
    """Euclidean distance between two centroids in the a*b* plane."""
    return math.sqrt((c1['a'] - c2['a'])**2 + (c1['b'] - c2['b'])**2)


def find_ab_groups(centroids, radius):
    """Find groups via complete linkage on a*b* distance.
    Every pair within a group must be within radius (no chaining).

    Returns:
        (groups, singletons) where groups is a list of index-lists (len >= 2)
        and singletons is a list of individual indices.
    """
    n = len(centroids)

    # Compute full a*b* distance matrix
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = ab_distance(centroids[i], centroids[j])
            dist[i][j] = d
            dist[j][i] = d

    # Agglomerative complete linkage
    labels = list(range(n))

    while True:
        clusters = {}
        for i, lab in enumerate(labels):
            clusters.setdefault(lab, []).append(i)

        cluster_ids = list(clusters.keys())
        if len(cluster_ids) <= 1:
            break

        best_dist = float('inf')
        best_pair = None

        for ci_idx in range(len(cluster_ids)):
            for cj_idx in range(ci_idx + 1, len(cluster_ids)):
                ci, cj = cluster_ids[ci_idx], cluster_ids[cj_idx]
                # Complete linkage: max distance between any cross-pair
                max_d = 0.0
                for a in clusters[ci]:
                    for b in clusters[cj]:
                        if dist[a][b] > max_d:
                            max_d = dist[a][b]
                if max_d < best_dist:
                    best_dist = max_d
                    best_pair = (ci, cj)

        if best_dist > radius:
            break

        ci, cj = best_pair
        for i in range(n):
            if labels[i] == cj:
                labels[i] = ci

    # Collect groups and singletons
    clusters = {}
    for i, lab in enumerate(labels):
        clusters.setdefault(lab, []).append(i)

    groups = []
    singletons = []
    for members in clusters.values():
        if len(members) == 1:
            singletons.append(members[0])
        else:
            groups.append(members)

    return groups, singletons
