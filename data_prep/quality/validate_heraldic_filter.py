"""
ENTRY - run: python cli.py validate-heraldic-filter  (measures heraldic-filter precision/recall).

Heraldic Filter Validation Tool
================================

Companion to discover_heraldic_keywords.py. Where the audit tool surfaces candidate
keywords for filter expansion, THIS tool measures the filter's quality
quantitatively. It produces the concrete precision/recall numbers needed for
the writeup ("X% recall on a 500-product labeled sample, 95% CI [a, b]").

Three stages:

  1. sample   — Pull a stratified random sample of N products from the raw
                `instance` table. Stratify by (brand × filter_catches) so we get
                balanced classes for precision AND recall estimation. Encode
                product thumbnails as base64 and bundle into a JSON file.

  2. html     — Generate a self-contained HTML labeler from sample.json. One
                product at a time, three buttons (heraldic / not heraldic /
                skip), keyboard shortcuts, localStorage persistence, export to
                JSON on demand. Filter prediction is HIDDEN by default to avoid
                anchoring the labeler.

  3. analyze  — Read the exported labels JSON, match against the sample, build
                a confusion matrix vs. the current filter, and report:
                  - Precision (when filter flags, how often is it right)
                  - Recall   (of true heraldic products, how often filter
                              catches them — the key writeup metric)
                  - F1, accuracy, Wilson 95% CIs
                  - Per-brand and per-stratum breakdowns

Usage:
    python validate_heraldic_filter.py sample --per-brand 100
    python validate_heraldic_filter.py html
    # open labeler.html in browser, label products, click Export
    # move downloaded labels.json into data_prep/quality/outputs/heraldic_validation/
    python validate_heraldic_filter.py analyze

Outputs (data_prep/quality/outputs/heraldic_validation/, next to this module):
    sample.json
    labeler.html
    labels.json   (user-exported)
    analysis.txt
"""

import os
import sys
import re
import csv
import json
import math
import random
import argparse
import base64
from io import BytesIO
from collections import defaultdict
from datetime import datetime

import psycopg2
import psycopg2.extras
from PIL import Image

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from shared import db
from data_prep.heraldic_filter import HERALDIC_KEYWORDS

# ============================================================================
# CONFIGURATION
# ============================================================================

BRANDS = ['nike', 'adidas', 'puma', 'lulu', 'ua']

_HERE = os.path.dirname(os.path.abspath(__file__))
_CODEBASE_DIR = os.path.dirname(os.path.dirname(_HERE))
_IMAGE_CANDIDATES = [
    os.path.join(_CODEBASE_DIR, '03_images'),
    r'D:\D01_Code_Forge\01_Codebase\Codebase_ColorAnalytics\03_images',
    os.path.join(_HERE, 'images'),  # docker volume mount
]
if os.environ.get("IMAGE_DIR"):
    _IMAGE_CANDIDATES.insert(0, os.environ["IMAGE_DIR"])
DEFAULT_IMAGES_DIR = next((p for p in _IMAGE_CANDIDATES if os.path.exists(p)), _IMAGE_CANDIDATES[0])

OUTPUT_DIR = os.path.join(_HERE, 'outputs', 'heraldic_validation')
SAMPLE_JSON = os.path.join(OUTPUT_DIR, 'sample.json')
LABELER_HTML = os.path.join(OUTPUT_DIR, 'labeler.html')
LABELS_JSON = os.path.join(OUTPUT_DIR, 'labels.json')
ANALYSIS_REPORT = os.path.join(OUTPUT_DIR, 'analysis.txt')
MISCLASSIFICATIONS_CSV = os.path.join(OUTPUT_DIR, 'misclassifications.csv')

THUMBNAIL_MAX_PX = 320
JPEG_QUALITY = 75
RANDOM_SEED = 42

# ============================================================================
# DATABASE QUERIES (READ-ONLY)
# ============================================================================

INSTANCE_QUERY = """
    SELECT
        i.instance_id,
        i.archive_id_ref,
        i.family_id_ref,
        i.family_rank,
        i.title,
        i.title_second,
        a.query AS archive_query
    FROM instance i
    JOIN archive a ON i.archive_id_ref = a.archive_id
"""

