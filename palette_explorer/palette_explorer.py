"""
ENTRY (FLAGSHIP) - run: python cli.py palette-explorer  -> palette_explorer.html, the primary deliverable.

Column preview visualization with discount/pricing heatmap overlay.

Supports multi-brand side-by-side comparison with synced scroll,
adjustable panel width, and per-brand collapsible thumbnail panels.

View modes:
  - Palette (default): zones colored by LAB centroid
  - Discount Frequency: diverging heatmap of freq deviation from baseline
  - Discount Depth: diverging heatmap of depth deviation from baseline
  - Price Level: diverging heatmap of price deviation from baseline
"""

import json
import csv
import math
import os
import re
import glob

try:
    from shared.db import connect_to_db as _connect_to_db
    _HAS_DB = True
except ImportError:
    _HAS_DB = False


# ---------------------------------------------------------------------------
# Guide slide SVGs
# ---------------------------------------------------------------------------

_GUIDE_SVG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'images')


def _embed_icon_svg(name):
    """Read a button icon SVG (close / prev / next) and rewrite its fills to
    `currentColor` so the icon inherits the button's color (hover-aware)."""
    path = os.path.join(_GUIDE_SVG_DIR, f'{name}.svg')
    if not os.path.exists(path):
        return f'<!-- missing {name}.svg -->'
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    content = re.sub(r'<\?xml[^>]*\?>\s*', '', content)
    scope = f'icon-svg-{name}'
    content = re.sub(r'id="Layer_2"', f'id="{scope}"', content, count=1)
    content = re.sub(r'\s+id="Layer_1-2"', '', content)
    content = re.sub(r'\s+data-name="Layer [12]"', '', content)
    content = re.sub(r'fill:\s*#[0-9a-fA-F]{3,6}', 'fill: currentColor', content)
    return content


def _embed_guide_svg(slide_num):
    """Read Asset N.svg, scope its CSS classes so multiple SVGs can coexist on
    the page without ID/class collisions, and return the inline markup."""
    path = os.path.join(_GUIDE_SVG_DIR, f'Asset {slide_num}.svg')
    if not os.path.exists(path):
        return f'<!-- missing Asset {slide_num}.svg -->'
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    content = re.sub(r'<\?xml[^>]*\?>\s*', '', content)
    scope = f'guide-svg-{slide_num}'
    content = re.sub(r'id="Layer_2"', f'id="{scope}"', content, count=1)
    content = re.sub(r'\s+id="Layer_1-2"', '', content)
    content = re.sub(r'\s+data-name="Layer [12]"', '', content)
    content = re.sub(
        r'\.cls-(\d+)\s*\{',
        lambda m: f'#{scope} .cls-{m.group(1)} {{',
        content)
    # Inject explicit pixel width/height from viewBox so each SVG keeps its
    # natural size; CSS max-width/max-height then scales down only as needed,
    # preserving the cross-slide scale relationships from the Illustrator exports.
    vb_match = re.search(
        r'<svg\b[^>]*\sviewBox="\s*[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)\s*"',
        content)
    if vb_match:
        # Scale intrinsic size 1.1x relative to the viewBox so each SVG renders
        # ~10% larger while CSS max-width/max-height still caps it inside the
        # modal's graphic zone.
        vb_w = float(vb_match.group(1)) * 1.1
        vb_h = float(vb_match.group(2)) * 1.1
        # Strip any existing width/height attrs on the root <svg> tag, then add ours.
        def _add_dims(m):
            tag = m.group(0)
            tag = re.sub(r'\s+(?:width|height|shape-rendering)="[^"]*"', '', tag)
            return tag[:4] + f' width="{vb_w:.2f}" height="{vb_h:.2f}" shape-rendering="geometricPrecision"' + tag[4:]
        content = re.sub(r'<svg\b[^>]*>', _add_dims, content, count=1)
    return content


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_cluster_summary(path):
    clusters = {}
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = int(row['cluster_id'])
            n_disc = int(float(row['n_discounted_depth'])) if row['n_discounted_depth'] else 0
            depth_pct = float(row['depth_pct']) if row['depth_pct'] else None
            clusters[cid] = {
                'lab_l': float(row['lab_l']),
                'lab_a': float(row['lab_a']),
                'lab_b': float(row['lab_b']),
                'n_products': int(row['n_products']),
                'n_discounted': n_disc,
                'depth_pct': depth_pct,
                'n_discounted_depth': n_disc,
            }
    return clusters


def load_zones(path):
    with open(path, 'r') as f:
        return json.load(f)['zones']


def load_run_metadata(path):
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        row = next(reader)
        return {
            'baseline_freq_pct': float(row['baseline_freq_pct']),
            'baseline_depth_pct': float(row['baseline_depth_pct']),
        }


def load_product_assignments(path):
    """Read product_assignments.csv. Returns list of dicts with pricing/discount data."""
    assignments = []
    if not os.path.exists(path):
        return assignments
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                price_std = float(row['price_std']) if row.get('price_std') else 0
            except (ValueError, TypeError):
                price_std = 0
            if price_std <= 0:
                continue
            try:
                cluster_id = int(row['cluster_id'])
            except (ValueError, TypeError):
                continue
            assignments.append({
                'cluster_id': cluster_id,
                'price_std': price_std,
            })
    return assignments


def load_archive_dates(brand_name):
    """Fetch archive dates from the database. Returns {archive_id: 'YYYY-MM-DD'} or None."""
    if not _HAS_DB:
        return None
    try:
        conn, cur = _connect_to_db(brand_name)
        cur.execute("SELECT archive_id, received FROM archive ORDER BY received")
        dates = {}
        for row in cur.fetchall():
            aid = int(row[0])
            received = row[1]
            if received:
                dates[aid] = str(received)[:10]  # YYYY-MM-DD
        conn.close()
        return dates if dates else None
    except Exception as e:
        print(f"  Warning: Could not load archive dates for {brand_name}: {e}")
        return None


