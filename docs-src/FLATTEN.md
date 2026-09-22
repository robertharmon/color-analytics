# Producing `docs/operator-reference/index.html`

The doc's source (`README preview v4.dc.html`) stores its content as JavaScript
data structures and renders Mermaid diagrams in-browser. Neither that file nor a
bundled build is what belongs in the repo.

What belongs in the repo is a **flattened snapshot**: the fully rendered DOM
with diagrams as real inline `<svg>`, saved as static HTML. The browser produces
that snapshot on every page load. The snippet below serializes it and downloads
the finished file.

Output: one `.html` file, ~590 KB, no `<script>` tags, no `<link>` tags, inline
`<style>`, system font stacks, inline `<svg>` diagrams each preceded by an HTML
comment holding its Mermaid source. Renders under strict CSP. Greppable and
directly editable.

## Procedure

1. Open `README preview v4.dc.html` in a browser. Wait for all six diagrams to
   draw (about a second).
2. DevTools → Console.
3. Paste the snippet, press Enter.
4. `index.html` downloads. Move it to `color-analytics/docs/operator-reference/index.html`.

Re-run whenever the source doc changes.

## The snippet

```js
(() => {
  const round = s => s.replace(/-?\d+\.\d+/g, m => (+m).toFixed(1));
  const SANS = "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif";
  const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace";

  // capture Mermaid sources from the live page, keyed by diagram
  const sources = {};
  document.querySelectorAll('.seq').forEach(el => {
    sources[el.getAttribute('data-seq')] = el.__mmd || '';
  });

  const gh = document.querySelector('.gh').cloneNode(true);

  // drop editor runtime attributes
  gh.querySelectorAll('*').forEach(el =>
    [...el.attributes].forEach(a => {
      if (/^data-(cc|dm|dc|om)/.test(a.name)) el.removeAttribute(a.name);
    }));

  // Mermaid's hand-drawn look emits 15-digit path coords; 1dp is visually
  // identical and halves the file (~1.4 MB -> ~590 KB)
  gh.querySelectorAll('path').forEach(p =>
    p.setAttribute('d', round(p.getAttribute('d') || '')));

  // prepend each diagram's Mermaid source as a regeneration comment
  gh.querySelectorAll('.seq').forEach(el => {
    const src = sources[el.getAttribute('data-seq')];
    if (!src) return;
    // "-->" would close the comment early; escape the ">" and say so
    const safe = src.replace(/--&gt;|-->/g, '--&gt;');
    el.insertBefore(
      document.createComment(
        `\nMERMAID SOURCE (regenerate this diagram from this source).\n` +
        `Arrows are escaped as "--&gt;" — restore them to "-->" before rendering.\n\n` +
        `${safe}\n`),
      el.firstChild);
  });

  let css = [...document.querySelectorAll('style')]
    .map(s => s.textContent).filter(t => /\.gh\b/.test(t)).join('\n');

  // swap webfonts for system stacks — no external fetches
  css = css.replace(/'IBM Plex Sans'/g, SANS).replace(/'IBM Plex Mono'/g, MONO);

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Data Flow &amp; Usage — Operator's Reference</title>
<style>
  html, body { margin: 0; padding: 0; }
  body { background: #f6f8fa; font-family: ${SANS}; }
  ${css}
  /* centered column + card, from the source doc's .page / .readme wrappers */
  .gh {
    max-width: 1012px;
    margin: 24px auto;
    background: #fff;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    padding: 32px 40px 48px;
  }
</style>
</head>
<body>
<div class="gh">
${gh.innerHTML}
</div>
</body>
</html>`;

  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
  a.download = 'index.html';
  a.click();
})();
```

## Editing it afterwards

Prose, command names, tunable descriptions, table cells — all real markup. Grep
and edit `docs/operator-reference/index.html` directly.

Diagram *labels* live inside the SVG blobs. To change one, edit the Mermaid
source in the comment above the diagram, re-render it (any Mermaid renderer,
or the source doc), and swap the `<svg>`. The comment exists so that stays
possible without the original project.
