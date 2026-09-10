# Naming & Code Conventions

Rubric for the rebuilt repo. The goal: a newcomer infers what a file/function does from its
name alone, and the same concept is always called the same thing across code, outputs, and docs.

Naming is a **rule you apply**, not a judgment you relitigate per file. When adding or renaming
anything, resolve it against this doc.

Grounded in: Martin, *Clean Code* ch.2 (Meaningful Names) · Ousterhout, *A Philosophy of
Software Design* ch.14 (Choosing Names) · Evans, *Domain-Driven Design* (ubiquitous language).

---

## 0. The one hard rule: never rename a contract

Some names are **contracts** shared with the database and the scraper codebase. They are
deliberately versioned encodings, not human-facing labels. Renaming them breaks data that is
expensive or impossible to reconstruct.

**FROZEN — do not rename, ever:**
- DB table names: `segment_fpyolo11l241114`, `cluster_fpyolo11l241114_kmeans250218`,
  `comp_cluster_fpyolo11l241114_kmeans250218`, `driftcolor_comp_cluster_...`
- Read-only scraper tables: `instance`, `archive`
- Version identifiers embedded in those names: `fpyolo11l241114`, `kmeans250218`, `kmeans250514`
- The segmentation output-filename schema parsed by the pipeline
  (`archiveID-instanceID-familyID-rank-PID-...`)

Everything else (module files, functions, variables, output HTML, directories) is free to change.
**Version identifiers belong in contract names, not in function names** — rename the *function*
`fpyolo11l241114()` to `run_segmentation()`; leave the *table* `segment_fpyolo11l241114` alone.

---

## 1. Filename pattern

```
<verb>_<object>[_<qualifier>].py
```
- **verb** = what the tool does (the action vocabulary, §2)
- **object** = the domain noun it acts on (the ubiquitous language, §3)
- **qualifier** = optional disambiguator (a method, a scope) — use only when it adds information

Examples: `analyze_discount.py`, `visualize_color_explorer.py`, `validate_heraldic_filter.py`,
`tune_hue_family_rows.py`, `build_zones.py`.

Avoid qualifiers that describe a rendering detail or history rather than purpose
(`_scaled`, `_v2`, `_forcedirected`, `_monthly`, a date).

## 2. Verb vocabulary (one word per concept)

| Verb | Means | Use for |
|---|---|---|
| `segment` / `extract-colors` / `downsample` / `drift` | the four pipeline stages | these ARE the pipeline verbs. `extract-colors` replaced the command `cluster` (2026-07) — see the command-verb note below |
| `analyze_` | compute results/statistics from data | discount stats, coverage distribution |
| `visualize_` | render a view (interactive or static) | the color explorer, distribution heatmaps |
| `build_` | deterministically construct an artifact | zone definitions, keyword maps |
| `discover_` | surface **candidates for human review** from data | new heraldic keyword candidates |
| `assign_` | apply a trained model to label data | RF zone propagation |
| `tune_` | recalibrate a rule/threshold/parameter | hue-family radius, merge thresholds |
| `validate_` | measure efficacy against **labeled ground truth** | filter precision/recall, classification accuracy |
| `measure_` | compute a quality metric (no ground truth) | a label-free cohesion/quality score (no live instance — silhouette QA dropped 2026-07) |
| `review_` | interactive human-in-the-loop inspection tool | click-to-flag cluster review |
| `check_` | a regression/assertion test | ICC color-fidelity test |

Do NOT introduce synonyms for these (`audit_`, `diagnostic_`, `test_`, `vis_` are retired as
prefixes — map each to the verb above that fits its actual behavior).

**CLI command verbs vs. module names (2026-07 rename).** No CLI command reads as a bare `cluster` —
the command verbs deliberately avoid the noun so each names its distinct role. `extract-colors`
replaced `cluster`; `consolidate-colors` replaced `analyze-discount` (it runs the CIEDE2000
clustering **and** the per-cluster discount FDR — the clustering is the essential half); and
`build-color-clusters` is retired (folded into `consolidate-colors`). The word `cluster` survives
only as a data **noun** — the grouping hierarchy in §3 (`cluster` → `zone` → `archetype`),
`cluster_id`, `cluster_zones.json`, and the DB tables all keep it. Command verbs are their own
namespace and are exempt from the one-word-per-concept rule above; module filenames were left
unchanged, so command verb ≠ module name for two of these (`extract-colors` →
`data_prep/cluster.py`, `consolidate-colors` → `palette_explorer/analyze_discount.py`).

