"""
INTERNAL - single source of truth for the hue-merge primitive + thresholds; imported by archetype_taxonomy, palette_explorer. Not run directly.

Hue-Based Palette Merge
=======================

Single source of truth for the hue-based cluster merge primitive and the
color-geometry helpers + threshold constants it depends on.

A product's raw 5-color K-means palette often splits one perceptual color
across several clusters (lighting variation, near-neutral straddling the
neutral boundary). This module collapses that redundancy: it unions clusters
that belong to the same hue family and returns coverage-weighted merged
colors, sorted by coverage descending.

Importers (keep in sync via this module — do not re-copy the logic):
- archetype_taxonomy.classify_archetypes    (classification builds on merged palettes)
- palette_explorer.build_color_clusters      (CIEDE2000 agglomerative clustering)
Its recalibration tool is tune_merge_rules.py (same folder).

Merge rules (see should_merge_hue_based):
  1. Both low chroma  -> merge if |ΔL| <= LOW_CHROMA_MAX_DELTA_L
  2. Both neutral     -> merge if |ΔL| <= LOW_CHROMA_MAX_DELTA_L
  3. One neutral, one chromatic -> don't merge (accent on a neutral base)
  4/5. Both chromatic -> merge iff hue difference < HUE_MERGE_THRESHOLD

Thresholds were tuned to a 94.7% merge-accuracy ceiling (Session 84) against
labeled Nike data. LOW_CHROMA_MAX_DELTA_L guards black+white from merging
despite both being low-chroma.

`compute_merge_group_indices` exposes the union-find grouping as per-cluster
group IDs (for building labeling tools and comparing against hand-labels). Every
public function takes an optional `thresholds=DEFAULT_THRESHOLDS`: it exists so
the recalibration tool can score candidate constants against THIS code rather
than a forked replica — production callers omit it.
"""

import math
from collections import defaultdict, namedtuple

# ======================
# HUE-BASED MERGE CONFIG
# ======================

# Chroma threshold for "true neutral" (no meaningful hue)
NEUTRAL_CHROMA_THRESHOLD = 8  # Chroma < 8 = neutral (gray/black/white)

# Chroma threshold for "both low chroma" merge rule
# If BOTH colors have chroma below this, merge them (subject to lightness guard)
# Trade-off: May over-merge distinct muted colors, but prevents lighting variation splits
LOW_CHROMA_MERGE_THRESHOLD = 15

# Maximum lightness difference for merging low-chroma/neutral pairs
# Prevents merging black+white (delta_L ~54) while allowing similar grays
# Derived from labeled merge data: 300 Nike products, 3000 pairwise comparisons
LOW_CHROMA_MAX_DELTA_L = 35

# Hue angle threshold for merging (degrees)
HUE_MERGE_THRESHOLD = 25  # Colors within 25 degrees are "same hue family"

# Bundle of the four merge thresholds, so a candidate set can be passed through
# the merge rule as a single argument. The `thresholds=` override on the
# functions below exists ONLY so the tuner (tune_merge_rules.py) can score
# candidate constants against the SAME code the pipeline runs — do not fork the
# rule to try new values. Every production call omits it and gets DEFAULT_THRESHOLDS.
MergeThresholds = namedtuple(
    'MergeThresholds',
    ['neutral_chroma', 'low_chroma_merge', 'low_chroma_max_delta_l', 'hue_merge'],
)
DEFAULT_THRESHOLDS = MergeThresholds(
    neutral_chroma=NEUTRAL_CHROMA_THRESHOLD,
    low_chroma_merge=LOW_CHROMA_MERGE_THRESHOLD,
    low_chroma_max_delta_l=LOW_CHROMA_MAX_DELTA_L,
    hue_merge=HUE_MERGE_THRESHOLD,
)


# ============================================================================
# COLOR-GEOMETRY HELPERS
# ============================================================================

def get_chroma(a, b):
    """Calculate chroma (saturation/colorfulness) from LAB a and b values."""
    return math.sqrt(a**2 + b**2)


def get_hue_angle(a, b, neutral_threshold=NEUTRAL_CHROMA_THRESHOLD):
    """
    Get hue angle in degrees (0-360).
    Returns None for neutral colors (chroma < neutral_threshold).

    `neutral_threshold` defaults to the module constant; it is a parameter so a
    threshold sweep over NEUTRAL_CHROMA_THRESHOLD reaches the code that consults it.
    """
    chroma = get_chroma(a, b)
    if chroma < neutral_threshold:
        return None  # Neutral has no meaningful hue
    angle = math.atan2(b, a) * 180 / math.pi
    return angle % 360


def hue_difference(h1, h2):
    """
    Get smallest angle between two hues (0-180).
    Returns None if either hue is None (neutral).
    """
    if h1 is None or h2 is None:
        return None
    diff = abs(h1 - h2)
    return min(diff, 360 - diff)


def classify_saturation_lab(chroma):
    """
    Classify LAB chroma into S (Saturated), M (Muted), or N (Neutral).
    NOTE: This is used for internal processing only, NOT for has_saturated field.
    """
    if chroma >= 30:  # Keep original thresholds for merging logic
        return 'S'
    elif chroma >= 10:
        return 'M'
    else:
        return 'N'


