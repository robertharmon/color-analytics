"""palette_explorer/ - FLAGSHIP consumer slice -> palette_explorer.html, the primary interactive deliverable.

Build order (idempotent - run a step only if its artifact is missing/stale; full recipe: README ## Recipes):
  one-time prerequisite, ONLY if outputs/nike_mens/cluster_zones.json doesn't exist yet:
    cli.py build-zones nike --gender mens        ENTRY - opens the tool; you HAND-LABEL zones -> cluster_zones.json
  per brand/gender you want shown (incl. nike mens), each step skippable if its output is current:
    cli.py consolidate-colors <brand> --gender G ENTRY - -> cluster + discount CSVs (runs clustering itself)
    cli.py assign-zones <brand> --gender G       ENTRY - -> that brand's cluster_zones.json (RF-propagated)
  once, to (re)render:
    cli.py palette-explorer                      ENTRY - render every brand folder into the flagship HTML

  build_color_clusters.py  INTERNAL - clustering engine reused by consolidate-colors (no command of its own)
  hue_family_grouping.py   INTERNAL - a*b* row-grouping primitive
  cli.py tune-hue-family-rows   calibrate that primitive's radius (optional)
  quality/  optional cluster-quality checks (see quality/__init__.py)
"""