## 3. Object vocabulary (ubiquitous language)

Use exactly these domain nouns; do not invent adjacent ones.

**Pipeline / data:** `product`, `instance`, `archive`, `brand`, `gender`, `segment`, `mask`
**Color:** `lab`, `rgb`, `hsb`, `hue`, `lightness`, `chroma`, `coverage`, `palette`, `swatch`,
saturation classes `saturated` / `muted` / `neutral`
**Grouping (current hierarchy):** `cluster` → `zone` → `archetype`
**Perceptual:** `ciede2000` / `delta_e`, `agglomerative`, `centroid`, `drift`, `sinkhorn`
**Analysis:** `discount` (`frequency`, `depth`), `price`, `attrition`, `heraldic`, `keyword_map`

**DEPRECATED terms — remove from all live names:**
- `theme` (themes layer abandoned, S95) — fully purged from the slice: the zone builder's
  Themes mode, the `cluster_themes.json` export, and the vestigial `theme_height`/`themeH`
  row heights in the explorer are all gone. `zone` is the only grouping above `cluster`.
- `family` (family layer dropped) — except the frozen `familyID` field inside the contract schema
- `emd`, `k_mode`, `fingerprint`, `vibrancy` (superseded eras — live only in the museum repo)

## 4. Uniqueness & searchability

- **No two produced artifacts may share a filename.** (Today two scripts both emit
  `column_preview_scaled.html` — a correctness hazard, not just a smell.)
- Prefer names that grep cleanly. Avoid bare generic module names for widely-imported code:
  `utils.py` → `db.py`; `main.py` → `pipeline_cli.py` (or similar precise names).
- Name length scales with scope: a repo-wide module earns a long precise name; a local variable
  can be short.

## 5. Python identifier conventions (unchanged from existing style)

- Functions / variables: `lower_snake_case`
- Constants: `SCREAMING_SNAKE_CASE` at module top
- Classes: `PascalCase`
- Module filename matches its primary public function where one dominates
- SQL identifiers via `psycopg2.sql.SQL()` (safety) — unchanged

## 6. Output artifact naming

- Rendered deliverables follow the same `<verb-less concept>_<qualifier>` idea and must be unique.
- First-class outputs are **regenerable** — their names describe the *view*, not a run
  (`color_explorer.html`, not `column_preview_scaled.html`).
- Museum renders are frozen snapshots — keep their original names for provenance fidelity.

---

## 7. Worked rename examples (seed; full per-file map lives in the audit ledger)

| Current | Issue (principle #) | Proposed |
|---|---|---|
| `visualize_column_preview_discount.py` → `column_preview_scaled.html` | reveals-intent (1), searchable/unique (5) | `palette_explorer.py` → `palette_explorer.html` (named interactive app; "explorer" carries the visualize sense, so no verb prefix) |
| `audit_heraldic_filter.py` | disinformation — it builds, not audits (3) | `discover_heraldic_keywords.py` |
| `diagnostic_ab_neighbors.py` | names implementation not abstraction (2) | `tune_hue_family_rows.py` |
| `diagnostic_cluster_review.py` | retired `diagnostic_` prefix (4) | `review_cluster_quality.py` |
| `diagnostic_cluster_silhouette.py` | prefix (4) + measure vs validate (4) | `measure_cluster_silhouette.py` *(later removed — Track B QA dropped 2026-07)* |
| `analyze_merge_rule_discovery.py` | verb+object clarity (1) | `tune_palette_merge_rules.py` |
| `test_mask_binarization.py` | prefix (4) | `check_mask_color_shift.py` |
| `validation_tool.html` | reveals-intent (1) | `archetype_classification_validator.html` |

**Deferred to the folder-structure phase** (correct by "length scales with scope" §6, but cleaner to settle with the layout):
`utils.py → db.py` · `main.py → pipeline_cli.py` · `drift_monthly.py → drift.py`

DB-side names (e.g. the `fpyolo11l241114` inside table names) stay frozen per §0.
