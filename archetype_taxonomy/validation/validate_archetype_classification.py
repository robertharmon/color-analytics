"""
ENTRY - run: python cli.py validate-archetypes  (score archetype accuracy from a labeled export).

Archetype Classification Accuracy
=================================

Reads the archetype labeling tool's exported results CSV and reports how well the
algorithm's archetype classification matches human ground truth: overall accuracy
with a Wilson 95% CI, a 5-class confusion matrix, per-class precision/recall/F1,
per-brand accuracy, and a high-confidence-only cut.

This is the archetype-side counterpart to tune-merge-rules (the merge-side scorer):
one labeling pass in archetype-review produces BOTH a merge CSV and this results CSV.

READ-ONLY: reads one CSV, writes a report. No database, no pipeline state touched.

Label resolution (from the export's human_classification column):
  - an archetype code (MONO/DOM_ACC/...)  -> the human's true label
  - 'CORRECT'                              -> true == the algorithm's label
  - 'UNCERTAIN'                            -> excluded from accuracy; reported separately
  - blank / unrecognized                   -> excluded as 'unusable'; listed

Input CSV columns used: original_classification (the algorithm's archetype),
human_classification, brand, confidence, is_correct (cross-checked), strata_mode
(optional — a boundary sample relabels the headline and suppresses the real-world estimate).

Usage:
    python cli.py validate-archetypes
    python cli.py validate-archetypes --input path/to/validation_results_hsb.csv
"""

import os
import sys
import math
import argparse

import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT_CSV = os.path.join(_SCRIPT_DIR, 'outputs', 'validation_results_hsb.csv')
DEFAULT_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, 'outputs')

# Canonical order — hand-rolled matrix uses this so classes never reorder or drop.
ARCHETYPES = ['MONO', 'DOM_ACC', 'DUAL_BAL', 'MULTI_DOM', 'MULTI_BAL']


# ---------------------------------------------------------------------------
# CI helpers (ported from data_prep/quality/validate_heraldic_filter.py)
# ---------------------------------------------------------------------------

