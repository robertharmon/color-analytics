"""
ENTRY - run: python cli.py check-mask-shift  (segmented-mask color-shift regression check).

Test: Why do segmented PNGs look slightly muted compared to original JPEGs?

Hypothesis: Original JPEGs have an embedded ICC color profile that browsers apply
when rendering. PIL strips this profile during segmentation, so the PNG gets
displayed as raw sRGB — causing a small but visible color shift.

This test:
  1. Checks whether original JPEGs have embedded ICC profiles
  2. Compares PIL-decoded pixels (no profile) vs profile-corrected pixels
  3. Measures the RGB shift caused by the missing profile
  4. Saves comparison images showing the difference

Usage:
    docker compose run --rm pipeline python check_mask_color_shift.py --scan nike
    docker compose run --rm pipeline python check_mask_color_shift.py <archive_dir>
"""

import os
import sys
import glob
import argparse
import numpy as np
from PIL import Image, ImageCms
import cv2


SEARCH_DIR = "./images"
OUTPUT_DIR = "./test_output/mask_binarization"
SEG_MODULE = "fpyolo11l241114"
MAX_SAMPLES = 10


def find_archive_dir(brand):
    """Find the first archive directory for a brand that has segmented images."""
    pattern = os.path.join(SEARCH_DIR, f"{brand}_*")
    for archive_dir in sorted(glob.glob(pattern)):
        seg_dirs = glob.glob(os.path.join(archive_dir, f"{SEG_MODULE}_*"))
        if seg_dirs:
            return archive_dir
    return None


def find_pairs(archive_dir):
    """Find matched (original_jpg, segmented_png) pairs."""
    seg_dirs = glob.glob(os.path.join(archive_dir, f"{SEG_MODULE}_*"))
    if not seg_dirs:
        print(f"No segmentation folder found in {archive_dir}")
        return []

    seg_dir = max(seg_dirs, key=os.path.getmtime)
    print(f"Segmentation folder: {seg_dir}")

    pairs = []
    for png_path in sorted(glob.glob(os.path.join(seg_dir, "*.png"))):
        fname = os.path.splitext(os.path.basename(png_path))[0]
        parts = fname.split("-")
        if len(parts) < 7:
            continue
        original_name = "-".join(parts[:5]) + ".jpg"
        original_path = os.path.join(archive_dir, original_name)
        if os.path.exists(original_path):
            pairs.append((original_path, png_path))
            if len(pairs) >= MAX_SAMPLES:
                break

    print(f"Found {len(pairs)} matched pairs (using up to {MAX_SAMPLES})")
    return pairs


def get_icc_profile_info(image_path):
    """Extract ICC profile info from an image file."""
    img = Image.open(image_path)
    icc_data = img.info.get("icc_profile")
    if not icc_data:
        return None, None

    try:
        profile = ImageCms.ImageCmsProfile(ImageCms.core.profile_frombytes(icc_data))
        description = ImageCms.getProfileDescription(profile)
        return icc_data, description
    except Exception as e:
        return icc_data, f"(could not read profile name: {e})"


def open_with_profile_correction(image_path):
    """
    Open a JPEG and apply its embedded ICC profile to convert to sRGB.

    Returns:
        (raw_rgb, corrected_rgb, profile_name)
        - raw_rgb: pixels as PIL decoded them (no profile applied)
        - corrected_rgb: pixels after ICC profile → sRGB conversion
        - profile_name: name of the embedded profile, or None
    """
    img = Image.open(image_path)
    raw_rgb = np.array(img.convert("RGB"))

    icc_data = img.info.get("icc_profile")
    if not icc_data:
        return raw_rgb, raw_rgb, None

    try:
        src_profile = ImageCms.ImageCmsProfile(ImageCms.core.profile_frombytes(icc_data))
        profile_name = ImageCms.getProfileDescription(src_profile)
        dst_profile = ImageCms.createProfile("sRGB")

        # Convert from embedded profile to sRGB
        corrected = ImageCms.profileToProfile(
            img.convert("RGB"), src_profile, dst_profile,
            renderingIntent=ImageCms.Intent.PERCEPTUAL,
            outputMode="RGB",
        )
        corrected_rgb = np.array(corrected)
        return raw_rgb, corrected_rgb, profile_name
    except Exception as e:
        print(f"  ICC conversion failed: {e}")
        return raw_rgb, raw_rgb, f"(error: {e})"


