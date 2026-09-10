"""
ENTRY - run: python cli.py tune-hue-family-rows <brand> --gender G  (calibrate the a*b* hue-family row radius).

Sweeps the a*b* radius that groups zones into the palette explorer's row
scaffolding, so the value can be chosen by eye instead of guessed.

For each candidate radius, zones whose a*b* pairwise distances are all within R
(complete linkage, no chaining) collapse into one hue-family row; the rest stay
singletons. Grouping is delegated to find_ab_groups() in hue_family_grouping.py
- the same primitive palette_explorer.py uses - so the preview matches what the
flagship will render.

Reads one brand/gender segment (outputs/<brand>_<gender>/), written by
consolidate-colors + assign-zones. Writes its reports to a hue_family_tuning/
subfolder of that segment so diagnostics never mix with the production CSVs.

Usage:
    python cli.py tune-hue-family-rows
    python cli.py tune-hue-family-rows nike --gender mens
"""

import json
import csv
import math
import os

# =============================================================================
# CONFIGURATION
# =============================================================================

BRAND_DEFAULT = 'nike'
GENDER_DEFAULT = 'mens'

# The candidate radii rendered for comparison. 5 is what palette_explorer.py
# currently passes; the rest bracket it so over- and under-grouping are both
# visible in the same report.
RADII_SWEEP = [5, 7, 10, 14]

# Saturation-class boundaries used to order the preview's rows. Mirrors the
# values palette_explorer.py runs with (see its NEUTRAL_HSB/SATURATED_HSB) so
# the preview bands products the same way the flagship does. NOTE: shared/hsb.py
# carries a different NEUTRAL_HSB (12.0) for the coverage/archetype slices.
NEUTRAL_HSB = 5.0
SATURATED_HSB = 50.0

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Cluster + zone data live in outputs/<brand>_<gender>/, one subfolder per
# consolidate-colors run, so the path is built per-invocation from the args
# rather than fixed at import time.
OUTPUT_BASE = os.path.join(_SCRIPT_DIR, 'outputs')


def _segment_input_dir(brand, gender):
    """Path to one brand+gender run's cluster/zone data."""
    return os.path.join(OUTPUT_BASE, f'{brand}_{gender}')


def _segment_output_dir(brand, gender):
    """Path where this run's tuning reports are written.

    A subfolder of the segment (not the segment root) so the diagnostics stay
    separable from the production CSVs, while the thumbnails the preview links
    to remain one level up at a stable relative path.
    """
    return os.path.join(_segment_input_dir(brand, gender), 'hue_family_tuning')


# Thumbnails live in the segment root; the reports sit one level deeper.
THUMB_REL_PATH = '../thumbs'


def load_cluster_summary(path):
    clusters = {}
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = int(row['cluster_id'])
            clusters[cid] = {
                'lab_l': float(row['lab_l']),
                'lab_a': float(row['lab_a']),
                'lab_b': float(row['lab_b']),
                'n_products': int(row['n_products']),
            }
    return clusters


def load_zones(path):
    with open(path, 'r') as f:
        return json.load(f)['zones']


def compute_zone_centroids(zones, clusters):
    centroids = []
    for zone in zones:
        total_w = 0
        wL, wa, wb = 0.0, 0.0, 0.0
        for cid in zone['cluster_ids']:
            if cid not in clusters:
                continue
            c = clusters[cid]
            w = c['n_products']
            wL += c['lab_l'] * w
            wa += c['lab_a'] * w
            wb += c['lab_b'] * w
            total_w += w
        if total_w > 0:
            centroids.append({
                'L': wL / total_w, 'a': wa / total_w, 'b': wb / total_w,
                'n_products': total_w, 'name': zone['name'],
            })
        else:
            centroids.append({'L': 0, 'a': 0, 'b': 0, 'n_products': 0, 'name': zone['name']})
    return centroids


# The a*b* row-grouping primitive is shared with palette_explorer.py — single
# source of truth lives in hue_family_grouping.py so the explorer and this tuner
# never drift. (This tool exists to calibrate the `radius` it takes.)
from palette_explorer.hue_family_grouping import ab_distance, find_ab_groups


