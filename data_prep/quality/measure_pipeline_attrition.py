"""
ENTRY - run: python cli.py measure-attrition  (per-brand/gender pipeline attrition audit).

measure_pipeline_attrition.py — Per-brand × gender audit of how much catalog
data ends up as visualized data, attributing each unvisualized product to
its first failure point.

Reads the live Postgres DBs (instance, segment_fpyolo11l241114, and
cluster_fpyolo11l241114_kmeans250218 tables), parses processing_log.txt
files under 03_images, and reads the CIEDE2000 product_assignments.csv
files in production/outputs/. Each instance is attributed to exactly one
bucket — the first stage at which it left the pipeline (or final_palette
if it ended up visualized).

Output: per-brand × gender attrition table with counts and percentages, plus
per-analysis-mode visualization counts (Palette / Discount Frequency /
Discount Depth / Price Level).

Usage:
    python measure_pipeline_attrition.py
    python measure_pipeline_attrition.py --brand nike
"""

import argparse
import os
import re
from pathlib import Path

import pandas as pd
import psycopg2

from data_prep.heraldic_filter import HERALDIC_KEYWORDS
from data_prep.keyword_maps import BRAND_KEYWORD_MAPS


BRANDS = ['nike', 'adidas', 'puma', 'lulu', 'ua']

IMAGE_ROOT = Path(os.environ.get("IMAGE_DIR", r"D:\D01_Code_Forge\01_Codebase\Codebase_ColorAnalytics\03_images"))
# Walk quality -> data_prep -> repo root.
PIPELINE_ROOT = Path(__file__).resolve().parents[2]
PALETTE_ASSIGNMENTS_DIR = PIPELINE_ROOT / "palette_explorer" / "outputs"


# Stages, in pipeline-logical order. Each instance lands in exactly one.
# Attribution checks final outcomes first (palette / clustered / segmented),
# then falls back to text-filter + log-derived reasons for non-segmented
# products. This is more accurate than walking filters first, because the
# heraldic filter and keyword map have been edited over the project's life
# — a product caught by today's filter may have passed through and been
# segmented under an older version.
STAGES = [
    'null_title',            # both title and title_second are NULL/empty
    'heraldic_filter',       # title matches current heraldic regex
    'keyword_map_miss',      # no word in brand's keyword map matches title
    'archive_unsegmented',   # archive folder has no processing log — segmentation never run for this archive
    'yolo_no_masks',         # YOLO detected nothing in the image
    'yolo_no_garment',       # YOLO detected but no matching garment class
    'yolo_file_not_found',   # image file missing
    'yolo_other',            # filename_format_error, exception, etc.
    'yolo_unlogged',         # archive WAS segmented but this specific instance has no log entry
    'clustering_failed',     # segmented but absent from cluster table
    'gender_unknown',        # clustered but archive query lacks mens/womens
    'dominant_merge_failed', # passed gender filter but dropped by analyze step (hue-merge returned empty)
    'final_palette',          # in product_assignments.csv (visualized)
]