def analyze_pair(original_path, segmented_path):
    """
    Compare original JPEG (with ICC) vs segmented PNG (without ICC).

    Measures:
      - Whether the JPEG has an embedded ICC profile
      - The pixel-level shift between raw and profile-corrected values
      - Whether the segmented PNG matches the raw (uncorrected) pixels
    """
    # Open original with and without ICC correction
    raw_rgb, corrected_rgb, profile_name = open_with_profile_correction(original_path)

    # Open segmented PNG
    segmented = cv2.imread(segmented_path, cv2.IMREAD_UNCHANGED)
    if segmented is None or len(segmented.shape) < 3 or segmented.shape[2] != 4:
        return None
    seg_rgb = cv2.cvtColor(segmented[:, :, :3], cv2.COLOR_BGR2RGB)
    seg_alpha = segmented[:, :, 3]

    h, w = raw_rgb.shape[:2]
    if seg_rgb.shape[:2] != (h, w):
        print(f"  Size mismatch — skipping")
        return None

    opaque = seg_alpha == 255
    n_opaque = int(opaque.sum())
    if n_opaque == 0:
        return None

    results = {
        "profile_name": profile_name,
        "has_profile": profile_name is not None,
        "opaque_pixels": n_opaque,
    }

    # Test 1: Does the segmented PNG match PIL's raw decode? (should be ~0)
    raw_vs_seg = np.abs(raw_rgb[opaque].astype(np.float64) - seg_rgb[opaque].astype(np.float64))
    results["raw_vs_seg_mean"] = round(raw_vs_seg.mean(), 3)
    results["raw_vs_seg_max"] = int(raw_vs_seg.max())

    # Test 2: Does the ICC correction change the pixel values?
    raw_vs_corrected = np.abs(raw_rgb[opaque].astype(np.float64) - corrected_rgb[opaque].astype(np.float64))
    results["icc_shift_mean"] = round(raw_vs_corrected.mean(), 3)
    results["icc_shift_max"] = int(raw_vs_corrected.max())

    # Per-channel ICC shift
    for ch, name in enumerate(["R", "G", "B"]):
        ch_diff = corrected_rgb[opaque, ch].astype(np.float64) - raw_rgb[opaque, ch].astype(np.float64)
        results[f"icc_shift_{name}_mean"] = round(ch_diff.mean(), 2)

    # Test 3: Does the corrected original match what the browser would show?
    # (i.e., corrected original vs segmented PNG = the shift the user sees)
    corrected_vs_seg = np.abs(corrected_rgb[opaque].astype(np.float64) - seg_rgb[opaque].astype(np.float64))
    results["browser_shift_mean"] = round(corrected_vs_seg.mean(), 3)
    results["browser_shift_max"] = int(corrected_vs_seg.max())

    for ch, name in enumerate(["R", "G", "B"]):
        ch_diff = seg_rgb[opaque, ch].astype(np.float64) - corrected_rgb[opaque, ch].astype(np.float64)
        results[f"browser_shift_{name}_mean"] = round(ch_diff.mean(), 2)

    return results, raw_rgb, corrected_rgb, seg_rgb, seg_alpha


