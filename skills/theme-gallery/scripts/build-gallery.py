#!/usr/bin/env python
"""Build a single HTML gallery page showing every md-publisher theme.

Combines built-in + user themes into one scrollable page. Each theme card
shows its name, tagline, palette swatches, and an iframe pointing at the
theme's preview.html (when present).

Output goes to ~/.md-publisher/gallery.html by default; pass --output to
override. The skill typically opens this file in the user's browser.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from html import escape as html_escape
from pathlib import Path


PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or
                   Path(__file__).resolve().parent.parent.parent.parent)
USER_DATA_ROOT = Path.home() / ".md-publisher"
DEFAULT_OUTPUT = USER_DATA_ROOT / "gallery.html"
LIST_SCRIPT = Path(__file__).resolve().parent / "list-themes.py"


GALLERY_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>md-publisher · theme gallery</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,wght@0,400;0,800;1,400&family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@300;500&display=swap" rel="stylesheet">
<style>
  :root {
    --chrome-bg: #FAF8F4;
    --chrome-paper: #FFFFFF;
    --chrome-ink: #14141A;
    --chrome-muted: #5A5A6A;
    --chrome-rule: #E8E2D4;
    --chrome-sans: 'DM Sans', system-ui, sans-serif;
    --chrome-mono: 'JetBrains Mono', ui-monospace, monospace;
    --chrome-italic: 'Newsreader', Georgia, serif;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--chrome-bg); color: var(--chrome-ink);
         font-family: var(--chrome-sans); font-size: 14px; line-height: 1.55; }
  .page { max-width: 1480px; margin: 0 auto; padding: 56px 48px 96px; }
  header.doc { display: grid; grid-template-columns: 1fr auto; align-items: end;
               gap: 32px; padding-bottom: 28px; border-bottom: 1px solid var(--chrome-rule); }
  .eyebrow { font-family: var(--chrome-mono); font-size: 11px; text-transform: uppercase;
             letter-spacing: 0.18em; color: var(--chrome-muted); margin-bottom: 18px; }
  h1.doc-title { font-family: var(--chrome-italic); font-style: italic; font-weight: 800;
                 font-size: 56px; letter-spacing: -0.02em; margin: 0; }
  .doc-meta { font-family: var(--chrome-mono); font-size: 12px; color: var(--chrome-muted);
              text-align: right; line-height: 1.6; }
  .doc-meta b { color: var(--chrome-ink); }
  .source-section { margin-top: 56px; }
  .source-label { font-family: var(--chrome-mono); font-size: 11px; text-transform: uppercase;
                  letter-spacing: 0.18em; color: var(--chrome-muted); margin-bottom: 16px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(440px, 1fr)); gap: 24px; }
  .card { border: 1px solid var(--chrome-rule); border-radius: 4px; overflow: hidden;
          background: var(--chrome-paper); display: flex; flex-direction: column; }
  .card-head { padding: 18px 22px; border-bottom: 1px solid var(--chrome-rule); }
  .card-source { font-family: var(--chrome-mono); font-size: 10px; text-transform: uppercase;
                 letter-spacing: 0.16em; color: var(--chrome-muted); margin-bottom: 6px; }
  .card-source.user { color: #B22234; }
  .card-name { font-family: var(--chrome-italic); font-weight: 800; font-size: 28px;
               line-height: 1.05; letter-spacing: -0.02em; margin: 0 0 4px 0; }
  .card-mode { font-family: var(--chrome-mono); font-size: 11px; color: var(--chrome-muted);
               text-transform: lowercase; }
  .card-tagline { font-family: var(--chrome-italic); font-style: italic; font-size: 15px;
                  color: var(--chrome-muted); margin: 8px 0 0 0; }
  .card-slug { font-family: var(--chrome-mono); font-size: 11px; color: var(--chrome-muted);
               margin-top: 10px; }
  .card-slug code { background: var(--chrome-bg); padding: 2px 6px; border-radius: 2px; }
  .card-preview { flex: 1; min-height: 380px; }
  .card-preview iframe { width: 100%; height: 100%; min-height: 380px; border: none; display: block; }
  .card-preview.no-preview { padding: 40px; color: var(--chrome-muted); font-size: 13px;
                              text-align: center; align-self: stretch; display: flex;
                              align-items: center; justify-content: center; min-height: 380px; }
  .card-foot { padding: 12px 22px; border-top: 1px solid var(--chrome-rule);
               font-family: var(--chrome-mono); font-size: 11px; color: var(--chrome-muted);
               display: flex; gap: 14px; align-items: center; }
  .card-foot code { background: var(--chrome-bg); padding: 2px 6px; border-radius: 2px;
                    color: var(--chrome-ink); }
  footer.doc { margin-top: 80px; padding-top: 24px; border-top: 1px solid var(--chrome-rule);
               font-family: var(--chrome-mono); font-size: 11px; color: var(--chrome-muted); }
</style>
</head>
<body>
<div class="page">
  <header class="doc">
    <div>
      <div class="eyebrow">md-publisher · theme gallery</div>
      <h1 class="doc-title">{n_total} themes installed.</h1>
    </div>
    <div class="doc-meta">
      <b>{n_user}</b> custom &middot; <b>{n_builtin}</b> built-in<br>
      user dir: <code>{user_themes_dir}</code><br>
      bundled:  <code>{plugin_themes_dir}</code>
    </div>
  </header>

  {sections}

  <footer class="doc">
    Built by <code>md-publisher:theme-gallery</code>. Each card's preview is the live
    <code>preview.html</code> from that theme's directory; user themes override built-ins of
    the same slug. Use <code>/md-publisher:publish &lt;doc.md&gt; --theme &lt;name&gt; --mode &lt;mode&gt;</code>
    to render with a theme; <code>/md-publisher:theme-advisor</code> creates new ones.
  </footer>
</div>
</body>
</html>
"""