# Stage metadata for the aggregate reclamation outlook printed after the
# per-brand audits. Each entry: humanized name, group (drives section split),
# optional flag (appended to the headline), what's-happening description,
# and reclaim-by recommendation.
STAGE_INFO = {
    'yolo_no_garment': {
        'humanized': 'Wrong garment class',
        'group': 'reclaimable',
        'whats_happening': (
            "YOLO detected garment-class objects, but none matched the type\n"
            "the keyword map said to look for. Often: a title routed to\n"
            '"shorts" but the dominant detection is a "top".'
        ),
        'reclaim_by': (
            "Auditing brand keyword-map → YOLO-class mappings, or accepting\n"
            "the largest detected garment mask when only one type is present."
        ),
    },
    'yolo_no_masks': {
        'humanized': 'YOLO detected nothing',
        'group': 'reclaimable',
        'whats_happening': (
            "Model returned zero garment masks. Complex backgrounds, multi-\n"
            "product photos, unusual crops, or garments outside Fashionpedia's\n"
            "training set."
        ),
        'reclaim_by': (
            "Fine-tuning YOLO on failed examples, a stronger backbone, or\n"
            "pre-cropping photos before inference."
        ),
    },
    'keyword_map_miss': {
        'humanized': 'Unrecognized title',
        'group': 'reclaimable',
        'whats_happening': (
            "Product title has no word in the brand's keyword map. The\n"
            "pipeline doesn't know what garment class to look for, so it\n"
            "skips before opening the image."
        ),
        'reclaim_by': (
            "Patching uncovered-title CSVs per brand (see\n"
            "validate_keyword_map_coverage.py). S109 measured per-brand\n"
            "coverage 80–95%; Lulu has the largest gap."
        ),
    },
    'archive_unsegmented': {
        'humanized': 'Pending segmentation',
        'group': 'reclaimable',
        'whats_happening': (
            "Whole archives haven't been run through segmentation yet. Not a\n"
            "failure — just pending work."
        ),
        'reclaim_by': (
            "Running segmentation against the archive_ids listed in the\n"
            "per-brand sections above."
        ),
    },
    'clustering_failed': {
        'humanized': 'Clustering failure',
        'group': 'reclaimable',
        'whats_happening': (
            "Segmented but no row in the cluster table. Likely: corrupt PNG,\n"
            "all-transparent mask, too few opaque pixels for K-means."
        ),
        'reclaim_by': (
            "Reading clustering_error_log.txt; re-running cluster.py\n"
            "against the affected segments."
        ),
    },
    'dominant_merge_failed': {
        'humanized': 'Dominant-color merge failed',
        'group': 'reclaimable',
        'whats_happening': (
            "Clustered but compute_dominant_clusters() in the analyze step\n"
            "returned empty — the 5 K-means clusters couldn't be hue-merged.\n"
            "Rare edge case."
        ),
        'reclaim_by': (
            "Investigating merge_clusters_hue_based() on these instances."
        ),
    },
    'yolo_unlogged': {
        'humanized': 'Logged failure not classified',
        'group': 'reclaimable',
        'whats_happening': (
            "Archive WAS segmented but this specific instance has no log\n"
            "line. Suggests a logging gap or an interrupted run."
        ),
        'reclaim_by': (
            "Re-running segmentation for the affected archive; or debugging\n"
            "the row-log loop."
        ),
    },
    'heraldic_filter': {
        'humanized': 'Heraldic filter',
        'flag': '⚠ INTENTIONAL',
        'group': 'intentional',
        'whats_happening': (
            "Title contains a team, league, college, country, or city name.\n"
            "Filter excludes licensed/branded merch so prescribed colors\n"
            "don't pollute brand-design color analysis."
        ),
        'reclaim_by': (
            "Not recommended in aggregate. Filter precision is 81.8% (S108)\n"
            "— ~18% are legitimate brand-design items wrongly excluded. The\n"
            "S108 REVIEW BACKLOG flags 6 over-broad keywords for targeted\n"
            "reclamation."
        ),
    },
    'gender_unknown': {
        'humanized': 'No gender label',
        'group': 'intentional',
        'whats_happening': (
            'Archive\'s query string contains neither "mens" nor "womens",\n'
            "so the product can't be placed in either gendered analysis."
        ),
        'reclaim_by': (
            "Inferring gender from category or title; or running ungendered\n"
            "analysis as a separate axis."
        ),
    },
    'yolo_file_not_found': {
        'humanized': 'Missing image file',
        'group': 'intentional',
        'whats_happening': (
            "Image referenced by the instance is missing on disk."
        ),
        'reclaim_by': (
            "Re-scraping the affected products."
        ),
    },
    'yolo_other': {
        'humanized': 'Other YOLO error',
        'group': 'intentional',
        'whats_happening': (
            "Filename format errors, YOLO exceptions, edge cases."
        ),
        'reclaim_by': (
            "Inspecting processing_log.txt for specific messages."
        ),
    },
    'null_title': {
        'humanized': 'Empty title',
        'group': 'intentional',
        'whats_happening': (
            "Both title and title_second are NULL/empty. No text to route\n"
            "from."
        ),
        'reclaim_by': (
            "Re-scraping titles if they exist on source pages; otherwise\n"
            "unrecoverable."
        ),
    },
}


