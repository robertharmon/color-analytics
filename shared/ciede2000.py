"""
INTERNAL - shared GPU CIEDE2000 distance; imported by palette_explorer and drift. Not run directly.

CIEDE2000 Pairwise Distance Utilities
======================================

Shared utility for GPU-accelerated vectorized CIEDE2000 distance computation.
Used by the palette_explorer (agglomerative clustering) and drift slices.

Provides:
- detect_gpu(): CuPy/CUDA detection
- ciede2000_pairwise(): Tiled pairwise CIEDE2000 → scipy condensed vector
- DEFAULT_TILE_SIZE: Recommended GPU tile size

Usage:
    from shared.ciede2000 import ciede2000_pairwise, detect_gpu

    use_gpu, device_info = detect_gpu()
    condensed = ciede2000_pairwise(lab_array, use_gpu)
"""

import numpy as np

# NumPy 2.0 compatibility for colormath (removed np.asscalar)
if not hasattr(np, 'asscalar'):
    np.asscalar = lambda x: x.item() if hasattr(x, 'item') else x

# Conditional CuPy import
try:
    import cupy as cp
    HAS_CUPY = True
except ImportError:
    cp = None
    HAS_CUPY = False

# =============================================================================
# CONFIGURATION
# =============================================================================

DEFAULT_TILE_SIZE = 2000


# =============================================================================
# GPU DETECTION
# =============================================================================

def detect_gpu():
    """
    Detect CuPy/CUDA availability.

    Returns:
        Tuple of (use_gpu: bool, device_info: str)
    """
    if HAS_CUPY:
        try:
            dev = cp.cuda.Device(0)
            mem_info = dev.mem_info
            mem_gb = mem_info[1] / (1024 ** 3)
            return True, f"CUDA device detected ({mem_gb:.1f} GB VRAM)"
        except Exception:
            return False, "CuPy available but CUDA device not accessible — using CPU"
    else:
        return False, "CuPy not available — using CPU (numpy) fallback"


# =============================================================================
# CIEDE2000 VECTORIZED IMPLEMENTATION
# =============================================================================

def _ciede2000_tile(lab1, lab2, xp):
    """
    Vectorized CIEDE2000 for (M, 3) vs (N, 3) -> (M, N) distance matrix.

    Implements the full CIE DE 2000 formula (Sharma et al., 2005) using
    array broadcasting. All intermediate computations use float64.

    Args:
        lab1: (M, 3) array of LAB values
        lab2: (N, 3) array of LAB values
        xp: array library (cupy or numpy)

    Returns:
        (M, N) array of CIEDE2000 distances (float64)
    """
    pi = xp.float64(np.pi)
    deg2rad = pi / 180.0
    pow25_7 = xp.float64(25.0 ** 7)

    # Extract components with broadcasting shapes: (M, 1) vs (1, N)
    L1 = lab1[:, 0:1].astype(xp.float64)    # (M, 1)
    a1 = lab1[:, 1:2].astype(xp.float64)    # (M, 1)
    b1 = lab1[:, 2:3].astype(xp.float64)    # (M, 1)
    L2 = lab2[:, 0:1].astype(xp.float64).T  # (1, N)
    a2 = lab2[:, 1:2].astype(xp.float64).T  # (1, N)
    b2 = lab2[:, 2:3].astype(xp.float64).T  # (1, N)

    # Step 1: Chroma C*ab
    C1_ab = xp.sqrt(a1 ** 2 + b1 ** 2)  # (M, 1)
    C2_ab = xp.sqrt(a2 ** 2 + b2 ** 2)  # (1, N)

    # Step 2: Mean chroma
    C_bar_ab = (C1_ab + C2_ab) / 2.0  # (M, N)

    # Step 3: G factor
    C_bar_ab_7 = C_bar_ab ** 7
    G = 0.5 * (1.0 - xp.sqrt(C_bar_ab_7 / (C_bar_ab_7 + pow25_7)))  # (M, N)
    del C_bar_ab, C_bar_ab_7

    # Step 4: Corrected a'
    a1_prime = a1 * (1.0 + G)  # (M, N)
    a2_prime = a2 * (1.0 + G)  # (M, N)
    del G

    # Steps 5-6 combined: Corrected C' and hue h' (minimizes peak memory)
    C1_prime = xp.sqrt(a1_prime ** 2 + b1 ** 2)  # (M, N)
    h1_prime = xp.arctan2(b1, a1_prime) / deg2rad % 360.0  # (M, N)
    del a1_prime

    C2_prime = xp.sqrt(a2_prime ** 2 + b2 ** 2)  # (M, N)
    h2_prime = xp.arctan2(b2, a2_prime) / deg2rad % 360.0  # (M, N)
    del a2_prime

    # Zero chroma -> hue = 0
    h1_prime = xp.where(C1_prime == 0.0, 0.0, h1_prime)
    h2_prime = xp.where(C2_prime == 0.0, 0.0, h2_prime)

    # Step 7: Delta L', Delta C'
    dL = L2 - L1                  # (M, N)
    dC = C2_prime - C1_prime      # (M, N)

    # Step 8: Delta h' with quadrant handling
    h_diff = h2_prime - h1_prime  # (M, N)
    C_product = C1_prime * C2_prime

    dh = xp.where(
        C_product == 0.0, 0.0,
        xp.where(
            xp.abs(h_diff) <= 180.0, h_diff,
            xp.where(h_diff > 180.0, h_diff - 360.0, h_diff + 360.0)
        )
    )
    del h_diff

    # Step 9: Delta H'
    dH = 2.0 * xp.sqrt(xp.maximum(C_product, 0.0)) * xp.sin(dh / 2.0 * deg2rad)
    del dh

    # Step 10: Mean L', Mean C'
    L_bar = (L1 + L2) / 2.0       # (M, N)
    C_bar = (C1_prime + C2_prime) / 2.0  # (M, N)

    # Step 11: Mean hue h_bar' with quadrant handling
    h_sum = h1_prime + h2_prime
    h_abs_diff = xp.abs(h1_prime - h2_prime)

    h_bar = xp.where(
        C_product == 0.0, h_sum,
        xp.where(
            h_abs_diff <= 180.0, h_sum / 2.0,
            xp.where(
                h_sum < 360.0,
                (h_sum + 360.0) / 2.0,
                (h_sum - 360.0) / 2.0
            )
        )
    )
    del h_sum, h_abs_diff, C_product, h1_prime, h2_prime, C1_prime, C2_prime

    # Step 12: T factor (4 cosine terms)
    T = (1.0
         - 0.17 * xp.cos((h_bar - 30.0) * deg2rad)
         + 0.24 * xp.cos(2.0 * h_bar * deg2rad)
         + 0.32 * xp.cos((3.0 * h_bar + 6.0) * deg2rad)
         - 0.20 * xp.cos((4.0 * h_bar - 63.0) * deg2rad))

    # Step 13: Weighting functions S_L, S_C, S_H
    L_bar_50 = L_bar - 50.0
    S_L = 1.0 + 0.015 * L_bar_50 ** 2 / xp.sqrt(20.0 + L_bar_50 ** 2)
    S_C = 1.0 + 0.045 * C_bar
    S_H = 1.0 + 0.015 * C_bar * T
    del L_bar_50, T, L_bar

    # Step 14: Rotation term (blue hue correction)
    d_theta = 30.0 * xp.exp(-((h_bar - 275.0) / 25.0) ** 2)
    C_bar_7 = C_bar ** 7
    R_C = 2.0 * xp.sqrt(C_bar_7 / (C_bar_7 + pow25_7))
    R_T = -xp.sin(2.0 * d_theta * deg2rad) * R_C
    del h_bar, d_theta, C_bar, C_bar_7, R_C

    # Step 15: Final CIEDE2000 distance
    term_L = dL / S_L
    term_C = dC / S_C
    term_H = dH / S_H
    del dL, dC, dH, S_L, S_C, S_H

    result = xp.sqrt(
        term_L ** 2 + term_C ** 2 + term_H ** 2 + R_T * term_C * term_H
    )

    return result