def save_comparison(output_dir, name, raw_rgb, corrected_rgb, seg_rgb, seg_alpha):
    """Save comparison: Original (raw) | Original (ICC corrected) | Segmented | Diff heatmap."""
    h, w = raw_rgb.shape[:2]

    # Composite segmented on white
    alpha_f = seg_alpha.astype(np.float64) / 255.0
    seg_on_white = (
        seg_rgb.astype(np.float64) * alpha_f[:, :, None]
        + 255.0 * (1.0 - alpha_f[:, :, None])
    ).clip(0, 255).astype(np.uint8)

    # Diff between corrected original and segmented (what the browser shows)
    diff = np.abs(corrected_rgb.astype(np.float64) - seg_on_white.astype(np.float64))
    diff_amplified = np.clip(diff * 20, 0, 255).astype(np.uint8)

    gap = np.full((h, 3, 3), 180, dtype=np.uint8)
    comparison = np.hstack([
        raw_rgb, gap,
        corrected_rgb, gap,
        seg_on_white, gap,
        diff_amplified,
    ])

    label_h = 32
    label_bar = np.full((label_h, comparison.shape[1], 3), 255, dtype=np.uint8)
    comparison = np.vstack([label_bar, comparison])

    font = cv2.FONT_HERSHEY_SIMPLEX
    labels = ["Raw (PIL decode)", "ICC corrected", "Segmented PNG", "Diff (20x)"]
    x_offsets = [0, w + 3, 2 * (w + 3), 3 * (w + 3)]
    for label, x in zip(labels, x_offsets):
        cv2.putText(comparison, label, (x + 5, 22), font, 0.55, (0, 0, 0), 1, cv2.LINE_AA)

    out_path = os.path.join(output_dir, f"{name}_comparison.png")
    cv2.imwrite(out_path, cv2.cvtColor(comparison, cv2.COLOR_RGB2BGR))
    return out_path


def print_report(all_results):
    """Print aggregate statistics."""
    print("\n" + "=" * 70)
    print("AGGREGATE RESULTS")
    print("=" * 70)

    n = len(all_results)
    if n == 0:
        print("No results to report.")
        return

    # ICC profile summary
    profiles = [r["profile_name"] for r in all_results if r["has_profile"]]
    no_profile = sum(1 for r in all_results if not r["has_profile"])
    print(f"\nImages analyzed: {n}")
    print(f"  With ICC profile: {len(profiles)}")
    print(f"  Without ICC profile: {no_profile}")
    if profiles:
        from collections import Counter
        for name, count in Counter(profiles).most_common():
            print(f"    '{name}': {count} images")

    # Raw vs segmented. Pre-S92 this was ~0 (segment stripped ICC, so the PNG
    # matched the raw PIL decode). Post-S92 the segmented PNG is ICC-corrected,
    # so this now tracks icc_shift_mean instead — a healthy repo will show these
    # two values agree, not that raw_vs_seg is zero.
    raw_seg = [r["raw_vs_seg_mean"] for r in all_results]
    icc_mean = np.mean([r["icc_shift_mean"] for r in all_results])
    print(f"\nRaw PIL decode vs Segmented PNG (post-S92 should ≈ ICC shift below):")
    print(f"  Mean RGB diff: {np.mean(raw_seg):.3f}  (max across images: {max(r['raw_vs_seg_max'] for r in all_results)})")
    print(f"  (ICC shift for reference: {icc_mean:.3f})")

    # ICC shift (raw vs corrected)
    icc_shifts = [r["icc_shift_mean"] for r in all_results]
    print(f"\nICC profile correction shift (raw → sRGB):")
    print(f"  Mean RGB diff: min={min(icc_shifts):.2f}  max={max(icc_shifts):.2f}  mean={np.mean(icc_shifts):.2f}")
    r_shifts = [r["icc_shift_R_mean"] for r in all_results]
    g_shifts = [r["icc_shift_G_mean"] for r in all_results]
    b_shifts = [r["icc_shift_B_mean"] for r in all_results]
    print(f"  Per-channel mean shift:  R={np.mean(r_shifts):+.2f}  G={np.mean(g_shifts):+.2f}  B={np.mean(b_shifts):+.2f}")

    # Browser-visible shift (what the user sees)
    browser_shifts = [r["browser_shift_mean"] for r in all_results]
    print(f"\nBrowser-visible shift (ICC-corrected original vs untagged PNG):")
    print(f"  Mean RGB diff: min={min(browser_shifts):.2f}  max={max(browser_shifts):.2f}  mean={np.mean(browser_shifts):.2f}")
    br_shifts = [r["browser_shift_R_mean"] for r in all_results]
    bg_shifts = [r["browser_shift_G_mean"] for r in all_results]
    bb_shifts = [r["browser_shift_B_mean"] for r in all_results]
    print(f"  Per-channel mean shift:  R={np.mean(br_shifts):+.2f}  G={np.mean(bg_shifts):+.2f}  B={np.mean(bb_shifts):+.2f}")

    print("\n" + "=" * 70)
    if np.mean(browser_shifts) > 0.5:
        print("VERDICT: ICC profile stripping IS causing visible color shift.")
        print(f"  Average shift: {np.mean(browser_shifts):.1f} RGB levels per channel")
        print("  Fix: Apply ICC profile before saving segmented PNG,")
        print("       OR embed the profile in the output PNG.")
    elif len(profiles) > 0:
        print("VERDICT: ICC profiles found but shift is negligible.")
    else:
        print("VERDICT: No ICC profiles found — color shift has a different cause.")
    print("=" * 70)


