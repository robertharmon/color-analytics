"""
ENTRY - run: python cli.py archetype-review  (one idempotent command: classify -> sample -> render the labeling tool).

Archetype / Merge Labeling Workflow
===================================

One command that builds the interactive labeling tool a human uses to validate
archetype classification AND label merge groups (both captured in one pass).
Replaces the three-step archetypes -> archetype-validation-sample ->
archetype-validation dance with a single idempotent entry point:

  1. Classify  - run `archetypes` only if all_products_classified_hsb.csv is
                 missing (or --force-classify). This is the algorithm's guess.
  2. Sample    - draw (or REUSE) the stratified validation sample. Reuse is the
                 default: an existing sample is kept unless --resample, so a
                 rebuild never silently redraws the sample under your hand-labels.
                 If the existing sample was drawn with DIFFERENT parameters, the
                 command refuses and tells you to pass --resample explicitly.
  3. Render    - always regenerate the HTML tool (cheap, pure) so code changes
                 take effect without touching the sample.

READ-ONLY with respect to the pipeline: classify/sample do SELECT-only DB reads;
nothing here edits tunable constants, cluster data, zones, or the database.

Brand-optional: pass a brand to scope the sample/tool/exports to one brand;
omit it for the global all-brands sample.

Usage:
    python cli.py archetype-review
    python cli.py archetype-review nike
    python cli.py archetype-review --resample
    python cli.py archetype-review --strata pair-features   # boundary sample for merge tuning
    python cli.py archetype-review --force-classify --mono 200
"""

import os
import sys
import json
import shutil
import argparse
from datetime import datetime

from archetype_taxonomy import classify_archetypes
from archetype_taxonomy.validation.build_archetype_validation_sample import (
    build_validation_sample,
    sample_filename,
    DEFAULT_ARCHETYPE_TARGETS,
    DEFAULT_INPUT_CSV,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_IMAGES_DIR,
)
from archetype_taxonomy.validation.build_archetype_validator import build_validator_html

# The classifier's master CSV (produced by `archetypes`) — the sample builder's input.
CLASSIFIED_CSV = DEFAULT_INPUT_CSV


def _load_metadata(json_path):
    """Return the metadata dict of an existing sample JSON, or None."""
    try:
        with open(json_path, encoding='utf-8') as f:
            return json.load(f).get('metadata', {})
    except (OSError, ValueError):
        return None


def _params_match(meta, strata, random_seed, archetype_targets):
    """True if an existing sample's metadata matches the requested parameters."""
    if meta.get('strata_mode', 'archetype') != strata:
        return False, f"strata_mode {meta.get('strata_mode')!r} != requested {strata!r}"
    if int(meta.get('random_seed', -1)) != int(random_seed):
        return False, f"random_seed {meta.get('random_seed')} != requested {random_seed}"
    if strata == 'archetype':
        existing = meta.get('archetype_targets', {})
        if existing != archetype_targets:
            return False, f"archetype_targets {existing} != requested {archetype_targets}"
    return True, ""


