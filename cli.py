#!/usr/bin/env python3
"""
color-analytics — unified command-line entry point.

Merges the old root `main.py` (pipeline stages) and `production/main.py`
(analysis/visualization scripts) into one dispatcher.

Usage:
    python cli.py list                         # show all commands
    python cli.py <command> [brand] [--gender G] [extra args...]
    python cli.py all                          # run the safe multi-brand deliverables

Examples:
    python cli.py segment nike                 # foundation: YOLO segmentation
    python cli.py extract-colors nike          # foundation: LAB k-means
    python cli.py consolidate-colors nike --gender mens
    python cli.py palette-explorer             # build palette_explorer.html
    python cli.py archetypes                   # classify + distribution CSVs

Brands: nike, adidas, puma, lulu, ua

The command dispatcher inspects each target `main()`/entry function: it passes
`brand`/`gender` when the signature accepts them, and always exposes any extra
args via sys.argv so scripts that parse their own arguments keep working.
"""

import ast
import sys
import inspect
import importlib
import importlib.util

BRANDS = ["nike", "adidas", "puma", "lulu", "ua"]

# command -> (dotted "module:function", help, group, brand_required)
REGISTRY = {
    # --- Foundation (data_prep): raw images -> clustered color data ---
    'segment':        ('data_prep.segment:run_segmentation', 'Segment garments from product images (YOLO)', 'Foundation', True),
    'extract-colors': ('data_prep.cluster:run_clustering', 'Extract 5 dominant LAB colors per garment (k-means)', 'Foundation', True),

    # --- Foundation quality / filter maintenance ---
    'discover-heraldic-keywords': ('data_prep.discover_heraldic_keywords:main', 'Surface candidate heraldic keywords from titles', 'Foundation QA', False),
    'validate-heraldic-filter':   ('data_prep.quality.validate_heraldic_filter:main', 'Measure heraldic-filter precision/recall', 'Foundation QA', False),
    'validate-keyword-map':       ('data_prep.quality.validate_keyword_map_coverage:main', 'Measure keyword-map coverage', 'Foundation QA', False),
    'measure-attrition':          ('data_prep.quality.measure_pipeline_attrition:main', 'Per-brand/gender pipeline attrition audit', 'Foundation QA', False),
    'check-mask-shift':           ('data_prep.quality.check_mask_color_shift:main', 'Segmented-mask color-shift regression check', 'Foundation QA', False),

    # --- Drift: has a brand's color palette shifted over time? ---
    'downsample':      ('drift.downsample:run_downsample', 'Downsample clusters to 1900/archive (weighted k-means)', 'Drift', True),
    'compute-drift':   ('drift.drift:run_drift', 'Sinkhorn drift distance between archives', 'Drift', True),
    'visualize-drift': ('drift.visualize_drift:main', 'Multi-brand drift comparison chart -> drift_comparison.html', 'Drift', False),

    # --- Coverage ---
    'visualize-coverage': ('coverage.visualize_coverage:main', 'Coverage distribution grid -> coverage_distribution.html', 'Coverage', False),

    # --- Archetype taxonomy ---
    'archetypes':               ('archetype_taxonomy.classify_archetypes:main', 'Classify products into 5 archetypes -> CSVs', 'Archetype', False),
    'archetype-distribution':   ('archetype_taxonomy.visualize_archetype_distribution:main', 'Archetype distribution tables -> archetype_distribution.html', 'Archetype', False),
    'archetype-review':         ('archetype_taxonomy.validation.review_archetype_labels:main', 'Classify (if needed) + sample + render the archetype/merge labeling tool', 'Archetype', False),
    'validate-archetypes':      ('archetype_taxonomy.validation.validate_archetype_classification:main', 'Score archetype accuracy + 5-class confusion matrix from labeled exports', 'Archetype', False),
    # Advanced / individual steps (archetype-review runs these for you):
    'archetype-validation-sample': ('archetype_taxonomy.validation.build_archetype_validation_sample:main', 'Advanced: build stratified validation sample JSON only', 'Archetype', False),
    'archetype-validation':        ('archetype_taxonomy.validation.build_archetype_validator:main', 'Advanced: render the labeling tool from an existing sample JSON', 'Archetype', False),

    # --- Palette explorer (flagship) ---
    'consolidate-colors':        ('palette_explorer.consolidate_colors:main', 'Group products into pure color clusters (CIEDE2000) + per-cluster discount FDR', 'Palette Explorer', False),
    'build-zones':               ('palette_explorer.build_zones:main', 'Manual zone builder -> cluster_zone_builder.html', 'Palette Explorer', False),
    'assign-zones':              ('palette_explorer.assign_zones:main', 'Propagate zones to other brands (RF)', 'Palette Explorer', False),
    'palette-explorer':          ('palette_explorer.palette_explorer:main', 'Build the flagship -> palette_explorer.html', 'Palette Explorer', False),
    'tune-hue-family-rows':      ('palette_explorer.tune_hue_family_rows:main', 'Calibrate the a*b* hue-family row radius', 'Palette Explorer', False),
    'review-cluster-quality':    ('palette_explorer.quality.review_cluster_quality:main', 'Interactive click-to-flag cluster review', 'Palette Explorer QA', False),

    # --- Shared recalibration ---
    'tune-merge-rules': ('shared.palette_merge.tune_merge_rules:main', 'Discover/tune hue-merge rules from labeled data', 'Shared', False),
}

# Safe multi-brand deliverables for `all` (in dependency order). Pipeline stages
# and per-brand clustering/flagship steps must be run explicitly (they need a
# brand, a GPU, and hours of compute), so they are intentionally excluded.
EXECUTION_ORDER = [
    'archetypes',
    'archetype-distribution',
    'visualize-coverage',
]


