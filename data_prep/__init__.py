"""data_prep/ - FOUNDATION: raw product images -> clustered LAB color data. Every other slice consumes its output.

Run first, per brand:
  cli.py segment <brand>   ENTRY - YOLO garment segmentation
  cli.py extract-colors <brand>   ENTRY - LAB k-means (5 colors / garment)

Filter maintenance + data:
  cli.py discover-heraldic-keywords    ENTRY - surface new team/place keywords
  heraldic_filter.py, keyword_maps.py  INTERNAL - data imported by segment.py
  quality/  optional precision/recall + fidelity checks (see quality/__init__.py)

Foundation must run before any analysis slice. Full recipe: README ## Recipes.
"""