# Curated top-3 reclamation opportunities. Each tuple: (title, list of stages
# to sum counts from, qualitative note).
TOP_OPPORTUNITIES = [
    (
        'Improving YOLO/Fashionpedia (no_garment + no_masks)',
        ['yolo_no_garment', 'yolo_no_masks'],
        'Largest opportunity, hardest fix. Model fine-tuning effort.',
    ),
    (
        'Extending brand keyword maps (keyword_map_miss)',
        ['keyword_map_miss'],
        'Smaller opportunity, easier fix — vocabulary patching per brand.',
    ),
    (
        'Running segmentation on pending archives (archive_unsegmented)',
        ['archive_unsegmented'],
        'Already-built pipeline; just needs to be executed.',
    ),
]


# Patterns for parsing per-row lines in processing_log.txt. Supports both
# the older "Skipping because no garment detection found." phrasing and
# the newer "no garment detection" phrasing.
LOG_PATTERNS = [
    (re.compile(r"^(?P<fn>[^:]+): no masks\b"), 'yolo_no_masks'),
    (re.compile(r"^(?P<fn>[^:]+): Skipping because no masks", re.IGNORECASE), 'yolo_no_masks'),
    (re.compile(r"^(?P<fn>[^:]+): no keyword match\b"), 'keyword_map_miss'),
    (re.compile(r"^(?P<fn>[^:]+): Skipping because no keyword", re.IGNORECASE), 'keyword_map_miss'),
    (re.compile(r"^(?P<fn>[^:]+): no garment detection\b"), 'yolo_no_garment'),
    (re.compile(r"^(?P<fn>[^:]+): Skipping because no garment", re.IGNORECASE), 'yolo_no_garment'),
    (re.compile(r"^(?P<fn>[^:]+): file not found\b"), 'yolo_file_not_found'),
    (re.compile(r"^(?P<fn>[^:]+): bad filename format\b"), 'yolo_other'),
    (re.compile(r"^(?P<fn>[^:]+): exception\b"), 'yolo_other'),
    # Success patterns — treated as 'segmented' (final outcome comes from DB)
    (re.compile(r"^(?P<fn>[^:]+): success \(seg_id"), 'segmented'),
    (re.compile(r"^(?P<fn>[^:]+): Processed successfully", re.IGNORECASE), 'segmented'),
]


def compile_word_pattern(words):
    if not words:
        return None
    pattern = r"\b(" + "|".join(re.escape(w) for w in words) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


def extract_gender(query):
    if query is None:
        return 'unknown'
    q = query.lower()
    if 'womens' in q or 'women' in q:
        return 'womens'
    if 'mens' in q or 'men' in q:
        return 'mens'
    return 'unknown'


def connect_brand_db(brand):
    password = os.getenv("POSTGRES_PASSWORD")
    if not password:
        raise SystemExit("POSTGRES_PASSWORD environment variable must be set")
    return psycopg2.connect(
        dbname=brand, user='postgres', password=password,
        host=os.getenv("DB_HOST", "localhost"), port='5432'
    )


def query_instance_data(brand):
    conn = connect_brand_db(brand)
    cur = conn.cursor()
    cur.execute("""
        SELECT
            i.instance_id, i.archive_id_ref,
            i.title, i.title_second,
            a.query AS archive_query,
            i.price_std, i.price_curr
        FROM instance i
        JOIN archive a ON i.archive_id_ref = a.archive_id
    """)
    rows = cur.fetchall()
    conn.close()
    df = pd.DataFrame(rows, columns=[
        'instance_id', 'archive_id', 'title', 'title_second',
        'archive_query', 'price_std', 'price_curr'
    ])
    return df


def query_distinct_ids(brand, table, column):
    conn = connect_brand_db(brand)
    cur = conn.cursor()
    cur.execute(f"SELECT DISTINCT {column} FROM {table}")
    ids = {row[0] for row in cur.fetchall()}
    conn.close()
    return ids


