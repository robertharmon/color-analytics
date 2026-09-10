"""archetype_taxonomy/ - consumer slice: classify each product's 5-color palette into 5 archetypes.

  cli.py archetypes              ENTRY - classify -> CSVs (also imported by the visualizer + validation)
  cli.py archetype-distribution  ENTRY - distribution tables -> archetype_distribution.html
  validation/  build + score a labeled validation sample (see validation/__init__.py)

Order: archetypes, then archetype-distribution (validation is optional). Full recipe: README ## Recipes.
"""