def _resolve(dotted):
    module_name, func_name = dotted.split(':')
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def _extract_gender(rest):
    if '--gender' in rest:
        i = rest.index('--gender')
        if i + 1 < len(rest):
            return rest[i + 1]
    return None


def _module_docstring(module_name):
    """Read a module's docstring WITHOUT importing it.

    Importing would execute the module's imports — torch, cupy, PIL — so `--help`
    would die on a machine that lacks the GPU stack. Parsing the source lets help
    work on any checkout.
    """
    spec = importlib.util.find_spec(module_name)
    if spec is None or not spec.origin:
        return None
    with open(spec.origin, encoding='utf-8') as f:
        return ast.get_docstring(ast.parse(f.read()))


def _safe_print(text):
    """print() that survives a cp1252 Windows console.

    Several docstrings contain characters the default Windows codepage can't encode
    (ΔE, ★, —). Printing them raw raises UnicodeEncodeError, so `--help` would die on
    exactly the docs it was asked to show.
    """
    enc = sys.stdout.encoding or 'utf-8'
    print(text.encode(enc, errors='replace').decode(enc, errors='replace'))


def _print_command_help(command):
    """Print the target module's docstring as that command's help, and run nothing.

    Not every target uses argparse. Passing --help through to those would land in
    sys.argv unread and the script would start doing real work (hitting the DB,
    reading CSVs) instead of printing help. So --help is answered here, uniformly,
    for all commands: every module docstring carries a `Usage:` block with its flags.
    """
    dotted, help_text, _group, brand_required = REGISTRY[command]
    module_name = dotted.split(':')[0]

    _safe_print(f"cli.py {command} - {help_text}\n")
    if brand_required:
        _safe_print(f"Requires a brand: {', '.join(BRANDS)}\n")

    doc = _module_docstring(module_name)
    _safe_print(doc or f"(no module docstring in {module_name})")


def _dispatch(command, rest):
    """Import the target and call it, adapting to its signature."""
    dotted, _help, _group, brand_required = REGISTRY[command]

    if any(a in ('-h', '--help') for a in rest):
        _print_command_help(command)
        return

    func = _resolve(dotted)

    # Expose extra args to scripts that parse their own argv.
    sys.argv = [f'cli.py {command}'] + rest

    params = inspect.signature(func).parameters
    brand = next((a for a in rest if not a.startswith('-')), None)

    if brand_required and brand not in BRANDS:
        print(f"Command '{command}' requires a brand argument: {', '.join(BRANDS)}")
        sys.exit(2)

    kwargs = {}
    if 'brand' in params:
        kwargs['brand'] = brand
    if 'gender' in params:
        kwargs['gender'] = _extract_gender(rest)

    func(**kwargs)


# Display order for `list`. Numbered rows are the pipeline stages a fresh
# reader should follow top-to-bottom; blank-numbered rows are auxiliary
# (optional QA + shared recalibration). The one-liner says what each slice is for.
GROUP_ORDER = [
    ('1', 'Foundation',          'raw product images -> clustered LAB color data (run these first)'),
    ('',  'Foundation QA',       'optional filter / segmentation quality checks'),
    ('2', 'Drift',               "has a brand's color palette shifted over time?"),
    ('3', 'Coverage',            'how colorful / saturated is each brand overall?'),
    ('4', 'Archetype',           "classify each product's 5-color palette into 5 archetypes"),
    ('5', 'Palette Explorer',    'the flagship interactive deliverable  <- start here'),
    ('',  'Palette Explorer QA', 'optional cluster-quality checks'),
    ('',  'Shared',              'recalibrate the shared hue-merge thresholds'),
]
FLAGSHIP_CMD = 'palette-explorer'


def print_list():
    print("color-analytics commands\n" + "=" * 66)
    print("Foundation feeds four independent analyses. Per brand, run `segment`")
    print("then `extract-colors`, then any analysis below. (* = flagship deliverable)")
    groups = {}
    for cmd, (_d, help_text, group, _b) in REGISTRY.items():
        groups.setdefault(group, []).append((cmd, help_text))
    for num, group, purpose in GROUP_ORDER:
        if group not in groups:
            continue
        head = f"{num}. {group}" if num else f"   {group}"
        print(f"\n{head} - {purpose}")
        for cmd, help_text in sorted(groups[group]):
            star = '*' if cmd == FLAGSHIP_CMD else ' '
            print(f"   {star} {cmd:<28} {help_text}")
    print(f"\n   Meta")
    print(f"     {'list':<28} Show this list")
    print(f"     {'all':<28} Run safe multi-brand deliverables: {', '.join(EXECUTION_ORDER)}")


def run_all():
    for i, command in enumerate(EXECUTION_ORDER, 1):
        print(f"\n[{i}/{len(EXECUTION_ORDER)}] {command}")
        print("-" * 60)
        _dispatch(command, [])


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help', 'list'):
        print_list()
        return

    command = sys.argv[1]
    rest = sys.argv[2:]

    if command == 'all':
        if any(a in ('-h', '--help') for a in rest):
            print("cli.py all - run the safe multi-brand deliverables, in order:\n")
            for c in EXECUTION_ORDER:
                print(f"  {c:<26} {REGISTRY[c][1]}")
            print("\nPipeline stages and per-brand steps are excluded: they need a brand,")
            print("a GPU, and hours of compute. Run those explicitly.")
            return
        run_all()
        return

    if command not in REGISTRY:
        print(f"Unknown command: {command}\n")
        print_list()
        sys.exit(2)

    _dispatch(command, rest)


if __name__ == '__main__':
    main()