def parse_log_filename(fn):
    """Filename: archiveID-instanceID-familyID-rank-PID. Returns (aid, iid) or None."""
    parts = fn.split("-")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def read_log_archive_id(log_path):
    """Read the 'Archive ID: N' header from the first line of a processing log."""
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            first = f.readline().strip()
    except OSError:
        return None
    m = re.match(r"Archive ID:\s*(\d+)", first)
    return int(m.group(1)) if m else None


def parse_processing_log(log_path):
    """Yield (instance_id, reason) from a processing_log.txt. First matching
    line per instance wins (so 'success' overrides nothing because earlier
    failure lines already attributed the instance)."""
    seen = set()
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.rstrip()
                for pat, reason in LOG_PATTERNS:
                    m = pat.match(line)
                    if not m:
                        continue
                    parsed = parse_log_filename(m.group('fn'))
                    if parsed is None:
                        break
                    _, iid = parsed
                    if iid in seen:
                        break
                    seen.add(iid)
                    yield iid, reason
                    break
    except OSError:
        pass


def find_brand_logs(brand):
    """Most-recent processing_log.txt per archive folder for this brand."""
    logs = {}
    if not IMAGE_ROOT.exists():
        print(f"    WARNING: image root not found: {IMAGE_ROOT}")
        return logs
    for archive_dir in IMAGE_ROOT.iterdir():
        if not archive_dir.is_dir():
            continue
        if not archive_dir.name.startswith(f"{brand}_"):
            continue
        seg_dirs = sorted(
            [d for d in archive_dir.iterdir()
             if d.is_dir() and d.name.startswith("fpyolo11l241114_")],
            key=lambda d: d.name,
        )
        if not seg_dirs:
            continue
        log_path = seg_dirs[-1] / "processing_log.txt"
        if log_path.exists():
            logs[archive_dir.name] = log_path
    return logs


def load_palette_ids(brand):
    """Load instance_ids present in product_assignments.csv for both genders."""
    palette = set()
    missing = []
    for gender in ('mens', 'womens'):
        csv_path = PALETTE_ASSIGNMENTS_DIR / f"{brand}_{gender}" / "product_assignments.csv"
        if not csv_path.exists():
            missing.append(str(csv_path))
            continue
        df = pd.read_csv(csv_path, usecols=['instance_id'])
        palette.update(df['instance_id'].astype(int).tolist())
    return palette, missing


