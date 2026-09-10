"""
INTERNAL - shared HSB/LAB color helpers; imported by archetype_taxonomy, coverage, palette_explorer. Not run directly.

HSB Saturation Utilities
========================

Shared utility functions for HSB (Photoshop-style) saturation-based
color classification. Used by the archetype_taxonomy, coverage, and
palette_explorer slices.

HSB saturation = (max(R,G,B) - min(R,G,B)) / max(R,G,B), on 0-100 scale.
This matches Photoshop's color picker S axis.

Color class boundaries:
- Saturated: HSB sat >= 50%
- Muted: 12% <= HSB sat < 50%
- Neutral: HSB sat < 12%
"""

import numpy as np

# =============================================================================
# HSB SATURATION THRESHOLDS
# =============================================================================

SATURATED_HSB = 50.0    # HSB saturation above this = saturated
NEUTRAL_HSB = 12.0      # HSB saturation below this = neutral
                        # Between NEUTRAL_HSB and SATURATED_HSB = muted


# =============================================================================
# CONVERSION FUNCTIONS
# =============================================================================

def lab_to_rgb_array(lab_l, lab_a, lab_b):
    """
    Vectorized LAB -> sRGB conversion (0-1, clipped) for batch HSB computation.

    Uses D65 illuminant and sRGB color space with proper gamma correction.

    Args:
        lab_l, lab_a, lab_b: Arrays of CIELAB values.

    Returns:
        Tuple of (r, g, b) arrays, each in 0-1 range (clipped).
    """
    # LAB to XYZ
    y = (lab_l + 16) / 116
    x = lab_a / 500 + y
    z = y - lab_b / 200

    # Inverse f function
    delta = 6 / 29
    mask_x = x > delta
    mask_y = y > delta
    mask_z = z > delta

    x3 = x ** 3
    y3 = y ** 3
    z3 = z ** 3

    x = np.where(mask_x, x3, (x - 16 / 116) / 7.787)
    y = np.where(mask_y, y3, (y - 16 / 116) / 7.787)
    z = np.where(mask_z, z3, (z - 16 / 116) / 7.787)

    # Scale by reference white (D65)
    x = x * 95.047 / 100
    y = y * 100.0 / 100
    z = z * 108.883 / 100

    # XYZ to linear RGB
    r = x * 3.2406 + y * -1.5372 + z * -0.4986
    g = x * -0.9689 + y * 1.8758 + z * 0.0415
    b = x * 0.0557 + y * -0.2040 + z * 1.0570

    # Gamma correction (sRGB)
    def gamma(c):
        return np.where(c > 0.0031308,
                        1.055 * np.power(np.maximum(c, 0), 1 / 2.4) - 0.055,
                        12.92 * c)

    r = np.clip(gamma(r), 0, 1)
    g = np.clip(gamma(g), 0, 1)
    b = np.clip(gamma(b), 0, 1)

    return r, g, b


def compute_hsb_saturation(lab_l, lab_a, lab_b):
    """
    Compute HSB (Photoshop-style) saturation for LAB colors.

    HSB saturation = (max(R,G,B) - min(R,G,B)) / max(R,G,B) * 100

    This is NOT perceptually uniform - it is a geometric RGB ratio.
    It matches Photoshop's color picker S axis, which designers use
    to define their intuition of "how saturated is this color?"

    Args:
        lab_l, lab_a, lab_b: Arrays of CIELAB values.

    Returns:
        Array of HSB saturation values (0-100 scale).
    """
    r, g, b = lab_to_rgb_array(lab_l, lab_a, lab_b)

    rgb_max = np.maximum(np.maximum(r, g), b)
    rgb_min = np.minimum(np.minimum(r, g), b)

    # Avoid division by zero for black pixels (np.where evaluates both
    # branches eagerly, so clamp the denominator to suppress the warning)
    safe_max = np.maximum(rgb_max, 1e-10)
    saturation = np.where(rgb_max > 0,
                          (rgb_max - rgb_min) / safe_max * 100,
                          0)

    return saturation


# =============================================================================
# CLASSIFICATION FUNCTIONS
# =============================================================================

def classify_hsb(hsb_sat):
    """
    Classify a single HSB saturation value into color class.

    Args:
        hsb_sat: HSB saturation value (0-100 scale).

    Returns:
        String: 'saturated', 'muted', or 'neutral'.
    """
    if hsb_sat >= SATURATED_HSB:
        return 'saturated'
    elif hsb_sat >= NEUTRAL_HSB:
        return 'muted'
    else:
        return 'neutral'


def classify_hsb_vectorized(hsb_sat_array):
    """
    Classify an array of HSB saturation values into color classes.

    Vectorized version for efficient bulk classification.

    Args:
        hsb_sat_array: NumPy array of HSB saturation values (0-100 scale).

    Returns:
        NumPy array of strings: 'saturated', 'muted', or 'neutral'.
    """
    hsb_sat_array = np.asarray(hsb_sat_array)

    # Initialize with 'muted' as default (the middle category)
    result = np.full(hsb_sat_array.shape, 'muted', dtype=object)

    # Assign 'saturated' and 'neutral'
    result[hsb_sat_array >= SATURATED_HSB] = 'saturated'
    result[hsb_sat_array < NEUTRAL_HSB] = 'neutral'

    return result
