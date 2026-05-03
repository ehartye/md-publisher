"""End-to-end build pipeline: corpus markdown -> themed PDF.

Stages:
  1. Read the source markdown
  2. Configure python-markdown with extensions (mermaid, tables, code, etc.)
  3. Convert to HTML body (mermaid blocks become inline SVG along the way)
  4. Walk the body, ensure every h1/h2 has a stable id, collect headings
  5. Build the TOC nav block
  6. Wrap the result in an HTML document with our print stylesheet
  7. Hand to WeasyPrint to produce the paged PDF
"""

from __future__ import annotations

import re
import sys
from html import escape as html_escape
from pathlib import Path

import markdown

from .gtk_loader import register_gtk_runtime
from .mermaid_processor import (
    MermaidExtension, MermaidPostprocessor, find_mmdc,
)
from .theme_loader import ThemeSelection, build_classdefs
from .toc import assign_heading_ids, render_toc


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>{pygments_css}</style>
    <style>{print_css}</style>
    <style>{cover_css}</style>
</head>
<body>
{cover}
{toc}
{body}
</body>
</html>
"""


# Lightweight YAML front-matter parser for the limited subset we care about
# (title / subtitle / author / date — all strings). Avoids pulling in PyYAML.
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_KV_RE = re.compile(r"^\s*([A-Za-z][\w-]*)\s*:\s*(.*?)\s*$")


def parse_front_matter(md_text: str) -> tuple[dict[str, str], str]:
    """Pull a `--- ... ---` YAML block off the top of the markdown if present.

    Returns ``(metadata_dict, remaining_markdown)``. Quoted values have their
    surrounding quotes stripped. If no front matter is present, returns an
    empty dict and the original text.
    """
    match = _FRONT_MATTER_RE.match(md_text)
    if match is None:
        return {}, md_text
    body_remaining = md_text[match.end():]
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        kv = _KV_RE.match(line)
        if kv is None:
            continue
        key, value = kv.group(1), kv.group(2)
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        meta[key.lower()] = value
    return meta, body_remaining


def derive_first_h1(md_text: str) -> str | None:
    """Pull the text of the first `# ...` heading from a markdown source.

    Skips fenced code blocks so a `#` line inside a code sample doesn't
    masquerade as a heading. Returns None if no h1 is present.
    """
    in_fence = False
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^#\s+(.+?)\s*#*\s*$", line)
        if m:
            return m.group(1).strip()
    return None


def build_cover_html(
    *,
    title: str,
    subtitle: str | None,
    author: str | None,
    date: str | None,
    attribution: str,
) -> str:
    """Render the cover-page HTML block. Inserted before the TOC in the body."""
    parts = ['<header class="cover">']
    parts.append(f'  <h1 class="cover-title">{html_escape(title)}</h1>')
    parts.append('  <div class="cover-rule"></div>')
    if subtitle:
        parts.append(f'  <p class="cover-subtitle">{html_escape(subtitle)}</p>')
    meta_items = []
    if author:
        meta_items.append(f'<span class="cover-author">{html_escape(author)}</span>')
    if date:
        meta_items.append(f'<span class="cover-date">{html_escape(date)}</span>')
    if meta_items:
        parts.append('  <div class="cover-meta">')
        for item in meta_items:
            parts.append(f'    {item}')
        parts.append('  </div>')
    parts.append(
        f'  <footer class="cover-attribution">{html_escape(attribution)}</footer>'
    )
    parts.append('</header>')
    return "\n".join(parts)


def cover_css(theme_selection: ThemeSelection) -> str:
    """Theme-aware CSS for the cover page. Generated per build so each theme
    can express its identity (italic serif title for ATLAS, shell-prompt
    `> title` for PHOSPHOR, neon-shadow display for ARCADE) without
    duplicating the cover styling across all 7 CSS files.
    """
    base = """
/* Cover page — first page of the document, before the TOC. Named so we can
   suppress running header/footer chrome via @page cover. */
header.cover {
    page: cover;
    break-after: page;
    page-break-after: always;
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    min-height: 9in;
    padding: 0;
}
header.cover .cover-title {
    margin: 2.6in 0 0.3in 0;
    line-height: 1.05;
    border: none;
    padding: 0;
}
header.cover .cover-rule {
    margin: 0.3in 0;
}
header.cover .cover-subtitle {
    margin: 0 0 0.5in 0;
}
header.cover .cover-meta {
    margin: 0;
}
header.cover .cover-meta span {
    display: block;
    margin: 0.08in 0;
}
header.cover .cover-attribution {
    margin-top: auto;
    padding-top: 1in;
}
@page cover {
    margin: 1in;
    @top-right { content: ""; }
    @bottom-center { content: ""; }
    @bottom-right { content: ""; }
}
"""
    if theme_selection.name == "default":
        # Default theme: clean accent-colored sans title, italic muted subtitle
        per_theme = """
