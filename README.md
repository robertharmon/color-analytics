# Color Analytics Pipeline

A computer-vision and statistical pipeline for extracting and analyzing color trends in athletic apparel across major brands. The system processes raw product photos, isolates garments, reduces them to dominant colors, and outputs interactive deliverables detailing brand palette usage, temporal shifts, and discount correlations.

This pipeline produces four deliverables. The flagship, an interactive [Palette Explorer](TODO-palette-explorer-url), has a live demo you can explore.

To understand what each deliverable is for, how it's built, and the overall structure of this project, see [Data Flow & Usage — Operator's Reference](https://robertharmon.github.io/color-analytics/) — every stage is diagrammed and every command documented.

To learn more about why this project exists, what it aims to investigate, and the technical hurdles encountered during development, see the [project retrospective](TODO-retrospective-url).

---

> ### Reproducibility Note
>
> **This project is not runnable from a fresh clone.** It depends on a private product-image corpus and per-brand PostgreSQL databases populated by a separate scraper — neither of which is included in this repository. The code is published for review and as a personal reference. If you're evaluating the work, do not expect `git clone` to reproduce results.

---

## Table of Contents

1. [Environment & Setup](#1-environment--setup)
2. [Appendix: Migration & Contracts](#2-appendix-migration--contracts)
3. [License](#3-license)

---

## 1. Environment & Setup

Ensure these prerequisites are provisioned before running production commands.

### Production Prerequisites

* **Runtime:** Docker is the default runtime (see below); the image ships Python 3.11 and CUDA. Running outside Docker instead needs Python 3.11+ (`requirements.txt`) on the host. Either way a CUDA GPU is required for YOLO segmentation, Sinkhorn drift, and GPU-accelerated CIEDE2000 clustering.
* **PostgreSQL:**
    * One database per brand named exactly: `nike`, `adidas`, `puma`, `lulu`, `ua`.
    * Connection: User `postgres` on port `5432` at `$DB_HOST` (defaults to `localhost`). Password must be set via `POSTGRES_PASSWORD`.
    * Analytics destination tables must already exist; input tables (`archive`, `instance`) are pre-populated by the upstream scraper.
* **Local Assets:**
    * **Images:** Product images reside at `./images/` in `BRAND_query_archiveID_timestamp/` format.
    * **Model Weights:** Place YOLO weights at `data_prep/weights/Fashionpedia_YOLO11l_241114/best.pt`.

### Docker Environment (Default)
The `Dockerfile` and `docker-compose.yml` encapsulate the runtime, GPU allocation, and image store mounting (`../../../03_images`). This is the standard way to run every command; the external commands reference shows how to build a full invocation from a minimal command name.

Build once, then smoke-test with the one command that needs no database or password:

    docker compose build
    docker compose run --rm pipeline python cli.py list    # prints the command list

Everything past `list` touches the database or GPU — see the external commands reference for the full invocation template (including the required `POSTGRES_PASSWORD`).

---

## 2. Appendix: Migration & Contracts

* **Frozen Contracts:** Database table names, embedded version IDs (`fpyolo11l241114`, `kmeans250218`), the read-only `instance`/`archive` tables, and segmentation schemas are strictly immutable to maintain scraper compatibility.
* **Migration Status:** The package-by-feature re-architecture is structurally complete. 44 Python files migrated, standard LF encoding applied, and fused-file splits executed.
* **Next Action:** Execute user-run acceptance test against the live DB/GPU to verify `palette_explorer.py` successfully reproduces legacy `column_preview_scaled.html`.

---

## 3. License

**All rights reserved.**

This repository is published for review only. No permission is granted to use, copy, modify, merge, publish, distribute, sublicense, or sell any part of the code, models, data, or generated deliverables, whether for commercial or non-commercial purposes. Viewing the source on GitHub does not confer any license to it.

If you are interested in using or building on this work, contact the author.

See the [`LICENSE`](LICENSE) file at the repository root for the full text.