def render_card(theme: dict) -> str:
    src = theme["source"]
    summary = theme.get("spec_summary", {}) or {}
    name_label = html_escape(summary.get("displayName") or theme["name"])
    mode = theme.get("mode")
    mode_html = f'<div class="card-mode">{html_escape(mode)} mode</div>' if mode else ""
    tagline = html_escape(summary.get("tagline", ""))
    audience = html_escape(summary.get("audience", ""))
    slug = html_escape(theme["slug"])

    preview_path = Path(theme["path"]) / "preview.html"
    if theme["has_preview"] and preview_path.exists():
        # file:// URL is fine for local viewing in modern browsers
        preview_url = preview_path.as_uri()
        preview_html = (
            f'<div class="card-preview">'
            f'<iframe src="{preview_url}" loading="lazy" title="{name_label} preview"></iframe>'
            f'</div>'
        )
    else:
        preview_html = (
            '<div class="card-preview no-preview">'
            'No preview.html for this theme. '
            'Run <code>/md-publisher:publish</code> with this theme to see it on a real doc.'
            '</div>'
        )

    audience_html = (
        f'<div class="card-tagline" style="margin-top:14px; font-style:normal; font-size:11px; '
        f'text-transform:uppercase; letter-spacing:0.16em;">{audience}</div>'
        if audience else ""
    )

    return (
        f'<article class="card">'
        f'  <div class="card-head">'
        f'    <div class="card-source {src}">{html_escape(src)}</div>'
        f'    <h2 class="card-name">{name_label}</h2>'
        f'    {mode_html}'
        f'    {f"<p class=\'card-tagline\'>{tagline}</p>" if tagline else ""}'
        f'    {audience_html}'
        f'    <div class="card-slug">slug: <code>{slug}</code></div>'
        f'  </div>'
        f'  {preview_html}'
        f'  <div class="card-foot">'
        f'    /md-publisher:publish <code>&lt;doc.md&gt;</code> '
        f'    --theme <code>{html_escape(theme["name"])}</code>'
        f'    {f"--mode <code>{mode}</code>" if mode else ""}'
        f'  </div>'
        f'</article>'
    )


def render_sections(themes: list[dict]) -> str:
    builtins = [t for t in themes if t["source"] == "builtin"]
    user = [t for t in themes if t["source"] == "user"]
    out = []
    if user:
        out.append('<section class="source-section"><div class="source-label">Your custom themes</div>')
        out.append(f'<div class="grid">{"".join(render_card(t) for t in user)}</div></section>')
    if builtins:
        out.append('<section class="source-section"><div class="source-label">Built-in themes</div>')
        out.append(f'<div class="grid">{"".join(render_card(t) for t in builtins)}</div></section>')
    return "".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                   help=f"output path for the gallery HTML (default: {DEFAULT_OUTPUT})")
    p.add_argument("--open", action="store_true",
                   help="open the gallery in the OS default browser after building")
    args = p.parse_args()

    proc = subprocess.run(
        [sys.executable, str(LIST_SCRIPT)], capture_output=True, text=True
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return proc.returncode
    payload = json.loads(proc.stdout)
    themes = payload["themes"]

    n_user = sum(1 for t in themes if t["source"] == "user")
    n_builtin = sum(1 for t in themes if t["source"] == "builtin")

    html_doc = GALLERY_HTML.format(
        n_total=len(themes),
        n_user=n_user,
        n_builtin=n_builtin,
        user_themes_dir=html_escape(payload["user_themes_dir"]),
        plugin_themes_dir=html_escape(payload["plugin_themes_dir"]),
        sections=render_sections(themes),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html_doc, encoding="utf-8")
    print(f"[gallery] wrote: {args.output} ({len(themes)} themes)")

    if args.open:
        try:
            if sys.platform == "win32":
                os.startfile(str(args.output))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.call(["open", str(args.output)])
            else:
                subprocess.call(["xdg-open", str(args.output)])
        except Exception as e:
            sys.stderr.write(f"[gallery] could not open browser: {e}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