PID_QUERY = """
    SELECT instance_id_ref, pid
    FROM appendix
    WHERE instance_id_ref IN %s
"""

# ============================================================================
# HERALDIC REGEX (matches segment.py's behavior)
# ============================================================================

def compile_heraldic_filter():
    """Same compile as segment.py — Python regex with \\b boundaries."""
    pattern = r"\b(" + "|".join(re.escape(k) for k in HERALDIC_KEYWORDS) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


def title_matches_filter(regex, title, title_second):
    """Return True if either title field matches the heraldic regex."""
    for field in (title, title_second):
        if isinstance(field, str) and regex.search(field):
            return True
    return False


# ============================================================================
# IMAGE HELPERS
# ============================================================================

def find_image_path(archive_dirs, archive_id, instance_id, family_id, rank, pid):
    """Build path to original JPG using the documented naming pattern."""
    key = str(archive_id)
    if key not in archive_dirs:
        return None
    folder = archive_dirs[key]
    filename = f"{archive_id}-{instance_id}-{family_id}-{rank}-{pid}.jpg"
    full = os.path.join(folder, filename)
    return full if os.path.exists(full) else None


def encode_thumbnail(image_path, max_px=THUMBNAIL_MAX_PX, quality=JPEG_QUALITY):
    """Open image, resize to max_px, JPEG-encode to base64. None on failure."""
    if not image_path or not os.path.exists(image_path):
        return None
    try:
        img = Image.open(image_path).convert('RGB')
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=quality)
        return base64.b64encode(buf.getvalue()).decode('ascii')
    except Exception as e:
        print(f"    [image error] {image_path}: {e}")
        return None


# ============================================================================
# STAGE 1 — SAMPLE
# ============================================================================

