# `docs-src/` — source material for the served `docs/` page

This folder holds the editable source for the Operator's Reference. It is
**not** served by GitHub Pages — Pages serves the sibling `docs/` folder
(source: `main` branch, folder: `/docs`). The published page lives at
whatever URL you've configured Pages to serve.

## Layout

```
docs/
├── index.html                        ← landing page linking to the two docs
├── operator-reference/
│   └── index.html                    ← the published Operator's Reference (regenerated from source-standalone.html)
└── retrospective/
    └── index.html                    ← the published Project Retrospective (unrelated to this folder)

docs-src/                             ← this folder (not served)
├── FLATTEN.md                        ← procedure for regenerating docs/operator-reference/index.html
├── README.md             ← this file
└── source-standalone.html           ← the recipe used to regenerate docs/operator-reference/index.html
```

## The two source files

| File | Role |
|---|---|
| `source-standalone.html` | **The recipe.** A single-file HTML with the doc's content stored as JavaScript data structures, plus a Mermaid runtime that renders the six diagrams to SVG at page load. Not served by Pages — kept here so `docs/operator-reference/index.html` can be regenerated when structural changes are needed. |
| `FLATTEN.md` | **The regeneration procedure.** Explains how to turn `source-standalone.html` into `docs/operator-reference/index.html`. Refer to it before any re-flatten. |

## Editing the doc

**For most changes — prose, tunable values, `file:line` citations, table
cells, CSS tweaks — edit `docs/operator-reference/index.html` directly.** It's plain HTML with
real markup; grep and edit work exactly as you'd expect.

**For diagram label changes** (e.g., renaming a CLI verb that appears
inside a diagram box) — each `<svg>` in `docs/operator-reference/index.html` is preceded by an
HTML comment containing its Mermaid source. Format:

    <!--
    MERMAID SOURCE (regenerate this diagram from this source).
    Escapes: Mermaid arrow syntax is written as `--&gt;` throughout
    this comment to keep it valid HTML. Restore each `--&gt;` to its
    plain form before rendering.

    flowchart TB
      ...
    -->
    <div class="seq" data-seq="dia-data_prep"><svg ...>...</svg></div>

Copy the source out of the comment, restore each `--&gt;` to `-->` (the
escape exists so `-->` inside the Mermaid source doesn't terminate the
HTML comment early), paste into [Mermaid Live Editor](https://mermaid.live),
edit, download the SVG, and swap it into `docs/operator-reference/index.html` in place of the
old `<svg>...</svg>` block. Update the source in the comment to match.

**For structural changes** (adding a new command entry, reordering
sections, changing a diagram's shape) — edit `source-standalone.html`'s
JavaScript data structures, then re-flatten per `FLATTEN.md` to regenerate
`docs/operator-reference/index.html`.

## After any change

The published Pages URL updates automatically once `docs/operator-reference/index.html` is
pushed to `main`. No build step, no workflow, no cache to bust.