header.cover .cover-title {
    font-family: var(--font-heading);
    font-size: 36pt;
    font-weight: 700;
    color: var(--color-accent);
}
header.cover .cover-rule {
    width: 1.4in;
    height: 2px;
    background: var(--color-accent);
}
header.cover .cover-subtitle {
    font-family: var(--font-body);
    font-size: 16pt;
    font-style: italic;
    color: var(--color-muted);
}
header.cover .cover-meta {
    font-family: var(--font-heading);
    font-size: 11pt;
    color: var(--color-text);
}
header.cover .cover-attribution {
    font-family: var(--font-heading);
    font-size: 9pt;
    color: var(--color-muted);
}
"""
    elif theme_selection.name == "atlas":
        # Editorial trust: italic Newsreader display, red rule, sans-uppercase meta
        per_theme = """
header.cover .cover-title {
    font-family: var(--font-display);
    font-size: 44pt;
    font-weight: 800;
    font-style: italic;
    color: var(--ink);
    letter-spacing: -0.02em;
}
header.cover .cover-rule {
    width: 1.2in;
    height: 2px;
    background: var(--accent);
}
header.cover .cover-subtitle {
    font-family: var(--font-display);
    font-size: 16pt;
    font-style: italic;
    color: var(--ink-soft);
}
header.cover .cover-meta {
    font-family: var(--font-sans);
    font-size: 10pt;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--ink-soft);
}
header.cover .cover-attribution {
    font-family: var(--font-sans);
    font-size: 8.5pt;
    color: var(--ink-soft);
    text-transform: uppercase;
    letter-spacing: 0.08em;
}
@page cover { background: var(--bg); }
"""
    elif theme_selection.name == "phosphor":
        # CRT amber: > prefix, lowercase, all mono, // subtitle, [ bracketed ] meta
        per_theme = """
header.cover .cover-title {
    font-family: var(--font-mono);
    font-size: 30pt;
    font-weight: 700;
    color: var(--ink-emphasis);
    text-transform: lowercase;
}
header.cover .cover-title::before {
    content: "> ";
    color: var(--accent);
}
header.cover .cover-rule {
    width: 1.4in;
    height: 1px;
    background: var(--ink-soft);
}
header.cover .cover-subtitle {
    font-family: var(--font-mono);
    font-size: 13pt;
    font-style: italic;
    color: var(--ink-soft);
}
header.cover .cover-subtitle::before { content: "// "; color: var(--accent); }
header.cover .cover-meta {
    font-family: var(--font-mono);
    font-size: 10pt;
    color: var(--ink);
}
header.cover .cover-meta span::before { content: "[ "; color: var(--ink-soft); }
header.cover .cover-meta span::after  { content: " ]"; color: var(--ink-soft); }
header.cover .cover-attribution {
    font-family: var(--font-mono);
    font-size: 9pt;
    color: var(--ink-soft);
}
@page cover { background: var(--bg); }
"""
    elif theme_selection.name == "arcade":
        # Game manual: bold display title with hard offset shadow, gradient rule,
        # Bungee subtitle in cyan, body-font meta, neon attribution
        per_theme = """