def should_merge_hue_based(c1, c2, thresholds=DEFAULT_THRESHOLDS):
    """
    Determine if two clusters should merge based on hue similarity.

    Rules:
    1. Both low chroma (< low_chroma_merge) -> MERGE if |ΔL| <= low_chroma_max_delta_l
    2. Both neutral (no hue) -> MERGE if |ΔL| <= low_chroma_max_delta_l
    3. One neutral, one chromatic -> DON'T MERGE (accent on neutral base)
    4. Both chromatic, similar hue -> MERGE (same color family)
    5. Both chromatic, different hue -> DON'T MERGE (distinct colors)

    `thresholds` defaults to DEFAULT_THRESHOLDS (the module constants); pass a
    MergeThresholds to score a candidate set. See the config block above.
    """
    chroma1 = get_chroma(c1['lab_a'], c1['lab_b'])
    chroma2 = get_chroma(c2['lab_a'], c2['lab_b'])

    # Rule 1: Both low chroma - merge if lightness is similar
    # This catches near-neutral colors (C: 6-15) that are lighting variation
    # but prevents merging black+white despite both being low-chroma
    if chroma1 < thresholds.low_chroma_merge and chroma2 < thresholds.low_chroma_merge:
        return abs(c1['lab_l'] - c2['lab_l']) <= thresholds.low_chroma_max_delta_l

    h1 = get_hue_angle(c1['lab_a'], c1['lab_b'], thresholds.neutral_chroma)
    h2 = get_hue_angle(c2['lab_a'], c2['lab_b'], thresholds.neutral_chroma)

    # Rule 2: Both neutral (no hue) - merge if lightness is similar
    if h1 is None and h2 is None:
        return abs(c1['lab_l'] - c2['lab_l']) <= thresholds.low_chroma_max_delta_l

    # Rule 3: One neutral, one chromatic - don't merge
    if (h1 is None) != (h2 is None):
        return False

    # Rules 4 & 5: Both chromatic - compare hue angles
    return hue_difference(h1, h2) < thresholds.hue_merge


def compute_merge_group_indices(clusters, thresholds=DEFAULT_THRESHOLDS):
    """
    Run the same union-find merge logic as merge_clusters_hue_based but return an
    array of group IDs (one per raw cluster, dense sequential from 0 in
    first-appearance order) instead of merged colors.

    Captures which raw clusters the algorithm groups together, so a human's
    hand-labeled grouping can be compared against the algorithm's. Non-mutating
    with respect to the merge itself: it only ensures chroma/hue are present.

    `thresholds` defaults to DEFAULT_THRESHOLDS; pass a MergeThresholds to see how
    a candidate set would regroup a product (used by the constant-proposal sweep).
    """
    n = len(clusters)
    if n == 0:
        return []

    # Ensure computed fields exist (get_hue_angle needs the neutral threshold)
    for c in clusters:
        if 'chroma' not in c:
            c['chroma'] = get_chroma(c['lab_a'], c['lab_b'])
        if 'hue' not in c:
            c['hue'] = get_hue_angle(c['lab_a'], c['lab_b'], thresholds.neutral_chroma)

    if n == 1:
        return [0]

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        for j in range(i + 1, n):
            if should_merge_hue_based(clusters[i], clusters[j], thresholds):
                union(i, j)

    # Map roots to sequential group IDs in first-appearance order
    root_to_group = {}
    next_group = 0
    groups = []
    for i in range(n):
        root = find(i)
        if root not in root_to_group:
            root_to_group[root] = next_group
            next_group += 1
        groups.append(root_to_group[root])

    return groups


def merge_clusters_hue_based(clusters, thresholds=DEFAULT_THRESHOLDS):
    """
    Merge clusters using hue-based logic.

    Uses union-find to group clusters that should merge,
    then computes weighted average for each group.

    `thresholds` defaults to DEFAULT_THRESHOLDS (module constants).
    """
    if len(clusters) == 0:
        return []

    # Add computed fields to each cluster
    for c in clusters:
        c['chroma'] = get_chroma(c['lab_a'], c['lab_b'])
        c['hue'] = get_hue_angle(c['lab_a'], c['lab_b'], thresholds.neutral_chroma)
        c['sat_type'] = classify_saturation_lab(c['chroma'])

    if len(clusters) == 1:
        return clusters

    # Union-find for grouping
    n = len(clusters)
    parent = list(range(n))

    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # Union clusters that should merge
    for i in range(n):
        for j in range(i + 1, n):
            if should_merge_hue_based(clusters[i], clusters[j], thresholds):
                union(i, j)

    # Group by root
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(clusters[i])

    # Merge each group (weighted average by coverage)
    merged = []
    for group in groups.values():
        total_perc = sum(c['perc'] for c in group)
        if total_perc == 0:
            continue

        avg_l = sum(c['lab_l'] * c['perc'] for c in group) / total_perc
        avg_a = sum(c['lab_a'] * c['perc'] for c in group) / total_perc
        avg_b = sum(c['lab_b'] * c['perc'] for c in group) / total_perc

        avg_chroma = get_chroma(avg_a, avg_b)
        avg_hue = get_hue_angle(avg_a, avg_b)

        merged.append({
            'lab_l': avg_l,
            'lab_a': avg_a,
            'lab_b': avg_b,
            'perc': total_perc,
            'chroma': avg_chroma,
            'hue': avg_hue,
            'sat_type': classify_saturation_lab(avg_chroma),
            'n_merged': len(group)  # Track how many raw clusters merged
        })

    # Sort by coverage descending
    merged.sort(key=lambda x: x['perc'], reverse=True)
    return merged