def compute_temporal_data(zones, assignments_path, archive_dates):
    """Aggregate product_assignments.csv by (month, zone_index).

    Uses archive_dates to map archive_id -> YYYY-MM month.
    Returns {month_str: {zone_index: {n, disc, depth_sum, price_sum, n_priced}}}
    """
    if not os.path.exists(assignments_path) or not archive_dates:
        return {}

    # Build cluster_id -> zone_index mapping
    cluster_to_zone = {}
    for zi, zone in enumerate(zones):
        for cid in zone['cluster_ids']:
            cluster_to_zone[cid] = zi

    # Map archive_id -> month string
    archive_to_month = {}
    for aid, date_str in archive_dates.items():
        archive_to_month[aid] = date_str[:7]  # 'YYYY-MM-DD' -> 'YYYY-MM'

    temporal = {}
    with open(assignments_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                archive_id = int(row['archive_id'])
                cluster_id = int(row['cluster_id'])
            except (ValueError, TypeError, KeyError):
                continue
            month = archive_to_month.get(archive_id)
            if month is None:
                continue
            zi = cluster_to_zone.get(cluster_id)
            if zi is None:
                continue

            if month not in temporal:
                temporal[month] = {}
            if zi not in temporal[month]:
                temporal[month][zi] = {'n': 0, 'disc': 0, 'depth_sum': 0.0, 'price_sum': 0.0, 'n_priced': 0}

            entry = temporal[month][zi]
            entry['n'] += 1

            is_disc = row.get('is_discounted', '').lower() == 'true'
            if is_disc:
                entry['disc'] += 1
                try:
                    entry['depth_sum'] += float(row.get('discount_depth', 0))
                except (ValueError, TypeError):
                    pass

            try:
                price_std = float(row.get('price_std', 0))
                if price_std > 0:
                    entry['price_sum'] += price_std
                    entry['n_priced'] += 1
            except (ValueError, TypeError):
                pass

    return temporal


def load_brand_data(name, slug, output_dir, radius=5):
    """Load all data for one brand and return a dict."""
    summary_path = os.path.join(output_dir, 'cluster_summary.csv')
    zones_path = os.path.join(output_dir, 'cluster_zones.json')
    metadata_path = os.path.join(output_dir, 'run_metadata.csv')
    assignments_path = os.path.join(output_dir, 'product_assignments.csv')
    thumb_dir = os.path.join(output_dir, 'thumbs')

    clusters = load_cluster_summary(summary_path)
    zones = load_zones(zones_path)
    baseline = load_run_metadata(metadata_path)
    centroids = compute_zone_centroids(zones, clusters)
    zone_stats = compute_zone_discount_stats(zones, clusters, baseline)
    zone_thumbs = scan_thumbnails(thumb_dir, zones, clusters)

    # Price analysis from product assignments
    assignments = load_product_assignments(assignments_path)
    all_prices = [a['price_std'] for a in assignments]
    baseline_price = sum(all_prices) / len(all_prices) if all_prices else 0
    zone_price_stats = compute_zone_price_stats(zones, assignments, baseline_price)

    # Temporal data
    brand_name = slug.split('_')[0]  # 'nike_mens' -> 'nike'
    archive_dates = load_archive_dates(brand_name)
    temporal_data = compute_temporal_data(zones, assignments_path, archive_dates)

    return {
        'name': name,
        'slug': slug,
        'clusters': clusters,
        'zones': zones,
        'centroids': centroids,
        'zone_stats': zone_stats,
        'zone_price_stats': zone_price_stats,
        'baseline': {**baseline, 'baseline_price': round(baseline_price, 2)},
        'zone_thumbs': zone_thumbs,
        'thumb_dir': thumb_dir,
        'radius': radius,
        'archive_dates': archive_dates,
        'temporal_data': temporal_data,
    }


# ---------------------------------------------------------------------------
# Zone-level computation
# ---------------------------------------------------------------------------

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
            L, a, b = wL / total_w, wa / total_w, wb / total_w
            centroids.append({
                'L': L, 'a': a, 'b': b,
                'n_products': total_w, 'name': zone['name'],
                'hex': lab_to_hex(L, a, b),
            })
        else:
            centroids.append({
                'L': 0, 'a': 0, 'b': 0, 'n_products': 0, 'name': zone['name'],
                'hex': '#000000',
            })
    return centroids


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def compute_zone_discount_stats(zones, clusters, baseline):
    baseline_freq = baseline['baseline_freq_pct']
    baseline_depth = baseline['baseline_depth_pct']
    p0 = baseline_freq / 100.0

    stats = {}
    for zi, zone in enumerate(zones):
        total_products = 0
        total_discounted = 0
        depth_weighted_sum = 0.0
        depth_weight_total = 0

        for cid in zone['cluster_ids']:
            if cid not in clusters:
                continue
            c = clusters[cid]
            total_products += c['n_products']
            total_discounted += c['n_discounted']
            if c['depth_pct'] is not None and c['n_discounted_depth'] > 0:
                depth_weighted_sum += c['depth_pct'] * c['n_discounted_depth']
                depth_weight_total += c['n_discounted_depth']

        if total_products > 0:
            freq_pct = total_discounted / total_products * 100
            freq_dev = freq_pct - baseline_freq
            p_hat = total_discounted / total_products
            se = math.sqrt(p0 * (1 - p0) / total_products) if 0 < p0 < 1 else 1e-10
            z = (p_hat - p0) / se
            p_val = 2 * (1 - _norm_cdf(abs(z)))
            freq_sig = p_val < 0.05
        else:
            freq_pct = freq_dev = 0
            freq_sig = False

        has_depth = depth_weight_total > 0
        if has_depth:
            depth_pct = depth_weighted_sum / depth_weight_total
            depth_dev = depth_pct - baseline_depth
        else:
            depth_pct = depth_dev = 0

        stats[zi] = {
            'n_products': total_products, 'n_discounted': total_discounted,
            'freq_pct': round(freq_pct, 2), 'freq_deviation_pp': round(freq_dev, 2),
            'freq_significant': freq_sig,
            'depth_pct': round(depth_pct, 2), 'depth_deviation_pp': round(depth_dev, 2),
            'has_depth': has_depth,
        }
    return stats


def compute_zone_price_stats(zones, assignments, baseline_price):
    """Compute per-zone price level statistics from product assignments."""
    if not assignments or baseline_price <= 0:
        return {}

    # Build cluster_id -> list of prices
    cluster_prices = {}
    for a in assignments:
        cluster_prices.setdefault(a['cluster_id'], []).append(a['price_std'])

    stats = {}
    for zi, zone in enumerate(zones):
        prices = []
        for cid in zone['cluster_ids']:
            if cid in cluster_prices:
                prices.extend(cluster_prices[cid])
        n_priced = len(prices)
        if n_priced == 0:
            continue
        price_mean = sum(prices) / n_priced
        price_dev_pct = (price_mean - baseline_price) / baseline_price * 100

        # One-sample t-test against baseline_price (normal approximation)
        price_sig = False
        if n_priced >= 14:
            variance = sum((p - price_mean) ** 2 for p in prices) / (n_priced - 1) if n_priced > 1 else 0
            if variance > 0:
                se = math.sqrt(variance / n_priced)
                t = (price_mean - baseline_price) / se
                # Two-tailed p-value via normal approximation (good for n >= 14)
                p_val = 2 * (1 - _norm_cdf(abs(t)))
                price_sig = p_val < 0.05

        stats[zi] = {
            'price_mean': round(price_mean, 2),
            'price_deviation_pct': round(price_dev_pct, 2),
            'price_significant': price_sig,
            'n_priced': n_priced,
        }
    return stats


# ---------------------------------------------------------------------------
# Color / geometry helpers
# ---------------------------------------------------------------------------

# a*b* row-grouping primitive extracted to hue_family_grouping.py (single source
# of truth, shared with tune_hue_family_rows.py which calibrates the radius).
from palette_explorer.hue_family_grouping import ab_distance, find_ab_groups


def lab_to_hex(L, a, b):
    fy = (L + 16) / 116
    fx = a / 500 + fy
    fz = fy - b / 200
    x = fx**3 if fx**3 > 0.008856 else (fx - 16/116) / 7.787
    y = fy**3 if fy**3 > 0.008856 else (fy - 16/116) / 7.787
    z = fz**3 if fz**3 > 0.008856 else (fz - 16/116) / 7.787
    x *= 0.95047; z *= 1.08883
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


def compute_group_centroid(group, centroids):
    total_w = 0
    wL, wa, wb = 0.0, 0.0, 0.0
    for zi in group:
        c = centroids[zi]
        w = c['n_products']
        wL += c['L'] * w; wa += c['a'] * w; wb += c['b'] * w
        total_w += w
    if total_w == 0:
        return centroids[group[0]]['L'], centroids[group[0]]['a'], centroids[group[0]]['b']
    return wL / total_w, wa / total_w, wb / total_w


def compute_hsb_saturation(L, a, b):
    fy = (L + 16) / 116
    fx = a / 500 + fy
    fz = fy - b / 200
    x = fx**3 if fx**3 > 0.008856 else (fx - 16/116) / 7.787
    y = fy**3 if fy**3 > 0.008856 else (fy - 16/116) / 7.787
    z = fz**3 if fz**3 > 0.008856 else (fz - 16/116) / 7.787
    x *= 0.95047; z *= 1.08883
    r = x * 3.2406 + y * -1.5372 + z * -0.4986
    g = x * -0.9689 + y * 1.8758 + z * 0.0415
    bl = x * 0.0557 + y * -0.2040 + z * 1.0570
    def gamma(c):
        return 12.92 * c if c <= 0.0031308 else 1.055 * c**(1/2.4) - 0.055
    r, g, bl = max(0, min(1, gamma(r))), max(0, min(1, gamma(g))), max(0, min(1, gamma(bl)))
    mx = max(r, g, bl); mn = min(r, g, bl)
    return ((mx - mn) / mx * 100) if mx > 0 else 0


# ---------------------------------------------------------------------------
# Thumbnail scanning
# ---------------------------------------------------------------------------

def scan_thumbnails(thumb_dir, zones, clusters):
    zone_thumbs = {}
    for zi, zone in enumerate(zones):
        cluster_thumbs = []
        for cid in zone['cluster_ids']:
            if cid not in clusters:
                continue
            c = clusters[cid]
            files = sorted(glob.glob(os.path.join(thumb_dir, f"{cid}_*.jpg")))
            if files:
                lab = (c['lab_l'], c['lab_a'], c['lab_b'])
                cluster_thumbs.append((lab, [os.path.basename(f) for f in files]))
        if not cluster_thumbs:
            zone_thumbs[zi] = []; continue
        if len(cluster_thumbs) == 1:
            zone_thumbs[zi] = cluster_thumbs[0][1]; continue

        remaining = list(range(len(cluster_thumbs)))
        start = min(remaining, key=lambda i: cluster_thumbs[i][0][0])
        order = [start]; remaining.remove(start)
        def _lab_dist(a, b):
            return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
        while remaining:
            farthest = max(remaining, key=lambda i: min(
                _lab_dist(cluster_thumbs[i][0], cluster_thumbs[j][0]) for j in order))
            order.append(farthest); remaining.remove(farthest)
        result = []
        max_rank = max(len(cluster_thumbs[i][1]) for i in order)
        for rank in range(max_rank):
            for ci in order:
                thumbs = cluster_thumbs[ci][1]
                if rank < len(thumbs):
                    result.append(thumbs[rank])
        zone_thumbs[zi] = result
    return zone_thumbs


# ---------------------------------------------------------------------------
# Layout computation (per-brand)
# ---------------------------------------------------------------------------

TRACK_WIDTH = 800
BASE_SWATCH = 36
MAX_SIZE = 600
NEUTRAL_HSB = 5.0
SATURATED_HSB = 50.0


def compute_swatch_layout(centroids, radius):
    """Compute swatch positions and a*b* grouping for one brand.

    Returns (all_swatches, cat_labels, total_height, display_items_js, max_products).
    display_items_js is the a*b* group structure for JS relayout.
    """
    ab_groups, singletons = find_ab_groups(centroids, radius)

    display_items = []
    display_items_js = []
    for g in ab_groups:
        display_items.append(g)
        display_items_js.append({'zones': g, 'isSingle': False})
    for zi in singletons:
        display_items.append([zi])
        display_items_js.append({'zones': [zi], 'isSingle': True})

    max_products = max((c['n_products'] for c in centroids if c['n_products'] > 0), default=1)

    def scaled_size(n):
        return MAX_SIZE * math.sqrt(n / max_products) if n > 0 else 1

    def group_centroid(zone_indices):
        total_w = 0
        wL = wa = wb = 0.0
        for zi in zone_indices:
            c = centroids[zi]
            w = c['n_products']
            wL += c['L'] * w; wa += c['a'] * w; wb += c['b'] * w
            total_w += w
        if total_w == 0:
            c = centroids[zone_indices[0]]
            return c['L'], c['a'], c['b']
        return wL / total_w, wa / total_w, wb / total_w

    categorized = []
    for idx, zone_indices in enumerate(display_items):
        cL, ca, cb = group_centroid(zone_indices)
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

    all_swatches = []
    cat_labels = []
    gap = 4
    min_spacing = BASE_SWATCH + gap
    MIN_ROW_HEIGHT = 48
    ROW_SCALE = 0.6

    y_cursor = 0
    prev_cat = None

    for pos, item_idx in enumerate(ordered_indices):
        zone_indices = display_items[item_idx]
        cat = categorized[pos][0]
        if cat != prev_cat:
            if prev_cat is not None:
                y_cursor += 12
            cat_labels.append((y_cursor, cat))
            y_cursor += 56
            prev_cat = cat

        zones_by_L = sorted(zone_indices, key=lambda zi: centroids[zi]['L'])
        ideals = [centroids[zi]['L'] / 100.0 * (TRACK_WIDTH - BASE_SWATCH) for zi in zones_by_L]

        positions = []
        cursor = 0
        for ideal in ideals:
            actual = max(ideal, cursor)
            positions.append(actual)
            cursor = actual + min_spacing

        if len(positions) > 1:
            overlap_groups = []
            current = [0]
            for i in range(1, len(positions)):
                if ideals[i] < positions[i - 1] + min_spacing:
                    current.append(i)
                else:
                    overlap_groups.append(current)
                    current = [i]
            overlap_groups.append(current)
            for grp in overlap_groups:
                if len(grp) < 2:
                    continue
                mean_ideal = sum(ideals[i] for i in grp) / len(grp)
                total_w = (len(grp) - 1) * min_spacing
                start = mean_ideal - total_w / 2
                if grp[0] > 0:
                    prev_right = positions[grp[0] - 1] + min_spacing
                    start = max(start, prev_right)
                start = max(start, 0)
                for k, idx in enumerate(grp):
                    positions[idx] = start + k * min_spacing

        zone_sizes = [scaled_size(centroids[zi]['n_products']) for zi in zones_by_L]
        max_zone_size = max(zone_sizes) if zone_sizes else 0
        row_height = max(MIN_ROW_HEIGHT, max_zone_size * ROW_SCALE)

        for i, zi in enumerate(zones_by_L):
            c = centroids[zi]
            x_center = positions[i] + BASE_SWATCH / 2
            y_center = y_cursor + row_height / 2
            hex_color = lab_to_hex(c['L'], c['a'], c['b'])
            all_swatches.append((x_center, y_center, zone_sizes[i], zi, hex_color, c))

        y_cursor += row_height

    total_height = y_cursor + 50
    all_swatches.sort(key=lambda s: -s[2])
    return all_swatches, cat_labels, total_height, display_items_js, max_products


def render_brand_swatches(slug, all_swatches, cat_labels, centroids):
    """Generate HTML strings for one brand's swatch divs."""
    html = []
    for y, text in cat_labels:
        html.append(f'<div class="cat-label" style="top:{y}px;"><span>{text}</span></div>')

    dot_pct = 38
    dot_offset = (100 - dot_pct) / 2
    for rank, (x_center, y_center, size, zi, hex_color, c) in enumerate(all_swatches):
        left = x_center - size / 2
        top = y_center - size / 2
        z = rank + 1
        outline_L = 100 - c['L']
        outline_hex = lab_to_hex(outline_L, 0, 0)
        html.append(
            f'<div class="swatch" style="background:{hex_color}; --ol:{outline_hex}; '
            f'left:{left:.1f}px; top:{top:.1f}px; width:{size:.0f}px; height:{size:.0f}px; '
            f'z-index:{z}; border-radius:50%;" '
            f'data-brand="{slug}" data-zi="{zi}" data-hex="{hex_color}" '
            f"onclick=\"handleClick(event,'{slug}',{zi})\" "
            f"onmouseenter=\"handleHover('{slug}',{zi})\" "
            f"onmouseleave=\"handleHoverOut('{slug}')\">"
            f'<div class="centroid-dot" style="background:{hex_color}; '
            f'width:{dot_pct}%; height:{dot_pct}%; left:{dot_offset}%; top:{dot_offset}%;"></div>'
            f'<div class="tip"></div>'
            f'</div>'
        )
    return html


# ---------------------------------------------------------------------------
# Multi-brand HTML generator
# ---------------------------------------------------------------------------

def generate_multi_brand_preview(output_path, brands_data, global_freq_max, global_depth_max, global_price_max):
    """Generate multi-brand side-by-side comparison HTML."""

    # Pre-compute layouts per brand
    brand_layouts = {}
    for bd in brands_data:
        swatches, labels, height, display_items_js, max_products = compute_swatch_layout(
            bd['centroids'], bd['radius'])
        brand_layouts[bd['slug']] = {
            'swatches': swatches, 'labels': labels, 'height': height,
            'displayItems': display_items_js, 'maxProducts': max_products,
        }

    # Global max products across all brands (for cross-brand comparable sizing)
    global_max_products = max(bl['maxProducts'] for bl in brand_layouts.values())

    # Serialize per-brand JS data
    brands_js = {}
    for bd in brands_data:
        zone_centroids_js = {str(zi): {
            'name': c['name'], 'L': round(c['L'], 1), 'a': round(c['a'], 1),
            'b': round(c['b'], 1), 'n_products': c['n_products']
        } for zi, c in enumerate(bd['centroids'])}

        # Compact temporal data: {month_str: {zone_idx_str: {n, disc, depth_sum, price_sum, n_priced}}}
        temporal_js = {}
        if bd.get('temporal_data'):
            for month, zone_data in bd['temporal_data'].items():
                temporal_js[month] = {str(zi): d for zi, d in zone_data.items()}

        brands_js[bd['slug']] = {
            'name': bd['name'],
            'zoneThumbs': bd['zone_thumbs'],
            'zoneStats': {str(k): v for k, v in bd['zone_stats'].items()},
            'zonePriceStats': {str(k): v for k, v in bd['zone_price_stats'].items()},
            'baseline': bd['baseline'],
            'zoneCentroids': zone_centroids_js,
            'thumbDir': bd['slug'] + '/thumbs',
            'baseW': TRACK_WIDTH,
            'baseH': brand_layouts[bd['slug']]['height'],
            'displayItems': brand_layouts[bd['slug']]['displayItems'],
            'maxProducts': brand_layouts[bd['slug']]['maxProducts'],
            'temporal': temporal_js,
        }

    brands_json = json.dumps(brands_js)
    brand_slugs = [bd['slug'] for bd in brands_data]
    brand_slugs_json = json.dumps(brand_slugs)

    # Global sorted months (union across all brands' temporal data)
    all_months = set()
    for bd in brands_data:
        if bd.get('temporal_data'):
            all_months.update(bd['temporal_data'].keys())
    sorted_months = sorted(all_months)
    months_json = json.dumps(sorted_months)
    has_temporal = len(sorted_months) > 1

    html = []

    # --- CSS ---
    html.append(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Multi-Brand Color Comparison</title>
<style>
* {{ box-sizing: border-box; scrollbar-width: thin;
     scrollbar-color: rgba(255,255,255,0.15) transparent; }}
::-webkit-scrollbar {{ width: 6px; height: 6px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,0.15); border-radius: 3px; }}
::-webkit-scrollbar-thumb:hover {{ background: rgba(255,255,255,0.3); }}
body {{ background: #1a1a1a; color: #ddd; font-family: system-ui, sans-serif; margin: 0;
       display: flex; flex-direction: row; height: 100vh; overflow: hidden; }}

.sidebar {{ flex: 0 0 var(--sidebar-width, 360px); display: flex; flex-direction: column;
            overflow: hidden; background: #1e1e1e; border-right: 1px solid #333; }}

.compat-overlay {{ position: fixed; inset: 0; background: rgba(0,0,0,0.75); z-index: 30000;
                   display: flex; align-items: center; justify-content: center; }}
.compat-overlay.hidden {{ display: none; }}
.compat-modal {{ background: #1e1e1e; border: 1px solid #444; border-radius: 10px;
                 padding: 28px 34px; max-width: 420px; width: 90%; position: relative;
                 box-shadow: 0 16px 48px rgba(0,0,0,0.7); text-align: center; }}
.compat-modal h2 {{ color: #fff; font-size: 18px; margin: 0 0 12px 0; }}
.compat-modal p {{ font-size: 14px; color: #999; line-height: 1.5; margin: 0 0 8px 0; }}
.compat-modal button {{ margin-top: 16px; background: #333; border: 1px solid #555;
                        color: #ddd; padding: 8px 24px; border-radius: 4px; cursor: pointer;
                        font-size: 13px; }}
.compat-modal button:hover {{ background: #444; }}
.sidebar-scroll {{ flex: 1; overflow-y: auto; }}
.sidebar-section {{ padding: 30px 26px; border-bottom: 1px solid #2a2a2a;
                     display: flex; flex-direction: column; gap: 12px; }}

.main-area {{ flex: 1; display: flex; flex-direction: column; overflow: hidden; }}

.ctrl-panel {{ display: flex; flex-direction: column; gap: 26px; align-items: stretch; }}
.ctrl-panel-header {{ margin-bottom: 4px; display: flex; flex-direction: column; gap: 4px;
                      padding-left: 10px; border-left: 2px solid #60a5fa;
                      cursor: pointer; user-select: none; }}
.ctrl-panel-label {{ font-size: 13px; font-weight: 700; color: #bbb; text-transform: uppercase;
                     letter-spacing: 1.5px; display: flex; align-items: center;
                     justify-content: space-between; }}
.ctrl-panel-chevron {{ font-size: 12px; color: #555; transition: transform 0.2s; }}
.sidebar-section.collapsed .ctrl-panel-chevron {{ transform: rotate(-90deg); }}
.ctrl-panel-body {{ display: flex; flex-direction: column; gap: 26px; }}
.sidebar-section.collapsed .ctrl-panel-body {{ display: none; }}
.sidebar-section.collapsed {{ padding-bottom: 20px; }}
.ctrl-panel-desc {{ font-size: 13px; color: #666; line-height: 1.4; }}
.ctrl-panel-content {{ display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }}
.ctrl-panel-row {{ display: flex; flex-direction: column; gap: 4px; }}
.ctrl-panel-row-label {{ font-size: 12px; color: #555; text-transform: uppercase; letter-spacing: 1px; }}

.view-bar {{ display: flex; flex-direction: column; gap: 6px; }}
.view-option {{ display: flex; align-items: center; gap: 10px; padding: 14px 30px 16px 10px;
                border: 1px solid #333; border-radius: 4px; cursor: pointer;
                background: #252525; transition: all 0.15s; }}
.view-option:hover {{ background: #2a2a2a; border-color: #555; }}
.view-option.active {{ background: #2a2a3a; border-color: #60a5fa; }}
.view-option-radio {{ width: 8px; height: 8px; border-radius: 50%; border: 2px solid #555;
                      flex-shrink: 0; transition: all 0.15s; }}
.view-option.active .view-option-radio {{ border-color: #60a5fa; background: #60a5fa; }}
.view-option-text {{ display: flex; flex-direction: column; gap: 2px; }}
.view-option-name {{ font-size: 14px; color: #ddd; font-weight: 600; }}
.view-option.active .view-option-name {{ color: #fff; }}
.view-option-desc {{ font-size: 12px; color: #666; line-height: 1.3; }}
.view-option.active .view-option-desc {{ color: #888; }}

.colorbar-wrap {{ display: none; flex-direction: column; gap: 10px; }}
.colorbar-wrap.visible {{ display: flex; }}
.colorbar-title {{ font-size: 12px; color: #555; text-transform: uppercase; letter-spacing: 1px; }}
.colorbar-bar {{ display: flex; align-items: center; gap: 8px; }}
.colorbar-label {{ font-size: 13px; color: #999; min-width: 32px; }}
.colorbar-label.right {{ text-align: right; }}
.colorbar-canvas {{ border-radius: 2px; }}

.zoom-bar {{ display: flex; gap: 6px; align-items: center; }}
.zoom-bar button {{ background: #333; color: #ddd; border: 1px solid #555; padding: 3px 10px;
                    cursor: pointer; border-radius: 3px; font-size: 13px; }}
.zoom-bar button:hover {{ background: #444; }}
.zoom-level {{ font-size: 14px; color: #999; min-width: 40px; text-align: center; }}

.width-control {{ display: flex; align-items: center; gap: 6px; }}
.width-control .label {{ font-size: 13px; color: #777; }}
.width-control input[type=range] {{ -webkit-appearance: none; appearance: none;
    width: 100%; min-width: 60px; flex: 1; height: 14px; background: transparent; }}
.width-control input[type=range]::-webkit-slider-runnable-track {{ height: 4px;
    background: #444; border-radius: 2px; }}
.width-control input[type=range]::-webkit-slider-thumb {{ -webkit-appearance: none;
    width: 14px; height: 14px; border-radius: 50%; background: #ccc; cursor: pointer;
    border: none; margin-top: -5px; }}
.width-control input[type=range]::-moz-range-track {{ height: 4px;
    background: #444; border-radius: 2px; border: none; }}
.width-control input[type=range]::-moz-range-thumb {{ width: 14px; height: 14px;
    border-radius: 50%; background: #ccc; cursor: pointer; border: none; }}
.width-control .val {{ font-size: 13px; color: #999; min-width: 36px; }}

.brand-toggles {{ display: flex; flex-direction: column; gap: 4px; }}
.brand-toggle {{ display: flex; align-items: center; gap: 8px; background: #2a2a2a;
                 color: #888; border: 1px solid #333; padding: 6px 10px;
                 cursor: pointer; border-radius: 4px; font-size: 13px; transition: all 0.15s; }}
.brand-toggle:hover {{ background: #333; border-color: #555; }}
.brand-toggle.active {{ background: #2a2a3a; border-color: #60a5fa; color: #ddd; }}
.brand-toggle-dot {{ width: 8px; height: 8px; border-radius: 50%; border: 2px solid #555;
                     flex-shrink: 0; transition: all 0.15s; }}
.brand-toggle.active .brand-toggle-dot {{ border-color: #60a5fa; background: #60a5fa; }}
.brand-toggle-name {{ font-weight: 600; }}
.brand-toggle-stat {{ margin-left: auto; font-size: 12px; color: #555; }}
.brand-toggle.active .brand-toggle-stat {{ color: #666; }}
.brand-actions {{ display: flex; gap: 10px; margin-top: 16px; }}
.brand-actions a {{ font-size: 12px; color: #60a5fa; cursor: pointer; text-decoration: none; }}
.brand-actions a:hover {{ text-decoration: underline; }}

/* --- Selection Tools panel --- */
.sel-clear-btn {{ background: #2a2a2a; color: #ddd; border: 1px solid #444; padding: 6px 12px;
                  cursor: pointer; border-radius: 4px; font-size: 12px; align-self: flex-start; }}
.sel-clear-btn:hover {{ background: #3a2a2a; border-color: #b2182b; }}
.sel-note {{ font-size: 11px; color: #666; line-height: 1.4; }}
.sel-stats {{ display: flex; flex-direction: column; gap: 10px; width: 100%; }}
.sel-total {{ font-size: 12px; color: #fbbf24; font-weight: 600; }}
.sel-empty {{ font-size: 12px; color: #666; line-height: 1.4; }}
.sel-brand-row {{ display: flex; flex-direction: column; gap: 4px; }}
.sel-brand-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }}
.sel-brand-name {{ font-size: 12px; color: #ddd; font-weight: 600; }}
.sel-brand-stat {{ font-size: 11px; color: #777; }}
.sel-chips {{ display: flex; flex-wrap: wrap; gap: 2px; }}
.sel-chip {{ width: 12px; height: 12px; border-radius: 2px; border: 1px solid rgba(255,255,255,0.15); }}

.info-bar {{ width: 100%; padding: 0 20px; background: #1e1e1e; border-bottom: 1px solid #333;
            font-size: 12px; color: #555; height: 36px; flex: 0 0 36px; position: relative;
            display: flex; align-items: center; justify-content: center; gap: 10px; }}
.info-bar .ib-swatch {{ width: 14px; height: 14px; border-radius: 3px; flex-shrink: 0; }}
.info-bar .ib-brand {{ font-weight: 600; text-transform: uppercase; font-size: 11px; }}
.info-bar .ib-name {{ font-weight: 600; }}
.info-bar .ib-sep {{ color: #555; }}
.info-bar .ib-dim {{ }}
.info-bar .ib-clear {{ cursor: pointer; font-size: 11px; text-decoration: underline;
                       position: absolute; right: 20px; }}

.panels-container {{ display: flex; flex: 1 1 0; overflow-x: auto; overflow-y: hidden; min-height: 0; }}

.brand-panel {{ flex: 0 0 var(--panel-width, 440px); display: flex; flex-direction: row;
                min-width: 0; }}
.brand-panel.hidden {{ display: none; }}
.brand-panel-content {{ flex: 1; display: flex; flex-direction: column; min-width: 0; overflow: hidden; }}
.panel-resize {{ flex: 0 0 5px; cursor: col-resize; background: #333; }}
.panel-resize:hover, .panel-resize.dragging {{ background: #60a5fa; }}
.brand-label {{ text-align: center; font-size: 13px; font-weight: 700; color: #fff;
                padding: 6px 0; background: #222; border-bottom: 1px solid #333; flex: 0 0 auto;
                text-transform: capitalize; letter-spacing: 1px; }}
.brand-scroll {{ flex: 1; overflow: auto; padding: 10px; display: flex; justify-content: center; }}
.zoom-container {{ position: relative; }}
.canvas {{ position: relative; width: {TRACK_WIDTH}px; transform-origin: 0 0; }}
.cat-label {{ position: absolute; left: 0; right: 0; padding: 14px 0;
              display: flex; align-items: center; justify-content: center;
              background: linear-gradient(to right, transparent, rgba(255,255,255,0.06) 20%, rgba(255,255,255,0.06) 80%, transparent); }}
.cat-label::before {{ content: ''; position: absolute; left: 0; right: 0; top: 50%;
                      border-top: 1px solid rgba(255,255,255,0.12); }}
.cat-label span {{ position: relative; background: #222; color: #aaa; font-size: 20px;
                   font-weight: 700; text-transform: uppercase; letter-spacing: 4px;
                   padding: 5px 20px; border-radius: 12px; border: 2px solid rgba(255,255,255,0.15); }}
.swatch {{ position: absolute; cursor: pointer;
           border-style: solid; border-color: var(--ol, transparent);
           border-width: 0.5px; transition: border-color 0.1s; }}
.swatch:hover {{ border-color: #fff; }}
.swatch.active {{ border-color: #60a5fa; }}
.swatch.selected {{ border-color: #fbbf24; border-width: 2px !important; }}
.swatch .tip {{ display: none; }}
.centroid-dot {{ display: none; position: absolute; border-radius: 50%; pointer-events: none; }}

.thumb-section {{ flex: 0 0 auto; border-top: 1px solid #333; }}
.thumb-detail {{ display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px;
                 padding: 8px 10px; background: #222; border-bottom: 1px solid #2a2a2a;
                 font-size: 10px; color: #888; min-height: 20px; }}
.thumb-detail:empty {{ display: none; }}
.thumb-detail .td-swatch {{ width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; }}
.thumb-detail .td-name {{ font-weight: 600; color: #ccc; }}
.thumb-detail .td-stat {{ color: #777; }}
.thumb-toggle {{ display: flex; align-items: center; justify-content: space-between;
                 width: 100%; padding: 10px 10px; background: #252525; color: #999;
                 border: none; cursor: pointer; font-size: 11px; text-align: left; }}
.thumb-right {{ margin-left: auto; }}
.thumb-toggle:hover {{ background: #2a2a2a; color: #bbb; }}
.thumb-brand {{ font-size: 18px; font-weight: 700; color: #fff; letter-spacing: 0.5px;
                padding: 0 5px; }}
.thumb-content {{ max-height: 300px; overflow-y: auto; padding: 6px; }}
.thumb-content.collapsed {{ display: none; }}
.thumb-grid {{ display: flex; flex-wrap: wrap; gap: 3px; }}
.thumb-grid img {{ height: 100px; object-fit: contain; border-radius: 3px; }}
.thumb-grid .empty {{ color: #555; font-style: italic; font-size: 11px; padding: 8px; }}
.threshold-editor {{ display: flex; flex-direction: column; gap: 6px;
                     background: #252525; border: 1px solid #333; border-radius: 5px;
                     padding: 10px 12px 12px 12px; margin-top: 4px; }}
.sat-toggle {{ position: relative; display: inline-block; width: 28px; height: 16px;
              flex-shrink: 0; }}
.sat-toggle input {{ opacity: 0; width: 0; height: 0; }}
.sat-toggle .slider {{ position: absolute; cursor: pointer; inset: 0;
                        border: 2px solid #414141; border-radius: 50px;
                        transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275); }}
.sat-toggle .slider::before {{ position: absolute; content: "";
                                width: 10px; height: 10px; left: 1px; top: 1px;
                                background: #ccc; border-radius: inherit;
                                transition: all 0.4s cubic-bezier(0.23, 1, 0.32, 1); }}
.sat-toggle input:checked + .slider {{ box-shadow: 0 0 12px rgba(96,165,250,0.6);
                                        border-color: #60a5fa; }}
.sat-toggle input:checked + .slider::before {{ transform: translateX(12px); }}
.sat-controls {{ display: flex; flex-direction: column; gap: 26px; transition: opacity 0.2s; }}
.sat-controls.disabled {{ opacity: 0.3; pointer-events: none; }}
.sat-slider {{ position: relative; padding: 20px 2px 40px 2px; }}
.sat-track {{ position: relative; height: 8px; border-radius: 4px;
              background: linear-gradient(to right, #444, #d4739d); }}
.sat-marker {{ position: absolute; top: 50%; transform: translate(-50%, -50%);
               cursor: ew-resize; z-index: 2; touch-action: none; padding: 4px 6px; }}
.sat-marker-handle {{ width: 4px; height: 22px; background: #ccc; border-radius: 2px;
                     transition: background 0.1s; }}
.sat-marker-handle:hover, .sat-marker.dragging .sat-marker-handle {{ background: #fff; }}
.sat-marker.selected .sat-marker-handle {{ width: 6px; background: #60a5fa; }}
.sat-marker.selected .sat-marker-val {{ color: #60a5fa; }}
.sat-marker-val {{ position: absolute; top: 100%; left: 50%; transform: translateX(-50%);
                   font-size: 12px; color: #aaa; margin-top: 2px; white-space: nowrap;
                   pointer-events: none; }}
.sat-scale {{ display: flex; justify-content: space-between; font-size: 12px; color: #555;
              margin-top: 2px; padding: 0 6px; }}
.sat-hint {{ font-size: 12px; color: #555; font-style: italic; }}
.sat-btns {{ display: flex; gap: 6px; align-items: center; }}
.sat-btns button {{ background: #333; border: 1px solid #555; color: #ddd; cursor: pointer;
                    border-radius: 3px; padding: 3px 10px; font-size: 13px; }}
.sat-btns button:hover {{ background: #444; }}
.sat-btns button:disabled {{ opacity: 0.35; cursor: default; }}
.sat-btns button:disabled:hover {{ background: #333; }}

.guide-overlay {{ position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 20000;
                   display: flex; align-items: center; justify-content: center; }}
.guide-overlay.hidden {{ display: none; }}
.guide-modal {{ background: #1e1e1e; border: 1px solid #444; border-radius: 11px;
                width: 748px; max-width: min(748px, 90vw); min-height: 616px; height: auto;
                position: relative; padding: 0;
                box-shadow: 0 18px 53px rgba(0,0,0,0.7);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }}
.guide-close {{ position: absolute; top: 20px; right: 22px; background: none; border: none;
                color: #999; cursor: pointer; width: 31px; height: 31px; padding: 0;
                z-index: 10; display: flex; align-items: center; justify-content: center; }}
.guide-close:hover {{ color: #ccc; }}
.guide-close svg {{ width: 100%; height: 100%; display: block; }}
.sidebar-header {{ display: flex; align-items: center; padding: 0 26px;
                    height: 36px; flex: 0 0 36px; border-bottom: 1px solid #2a2a2a; }}
.sidebar-section.guide-section {{ cursor: pointer; }}
.sidebar-section.guide-section .ctrl-panel-header {{ margin-bottom: 0; }}
.sidebar-section.guide-section .ctrl-panel-label {{ color: #60a5fa; text-transform: none;
                                                     letter-spacing: 0.3px; font-size: 16px;
                                                     text-shadow: 0 0 10px rgba(96,165,250,0.55),
                                                                  0 0 20px rgba(96,165,250,0.3);
                                                     transition: color 0.15s, text-shadow 0.15s; }}
.sidebar-section.guide-section .guide-icon {{ display: inline-flex; align-items: center;
                                               color: #60a5fa;
                                               filter: drop-shadow(0 0 6px rgba(96,165,250,0.55));
                                               transition: color 0.15s, filter 0.15s; }}
.sidebar-section.guide-section:hover .ctrl-panel-label {{ color: #8bc0ff;
                                                           text-shadow: 0 0 14px rgba(139,192,255,0.8),
                                                                        0 0 28px rgba(139,192,255,0.45); }}
.sidebar-section.guide-section:hover .guide-icon {{ color: #8bc0ff;
                                                     filter: drop-shadow(0 0 8px rgba(139,192,255,0.85)); }}

.help-carousel {{ position: relative; height: 616px; }}
.help-section {{ display: none; position: absolute; inset: 0;
                 padding: 57px 53px 118px 53px; flex-direction: column; }}
.help-section.active {{ display: flex; }}

.guide-title {{ margin: 0; text-align: center; font-size: 31px; font-weight: 700; color: #fff;
                line-height: 1; flex: 0 0 auto; }}
.guide-subtitle {{ margin: 13px 40px 0 40px; text-align: center; font-size: 18px; font-weight: 400;
                   color: #9b9b9b; line-height: 1.4; flex: 0 0 auto; }}
.guide-graphic {{ flex: 1 1 auto; min-height: 0; margin: 13px 0;
                  display: flex; align-items: center; justify-content: center; }}
.guide-graphic svg {{ display: block; max-width: 100%; max-height: 100%; width: auto; height: auto; }}
.guide-graphic.fade-bottom {{ -webkit-mask-image: linear-gradient(to bottom, #000 55%, transparent 100%);
                               mask-image: linear-gradient(to bottom, #000 55%, transparent 100%); }}
.guide-graphic.shift-up svg {{ transform: translateY(-5px) scale(0.95); }}
.guide-body {{ margin: 0 13px; flex: 0 0 auto;
               text-align: center; font-size: 20px; font-weight: 600; color: #fff;
               line-height: 1.4; }}
.guide-highlight {{ color: #60a5fa; }}
.guide-emph {{ font-weight: 700; color: #fff; }}
.help-section.slide-no-graphic .guide-centered {{ position: absolute; top: 154px; bottom: 143px;
                                                   left: 66px; right: 66px; margin: 0;
                                                   display: flex; flex-direction: column;
                                                   align-items: center; justify-content: center;
                                                   gap: 20px; text-align: center; font-size: 20px;
                                                   font-weight: 400; color: #9b9b9b; line-height: 1.4; }}
.help-section.slide-no-graphic .guide-centered p {{ margin: 0; }}

.help-nav {{ position: absolute; bottom: 44px; left: 53px; right: 53px;
             display: flex; align-items: center; justify-content: center; gap: 20px; }}
.help-nav-arrow {{ background: none; border: none; color: #888;
                   cursor: pointer; width: 31px; height: 31px; padding: 0;
                   display: flex; align-items: center; justify-content: center; }}
.help-nav-arrow:hover {{ color: #ddd; }}
.help-nav-arrow svg {{ width: 100%; height: 100%; display: block; }}
.help-dots {{ display: flex; gap: 9px; }}
.help-dot {{ width: 8px; height: 8px; border-radius: 50%; background: #444; cursor: pointer;
             transition: background 0.15s; }}
.help-dot.active {{ background: #60a5fa; }}

/* Month grid */
.month-grid {{ display: grid; grid-template-columns: auto repeat(12, 1fr); gap: 2px;
               user-select: none; }}
.month-grid-header {{ font-size: 9px; color: #555; text-align: center; padding: 2px 0;
                      text-transform: uppercase; letter-spacing: 0.5px; }}
.month-grid-year {{ font-size: 10px; color: #555; display: flex; align-items: center;
                    padding-right: 6px; white-space: nowrap; }}
.month-cell {{ height: 22px; border-radius: 3px; cursor: pointer; position: relative; }}
.month-cell.has-data {{ background: #2a2a2a; border: 1px solid #3a3a3a; }}
.month-cell.has-data:hover {{ border-color: #60a5fa; }}
.month-cell.no-data {{ background: transparent; border: 1px solid #2a2a2a; cursor: default; opacity: 0.3; }}
.month-cell.selected {{ background: #2a3a5c; border-color: #60a5fa; }}
.window-btn {{ font-size: 12px; padding: 2px 8px; background: #333; color: #888;
               border: 1px solid #555; border-radius: 3px; cursor: pointer; }}
.window-btn.active {{ color: #fff; border-color: #60a5fa; background: #2a3a5c; }}
.swatch.low-power {{ opacity: 0.2 !important; }}
.swatch.empty-zone {{ opacity: 0.05 !important; pointer-events: none; }}
</style>
</head><body>
""")

    # --- Compatibility warning overlay ---
    html.append("""
<div class="compat-overlay hidden" id="compatOverlay" onclick="if(event.target===this)dismissCompat()">
  <div class="compat-modal">
    <h2 id="compatTitle">Small Screen Detected</h2>
    <p id="compatMsg">This tool is designed for screens at least 1024px wide. Some controls and panels may not display correctly at your current resolution.</p>
    <button onclick="dismissCompat()">Continue anyway</button>
  </div>
</div>
""")

    # --- Guide modal (centered popup, shown on load) ---
    svg_1 = _embed_guide_svg(1)
    svg_2 = _embed_guide_svg(2)
    svg_3 = _embed_guide_svg(3)
    svg_4 = _embed_guide_svg(4)
    svg_5 = _embed_guide_svg(5)
    icon_close = _embed_icon_svg('icon-close')
    icon_prev = _embed_icon_svg('icon-prev')
    icon_next = _embed_icon_svg('icon-next_1')
    guide_html = (
'<div class="guide-overlay" id="guideOverlay" onclick="if(event.target===this)closeGuide()">\n'
'  <div class="guide-modal">\n'
'    <button class="guide-close" onclick="closeGuide()">' + icon_close + '</button>\n'
'    <div class="help-carousel" id="helpCarousel">\n'
'\n'
'      <!-- Slide 1: What is this? -->\n'
'      <div class="help-section active">\n'
'        <h1 class="guide-title">What is this?</h1>\n'
'        <p class="guide-subtitle">An interactive tool that allows users to compare sportswear color palettes at a glance.</p>\n'
'        <div class="guide-graphic shift-up">' + svg_1 + '</div>\n'
'        <p class="guide-body">Each column is a brand. <span class="guide-highlight">Each bubble is a group of products sharing a dominant color</span> &mdash; bigger bubbles mean more products!</p>\n'
'      </div>\n'
'\n'
'      <!-- Slide 2: Where does the data come from? -->\n'
'      <div class="help-section">\n'
'        <h1 class="guide-title">Where does the data come from?</h1>\n'
'        <p class="guide-subtitle">Data is collected from the web on a monthly basis, including product images, prices, and descriptions.</p>\n'
'        <div class="guide-graphic">' + svg_2 + '</div>\n'
'        <p class="guide-body"><span class="guide-highlight">Computer vision and ML techniques are used to:</span> remove backgrounds from images, extract color palettes, and identify each product&rsquo;s dominant color.</p>\n'
'      </div>\n'
'\n'
'      <!-- Slide 3: How much data is there? -->\n'
'      <div class="help-section">\n'
'        <h1 class="guide-title">How much data is there?</h1>\n'
'        <p class="guide-subtitle">Currently, 16 months, or around 200,000 products!</p>\n'
'        <div class="guide-graphic fade-bottom">' + svg_3 + '</div>\n'
'        <p class="guide-body">Use the <span class="guide-highlight">Brands</span> tab to toggle which brand and gender segments appear on screen.</p>\n'
'      </div>\n'
'\n'
'      <!-- Slide 4: What&rsquo;s the point? -->\n'
'      <div class="help-section">\n'
'        <h1 class="guide-title">What&rsquo;s the point?</h1>\n'
'        <p class="guide-subtitle">In design, a brand that &ldquo;blends in&rdquo; is considered a failure &mdash; standing out is everything. Fashion and sportswear echo this: endless talk of seasonal palettes, colors of the year, and what&rsquo;s trending next.</p>\n'
'        <div class="guide-graphic">' + svg_4 + '</div>\n'
'        <p class="guide-body">So I wanted to see for myself &mdash; when it comes to the color used in actual products on the shelf, <span class="guide-highlight">how do major sportswear brands differentiate themselves?</span></p>\n'
'      </div>\n'
'\n'
'      <!-- Slide 5: ...and? -->\n'
'      <div class="help-section">\n'
'        <h1 class="guide-title">&hellip;and?</h1>\n'
'        <p class="guide-subtitle">The brands in this space largely utilize the same color palette with some subtle differences. My explorations using the <span class="guide-highlight" style="font-weight:700">Time Range</span> tab don&rsquo;t expose any detectable seasonal trends.</p>\n'
'        <div class="guide-graphic">' + svg_5 + '</div>\n'
'        <p class="guide-body">Given a few more years, slower-moving trends might start to emerge! That would be interesting to see.</p>\n'
'      </div>\n'
'\n'
'      <!-- Slide 6: One last thing! (no graphic) -->\n'
'      <div class="help-section slide-no-graphic">\n'
'        <h1 class="guide-title">One last thing!</h1>\n'
'        <div class="guide-centered">\n'
'          <p>I&rsquo;m investigating other uses for this data. To see some examples of what I&rsquo;ve tried already, <span class="guide-emph">check out the <span class="guide-highlight">Analyses</span> tab</span> for a closer look at how color relates to pricing and discounts.</p>\n'
'          <p>Nothing jumps out yet, but it&rsquo;s fun to explore!</p>\n'
'        </div>\n'
'      </div>\n'
'\n'
'      <div class="help-nav">\n'
'        <button class="help-nav-arrow" onclick="helpNav(-1)">' + icon_prev + '</button>\n'
'        <div class="help-dots" id="helpDots"></div>\n'
'        <button class="help-nav-arrow" onclick="helpNav(1)">' + icon_next + '</button>\n'
'      </div>\n'
'    </div>\n'
'  </div>\n'
'</div>\n'
    )
    html.append(guide_html)
    html.append("""


<div class="sidebar">
  <div class="sidebar-header"></div>
  <div class="sidebar-scroll">
  <div class="sidebar-section collapsed">
    <div class="ctrl-panel" id="ctrl-panel-layout">
      <div class="ctrl-panel-header" onclick="toggleSection(this)">
        <div class="ctrl-panel-label">View<span class="ctrl-panel-chevron">&#9660;</span></div>
      </div>
      <div class="ctrl-panel-body">
      <div class="ctrl-panel-row">
        <div class="ctrl-panel-row-label">Zoom</div>
        <div class="width-control">
          <input type="range" id="zoomSlider" min="0" max="200" value="100" step="5">
          <span class="val" id="zoomLevel">100%</span>
        </div>
      </div>
      <div class="ctrl-panel-row">
        <div class="ctrl-panel-row-label">Panel width</div>
        <div class="width-control">
          <input type="range" id="widthSlider" min="200" max="840" value="440" step="10">
          <span class="val" id="widthValue">440px</span>
        </div>
      </div>
      <div class="ctrl-panel-row" style="gap:10px">
        <div class="ctrl-panel-row-label">Zone sizing</div>
        <div style="display:flex;align-items:center;gap:10px;">
          <label class="sat-toggle"><input type="checkbox" id="globalScaleToggle" checked onchange="toggleGlobalScale(this.checked)"><span class="slider"></span></label>
          <span id="globalScaleLabel" style="font-size:11px;color:#888;">Cross-brand zone sizing</span>
        </div>
      </div>
      </div>
    </div>
  </div>

  <div class="sidebar-section collapsed">
    <div class="ctrl-panel" id="ctrl-panel-selection">
      <div class="ctrl-panel-header" onclick="toggleSection(this)">
        <div class="ctrl-panel-label">Selection<span class="ctrl-panel-chevron">&#9660;</span></div>
      </div>
      <div class="ctrl-panel-body">
      <div class="ctrl-panel-row" style="gap:10px">
        <div class="ctrl-panel-row-label">&Delta;E radius select</div>
        <div style="display:flex;align-items:center;gap:10px;">
          <label class="sat-toggle"><input type="checkbox" id="deltaSelectToggle" onchange="toggleDeltaSelect(this.checked)"><span class="slider"></span></label>
          <span style="font-size:11px;color:#888;">Ctrl+click grabs all zones within the radius (off = one zone)</span>
        </div>
      </div>
      <div class="ctrl-panel-row" style="gap:10px">
        <div class="ctrl-panel-row-label">Scope</div>
        <div style="display:flex;align-items:center;gap:10px;">
          <label class="sat-toggle"><input type="checkbox" id="selScopeToggle" onchange="setSelScope(this.checked)"><span class="slider"></span></label>
          <span id="selScopeLabel" style="font-size:11px;color:#888;">Cross-brand selection</span>
        </div>
      </div>
      <div class="ctrl-panel-row" style="gap:10px" id="selRadiusRow">
        <div class="ctrl-panel-row-label">Radius (&Delta;E)</div>
        <div class="width-control">
          <input type="range" id="selDeltaSlider" min="1" max="40" value="8" step="1">
          <span class="val" id="selDeltaValue">8</span>
        </div>
      </div>
      <div class="ctrl-panel-row" style="gap:10px">
        <div class="sel-note">CIEDE2000 (&Delta;E00) &mdash; the metric the zones were built with. Ctrl+click a zone to add/remove; left-click empty space to clear.</div>
        <button class="sel-clear-btn" onclick="clearAllSelections()">Clear selection</button>
      </div>
      <div class="ctrl-panel-row">
        <div id="selStats" class="sel-stats"></div>
      </div>
      </div>
    </div>
  </div>

  <div class="sidebar-section collapsed">
    <div class="ctrl-panel" id="ctrl-panel-brands">
      <div class="ctrl-panel-header" onclick="toggleSection(this)">
        <div class="ctrl-panel-label">Brands<span class="ctrl-panel-chevron">&#9660;</span></div>
      </div>
      <div class="ctrl-panel-body">
      <div class="ctrl-panel-row">
        <div class="brand-toggles" id="brandToggles">
""")
    for i, bd in enumerate(brands_data):
        active_cls = ' active' if '_mens' in bd['slug'] else ''
        n_zones = len(bd['zones'])
        n_products = sum(c['n_products'] for c in bd['centroids'])
        spacer = ' style="margin-top:12px"' if (i > 0 and i % 2 == 0) else ''
        html.append(
            f'          <div class="brand-toggle{active_cls}"{spacer} data-brand="{bd["slug"]}" '
            f'onclick="toggleBrand(\'{bd["slug"]}\')">'
            f'<div class="brand-toggle-dot"></div>'
            f'<span class="brand-toggle-name">{bd["name"]}</span>'
            f'<span class="brand-toggle-stat">{n_zones} zones &middot; {n_products:,} products</span>'
            f'</div>')
    html.append("""
          <div class="brand-actions">
            <a onclick="showAllBrands()">Show all</a>
            <a onclick="hideAllBrands()">Hide all</a>
          </div>
        </div>
      </div>
      </div>
    </div>
  </div>

  <div class="sidebar-section collapsed">
    <div class="ctrl-panel" id="ctrl-panel-analysis">
      <div class="ctrl-panel-header" onclick="toggleSection(this)">
        <div class="ctrl-panel-label">Analyses<span class="ctrl-panel-chevron">&#9660;</span></div>
      </div>
      <div class="ctrl-panel-body">
      <div class="view-bar">
        <div class="view-option active" data-mode="palette" onclick="setView('palette')">
          <div class="view-option-radio"></div>
          <div class="view-option-text">
            <div class="view-option-name">Palette</div>
            <div class="view-option-desc">Color each zone by its LAB centroid.</div>
          </div>
        </div>
        <div class="view-option" data-mode="freq" onclick="setView('freq')">
          <div class="view-option-radio"></div>
          <div class="view-option-text">
            <div class="view-option-name">Discount Frequency</div>
            <div class="view-option-desc">How often products in each zone are discounted, relative to the brand baseline.</div>
          </div>
        </div>
        <div class="view-option" data-mode="depth" onclick="setView('depth')">
          <div class="view-option-radio"></div>
          <div class="view-option-text">
            <div class="view-option-name">Discount Depth</div>
            <div class="view-option-desc">How deeply products are marked down when discounted, relative to the brand baseline.</div>
          </div>
        </div>
        <div class="view-option" data-mode="price" onclick="setView('price')">
          <div class="view-option-radio"></div>
          <div class="view-option-text">
            <div class="view-option-name">Price Level</div>
            <div class="view-option-desc">Whether products in each zone are priced above or below the brand average list price.</div>
          </div>
        </div>
      </div>
      <div class="colorbar-wrap" id="colorbarWrap">
        <span class="colorbar-title" id="colorbarTitle"></span>
        <div class="colorbar-bar">
          <span class="colorbar-label right" id="cbLabelMin"></span>
          <canvas class="colorbar-canvas" id="colorbarCanvas" width="200" height="14"></canvas>
          <span class="colorbar-label" id="cbLabelMax"></span>
        </div>
      </div>
      <div id="smoothingControls" style="display:none;flex-direction:column;gap:22px;">
      <div class="ctrl-panel-row" style="gap:10px">
        <div class="ctrl-panel-row-label">Smoothing</div>
        <label class="sat-toggle"><input type="checkbox" id="smoothToggle" checked onchange="toggleSmoothing(this.checked)"><span class="slider"></span></label>
      </div>
      <div class="ctrl-panel-row" id="smoothSliderWrap" style="gap:10px;">
        <div class="ctrl-panel-row-label">Bandwidth (&Delta;E)</div>
        <div class="width-control">
          <input type="range" id="smoothBandwidth" min="5" max="40" value="15" step="1">
          <span class="val" id="smoothBWValue">15</span>
        </div>
      </div>
      </div>
      <div id="temporalWarning" style="display:none;font-size:11px;color:#d4a017;line-height:1.4;">
        Time-filtered &mdash; showing raw rates without significance testing. Faded zones have &lt;14 products in the selected range.
      </div>
      </div>
    </div>
  </div>

  <div class="sidebar-section collapsed">
    <div class="ctrl-panel" id="ctrl-panel-chroma">
      <div class="ctrl-panel-header" onclick="toggleSection(this)">
        <div class="ctrl-panel-label">Saturation Thresholds<span class="ctrl-panel-chevron">&#9660;</span></div>
      </div>
      <div class="ctrl-panel-body">
      <div class="ctrl-panel-row" style="gap:10px">
        <div class="ctrl-panel-row-label">Enable / Disable</div>
        <label class="sat-toggle">
          <input type="checkbox" id="satToggle" checked onchange="toggleThresholds(this.checked)">
          <span class="slider"></span>
        </label>
      </div>
      <div class="sat-controls" id="satControls">
      <div class="ctrl-panel-row" style="gap:10px">
        <div class="ctrl-panel-row-label">Edit thresholds</div>
      <div class="threshold-editor" id="thresholdEditor">
          <div class="sat-slider" id="satSlider">
            <div class="sat-track" id="satTrack"></div>
          </div>
          <div class="sat-btns">
            <button onclick="addThreshold()">+ Add</button>
            <button id="satRemoveBtn" disabled onclick="removeThreshold()">&minus; Remove</button>
          </div>
      </div>
      </div>
      <div class="ctrl-panel-row">
        <div class="ctrl-panel-row-label">Hue origin</div>
        <div class="width-control">
          <input type="range" id="hueOffsetSlider" min="0" max="360" value="45" step="5">
          <span class="val" id="hueOffsetValue">45&deg;</span>
        </div>
      </div>
      </div>
      </div>
    </div>
  </div>

  <div class="sidebar-section collapsed" id="temporal-section">
    <div class="ctrl-panel" id="ctrl-panel-temporal">
      <div class="ctrl-panel-header" onclick="toggleSection(this)">
        <div class="ctrl-panel-label">Time Range<span class="ctrl-panel-chevron">&#9660;</span></div>
      </div>
      <div class="ctrl-panel-body">
      <div id="temporalUnavailable" style="display:none;font-size:12px;color:#666;line-height:1.4;">
        No temporal data available. Regenerate the HTML with database access (POSTGRES_PASSWORD) to enable time range filtering.
      </div>
      <div class="ctrl-panel-row" style="gap:10px">
        <div class="ctrl-panel-row-label">Enable / Disable</div>
        <label class="sat-toggle">
          <input type="checkbox" id="temporalToggle" onchange="toggleTemporal(this.checked)">
          <span class="slider"></span>
        </label>
      </div>
      <div id="temporalControls" style="opacity:0.3;pointer-events:none;display:flex;flex-direction:column;gap:22px;">
      <div class="ctrl-panel-row" style="gap:10px">
        <div class="ctrl-panel-row-label">Date range</div>
        <div class="month-grid" id="monthGrid"></div>
        <div id="timeSelectionLabel" style="text-align:center;font-size:12px;color:#999;"></div>
      </div>
      <div class="ctrl-panel-row" style="gap:10px">
        <div class="ctrl-panel-row-label">Window</div>
        <div style="display:flex;gap:6px;">
          <button class="window-btn" data-months="1" onclick="setAnimWindow(1)">1 mo</button>
          <button class="window-btn active" data-months="3" onclick="setAnimWindow(3)">3 mo</button>
          <button class="window-btn" data-months="6" onclick="setAnimWindow(6)">6 mo</button>
        </div>
      </div>
      <div class="ctrl-panel-row" style="gap:10px">
        <div class="ctrl-panel-row-label">Playback</div>
        <div style="display:flex;gap:10px;align-items:center;">
          <button id="playBtn" onclick="togglePlay()" style="padding:0;width:32px;height:26px;background:#333;color:#ddd;border:1px solid #555;border-radius:3px;cursor:pointer;display:flex;align-items:center;justify-content:center;"><svg width="12" height="12" viewBox="0 0 12 12"><polygon points="2,0 12,6 2,12" fill="currentColor"/></svg></button>
          <div class="width-control" style="flex:1;">
            <span class="label">Speed</span>
            <input type="range" id="animSpeedSlider" min="200" max="3000" value="1000" step="100">
            <span class="val" id="animSpeedValue">1s</span>
          </div>
        </div>
      </div>
      </div>
      </div>
    </div>
  </div>

  <div class="sidebar-section guide-section" onclick="openGuide()">
    <div class="ctrl-panel">
      <div class="ctrl-panel-header">
        <div class="ctrl-panel-label">What is this?<span class="guide-icon"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17 17 7"/><path d="M7 7h10v10"/></svg></span></div>
      </div>
    </div>
  </div>

  </div>
</div>

<div class="main-area">
  <div class="info-bar" id="infoBar">
    <span class="ib-dim">Hover for details. Left-click a zone for its thumbnails; left-click empty space to clear. Ctrl+click a zone to add/remove (a &Delta;E ball when &Delta;E radius select is on).</span>
  </div>
""")

    # --- Brand panels ---
    html.append('<div class="panels-container" id="panelsContainer">')

    for bd in brands_data:
        slug = bd['slug']
        layout = brand_layouts[slug]
        total_height = layout['height']

        hidden_cls = '' if '_mens' in slug else ' hidden'
        html.append(f'<div class="brand-panel{hidden_cls}" id="panel-{slug}">')
        html.append('<div class="brand-panel-content">')
        html.append(f'<div class="brand-scroll" data-brand="{slug}">')
        html.append(f'<div class="zoom-container" id="zoomContainer-{slug}">')
        html.append(f'<div class="canvas" id="canvas-{slug}" style="height:{total_height}px;">')

        swatch_html = render_brand_swatches(slug, layout['swatches'], layout['labels'], bd['centroids'])
        html.extend(swatch_html)

        html.append('</div></div></div>')  # canvas, zoom-container, brand-scroll

        # Collapsible thumbnails
        html.append(f'<div class="thumb-section">')
        html.append(f'<button class="thumb-toggle" id="thumb-toggle-{slug}" '
                    f'data-brand-name="{bd["name"]}" '
                    f"onclick=\"toggleThumbs('{slug}')\">"
                    f'<span>&#9654; <span class="thumb-brand">{bd["name"]}</span></span>'
                    f'<span class="thumb-right">Thumbnails</span>'
                    f'</button>')
        html.append(f'<div class="thumb-content collapsed" id="thumbs-{slug}">')
        html.append(f'<div class="thumb-grid" id="thumb-grid-{slug}">')
        html.append('<span class="empty">Click a zone to see thumbnails</span>')
        html.append('</div></div></div>')  # thumb-grid, thumb-content, thumb-section

        html.append('</div>')  # brand-panel-content
        html.append('<div class="panel-resize"></div>')
        html.append('</div>')  # brand-panel

    html.append('</div>')  # panels-container
    html.append('</div>')  # main-area

    # --- JavaScript ---
    html.append(f"""
<script>
var brands = {brands_json};
var brandSlugs = {brand_slugs_json};
var currentView = 'palette';
var zoom = 1;
var panelWidth = 440;
var globalMaxProducts = {global_max_products};
var useGlobalScale = true;
var freqMaxAbs = {global_freq_max};
var depthMaxAbs = {global_depth_max};
var priceMaxAbs = {global_price_max};
var allMonths = {months_json};
var hasTemporal = {'true' if has_temporal else 'false'};
var activeBrands = new Set(brandSlugs.filter(function(s) {{ return s.indexOf('_mens') !== -1; }}));
var focusBrand = null;
var smoothingEnabled = true;
var smoothBandwidth = 15;
// --- ΔE ball-selection tools ---
var selScope = 'brand';     // 'brand' | 'all'
var selDeltaE = 8;          // ball radius in CIEDE2000 (ΔE00)
var deltaSelectEnabled = false;  // when true, ctrl+click grabs a ΔE ball instead of one zone
var timeFiltered = false;
var timeStartIdx = 0;
var timeEndIdx = allMonths.length - 1;
var animPlaying = false;
var animInterval = null;
var animWindowMonths = 3;
var animSpeed = 1000;

// Init per-brand runtime state
for (var i = 0; i < brandSlugs.length; i++) {{
    var s = brandSlugs[i];
    brands[s].selectedZones = new Set();
    brands[s].hoveredZone = null;
    brands[s].thumbExpanded = false;
}}

// --- Sidebar collapse ---
function toggleSection(header) {{
    var section = header.closest('.sidebar-section');
    section.classList.toggle('collapsed');
}}

// --- Guide modal ---
function openGuide() {{
    document.getElementById('guideOverlay').classList.remove('hidden');
}}
function closeGuide() {{
    document.getElementById('guideOverlay').classList.add('hidden');
}}

// --- Guide carousel ---
var helpIdx = 0;
function helpNav(dir) {{
    var sections = document.querySelectorAll('#helpCarousel .help-section');
    var dots = document.querySelectorAll('#helpDots .help-dot');
    var next = helpIdx + dir;
    if (next < 0 || next >= sections.length) return;
    sections[helpIdx].classList.remove('active');
    if (dots[helpIdx]) dots[helpIdx].classList.remove('active');
    helpIdx = next;
    sections[helpIdx].classList.add('active');
    if (dots[helpIdx]) dots[helpIdx].classList.add('active');
}}
function helpGoTo(i) {{
    var sections = document.querySelectorAll('#helpCarousel .help-section');
    var dots = document.querySelectorAll('#helpDots .help-dot');
    sections[helpIdx].classList.remove('active');
    if (dots[helpIdx]) dots[helpIdx].classList.remove('active');
    helpIdx = i;
    sections[helpIdx].classList.add('active');
    if (dots[helpIdx]) dots[helpIdx].classList.add('active');
}}
(function() {{
    var sections = document.querySelectorAll('#helpCarousel .help-section');
    var container = document.getElementById('helpDots');
    for (var i = 0; i < sections.length; i++) {{
        var d = document.createElement('div');
        d.className = 'help-dot' + (i === 0 ? ' active' : '');
        d.setAttribute('data-i', i);
        d.onclick = function() {{ helpGoTo(parseInt(this.getAttribute('data-i'))); }};
        container.appendChild(d);
    }}
}})();

// --- Layout parameters ---
var thresholds = [{NEUTRAL_HSB}, {SATURATED_HSB}];
var savedThresholds = null;  // used by toggle
var thresholdsEnabled = true;
var hueOffset = 45;
var TRACK_W = {TRACK_WIDTH};
var BASE_SW = {BASE_SWATCH};
var MAX_SZ = {MAX_SIZE};

function getCategoryForSat(sat) {{
    for (var i = 0; i < thresholds.length; i++) {{
        if (sat < thresholds[i]) return i;
    }}
    return thresholds.length;
}}

function getCategoryName(catIdx) {{
    if (thresholds.length === 0) return '';
    if (catIdx === 0) return '< ' + thresholds[0] + '%';
    if (catIdx >= thresholds.length) return '\u2265 ' + thresholds[thresholds.length - 1] + '%';
    return thresholds[catIdx - 1] + ' \u2013 ' + thresholds[catIdx] + '%';
}}

// --- Slider-based threshold editor ---
var dragState = null;
var lastRelayout = 0;
var selectedMarkerIdx = null;

function renderSliderMarkers() {{
    var track = document.getElementById('satTrack');
    var old = track.querySelectorAll('.sat-marker');
    old.forEach(function(el) {{ el.remove(); }});
    for (var i = 0; i < thresholds.length; i++) {{
        var pct = thresholds[i];
        var mk = document.createElement('div');
        mk.className = 'sat-marker' + (i === selectedMarkerIdx ? ' selected' : '');
        mk.setAttribute('data-idx', i);
        mk.style.left = pct + '%';
        mk.innerHTML = '<div class="sat-marker-handle"></div>'
            + '<div class="sat-marker-val">' + pct + '%</div>';
        mk.addEventListener('mousedown', startDrag);
        track.appendChild(mk);
    }}
    updateRemoveBtn();
}}

function updateRemoveBtn() {{
    var btn = document.getElementById('satRemoveBtn');
    if (!btn) return;
    var hasSelection = selectedMarkerIdx !== null && selectedMarkerIdx < thresholds.length;
    btn.disabled = !hasSelection;
    btn.textContent = hasSelection
        ? '\u2212 Remove ' + thresholds[selectedMarkerIdx] + '%'
        : '\u2212 Remove';
}}

function selectMarker(idx) {{
    if (selectedMarkerIdx === idx) {{
        selectedMarkerIdx = null;
    }} else {{
        selectedMarkerIdx = idx;
    }}
    renderSliderMarkers();
}}

function startDrag(e) {{
    e.preventDefault();
    var mk = e.currentTarget;
    var idx = parseInt(mk.getAttribute('data-idx'));
    var track = document.getElementById('satTrack');
    var rect = track.getBoundingClientRect();
    mk.classList.add('dragging');
    dragState = {{ idx: idx, marker: mk, rect: rect, startX: e.clientX, moved: false }};
    document.addEventListener('mousemove', onDrag);
    document.addEventListener('mouseup', endDrag);
}}

function onDrag(e) {{
    if (!dragState) return;
    if (Math.abs(e.clientX - dragState.startX) > 3) dragState.moved = true;
    if (!dragState.moved) return;
    var pct = (e.clientX - dragState.rect.left) / dragState.rect.width * 100;
    var idx = dragState.idx;
    var minV = idx > 0 ? thresholds[idx - 1] + 1 : 1;
    var maxV = idx < thresholds.length - 1 ? thresholds[idx + 1] - 1 : 99;
    pct = Math.round(Math.max(minV, Math.min(maxV, pct)));
    thresholds[idx] = pct;
    dragState.marker.style.left = pct + '%';
    dragState.marker.querySelector('.sat-marker-val').textContent = pct + '%';
    var now = Date.now();
    if (now - lastRelayout > 80) {{
        lastRelayout = now;
        recomputeAllLayouts();
    }}
}}

function endDrag() {{
    if (dragState) {{
        dragState.marker.classList.remove('dragging');
        var wasDrag = dragState.moved;
        var idx = dragState.idx;
        dragState = null;
        if (!wasDrag) selectMarker(idx);
    }}
    document.removeEventListener('mousemove', onDrag);
    document.removeEventListener('mouseup', endDrag);
    recomputeAllLayouts();
}}

function addThreshold() {{
    var lastVal = thresholds.length > 0 ? thresholds[thresholds.length - 1] : 0;
    var newVal = Math.min(lastVal + 10, 99);
    if (newVal <= lastVal) newVal = lastVal + 1;
    if (newVal > 99) return;
    thresholds.push(newVal);
    selectedMarkerIdx = null;
    renderSliderMarkers();
    recomputeAllLayouts();
    if (!thresholdsEnabled) {{
        thresholdsEnabled = true;
        savedThresholds = null;
        document.getElementById('satToggle').checked = true;
        document.getElementById('satSlider').classList.remove('disabled');
    }}
}}

function removeThreshold() {{
    if (thresholds.length < 1) return;
    if (selectedMarkerIdx !== null && selectedMarkerIdx < thresholds.length) {{
        thresholds.splice(selectedMarkerIdx, 1);
    }} else {{
        thresholds.pop();
    }}
    selectedMarkerIdx = null;
    renderSliderMarkers();
    recomputeAllLayouts();
}}

function toggleThresholds(enabled) {{
    thresholdsEnabled = enabled;
    if (!enabled) {{
        savedThresholds = thresholds.slice();
        thresholds = [];
        document.getElementById('satControls').classList.add('disabled');
    }} else {{
        if (savedThresholds && savedThresholds.length > 0) {{
            thresholds = savedThresholds.slice();
        }} else {{
            thresholds = [{NEUTRAL_HSB}, {SATURATED_HSB}];
        }}
        savedThresholds = null;
        document.getElementById('satControls').classList.remove('disabled');
    }}
    renderSliderMarkers();
    recomputeAllLayouts();
}}

function labToHsbSat(L, a, b) {{
    var fy = (L + 16) / 116;
    var fx = a / 500 + fy;
    var fz = fy - b / 200;
    function inv(t) {{ return t*t*t > 0.008856 ? t*t*t : (t - 16/116) / 7.787; }}
    var x = inv(fx) * 0.95047, y = inv(fy), z = inv(fz) * 1.08883;
    var r = x*3.2406 + y*-1.5372 + z*-0.4986;
    var g = x*-0.9689 + y*1.8758 + z*0.0415;
    var bl = x*0.0557 + y*-0.2040 + z*1.0570;
    function gam(c) {{ return c <= 0.0031308 ? 12.92*c : 1.055*Math.pow(c, 1/2.4) - 0.055; }}
    r = Math.max(0, Math.min(1, gam(r)));
    g = Math.max(0, Math.min(1, gam(g)));
    bl = Math.max(0, Math.min(1, gam(bl)));
    var mx = Math.max(r, g, bl), mn = Math.min(r, g, bl);
    return mx > 0 ? (mx - mn) / mx * 100 : 0;
}}

function recomputeLayout(slug) {{
    var bd = brands[slug];
    var items = bd.displayItems;
    var zc = bd.zoneCentroids;
    var maxProd = useGlobalScale ? globalMaxProducts : bd.maxProducts;

    function groupCentroid(zones) {{
        var tw = 0, wL = 0, wa = 0, wb = 0;
        for (var i = 0; i < zones.length; i++) {{
            var c = zc[zones[i]]; if (!c) continue;
            wL += c.L * c.n_products; wa += c.a * c.n_products; wb += c.b * c.n_products;
            tw += c.n_products;
        }}
        if (tw === 0) {{ var c0 = zc[zones[0]]; return [c0.L, c0.a, c0.b]; }}
        return [wL/tw, wa/tw, wb/tw];
    }}

    function scaledSize(n) {{ return n > 0 ? MAX_SZ * Math.sqrt(n / maxProd) : 1; }}

    // Categorize and sort display items
    var cats = [];
    for (var idx = 0; idx < items.length; idx++) {{
        var gc = groupCentroid(items[idx].zones);
        var sat = labToHsbSat(gc[0], gc[1], gc[2]);
        var hue = (Math.atan2(gc[2], gc[1]) * 180 / Math.PI + hueOffset) % 360;
        if (hue < 0) hue += 360;
        var cat = getCategoryForSat(sat);
        // Category 0 sorts by L* only when thresholds exist (it's the neutral bucket);
        // when no thresholds exist, everything is one category — sort by hue
        var sk = (cat === 0 && thresholds.length > 0) ? gc[0] : hue;
        cats.push({{ cat: cat, sk: sk, idx: idx }});
    }}
    cats.sort(function(a, b) {{ return a.cat !== b.cat ? a.cat - b.cat : a.sk - b.sk; }});
    var gap = 4, minSp = BASE_SW + gap, minRow = 48, rowScale = 0.6;
    var yCursor = 0, prevCat = -1;

    // Collect new positions per zone: zi -> {{x, y, size}}
    var zonePos = {{}};
    var catLabels = [];

    for (var pos = 0; pos < cats.length; pos++) {{
        var item = items[cats[pos].idx];
        var cat = cats[pos].cat;
        if (cat !== prevCat) {{
            if (thresholds.length > 0) {{
                if (prevCat >= 0) yCursor += 12;
                catLabels.push({{ y: yCursor, text: getCategoryName(cat) }});
                yCursor += 56;
            }}
            prevCat = cat;
        }}
        var zones = item.zones.slice().sort(function(a, b) {{
            return (zc[a] ? zc[a].L : 0) - (zc[b] ? zc[b].L : 0);
        }});
        var ideals = [];
        for (var i = 0; i < zones.length; i++) {{
            var c = zc[zones[i]];
            ideals.push(c ? c.L / 100 * (TRACK_W - BASE_SW) : 0);
        }}
        // Forward pass
        var positions = [], cursor = 0;
        for (var i = 0; i < ideals.length; i++) {{
            var actual = Math.max(ideals[i], cursor);
            positions.push(actual);
            cursor = actual + minSp;
        }}
        // Centered overlap resolution
        if (positions.length > 1) {{
            var oGroups = [], cur = [0];
            for (var i = 1; i < positions.length; i++) {{
                if (ideals[i] < positions[i-1] + minSp) cur.push(i);
                else {{ oGroups.push(cur); cur = [i]; }}
            }}
            oGroups.push(cur);
            for (var gi = 0; gi < oGroups.length; gi++) {{
                var grp = oGroups[gi];
                if (grp.length < 2) continue;
                var meanIdeal = 0;
                for (var i = 0; i < grp.length; i++) meanIdeal += ideals[grp[i]];
                meanIdeal /= grp.length;
                var tw = (grp.length - 1) * minSp;
                var start = meanIdeal - tw / 2;
                if (grp[0] > 0) start = Math.max(start, positions[grp[0]-1] + minSp);
                start = Math.max(start, 0);
                for (var i = 0; i < grp.length; i++) positions[grp[i]] = start + i * minSp;
            }}
        }}
        // Sizes and row height
        var zoneSizes = [];
        for (var i = 0; i < zones.length; i++) {{
            var c = zc[zones[i]];
            zoneSizes.push(scaledSize(c ? c.n_products : 0));
        }}
        var maxZS = 0;
        for (var i = 0; i < zoneSizes.length; i++) if (zoneSizes[i] > maxZS) maxZS = zoneSizes[i];
        var rowH = Math.max(minRow, maxZS * rowScale);
        for (var i = 0; i < zones.length; i++) {{
            zonePos[zones[i]] = {{
                x: positions[i] + BASE_SW / 2,
                y: yCursor + rowH / 2,
                size: zoneSizes[i]
            }};
        }}
        yCursor += rowH;
    }}

    var totalH = yCursor + 50;
    bd.baseH = totalH;

    // Update DOM
    var canvas = document.getElementById('canvas-' + slug);
    canvas.style.height = totalH + 'px';

    // Update category labels
    var existingLabels = canvas.querySelectorAll('.cat-label');
    existingLabels.forEach(function(el) {{ el.remove(); }});
    for (var i = 0; i < catLabels.length; i++) {{
        var div = document.createElement('div');
        div.className = 'cat-label';
        div.style.top = catLabels[i].y + 'px';
        var sp = document.createElement('span');
        sp.textContent = catLabels[i].text;
        div.appendChild(sp);
        canvas.appendChild(div);
    }}

    // Reposition swatches and update z-index (largest behind)
    var swatchEls = Array.from(canvas.querySelectorAll('.swatch'));
    // Sort by size descending for z-index
    swatchEls.sort(function(a, b) {{
        var ziA = a.getAttribute('data-zi'), ziB = b.getAttribute('data-zi');
        var sA = zonePos[ziA] ? zonePos[ziA].size : 0;
        var sB = zonePos[ziB] ? zonePos[ziB].size : 0;
        return sB - sA;
    }});
    for (var i = 0; i < swatchEls.length; i++) {{
        var el = swatchEls[i];
        var zi = el.getAttribute('data-zi');
        var p = zonePos[zi];
        if (!p) continue;
        var left = p.x - p.size / 2;
        var top = p.y - p.size / 2;
        el.style.left = left.toFixed(1) + 'px';
        el.style.top = top.toFixed(1) + 'px';
        el.style.width = Math.round(p.size) + 'px';
        el.style.height = Math.round(p.size) + 'px';
        el.style.zIndex = i + 1;
    }}

    applyZoom();
}}

function recomputeAllLayouts() {{
    for (var slug in brands) {{
        recomputeLayout(slug);
    }}
    setView(currentView);
}}

// --- Zone sizing toggle ---
function toggleGlobalScale(enabled) {{
    useGlobalScale = enabled;
    recomputeAllLayouts();
}}

// Hue offset slider
document.getElementById('hueOffsetSlider').addEventListener('input', function() {{
    hueOffset = parseFloat(this.value);
    document.getElementById('hueOffsetValue').innerHTML = this.value + '&deg;';
    recomputeAllLayouts();
}});

// --- Colorscale ---
function devToColor(dev, maxAbs) {{
    var t = Math.max(-1, Math.min(1, dev / maxAbs));
    var r, g, b;
    if (t < 0) {{
        var s = -t;
        r = Math.round(0xf7 - (0xf7 - 0x21) * s);
        g = Math.round(0xf7 - (0xf7 - 0x66) * s);
        b = Math.round(0xf7 - (0xf7 - 0xac) * s);
    }} else {{
        var s = t;
        r = Math.round(0xf7 - (0xf7 - 0xb2) * s);
        g = Math.round(0xf7 - (0xf7 - 0x18) * s);
        b = Math.round(0xf7 - (0xf7 - 0x2b) * s);
    }}
    return 'rgb(' + r + ',' + g + ',' + b + ')';
}}

function drawColorbar(maxAbs, title) {{
    var wrap = document.getElementById('colorbarWrap');
    var cvs = document.getElementById('colorbarCanvas');
    var ctx = cvs.getContext('2d');
    wrap.classList.add('visible');
    document.getElementById('colorbarTitle').textContent = title;
    document.getElementById('cbLabelMin').textContent = (-maxAbs).toFixed(0) + 'pp';
    document.getElementById('cbLabelMax').textContent = '+' + maxAbs.toFixed(0) + 'pp';
    for (var x = 0; x < 200; x++) {{
        var dev = (x / 199) * 2 * maxAbs - maxAbs;
        ctx.fillStyle = devToColor(dev, maxAbs);
        ctx.fillRect(x, 0, 1, 14);
    }}
}}

// --- Info bar ---
function formatZoneInfo(brand, zi) {{
    var bd = brands[brand];
    var c = bd.zoneCentroids[zi];
    if (!c) return '';
    var hex = '';
    document.querySelectorAll('#panel-' + brand + ' .swatch[data-zi="'+zi+'"]').forEach(function(el) {{ hex = el.getAttribute('data-hex'); }});
    var h = '<span class="ib-swatch" style="background:' + hex + '"></span>';
    h += '<span class="ib-name">' + c.name + '</span>';
    h += '<span class="ib-sep">|</span>';
    h += c.n_products.toLocaleString() + ' products';
    return h;
}}

function formatZoneDetail(brand, zi) {{
    var bd = brands[brand];
    var c = bd.zoneCentroids[zi];
    var s = bd.zoneStats[zi];
    if (!c) return '';
    var hex = '';
    document.querySelectorAll('#panel-' + brand + ' .swatch[data-zi="'+zi+'"]').forEach(function(el) {{ hex = el.getAttribute('data-hex'); }});
    var h = '<span class="td-swatch" style="background:' + hex + '"></span>';
    h += '<span class="td-name">' + c.name + '</span>';
    h += '<span class="td-stat">L*=' + c.L.toFixed(1) + ' &nbsp;a*=' + c.a.toFixed(1) + ' &nbsp;b*=' + c.b.toFixed(1) + '</span>';
    h += '<span class="td-stat">' + c.n_products.toLocaleString() + ' products</span>';
    if (s) {{
        h += '<span class="td-stat">Freq: ' + s.freq_pct.toFixed(1) + '% (' +
             (s.freq_deviation_pp >= 0 ? '+' : '') + s.freq_deviation_pp.toFixed(1) + 'pp)' +
             (s.freq_significant ? ' *' : '') + '</span>';
        h += '<span class="td-stat">' +
             (s.has_depth ? 'Depth: ' + s.depth_pct.toFixed(1) + '% (' +
             (s.depth_deviation_pp >= 0 ? '+' : '') + s.depth_deviation_pp.toFixed(1) + 'pp)' : 'Depth: n/a') +
             '</span>';
    }}
    var ps = bd.zonePriceStats ? bd.zonePriceStats[zi] : null;
    if (ps) {{
        h += '<span class="td-stat">Price: $' + ps.price_mean.toFixed(0) + ' (' +
             (ps.price_deviation_pct >= 0 ? '+' : '') + ps.price_deviation_pct.toFixed(1) + '%)' +
             (ps.price_significant ? ' *' : '') + '</span>';
    }}
    return h;
}}

function formatSelectionDetail(brand) {{
    var bd = brands[brand];
    var zones = Array.from(bd.selectedZones);
    var totalProducts = 0, totalDiscounted = 0, depthWS = 0, depthWT = 0;
    var priceSum = 0, priceCount = 0;
    for (var i = 0; i < zones.length; i++) {{
        var s = bd.zoneStats[zones[i]];
        if (s) {{
            totalProducts += s.n_products; totalDiscounted += s.n_discounted;
            if (s.has_depth) {{ depthWS += s.depth_pct * s.n_discounted; depthWT += s.n_discounted; }}
        }}
        var ps = bd.zonePriceStats ? bd.zonePriceStats[zones[i]] : null;
        if (ps) {{ priceSum += ps.price_mean * ps.n_priced; priceCount += ps.n_priced; }}
    }}
    var freqPct = totalProducts > 0 ? (totalDiscounted / totalProducts * 100) : 0;
    var freqDev = freqPct - bd.baseline.baseline_freq_pct;
    var depthPct = depthWT > 0 ? (depthWS / depthWT) : 0;
    var depthDev = depthWT > 0 ? (depthPct - bd.baseline.baseline_depth_pct) : 0;
    var h = '<span class="td-name">' + zones.length + ' zones selected</span>';
    h += '<span class="td-stat">' + totalProducts.toLocaleString() + ' products</span>';
    h += '<span class="td-stat">Freq: ' + freqPct.toFixed(1) + '% (' +
         (freqDev >= 0 ? '+' : '') + freqDev.toFixed(1) + 'pp)</span>';
    if (depthWT > 0) {{
        h += '<span class="td-stat">Depth: ' + depthPct.toFixed(1) + '% (' +
             (depthDev >= 0 ? '+' : '') + depthDev.toFixed(1) + 'pp)</span>';
    }}
    if (priceCount > 0) {{
        var avgPrice = priceSum / priceCount;
        var priceDev = bd.baseline.baseline_price > 0 ? (avgPrice - bd.baseline.baseline_price) / bd.baseline.baseline_price * 100 : 0;
        h += '<span class="td-stat">Price: $' + avgPrice.toFixed(0) + ' (' +
             (priceDev >= 0 ? '+' : '') + priceDev.toFixed(1) + '%)</span>';
    }}
    return h;
}}

function formatSelectionInfo(brand) {{
    var bd = brands[brand];
    var zones = Array.from(bd.selectedZones);
    var totalProducts = 0;
    for (var i = 0; i < zones.length; i++) {{
        var s = bd.zoneStats[zones[i]];
        if (s) totalProducts += s.n_products;
    }}
    var brandTotal = 0;
    for (var zt in bd.zoneCentroids) {{ var cc = bd.zoneCentroids[zt]; if (cc) brandTotal += cc.n_products; }}
    var pct = brandTotal > 0 ? (totalProducts / brandTotal * 100) : 0;
    var h = '<span class="ib-name">' + zones.length + ' zones selected</span>';
    h += '<span class="ib-sep">|</span>' + totalProducts.toLocaleString() + ' products';
    h += '<span class="ib-sep">|</span>' + pct.toFixed(1) + '%';
    h += '<span class="ib-clear" onclick="clearAllSelections()">Clear</span>';
    return h;
}}

function updateInfoBar() {{
    var bar = document.getElementById('infoBar');
    // Tally the selection across all brands.
    var totalZones = 0, nBrands = 0, totalProducts = 0, involvedTotal = 0, oneBrand = null;
    for (var i = 0; i < brandSlugs.length; i++) {{
        var bd = brands[brandSlugs[i]];
        var n = bd.selectedZones.size;
        if (n === 0) continue;
        nBrands++; oneBrand = brandSlugs[i]; totalZones += n;
        bd.selectedZones.forEach(function(zi) {{ var c = bd.zoneCentroids[zi]; if (c) totalProducts += c.n_products; }});
        for (var zt in bd.zoneCentroids) {{ var cc = bd.zoneCentroids[zt]; if (cc) involvedTotal += cc.n_products; }}
    }}
    if (nBrands > 1) {{
        var pctAll = involvedTotal > 0 ? (totalProducts / involvedTotal * 100) : 0;
        bar.style.color = '#fbbf24';
        bar.innerHTML = '<span class="ib-name">Selection</span><span class="ib-sep">|</span>' +
                        totalZones + ' zones across ' + nBrands + ' brands<span class="ib-sep">|</span>' +
                        totalProducts.toLocaleString() + ' products<span class="ib-sep">|</span>' +
                        pctAll.toFixed(1) + '%' +
                        '<span class="ib-clear" onclick="clearAllSelections()">Clear</span>';
    }} else if (nBrands === 1) {{
        bar.style.color = '#fbbf24';
        bar.innerHTML = '<span class="ib-brand">' + oneBrand + '</span><span class="ib-sep">|</span>' +
                        formatSelectionInfo(oneBrand);
    }} else if (focusBrand && brands[focusBrand].hoveredZone !== null) {{
        bar.style.color = '#bbb';
        bar.innerHTML = '<span class="ib-brand">' + focusBrand + '</span><span class="ib-sep">|</span>' +
                        formatZoneInfo(focusBrand, brands[focusBrand].hoveredZone);
    }} else {{
        bar.style.color = '';
        bar.innerHTML = '<span class="ib-dim">Hover for details. Left-click a zone for its thumbnails; left-click empty space to clear. Ctrl+click a zone to add/remove (a &Delta;E ball when &Delta;E radius select is on).</span>';
    }}
}}

// --- Hover ---
function handleHover(brand, zi) {{
    brands[brand].hoveredZone = zi;
    focusBrand = brand;
    if (brands[brand].selectedZones.size === 0) updateInfoBar();
}}
function handleHoverOut(brand) {{
    brands[brand].hoveredZone = null;
    if (brands[brand].selectedZones.size === 0) updateInfoBar();
}}

// --- Temporal filtering ---
function formatMonth(m) {{
    // 'YYYY-MM' -> 'Mon YYYY'
    var parts = m.split('-');
    var names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return names[parseInt(parts[1]) - 1] + ' ' + parts[0];
}}

function renderMonthGrid() {{
    if (!hasTemporal || allMonths.length < 2) return;
    var grid = document.getElementById('monthGrid');
    var monthSet = new Set(allMonths);
    // Determine year range
    var firstYear = parseInt(allMonths[0].split('-')[0]);
    var lastYear = parseInt(allMonths[allMonths.length - 1].split('-')[0]);
    var monthNames = ['1','2','3','4','5','6','7','8','9','10','11','12'];
    // Header row
    var h = '<div class="month-grid-header"></div>';
    for (var m = 0; m < 12; m++) {{
        h += '<div class="month-grid-header">' + monthNames[m] + '</div>';
    }}
    // Year rows
    for (var y = firstYear; y <= lastYear; y++) {{
        h += '<div class="month-grid-year">' + y + '</div>';
        for (var m = 1; m <= 12; m++) {{
            var key = y + '-' + (m < 10 ? '0' : '') + m;
            var idx = allMonths.indexOf(key);
            var hasData = idx >= 0;
            var cls = hasData ? 'month-cell has-data' : 'month-cell no-data';
            h += '<div class="' + cls + '"' + (hasData ? ' data-idx="' + idx + '"' : '') + '></div>';
        }}
    }}
    grid.innerHTML = h;
    setupMonthGridDrag();
    updateMonthGridSelection();
}}

function updateMonthGridSelection() {{
    document.querySelectorAll('.month-cell.selected').forEach(function(el) {{
        el.classList.remove('selected');
    }});
    document.querySelectorAll('.month-cell.has-data').forEach(function(el) {{
        var idx = parseInt(el.getAttribute('data-idx'));
        if (idx >= timeStartIdx && idx <= timeEndIdx) {{
            el.classList.add('selected');
        }}
    }});
    // Selection label
    var startM = formatMonth(allMonths[timeStartIdx]);
    var endM = formatMonth(allMonths[timeEndIdx]);
    var isAll = timeStartIdx === 0 && timeEndIdx === allMonths.length - 1;
    var label = isAll ? 'All months' : (startM === endM ? startM : startM + ' \u2013 ' + endM);
    document.getElementById('timeSelectionLabel').textContent = label;
}}

function applyWindowToClick(clickIdx) {{
    // Expand a single click to the current window size
    var half = Math.floor((animWindowMonths - 1) / 2);
    var start = clickIdx - half;
    var end = start + animWindowMonths - 1;
    // Clamp to valid range
    if (start < 0) {{ end -= start; start = 0; }}
    if (end >= allMonths.length) {{ start -= (end - allMonths.length + 1); end = allMonths.length - 1; }}
    start = Math.max(0, start);
    end = Math.min(allMonths.length - 1, end);
    return [start, end];
}}

function setupMonthGridDrag() {{
    var grid = document.getElementById('monthGrid');
    var dragging = false;
    var dragStartIdx = null;
    grid.addEventListener('mousedown', function(e) {{
        var cell = e.target.closest('.month-cell.has-data');
        if (!cell) return;
        e.preventDefault();
        dragging = true;
        dragStartIdx = parseInt(cell.getAttribute('data-idx'));
        var range = applyWindowToClick(dragStartIdx);
        timeStartIdx = range[0];
        timeEndIdx = range[1];
        updateMonthGridSelection();
    }});
    document.addEventListener('mousemove', function(e) {{
        if (!dragging) return;
        var cell = e.target.closest('.month-cell.has-data');
        if (!cell) return;
        var idx = parseInt(cell.getAttribute('data-idx'));
        // Drag expands from the original click's window
        var range = applyWindowToClick(dragStartIdx);
        timeStartIdx = Math.min(range[0], idx);
        timeEndIdx = Math.max(range[1], idx);
        updateMonthGridSelection();
    }});
    document.addEventListener('mouseup', function() {{
        if (dragging) {{
            dragging = false;
            dragStartIdx = null;
            applyTimeFilter();
        }}
    }});
}}

function getSelectedMonths() {{
    var months = [];
    for (var i = timeStartIdx; i <= timeEndIdx; i++) {{
        months.push(allMonths[i]);
    }}
    return months;
}}

function updateTemporalIndicators() {{
    document.querySelectorAll('.swatch').forEach(function(el) {{
        el.classList.remove('low-power', 'empty-zone');
    }});
    if (timeFiltered && currentView !== 'palette') {{
        for (var slug in brands) {{
            var bd = brands[slug];
            if (!bd.temporalStats) continue;
            document.querySelectorAll('#panel-' + slug + ' .swatch').forEach(function(el) {{
                var zi = el.getAttribute('data-zi');
                var ts = bd.temporalStats[zi];
                var n = ts ? ts.n_products : 0;
                if (n === 0) el.classList.add('empty-zone');
                else if (n < 14) el.classList.add('low-power');
            }});
        }}
    }}
}}

function applyTimeFilter() {{
    var isAll = timeStartIdx === 0 && timeEndIdx === allMonths.length - 1;
    timeFiltered = !isAll;
    var selectedMonths = new Set(getSelectedMonths());

    for (var slug in brands) {{
        var bd = brands[slug];
        if (!timeFiltered) {{
            if (bd.originalCentroids) {{
                for (var zi in bd.originalCentroids) {{
                    bd.zoneCentroids[zi].n_products = bd.originalCentroids[zi];
                }}
            }}
            bd.temporalStats = null;
            continue;
        }}
        if (!bd.originalCentroids) {{
            bd.originalCentroids = {{}};
            for (var zi in bd.zoneCentroids) {{
                bd.originalCentroids[zi] = bd.zoneCentroids[zi].n_products;
            }}
        }}
        var temporal = bd.temporal || {{}};
        var zoneTotals = {{}};
        var grandTotal = 0, grandDisc = 0, grandPriceSum = 0, grandNPriced = 0;
        for (var month in temporal) {{
            if (!selectedMonths.has(month)) continue;
            var monthData = temporal[month];
            for (var zi in monthData) {{
                var d = monthData[zi];
                if (!zoneTotals[zi]) zoneTotals[zi] = {{n:0, disc:0, depthSum:0, priceSum:0, nPriced:0}};
                zoneTotals[zi].n += d.n;
                zoneTotals[zi].disc += d.disc;
                zoneTotals[zi].depthSum += d.depth_sum;
                zoneTotals[zi].priceSum += d.price_sum;
                zoneTotals[zi].nPriced += d.n_priced;
                grandTotal += d.n;
                grandDisc += d.disc;
                grandPriceSum += d.price_sum;
                grandNPriced += d.n_priced;
            }}
        }}
        var baseFreq = grandTotal > 0 ? grandDisc / grandTotal * 100 : 0;
        var basePrice = grandNPriced > 0 ? grandPriceSum / grandNPriced : 0;
        var grandDepthSum = 0, grandDepthN = 0;
        for (var zi in zoneTotals) {{
            grandDepthSum += zoneTotals[zi].depthSum;
            grandDepthN += zoneTotals[zi].disc;
        }}
        var baseDepth = grandDepthN > 0 ? grandDepthSum / grandDepthN : bd.baseline.baseline_depth_pct;

        bd.temporalStats = {{}};
        bd.temporalBaseline = {{baseline_freq_pct: baseFreq, baseline_depth_pct: baseDepth, baseline_price: basePrice}};
        for (var zi in bd.zoneCentroids) {{
            var t = zoneTotals[zi] || {{n:0, disc:0, depthSum:0, priceSum:0, nPriced:0}};
            var freqPct = t.n > 0 ? t.disc / t.n * 100 : 0;
            var depthPct = t.disc > 0 ? t.depthSum / t.disc : 0;
            var priceMean = t.nPriced > 0 ? t.priceSum / t.nPriced : 0;
            bd.temporalStats[zi] = {{
                n_products: t.n, n_discounted: t.disc,
                freq_pct: freqPct,
                freq_deviation_pp: freqPct - baseFreq,
                depth_pct: depthPct,
                depth_deviation_pp: depthPct - baseDepth,
                has_depth: t.disc > 0,
                freq_significant: false,
                price_mean: priceMean,
                price_deviation_pct: basePrice > 0 ? (priceMean - basePrice) / basePrice * 100 : 0,
                n_priced: t.nPriced
            }};
            bd.zoneCentroids[zi].n_products = t.n;
        }}
    }}
    updateTemporalIndicators();
    document.getElementById('temporalWarning').style.display = (timeFiltered && currentView !== 'palette') ? '' : 'none';
    if (smoothingEnabled) {{
        for (var slug in brands) computeSmoothedDeviations(slug);
    }}
    recomputeAllLayouts();
}}

function toggleTemporal(enabled) {{
    var controls = document.getElementById('temporalControls');
    if (enabled) {{
        controls.style.opacity = '';
        controls.style.pointerEvents = '';
    }} else {{
        controls.style.opacity = '0.3';
        controls.style.pointerEvents = 'none';
        if (animPlaying) togglePlay();
        timeStartIdx = 0;
        timeEndIdx = allMonths.length - 1;
        updateMonthGridSelection();
        applyTimeFilter();
    }}
}}

function resetTimeRange() {{
    if (animPlaying) togglePlay();
    timeStartIdx = 0;
    timeEndIdx = allMonths.length - 1;
    updateMonthGridSelection();
    applyTimeFilter();
}}

function togglePlay() {{
    if (animPlaying) {{
        clearInterval(animInterval);
        animPlaying = false;
        document.getElementById('playBtn').innerHTML = '<svg width="12" height="12" viewBox="0 0 12 12"><polygon points="2,0 12,6 2,12" fill="currentColor"/></svg>';
    }} else {{
        animPlaying = true;
        document.getElementById('playBtn').innerHTML = '<svg width="12" height="12" viewBox="0 0 12 12"><rect x="1" y="0" width="3.5" height="12" fill="currentColor"/><rect x="7.5" y="0" width="3.5" height="12" fill="currentColor"/></svg>';
        animStep();
        animInterval = setInterval(animStep, animSpeed);
    }}
}}

function animStep() {{
    timeEndIdx++;
    if (timeEndIdx >= allMonths.length) {{
        // Loop back to start on the NEXT step after showing the last month
        timeStartIdx = 0;
        timeEndIdx = Math.min(animWindowMonths - 1, allMonths.length - 1);
    }} else {{
        timeStartIdx = Math.max(0, timeEndIdx - animWindowMonths + 1);
    }}
    updateMonthGridSelection();
    applyTimeFilter();
}}

function setAnimWindow(months) {{
    animWindowMonths = months;
    document.querySelectorAll('.window-btn').forEach(function(btn) {{
        btn.classList.toggle('active', parseInt(btn.getAttribute('data-months')) === months);
    }});
    if (animPlaying) {{
        clearInterval(animInterval);
        animStep();
        animInterval = setInterval(animStep, animSpeed);
    }}
}}

document.getElementById('animSpeedSlider').addEventListener('input', function() {{
    animSpeed = parseInt(this.value);
    document.getElementById('animSpeedValue').textContent = (animSpeed / 1000).toFixed(1) + 's';
    if (animPlaying) {{
        clearInterval(animInterval);
        animInterval = setInterval(animStep, animSpeed);
    }}
}});

// --- Empirical Bayes smoothing ---
function computeSmoothedDeviations(slug) {{
    var bd = brands[slug];
    var zc = bd.zoneCentroids;
    var zs = (timeFiltered && bd.temporalStats) ? bd.temporalStats : bd.zoneStats;
    var ps = (timeFiltered && bd.temporalStats) ? bd.temporalStats : (bd.zonePriceStats || {{}});
    var zoneIds = Object.keys(zc);
    var n = zoneIds.length;
    // Compute pairwise Euclidean LAB distances (cache per brand)
    if (!bd.labDists) {{
        bd.labDists = {{}};
        for (var i = 0; i < n; i++) {{
            var ci = zc[zoneIds[i]];
            bd.labDists[zoneIds[i]] = {{}};
            for (var j = 0; j < n; j++) {{
                if (i === j) {{ bd.labDists[zoneIds[i]][zoneIds[j]] = 0; continue; }}
                var cj = zc[zoneIds[j]];
                var dL = ci.L - cj.L, da = ci.a - cj.a, db = ci.b - cj.b;
                bd.labDists[zoneIds[i]][zoneIds[j]] = Math.sqrt(dL*dL + da*da + db*db);
            }}
        }}
    }}
    // Kappa = median n_products
    var nArr = [];
    for (var i = 0; i < n; i++) {{
        var s = zs[zoneIds[i]];
        if (s) nArr.push(s.n_products);
    }}
    nArr.sort(function(a,b){{ return a-b; }});
    var kappa = nArr.length > 0 ? nArr[Math.floor(nArr.length / 2)] : 1;
    // Compute smoothed deviations
    bd.smoothedStats = {{}};
    for (var i = 0; i < n; i++) {{
        var zi = zoneIds[i];
        var si = zs[zi];
        if (!si) continue;
        var ni = si.n_products;
        var wSum = 0, freqW = 0, depthW = 0, priceW = 0;
        for (var j = 0; j < n; j++) {{
            if (i === j) continue;
            var zj = zoneIds[j];
            var dist = bd.labDists[zi][zj];
            if (dist >= smoothBandwidth) continue;
            var sj = zs[zj];
            if (!sj || sj.n_products === 0) continue;
            var w = (1 - dist / smoothBandwidth) * sj.n_products;
            wSum += w;
            freqW += w * sj.freq_deviation_pp;
            depthW += w * sj.depth_deviation_pp;
            var pj = ps[zj];
            if (pj) priceW += w * pj.price_deviation_pct;
        }}
        var freqPrior = wSum > 0 ? freqW / wSum : si.freq_deviation_pp;
        var depthPrior = wSum > 0 ? depthW / wSum : si.depth_deviation_pp;
        var pricePrior = wSum > 0 ? priceW / wSum : (ps[zi] ? ps[zi].price_deviation_pct : 0);
        var shrink = kappa / (ni + kappa);
        bd.smoothedStats[zi] = {{
            freq_deviation_pp: (1 - shrink) * si.freq_deviation_pp + shrink * freqPrior,
            depth_deviation_pp: (1 - shrink) * si.depth_deviation_pp + shrink * depthPrior,
            price_deviation_pct: (1 - shrink) * (ps[zi] ? ps[zi].price_deviation_pct : 0) + shrink * pricePrior
        }};
    }}
}}

function toggleSmoothing(enabled) {{
    smoothingEnabled = enabled;
    document.getElementById('smoothSliderWrap').style.display = enabled ? '' : 'none';
    if (enabled) {{
        for (var slug in brands) computeSmoothedDeviations(slug);
    }}
    setView(currentView);
}}

document.getElementById('smoothBandwidth').addEventListener('input', function() {{
    smoothBandwidth = parseFloat(this.value);
    document.getElementById('smoothBWValue').textContent = this.value;
    if (smoothingEnabled) {{
        for (var slug in brands) {{
            computeSmoothedDeviations(slug);
        }}
        setView(currentView);
    }}
}});

// --- View switching ---
function setView(mode) {{
    currentView = mode;
    var isHeatmap = mode !== 'palette';
    document.querySelectorAll('#ctrl-panel-analysis .view-option').forEach(function(opt) {{
        opt.classList.toggle('active', opt.getAttribute('data-mode') === mode);
    }});
    for (var slug in brands) {{
        var bd = brands[slug];
        document.querySelectorAll('#panel-' + slug + ' .swatch').forEach(function(el) {{
            var zi = el.getAttribute('data-zi');
            var origHex = el.getAttribute('data-hex');
            var dot = el.querySelector('.centroid-dot');
            var s = (timeFiltered && bd.temporalStats) ? bd.temporalStats[zi] : bd.zoneStats[zi];
            var ps = (timeFiltered && bd.temporalStats && bd.temporalStats[zi]) ?
                     bd.temporalStats[zi] : (bd.zonePriceStats ? bd.zonePriceStats[zi] : null);
            if (!isHeatmap || (!s && !ps)) {{
                el.style.background = origHex;
                if (dot) dot.style.display = 'none';
            }} else {{
                var dev, maxAbs;
                var sm = (smoothingEnabled && bd.smoothedStats) ? bd.smoothedStats[zi] : null;
                if (mode === 'price') {{
                    dev = sm ? sm.price_deviation_pct : (ps ? ps.price_deviation_pct : 0);
                    maxAbs = priceMaxAbs;
                }} else if (mode === 'freq') {{
                    dev = sm ? sm.freq_deviation_pp : (s ? s.freq_deviation_pp : 0);
                    maxAbs = freqMaxAbs;
                }} else {{
                    dev = sm ? sm.depth_deviation_pp : (s ? s.depth_deviation_pp : 0);
                    maxAbs = depthMaxAbs;
                }}
                el.style.background = devToColor(dev, maxAbs);
                if (dot) dot.style.display = 'block';
            }}
        }});
    }}
    updateInfoBar();
    document.getElementById('smoothingControls').style.display = isHeatmap ? 'flex' : 'none';
    if (isHeatmap) {{
        var maxAbs = mode === 'freq' ? freqMaxAbs : mode === 'depth' ? depthMaxAbs : priceMaxAbs;
        var label = mode === 'freq' ? 'Freq deviation (pp)' : mode === 'depth' ? 'Depth deviation (pp)' : 'Price deviation (%)';
        if (smoothingEnabled) label += ' (smoothed)';
        drawColorbar(maxAbs, label);
    }} else {{
        document.getElementById('colorbarWrap').classList.remove('visible');
    }}
    updateTemporalIndicators();
    document.getElementById('temporalWarning').style.display = (timeFiltered && currentView !== 'palette') ? '' : 'none';
}}

// --- Zoom ---
function applyZoom() {{
    for (var slug in brands) {{
        if (!activeBrands.has(slug)) continue;
        var bd = brands[slug];
        var canvas = document.getElementById('canvas-' + slug);
        var container = document.getElementById('zoomContainer-' + slug);
        var baseScale = 400 / bd.baseW;
        var scale = baseScale * zoom;
        canvas.style.transform = 'scale(' + scale + ')';
        container.style.width = (bd.baseW * scale) + 'px';
        container.style.height = (bd.baseH * scale) + 'px';
    }}
    document.getElementById('zoomLevel').textContent = Math.round(zoom * 100) + '%';
}}
function syncZoomSlider() {{
    var pct = Math.round(zoom * 100);
    document.getElementById('zoomSlider').value = Math.min(200, Math.max(0, pct));
}}

document.getElementById('zoomSlider').addEventListener('input', function() {{
    zoom = parseInt(this.value) / 100;
    if (zoom < 0.01) zoom = 0.01;
    applyZoom();
}});

document.getElementById('panelsContainer').addEventListener('wheel', function(e) {{
    if (e.ctrlKey) {{
        e.preventDefault();
        zoom = Math.max(0.01, Math.min(2, zoom * (e.deltaY > 0 ? 1/1.15 : 1.15)));
        applyZoom();
        syncZoomSlider();
    }}
}}, {{ passive: false }});

// --- Width slider ---
function setPanelWidth(w) {{
    panelWidth = w;
    document.getElementById('widthSlider').value = w;
    document.getElementById('widthValue').textContent = w + 'px';
    document.getElementById('panelsContainer').style.setProperty('--panel-width', w + 'px');
    applyZoom();
}}
document.getElementById('widthSlider').addEventListener('input', function() {{
    setPanelWidth(parseInt(this.value));
}});

// --- Panel edge resize ---
(function() {{
    var resizeDrag = null;
    document.querySelectorAll('.panel-resize').forEach(function(handle) {{
        handle.addEventListener('mousedown', function(e) {{
            e.preventDefault();
            handle.classList.add('dragging');
            resizeDrag = {{ startX: e.clientX, startWidth: panelWidth, handle: handle }};
            document.addEventListener('mousemove', onResizeMove);
            document.addEventListener('mouseup', onResizeEnd);
        }});
    }});
    function onResizeMove(e) {{
        if (!resizeDrag) return;
        var delta = e.clientX - resizeDrag.startX;
        var newW = Math.round(Math.max(200, Math.min(840, resizeDrag.startWidth + delta)));
        setPanelWidth(newW);
    }}
    function onResizeEnd() {{
        if (resizeDrag) resizeDrag.handle.classList.remove('dragging');
        resizeDrag = null;
        document.removeEventListener('mousemove', onResizeMove);
        document.removeEventListener('mouseup', onResizeEnd);
    }}
}})();

// --- Brand toggles ---
function toggleBrand(slug) {{
    var panel = document.getElementById('panel-' + slug);
    var btn = document.querySelector('.brand-toggle[data-brand="' + slug + '"]');
    if (activeBrands.has(slug)) {{
        activeBrands.delete(slug);
        panel.classList.add('hidden');
        btn.classList.remove('active');
    }} else {{
        activeBrands.add(slug);
        panel.classList.remove('hidden');
        btn.classList.add('active');
        applyZoom();
    }}
}}

function showAllBrands() {{
    brandSlugs.forEach(function(slug) {{
        if (!activeBrands.has(slug)) {{
            activeBrands.add(slug);
            document.getElementById('panel-' + slug).classList.remove('hidden');
            document.querySelector('.brand-toggle[data-brand="' + slug + '"]').classList.add('active');
        }}
    }});
    applyZoom();
}}

function hideAllBrands() {{
    brandSlugs.forEach(function(slug) {{
        if (activeBrands.has(slug)) {{
            activeBrands.delete(slug);
            document.getElementById('panel-' + slug).classList.add('hidden');
            document.querySelector('.brand-toggle[data-brand="' + slug + '"]').classList.remove('active');
        }}
    }});
}}

// --- Synced scroll ---
var scrollLock = false;
document.querySelectorAll('.brand-scroll').forEach(function(el) {{
    el.addEventListener('scroll', function() {{
        if (scrollLock) return;
        scrollLock = true;
        var srcBrand = this.getAttribute('data-brand');
        var maxTop = this.scrollHeight - this.clientHeight;
        var ratio = maxTop > 0 ? this.scrollTop / maxTop : 0;
        document.querySelectorAll('.brand-scroll').forEach(function(other) {{
            if (other.getAttribute('data-brand') === srcBrand) return;
            if (other.closest('.brand-panel.hidden')) return;
            var otherMax = other.scrollHeight - other.clientHeight;
            other.scrollTop = ratio * otherMax;
        }});
        requestAnimationFrame(function() {{ scrollLock = false; }});
    }});
}});

// --- Click handling ---
// CIEDE2000 color difference (ΔE00) between two zone centroids — the same
// metric the zones were built with (build_color_clusters.py / consolidate_colors.py).
function deltaE00(c1, c2) {{
    var L1=c1.L, a1=c1.a, b1=c1.b, L2=c2.L, a2=c2.a, b2=c2.b;
    var kL=1, kC=1, kH=1;
    var C1=Math.sqrt(a1*a1+b1*b1), C2=Math.sqrt(a2*a2+b2*b2);
    var Cbar=(C1+C2)/2;
    var Cbar7=Math.pow(Cbar,7);
    var G=0.5*(1-Math.sqrt(Cbar7/(Cbar7+6103515625)));   // 25^7 = 6103515625
    var a1p=(1+G)*a1, a2p=(1+G)*a2;
    var C1p=Math.sqrt(a1p*a1p+b1*b1), C2p=Math.sqrt(a2p*a2p+b2*b2);
    var h1p=Math.atan2(b1,a1p); if(h1p<0) h1p+=2*Math.PI;
    var h2p=Math.atan2(b2,a2p); if(h2p<0) h2p+=2*Math.PI;
    var dLp=L2-L1;
    var dCp=C2p-C1p;
    var dhp=0;
    if (C1p*C2p!==0) {{
        dhp=h2p-h1p;
        if (dhp>Math.PI) dhp-=2*Math.PI;
        else if (dhp<-Math.PI) dhp+=2*Math.PI;
    }}
    var dHp=2*Math.sqrt(C1p*C2p)*Math.sin(dhp/2);
    var Lbarp=(L1+L2)/2;
    var Cbarp=(C1p+C2p)/2;
    var hbarp;
    if (C1p*C2p===0) {{ hbarp=h1p+h2p; }}
    else if (Math.abs(h1p-h2p)>Math.PI) {{ hbarp=(h1p+h2p+2*Math.PI)/2; }}
    else {{ hbarp=(h1p+h2p)/2; }}
    var T=1-0.17*Math.cos(hbarp-Math.PI/6)+0.24*Math.cos(2*hbarp)
        +0.32*Math.cos(3*hbarp+Math.PI/30)-0.20*Math.cos(4*hbarp-21*Math.PI/60);
    var dtheta=(Math.PI/6)*Math.exp(-Math.pow((hbarp*180/Math.PI-275)/25,2));
    var Cbarp7=Math.pow(Cbarp,7);
    var Rc=2*Math.sqrt(Cbarp7/(Cbarp7+6103515625));
    var Sl=1+(0.015*Math.pow(Lbarp-50,2))/Math.sqrt(20+Math.pow(Lbarp-50,2));
    var Sc=1+0.045*Cbarp;
    var Sh=1+0.015*Cbarp*T;
    var Rt=-Math.sin(2*dtheta)*Rc;
    return Math.sqrt(
        Math.pow(dLp/(kL*Sl),2)+Math.pow(dCp/(kC*Sc),2)+Math.pow(dHp/(kH*Sh),2)+
        Rt*(dCp/(kC*Sc))*(dHp/(kH*Sh))
    );
}}

function setZoneSelected(brand, zi, on) {{
    var bd = brands[brand];
    if (on) bd.selectedZones.add(zi); else bd.selectedZones.delete(zi);
    document.querySelectorAll('#panel-' + brand + ' .swatch[data-zi="' + zi + '"]').forEach(function(el) {{
        el.classList.toggle('selected', on);
    }});
}}

// Add (on=true) or remove (on=false) every zone within selDeltaE of the clicked
// zone's centroid. Scope is the clicked brand or all brands.
function applyBallSelection(brand, zi, on) {{
    var seed = brands[brand].zoneCentroids[zi];
    if (!seed) return;
    var targets = (selScope === 'all') ? brandSlugs : [brand];
    for (var ti = 0; ti < targets.length; ti++) {{
        var zc = brands[targets[ti]].zoneCentroids;
        for (var z in zc) {{
            if (zc[z] && deltaE00(seed, zc[z]) <= selDeltaE) setZoneSelected(targets[ti], z, on);
        }}
    }}
    for (var ti2 = 0; ti2 < targets.length; ti2++) showMultiThumbs(targets[ti2]);
    updateSelectionStats();
    updateInfoBar();
}}

function handleClick(e, brand, zi) {{
    focusBrand = brand;
    zi = '' + zi;  // normalize to string so it matches for-in keys in applyBallSelection
    if (e.ctrlKey || e.metaKey) {{
        // Toggle on the clicked zone's current state: unselected -> add,
        // selected -> remove. ΔE toggle ON applies it to the whole ball.
        var adding = !brands[brand].selectedZones.has(zi);
        if (deltaSelectEnabled) {{
            applyBallSelection(brand, zi, adding);
        }} else {{
            setZoneSelected(brand, zi, adding);
            showMultiThumbs(brand);
            updateSelectionStats();
            updateInfoBar();
        }}
        return;
    }}
    // Plain left-click: inspect this zone's thumbnails; selection unchanged.
    showThumbs(brand, zi);
}}

function clearSelection(brand) {{
    brands[brand].selectedZones.clear();
    document.querySelectorAll('#panel-' + brand + ' .swatch.selected').forEach(function(el) {{ el.classList.remove('selected'); }});
    showMultiThumbs(brand);
    updateSelectionStats();
    updateInfoBar();
}}

function clearAllSelections() {{
    for (var s in brands) brands[s].selectedZones.clear();
    document.querySelectorAll('.swatch.selected').forEach(function(el) {{ el.classList.remove('selected'); }});
    for (var s2 in brands) showMultiThumbs(s2);
    updateSelectionStats();
    updateInfoBar();
}}

// Live per-brand selection breakdown for the Selection Tools panel.
function updateSelectionStats() {{
    var el = document.getElementById('selStats');
    if (!el) return;
    var totalZones = 0, totalProducts = 0, nBrands = 0, rows = '';
    for (var i = 0; i < brandSlugs.length; i++) {{
        var slug = brandSlugs[i], bd = brands[slug];
        var zones = Array.from(bd.selectedZones);
        if (zones.length === 0) continue;
        nBrands++;
        var prod = 0, chips = '';
        for (var k = 0; k < zones.length; k++) {{
            var c = bd.zoneCentroids[zones[k]];
            if (c) prod += c.n_products;
        }}
        totalZones += zones.length; totalProducts += prod;
        var brandTotal = 0;
        for (var zt in bd.zoneCentroids) {{ var cc = bd.zoneCentroids[zt]; if (cc) brandTotal += cc.n_products; }}
        var pct = brandTotal > 0 ? (prod / brandTotal * 100) : 0;
        var capped = zones.slice(0, 48);
        for (var k2 = 0; k2 < capped.length; k2++) {{
            var hex = '';
            document.querySelectorAll('#panel-' + slug + ' .swatch[data-zi="' + capped[k2] + '"]').forEach(function(s) {{ hex = s.getAttribute('data-hex'); }});
            chips += '<span class="sel-chip" style="background:' + hex + '"></span>';
        }}
        rows += '<div class="sel-brand-row"><div class="sel-brand-head">'
             + '<span class="sel-brand-name">' + (bd.name || slug) + '</span>'
             + '<span class="sel-brand-stat">' + zones.length + ' zones &middot; ' + prod.toLocaleString() + ' &middot; ' + pct.toFixed(1) + '%</span></div>'
             + '<div class="sel-chips">' + chips + '</div></div>';
    }}
    var head;
    if (totalZones === 0) {{
        head = '<div class="sel-empty">No zones selected. Ctrl+click a zone to select' + (deltaSelectEnabled ? (' all within &Delta;E ' + selDeltaE) : ' it') + '.</div>';
    }} else {{
        head = '<div class="sel-total">' + totalZones + ' zones &middot; ' + nBrands + ' brand' + (nBrands === 1 ? '' : 's')
             + ' &middot; ' + totalProducts.toLocaleString() + ' products</div>';
    }}
    el.innerHTML = head + rows;
}}

function setSelScope(allBrands) {{
    selScope = allBrands ? 'all' : 'brand';
}}

function toggleDeltaSelect(enabled) {{
    deltaSelectEnabled = enabled;
    var row = document.getElementById('selRadiusRow');
    if (row) {{
        row.style.opacity = enabled ? '' : '0.4';
        row.style.pointerEvents = enabled ? '' : 'none';
    }}
}}

// --- Thumbnails ---
function toggleThumbs(brand) {{
    var bd = brands[brand];
    bd.thumbExpanded = !bd.thumbExpanded;
    document.getElementById('thumbs-' + brand).classList.toggle('collapsed', !bd.thumbExpanded);
    var toggle = document.getElementById('thumb-toggle-' + brand);
    var bn = toggle.getAttribute('data-brand-name');
    var arrow = bd.thumbExpanded ? '\u25BC' : '\u25B6';
    var right = toggle.querySelector('.thumb-right');
    toggle.firstElementChild.innerHTML = arrow + ' <span class="thumb-brand">' + bn + '</span>';
}}

function showThumbs(brand, zi) {{
    var bd = brands[brand];
    document.querySelectorAll('#panel-' + brand + ' .swatch.active').forEach(function(el) {{ el.classList.remove('active'); }});
    document.querySelectorAll('#panel-' + brand + ' .swatch[data-zi="'+zi+'"]').forEach(function(el) {{ el.classList.add('active'); }});


    var files = bd.zoneThumbs[zi] || [];
    var grid = document.getElementById('thumb-grid-' + brand);
    var toggle = document.getElementById('thumb-toggle-' + brand);

    if (files.length === 0) {{
        grid.innerHTML = '<span class="empty">No thumbnails</span>';
    }} else {{
        var h = '';
        for (var i = 0; i < files.length; i++) {{
            h += '<img src="' + bd.thumbDir + '/' + files[i] + '" loading="lazy">';
        }}
        grid.innerHTML = h;
    }}
    var bn = toggle.getAttribute('data-brand-name');
    toggle.firstElementChild.innerHTML = '\u25BC <span class="thumb-brand">' + bn + '</span>';
    toggle.querySelector('.thumb-right').textContent = 'Thumbnails (' + files.length + ')';
    bd.thumbExpanded = true;
    document.getElementById('thumbs-' + brand).classList.remove('collapsed');
}}

function showMultiThumbs(brand) {{
    var bd = brands[brand];
    var zones = Array.from(bd.selectedZones);
    var grid = document.getElementById('thumb-grid-' + brand);
    var toggle = document.getElementById('thumb-toggle-' + brand);

    if (zones.length === 0) {{
        grid.innerHTML = '<span class="empty">Click a zone to see thumbnails</span>';
        var bn = toggle.getAttribute('data-brand-name');
        toggle.firstElementChild.innerHTML = '\u25B6 <span class="thumb-brand">' + bn + '</span>';
        toggle.querySelector('.thumb-right').textContent = 'Thumbnails';
        return;
    }}
    if (zones.length === 1) {{ showThumbs(brand, zones[0]); return; }}

    // Farthest-point ordering across zones by LAB centroid
    var zoneData = [];
    for (var i = 0; i < zones.length; i++) {{
        var c = bd.zoneCentroids[zones[i]];
        var files = bd.zoneThumbs[zones[i]] || [];
        if (c && files.length > 0) zoneData.push({{ lab: [c.L, c.a, c.b], files: files }});
    }}
    if (zoneData.length === 0) {{
        grid.innerHTML = '<span class="empty">No thumbnails</span>';
        return;
    }}

    function labDist(a, b) {{
        var dL=a[0]-b[0], da=a[1]-b[1], db=a[2]-b[2];
        return Math.sqrt(dL*dL+da*da+db*db);
    }}
    var rem = []; for (var i=0;i<zoneData.length;i++) rem.push(i);
    var si=0; for (var i=1;i<rem.length;i++) if (zoneData[rem[i]].lab[0]<zoneData[rem[si]].lab[0]) si=i;
    var ord=[rem[si]]; rem.splice(si,1);
    while (rem.length>0) {{
        var bi=0,bd2=-1;
        for (var i=0;i<rem.length;i++) {{
            var mn=Infinity;
            for (var j=0;j<ord.length;j++) {{ var d=labDist(zoneData[rem[i]].lab,zoneData[ord[j]].lab); if(d<mn) mn=d; }}
            if (mn>bd2) {{ bd2=mn; bi=i; }}
        }}
        ord.push(rem[bi]); rem.splice(bi,1);
    }}

    var maxRank=0;
    for (var i=0;i<ord.length;i++) if (zoneData[ord[i]].files.length>maxRank) maxRank=zoneData[ord[i]].files.length;
    var totalFiles = 0;
    var h = '';
    for (var rank=0;rank<maxRank;rank++) {{
        for (var i=0;i<ord.length;i++) {{
            var files=zoneData[ord[i]].files;
            if (rank<files.length) {{ h+='<img src="'+bd.thumbDir+'/'+files[rank]+'" loading="lazy">'; totalFiles++; }}
        }}
    }}
    grid.innerHTML = h;
    var bn = toggle.getAttribute('data-brand-name');
    toggle.firstElementChild.innerHTML = '\u25BC <span class="thumb-brand">' + bn + '</span>';
    toggle.querySelector('.thumb-right').textContent = 'Thumbnails (' + totalFiles + ')';
    bd.thumbExpanded = true;
    document.getElementById('thumbs-' + brand).classList.remove('collapsed');
}}

// --- Compatibility checks ---
function dismissCompat() {{
    document.getElementById('compatOverlay').classList.add('hidden');
}}
(function() {{
    var warnings = [];
    if (window.innerWidth < 1024) {{
        warnings.push('This tool is designed for screens at least 1024px wide. Some controls and panels may not display correctly at your current resolution.');
    }}
    var isTouch = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0);
    if (isTouch) {{
        warnings.push('This tool relies on mouse hover and click interactions that may not work well on touch devices. For the best experience, use a device with a mouse or trackpad.');
    }}
    if (warnings.length > 0) {{
        var title = (warnings.length > 1) ? 'Compatibility Notice' :
                    (window.innerWidth < 1024 ? 'Small Screen Detected' : 'Touch Device Detected');
        document.getElementById('compatTitle').textContent = title;
        document.getElementById('compatMsg').innerHTML = warnings.join('<br><br>');
        document.getElementById('compatOverlay').classList.remove('hidden');
    }}
}})();

// --- Responsive init ---
(function() {{
    var vw = window.innerWidth;
    var sidebarW = Math.round(Math.min(360, Math.max(280, vw * 0.28)));
    document.documentElement.style.setProperty('--sidebar-width', sidebarW + 'px');
    var available = vw - sidebarW;
    var initPanelW = Math.round(Math.min(440, Math.max(200, available * 0.6)));
    panelWidth = initPanelW;
    document.getElementById('panelsContainer').style.setProperty('--panel-width', initPanelW + 'px');
    document.getElementById('widthSlider').value = initPanelW;
    document.getElementById('widthValue').textContent = initPanelW + 'px';
}})();

// --- Init ---
document.getElementById('zoomSlider').value = 100;
document.getElementById('hueOffsetSlider').value = 45;
document.getElementById('globalScaleToggle').checked = true;
document.getElementById('satToggle').checked = true;
document.getElementById('temporalToggle').checked = false;
document.getElementById('smoothToggle').checked = true;
document.getElementById('animSpeedSlider').value = 1000;
// Selection Tools defaults (override any browser-cached form state)
document.getElementById('selScopeToggle').checked = false;
document.getElementById('deltaSelectToggle').checked = false;
document.getElementById('selDeltaSlider').value = 8;
document.getElementById('selDeltaValue').textContent = '8';
setSelScope(false);
toggleDeltaSelect(false);
document.getElementById('selDeltaSlider').addEventListener('input', function() {{
    selDeltaE = parseInt(this.value);
    document.getElementById('selDeltaValue').textContent = this.value;
    updateSelectionStats();
}});
// Left-click on empty plotting area (not a zone) clears the selection.
document.getElementById('panelsContainer').addEventListener('click', function(e) {{
    if (e.target.closest('.swatch')) return;          // zone click — handled by the swatch
    if (!e.target.closest('.brand-scroll')) return;   // only the zone plotting area, not thumbnails/UI
    clearAllSelections();
}});
updateSelectionStats();
for (var slug in brands) computeSmoothedDeviations(slug);
renderSliderMarkers();
if (hasTemporal) {{
    renderMonthGrid();
    document.getElementById('temporalUnavailable').style.display = 'none';
}} else {{
    document.getElementById('temporalToggle').disabled = true;
    document.getElementById('temporalUnavailable').style.display = '';
}}
recomputeAllLayouts();
</script>
</body></html>""")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(html))
    print(f"Multi-brand preview: {output_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

BRANDS = [
    ('Nike Mens', 'nike_mens'),
    ('Nike Womens', 'nike_womens'),
    ('Adidas Mens', 'adidas_mens'),
    ('Adidas Womens', 'adidas_womens'),
    ('Puma Mens', 'puma_mens'),
    ('Puma Womens', 'puma_womens'),
    ('Lulu Mens', 'lulu_mens'),
    ('Lulu Womens', 'lulu_womens'),
    ('UA Mens', 'ua_mens'),
    ('UA Womens', 'ua_womens'),
]


def main():
    # No brand/gender args — the explorer renders all brand/gender panels in one page.
    base = os.path.dirname(os.path.abspath(__file__))
    output_base = os.path.join(base, 'outputs')

    brands_data = []
    for name, slug in BRANDS:
        brand_dir = os.path.join(output_base, slug)
        if not os.path.isdir(brand_dir):
            print(f"  WARNING: {brand_dir} not found, skipping {name}")
            continue
        bd = load_brand_data(name, slug, brand_dir, radius=5)
        brands_data.append(bd)
        print(f"  {name}: {len(bd['clusters'])} clusters, {len(bd['zones'])} zones, "
              f"baseline freq={bd['baseline']['baseline_freq_pct']:.1f}%")

    if not brands_data:
        print("ERROR: No brand data found. Run consolidate-colors + assign-zones first.")
        return

    # Global colorscale bounds (max across all brands)
    global_freq_max = 10
    global_depth_max = 10
    global_price_max = 10
    for bd in brands_data:
        for s in bd['zone_stats'].values():
            global_freq_max = max(global_freq_max, abs(s['freq_deviation_pp']))
            global_depth_max = max(global_depth_max, abs(s['depth_deviation_pp']))
        for s in bd['zone_price_stats'].values():
            global_price_max = max(global_price_max, abs(s['price_deviation_pct']))
    global_freq_max = math.ceil(global_freq_max)
    global_depth_max = math.ceil(global_depth_max)
    global_price_max = math.ceil(global_price_max)

    output_path = os.path.join(output_base, 'palette_explorer.html')
    generate_multi_brand_preview(output_path, brands_data, global_freq_max, global_depth_max, global_price_max)


if __name__ == '__main__':
    main()
