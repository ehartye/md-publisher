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

import os
import re
import sys
import tempfile
from html import escape as html_escape
from pathlib import Path

import markdown

from .gtk_loader import register_gtk_runtime
from .mermaid_processor import (
    MermaidExtension, MermaidPostprocessor, find_mmdc,
)
from .theme_loader import ThemeSelection, build_classdefs, pygments_css_path
from .toc import assign_heading_ids, render_toc


# ---------------------------------------------------------------------------
# Emoji → text normalization
# ---------------------------------------------------------------------------
# Emoji-presentation characters that can't be embedded in PDF without Apple
# Color Emoji (which PDF viewers can't reliably extract). Map each to the
# closest text-presentation equivalent that ships in standard fonts.
_EMOJI_TO_TEXT: dict[str, str] = {
    "\u2705": "\u2713",   # ✅ → ✓  (check mark)
    "\u274C": "\u2717",   # ❌ → ✗  (ballot X)
    "\u2753": "?",        # ❓ → ?
    "\u2754": "?",        # ❔ → ?
    "\u2757": "!",        # ❗ → !
    "\u2755": "!",        # ❕ → !
    "\u2714": "\u2713",   # ✔ → ✓
    "\u2716": "\u2717",   # ✖ → ✗
    "\u26A0\uFE0F": "\u26A0",  # ⚠️ → ⚠ (strip emoji presentation selector)
}
# Variation selector U+FE0F forces emoji presentation; strip it so Pango
# picks a text font instead of a color-emoji font.
_VARIATION_SELECTOR = "\uFE0F"

# Build a single-pass regex from the mapping keys (longest first).
_EMOJI_RE = re.compile(
    "|".join(re.escape(k) for k in sorted(_EMOJI_TO_TEXT, key=len, reverse=True))
)


def _normalize_emoji(text: str) -> str:
    """Replace emoji-presentation characters with embeddable text glyphs."""
    text = _EMOJI_RE.sub(lambda m: _EMOJI_TO_TEXT[m.group()], text)
    return text.replace(_VARIATION_SELECTOR, "")


# ---------------------------------------------------------------------------
# Table code-term line breaking
# ---------------------------------------------------------------------------
# Long identifiers inside <code> in table cells (e.g.
# CMPL123QMS__Audit_Finding__c) have no natural break opportunities.
# We inject <wbr> (zero-width break) after underscores, dots, colons,
# slashes, and at camelCase boundaries so WeasyPrint can wrap them.

_TABLE_CODE_RE = None  # removed — superseded by _add_code_breaks
_INLINE_CODE_IN_CELL_RE = None  # removed
# Insert <wbr> after _, ., :, /, and at lowercase→uppercase (camelCase).
_BREAK_AFTER = re.compile(r"([_.:/])")
_BREAK_CAMEL = re.compile(r"([a-z])([A-Z])")


def _inject_wbr(code_content: str) -> str:
    """Insert <wbr> at natural break points inside a code span."""
    # Double underscores (__) should stay together — break after the pair.
    s = code_content.replace("__", "\x00DUNDER\x00")
    s = _BREAK_AFTER.sub(r"\1<wbr>", s)
    s = _BREAK_CAMEL.sub(r"\1<wbr>\2", s)
    return s.replace("\x00DUNDER\x00", "__<wbr>")


# Match <code>…</code> spans that are NOT inside <pre> blocks.
# We split the HTML on <pre>…</pre> regions and only process outside them.
_PRE_SPLIT_RE = re.compile(
    r"(<pre[\s>].*?</pre>)", re.DOTALL | re.IGNORECASE
)
_INLINE_CODE_RE = re.compile(r"(<code>)(.*?)(</code>)", re.DOTALL)
_MIN_WBR_LEN = 12  # only inject <wbr> in code spans longer than this


def _add_code_breaks(html: str) -> str:
    """Add <wbr> to inline <code> spans outside <pre> blocks."""
    parts = _PRE_SPLIT_RE.split(html)
    for i, part in enumerate(parts):
        # Odd-indexed parts are <pre>…</pre> — leave untouched.
        if i % 2 == 1:
            continue
        parts[i] = _INLINE_CODE_RE.sub(
            lambda m: (
                m.group(1) + _inject_wbr(m.group(2)) + m.group(3)
                if len(m.group(2)) >= _MIN_WBR_LEN
                else m.group(0)
            ),
            part,
        )
    return "".join(parts)


# Detect tables with many columns and wrap them for landscape rendering.
_TABLE_RE = re.compile(r"<table>(.*?)</table>", re.DOTALL)
_TH_COUNT_RE = re.compile(r"<th[ >]")
_WIDE_TABLE_THRESHOLD = 5  # columns that trigger landscape


def _tag_wide_tables(html: str) -> str:
    """Wrap tables with ≥5 columns in a landscape-page container."""
    def maybe_wrap(m: re.Match[str]) -> str:
        th_count = len(_TH_COUNT_RE.findall(m.group(1)))
        if th_count >= _WIDE_TABLE_THRESHOLD:
            return (
                '<div class="landscape-page">'
                f'<table class="wide-table">{m.group(1)}</table>'
                '</div>'
            )
        return m.group(0)
    return _TABLE_RE.sub(maybe_wrap, html)


