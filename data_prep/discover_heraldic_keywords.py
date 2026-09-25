r"""
ENTRY - run: python cli.py discover-heraldic-keywords  (surfaces candidate keywords for heraldic_filter.py).

Heraldic Keyword Discovery Tool
===============================

A purpose-built workflow for discovering, reviewing, and quantifying additions
to heraldic_filter.py's `HERALDIC_KEYWORDS` list.

Three stages:

  1. scan    — Query the raw `instance` table (pre-filter), tokenize titles
               into unigrams + capitalized bigrams, compute signals per term
               (per-brand counts, cv, skew, cap_rate, in_filter), and output a
               ranked CSV of candidates.

  2. review  — Interactive CLI: walk the top N not-in-filter candidates, show
               their signals and 5 sample titles each, and accept/skip each.
               Decisions persist to JSON so re-runs resume mid-way. At the end,
               emit a ready-to-paste Python list of accepted terms.

  3. sanity  — Quantify impact. Count distinct products in `instance` that the
               *current* heraldic regex matches vs the *expanded* regex (with
               accepted additions). Report delta both per-brand and overall —
               this gives the writeup a concrete number.

Ranking signal:
    score = max_brand_count * (max_brand_count + 1) / (second_max_brand + 1)
    in_filter terms get score=0 so they sink to the bottom.

  The reasoning: heraldic terms are typically brand-exclusive (licensing deals),
  so the dominant brand's count vastly exceeds the second-highest brand's. This
  formula prefers brand-skewed high-volume terms — exactly the heraldic shape.

Usage:
    python discover_heraldic_keywords.py scan
    python discover_heraldic_keywords.py review --top 100
    python discover_heraldic_keywords.py sanity
    python discover_heraldic_keywords.py all --top 100

    # Locally:
    .\.cluster_venv\Scripts\python.exe discover_heraldic_keywords.py scan

    # Docker (Stage 2 needs interactive TTY: add -it flag):
    docker compose run --rm pipeline python discover_heraldic_keywords.py scan
    docker compose run --rm -it pipeline python discover_heraldic_keywords.py review

Requires: POSTGRES_PASSWORD env var.

Outputs (data_prep/outputs/heraldic_audit/, next to this module):
    candidates.csv
    decisions.json
    new_terms.txt
    sanity_report.txt
"""

import os
import sys
import re
import json
import argparse
from collections import defaultdict
import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from shared import db
from data_prep.heraldic_filter import HERALDIC_KEYWORDS

# ============================================================================
# CONFIGURATION
# ============================================================================

BRANDS = ['nike', 'adidas', 'puma', 'lulu', 'ua']

_HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_HERE, 'outputs', 'heraldic_audit')
CANDIDATES_CSV = os.path.join(OUTPUT_DIR, 'candidates.csv')
DECISIONS_JSON = os.path.join(OUTPUT_DIR, 'decisions.json')
NEW_TERMS_FILE = os.path.join(OUTPUT_DIR, 'new_terms.txt')
SANITY_REPORT = os.path.join(OUTPUT_DIR, 'sanity_report.txt')

SAMPLE_TITLES_PER_TERM = 12     # samples stored per term in CSV
MIN_TOTAL_PRODUCTS = 30         # low threshold — surface niche heraldic leaks
REVIEW_SAMPLES_FIRST_PASS = 5   # samples shown initially during review

# ============================================================================
# NOISE WORD LISTS — carried over from discover_subcategories.py
# ============================================================================

STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
    'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
    'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need',
    'it', 'its', 'this', 'that', 'these', 'those', 'i', 'you', 'he', 'she',
    'we', 'they', 'what', 'which', 'who', 'whom', 'when', 'where', 'why',
    'how', 'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other',
    'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than',
    'too', 'very', 'just', 'also', 'now', 'new', 'first', 'last', 'long',
    'great', 'little', 'old', 'right', 'big', 'high', 'low',
}

