# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## Repo context

This is the standalone **Color Analytics** repository — computer-vision and statistical analysis of color trends in athletic apparel across five brands. Split out from a larger hybrid repository on 2026-09-10 after a 12-phase parity exercise verified the port reproduces the pre-port codebase byte-for-byte on identical inputs.

The **museum repo** at `../color_analytics_pipeline/` holds the pre-port codebase, the parity exercise artifacts (`parity_tests/`), and 156 session documents covering the project's full history. When you need pre-2026-09-10 provenance — how a decision was made, what an old-code file looked like, why the port took the shape it did — look there. When you need to know what this repo does today, everything is here.

Read on open, in order:
- `README.md` — user-facing overview, four deliverables, reproducibility note
- `STRUCTURE.md` — file tree and slice-by-slice module inventory
- `CONVENTIONS.md` — naming rubric, frozen contracts, coding conventions

## Architecture at a glance

One shared foundation (`data_prep/` — segment → cluster) feeds several independent consumers:
- `palette_explorer/` (flagship — CIEDE2000 agglomerative clustering + interactive HTML)
- `archetype_taxonomy/` (5-category palette classification)
- `coverage/` (coverage distributions by color class)
- `drift/` (temporal drift via Sinkhorn OT)
- `shared/` (DB, LAB↔RGB, palette-merge primitive, CIEDE2000 GPU distance)

`cli.py` at repo root registers all commands. `python cli.py list` prints the current registry.

## Running

Docker is the intended execution environment (CUDA + ultralytics + CuPy + PyTorch dep stack).

```bash
# Prerequisites: host env must have POSTGRES_PASSWORD set.
# docker-compose.yml uses ${POSTGRES_PASSWORD:?...} — unset fails fast at config time
# with a clear message, before the container starts.

docker compose run --rm pipeline python cli.py list
docker compose run --rm pipeline python cli.py <command> <brand> [--gender mens|womens]
```

Image bind mount: `../../03_images:/app/images`. Container-side `IMAGE_DIR=/app/images` is set in `docker-compose.yml` so tools' `IMAGE_DIR` candidate lists find the mount without per-invocation `-e` flags.

## Database

**Contract-protected tables** (owned by the scraper repo, read-only here): `archive`, `instance`, `family`, `appendix`. This pipeline NEVER writes to these — verified by static grep in the parity exercise (S152 Q1).

**Analytics-owned tables** (this pipeline writes here): `segment_fpyolo11l241114`, `cluster_fpyolo11l241114_kmeans250218`, `comp_cluster_fpyolo11l241114_kmeans250218`, `driftcolor_comp_cluster_fpyolo11l241114_kmeans250218`. These table names are frozen — never rename them.

**Read-only-default enforcement** (S155): `shared/db.py`'s `connect_to_db(brand)` defaults to `readonly=True`. Any INSERT/UPDATE/DELETE on a default connection errors at the driver ("cannot execute … in a read-only transaction"). The four writer sites opt in explicitly:
- `data_prep/segment.py` — writes `segment_*`
- `data_prep/cluster.py` — writes `cluster_*`
- `drift/downsample.py` — writes `comp_cluster_*`
- `drift/drift.py` — writes `driftcolor_*`

When adding a new writer, follow the same pattern: `db.connect_to_db(brand, readonly=False)`. When adding a reader, use the bare default — driver-layer enforcement then blocks accidental writes.

## Environment variables

- `POSTGRES_PASSWORD` — required, no default. Host env var, forwarded into the container by compose.
- `DB_HOST` — defaults to `host.docker.internal`.
- `DB_PORT` — defaults to `5432`.
- `IMAGE_DIR` — path to the product-image tree. Set to `/app/images` inside the container.
- `SEG_OUTPUT_ROOT` — optional. When set, segmentation writes PNGs under `<SEG_OUTPUT_ROOT>/<archive>/fpyolo11l241114_<ts>/`. Unset (production default): PNGs land next to source JPEGs.

## Frozen contracts (never rename)

- Table names above.
- Segmentation output filename schema: `<archive>-<instance>-<family>-<rank>-<pid>-<model>-<segid>.png`.
- Version identifiers embedded in table/module names: `fpyolo11l241114` (YOLO model), `kmeans250218` (K-means clustering), `downsample_kmeans250514`, `drift_monthly_250503`.

Function names, filenames, and CLI verbs are free to rename per the CONVENTIONS.md rubric.

## Session documentation

New repo, so session numbering starts fresh at **Session 001**. Museum session docs (156 of them) are historical reference only — cross-link when relevant but don't renumber them.

When the user asks to document a session, follow the museum's session-doc format (see `../color_analytics_pipeline/CLAUDE.md § Session Documentation Procedures`) but write to `documentation/SessionNN_YYYYMMDD_slug.md` in this repo, and maintain this repo's own `documentation/DECISION_LOG.md`.

## Working conventions

- Write to a scratch dir (not the project tree) for temporary/experimental outputs.
- Prefer editing existing files over creating new ones.
- The `outputs/` folders inside each slice are gitignored; they're regenerated on run.
- When modifying color analysis logic: always work in LAB space, convert only for visualization.
- For UI/HTML deliverables: open in a browser and verify visually before reporting the task done.