def ciede2000_pairwise(lab, use_gpu, tile_size=DEFAULT_TILE_SIZE, verbose=True):
    """
    Compute pairwise CIEDE2000 distances in scipy condensed form.

    Uses tiled computation to manage GPU memory. For each tile of rows,
    only computes against remaining columns (upper-triangular optimization).

    Args:
        lab: (N, 3) numpy array of LAB values (float64)
        use_gpu: whether to use CuPy
        tile_size: rows per computation tile
        verbose: print tile progress

    Returns:
        condensed distance vector (float32), length N*(N-1)//2

    Note (S137, 2026-08-31): output dtype changed from float64 to float32 to
    halve the memory footprint of the pairwise matrix (~26 GB -> ~13 GB at
    n=84k). Intermediate CIEDE2000 arithmetic is still performed in float64
    inside _ciede2000_tile; only the stored condensed vector is downcast.
    Mirrors the same change in production/ciede2000_utils.py (old code); both
    codebases must match for Phase 5.6's consolidate-colors parity diff to be
    meaningful. Downstream fastcluster.linkage in build_color_clusters.py
    supports float32 natively; scipy.linkage in other new-code sites (build_zones,
    review_cluster_quality) up-casts to float64 but is only used on small
    centroid inputs.
    """
    N = len(lab)
    n_pairs = N * (N - 1) // 2
    condensed = np.empty(n_pairs, dtype=np.float32)

    if use_gpu:
        xp = cp
        lab_dev = cp.asarray(lab)
    else:
        xp = np
        lab_dev = lab

    n_tiles = (N - 1 + tile_size - 1) // tile_size
    idx = 0

    for tile_num, start in enumerate(range(0, N - 1, tile_size)):
        end = min(start + tile_size, N)
        M = end - start

        if verbose:
            print(f"    tile {tile_num + 1}/{n_tiles}: "
                  f"rows {start:,}-{end - 1:,} vs cols {start:,}-{N - 1:,}",
                  end="\r", flush=True)

        # Compute distances: rows [start, end) vs columns [start, N)
        # Upper-triangular optimization: only need columns >= start
        tile_lab = lab_dev[start:end]       # (M, 3)
        remaining_lab = lab_dev[start:]     # (N - start, 3)

        dist_tile = _ciede2000_tile(tile_lab, remaining_lab, xp)  # (M, N-start)

        if use_gpu:
            dist_tile = cp.asnumpy(dist_tile)

        # Extract upper-triangular entries for each row
        for local_i in range(M):
            # Column 0 of dist_tile corresponds to global column `start`
            # For global row (start + local_i), need columns > (start + local_i)
            # In local coords: columns > local_i
            row_entries = dist_tile[local_i, local_i + 1:]
            n_entries = len(row_entries)
            if n_entries > 0:
                condensed[idx:idx + n_entries] = row_entries
                idx += n_entries

        del dist_tile
        if use_gpu:
            cp.get_default_memory_pool().free_all_blocks()

    if verbose:
        print()  # Clear progress line

    assert idx == n_pairs, f"Expected {n_pairs} pairs, got {idx}"
    return condensed