def lab_to_hex(L, a, b):
    fy = (L + 16) / 116
    fx = a / 500 + fy
    fz = fy - b / 200
    x = fx**3 if fx**3 > 0.008856 else (fx - 16/116) / 7.787
    y = fy**3 if fy**3 > 0.008856 else (fy - 16/116) / 7.787
    z = fz**3 if fz**3 > 0.008856 else (fz - 16/116) / 7.787
    x *= 0.95047
    z *= 1.08883
    r = x * 3.2406 + y * -1.5372 + z * -0.4986
    g = x * -0.9689 + y * 1.8758 + z * 0.0415
    bl = x * 0.0557 + y * -0.2040 + z * 1.0570
    def gamma(c):
        return 12.92 * c if c <= 0.0031308 else 1.055 * c**(1/2.4) - 0.055
    r, g, bl = gamma(r), gamma(g), gamma(bl)
    r = max(0, min(255, int(round(r * 255))))
    g = max(0, min(255, int(round(g * 255))))
    bl = max(0, min(255, int(round(bl * 255))))
    return f"#{r:02x}{g:02x}{bl:02x}"


def generate_report(output_path, centroids, radii):
    """Generate HTML report comparing multiple radii."""
    n = len(centroids)

    html = []
    html.append(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>a*b* Neighbor Grouping</title>
<style>
body {{ background: #1a1a1a; color: #ddd; font-family: system-ui, sans-serif; margin: 20px; }}
h1, h2, h3 {{ color: #fff; }}
.radius-section {{ margin: 32px 0; }}
.group {{ margin: 6px 0; display: flex; align-items: flex-start; gap: 8px; }}
.group-meta {{ min-width: 120px; font-size: 11px; color: #888; padding-top: 2px; }}
.swatch-row {{ display: flex; flex-wrap: wrap; gap: 2px; }}
.swatch {{ width: 32px; height: 32px; border-radius: 3px; position: relative; cursor: default;
           display: flex; align-items: center; justify-content: center; font-size: 9px; color: rgba(255,255,255,0.7); }}
.swatch:hover .tip {{ display: block; }}
.tip {{ display: none; position: absolute; bottom: 36px; left: 0; background: #333; color: #fff;
        padding: 4px 8px; border-radius: 4px; font-size: 11px; white-space: nowrap; z-index: 10; }}
.stats {{ background: #252525; padding: 12px; border-radius: 6px; margin: 12px 0; }}
.singleton-row {{ display: flex; flex-wrap: wrap; gap: 3px; margin: 8px 0; }}
.sep {{ border-top: 1px solid #333; margin: 20px 0; }}
</style>
</head><body>
<h1>a*b* Neighbor Grouping</h1>
<p>For each zone, find all zones within a*b* radius R. Connected zones form a group.<br>
Swatches sorted by L* (dark→light) within each group. L* value shown on swatch.</p>
""")

    for radius in radii:
        groups, singletons = find_ab_groups(centroids, radius)

        # Sort groups by size descending
        groups.sort(key=lambda g: -len(g))

        # Compute stats
        total_grouped = sum(len(g) for g in groups)
        max_ab_spreads = []
        max_L_ranges = []
        for g in groups:
            ab_dists = []
            for i in range(len(g)):
                for j in range(i+1, len(g)):
                    ab_dists.append(ab_distance(centroids[g[i]], centroids[g[j]]))
            max_ab_spreads.append(max(ab_dists) if ab_dists else 0)
            Ls = [centroids[zi]['L'] for zi in g]
            max_L_ranges.append(max(Ls) - min(Ls))

        html.append(f"""
<div class="radius-section">
<h2>R = {radius} (a*b* Euclidean)</h2>
<div class="stats">
    <strong>{len(groups)}</strong> groups containing <strong>{total_grouped}</strong> zones
    &nbsp;|&nbsp; <strong>{len(singletons)}</strong> singletons (no neighbors)
    &nbsp;|&nbsp; <strong>{n}</strong> total zones
</div>
""")

        for g_idx, g in enumerate(groups):
            # Sort by L*
            g_sorted = sorted(g, key=lambda zi: centroids[zi]['L'])

            # a*b* spread and L* range for this group
            ab_spread = max_ab_spreads[g_idx]
            L_range = max_L_ranges[g_idx]

            html.append(f'<div class="group">')
            html.append(f'<div class="group-meta">{len(g)} zones<br>'
                        f'a*b* spread: {ab_spread:.1f}<br>'
                        f'L* range: {L_range:.1f}</div>')
            html.append(f'<div class="swatch-row">')

            for zi in g_sorted:
                c = centroids[zi]
                hex_color = lab_to_hex(c['L'], c['a'], c['b'])
                # Text color: white for dark swatches, black for light
                text_color = "#fff" if c['L'] < 55 else "#000"
                html.append(
                    f'<div class="swatch" style="background:{hex_color}; color:{text_color}">'
                    f'{c["L"]:.0f}'
                    f'<div class="tip">{c["name"]}<br>a*={c["a"]:.1f} b*={c["b"]:.1f}<br>'
                    f'{c["n_products"]} products</div>'
                    f'</div>'
                )

            html.append(f'</div>')
            html.append(f'</div>')

        # Show singletons
        html.append(f'<div class="sep"></div>')
        html.append(f'<h3>{len(singletons)} Singletons (no a*b* neighbor within R={radius})</h3>')
        html.append(f'<div class="singleton-row">')
        for zi in sorted(singletons, key=lambda zi: centroids[zi]['L']):
            c = centroids[zi]
            hex_color = lab_to_hex(c['L'], c['a'], c['b'])
            text_color = "#fff" if c['L'] < 55 else "#000"
            html.append(
                f'<div class="swatch" style="background:{hex_color}; color:{text_color}">'
                f'{c["L"]:.0f}'
                f'<div class="tip">{c["name"]}<br>a*={c["a"]:.1f} b*={c["b"]:.1f}<br>'
                f'{c["n_products"]} products</div>'
                f'</div>'
            )
        html.append(f'</div>')
        html.append(f'</div>')

    html.append("</body></html>")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(html))
    print(f"HTML report: {output_path}")


def compute_group_centroid(group, centroids):
    """Weighted LAB centroid for a group of zone indices."""
    total_w = 0
    wL, wa, wb = 0.0, 0.0, 0.0
    for zi in group:
        c = centroids[zi]
        w = c['n_products']
        wL += c['L'] * w
        wa += c['a'] * w
        wb += c['b'] * w
        total_w += w
    if total_w == 0:
        return centroids[group[0]]['L'], centroids[group[0]]['a'], centroids[group[0]]['b']
    return wL / total_w, wa / total_w, wb / total_w


def compute_hsb_saturation(L, a, b):
    """LAB -> HSB saturation percentage."""
    fy = (L + 16) / 116
    fx = a / 500 + fy
    fz = fy - b / 200
    x = fx**3 if fx**3 > 0.008856 else (fx - 16/116) / 7.787
    y = fy**3 if fy**3 > 0.008856 else (fy - 16/116) / 7.787
    z = fz**3 if fz**3 > 0.008856 else (fz - 16/116) / 7.787
    x *= 0.95047
    z *= 1.08883
    r = x * 3.2406 + y * -1.5372 + z * -0.4986
    g = x * -0.9689 + y * 1.8758 + z * 0.0415
    bl = x * 0.0557 + y * -0.2040 + z * 1.0570
    def gamma(c):
        return 12.92 * c if c <= 0.0031308 else 1.055 * c**(1/2.4) - 0.055
    r, g, bl = max(0, min(1, gamma(r))), max(0, min(1, gamma(g))), max(0, min(1, gamma(bl)))
    mx = max(r, g, bl)
    mn = min(r, g, bl)
    return ((mx - mn) / mx * 100) if mx > 0 else 0


def scan_thumbnails(thumb_dir, zones):
    """Build zone_index -> list of thumbnail relative paths."""
    zone_thumbs = {}
    for zi, zone in enumerate(zones):
        thumbs = []
        for cid in zone['cluster_ids']:
            pattern = os.path.join(thumb_dir, f"{cid}_*.jpg")
            import glob
            files = sorted(glob.glob(pattern))
            for f in files:
                thumbs.append(os.path.basename(f))
        zone_thumbs[zi] = thumbs
    return zone_thumbs


def generate_column_preview(output_path, centroids, radius, zones, thumb_dir):
    """Generate a vertical column preview with clickable thumbnail panel."""
    groups, singletons = find_ab_groups(centroids, radius)

    # Scan thumbnails
    zone_thumbs = scan_thumbnails(thumb_dir, zones)

    # Build display items
    display_items = []
    for g in groups:
        cL, ca, cb = compute_group_centroid(g, centroids)
        display_items.append((cL, ca, cb, g, False))
    for zi in singletons:
        c = centroids[zi]
        display_items.append((c['L'], c['a'], c['b'], [zi], True))

    # HSB classification and ordering (module constants; see the note there)
    categorized = []
    for idx, (cL, ca, cb, zone_list, is_single) in enumerate(display_items):
        sat = compute_hsb_saturation(cL, ca, cb)
        hue = (math.degrees(math.atan2(cb, ca)) + 180) % 360

        if sat < NEUTRAL_HSB:
            categorized.append(('neutral', cL, idx))
        elif sat < SATURATED_HSB:
            categorized.append(('muted', hue, idx))
        else:
            categorized.append(('saturated', hue, idx))

    cat_order = {'neutral': 0, 'muted': 1, 'saturated': 2}
    categorized.sort(key=lambda x: (cat_order[x[0]], x[1]))
    ordered_indices = [c[2] for c in categorized]

    # Embed zone thumbnail data as JSON
    zone_thumbs_json = json.dumps(zone_thumbs)

    html = []
    html.append(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Column Preview (R={radius})</title>
<style>
* {{ box-sizing: border-box; }}
body {{ background: #1a1a1a; color: #ddd; font-family: system-ui, sans-serif; margin: 0; }}
.layout {{ display: flex; height: 100vh; }}
.col-panel {{ flex: 0 0 auto; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; align-items: center; }}
.thumb-panel {{ flex: 1; overflow-y: auto; padding: 20px; border-left: 1px solid #333;
                display: flex; flex-wrap: wrap; align-content: flex-start; gap: 4px; min-width: 400px; }}
.thumb-panel img {{ height: 200px; object-fit: contain; border-radius: 4px; cursor: pointer; }}
.thumb-panel .zone-label {{ width: 100%; font-size: 12px; color: #888; margin: 8px 0 2px 0;
                             border-bottom: 1px solid #333; padding-bottom: 4px; }}
.thumb-panel .empty {{ color: #555; font-style: italic; }}
h1 {{ color: #fff; font-size: 18px; margin: 0 0 8px 0; }}
.info {{ max-width: 500px; text-align: center; margin-bottom: 16px; color: #999; font-size: 12px; }}
.family-block {{ display: flex; align-items: center; gap: 8px; margin: 6px 0; }}
.swatches {{ display: flex; flex-direction: row; gap: 1px; }}
.swatch {{ width: 36px; height: 36px; border-radius: 3px; position: relative; cursor: pointer;
           display: flex; align-items: center; justify-content: center; font-size: 9px;
           border: 2px solid transparent; transition: border-color 0.1s; }}
.swatch:hover {{ border-color: #fff; }}
.swatch.active {{ border-color: #60a5fa; }}
.swatch .tip {{ display: none; position: absolute; left: 42px; top: 0; background: #333; color: #fff;
        padding: 4px 8px; border-radius: 4px; font-size: 11px; white-space: nowrap; z-index: 10; }}
.swatch:hover .tip {{ display: block; }}
.label {{ font-size: 10px; color: #666; min-width: 80px; text-align: right; }}
.singleton .swatches {{ opacity: 0.7; }}
.cat-label {{ font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: 2px;
              margin: 12px 0 4px 0; }}
</style>
</head><body>
<div class="layout">
<div class="col-panel">
<h1>Column Preview &mdash; R={radius}</h1>
<div class="info">Click a zone swatch to see product thumbnails. Swatches sorted dark&rarr;light.</div>
""")

    prev_cat = None
    prev_idx = None

    for pos, item_idx in enumerate(ordered_indices):
        cL, ca, cb, zone_indices, is_singleton = display_items[item_idx]

        cat = categorized[pos][0]
        if cat != prev_cat:
            html.append(f'<div class="cat-label">{cat}</div>')
            prev_cat = cat

        if prev_idx is not None:
            html.append(f'<div style="height:12px"></div>')

        zones_sorted = sorted(zone_indices, key=lambda zi: centroids[zi]['L'])

        # Row label describes the row itself: a singleton names its zone, a
        # family reports how many zones merged and over what lightness span -
        # the two things the radius is being judged on.
        if is_singleton:
            row_label = centroids[zone_indices[0]]['name']
        else:
            Ls = [centroids[zi]['L'] for zi in zone_indices]
            row_label = f'{len(zone_indices)} zones<br>L* {min(Ls):.0f}&ndash;{max(Ls):.0f}'

        css_class = "family-block singleton" if is_singleton else "family-block"

        html.append(f'<div class="{css_class}">')
        html.append(f'<div class="label">{row_label}</div>')
        html.append(f'<div class="swatches">')

        for zi in zones_sorted:
            c = centroids[zi]
            hex_color = lab_to_hex(c['L'], c['a'], c['b'])
            text_color = "#fff" if c['L'] < 55 else "#000"
            html.append(
                f'<div class="swatch" style="background:{hex_color}; color:{text_color}" '
                f'data-zi="{zi}" onclick="showThumbs({zi})">'
                f'{c["L"]:.0f}'
                f'<div class="tip">{c["name"]}<br>L*={c["L"]:.1f} a*={c["a"]:.1f} b*={c["b"]:.1f}<br>'
                f'{c["n_products"]} products</div>'
                f'</div>'
            )

        html.append(f'</div></div>')
        prev_idx = item_idx

    html.append(f"""
</div>
<div class="thumb-panel" id="thumbPanel">
    <div class="empty">Click a zone swatch to see product thumbnails</div>
</div>
</div>
<script>
var zoneThumbs = {zone_thumbs_json};
var thumbDir = '{THUMB_REL_PATH}';

function showThumbs(zi) {{
    // Highlight active swatch
    document.querySelectorAll('.swatch.active').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.swatch[data-zi="'+zi+'"]').forEach(el => el.classList.add('active'));

    var panel = document.getElementById('thumbPanel');
    var files = zoneThumbs[zi] || [];

    if (files.length === 0) {{
        panel.innerHTML = '<div class="empty">No thumbnails for this zone</div>';
        return;
    }}

    var html = '';
    for (var i = 0; i < files.length; i++) {{
        html += '<img src="' + thumbDir + '/' + files[i] + '" loading="lazy">';
    }}
    panel.innerHTML = html;
    panel.scrollTop = 0;
}}
</script>
</body></html>""")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(html))
    print(f"Column preview: {output_path}")


def main(brand=None, gender=None):
    brand = (brand or BRAND_DEFAULT).lower()
    gender = (gender or GENDER_DEFAULT).lower()

    input_dir = _segment_input_dir(brand, gender)
    output_dir = _segment_output_dir(brand, gender)

    print("=" * 70)
    print(f"HUE-FAMILY ROW TUNING - {brand.upper()} {gender.upper()}")
    print("=" * 70)

    summary_path = os.path.join(input_dir, 'cluster_summary.csv')
    zones_path = os.path.join(input_dir, 'cluster_zones.json')

    if not os.path.exists(summary_path):
        raise FileNotFoundError(
            f"cluster_summary.csv not found in {input_dir}. "
            f"Run: consolidate-colors {brand} --gender {gender}"
        )
    if not os.path.exists(zones_path):
        raise FileNotFoundError(
            f"cluster_zones.json not found in {input_dir}. "
            f"Run: assign-zones {brand} --gender {gender}"
        )

    clusters = load_cluster_summary(summary_path)
    zones = load_zones(zones_path)
    centroids = compute_zone_centroids(zones, clusters)

    print(f"  Loaded {len(clusters)} clusters, {len(zones)} zones")

    for r in RADII_SWEEP:
        groups, singletons = find_ab_groups(centroids, r)
        total_grouped = sum(len(g) for g in groups)
        print(f"\n  R={r:>2d}:  {len(groups):>3d} rows ({total_grouped} zones)  |  {len(singletons)} singletons")

    os.makedirs(output_dir, exist_ok=True)

    # Diagnostic report (all radii, groups listed by size)
    report_path = os.path.join(output_dir, 'ab_neighbor_diagnostic.html')
    generate_report(report_path, centroids, RADII_SWEEP)

    # Column preview (one per radius). Thumbnails stay in the segment root, so
    # the previews reference them one level up (THUMB_REL_PATH).
    thumb_dir = os.path.join(input_dir, 'thumbs')
    for r in RADII_SWEEP:
        preview_path = os.path.join(output_dir, f'ab_column_preview_R{r}.html')
        generate_column_preview(preview_path, centroids, r, zones, thumb_dir)

    print(f"\n{'=' * 70}")
    print(f"  Complete. Output: {output_dir}")
    print(f"{'=' * 70}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description='Sweep the a*b* hue-family row radius'
    )
    parser.add_argument('brand', nargs='?', default=None,
                        help=f'Brand (default: {BRAND_DEFAULT})')
    parser.add_argument('--gender', default=None,
                        help=f'Gender (default: {GENDER_DEFAULT})')
    args = parser.parse_args()
    main(brand=args.brand, gender=args.gender)
