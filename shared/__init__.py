"""shared/ - utilities used by 2+ slices. Import-only; nothing here is run via cli.py.

  db.py           connect_to_db + archive/unprocessed helpers
  hsb.py          HSB/LAB color helpers
  ciede2000.py    GPU CIEDE2000 pairwise distance
  palette_merge/  the hue-merge primitive (single source of truth) + its tuner
"""