def main(brand=None, argv=None):
    parser = argparse.ArgumentParser(
        description="Classify (if needed) + sample + render the archetype/merge labeling tool"
    )
    parser.add_argument('--strata', choices=['archetype', 'pair-features'], default='archetype',
                        help="Sampling strategy (default: archetype). pair-features is a "
                             "boundary-refinement sample for merge tuning — NOT a real-world accuracy set.")
    parser.add_argument('--resample', action='store_true',
                        help="Redraw the sample even if one exists (orphans existing hand-labels).")
    parser.add_argument('--force-classify', action='store_true',
                        help="Re-run classification even if the classified CSV exists.")
    parser.add_argument('--random-seed', type=int, default=42)
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_DIR)
    parser.add_argument('--images-dir', default=DEFAULT_IMAGES_DIR)
    parser.add_argument('--host-images-dir', default=None)
    parser.add_argument('--open', action='store_true', help="Open the tool in a browser when done.")

    # Archetype target overrides
    parser.add_argument('--mono', type=int, default=DEFAULT_ARCHETYPE_TARGETS['MONO'])
    parser.add_argument('--dom-acc', type=int, default=DEFAULT_ARCHETYPE_TARGETS['DOM_ACC'])
    parser.add_argument('--dual-bal', type=int, default=DEFAULT_ARCHETYPE_TARGETS['DUAL_BAL'])
    parser.add_argument('--multi-dom', type=int, default=DEFAULT_ARCHETYPE_TARGETS['MULTI_DOM'])
    parser.add_argument('--multi-bal', type=int, default=DEFAULT_ARCHETYPE_TARGETS['MULTI_BAL'])

    # Pair-features overrides (Phase 5)
    parser.add_argument('--candidate-pool', type=int, default=5000)
    parser.add_argument('--boundary-band', type=float, default=None)

    args = parser.parse_args(argv)

    archetype_targets = {
        'MONO': args.mono, 'DOM_ACC': args.dom_acc, 'DUAL_BAL': args.dual_bal,
        'MULTI_DOM': args.multi_dom, 'MULTI_BAL': args.multi_bal,
    }

    print("=" * 70)
    print("ARCHETYPE / MERGE LABELING WORKFLOW")
    print("=" * 70)
    print(f"  Brand:  {brand or '(all brands)'}")
    print(f"  Strata: {args.strata}")

    # ---- Step 1: Classify ----
    print("\n[1/3] Classification")
    if args.force_classify or not os.path.exists(CLASSIFIED_CSV):
        why = "forced" if args.force_classify else "missing"
        print(f"  Running `archetypes` ({why}: {CLASSIFIED_CSV})...")
        classify_archetypes.main()
    else:
        mtime = datetime.fromtimestamp(os.path.getmtime(CLASSIFIED_CSV)).isoformat(timespec='seconds')
        print(f"  Reusing {os.path.basename(CLASSIFIED_CSV)} (mtime {mtime}); --force-classify to rebuild.")

    # ---- Step 2: Sample (reuse / refuse / resample / fresh) ----
    print("\n[2/3] Sampling")
    sample_path = os.path.join(args.output_dir, sample_filename(args.strata, brand))
    meta = _load_metadata(sample_path) if os.path.exists(sample_path) else None

    if meta is not None and not args.resample:
        ok, reason = _params_match(meta, args.strata, args.random_seed, archetype_targets)
        if ok:
            print(f"  Reusing existing sample: {sample_path}")
            print(f"    fingerprint {meta.get('sample_fingerprint')} | {meta.get('total_sampled')} products")
        else:
            print(f"  ERROR: an existing sample was drawn with different parameters:")
            print(f"    {reason}")
            print(f"    File: {sample_path}")
            print("  Re-run with --resample to redraw (existing hand-labels will be orphaned),")
            print("  or drop the overriding flags to reuse the existing sample.")
            sys.exit(2)
    else:
        if meta is not None and args.resample:
            stamp = (meta.get('generated_at') or 'prev').replace(':', '-')
            archived = f"{sample_path}.{stamp}.bak"
            shutil.copy2(sample_path, archived)
            fp = meta.get('sample_fingerprint', '?')
            print(f"  --resample: archived existing sample -> {os.path.basename(archived)}")
            print(f"  WARNING: hand-labels under localStorage keys for fingerprint {fp} will be orphaned.")
        print(f"  Building sample ({args.strata})...")
        sample_path = build_validation_sample(
            input_csv=CLASSIFIED_CSV, output_dir=args.output_dir,
            images_dir=args.images_dir, host_images_dir=args.host_images_dir,
            random_seed=args.random_seed, archetype_targets=archetype_targets,
            brand=brand, strata=args.strata,
            candidate_pool=args.candidate_pool, boundary_band=args.boundary_band,
        )

    # ---- Step 3: Render (always) ----
    print("\n[3/3] Rendering labeling tool")
    html_path = build_validator_html(input_json=sample_path, output_dir=args.output_dir)

    final_meta = _load_metadata(sample_path) or {}
    fp = final_meta.get('sample_fingerprint', '?')
    strata_mode = final_meta.get('strata_mode', args.strata)

    print("\n" + "=" * 70)
    print("  Done.")
    print(f"  Tool:        {html_path}")
    print(f"  Sample:      {sample_path}")
    print(f"  Fingerprint: {fp}")
    print(f"  Strata:      {strata_mode}")
    if strata_mode != 'archetype':
        print("  NOTE: boundary-refinement sample — labels here tune the merge rule but are")
        print("        NOT a real-world accuracy estimate (see --strata pair-features).")
    print("  localStorage keys:")
    print(f"    archetype: archetype_validation_results_v2_{strata_mode}_{fp}")
    print(f"    merge:     archetype_merge_labels_v1_{strata_mode}_{fp}")
    print("  After labeling, export and feed:")
    print("    merge CSV  -> tune-merge-rules --input <merge_labels_*.csv>")
    print("    results CSV -> validate-archetypes --input <validation_results_*.csv>")
    print("=" * 70)

    if args.open:
        import webbrowser
        webbrowser.open('file:///' + os.path.abspath(html_path).replace('\\', '/'))


if __name__ == '__main__':
    main()