def _wilson_ci(k, n, z=1.96):
    """Wilson score 95% CI for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _ratio_with_ci(k, n):
    if n == 0:
        return (0.0, 0.0, 0.0)
    return (k / n, *_wilson_ci(k, n))


# ---------------------------------------------------------------------------
# Label resolution
# ---------------------------------------------------------------------------

def resolve_labels(df):
    """Split rows into resolved / uncertain / unusable and attach a true label.

    Returns (resolved_df, n_uncertain, unusable_values) where resolved_df has
    'predicted' and 'true' columns.
    """
    df = df.copy()
    df['predicted'] = df['original_classification'].astype(str).str.strip()
    hc = df['human_classification'].astype(str).str.strip()

    resolved, uncertain, unusable_vals = [], 0, []
    for i, row in df.iterrows():
        h = str(row['human_classification']).strip()
        pred = str(row['original_classification']).strip()
        if h == 'CORRECT':
            true = pred
        elif h in ARCHETYPES:
            true = h
        elif h == 'UNCERTAIN':
            uncertain += 1
            continue
        else:
            unusable_vals.append(h)
            continue
        r = row.to_dict()
        r['true'] = true
        r['predicted'] = pred
        resolved.append(r)

    resolved_df = pd.DataFrame(resolved)
    return resolved_df, uncertain, unusable_vals


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _fmt_pct_ci(k, n):
    p, lo, hi = _ratio_with_ci(k, n)
    return f"{p:.1%} (95% CI [{lo:.1%}, {hi:.1%}])"


def print_report(df, resolved, n_uncertain, unusable, out_lines):
    def emit(s=""):
        print(s)
        out_lines.append(s)

    n_exported = len(df)
    n_resolved = len(resolved)
    n_unusable = len(unusable)

    strata_modes = set()
    if 'strata_mode' in df.columns:
        strata_modes = set(str(m) for m in df['strata_mode'].dropna().unique())
    is_boundary = bool(strata_modes - {'archetype'})

    emit("=" * 70)
    emit("ARCHETYPE CLASSIFICATION ACCURACY")
    emit("=" * 70)
    if is_boundary:
        emit("*** BOUNDARY-REFINEMENT SAMPLE (strata_mode=%s) ***" % sorted(strata_modes))
        emit("These labels came from a boundary-oversampled set. The number below is")
        emit("a BOUNDARY-REFINEMENT accuracy, NOT a real-world estimate - do not quote it.")
    emit(f"\nRows: exported={n_exported}  resolved={n_resolved}  "
         f"uncertain={n_uncertain}  unusable={n_unusable}")
    if n_resolved == 0:
        emit("\nNo resolved rows - nothing to score.")
        return

    # Overall accuracy
    correct = int((resolved['true'] == resolved['predicted']).sum())
    label = "Boundary-refinement accuracy" if is_boundary else "Overall accuracy"
    emit(f"\n{label}: {_fmt_pct_ci(correct, n_resolved)}   ({correct}/{n_resolved})")

    # is_correct cross-check
    if 'is_correct' in resolved.columns:
        csv_correct = resolved['is_correct'].astype(str).str.lower().isin(['true', '1'])
        derived_correct = (resolved['true'] == resolved['predicted'])
        mismatch = int((csv_correct.values != derived_correct.values).sum())
        if mismatch:
            emit(f"  WARNING: {mismatch} rows where the CSV's is_correct disagrees with "
                 "resolved true==predicted (stale or hand-edited export?).")

    # Confusion matrix (rows = true/human, cols = predicted/algorithm)
    emit("\nConfusion matrix  (rows = TRUE/human, cols = PREDICTED/algorithm):")
    header = "  true \\ pred |" + "".join(f"{a:>10}" for a in ARCHETYPES) + f"{'total':>10}"
    emit(header)
    emit("  " + "-" * (len(header) - 2))
    cm = {t: {p: 0 for p in ARCHETYPES} for t in ARCHETYPES}
    for _, r in resolved.iterrows():
        if r['true'] in cm and r['predicted'] in cm[r['true']]:
            cm[r['true']][r['predicted']] += 1
    for t in ARCHETYPES:
        row_total = sum(cm[t].values())
        emit(f"  {t:>11} |" + "".join(f"{cm[t][p]:>10}" for p in ARCHETYPES) + f"{row_total:>10}")

    # Per-class precision / recall / F1
    emit("\nPer-class (support = # true):")
    emit(f"  {'class':>10}  {'support':>7}  {'recall':>22}  {'precision':>22}  {'F1':>6}")
    f1s = []
    for a in ARCHETYPES:
        support = sum(cm[a].values())                        # true == a
        tp = cm[a][a]
        pred_a = sum(cm[t][a] for t in ARCHETYPES)           # predicted == a
        rec_p, rec_lo, rec_hi = _ratio_with_ci(tp, support)
        prec_p, prec_lo, prec_hi = _ratio_with_ci(tp, pred_a)
        f1 = (2 * prec_p * rec_p / (prec_p + rec_p)) if (prec_p + rec_p) else 0.0
        f1s.append(f1)
        emit(f"  {a:>10}  {support:>7}  "
             f"{rec_p:>6.1%} [{rec_lo:.0%},{rec_hi:.0%}]   "
             f"{prec_p:>6.1%} [{prec_lo:.0%},{prec_hi:.0%}]   {f1:>5.1%}")
    emit(f"  macro-F1: {sum(f1s)/len(f1s):.1%}")

    # Per-brand
    if 'brand' in resolved.columns:
        emit("\nPer-brand accuracy:")
        for brand, grp in resolved.groupby('brand'):
            k = int((grp['true'] == grp['predicted']).sum())
            emit(f"  {str(brand):>8}: {_fmt_pct_ci(k, len(grp))}   ({k}/{len(grp)})")

    # High-confidence cut + confidence distribution
    if 'confidence' in resolved.columns:
        emit("\nConfidence:")
        dist = resolved['confidence'].value_counts().to_dict()
        emit("  distribution: " + ", ".join(f"{k}={v}" for k, v in dist.items()))
        hi = resolved[resolved['confidence'].astype(str).str.lower() == 'high']
        if len(hi):
            k = int((hi['true'] == hi['predicted']).sum())
            emit(f"  high-confidence accuracy: {_fmt_pct_ci(k, len(hi))}   ({k}/{len(hi)})")

    # Uncertain breakdown by predicted class
    if n_uncertain:
        emit(f"\nUNCERTAIN rows: {n_uncertain} (excluded from accuracy).")

    # Unusable listing
    if unusable:
        from collections import Counter
        emit("\nUnusable human_classification values (excluded): "
             + ", ".join(f"{v!r} x{c}" for v, c in Counter(unusable).items()))

    # Caveats
    emit("\nCaveats:")
    emit("  - The labeler saw the algorithm's answer (CORRECT is offered first), so")
    emit("    the diagonal is anchoring-inflated vs blind labeling.")
    if is_boundary:
        emit("  - Boundary sample: the real-world accuracy estimate is intentionally suppressed.")
    emit("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Score archetype classification accuracy from a labeled export (READ-ONLY)")
    parser.add_argument('--input', default=DEFAULT_INPUT_CSV,
                        help="Path to the exported validation_results CSV")
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_DIR)
    args, _ = parser.parse_known_args()

    if not os.path.exists(args.input):
        print(f"ERROR: results CSV not found: {args.input}")
        print("Label products in the archetype-review tool, click Export CSV, and save it here")
        print(f"(or pass --input). Expected default: {DEFAULT_INPUT_CSV}")
        sys.exit(1)

    df = pd.read_csv(args.input)
    required = {'original_classification', 'human_classification'}
    missing = required - set(df.columns)
    if missing:
        print(f"ERROR: CSV missing columns: {missing}")
        sys.exit(1)

    resolved, n_uncertain, unusable = resolve_labels(df)

    out_lines = []
    print_report(df, resolved, n_uncertain, unusable, out_lines)

    os.makedirs(args.output_dir, exist_ok=True)
    report_path = os.path.join(args.output_dir, 'archetype_accuracy.txt')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"\n  Saved report: {report_path}")


if __name__ == '__main__':
    main()