BRAND_NAMES = {
    'nike', 'adidas', 'puma', 'lululemon', 'lulu', 'under', 'armour', 'ua',
    'jordan', 'converse', 'reebok', 'fila', 'champion', 'new', 'balance',
    'asics', 'skechers', 'vans', 'north', 'face', 'patagonia', 'columbia',
    'swoosh', 'jumpman', 'trefoil',
}

COLORS = {
    'black', 'white', 'red', 'blue', 'green', 'yellow', 'orange', 'purple',
    'pink', 'brown', 'grey', 'gray', 'navy', 'beige', 'cream', 'tan',
    'maroon', 'burgundy', 'teal', 'cyan', 'magenta', 'coral', 'salmon',
    'olive', 'khaki', 'charcoal', 'ivory', 'gold', 'silver', 'bronze',
    'multicolor', 'multi', 'heather', 'camo', 'camouflage', 'print',
    'neon', 'bright', 'dark', 'light', 'pale', 'deep', 'vivid',
}

SIZES = {
    'xs', 'sm', 'md', 'lg', 'xl', 'xxl', 'xxxl', 'small', 'medium', 'large',
    'extra', 'plus', 'petite', 'tall', 'short', 'long', 'regular', 'standard',
    '2xl', '3xl', '4xl', '5xl', 'one', 'size', 'fits', 'all',
}

GENERIC_WORDS = {
    'mens', 'men', 'womens', 'women', 'man', 'woman', 'unisex', 'adult',
    'kids', 'kid', 'boys', 'boy', 'girls', 'girl', 'youth', 'junior',
    'clothing', 'clothes', 'apparel', 'wear', 'gear', 'collection',
    'product', 'item', 'style', 'design', 'edition', 'version', 'series',
    'pro', 'premium', 'elite', 'deluxe', 'luxury', 'basic', 'classic',
    'original', 'authentic', 'official', 'licensed', 'limited',
    'sale', 'clearance', 'discount', 'deal', 'offer', 'price',
    'free', 'shipping', 'return', 'exchange', 'warranty',
    'review', 'rating', 'star', 'best', 'seller', 'popular', 'trending',
    'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x',
    'gen', 'generation', 'model',
}

ALL_NOISE_WORDS = STOPWORDS | BRAND_NAMES | COLORS | SIZES | GENERIC_WORDS

# ============================================================================
# SQL
# ============================================================================

TITLE_QUERY = """
    SELECT
        i.title,
        i.title_second,
        i.instance_id
    FROM instance i
    JOIN archive a ON i.archive_id_ref = a.archive_id
"""

# Postgres POSIX regex uses \y for word boundary (NOT \b — that's Python only).
COUNT_REGEX_QUERY = """
    SELECT COUNT(DISTINCT i.instance_id)
    FROM instance i
    WHERE i.title ~* %s OR i.title_second ~* %s
"""

TOTAL_PRODUCTS_QUERY = """
    SELECT COUNT(DISTINCT instance_id) FROM instance
"""

# ============================================================================
# TEXT EXTRACTION
# ============================================================================

_TOKEN_SPLIT = re.compile(r"[\s\-]+")
_NON_WORD = re.compile(r"[^\w\s\-']")


def _split_preserving_case(text):
    """Return list of case-preserving tokens (>=2 chars, alpha-only after lowercase)."""
    cleaned = _NON_WORD.sub(' ', text)
    raw = _TOKEN_SPLIT.split(cleaned)
    out = []
    for t in raw:
        if len(t) < 2:
            continue
        tl = t.lower()
        if not tl.isalpha():
            continue
        out.append(t)
    return out


def extract_unigrams_with_case(text):
    """
    Return [(lowercased_word, was_capitalized), ...] for non-noise unigrams.
    """
    if not isinstance(text, str) or not text:
        return []
    out = []
    for t in _split_preserving_case(text):
        tl = t.lower()
        if tl in ALL_NOISE_WORDS:
            continue
        out.append((tl, t[0].isupper()))
    return out


