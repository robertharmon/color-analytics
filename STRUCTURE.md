# New Repo Folder Structure (FINAL)

Package-by-feature: each top-level folder is a **core function** owning its production-path modules
+ outputs. Production-path steps are **flat files** (no folder per step). Subfolders mark distinct
*concerns* only — QA (`quality/`, `validation/`) and model `weights/`. `data_prep/` and `shared/`
sit outside the deliverables as enablers. Names follow `CONVENTIONS.md`. DB tables + version ids FROZEN.

Rule keeping slices independent: **used by 2+ slices → `shared/`; used within one slice → local to it.**

```
color-analytics/
│
├── data_prep/                     ENABLER — raw images → clustered color data
│   ├── segment.py                 (was segment_fpyolo11l241114.py)   → table segment_fpyolo11l241114 [FROZEN]
│   ├── cluster.py                 (was cluster_kmeans250218.py)      → table cluster_…_kmeans250218 [FROZEN]
│   ├── heraldic_filter.py         (was filter.py)
│   ├── keyword_maps.py            (was segment_fpyolo11l241114_keywordmaps.py)
│   ├── discover_heraldic_keywords.py   (was audit_heraldic_filter.py — BUILDS the filter)
│   ├── weights/                   YOLO model weights (was models/Fashionpedia_YOLO11l_241114/)
│   └── quality/                   validation targeting the FOUNDATION
│       ├── validate_heraldic_filter.py         (kept)
│       ├── validate_keyword_map_coverage.py    (kept)
│       ├── measure_pipeline_attrition.py       (was audit_pipeline_attrition.py)
│       └── check_mask_color_shift.py           (was test_mask_binarization.py)
│
├── palette_explorer/              FLAGSHIP — flat production path + QA concern
│   ├── build_color_clusters.py    ciede2000 clustering → cluster structure  (from the fused old file)
│   ├── analyze_discount.py        per-cluster discount freq/depth + FDR      (from the fused old file)
│   ├── build_zones.py             (was cluster_zone_builder.py — manual)
│   ├── assign_zones.py            (was auto_zone_assign.py — RF)
│   ├── rf_zone_model.pkl          RF model artifact
│   ├── palette_explorer.py        (was visualize_column_preview_discount.py) → palette_explorer.html
│   │                              reads the CSVs; does its OWN zone rollup for all 4 modes
│   │                              (compute_zone_discount_stats / _price_stats / temporal_data stay here)
│   ├── hue_family_grouping.py     local primitive — a*b* row grouping (find_ab_groups)
│   ├── tune_hue_family_rows.py    (was diagnostic_ab_neighbors.py — tunes layout radius R)
│   ├── quality/
│   │   └── review_cluster_quality.py     (was diagnostic_cluster_review.py)
│   └── outputs/
│
├── drift/                         fast screen: has a brand's palette shifted over time? (renamed from detection/)
│   ├── downsample.py              (was downsample_kmeans250514.py)   → table comp_cluster_… [FROZEN]
│   ├── drift.py                   (was drift_monthly.py)             → table driftcolor_… [FROZEN]
│   ├── visualize_drift.py         (was vis_drift.py)                 → drift_comparison.html
│   └── outputs/
│
├── archetype_taxonomy/
│   ├── classify_archetypes.py     (was analyze_archetypes_hue_based_hsb.py; merge logic → shared)
│   ├── visualize_archetype_distribution.py (was …_tables_all.py)     → archetype_distribution.html
│   ├── validation/
│   │   ├── review_archetype_labels.py    ENTRY archetype-review — classify+sample+render (one idempotent cmd)
│   │   ├── validate_archetype_classification.py  ENTRY validate-archetypes — accuracy + 5-class confusion matrix
│   │   ├── build_archetype_validation_sample.py  (advanced: build sample JSON; emits algo_merge_groups)
│   │   └── build_archetype_validator.py  (advanced: render the labeling tool — archetype + merge labels in one page)
│   └── outputs/
│
├── coverage/
│   ├── analyze_coverage.py        (compute half of the old combined file)
│   ├── visualize_coverage.py      (render half)                      → coverage_distribution.html
│   └── outputs/
│
├── shared/                        used by 2+ slices
│   ├── db.py                      (was utils.py — connect_to_db + archive/unprocessed helpers)
│   ├── hsb.py                     (was hsb_utils.py)                 [archetype, coverage, zones]
│   ├── ciede2000.py               (was ciede2000_utils.py)           [palette_explorer, drift]
│   └── palette_merge/
│       ├── palette_merge.py       (extracted merge_clusters_hue_based + threshold constants)
│       └── tune_merge_rules.py    (was analyze_merge_rule_discovery.py)
│
├── cli.py                         (merges main.py + production/main.py — subcommands per stage/deliverable)
├── documentation/                 session docs + DECISION_LOG (carries over)
├── CONVENTIONS.md · STRUCTURE.md · README.md
├── requirements.txt · Dockerfile · docker-compose.yml · .gitignore (incl. `*/outputs/`, `images/`)
└── images/                        gitignored data store: raw + segmented images
```

## Key facts baked in
- The old fused `analyze_ciede2000_agglomerative_discount.py` splits into `build_color_clusters.py`
  (structure) + `analyze_discount.py` (per-cluster FDR); the retired bar-rendering is dropped.
- The explorer computes its 4-mode heatmap data ITSELF from the CSVs (zone rollup stays in-explorer).
- `hue_family_grouping` is LOCAL to palette_explorer (explorer + its tuner only).
- `palette_merge` is SHARED (clustering + archetype classifier import it); its tuner rides with it.
- Model weights live next to their only consumer (YOLO→data_prep, RF→palette_explorer).

## NOT in this repo
- **Museum repo** (separate, runnable + frozen renders): forcedirected explorer, Set-A LAB timeseries,
  theme_spread, ciede2000 benchmark, all of `archive/`.
- **Retired** (deleted): gallery/MDS/treemap + bar renders, all writeups, count_records, base
  column_preview, comp_lab_3d + vis_LABdistribution, LAB-timeseries all/top100/non-HSB, saturated
  archetype table, WebViewer mirror.
- **Data stores** (`images/`, DB): gitignored / external.

## Settled defaults (revisit at migration if needed)
- Enabler folder = `data_prep/`.
- One root `cli.py` (not per-slice entry points).
- QA groupings keep their subfolders (`quality/`, `validation/`).