def attribute_brand(brand):
    print(f"\n[{brand.upper()}] Loading data...")

    inst_df = query_instance_data(brand)
    print(f"    instance table:                {len(inst_df):>10,} rows")

    segmented = query_distinct_ids(brand, 'segment_fpyolo11l241114', 'instance_id_ref')
    print(f"    distinct segmented instances:  {len(segmented):>10,}")

    clustered = query_distinct_ids(brand, 'cluster_fpyolo11l241114_kmeans250218', 'instance_id_ref')
    print(f"    distinct clustered instances:  {len(clustered):>10,}")

    palette, missing_csvs = load_palette_ids(brand)
    print(f"    products in palette mode:      {len(palette):>10,}")
    for m in missing_csvs:
        print(f"      MISSING: {m}")

    print(f"    parsing processing logs...")
    log_paths = find_brand_logs(brand)
    log_attribution = {}
    archives_with_logs = set()
    for arch_name, log_path in log_paths.items():
        aid = read_log_archive_id(log_path)
        if aid is not None:
            archives_with_logs.add(aid)
        for iid, reason in parse_processing_log(log_path):
            log_attribution[iid] = reason
    all_archive_ids = set(int(x) for x in inst_df['archive_id'].unique())
    unsegmented_archives = all_archive_ids - archives_with_logs
    print(f"      logs found: {len(log_paths):,} archives, "
          f"{len(log_attribution):,} instance attributions")
    if unsegmented_archives:
        print(f"      archives with no log (segmentation not run): "
              f"{sorted(unsegmented_archives)}")

    heraldic_re = compile_word_pattern(HERALDIC_KEYWORDS)
    kw_map = BRAND_KEYWORD_MAPS.get(brand, {})
    kw_re = compile_word_pattern(list(kw_map.keys()))

    # Coerce NULL titles (loaded as NaN) to empty strings so concatenation works.
    inst_df['title'] = inst_df['title'].fillna('').astype(str)
    inst_df['title_second'] = inst_df['title_second'].fillna('').astype(str)

    def title_text(row):
        return (row['title'] + ' ' + row['title_second']).strip()

    def classify_row(row):
        iid = int(row['instance_id'])
        # 1. Final outcomes first (database state is authoritative).
        if iid in palette:
            return 'final_palette'
        if iid in clustered:
            if extract_gender(row['archive_query']) == 'unknown':
                return 'gender_unknown'
            return 'dominant_merge_failed'
        if iid in segmented:
            return 'clustering_failed'
        # 2. Not segmented. Walk filters in logical pipeline order.
        title = title_text(row)
        if not title:
            return 'null_title'
        if heraldic_re and heraldic_re.search(title):
            return 'heraldic_filter'
        if kw_re is None or not kw_re.search(title):
            return 'keyword_map_miss'
        # 3. Text filters passed. If the archive itself has no log, segmentation
        #    has not been run for it; otherwise defer to the per-instance log.
        if int(row['archive_id']) not in archives_with_logs:
            return 'archive_unsegmented'
        return log_attribution.get(iid, 'yolo_unlogged')

    print(f"    attributing {len(inst_df):,} instances...")
    inst_df['attrition_stage'] = inst_df.apply(classify_row, axis=1)
    inst_df['gender'] = inst_df['archive_query'].apply(extract_gender)
    inst_df['price_std_num'] = pd.to_numeric(inst_df['price_std'], errors='coerce')
    inst_df['price_curr_num'] = pd.to_numeric(inst_df['price_curr'], errors='coerce')
    inst_df['is_discounted'] = (
        (inst_df['price_curr_num'] < inst_df['price_std_num']) &
        (inst_df['price_std_num'] > 0)
    )
    return inst_df


def summarize_brand(inst_df, brand):
    print(f"\n{'=' * 100}")
    print(f"  {brand.upper()}")
    print(f"{'=' * 100}")

    for gender in ('mens', 'womens', 'unknown'):
        sub = inst_df[inst_df['gender'] == gender]
        n_total = len(sub)
        if n_total == 0:
            continue

        print(f"\n  [{gender.upper()}] — {n_total:,} instances total")
        print(f"  {'Stage':<24} {'Count':>10} {'% of raw':>10}   {'cum dropped':>12}")
        print(f"  {'-' * 24} {'-' * 10} {'-' * 10}   {'-' * 12}")

        cum_dropped = 0
        for stage in STAGES:
            n = int((sub['attrition_stage'] == stage).sum())
            pct = n / n_total * 100
            if stage == 'final_palette':
                print(f"  {'-' * 60}")
                print(f"  {stage:<24} {n:>10,} {pct:>9.2f}%   {'(visualized)':>12}")
            else:
                cum_dropped += n
                cum_pct = cum_dropped / n_total * 100
                print(f"  {stage:<24} {n:>10,} {pct:>9.2f}%   {cum_pct:>11.2f}%")

        palette_rows = sub[sub['attrition_stage'] == 'final_palette']
        n_palette = len(palette_rows)
        n_freq = int(palette_rows['price_std_num'].notna().sum())
        n_depth = int((palette_rows['is_discounted'] & palette_rows['price_std_num'].notna()).sum())
        n_price = n_freq  # Price Level uses price_std (== list_price) same as freq

        print(f"\n  Per-analysis-mode visualization (subset of {n_palette:,} palette products):")
        print(f"    {'Palette mode':<24} {n_palette:>10,}   ({n_palette/n_total*100:>5.2f}% of raw)")
        print(f"    {'Discount Frequency':<24} {n_freq:>10,}   ({n_freq/n_total*100:>5.2f}% of raw)")
        print(f"    {'Discount Depth':<24} {n_depth:>10,}   ({n_depth/n_total*100:>5.2f}% of raw)")
        print(f"    {'Price Level':<24} {n_price:>10,}   ({n_price/n_total*100:>5.2f}% of raw)")


