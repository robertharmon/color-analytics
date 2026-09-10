"""
ENTRY - run: python cli.py validate-keyword-map  (measures keyword-map coverage).

validate_keyword_map_coverage.py

Measures how well each brand's BRAND_KEYWORD_MAPS covers product-type
vocabulary in product titles. Mirrors the SQL regex-inversion workflow
that produced the original 98% Nike figure: for each brand, count
products whose `title` and/or `title_second` contains at least one
mapped keyword, against the post-heraldic-filter denominator that
matches runtime behavior.

This validates *vocabulary coverage* (left-hand side of the map). It
does NOT validate the right-hand side (keyword -> Fashionpedia class
routing) or the downstream main-garment bbox selection. Both were
hand-judged from inspecting example products per keyword.

Usage:
    python validate_keyword_map_coverage.py                    # all 5 brands
    python validate_keyword_map_coverage.py --brand nike       # one brand
    python validate_keyword_map_coverage.py --include-heraldic # raw catalog
"""

import argparse
import csv
import os
import re

from shared import db
from data_prep.heraldic_filter import HERALDIC_KEYWORDS
from data_prep.keyword_maps import BRAND_KEYWORD_MAPS

BRANDS = ["nike", "adidas", "puma", "lulu", "ua"]
# Anchor outputs to this module's own dir (data_prep/quality/) rather than the
# caller's CWD, so results always land in the slice's outputs/ regardless of
# where the command is launched from.
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(_HERE, "outputs", "keyword_map_validation")
DEFAULT_MAX_UNCOVERED = 2000


def compile_postgres_pattern(terms):
    """Build a POSIX regex alternation with \\y word boundaries."""
    escaped = sorted({re.escape(t) for t in terms}, key=len, reverse=True)
    return r"\y(" + "|".join(escaped) + r")\y"


