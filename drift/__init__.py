"""drift/ - consumer slice: has a brand's color palette shifted over time? (renamed from detection/)

  cli.py downsample <brand>     ENTRY - downsample clusters to 1900 / archive (feeds drift)
  cli.py compute-drift <brand>  ENTRY - Sinkhorn distance between archive palettes
  cli.py visualize-drift        ENTRY - multi-brand chart -> drift_comparison.html

Order: downsample + compute-drift are per-brand; run them for each brand, then visualize-drift once.
Full recipe (required-vs-optional, per-brand notes): README ## Recipes.
"""