def run_sample(per_brand, images_dir):
    """
    Stratified random sample. For each brand, draw N/2 products that the filter
    catches and N/2 that it doesn't, giving balanced classes for measuring both
    precision and recall.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    rng = random.Random(RANDOM_SEED)
    regex = compile_heraldic_filter()

    print("=" * 70)
    print(f"STAGE 1: SAMPLE  ({per_brand}/brand, half filter-catches half not)")
    print("=" * 70)
    print(f"Images source: {images_dir}")
    print(f"Filter entries: {len(HERALDIC_KEYWORDS)}")

    half = per_brand // 2
    all_samples = []

    for brand in BRANDS:
        print(f"\n[{brand}] querying instance...")
        try:
            conn, cur = db.connect_to_db(brand)
            cur.execute(INSTANCE_QUERY)
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        print(f"  {len(rows):,} products in instance table")

        # Bucket by whether filter catches
        catches, misses = [], []
        for row in rows:
            instance_id, archive_id, family_id, rank, title, title_second, archive_query = row
            hits = title_matches_filter(regex, title, title_second)
            record = {
                'instance_id': instance_id,
                'brand': brand,
                'archive_id': archive_id,
                'family_id': family_id,
                'rank': rank,
                'title': title or '',
                'title_second': title_second or '',
                'archive_query': archive_query or '',
                'filter_catches': hits,
            }
            (catches if hits else misses).append(record)

        print(f"  filter catches: {len(catches):,}  | filter misses: {len(misses):,}")

        n_catches = min(half, len(catches))
        n_misses = min(per_brand - n_catches, len(misses))
        sampled = rng.sample(catches, n_catches) + rng.sample(misses, n_misses)
        rng.shuffle(sampled)
        print(f"  sampled: {len(sampled)} ({n_catches} catches + {n_misses} misses)")

        # Fetch PIDs for image path construction
        instance_ids = tuple(s['instance_id'] for s in sampled)
        pids = {}
        try:
            conn, cur = db.connect_to_db(brand)
            cur.execute(PID_QUERY, (instance_ids,))
            for iid, pid in cur.fetchall():
                pids[iid] = pid
            conn.close()
        except Exception as e:
            print(f"  WARNING: pid lookup failed: {e}")

        # Map archive folders, encode thumbnails
        archive_dirs = db.map_archive_dirs(brand, images_dir) if os.path.exists(images_dir) else {}
        print(f"  archive folders found: {len(archive_dirs)}")

        n_with_image = 0
        for s in sampled:
            pid = pids.get(s['instance_id'])
            img_path = find_image_path(
                archive_dirs, s['archive_id'], s['instance_id'],
                s['family_id'], s['rank'], pid
            ) if pid else None
            s['pid'] = pid
            s['image_b64'] = encode_thumbnail(img_path) if img_path else None
            if s['image_b64']:
                n_with_image += 1
        print(f"  with images: {n_with_image}/{len(sampled)}")

        all_samples.extend(sampled)

    rng.shuffle(all_samples)

    payload = {
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'random_seed': RANDOM_SEED,
        'per_brand_target': per_brand,
        'filter_keyword_count': len(HERALDIC_KEYWORDS),
        'total_sampled': len(all_samples),
        'samples': all_samples,
    }
    with open(SAMPLE_JSON, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    size_mb = os.path.getsize(SAMPLE_JSON) / (1024 * 1024)
    print(f"\nSaved {len(all_samples)} samples to {SAMPLE_JSON} ({size_mb:.1f} MB)")


# ============================================================================
# STAGE 2 — HTML
# ============================================================================

def run_html():
    """Generate self-contained HTML labeler from sample.json."""
    if not os.path.exists(SAMPLE_JSON):
        print(f"ERROR: {SAMPLE_JSON} not found — run `sample` first.")
        return
    with open(SAMPLE_JSON, 'r', encoding='utf-8') as f:
        payload = json.load(f)

    # We pass the sample data into the HTML page as a JS literal.
    # filter_catches is INCLUDED in the payload but hidden in the UI until label is given.
    sample_json_inline = json.dumps(payload, ensure_ascii=False)

    html = HTML_TEMPLATE.replace('__SAMPLE_JSON__', sample_json_inline)
    with open(LABELER_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    size_mb = os.path.getsize(LABELER_HTML) / (1024 * 1024)
    print(f"Labeler HTML written: {LABELER_HTML} ({size_mb:.1f} MB)")
    print(f"\nOpen in browser, label products, then click Export.")
    print(f"Save the downloaded file as: {LABELS_JSON}")


# ============================================================================
# STAGE 3 — ANALYZE
# ============================================================================

def _wilson_ci(k, n, z=1.96):
    """Wilson score 95% CI for binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def run_analyze():
    """Build confusion matrix from exported labels, report precision/recall."""
    if not os.path.exists(SAMPLE_JSON):
        print(f"ERROR: {SAMPLE_JSON} not found.")
        return
    if not os.path.exists(LABELS_JSON):
        print(f"ERROR: {LABELS_JSON} not found.")
        print(f"Open {LABELER_HTML} in a browser, label products, click Export,")
        print(f"and save the downloaded file as {LABELS_JSON}.")
        return

    with open(SAMPLE_JSON, 'r', encoding='utf-8') as f:
        sample = json.load(f)
    with open(LABELS_JSON, 'r', encoding='utf-8') as f:
        labels_payload = json.load(f)

    # Build instance_id → label map. Accept either {"labels": {...}} or {"<id>": "heraldic", ...}
    raw_labels = labels_payload.get('labels', labels_payload) if isinstance(labels_payload, dict) else {}
    # Normalize keys to int
    label_map = {}
    for k, v in raw_labels.items():
        try:
            label_map[int(k)] = v
        except (TypeError, ValueError):
            continue

    # Cross-reference with sample
    rows = []
    for s in sample['samples']:
        iid = s['instance_id']
        if iid not in label_map:
            continue
        v = label_map[iid]
        if v not in ('heraldic', 'not_heraldic'):
            continue  # skip "skip"
        rows.append({
            'instance_id': iid,
            'brand': s['brand'],
            'archive_id': s.get('archive_id'),
            'archive_query': s.get('archive_query', ''),
            'title': s.get('title', ''),
            'title_second': s.get('title_second', ''),
            'filter_catches': bool(s['filter_catches']),
            'label_heraldic': (v == 'heraldic'),
        })

    if not rows:
        print("No usable labels found (only skips or no overlap with sample).")
        return

    # Confusion matrix
    tp = sum(1 for r in rows if r['filter_catches'] and r['label_heraldic'])
    fp = sum(1 for r in rows if r['filter_catches'] and not r['label_heraldic'])
    fn = sum(1 for r in rows if not r['filter_catches'] and r['label_heraldic'])
    tn = sum(1 for r in rows if not r['filter_catches'] and not r['label_heraldic'])
    n = tp + fp + fn + tn

    precision_p, precision_lo, precision_hi = _ratio_with_ci(tp, tp + fp)
    recall_p,    recall_lo,    recall_hi    = _ratio_with_ci(tp, tp + fn)
    accuracy_p,  accuracy_lo,  accuracy_hi  = _ratio_with_ci(tp + tn, n)
    if precision_p and recall_p:
        f1 = 2 * precision_p * recall_p / (precision_p + recall_p)
    else:
        f1 = 0.0

    print("=" * 70)
    print("STAGE 3: ANALYZE")
    print("=" * 70)
    print(f"\nSample: {len(sample['samples'])}, labeled: {len(rows)}, skipped: {len(sample['samples']) - len(rows)}")
    print(f"Filter entries: {sample.get('filter_keyword_count', 'unknown')}")
    print()
    print("Confusion matrix (against current heraldic filter):")
    print()
    print(f"                       │ label=heraldic │ label=not       │")
    print(f"  ─────────────────────┼────────────────┼─────────────────┤")
    print(f"  filter catches       │      {tp:>5}     │      {fp:>5}      │  (precision)")
    print(f"  filter doesn't catch │      {fn:>5}     │      {tn:>5}      │")
    print()
    print(f"  precision = TP/(TP+FP) = {tp}/{tp + fp} = {precision_p:.1%}  "
          f"(95% CI [{precision_lo:.1%}, {precision_hi:.1%}])")
    print(f"  recall    = TP/(TP+FN) = {tp}/{tp + fn} = {recall_p:.1%}  "
          f"(95% CI [{recall_lo:.1%}, {recall_hi:.1%}])")
    print(f"  accuracy  = (TP+TN)/n  = {tp + tn}/{n} = {accuracy_p:.1%}  "
          f"(95% CI [{accuracy_lo:.1%}, {accuracy_hi:.1%}])")
    print(f"  F1        = {f1:.1%}")

    # Per-brand
    print()
    print("Per-brand recall (how well filter catches heraldic products by brand):")
    print(f"  {'brand':<8} {'TP':>4} {'FN':>4} {'recall':>10}")
    for brand in BRANDS:
        b_rows = [r for r in rows if r['brand'] == brand]
        b_tp = sum(1 for r in b_rows if r['filter_catches'] and r['label_heraldic'])
        b_fn = sum(1 for r in b_rows if not r['filter_catches'] and r['label_heraldic'])
        if b_tp + b_fn:
            r_p, r_lo, r_hi = _ratio_with_ci(b_tp, b_tp + b_fn)
            print(f"  {brand:<8} {b_tp:>4} {b_fn:>4}   {r_p:>5.1%}  [{r_lo:.1%}, {r_hi:.1%}]")
        else:
            print(f"  {brand:<8} {b_tp:>4} {b_fn:>4}   (no heraldic products labeled)")

    # Misclassifications — every product where filter and human disagree
    false_negatives = [r for r in rows if r['label_heraldic'] and not r['filter_catches']]
    false_positives = [r for r in rows if not r['label_heraldic'] and r['filter_catches']]

    print()
    print("=" * 70)
    print(f"Misclassifications: {len(false_negatives)} FN (leaks) + {len(false_positives)} FP (over-exclusions)")
    print("=" * 70)

    _print_misclassification_block(
        "FALSE NEGATIVES (filter missed — labeled heraldic, filter didn't catch)",
        false_negatives,
        n_preview=15,
    )
    _print_misclassification_block(
        "FALSE POSITIVES (filter over-flagged — labeled NOT heraldic, filter caught)",
        false_positives,
        n_preview=15,
    )

    # Write full misclassifications CSV
    misclass_rows = []
    for r in false_negatives:
        misclass_rows.append({'error_type': 'FN', **r})
    for r in false_positives:
        misclass_rows.append({'error_type': 'FP', **r})
    misclass_rows.sort(key=lambda r: (r['error_type'], r['brand'], r['instance_id']))

    with open(MISCLASSIFICATIONS_CSV, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'error_type', 'brand', 'instance_id', 'archive_id',
            'archive_query', 'title', 'title_second',
            'filter_caught', 'human_labeled',
        ])
        for r in misclass_rows:
            writer.writerow([
                r['error_type'],
                r['brand'],
                r['instance_id'],
                r['archive_id'],
                r['archive_query'],
                r['title'],
                r['title_second'],
                str(r['filter_catches']),
                'heraldic' if r['label_heraldic'] else 'not_heraldic',
            ])
    print(f"\nFull misclassification list written to: {MISCLASSIFICATIONS_CSV}")

    # Note about stratification bias
    print()
    print("Note: Sample was stratified by filter prediction (half catches, half misses)")
    print("      so the raw rates above are conditional on the stratum. Precision and")
    print("      recall are still valid as filter-quality metrics; they answer")
    print("      'given the filter's behavior, how often is it right?'. The catalog-")
    print("      level fraction of heraldic products is a separate quantity not")
    print("      estimable from a stratified sample without weighting.")

    # Save report
    with open(ANALYSIS_REPORT, 'w', encoding='utf-8') as f:
        f.write(f"Heraldic filter validation\n")
        f.write(f"=" * 60 + "\n")
        f.write(f"Generated: {datetime.utcnow().isoformat()}Z\n")
        f.write(f"Sample: {len(sample['samples'])} products, {len(rows)} labeled\n")
        f.write(f"Filter entries: {sample.get('filter_keyword_count')}\n\n")
        f.write(f"Confusion matrix:\n")
        f.write(f"                       | label=heraldic | label=not       |\n")
        f.write(f"  filter catches       | {tp:>14} | {fp:>15} |\n")
        f.write(f"  filter doesn't catch | {fn:>14} | {tn:>15} |\n\n")
        f.write(f"Precision: {precision_p:.4f}  95% CI [{precision_lo:.4f}, {precision_hi:.4f}]\n")
        f.write(f"Recall:    {recall_p:.4f}  95% CI [{recall_lo:.4f}, {recall_hi:.4f}]\n")
        f.write(f"Accuracy:  {accuracy_p:.4f}  95% CI [{accuracy_lo:.4f}, {accuracy_hi:.4f}]\n")
        f.write(f"F1:        {f1:.4f}\n")
        f.write(f"\nMisclassifications: {len(false_negatives)} FN (filter missed) + "
                f"{len(false_positives)} FP (filter over-flagged)\n")
        f.write(f"Full per-product list: {os.path.basename(MISCLASSIFICATIONS_CSV)}\n")
    print(f"\nReport saved: {ANALYSIS_REPORT}")


