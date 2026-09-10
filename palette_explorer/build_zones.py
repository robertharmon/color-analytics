"""
ENTRY - run: python cli.py build-zones  (manual zone builder). Also imported by assign_zones.py.

Cluster Zone Builder
======================

Interactive HTML tool for manually grouping CIEDE2000 clusters into color
zones. All clusters are displayed as color swatches in CIEDE2000 perceptual
order. The user creates groups, clicks swatches to assign them, and verifies
coherence by inspecting group members side by side.

When a brand is provided, product images are sampled per cluster and displayed
in a preview panel — ordered by farthest-point diversity — so the user can
verify that a group's products look visually coherent.

State persists in LocalStorage between sessions. Export writes cluster_zones.json,
which assign-zones reads as its training reference and palette-explorer renders.

Usage:
    python cli.py build-zones              # swatches only
    python cli.py build-zones nike          # with product images
"""

import json
import os
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, leaves_list

from shared.ciede2000 import ciede2000_pairwise

# =============================================================================
# CONFIGURATION
# =============================================================================

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'outputs')

SAMPLES_PER_CLUSTER = 10
THUMB_WIDTH = 320
THUMB_QUALITY = 60
THUMB_DIR_NAME = 'thumbs'

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_SCRIPT_DIR)
_CODEBASE_DIR = os.path.dirname(os.path.dirname(_PIPELINE_DIR))

_IMAGES_CANDIDATES = [
    os.path.join(_CODEBASE_DIR, '03_images'),
    r'D:\D01_Code_Forge\01_Codebase\Codebase_ColorAnalytics\03_images',
    os.path.join(_PIPELINE_DIR, 'images'),
]
if os.environ.get("IMAGE_DIR"):
    _IMAGES_CANDIDATES.insert(0, os.environ["IMAGE_DIR"])


# =============================================================================
# IMAGE LOADING HELPERS
# =============================================================================

def _find_images_dir():
    """Return the first existing images directory candidate, or None."""
    for p in _IMAGES_CANDIDATES:
        if os.path.isdir(p):
            return p
    return None


def _map_archive_dirs(brand, images_dir):
    """Map archive IDs (as strings) to their folder paths."""
    mapping = {}
    brand_lower = brand.lower()
    for name in os.listdir(images_dir):
        path = os.path.join(images_dir, name)
        if not os.path.isdir(path):
            continue
        parts = name.split('_')
        if len(parts) >= 4 and parts[0].lower() == brand_lower:
            mapping[parts[2]] = path
    return mapping


def _load_product_images(assignments, summary, brand, output_dir=None):
    """Sample products per cluster and write thumbnails to disk.

    Returns dict: cluster_id -> list of {iid, L, a, b, src} dicts,
    or empty dict if images can't be loaded.
    """
    output_dir = output_dir or OUTPUT_DIR
    import cv2
    from shared.db import connect_to_db

    images_dir = _find_images_dir()
    if not images_dir:
        print("  WARNING: No images directory found, skipping product images")
        return {}

    archive_dirs = _map_archive_dirs(brand, images_dir)
    if not archive_dirs:
        print(f"  WARNING: No archive dirs for brand '{brand}', skipping images")
        return {}

    # Sample products per cluster using exhaustive farthest-point selection.
    # For each cluster, considers ALL products to find the most diverse set,
    # but only selects up to SAMPLES_PER_CLUSTER. Starts with the product
    # closest to the centroid, then greedily picks the product most distant
    # from all already-selected products (LAB Euclidean).
    samples = {}
    for _, centroid in summary.iterrows():
        cid = int(centroid['cluster_id'])
        cluster_prods = assignments[assignments['cluster_id'] == cid]
        if len(cluster_prods) == 0:
            continue
        n = min(SAMPLES_PER_CLUSTER, len(cluster_prods))

        lab = cluster_prods[['lab_l', 'lab_a', 'lab_b']].values
        indices = list(cluster_prods.index)

        # Seed: closest to centroid
        cent = np.array([centroid['lab_l'], centroid['lab_a'], centroid['lab_b']])
        dists_to_cent = np.sum((lab - cent) ** 2, axis=1)
        first = int(np.argmin(dists_to_cent))

        if n == 1:
            samples[cid] = cluster_prods.loc[[indices[first]]]
            continue

        # Greedy farthest-point: O(n_products * n_samples)
        selected = [first]
        min_dists = np.sum((lab - lab[first]) ** 2, axis=1).astype(np.float64)
        min_dists[first] = -1.0

        while len(selected) < n:
            next_idx = int(np.argmax(min_dists))
            if min_dists[next_idx] <= 0:
                break
            selected.append(next_idx)
            new_dists = np.sum((lab - lab[next_idx]) ** 2, axis=1)
            mask = min_dists >= 0
            min_dists = np.where(mask, np.minimum(min_dists, new_dists), min_dists)
            min_dists[next_idx] = -1.0

        selected_indices = [indices[i] for i in selected]
        samples[cid] = cluster_prods.loc[selected_indices]

    # Collect all instance IDs for DB lookup
    all_iids = []
    for df in samples.values():
        all_iids.extend(df['instance_id'].astype(int).tolist())

    if not all_iids:
        return {}

    print(f"  Fetching metadata for {len(all_iids)} sampled products...")
    conn, cur = connect_to_db(brand)
    cur.execute("""
        SELECT i.instance_id, i.archive_id_ref, i.family_id_ref, i.family_rank, a.pid
        FROM instance i
        JOIN appendix a ON i.instance_id = a.instance_id_ref
        WHERE i.instance_id IN %s
    """, (tuple(all_iids),))

    metadata = {}
    for row in cur.fetchall():
        metadata[row[0]] = {
            'archive_id': row[1], 'family_id': row[2],
            'rank': row[3], 'pid': row[4],
        }
    conn.close()

    # Create thumbnail directory
    thumb_dir = os.path.join(output_dir, THUMB_DIR_NAME)
    os.makedirs(thumb_dir, exist_ok=True)

    # Load images and write thumbnails
    products = {}
    found = 0
    missing = 0

    for cid, df in samples.items():
        prods = []
        for _, row in df.iterrows():
            iid = int(row['instance_id'])
            meta = metadata.get(iid)
            if not meta:
                missing += 1
                continue

            aid = str(meta['archive_id'])
            archive_folder = archive_dirs.get(aid)
            if not archive_folder:
                missing += 1
                continue

            filename = (f"{meta['archive_id']}-{iid}-{meta['family_id']}"
                        f"-{meta['rank']}-{meta['pid']}.jpg")
            filepath = os.path.join(archive_folder, filename)
            if not os.path.exists(filepath):
                missing += 1
                continue

            img = cv2.imread(filepath)
            if img is None:
                missing += 1
                continue

            # Resize to thumbnail
            h, w = img.shape[:2]
            scale = THUMB_WIDTH / w
            new_h = max(1, int(h * scale))
            thumb = cv2.resize(img, (THUMB_WIDTH, new_h), interpolation=cv2.INTER_AREA)

            thumb_name = f"{cid}_{iid}.jpg"
            thumb_path = os.path.join(thumb_dir, thumb_name)
            cv2.imwrite(thumb_path, thumb, [cv2.IMWRITE_JPEG_QUALITY, THUMB_QUALITY])

            prods.append({
                'iid': iid,
                'L': round(float(row['lab_l']), 1),
                'a': round(float(row['lab_a']), 1),
                'b': round(float(row['lab_b']), 1),
                'src': f"{THUMB_DIR_NAME}/{thumb_name}",
            })
            found += 1

        if prods:
            products[cid] = prods

    print(f"  Thumbnails: {found} written, {missing} missing")
    return products