def coverage_query(cur, heraldic_pattern, kw_pattern, include_heraldic):
    if include_heraldic:
        cur.execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE COALESCE(title, '') ~* %s)        AS t_match,
                COUNT(*) FILTER (WHERE COALESCE(title_second, '') ~* %s) AS ts_match,
                COUNT(*) FILTER (
                    WHERE COALESCE(title, '') ~* %s
                       OR COALESCE(title_second, '') ~* %s
                ) AS either_match
            FROM instance
            """,
            (kw_pattern, kw_pattern, kw_pattern, kw_pattern),
        )
    else:
        cur.execute(
            """
            WITH base AS (
                SELECT title, title_second
                FROM instance
                WHERE NOT (
                    COALESCE(title, '') ~* %s
                    OR COALESCE(title_second, '') ~* %s
                )
            )
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE COALESCE(title, '') ~* %s)        AS t_match,
                COUNT(*) FILTER (WHERE COALESCE(title_second, '') ~* %s) AS ts_match,
                COUNT(*) FILTER (
                    WHERE COALESCE(title, '') ~* %s
                       OR COALESCE(title_second, '') ~* %s
                ) AS either_match
            FROM base
            """,
            (heraldic_pattern, heraldic_pattern,
             kw_pattern, kw_pattern, kw_pattern, kw_pattern),
        )
    return cur.fetchone()


def uncovered_query(cur, heraldic_pattern, kw_pattern, include_heraldic, limit):
    if include_heraldic:
        cur.execute(
            """
            SELECT instance_id, title, title_second
            FROM instance
            WHERE NOT (
                COALESCE(title, '') ~* %s
                OR COALESCE(title_second, '') ~* %s
            )
            ORDER BY instance_id
            LIMIT %s
            """,
            (kw_pattern, kw_pattern, limit),
        )
    else:
        cur.execute(
            """
            SELECT instance_id, title, title_second
            FROM instance
            WHERE NOT (
                COALESCE(title, '') ~* %s
                OR COALESCE(title_second, '') ~* %s
            )
              AND NOT (
                COALESCE(title, '') ~* %s
                OR COALESCE(title_second, '') ~* %s
            )
            ORDER BY instance_id
            LIMIT %s
            """,
            (heraldic_pattern, heraldic_pattern,
             kw_pattern, kw_pattern, limit),
        )
    return cur.fetchall()


def pct(num, denom):
    return f"{num / denom * 100:.1f}%" if denom else "N/A"


def write_uncovered_csv(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["instance_id", "title", "title_second"])
        for r in rows:
            w.writerow([r[0], r[1] or "", r[2] or ""])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand", choices=BRANDS, default=None,
                        help="Run a single brand. If omitted, runs all five.")
    parser.add_argument("--include-heraldic", action="store_true",
                        help="Include heraldic products in the denominator. "
                             "Default: exclude them (matches runtime behavior).")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help=f"Where to write uncovered CSVs (default: {DEFAULT_OUTPUT_DIR}).")
    parser.add_argument("--max-uncovered", type=int, default=DEFAULT_MAX_UNCOVERED,
                        help=f"Max rows per uncovered CSV (0 = skip CSV). "
                             f"Default: {DEFAULT_MAX_UNCOVERED}.")
    args = parser.parse_args()

    brands = [args.brand] if args.brand else BRANDS
    heraldic_pattern = compile_postgres_pattern(HERALDIC_KEYWORDS)
    denom_label = "raw catalog" if args.include_heraldic else "post-heraldic-filter"

    summary = []
    for brand in brands:
        kw_keys = list(BRAND_KEYWORD_MAPS.get(brand, {}).keys())
        if not kw_keys:
            print(f"\n  {brand.upper()}: no keyword map found, skipping.")
            continue
        kw_pattern = compile_postgres_pattern(kw_keys)

        try:
            conn, cur = db.connect_to_db(brand)
        except Exception as e:
            print(f"\n  {brand.upper()}: connection error: {e}")
            continue

        try:
            total, t_match, ts_match, either_match = coverage_query(
                cur, heraldic_pattern, kw_pattern, args.include_heraldic
            )
            uncovered = total - either_match

            print(f"\n  {brand.upper()}  ({len(kw_keys)} keyword entries)")
            print(f"  {'-' * 64}")
            print(f"  Total ({denom_label}):  {total:>10,}")
            print(f"  Matched by title              : {t_match:>10,}  ({pct(t_match, total)})")
            print(f"  Matched by title_second       : {ts_match:>10,}  ({pct(ts_match, total)})")
            print(f"  Matched by either field       : {either_match:>10,}  ({pct(either_match, total)})")
            print(f"  Uncovered by either field     : {uncovered:>10,}  ({pct(uncovered, total)})")

            if args.max_uncovered > 0:
                rows = uncovered_query(
                    cur, heraldic_pattern, kw_pattern,
                    args.include_heraldic, args.max_uncovered
                )
                csv_path = os.path.join(args.output_dir, f"{brand}_uncovered.csv")
                write_uncovered_csv(rows, csv_path)
                print(f"  Uncovered CSV                 : {csv_path}  ({len(rows):,} rows)")

            summary.append((brand, len(kw_keys), total, either_match, uncovered))
        finally:
            cur.close()
            conn.close()

    if summary:
        print(f"\n{'=' * 72}")
        print(f"  SUMMARY  ({denom_label})")
        print(f"{'=' * 72}")
        print(f"  {'Brand':<8} {'Keys':>6} {'Total':>10} {'Matched':>10} {'Coverage':>10} {'Uncovered':>10}")
        print(f"  {'-' * 8} {'-' * 6} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10}")
        for brand, n_keys, total, matched, uncovered in summary:
            print(f"  {brand:<8} {n_keys:>6} {total:>10,} {matched:>10,} "
                  f"{pct(matched, total):>10} {uncovered:>10,}")


if __name__ == "__main__":
    main()