def print_reclamation_outlook(brand_dfs, scope_label):
    """Aggregate every per-brand attribution DataFrame, sort stages by count
    within their group, and render the reclamation-outlook section."""
    if not brand_dfs:
        return
    combined = pd.concat(brand_dfs, ignore_index=True)
    stage_counts = combined['attrition_stage'].value_counts().to_dict()
    n_total = len(combined)
    n_palette = stage_counts.get('final_palette', 0)
    n_dropped = n_total - n_palette

    print(f"\n{'=' * 100}")
    print(f"  PIPELINE ATTRITION RECLAMATION OUTLOOK — {scope_label}")
    print(f"{'=' * 100}\n")

    print(f"  Aggregate denominator:  {n_total:>10,} products")
    print(f"  Currently visualized:   {n_palette:>10,} ({n_palette/n_total*100:.1f}%)  →  palette mode")
    print(f"  Total dropped:          {n_dropped:>10,} ({n_dropped/n_total*100:.1f}%)")

    def render_group(group_label, group_key, start_num):
        print(f"\n╭{'─' * 78}╮")
        print(f"│  {group_label:<76}│")
        print(f"╰{'─' * 78}╯\n")

        in_group = [
            (s, stage_counts.get(s, 0))
            for s, info in STAGE_INFO.items()
            if info['group'] == group_key
        ]
        in_group.sort(key=lambda x: -x[1])

        num = start_num
        for i, (stage, count) in enumerate(in_group):
            info = STAGE_INFO[stage]
            name_part = info['humanized']
            if 'flag' in info:
                name_part = f"{name_part} {info['flag']}"
            pct = count / n_total * 100
            prefix = f"  #{num} ─ "
            indent = ' ' * len(prefix)
            print(f"{prefix}{name_part:<43} {count:>10,} dropped ({pct:>5.2f}%)")
            print(f"{indent}stage: {stage}")
            print()
            print(f"{indent}› What's happening")
            for line in info['whats_happening'].split('\n'):
                print(f"{indent}    {line}")
            print()
            print(f"{indent}› Reclaim by")
            for line in info['reclaim_by'].split('\n'):
                print(f"{indent}    {line}")
            print()
            if i < len(in_group) - 1:
                print(f"  {'─' * 78}")
                print()
            num += 1
        return num

    next_num = render_group(
        'RECLAIMABLE — fixes here would add products to the visualization',
        'reclaimable', 1,
    )
    render_group(
        'INTENTIONAL OR HARD-TO-RECLAIM — working as designed or unrecoverable',
        'intentional', next_num,
    )

    print(f"\n╭{'─' * 78}╮")
    print(f"│  {'TOP-3 RECLAMATION OPPORTUNITIES':<76}│")
    print(f"╰{'─' * 78}╯\n")
    for i, (title, stages, note) in enumerate(TOP_OPPORTUNITIES, 1):
        total = sum(stage_counts.get(s, 0) for s in stages)
        pct = total / n_total * 100
        print(f"  {i}. {title}")
        print(f"       {total:,} products ({pct:.1f}% of raw catalog)")
        print(f"       {note}")
        if i < len(TOP_OPPORTUNITIES):
            print()
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--brand', choices=BRANDS, help="Audit only this brand")
    args = parser.parse_args()

    targets = [args.brand] if args.brand else BRANDS
    all_dfs = []
    for brand in targets:
        try:
            df = attribute_brand(brand)
            summarize_brand(df, brand)
            all_dfs.append(df)
        except Exception as e:
            print(f"\n  {brand.upper()}: ERROR — {type(e).__name__}: {e}")
            raise

    if all_dfs:
        if len(targets) == len(BRANDS):
            scope = "all 5 brands × all genders combined"
        elif len(targets) == 1:
            scope = f"{targets[0].upper()} only"
        else:
            scope = " + ".join(b.upper() for b in targets) + " combined"
        print_reclamation_outlook(all_dfs, scope)


if __name__ == '__main__':
    main()