header.cover .cover-title {
    font-family: var(--font-display);
    font-size: 42pt;
    font-weight: 400;
    color: var(--ink);
    text-shadow: 4px 4px 0 var(--accent2);
    letter-spacing: 0.02em;
    line-height: 1.0;
}
header.cover .cover-rule {
    width: 2in;
    height: 6px;
    background: linear-gradient(90deg, var(--accent1), var(--accent2));
    border-radius: 3px;
}
header.cover .cover-subtitle {
    font-family: var(--font-display2);
    font-size: 13pt;
    color: var(--accent3);
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
header.cover .cover-meta {
    font-family: var(--font-body);
    font-size: 11pt;
    color: var(--ink);
}
header.cover .cover-attribution {
    font-family: var(--font-display2);
    font-size: 9pt;
    color: var(--accent1);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
@page cover { background: var(--bg); }
"""
    else:
        per_theme = ""
    return base + per_theme


def build_pdf(
    *,
    source: Path,
    output: Path,
    theme_selection: ThemeSelection,
    include_cover: bool = True,
    build_dir: Path | None = None,
) -> Path:
    """Render `source` markdown to a paged PDF at `output`.

    The four universal classDef lines (ingress/core/transform/bridge) are
    injected into every flowchart/graph mermaid block before mmdc renders
    it, picking up colors from the theme's spec block. For the "default"
    theme (no spec block), no injection happens — mermaid uses its built-in
    palette.

    `include_cover=False` suppresses the cover page entirely; useful for
    embeddable / multi-doc bundling where a cover would interrupt flow.

    `build_dir` defaults to `<output.parent>/build/`; intermediate SVGs and
    the assembled HTML land there for debugging. Returns the output path.
    """
    from . import runtime
    if not source.exists():
        raise FileNotFoundError(f"source markdown not found: {source}")

    # On Windows, register the GTK runtime DLL directory before importing
    # WeasyPrint. Python 3.8+ ignores PATH for native-DLL resolution, so
    # this os.add_dll_directory call is the load-bearing piece.
    gtk_dir = register_gtk_runtime()
    if gtk_dir is not None:
        print(f"      using GTK runtime at: {gtk_dir}")

    # Late import so we surface a clean error if WeasyPrint's native
    # dependencies (GTK / Cairo / Pango on Windows) are missing.
    try:
        from weasyprint import HTML, CSS  # noqa: F401
    except OSError as e:
        sys.stderr.write(
            "\n[ERROR] WeasyPrint failed to load its native libraries.\n"
            "On Windows you need the GTK runtime (Pango / Cairo / GDK-Pixbuf).\n"
            "See: https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows\n"
            f"Underlying error: {e}\n\n"
        )
        raise

    from weasyprint import HTML

    if build_dir is None:
        build_dir = output.parent / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    diagrams_dir = build_dir / "diagrams"
    diagrams_dir.mkdir(parents=True, exist_ok=True)

    mmdc_path = find_mmdc()
    puppeteer_config = runtime.puppeteer_config()

    mermaid_config = theme_selection.mermaid_config_path
    css_path = theme_selection.css_path
    classdef_lines = build_classdefs(theme_selection)
    theme_label = theme_selection.slug

    print(f"      theme   : {theme_label}")
    print(f"      css     : {css_path.name}")
    print(f"      mermaid : {mermaid_config.name}")
    if classdef_lines:
        print(f"      classDefs injected: {len(classdef_lines)}")

    print(f"[1/6] reading source: {source}")
    md_text_raw = source.read_text(encoding="utf-8")

    # Pull a YAML front-matter block off the top if present, then derive
    # cover-page metadata: title from front matter or first h1, subtitle /
    # author / date from front matter only. Rest is markdown for the body.
    front_matter, md_text = parse_front_matter(md_text_raw)
    cover_title = (
        front_matter.get("title")
        or derive_first_h1(md_text)
        or source.stem.replace("-", " ").title()
    )
    cover_subtitle = front_matter.get("subtitle")
    cover_author = front_matter.get("author")
    cover_date = front_matter.get("date")
    cover_attribution = (
        front_matter.get("attribution")
        or f"Built with WeasyPrint  ·  {theme_label}"
    )

    print(f"[2/6] configuring markdown (mmdc: {mmdc_path})")
    mermaid_ext = MermaidExtension(
        mmdc_path=mmdc_path,
        build_dir=diagrams_dir,
        mermaid_config=mermaid_config,
        puppeteer_config=puppeteer_config,
        classdef_lines=classdef_lines,
    )
    md = markdown.Markdown(
        extensions=[
            mermaid_ext,
            "fenced_code",
            "tables",
            "codehilite",
            "attr_list",
            "smarty",
            "sane_lists",
        ],
        extension_configs={
            "codehilite": {
                "css_class": "codehilite",
                "guess_lang": False,
                "linenums": False,
            },
        },
    )

    print("[3/6] rendering markdown -> HTML (this also runs mmdc)")
    body_html = md.convert(md_text)

    # Splice rendered SVG figures back into the HTML where the placeholders sit.
    assert mermaid_ext.preprocessor is not None
    figures = mermaid_ext.preprocessor.figures
    body_html = MermaidPostprocessor(figures)(body_html)
    print(f"      mermaid figures rendered: {len(figures)}")

    print("[4/6] assigning heading ids and building TOC")
    body_html, headings = assign_heading_ids(body_html)
    toc_html = render_toc(headings)
    print(f"      headings collected: {len(headings)} "
          f"(h1={sum(1 for h in headings if h.level == 1)}, "
          f"h2={sum(1 for h in headings if h.level == 2)})")

    print("[5/6] assembling HTML document")
    print_css = css_path.read_text(encoding="utf-8")
    pygments_css = (runtime.plugin_root() / "themes" / "pygments.css").read_text(encoding="utf-8")
    title = cover_title  # already derived above; consistent with cover

    if include_cover:
        cover_html = build_cover_html(
            title=cover_title,
            subtitle=cover_subtitle,
            author=cover_author,
            date=cover_date,
            attribution=cover_attribution,
        )
        cover_css_text = cover_css(theme_selection)
    else:
        cover_html = ""
        cover_css_text = ""

    full_html = HTML_TEMPLATE.format(
        title=title,
        body=body_html,
        toc=toc_html,
        cover=cover_html,
        print_css=print_css,
        pygments_css=pygments_css,
        cover_css=cover_css_text,
    )

    # Persist the assembled HTML for debugging — the rendered intermediate
    # is valuable when chasing layout bugs.
    debug_html = build_dir / "document.html"
    debug_html.write_text(full_html, encoding="utf-8")
    print(f"      intermediate HTML: {debug_html}")

    print(f"[6/6] running WeasyPrint -> {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    # base_url lets WeasyPrint resolve relative paths inside the HTML (mostly
    # absent in our generated doc, but stays safe). Use plugin_root so any
    # bundled-asset reference would resolve.
    HTML(string=full_html, base_url=str(runtime.plugin_root())).write_pdf(str(output))
    print(f"      PDF written: {output} "
          f"({output.stat().st_size // 1024} KB)")
    return output