def main(argv=None):
    global MAX_SAMPLES

    parser = argparse.ArgumentParser(description="Test ICC profile impact on segmented image colors")
    parser.add_argument("path", help="Archive directory path, or brand name with --scan")
    parser.add_argument("--scan", action="store_true", help="Treat path as brand name, auto-find archive")
    parser.add_argument("--samples", type=int, default=MAX_SAMPLES, help=f"Max images to test (default {MAX_SAMPLES})")
    args = parser.parse_args(argv)

    MAX_SAMPLES = args.samples

    if args.scan:
        brand = args.path.lower()
        archive_dir = find_archive_dir(brand)
        if not archive_dir:
            print(f"No archive with segmented images found for brand '{brand}' in {SEARCH_DIR}")
            sys.exit(1)
    else:
        archive_dir = args.path

    print(f"Archive directory: {archive_dir}")

    pairs = find_pairs(archive_dir)
    if not pairs:
        print("No matched original/segmented pairs found.")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_results = []
    for i, (orig_path, seg_path) in enumerate(pairs):
        name = os.path.splitext(os.path.basename(seg_path))[0]
        short_name = name[:50] + "..." if len(name) > 50 else name
        print(f"\n[{i+1}/{len(pairs)}] {short_name}")

        result = analyze_pair(orig_path, seg_path)
        if result is None:
            print("  Skipped")
            continue

        results, raw_rgb, corrected_rgb, seg_rgb, seg_alpha = result
        all_results.append(results)

        profile_str = f"'{results['profile_name']}'" if results["has_profile"] else "NONE"
        print(f"  ICC profile: {profile_str}")
        print(f"  Raw vs Segmented: {results['raw_vs_seg_mean']:.3f} mean diff (confirms PIL match)")
        print(f"  ICC correction shift: {results['icc_shift_mean']:.2f} mean diff")
        print(f"  Browser-visible shift: {results['browser_shift_mean']:.2f} mean diff"
              f"  (R:{results['browser_shift_R_mean']:+.1f}"
              f"  G:{results['browser_shift_G_mean']:+.1f}"
              f"  B:{results['browser_shift_B_mean']:+.1f})")

        comp_path = save_comparison(OUTPUT_DIR, f"sample_{i+1:02d}",
                                    raw_rgb, corrected_rgb, seg_rgb, seg_alpha)
        print(f"  Saved: {comp_path}")

    print_report(all_results)


if __name__ == "__main__":
    main()