def extract_capitalized_bigrams(text):
    """
    Return lowercased bigram strings ("new york") for consecutive pairs of
    capitalized non-noise words.
    """
    if not isinstance(text, str) or not text:
        return []
    tokens = _split_preserving_case(text)
    out = []
    for i in range(len(tokens) - 1):
        a, b = tokens[i], tokens[i + 1]
        if not (a[0].isupper() and b[0].isupper()):
            continue
        al, bl = a.lower(), b.lower()
        if al in ALL_NOISE_WORDS or bl in ALL_NOISE_WORDS:
            continue
        out.append(f"{al} {bl}")
    return out


# ============================================================================
# FILTER LOOKUPS — what's already covered
# ============================================================================

def build_single_word_filter_set(keywords):
    """Lowercase single-word, alpha-only heraldic entries."""
    s = set()
    for kw in keywords:
        if not isinstance(kw, str):
            continue
        c = kw.strip().lower()
        if not c or ' ' in c or '-' in c:
            continue
        if c.isalpha():
            s.add(c)
    return s


def build_phrase_filter_set(keywords):
    """Lowercase multi-word/hyphenated heraldic entries, hyphens normalized to spaces."""
    s = set()
    for kw in keywords:
        if not isinstance(kw, str):
            continue
        c = kw.strip().lower()
        if ' ' in c or '-' in c:
            s.add(c.replace('-', ' '))
    return s


def build_postgres_filter_regex(keywords):
    """Postgres POSIX regex pattern for `WHERE title ~* <pattern>`. Uses \\y."""
    if not keywords:
        return None
    escaped = [re.escape(k) for k in keywords if isinstance(k, str) and k.strip()]
    if not escaped:
        return None
    return r"\y(" + "|".join(escaped) + r")\y"


# ============================================================================
# STAGE 1 — SCAN
# ============================================================================

def run_scan():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    single_word_filter = build_single_word_filter_set(HERALDIC_KEYWORDS)
    phrase_filter = build_phrase_filter_set(HERALDIC_KEYWORDS)

    print("=" * 70)
    print("STAGE 1: SCAN")
    print("=" * 70)
    print(f"Heraldic filter currently has {len(HERALDIC_KEYWORDS)} entries total")
    print(f"  - {len(single_word_filter)} single-word entries (matched against unigrams)")
    print(f"  - {len(phrase_filter)} multi-word/hyphenated phrases (matched against bigrams)")
    print(f"Min total products per candidate: {MIN_TOTAL_PRODUCTS}")

    # term_stats: key = (type, term) → dict
    term_stats = defaultdict(lambda: {
        'occurrences': 0,
        'capitalized_occurrences': 0,
        'per_brand_products': defaultdict(set),
        'sample_titles': [],
    })

    for brand in BRANDS:
        print(f"\n[{brand}] querying instance table...")
        try:
            conn, cur = db.connect_to_db(brand)
            cur.execute(TITLE_QUERY)
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        print(f"  {len(rows):,} title rows fetched")

        for title, title_second, instance_id in rows:
            for text in (title, title_second):
                if not isinstance(text, str) or not text:
                    continue

                for word, was_cap in extract_unigrams_with_case(text):
                    rec = term_stats[('unigram', word)]
                    rec['occurrences'] += 1
                    if was_cap:
                        rec['capitalized_occurrences'] += 1
                    rec['per_brand_products'][brand].add(instance_id)
                    if len(rec['sample_titles']) < SAMPLE_TITLES_PER_TERM:
                        rec['sample_titles'].append(f"[{brand}] {text}")

                for bigram in extract_capitalized_bigrams(text):
                    rec = term_stats[('bigram', bigram)]
                    rec['occurrences'] += 1
                    rec['capitalized_occurrences'] += 1  # by construction
                    rec['per_brand_products'][brand].add(instance_id)
                    if len(rec['sample_titles']) < SAMPLE_TITLES_PER_TERM:
                        rec['sample_titles'].append(f"[{brand}] {text}")

    print(f"\nAggregating signals for {len(term_stats):,} raw candidate terms...")

    results = []
    for (term_type, term), rec in term_stats.items():
        per_brand_counts = {b: len(rec['per_brand_products'].get(b, set())) for b in BRANDS}
        total_products = sum(per_brand_counts.values())

        if total_products < MIN_TOTAL_PRODUCTS:
            continue

        sorted_counts = sorted(per_brand_counts.values(), reverse=True)
        max_count = sorted_counts[0]
        second_max = sorted_counts[1] if len(sorted_counts) > 1 else 0

        nonzero = [c for c in per_brand_counts.values() if c > 0]
        cv = float(np.std(nonzero) / np.mean(nonzero)) if len(nonzero) >= 2 else 0.0
        cap_rate = rec['capitalized_occurrences'] / rec['occurrences'] if rec['occurrences'] else 0.0
        skew = (max_count + 1) / (second_max + 1)

        if term_type == 'unigram':
            in_filter = term in single_word_filter
        else:
            in_filter = term in phrase_filter

        # in_filter terms get score=0; rest scored by brand-skewed volume
        score = 0.0 if in_filter else max_count * skew

        results.append({
            'term': term,
            'type': term_type,
            'in_filter': in_filter,
            'heraldic_score': round(score, 1),
            'total_products': total_products,
            'max_brand_count': max_count,
            'skew': round(skew, 2),
            'cv': round(cv, 2),
            'cap_rate': round(cap_rate, 3),
            'nike': per_brand_counts['nike'],
            'adidas': per_brand_counts['adidas'],
            'puma': per_brand_counts['puma'],
            'lulu': per_brand_counts['lulu'],
            'ua': per_brand_counts['ua'],
            'sample_titles': ' || '.join(rec['sample_titles'][:SAMPLE_TITLES_PER_TERM]),
        })

    df = pd.DataFrame(results)
    df = df.sort_values(['in_filter', 'heraldic_score'], ascending=[True, False]).reset_index(drop=True)
    df.to_csv(CANDIDATES_CSV, index=False)

    n_new = int((~df['in_filter']).sum())
    n_in = int(df['in_filter'].sum())
    print(f"\nResults:")
    print(f"  {n_new:,} candidates NOT in heraldic filter (potential additions)")
    print(f"  {n_in:,} candidates ALREADY in heraldic filter (validates coverage)")
    print(f"\nSaved: {CANDIDATES_CSV}")