def _ratio_with_ci(k, n):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    lo, hi = _wilson_ci(k, n)
    return (p, lo, hi)


def _print_misclassification_block(header, rows, n_preview=15):
    """Print a header and up to n_preview rows of misclassifications."""
    print()
    print(header)
    print("-" * 70)
    if not rows:
        print("  (none)")
        return
    by_brand = defaultdict(list)
    for r in rows:
        by_brand[r['brand']].append(r)
    shown = 0
    for brand in BRANDS:
        for r in by_brand.get(brand, []):
            if shown >= n_preview:
                break
            title = (r['title'] or r['title_second'] or '<no title>')
            print(f"  [{brand:<7}] {title[:80]}")
            shown += 1
        if shown >= n_preview:
            break
    remaining = len(rows) - shown
    if remaining > 0:
        print(f"  ... and {remaining} more (see misclassifications.csv)")


# ============================================================================
# HTML TEMPLATE
# ============================================================================

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Heraldic Filter Validation</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #1a1a1a; color: #e0e0e0; min-height: 100vh; }
  header { padding: 16px 32px; background: #111; border-bottom: 1px solid #333;
           display: flex; align-items: center; gap: 24px; }
  header h1 { font-size: 18px; font-weight: 600; }
  .progress-wrap { flex: 1; max-width: 480px; }
  .progress-bar { height: 8px; background: #333; border-radius: 4px; overflow: hidden; }
  .progress-fill { height: 100%; background: linear-gradient(90deg, #60a5fa, #8bc0ff);
                   transition: width 0.2s; }
  .progress-text { font-size: 12px; color: #999; margin-top: 4px; }
  .export-btn { padding: 8px 16px; background: #2563eb; color: white; border: none;
                border-radius: 6px; cursor: pointer; font-size: 14px; }
  .export-btn:hover { background: #1d4ed8; }
  main { max-width: 900px; margin: 0 auto; padding: 32px; }
  .product-card { background: #222; border-radius: 12px; padding: 32px;
                  display: flex; gap: 32px; align-items: flex-start;
                  border: 1px solid #333; }
  .product-image { width: 320px; height: 320px; flex-shrink: 0; background: #111;
                   border-radius: 8px; display: flex; align-items: center; justify-content: center;
                   overflow: hidden; }
  .product-image img { max-width: 100%; max-height: 100%; }
  .product-image.empty { color: #555; font-size: 14px; }
  .product-meta { flex: 1; padding-top: 8px; }
  .brand-badge { display: inline-block; padding: 4px 12px; background: #1e3a8a;
                 color: white; border-radius: 4px; font-size: 12px; font-weight: 600;
                 text-transform: uppercase; letter-spacing: 1px; margin-bottom: 16px; }
  .title-primary { font-size: 24px; font-weight: 600; color: white; margin-bottom: 12px;
                   line-height: 1.3; }
  .title-secondary { font-size: 16px; color: #aaa; margin-bottom: 24px; line-height: 1.4; }
  .meta-row { font-size: 12px; color: #777; margin-bottom: 6px; }
  .filter-hint { margin-top: 24px; padding: 12px; background: #1a1a1a; border-left: 3px solid #555;
                 border-radius: 4px; font-size: 13px; color: #aaa; display: none; }
  .filter-hint.shown { display: block; }
  .filter-hint.catches { border-left-color: #f59e0b; }
  .filter-hint.misses { border-left-color: #6b7280; }
  .reveal-btn { background: none; border: 1px solid #333; color: #777; padding: 4px 8px;
                border-radius: 4px; cursor: pointer; font-size: 11px; margin-top: 12px; }
  .controls { display: flex; gap: 16px; justify-content: center; margin-top: 32px; }
  .btn { padding: 14px 32px; border-radius: 8px; border: 2px solid; cursor: pointer;
         font-size: 16px; font-weight: 600; transition: all 0.15s;
         background: transparent; min-width: 200px; }
  .btn-heraldic { color: #f87171; border-color: #f87171; }
  .btn-heraldic:hover, .btn-heraldic.active { background: #f87171; color: white; }
  .btn-not { color: #10b981; border-color: #10b981; }
  .btn-not:hover, .btn-not.active { background: #10b981; color: white; }
  .btn-skip { color: #999; border-color: #555; min-width: 120px; }
  .btn-skip:hover, .btn-skip.active { background: #555; color: white; }
  .nav { display: flex; justify-content: space-between; margin-top: 24px;
         font-size: 13px; color: #777; }
  .nav button { background: none; border: none; color: #60a5fa; cursor: pointer;
                font-size: 13px; padding: 8px 12px; }
  .nav button:hover { color: #8bc0ff; }
  .nav button:disabled { color: #444; cursor: default; }
  .keyboard-hint { text-align: center; margin-top: 20px; font-size: 11px; color: #666; }
  .keyboard-hint kbd { display: inline-block; padding: 2px 6px; background: #333;
                       border-radius: 3px; font-family: monospace; color: #aaa; margin: 0 2px; }
  .summary { background: #222; padding: 24px; border-radius: 12px; text-align: center;
             margin-top: 32px; }
  .summary h2 { color: white; margin-bottom: 16px; }
  .summary-stats { display: flex; gap: 32px; justify-content: center; margin: 20px 0; }
  .summary-stat { font-size: 14px; color: #aaa; }
  .summary-stat strong { display: block; font-size: 24px; color: white; }
</style>
</head>
<body>
<header>
  <h1>Heraldic Filter Validation</h1>
  <div class="progress-wrap">
    <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
    <div class="progress-text" id="progressText">0 / 0</div>
  </div>
  <button class="export-btn" onclick="exportLabels()">Export labels</button>
</header>

<main>
  <div id="cardContainer"></div>
  <div class="keyboard-hint">
    <kbd>H</kbd> heraldic &nbsp;·&nbsp; <kbd>N</kbd> not heraldic &nbsp;·&nbsp; <kbd>S</kbd> skip
    &nbsp;·&nbsp; <kbd>←</kbd> back &nbsp;·&nbsp; <kbd>→</kbd> forward
  </div>
</main>

<script>
const SAMPLE = __SAMPLE_JSON__;
const STORAGE_KEY = 'heraldic_validation_labels_v1';

let labels = {};
try { labels = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch(e) { labels = {}; }
let index = 0;

function saveLabels() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(labels));
}

function setLabel(value) {
  const item = SAMPLE.samples[index];
  labels[item.instance_id] = value;
  saveLabels();
  if (index < SAMPLE.samples.length - 1) {
    index += 1;
    render();
  } else {
    render();
  }
}

function gotoNext() { if (index < SAMPLE.samples.length - 1) { index += 1; render(); } }
function gotoPrev() { if (index > 0) { index -= 1; render(); } }
function gotoFirstUnlabeled() {
  for (let i = 0; i < SAMPLE.samples.length; i++) {
    if (!(SAMPLE.samples[i].instance_id in labels)) { index = i; render(); return; }
  }
  index = SAMPLE.samples.length - 1;
  render();
}

function exportLabels() {
  const payload = {
    exported_at: new Date().toISOString(),
    filter_keyword_count: SAMPLE.filter_keyword_count,
    random_seed: SAMPLE.random_seed,
    labels: labels,
    label_counts: {
      heraldic: Object.values(labels).filter(v => v === 'heraldic').length,
      not_heraldic: Object.values(labels).filter(v => v === 'not_heraldic').length,
      skip: Object.values(labels).filter(v => v === 'skip').length,
    },
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'labels.json';
  a.click();
  URL.revokeObjectURL(url);
}

function render() {
  const item = SAMPLE.samples[index];
  if (!item) return;
  const container = document.getElementById('cardContainer');

  // Header progress
  const labeledCount = Object.keys(labels).length;
  document.getElementById('progressFill').style.width =
    (labeledCount / SAMPLE.samples.length * 100) + '%';
  document.getElementById('progressText').textContent =
    `${labeledCount} / ${SAMPLE.samples.length} labeled  (showing #${index + 1})`;

  // All labeled?
  if (labeledCount >= SAMPLE.samples.length) {
    const counts = {
      heraldic: Object.values(labels).filter(v => v === 'heraldic').length,
      not_heraldic: Object.values(labels).filter(v => v === 'not_heraldic').length,
      skip: Object.values(labels).filter(v => v === 'skip').length,
    };
    container.innerHTML = `
      <div class="summary">
        <h2>All ${SAMPLE.samples.length} products labeled</h2>
        <div class="summary-stats">
          <div class="summary-stat"><strong>${counts.heraldic}</strong>heraldic</div>
          <div class="summary-stat"><strong>${counts.not_heraldic}</strong>not heraldic</div>
          <div class="summary-stat"><strong>${counts.skip}</strong>skipped</div>
        </div>
        <p style="color:#aaa; font-size:14px; margin-top:16px;">
          Click <strong style="color:#60a5fa">Export labels</strong> in the header to download labels.json.
        </p>
        <div style="margin-top:24px;">
          <button class="reveal-btn" onclick="index=0; render();">Review from start</button>
        </div>
      </div>`;
    return;
  }

  const existing = labels[item.instance_id];
  const titleHtml = item.title ? `<div class="title-primary">${escapeHtml(item.title)}</div>` : '';
  const subtitleHtml = item.title_second ? `<div class="title-secondary">${escapeHtml(item.title_second)}</div>` : '';
  const imageHtml = item.image_b64
    ? `<img src="data:image/jpeg;base64,${item.image_b64}" alt="">`
    : `<span>no image</span>`;

  container.innerHTML = `
    <div class="product-card">
      <div class="product-image ${item.image_b64 ? '' : 'empty'}">${imageHtml}</div>
      <div class="product-meta">
        <span class="brand-badge">${item.brand}</span>
        ${titleHtml}
        ${subtitleHtml}
        <div class="meta-row">instance ${item.instance_id} · archive ${item.archive_id} · ${escapeHtml(item.archive_query)}</div>
        <button class="reveal-btn" onclick="revealFilter()">show filter prediction</button>
        <div class="filter-hint ${item.filter_catches ? 'catches' : 'misses'}" id="filterHint">
          Current filter ${item.filter_catches ? '<strong>flags</strong> this product as heraldic.' : 'does <strong>not</strong> flag this product.'}
        </div>
      </div>
    </div>
    <div class="controls">
      <button class="btn btn-heraldic ${existing === 'heraldic' ? 'active' : ''}" onclick="setLabel('heraldic')">Heraldic</button>
      <button class="btn btn-not ${existing === 'not_heraldic' ? 'active' : ''}" onclick="setLabel('not_heraldic')">Not heraldic</button>
      <button class="btn btn-skip ${existing === 'skip' ? 'active' : ''}" onclick="setLabel('skip')">Skip</button>
    </div>
    <div class="nav">
      <button onclick="gotoPrev()" ${index === 0 ? 'disabled' : ''}>← previous</button>
      <button onclick="gotoFirstUnlabeled()">jump to next unlabeled</button>
      <button onclick="gotoNext()" ${index >= SAMPLE.samples.length - 1 ? 'disabled' : ''}>next →</button>
    </div>`;
}

function revealFilter() {
  document.getElementById('filterHint').classList.add('shown');
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[ch]);
}

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  const k = e.key.toLowerCase();
  if (k === 'h') { setLabel('heraldic'); e.preventDefault(); }
  else if (k === 'n') { setLabel('not_heraldic'); e.preventDefault(); }
  else if (k === 's') { setLabel('skip'); e.preventDefault(); }
  else if (e.key === 'ArrowLeft') { gotoPrev(); e.preventDefault(); }
  else if (e.key === 'ArrowRight') { gotoNext(); e.preventDefault(); }
});

gotoFirstUnlabeled();
</script>
</body>
</html>
"""


# ============================================================================
# CLI
# ============================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Heraldic filter validation tool — three-stage labeling workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Stages:
  sample   — pull stratified random sample, encode images, save sample.json
  html     — generate labeler.html from sample.json (open in browser)
  analyze  — read user-exported labels.json, compute precision/recall/F1

Typical flow:
  python validate_heraldic_filter.py sample --per-brand 100
  python validate_heraldic_filter.py html
  # open labeler.html, label products, click Export, save as labels.json
  python validate_heraldic_filter.py analyze
""",
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    sp = sub.add_parser('sample', help='Stage 1: stratified random sample with images')
    sp.add_argument('--per-brand', type=int, default=100,
                    help='Sample size per brand, half from filter catches, half from misses (default 100)')
    sp.add_argument('--images-dir', default=DEFAULT_IMAGES_DIR,
                    help=f'Image directory root (default: {DEFAULT_IMAGES_DIR})')

    sub.add_parser('html', help='Stage 2: generate labeler HTML from sample.json')
    sub.add_parser('analyze', help='Stage 3: compute precision/recall from labels.json')

    args = parser.parse_args(argv)

    if args.cmd == 'sample':
        run_sample(args.per_brand, args.images_dir)
    elif args.cmd == 'html':
        run_html()
    elif args.cmd == 'analyze':
        run_analyze()


if __name__ == '__main__':
    main()