# ---------------------------------------------------------------------------
# Fontconfig: reject Apple Color Emoji during PDF rendering
# ---------------------------------------------------------------------------
# On macOS, Pango/fontconfig selects Apple Color Emoji as a fallback for
# miscellaneous-symbol code points. WeasyPrint embeds a subset of the font
# but PDF viewers (Preview, Acrobat) can't extract the COLR/CBDT tables
# and emit "Cannot extract the embedded font" warnings.  We write a
# temporary fontconfig override that rejects that font family so Pango falls
# through to a text-symbol font (or .LastResort) instead.

import contextlib

_FONTCONFIG_REJECT_COLOR_EMOJI = """\
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">{system_conf}</include>
  <selectfont>
    <rejectfont>
      <pattern>
        <patelt name="family"><string>Apple Color Emoji</string></patelt>
      </pattern>
    </rejectfont>
  </selectfont>
</fontconfig>
"""


@contextlib.contextmanager
def _fontconfig_no_color_emoji():
    """Temporarily override FONTCONFIG_FILE to block Apple Color Emoji."""
    if sys.platform != "darwin":
        yield
        return

    # Locate the system fontconfig config to include as a base.
    system_conf = "/opt/homebrew/etc/fonts/fonts.conf"
    if not Path(system_conf).exists():
        system_conf = "/usr/local/etc/fonts/fonts.conf"
    if not Path(system_conf).exists():
        # Can't find system config — skip the override.
        yield
        return

    old_val = os.environ.get("FONTCONFIG_FILE")
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".conf", prefix="mdpub-fc-", delete=False
    )
    try:
        tmp.write(_FONTCONFIG_REJECT_COLOR_EMOJI.format(system_conf=system_conf))
        tmp.close()
        os.environ["FONTCONFIG_FILE"] = tmp.name
        yield
    finally:
        if old_val is None:
            os.environ.pop("FONTCONFIG_FILE", None)
        else:
            os.environ["FONTCONFIG_FILE"] = old_val
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


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
    """Render the cover-page HTML block. Inserted before the TOC in the body.

    Includes decorative ``<div>`` elements (cover-ornament, cover-rule-top,
    cover-rule-bottom) that themes can style for richer title pages.  Themes
    that don't target these selectors get the base layout only — zero visual
    regression.
    """
    parts = ['<header class="cover">']
    parts.append('  <div class="cover-ornament"></div>')
    parts.append(f'  <h1 class="cover-title">{html_escape(title)}</h1>')
    parts.append('  <div class="cover-rule-top"></div>')
    parts.append('  <div class="cover-rule"></div>')
    parts.append('  <div class="cover-rule-bottom"></div>')
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
    if attribution:
        parts.append(
            f'  <footer class="cover-attribution">{html_escape(attribution)}</footer>'
        )
    parts.append('</header>')
    return "\n".join(parts)


_COVER_BASE_CSS = """
/* Cover page — first page of the document, before the TOC. Named so we can
   suppress running header/footer chrome via @page cover. Per-theme styling
   (typography, color, decorative flourishes) lives in each theme dir's
   cover.css; the base block here only defines the universal layout. */
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
header.cover .cover-ornament {
    /* Decorative element above the title — hidden unless themed. */
    display: none;
}
header.cover .cover-title {
    margin: 2.6in 0 0.3in 0;
    line-height: 1.05;
    border: none;
    padding: 0;
}
header.cover .cover-rule-top,
header.cover .cover-rule-bottom {
    /* Extra rule elements for multi-rule compositions — hidden unless themed. */
    display: none;
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


def cover_css(theme_selection: ThemeSelection) -> str:
    """Assemble cover-page CSS for a build.

    Returns the universal base layout plus the theme's `cover.css` if one
    exists in the theme directory. User themes (including those generated
    by theme-advisor) can ship their own `cover.css` and it will load
    automatically. Themes without a cover.css get the base layout only —
    typography and color cascade in via the theme's main style.css.
    """
    theme_dir = theme_selection.css_path.parent
    per_theme_path = theme_dir / "cover.css"
    per_theme = per_theme_path.read_text(encoding="utf-8") if per_theme_path.exists() else ""
    return _COVER_BASE_CSS + per_theme


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
    md_text_raw = _normalize_emoji(md_text_raw)

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
    cover_attribution = front_matter.get("attribution") or ""

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
            "pymdownx.superfences",
            "pymdownx.highlight",
            "tables",
            "attr_list",
            "pymdownx.tilde",
            "smarty",
            "sane_lists",
        ],
        extension_configs={
            "pymdownx.highlight": {
                "css_class": "codehilite",
                "guess_lang": False,
                "linenums": False,
                "use_pygments": True,
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
    body_html = _add_code_breaks(body_html)
    body_html = _tag_wide_tables(body_html)
    body_html, headings = assign_heading_ids(body_html)
    toc_html = render_toc(headings)
    print(f"      headings collected: {len(headings)} "
          f"(h1={sum(1 for h in headings if h.level == 1)}, "
          f"h2={sum(1 for h in headings if h.level == 2)})")

    print("[5/6] assembling HTML document")
    print_css = css_path.read_text(encoding="utf-8")
    # Mode-aware syntax-highlight palette via the shared helper so PDF +
    # DOCX pipelines stay in sync — see lib.theme_loader.pygments_css_path.
    pygments_css = pygments_css_path(theme_selection).read_text(encoding="utf-8")
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
    with _fontconfig_no_color_emoji():
        HTML(string=full_html, base_url=str(runtime.plugin_root())).write_pdf(str(output))
    print(f"      PDF written: {output} "
          f"({output.stat().st_size // 1024} KB)")
    return output