# ============================================================================
# STAGE 2 — REVIEW
# ============================================================================

def run_review(top_n):
    if not os.path.exists(CANDIDATES_CSV):
        print(f"ERROR: {CANDIDATES_CSV} does not exist. Run `scan` first.")
        return

    df = pd.read_csv(CANDIDATES_CSV)
    df = df[~df['in_filter']].copy()
    df = df.sort_values('heraldic_score', ascending=False).reset_index(drop=True)

    decisions = {}
    if os.path.exists(DECISIONS_JSON):
        with open(DECISIONS_JSON, 'r', encoding='utf-8') as f:
            decisions = json.load(f)

    candidates = df.head(top_n)
    pending = [row for _, row in candidates.iterrows() if str(row['term']) not in decisions]

    print("=" * 70)
    print(f"STAGE 2: REVIEW  (top {top_n} by score, {len(pending)} pending)")
    print("=" * 70)
    print("Commands:  [y] add  |  [n] skip  |  [s] more samples  |  [q] quit & save")

    if not pending:
        print("\nNo pending candidates — all top entries already decided.")
        _emit_accepted_terms(decisions)
        return

    for i, row in enumerate(pending, 1):
        term = str(row['term'])
        sample_titles = (
            row['sample_titles'].split(' || ')
            if isinstance(row['sample_titles'], str) and row['sample_titles']
            else []
        )

        shown_extra = False
        while True:
            print()
            print("-" * 70)
            print(f"[{i}/{len(pending)}]  '{term}'  ({row['type']})")
            print(f"  score: {row['heraldic_score']:>10,.0f}    total: {row['total_products']:>6,}    "
                  f"max_brand: {row['max_brand_count']:>5,}    skew: {row['skew']:.2f}    "
                  f"cv: {row['cv']:.2f}    cap_rate: {row['cap_rate']:.0%}")
            print(f"  per-brand: nike={row['nike']:,}  adidas={row['adidas']:,}  "
                  f"puma={row['puma']:,}  lulu={row['lulu']:,}  ua={row['ua']:,}")
            print(f"  samples:")
            limit = len(sample_titles) if shown_extra else REVIEW_SAMPLES_FIRST_PASS
            for s in sample_titles[:limit]:
                print(f"    {s[:140]}")

            try:
                choice = input("\n  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n\nInterrupted — saving and exiting.")
                _save_decisions(decisions)
                _emit_accepted_terms(decisions)
                return

            if choice == 'y':
                decisions[term] = 'accept'
                _save_decisions(decisions)
                print("  ✓ accepted")
                break
            elif choice == 'n':
                decisions[term] = 'skip'
                _save_decisions(decisions)
                print("  ✗ skipped")
                break
            elif choice == 's':
                shown_extra = True
                continue
            elif choice == 'q':
                _save_decisions(decisions)
                _emit_accepted_terms(decisions)
                print(f"\nResume any time:  python discover_heraldic_keywords.py review --top {top_n}")
                return
            else:
                print("  unknown command — use y/n/s/q")

    _save_decisions(decisions)
    _emit_accepted_terms(decisions)
    print(f"\nReview complete.")


