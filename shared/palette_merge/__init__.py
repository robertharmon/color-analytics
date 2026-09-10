"""shared/palette_merge/ - the hue-based cluster-merge primitive, shared by every slice that builds palettes.

  palette_merge.py     INTERNAL - merge_clusters_hue_based + thresholds (single source of truth)
  tune_merge_rules.py  ENTRY  (cli.py tune-merge-rules) - recalibrate the thresholds from labeled data
"""