# =============================================================================
# HTML GENERATION
# =============================================================================

def _generate_html(clusters_json, products_json, n_clusters, total_products):
    has_images = products_json != '{}'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cluster Zone Builder</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  background: #1a1a1a; color: #ddd;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  height: 100vh; overflow: hidden;
  display: flex; flex-direction: column;
}}

/* ---- Toolbar ---- */
.toolbar {{
  display: flex; align-items: center;
  padding: 8px 16px; gap: 12px;
  border-bottom: 1px solid #333; flex-shrink: 0;
}}
.toolbar h1 {{ font-size: 15px; font-weight: 600; white-space: nowrap; }}
.tbtn {{
  background: #333; color: #ddd; border: 1px solid #555;
  padding: 5px 14px; border-radius: 4px; cursor: pointer; font-size: 13px;
}}
.tbtn:hover {{ background: #444; }}
.tbtn.primary {{ background: #2563eb; border-color: #3b82f6; }}
.tbtn.primary:hover {{ background: #1d4ed8; }}
.status {{ margin-left: auto; font-size: 13px; color: #999; white-space: nowrap; }}

/* ---- Main split ---- */
.main {{ display: flex; flex: 1; overflow: hidden; min-height: 0; }}

/* ---- Left column (grid + preview stacked) ---- */
.left-col {{ flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }}

/* ---- Grid panel (top-left) ---- */
.grid-panel {{ flex: 1; overflow-y: auto; padding: 8px; min-height: 0; }}
.grid {{ display: flex; flex-wrap: wrap; gap: 2px; }}
.sw {{
  position: relative; cursor: pointer;
  width: 44px; height: 44px;
  display: flex; align-items: flex-end; justify-content: center;
  transition: opacity 0.1s;
}}
.sw .id {{
  font-size: 8px; opacity: 0.7; line-height: 1;
  padding: 1px 2px; pointer-events: none;
}}
.sw.assigned {{ opacity: 0.2; }}
.sw.active-member {{
  outline: 2px solid #fff; outline-offset: -2px;
  opacity: 1; z-index: 1;
}}
.sw:hover {{ opacity: 1 !important; z-index: 2; outline: 1px solid rgba(255,255,255,0.5); }}

/* ---- Groups panel (right) ---- */
.groups-panel {{
  width: 400px; overflow-y: auto;
  border-left: 1px solid #333; padding: 8px; flex-shrink: 0;
}}

/* ---- Minimap (far right) ---- */
.minimap {{
  width: 32px; flex-shrink: 0; overflow-y: auto;
  border-right: 1px solid #333;
  display: flex; flex-direction: column;
  padding: 2px; gap: 1px;
}}
.minimap .mm-sw {{
  width: 28px; height: 8px; border-radius: 1px;
  cursor: pointer; flex-shrink: 0;
  border: 1px solid transparent;
}}
.minimap .mm-sw:hover {{ border-color: rgba(255,255,255,0.5); }}
.minimap .mm-sw.active {{ border-color: #3b82f6; }}
.gcard {{
  background: #252525; border: 1px solid #333;
  border-radius: 6px; padding: 10px; margin-bottom: 8px;
}}
.gcard.active {{ border-color: #3b82f6; background: #1a2744; }}
.gcard-hdr {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
.gcolor {{ width: 24px; height: 24px; border-radius: 4px; flex-shrink: 0; }}
.gname {{
  background: transparent; border: none; color: #ddd;
  font-size: 14px; font-weight: 600; flex: 1; padding: 2px 4px; border-radius: 3px;
  min-width: 0;
}}
.gname:focus {{ background: #333; outline: 1px solid #555; }}
.gstats {{ font-size: 12px; color: #888; margin-bottom: 6px; }}
.gswatches {{ display: flex; flex-wrap: wrap; gap: 2px; }}
.gsw {{ width: 32px; height: 32px; cursor: pointer; border-radius: 2px; }}
.gsw:hover {{ outline: 1px solid #fff; z-index: 1; }}
.gactions {{ display: flex; gap: 4px; margin-top: 8px; }}
.gactions button {{
  background: #333; color: #999; border: 1px solid #444;
  padding: 2px 8px; border-radius: 3px; font-size: 11px; cursor: pointer;
}}
.gactions button:hover {{ background: #444; color: #ddd; }}
.gactions button.danger:hover {{ background: #7f1d1d; border-color: #991b1b; color: #fca5a5; }}
.new-group-btn {{
  display: block; width: 100%; padding: 10px;
  background: #252525; border: 1px dashed #555; border-radius: 6px;
  color: #888; font-size: 14px; cursor: pointer; text-align: center;
}}
.new-group-btn:hover {{ background: #333; color: #ddd; border-color: #777; }}
.empty-hint {{ text-align: center; color: #555; padding: 30px 16px; font-size: 13px; line-height: 1.6; }}

/* ---- Preview panel (bottom of left column) ---- */
#preview {{
  display: none; border-top: 1px solid #333;
  max-height: 50%; overflow-y: auto;
  padding: 8px 16px; flex-shrink: 0;
}}
.pv-hdr {{ font-size: 13px; color: #aaa; margin-bottom: 6px; }}
.pv-grid {{ display: flex; flex-wrap: wrap; gap: 4px; }}
.pv-item {{ position: relative; }}
.pv-item img {{ display: block; height: 320px; border-radius: 2px; }}
.pv-badge {{
  position: absolute; top: 2px; left: 2px;
  background: rgba(0,0,0,0.75); color: #fff;
  font-size: 9px; padding: 1px 4px; border-radius: 2px;
}}

/* ---- Tooltip ---- */
#tip {{
  display: none; position: fixed;
  background: rgba(15,15,15,0.95); border: 1px solid #666;
  padding: 8px 12px; border-radius: 4px;
  font-size: 13px; line-height: 1.5; color: #ddd;
  pointer-events: none; z-index: 200; max-width: 260px;
}}

/* ---- Drag and drop ---- */
.gcard {{ cursor: grab; transition: opacity 0.15s, box-shadow 0.15s; }}
.gcard:active {{ cursor: grabbing; }}
.gcard.dragging {{ opacity: 0.3; }}
.gcard.drop-above {{ box-shadow: inset 0 3px 0 0 #3b82f6; }}
.gcard.drop-below {{ box-shadow: inset 0 -3px 0 0 #3b82f6; }}
.gcard.drop-target {{ background: #1a3a2a !important; border-color: #22c55e !important; }}
.gsw[draggable] {{ transition: opacity 0.15s; }}
.gsw.dragging {{ opacity: 0.3; }}
</style>
</head>
<body>

<div class="toolbar">
  <h1>Cluster Zone Builder</h1>
  <button class="tbtn primary" onclick="createGroup()">+ New Group (N)</button>
  <button class="tbtn" onclick="exportJSON()">Export JSON (E)</button>
  <button class="tbtn" onclick="document.getElementById('fimport').click()">Import JSON</button>
  <button class="tbtn" onclick="sortGroupsByPerceptualDistance()">Sort by ΔE</button>
  <button class="tbtn" onclick="sortGroupsByHSBClass()">Sort by HSB</button>
  <button class="tbtn" onclick="clearAll()">Clear All</button>
  <div class="status" id="status"></div>
</div>

<div class="main">
  <div class="left-col">
    <div class="grid-panel" id="grid-panel">
      <div class="grid" id="grid"></div>
    </div>
    <div id="preview"></div>
  </div>
  <div class="minimap" id="minimap"></div>
  <div class="groups-panel" id="groups-panel"></div>
</div>
<div id="tip"></div>
<input type="file" id="fimport" style="display:none" accept=".json" onchange="handleImport(this)">

<script>
// ============================
// DATA (embedded by Python)
// ============================
const CLUSTERS = {clusters_json};
const PRODUCTS = {products_json};
const TOTAL = {total_products};
const HAS_IMAGES = {'true' if has_images else 'false'};
const CMAP = {{}};
CLUSTERS.forEach((c, i) => {{ c._i = i; CMAP[c.id] = c; }});

// ============================
// STATE
// ============================
const SKEY = 'ciede2000_zone_builder';
let S = {{ groups: [], activeId: null, nextId: 1 }};

function save() {{
  localStorage.setItem(SKEY, JSON.stringify(S));
  try {{ new BroadcastChannel('zone_builder').postMessage(S); }} catch(_) {{}}
}}
function load() {{
  try {{
    const d = JSON.parse(localStorage.getItem(SKEY));
    if (d && d.groups) {{ S = d; S.nextId = S.nextId || S.groups.length + 1; }}
  }} catch(_) {{}}
}}

// ============================
// GROUP HELPERS
// ============================
function findGroup(cid) {{
  return S.groups.find(g => g.cids.includes(cid)) || null;
}}
function activeGroup() {{
  return S.groups.find(g => g.id === S.activeId) || null;
}}

// ============================
// LAB -> HEX (for group color)
// ============================
function labToHex(L, a, b) {{
  let fy = (L + 16) / 116, fx = a / 500 + fy, fz = fy - b / 200;
  const d = 6/29;
  let x = fx > d ? fx*fx*fx : (fx - 16/116) / 7.787;
  let y = fy > d ? fy*fy*fy : (fy - 16/116) / 7.787;
  let z = fz > d ? fz*fz*fz : (fz - 16/116) / 7.787;
  x *= 0.95047; z *= 1.08883;
  let r = x*3.2406 + y*-1.5372 + z*-0.4986;
  let g = x*-0.9689 + y*1.8758 + z*0.0415;
  let bl = x*0.0557 + y*-0.2040 + z*1.0570;
  const gam = v => v > 0.0031308 ? 1.055*Math.pow(v,1/2.4)-0.055 : 12.92*v;
  r = gam(r); g = gam(g); bl = gam(bl);
  const h = v => Math.max(0,Math.min(255,Math.round(v*255))).toString(16).padStart(2,'0');
  return '#' + h(r) + h(g) + h(bl);
}}

// ============================
// CIEDE2000 DISTANCE
// ============================
function ciede2000(L1,a1,b1, L2,a2,b2) {{
  const rad = Math.PI/180, deg = 180/Math.PI;
  const C1 = Math.sqrt(a1*a1 + b1*b1), C2 = Math.sqrt(a2*a2 + b2*b2);
  const Cm = (C1 + C2) / 2;
  const Cm7 = Math.pow(Cm, 7);
  const G = 0.5 * (1 - Math.sqrt(Cm7 / (Cm7 + Math.pow(25, 7))));
  const a1p = a1 * (1 + G), a2p = a2 * (1 + G);
  const C1p = Math.sqrt(a1p*a1p + b1*b1), C2p = Math.sqrt(a2p*a2p + b2*b2);
  let h1p = Math.atan2(b1, a1p) * deg; if (h1p < 0) h1p += 360;
  let h2p = Math.atan2(b2, a2p) * deg; if (h2p < 0) h2p += 360;
  const dLp = L2 - L1, dCp = C2p - C1p;
  let dhp;
  if (C1p * C2p === 0) dhp = 0;
  else if (Math.abs(h2p - h1p) <= 180) dhp = h2p - h1p;
  else if (h2p - h1p > 180) dhp = h2p - h1p - 360;
  else dhp = h2p - h1p + 360;
  const dHp = 2 * Math.sqrt(C1p * C2p) * Math.sin(dhp / 2 * rad);
  const Lm = (L1 + L2) / 2, Cpm = (C1p + C2p) / 2;
  let Hpm;
  if (C1p * C2p === 0) Hpm = h1p + h2p;
  else if (Math.abs(h1p - h2p) <= 180) Hpm = (h1p + h2p) / 2;
  else if (h1p + h2p < 360) Hpm = (h1p + h2p + 360) / 2;
  else Hpm = (h1p + h2p - 360) / 2;
  const T = 1
    - 0.17 * Math.cos((Hpm - 30) * rad)
    + 0.24 * Math.cos(2 * Hpm * rad)
    + 0.32 * Math.cos((3 * Hpm + 6) * rad)
    - 0.20 * Math.cos((4 * Hpm - 63) * rad);
  const Lm50sq = (Lm - 50) * (Lm - 50);
  const SL = 1 + 0.015 * Lm50sq / Math.sqrt(20 + Lm50sq);
  const SC = 1 + 0.045 * Cpm;
  const SH = 1 + 0.015 * Cpm * T;
  const Cpm7 = Math.pow(Cpm, 7);
  const RC = 2 * Math.sqrt(Cpm7 / (Cpm7 + Math.pow(25, 7)));
  const dtheta = 30 * Math.exp(-Math.pow((Hpm - 275) / 25, 2));
  const RT = -Math.sin(2 * dtheta * rad) * RC;
  const rL = dLp / SL, rC = dCp / SC, rH = dHp / SH;
  return Math.sqrt(rL*rL + rC*rC + rH*rH + RT*rC*rH);
}}

// ============================
// SORT ZONES BY PERCEPTUAL DISTANCE
// ============================
function sortGroupsByPerceptualDistance() {{
  const groups = S.groups;
  if (groups.length < 2) return;

  // Compute weighted LAB centroid per group
  const centroids = groups.map(g => {{
    let tN = 0, sL = 0, sA = 0, sB = 0;
    g.cids.forEach(id => {{
      const c = CMAP[id]; if (!c) return;
      sL += c.L * c.n; sA += c.a * c.n; sB += c.b * c.n; tN += c.n;
    }});
    return tN > 0 ? {{ L: sL/tN, a: sA/tN, b: sB/tN }} : {{ L: 50, a: 0, b: 0 }};
  }});

  // Nearest-neighbor chain: start with darkest zone (lowest L)
  const n = centroids.length;
  const used = new Uint8Array(n);
  let first = 0, minL = Infinity;
  for (let i = 0; i < n; i++) {{
    if (centroids[i].L < minL) {{ minL = centroids[i].L; first = i; }}
  }}

  const order = [first];
  used[first] = 1;
  while (order.length < n) {{
    const last = centroids[order[order.length - 1]];
    let bestIdx = -1, bestDist = Infinity;
    for (let i = 0; i < n; i++) {{
      if (used[i]) continue;
      const d = ciede2000(last.L, last.a, last.b, centroids[i].L, centroids[i].a, centroids[i].b);
      if (d < bestDist) {{ bestDist = d; bestIdx = i; }}
    }}
    if (bestIdx < 0) break;
    used[bestIdx] = 1;
    order.push(bestIdx);
  }}

  // Reorder groups array
  const reordered = order.map(i => groups[i]);
  groups.length = 0;
  reordered.forEach(g => groups.push(g));

  save();
  render();
}}

// ============================
// HSB SATURATION FROM LAB
// ============================
function labToRgbLinear(L, a, b) {{
  let fy = (L + 16) / 116, fx = a / 500 + fy, fz = fy - b / 200;
  const d = 6/29;
  let x = fx > d ? fx*fx*fx : (fx - 16/116) / 7.787;
  let y = fy > d ? fy*fy*fy : (fy - 16/116) / 7.787;
  let z = fz > d ? fz*fz*fz : (fz - 16/116) / 7.787;
  x *= 0.95047; z *= 1.08883;
  let r = x*3.2406 + y*-1.5372 + z*-0.4986;
  let g = x*-0.9689 + y*1.8758 + z*0.0415;
  let bl = x*0.0557 + y*-0.2040 + z*1.0570;
  const gam = v => v > 0.0031308 ? 1.055*Math.pow(v,1/2.4)-0.055 : 12.92*v;
  return [Math.max(0,Math.min(1,gam(r))), Math.max(0,Math.min(1,gam(g))), Math.max(0,Math.min(1,gam(bl)))];
}}

function computeHsbSat(L, a, b) {{
  const [r, g, bl] = labToRgbLinear(L, a, b);
  const mx = Math.max(r, g, bl), mn = Math.min(r, g, bl);
  return mx > 0 ? (mx - mn) / mx * 100 : 0;
}}

const NEUTRAL_HSB = 12.0;
const SATURATED_HSB = 50.0;

// ============================
// SORT ZONES BY HSB CLASS (TREEMAP ORDER)
// ============================
function sortGroupsByHSBClass() {{
  const groups = S.groups;
  if (groups.length < 2) return;

  const indexed = groups.map((g, i) => {{
    let tN = 0, sL = 0, sA = 0, sB = 0;
    g.cids.forEach(id => {{
      const c = CMAP[id]; if (!c) return;
      sL += c.L * c.n; sA += c.a * c.n; sB += c.b * c.n; tN += c.n;
    }});
    const L = tN > 0 ? sL/tN : 50;
    const a = tN > 0 ? sA/tN : 0;
    const b = tN > 0 ? sB/tN : 0;
    const sat = computeHsbSat(L, a, b);
    const hue = (Math.atan2(b, a) * 180 / Math.PI + 180) % 360;
    return {{ i, L, a, b, sat, hue }};
  }});

  const neutral = indexed.filter(f => f.sat < NEUTRAL_HSB).sort((a, b) => a.L - b.L);
  const chromatic = indexed.filter(f => f.sat >= NEUTRAL_HSB).sort((a, b) => a.hue - b.hue);

  const order = [...neutral, ...chromatic].map(f => f.i);
  const reordered = order.map(i => groups[i]);
  groups.length = 0;
  reordered.forEach(g => groups.push(g));

  save();
  render();
}}

function groupColor(grp) {{
  if (!grp.cids.length) return '#333';
  let tN=0, sL=0, sA=0, sB=0;
  grp.cids.forEach(id => {{
    const c = CMAP[id]; if (!c) return;
    sL += c.L*c.n; sA += c.a*c.n; sB += c.b*c.n; tN += c.n;
  }});
  return tN > 0 ? labToHex(sL/tN, sA/tN, sB/tN) : '#333';
}}

function groupProducts(grp) {{
  return grp.cids.reduce((s, id) => s + (CMAP[id]?.n || 0), 0);
}}

// ============================
// FARTHEST-POINT ORDERING
// ============================
function farthestPointOrder(items) {{
  const n = items.length;
  if (n <= 1) return items;

  // LAB Euclidean distance
  const labDist = (a, b) => {{
    const dL = a.L - b.L, da = a.a - b.a, db = a.b - b.b;
    return Math.sqrt(dL*dL + da*da + db*db);
  }};

  // Start with item closest to the mean
  const mL = items.reduce((s,p) => s+p.L, 0) / n;
  const ma = items.reduce((s,p) => s+p.a, 0) / n;
  const mb = items.reduce((s,p) => s+p.b, 0) / n;
  const mean = {{ L: mL, a: ma, b: mb }};

  let first = 0, best = Infinity;
  for (let i = 0; i < n; i++) {{
    const d = labDist(items[i], mean);
    if (d < best) {{ best = d; first = i; }}
  }}

  const sel = [first];
  const minD = new Float64Array(n);
  for (let i = 0; i < n; i++) minD[i] = labDist(items[first], items[i]);
  minD[first] = -1;

  while (sel.length < n) {{
    let bi = -1, bd = -1;
    for (let i = 0; i < n; i++) {{
      if (minD[i] >= 0 && minD[i] > bd) {{ bd = minD[i]; bi = i; }}
    }}
    if (bi < 0) break;
    sel.push(bi);
    minD[bi] = -1;
    for (let i = 0; i < n; i++) {{
      if (minD[i] >= 0) minD[i] = Math.min(minD[i], labDist(items[bi], items[i]));
    }}
  }}

  return sel.map(i => items[i]);
}}

// ============================
// RENDERING
// ============================
function renderGrid() {{
  const el = document.getElementById('grid');
  let html = '';
  CLUSTERS.forEach(c => {{
    const grp = findGroup(c.id);
    let cls = 'sw';
    if (grp) cls += grp.id === S.activeId ? ' active-member' : ' assigned';
    const tc = c.L < 55 ? '#fff' : '#000';
    html += '<div class="' + cls + '" style="background:' + c.hex + ';color:' + tc + '" '
          + 'data-id="' + c.id + '" '
          + 'onmouseenter="tipOn(this,' + c.id + ')" '
          + 'onmousemove="tipMv(event)" '
          + 'onmouseleave="tipOff()" '
          + 'onclick="clickSw(' + c.id + ')">'
          + '<span class="id">C' + c.id + '</span></div>';
  }});
  el.innerHTML = html;
}}

function renderGroups() {{
  const el = document.getElementById('groups-panel');
  let html = '';

  if (S.groups.length === 0) {{
    html += '<div class="empty-hint">No groups yet.<br>Click <b>+ New Group</b> to start,<br>then click swatches to assign them.</div>';
  }}
  S.groups.forEach((g, gi) => {{
    const isActive = g.id === S.activeId;
    const gc = groupColor(g);
    const np = groupProducts(g);
    const pct = TOTAL > 0 ? (np / TOTAL * 100).toFixed(1) : '0.0';
    html += '<div class="gcard' + (isActive ? ' active' : '') + '" draggable="true" '
          + 'data-gid="' + g.id + '" '
          + 'onclick="selectGroup(' + g.id + ')" '
          + 'ondragstart="dragGroupStart(event,' + g.id + ')" '
          + 'ondragover="dragOver(event)" '
          + 'ondragleave="dragLeave(event)" '
          + 'ondrop="dragDrop(event,' + g.id + ')" '
          + 'ondragend="dragEnd()">';
    html += '<div class="gcard-hdr">';
    html += '<div class="gcolor" style="background:' + gc + '"></div>';
    html += '<input class="gname" value="' + escAttr(g.name) + '" '
          + 'onclick="event.stopPropagation()" '
          + 'onchange="renameGroup(' + g.id + ',this.value)" '
          + 'onkeydown="if(event.keyCode===13)this.blur()">';
    html += '</div>';
    html += '<div class="gstats">' + g.cids.length + ' cluster' + (g.cids.length !== 1 ? 's' : '')
          + ' &middot; ' + np.toLocaleString() + ' products (' + pct + '%)</div>';
    if (g.cids.length > 0) {{
      html += '<div class="gswatches">';
      g.cids.forEach(cid => {{
        const c = CMAP[cid];
        if (!c) return;
        html += '<div class="gsw" style="background:' + c.hex + '" title="C' + c.id + ' (' + c.n.toLocaleString() + ')" '
              + 'draggable="true" '
              + 'onclick="event.stopPropagation();removeSw(' + cid + ',' + g.id + ')" '
              + 'ondragstart="event.stopPropagation();dragSwStart(event,' + cid + ',' + g.id + ')" '
              + 'ondragend="dragEnd()"></div>';
      }});
      html += '</div>';
    }}
    html += '<div class="gactions">';
    if (isActive) html += '<button onclick="event.stopPropagation()" disabled style="opacity:0.5">Selected</button>';
    else html += '<button onclick="event.stopPropagation();selectGroup(' + g.id + ')">Select</button>';
    html += '<button class="danger" onclick="event.stopPropagation();deleteGroup(' + g.id + ')">Delete</button>';
    html += '</div></div>';
  }});
  html += '<button class="new-group-btn" onclick="createGroup()">+ New Group</button>';
  el.innerHTML = html;
}}

function renderPreview() {{
  const panel = document.getElementById('preview');

  if (!HAS_IMAGES) {{ panel.style.display = 'none'; return; }}
  const ag = activeGroup();
  if (!ag || ag.cids.length === 0) {{ panel.style.display = 'none'; return; }}
  let all = [];
  ag.cids.forEach(cid => {{
    const prods = PRODUCTS[cid];
    if (!prods) return;
    prods.forEach(p => all.push({{ ...p, cid: cid }}));
  }});
  if (all.length === 0) {{ panel.style.display = 'none'; return; }}
  const ordered = farthestPointOrder(all);
  let html = '<div class="pv-hdr">Products in <b>' + escHtml(ag.name) + '</b> &mdash; '
           + ordered.length + ' sampled, farthest-point ordered</div>';
  html += '<div class="pv-grid">';
  ordered.forEach(p => {{
    html += '<div class="pv-item">'
          + '<img src="' + p.src + '" loading="lazy">'
          + '<span class="pv-badge">C' + p.cid + '</span>'
          + '</div>';
  }});
  html += '</div>';
  panel.innerHTML = html;
  panel.style.display = 'block';
}}

function updateStatus() {{
  const assigned = S.groups.reduce((s, g) => s + g.cids.length, 0);
  const unassigned = CLUSTERS.length - assigned;
  const ag = activeGroup();
  let txt = assigned + ' assigned, ' + unassigned + ' unassigned';
  if (ag) txt += ' &nbsp;|&nbsp; Active: ' + escHtml(ag.name) + ' (' + ag.cids.length + ')';
  document.getElementById('status').innerHTML = txt;
}}

function renderMinimap() {{
  const el = document.getElementById('minimap');
  if (!el) return;
  let html = '';
  S.groups.forEach((g, i) => {{
    let tN = 0, sL = 0, sA = 0, sB = 0;
    g.cids.forEach(id => {{
      const c = CMAP[id]; if (!c) return;
      sL += c.L * c.n; sA += c.a * c.n; sB += c.b * c.n; tN += c.n;
    }});
    const hex = tN > 0 ? labToHex(sL/tN, sA/tN, sB/tN) : '#333';
    const active = S.activeId === g.id ? ' active' : '';
    html += '<div class="mm-sw' + active + '" style="background:' + hex + '" '
          + 'title="' + g.name + '" data-gid="' + g.id + '" '
          + 'onclick="minimapClick(' + g.id + ')"></div>';
  }});
  el.innerHTML = html;
}}

function minimapClick(gid) {{
  S.activeId = S.activeId === gid ? null : gid;
  save();
  render();
  // Scroll the group into view in the groups panel
  const card = document.querySelector('.gcard[data-gid="' + gid + '"]');
  if (card) card.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
}}

function render() {{
  renderGrid(); renderGroups(); renderPreview(); renderMinimap(); updateStatus();
}}

// ============================
// ACTIONS
// ============================
function createGroup() {{
  const g = {{ id: S.nextId, name: 'Group ' + S.nextId, cids: [] }};
  S.nextId++;
  const activeIdx = S.groups.findIndex(gr => gr.id === S.activeId);
  if (activeIdx >= 0) {{
    S.groups.splice(activeIdx + 1, 0, g);
  }} else {{
    S.groups.push(g);
  }}
  S.activeId = g.id;
  save(); render();
}}

function selectGroup(id) {{
  S.activeId = S.activeId === id ? null : id;
  save(); render();
}}

function deleteGroup(id) {{
  S.groups = S.groups.filter(g => g.id !== id);
  if (S.activeId === id) S.activeId = S.groups.length > 0 ? S.groups[S.groups.length - 1].id : null;
  save(); render();
}}

function renameGroup(id, name) {{
  const g = S.groups.find(g => g.id === id);
  if (g) g.name = name.trim() || g.name;
  save(); renderGroups();
}}

function clickSw(cid) {{
  const existing = findGroup(cid);
  if (existing && existing.id === S.activeId) {{
    existing.cids = existing.cids.filter(id => id !== cid);
  }} else if (S.activeId) {{
    if (existing) existing.cids = existing.cids.filter(id => id !== cid);
    const ag = activeGroup();
    if (ag) ag.cids.push(cid);
  }}
  save(); render();
}}

function removeSw(cid, groupId) {{
  const g = S.groups.find(g => g.id === groupId);
  if (g) g.cids = g.cids.filter(id => id !== cid);
  save(); render();
}}

// ============================
// DRAG AND DROP
// ============================
let dragState = null;

function dragGroupStart(e, gid) {{
  dragState = {{ type: 'group', groupId: gid }};
  e.currentTarget.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', '');
}}

function dragSwStart(e, itemId, gid) {{
  e.stopPropagation();
  dragState = {{ type: 'swatch', itemId: itemId, sourceGroupId: gid }};
  e.currentTarget.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', '');
}}

function dragOver(e) {{
  if (!dragState) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const card = e.currentTarget;
  document.querySelectorAll('.gcard').forEach(c => {{
    c.classList.remove('drop-above', 'drop-below', 'drop-target');
  }});
  if (dragState.type === 'group') {{
    const rect = card.getBoundingClientRect();
    const mid = rect.top + rect.height / 2;
    card.classList.add(e.clientY < mid ? 'drop-above' : 'drop-below');
  }} else if (dragState.type === 'swatch') {{
    card.classList.add('drop-target');
  }}
}}

function dragLeave(e) {{
  e.currentTarget.classList.remove('drop-above', 'drop-below', 'drop-target');
}}

function dragDrop(e, targetGid) {{
  e.preventDefault();
  e.stopPropagation();
  document.querySelectorAll('.gcard').forEach(c => {{
    c.classList.remove('drop-above', 'drop-below', 'drop-target', 'dragging');
  }});
  if (!dragState) return;
  if (dragState.type === 'group') {{
    const arr = S.groups;
    const fromIdx = arr.findIndex(g => g.id === dragState.groupId);
    let toIdx = arr.findIndex(g => g.id === targetGid);
    if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) {{ dragState = null; return; }}
    const rect = e.currentTarget.getBoundingClientRect();
    const insertBefore = e.clientY < rect.top + rect.height / 2;
    const [item] = arr.splice(fromIdx, 1);
    toIdx = arr.findIndex(g => g.id === targetGid);
    arr.splice(insertBefore ? toIdx : toIdx + 1, 0, item);
  }} else if (dragState.type === 'swatch') {{
    const src = S.groups.find(g => g.id === dragState.sourceGroupId);
    const tgt = S.groups.find(g => g.id === targetGid);
    if (!src || !tgt || src.id === tgt.id) {{ dragState = null; return; }}
    src.cids = src.cids.filter(id => id !== dragState.itemId);
    tgt.cids.push(dragState.itemId);
  }}
  dragState = null;
  save();
  render();
}}

function dragEnd() {{
  dragState = null;
  document.querySelectorAll('.gcard').forEach(c => {{
    c.classList.remove('drop-above', 'drop-below', 'drop-target', 'dragging');
  }});
  document.querySelectorAll('.gsw').forEach(c => {{
    c.classList.remove('dragging');
  }});
}}

function clearAll() {{
  if (S.groups.length === 0) return;
  if (!confirm('Remove all groups and assignments?')) return;
  S.groups = []; S.activeId = null;
  save(); render();
}}

// ============================
// EXPORT / IMPORT
// ============================
function exportJSON() {{
  const assigned = new Set();
  S.groups.forEach(g => g.cids.forEach(id => assigned.add(id)));
  const data = {{
    zones: S.groups.map(g => ({{
      name: g.name,
      cluster_ids: g.cids,
      n_clusters: g.cids.length,
      n_products: groupProducts(g),
    }})),
    unassigned: CLUSTERS.filter(c => !assigned.has(c.id)).map(c => c.id),
    total_clusters: CLUSTERS.length,
    total_products: TOTAL,
  }};
  const blob = new Blob([JSON.stringify(data, null, 2)], {{ type: 'application/json' }});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'cluster_zones.json';
  a.click();
  URL.revokeObjectURL(a.href);
}}

function handleImport(input) {{
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(e) {{
    try {{
      const data = JSON.parse(e.target.result);
      if (!data.zones || !Array.isArray(data.zones)) throw new Error('Missing zones array');
      S.groups = data.zones.map((f, i) => ({{
        id: i + 1,
        name: f.name || ('Group ' + (i + 1)),
        cids: (f.cluster_ids || []).filter(id => CMAP[id]),
      }}));
      S.nextId = S.groups.length + 1;
      S.activeId = S.groups.length > 0 ? S.groups[0].id : null;
      save();
      render();
    }} catch(err) {{
      alert('Import failed: ' + err.message);
    }}
  }};
  reader.readAsText(file);
  input.value = '';
}}

// ============================
// TOOLTIP
// ============================
const tip = document.getElementById('tip');
function tipOn(el, cid) {{
  const c = CMAP[cid]; if (!c) return;
  const pct = (c.n / TOTAL * 100).toFixed(1);
  const grp = findGroup(cid);
  let h = '<b>C' + c.id + '</b> &nbsp;' + c.hex
        + '<br>Products: ' + c.n.toLocaleString() + ' (' + pct + '%)'
        + '<br>L*=' + c.L + ' &nbsp;a*=' + c.a + ' &nbsp;b*=' + c.b;
  if (grp) h += '<br><span style="color:#999">In: ' + escHtml(grp.name) + '</span>';
  tip.innerHTML = h;
  tip.style.display = 'block';
}}
function tipMv(e) {{
  let x = e.clientX + 14, y = e.clientY + 14;
  const r = tip.getBoundingClientRect();
  if (x + r.width > window.innerWidth) x = e.clientX - r.width - 8;
  if (y + r.height > window.innerHeight) y = e.clientY - r.height - 8;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}}
function tipOff() {{ tip.style.display = 'none'; }}

// ============================
// KEYBOARD SHORTCUTS
// ============================
document.addEventListener('keydown', e => {{
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'n' || e.key === 'N') {{ createGroup(); e.preventDefault(); }}
  if (e.key === 'e' || e.key === 'E') {{ exportJSON(); e.preventDefault(); }}
  if (e.key >= '1' && e.key <= '9') {{
    const idx = parseInt(e.key) - 1;
    if (idx < S.groups.length) {{ selectGroup(S.groups[idx].id); e.preventDefault(); }}
  }}
}});

// ============================
// UTILITIES
// ============================
function escHtml(s) {{ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}
function escAttr(s) {{ return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }}

// ============================
// INIT
// ============================
load();
render();
</script>
</body>
</html>'''




def main(brand=None, gender=None):
    """Generate interactive cluster zone builder HTML tool."""

    # ------------------------------------------------------------------
    # 1. Load CSVs
    # ------------------------------------------------------------------
    if brand and gender:
        output_dir = os.path.join(OUTPUT_DIR, f'{brand}_{gender}')
    else:
        output_dir = OUTPUT_DIR

    summary_path = os.path.join(output_dir, 'cluster_summary.csv')
    assignments_path = os.path.join(output_dir, 'product_assignments.csv')

    if not os.path.exists(summary_path):
        print(f"ERROR: cluster_summary.csv not found in {output_dir}")
        print("  Run 'python cli.py consolidate-colors' first.")
        return

    summary = pd.read_csv(summary_path)
    n_clusters = len(summary)
    total_products = int(summary['n_products'].sum())
    print(f"Loaded {n_clusters} clusters ({total_products:,} products)")

    # ------------------------------------------------------------------
    # 2. CIEDE2000 optimal leaf ordering
    # ------------------------------------------------------------------
    lab = summary[['lab_l', 'lab_a', 'lab_b']].values.astype(np.float64)
    print(f"Computing CIEDE2000 ordering ({n_clusters * (n_clusters - 1) // 2:,} pairs)...")
    condensed = ciede2000_pairwise(lab, use_gpu=False, verbose=False)
    Z = linkage(condensed, method='average', optimal_ordering=True)
    order = leaves_list(Z)
    summary = summary.iloc[order].reset_index(drop=True)
    print(f"  Ordered {n_clusters} clusters by perceptual similarity")

    # ------------------------------------------------------------------
    # 3. Build cluster swatch data
    # ------------------------------------------------------------------
    clusters = []
    for _, row in summary.iterrows():
        clusters.append({
            'id': int(row['cluster_id']),
            'hex': row['hex_color'],
            'n': int(row['n_products']),
            'L': round(row['lab_l'], 1),
            'a': round(row['lab_a'], 1),
            'b': round(row['lab_b'], 1),
        })

    clusters_json = json.dumps(clusters, separators=(',', ':'))

    # ------------------------------------------------------------------
    # 4. Load product images (optional, requires brand)
    # ------------------------------------------------------------------
    products = {}
    if brand and os.path.exists(assignments_path):
        print(f"Loading product images for {brand}...")
        try:
            assignments = pd.read_csv(assignments_path)
            products = _load_product_images(assignments, summary, brand, output_dir=output_dir)
        except Exception as e:
            print(f"  WARNING: Image loading failed ({e}), continuing without images")
    elif not brand:
        print("  No brand specified — skipping product images (pass brand for images)")

    products_json = json.dumps(products, separators=(',', ':'))

    # ------------------------------------------------------------------
    # 5. Generate and save HTML
    # ------------------------------------------------------------------
    html = _generate_html(clusters_json, products_json, n_clusters, total_products)
    out_path = os.path.join(output_dir, 'cluster_zone_builder.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Saved: {out_path}")


if __name__ == '__main__':
    main()
