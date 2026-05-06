"""DOCX pipeline orchestration — mirror of lib/pipeline.py for .docx output.

Public entry: ``build_docx(*, source, output, theme_selection,
include_cover=True, build_dir=None) -> Path`` — same shape as
``lib.pipeline.build_pdf``.

Flow (mirrors md-publisher-docx-06-pydocx/docx/build.py:1-205):
  1. Read source markdown.
  2. Pre-render mermaid blocks to PNG via lib.mermaid_processor with
     ``output_format='png'`` (Phase 3.2 will switch to ``'dual'`` for the
     OOXML SVG+PNG embed); the processor leaves an ``<img
     class='mermaid-png' src='...'>`` marker that lib.docx_renderer
     consumes via its html_block branch.
  3. Create Document, set 0.75in margins ("Narrow" preset) on every
     section so cover + body both tighten — python-docx defaults to 1.0in.
  4. Apply per-theme styles (lib.docx_styles.apply_theme).
  5. Build the cover page (if requested), add a section break, re-set
     margins so the body section inherits Narrow.
  6. Insert the TOC field.
  7. Render the body (with mermaid markers already in place) via
     lib.docx_renderer.render_markdown_to_docx.
  8. Save to disk.

The first H1 is stripped from the body before rendering — the cover
already shows the title and the H1 would otherwise appear a second time
on page 2.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from docx import Document
from docx.shared import Inches

from . import mermaid_processor, runtime
from .docx_cover import add_cover_page, add_section_break
from .docx_renderer import render_markdown_to_docx
from .docx_styles import apply_theme
from .docx_toc import insert_toc
from .theme_loader import ThemeSelection, build_classdefs


_MARGIN_INCHES = 0.75


def build_docx(
    *,
    source: Path,
    output: Path,
    theme_selection: ThemeSelection,
    include_cover: bool = True,
    build_dir: Path | None = None,
) -> Path:
    """Render `source` markdown to a themed DOCX at `output`.

    Returns the absolute output path. Raises on any pipeline failure.
    """
    if theme_selection.palette is None or theme_selection.fonts is None:
        raise ValueError(
            f"build_docx requires a themed ThemeSelection (palette/fonts is "
            f"None — likely the 'default' theme was passed in; pass an "
            f"atlas/phosphor/arcade selection or a user theme with palette "
            f"+ fonts in spec.json). Got slug={theme_selection.slug!r}."
        )

    if build_dir is None:
        build_dir = output.parent / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    md_text = source.read_text(encoding="utf-8")

    title = _derive_title(md_text)
    body_md = _strip_first_h1(md_text)

    # Pre-render mermaid blocks to PNG. The preprocessor needs a real
    # python-markdown Markdown instance to attach to (it's a Preprocessor
    # subclass); we just borrow one for its run() method, even though we
    # parse with markdown-it-py downstream — the markers it emits are plain
    # HTML and the docx_renderer reads them via its html_block branch.
    body_md = _prerender_mermaid(body_md, theme_selection, build_dir)

    # Build the doc.
    doc = Document()
    _set_narrow_margins(doc)
    apply_theme(doc, theme_selection)

    # Cover (optional).
    if include_cover:
        subtitle, author, date, attribution = _extract_metadata(md_text)
        add_cover_page(
            doc, theme_selection,
            title=title, subtitle=subtitle,
            author=author, date=date, attribution=attribution,
        )
        add_section_break(doc)
        # Section break created a new section; re-apply Narrow margins.
        # python-docx inherits from section 1 in practice, but be explicit.
        _set_narrow_margins(doc)

    # TOC.
    insert_toc(doc, theme_selection)

    # Body (mermaid markers already in body_md; the renderer dispatches
    # them via its html_block branch -> _try_handle_mermaid_html).
    render_markdown_to_docx(doc, theme_selection, body_md)

    # Save.
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output))
    return output


def _set_narrow_margins(doc) -> None:
    """Apply 0.75in margins ("Narrow" preset) to every section.

    Mirrors md-publisher-docx-06-pydocx/docx/build.py::_set_narrow_margins.
    """
    for section in doc.sections:
        section.top_margin = Inches(_MARGIN_INCHES)
        section.bottom_margin = Inches(_MARGIN_INCHES)
        section.left_margin = Inches(_MARGIN_INCHES)
        section.right_margin = Inches(_MARGIN_INCHES)


def _prerender_mermaid(
    md_text: str, selection: ThemeSelection, build_dir: Path,
) -> str:
    """Replace ```mermaid fences with <img class='mermaid-png' ...> markers.

    Uses lib.mermaid_processor.MermaidPreprocessor in PNG mode; the
    accompanying MermaidPostprocessor unwraps any markdown <p> wrapping
    on the bare <img> markers, but here we run BEFORE markdown parsing
    so the postprocessor isn't needed — the preprocessor's placeholder
    tokens are stashed in self.figures, and we substitute them back in
    inline.
    """
    # Find mmdc; if not installed, surface a clear error early rather than
    # letting the preprocessor's subprocess call fail mid-build.
    mmdc = mermaid_processor.find_mmdc()

    # Borrow a python-markdown instance just to satisfy the Preprocessor's
    # constructor (it inherits from markdown.preprocessors.Preprocessor).
    from markdown import Markdown

    md = Markdown()
    pre = mermaid_processor.MermaidPreprocessor(
        md=md,
        mmdc_path=mmdc,
        build_dir=build_dir,
        mermaid_config=selection.mermaid_config_path,
        puppeteer_config=runtime.puppeteer_config(),
        classdef_lines=build_classdefs(selection),
        output_format="png",
    )
    # run() takes a list of lines, returns a list of lines with mermaid
    # fences replaced by placeholder tokens; figures dict carries the
    # placeholder -> <img> mapping.
    new_lines = pre.run(md_text.split("\n"))
    rendered = "\n".join(new_lines)

    # Substitute placeholders with the stashed <img> markers. The
    # placeholders sit on their own lines (the preprocessor wrapped them
    # in blank lines), so a plain string replace is sufficient — no <p>
    # wrapping concern here because we substitute pre-parse.
    for placeholder, img_html in pre.figures.items():
        rendered = rendered.replace(placeholder, img_html)
    return rendered


_H1_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _derive_title(md_text: str) -> str:
    """First H1 in the document → cover title.

    Skips fenced code blocks so a `# comment` line inside ```python ... ```
    doesn't get picked up.
    """
    in_fence = False
    for line in md_text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _H1_RE.match(line)
        if m:
            return m.group(1).strip()
    return "Untitled"


def _strip_first_h1(md_text: str) -> str:
    """Remove the first H1 + its line.

    The cover already shows the title — leaving the H1 in the body would
    cause it to appear a second time on page 2 of the document.
    """
    out_lines = []
    skipped = False
    for line in md_text.splitlines():
        if not skipped and re.match(r"^#\s+\S", line):
            skipped = True
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _extract_metadata(md_text: str) -> tuple[str, str, str, str]:
    """Return (subtitle, author, date, attribution) from YAML front matter.

    Title is derived separately via _derive_title (first H1 fallback).
    Keys recognized: subtitle, author, date, attribution. Anything missing
    falls back to a sensible default (today's date for date; empty for the
    rest). Tolerates simple ``key: value`` lines with optional quoting; no
    nested YAML structure.
    """
    subtitle = ""
    author = ""
    date = datetime.date.today().isoformat()
    attribution = ""

    fm = _FRONT_MATTER_RE.match(md_text)
    if fm:
        for line in fm.group(1).splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k == "subtitle":
                subtitle = v
            elif k == "author":
                author = v
            elif k == "date":
                date = v
            elif k == "attribution":
                attribution = v

    return subtitle, author, date, attribution