def _save_decisions(decisions):
    with open(DECISIONS_JSON, 'w', encoding='utf-8') as f:
        json.dump(decisions, f, indent=2, ensure_ascii=False)


def _emit_accepted_terms(decisions):
    accepted = sorted([t for t, d in decisions.items() if d == 'accept'])
    n_skipped = sum(1 for d in decisions.values() if d == 'skip')
    with open(NEW_TERMS_FILE, 'w', encoding='utf-8') as f:
        f.write("# Accepted heraldic additions — paste into heraldic_filter.py\n")
        f.write("# Generated by discover_heraldic_keywords.py\n\n")
        for term in accepted:
            f.write(f'    "{term}",\n')
    print(f"\n  accepted: {len(accepted)}    skipped: {n_skipped}")
    print(f"  new terms file: {NEW_TERMS_FILE}")


# ============================================================================
# STAGE 3 — SANITY CHECK
# ============================================================================

def run_sanity():
    decisions = {}
    if os.path.exists(DECISIONS_JSON):
        with open(DECISIONS_JSON, 'r', encoding='utf-8') as f:
            decisions = json.load(f)
    accepted = [t for t, d in decisions.items() if d == 'accept']

    print("=" * 70)
    print("STAGE 3: SANITY CHECK")
    print("=" * 70)
    print(f"\nAccepted additions: {len(accepted)}")
    if not accepted:
        print("(no accepted terms — measuring current filter coverage only)")

    current_pattern = build_postgres_filter_regex(HERALDIC_KEYWORDS)
    expanded_pattern = build_postgres_filter_regex(HERALDIC_KEYWORDS + accepted)

    print()
    print(f"  {'Brand':<10} {'Catalog':>10} {'Current':>11} {'(% cat)':>9}   "
          f"{'Expanded':>11} {'(% cat)':>9}   {'Δ new':>8}")
    print("  " + "-" * 78)

    total_catalog = 0
    total_current = 0
    total_expanded = 0

    rows_per_brand = []

    for brand in BRANDS:
        try:
            conn, cur = db.connect_to_db(brand)
            cur.execute(TOTAL_PRODUCTS_QUERY)
            n_total = cur.fetchone()[0]
            cur.execute(COUNT_REGEX_QUERY, (current_pattern, current_pattern))
            n_current = cur.fetchone()[0]
            if accepted:
                cur.execute(COUNT_REGEX_QUERY, (expanded_pattern, expanded_pattern))
                n_expanded = cur.fetchone()[0]
            else:
                n_expanded = n_current
            conn.close()
        except Exception as e:
            print(f"  {brand:<10} ERROR  {e}")
            continue

        delta = n_expanded - n_current
        pct_current = n_current / n_total if n_total else 0
        pct_expanded = n_expanded / n_total if n_total else 0
        print(f"  {brand:<10} {n_total:>10,} {n_current:>11,} {pct_current:>8.2%}   "
              f"{n_expanded:>11,} {pct_expanded:>8.2%}   {delta:>8,}")
        rows_per_brand.append((brand, n_total, n_current, n_expanded, delta))
        total_catalog += n_total
        total_current += n_current
        total_expanded += n_expanded

    print("  " + "-" * 78)
    delta_total = total_expanded - total_current
    if total_catalog:
        print(f"  {'TOTAL':<10} {total_catalog:>10,} {total_current:>11,} {total_current/total_catalog:>8.2%}   "
              f"{total_expanded:>11,} {total_expanded/total_catalog:>8.2%}   {delta_total:>8,}")
        delta_pct = delta_total / total_catalog
        print()
        print(f"  Current filter excludes  {total_current:,} / {total_catalog:,}  "
              f"({total_current/total_catalog:.2%} of catalog)")
        print(f"  Expanded filter excludes {total_expanded:,} / {total_catalog:,}  "
              f"({total_expanded/total_catalog:.2%} of catalog)")
        if accepted:
            print(f"  Δ from {len(accepted)} accepted additions: "
                  f"{delta_total:,} products ({delta_pct:.3%} of catalog)")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(SANITY_REPORT, 'w', encoding='utf-8') as f:
        f.write("Heraldic filter sanity check\n")
        f.write("=" * 60 + "\n")
        f.write(f"Filter entries: {len(HERALDIC_KEYWORDS)} current, "
                f"{len(HERALDIC_KEYWORDS) + len(accepted)} expanded\n")
        f.write(f"Accepted additions: {len(accepted)}\n\n")
        f.write(f"{'Brand':<10} {'Catalog':>10} {'Current':>11} {'Expanded':>11} {'Delta':>8}\n")
        f.write("-" * 55 + "\n")
        for brand, n_t, n_c, n_e, d in rows_per_brand:
            f.write(f"{brand:<10} {n_t:>10,} {n_c:>11,} {n_e:>11,} {d:>8,}\n")
        f.write("-" * 55 + "\n")
        f.write(f"{'TOTAL':<10} {total_catalog:>10,} {total_current:>11,} "
                f"{total_expanded:>11,} {delta_total:>8,}\n\n")
        if total_catalog:
            f.write(f"Current filter excludes  {total_current/total_catalog:.4%} of catalog\n")
            f.write(f"Expanded filter excludes {total_expanded/total_catalog:.4%} of catalog\n")
            if accepted:
                f.write(f"Delta from accepted: {delta_total:,} products "
                        f"({delta_total/total_catalog:.4%} of catalog)\n")
        if accepted:
            f.write(f"\nAccepted terms:\n")
            for term in sorted(accepted):
                f.write(f"  {term}\n")

    print(f"\n  Report saved: {SANITY_REPORT}")


# ============================================================================
# CLI
# ============================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Heraldic filter audit tool — three-stage discovery, review, sanity check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Stages run independently and persist their outputs:
  scan    — outputs candidates.csv
  review  — reads candidates.csv, prompts for each candidate, persists decisions.json
  sanity  — reads decisions.json, queries DB, reports filter impact

Re-running `review` resumes from where the last session left off.
""",
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('scan', help='Stage 1: scan catalog, output candidates.csv')

    rp = sub.add_parser('review', help='Stage 2: interactive review of top candidates')
    rp.add_argument('--top', type=int, default=100,
                    help='Number of top-ranked candidates to review (default 100)')

    sub.add_parser('sanity', help='Stage 3: quantify filter impact of accepted additions')

    ap = sub.add_parser('all', help='Run scan -> review -> sanity in sequence')
    ap.add_argument('--top', type=int, default=100)

    args = parser.parse_args(argv)

    if args.cmd == 'scan':
        run_scan()
    elif args.cmd == 'review':
        run_review(args.top)
    elif args.cmd == 'sanity':
        run_sanity()
    elif args.cmd == 'all':
        run_scan()
        run_review(args.top)
        run_sanity()


if __name__ == '__main__':
    main()
