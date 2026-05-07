"""Markdown extension that pre-renders ```mermaid fenced blocks to inline SVG.

WeasyPrint cannot execute JavaScript, so mermaid source must be rasterized
to vector before WeasyPrint sees the document. mmdc (mermaid-cli) does this
via headless Chromium and emits an SVG with real `<text>` elements when
configured with `htmlLabels: false` — that is what keeps the diagrams
searchable in the final PDF.

The processor:
  1. Intercepts ```mermaid blocks during the markdown pre-processing pass
     (before fenced_code sees them, so they aren't lowered to <pre>)
  2. Hashes the source so identical diagrams are only rendered once
  3. Shells out to mmdc to produce build/diagrams/<hash>.svg
  4. Inlines the SVG into the HTML wrapped in <figure class="mermaid">
  5. Marks figures with >= OVERSIZED_NODE_THRESHOLD nodes as .oversized so
     CSS can give them their own page (the multi-head attention diagram)
  6. Strips the explicit width/height from the root <svg> so the CSS
     max-width/max-height clamps actually take effect (otherwise mmdc's
     pixel dimensions win and the figure overflows the printable area)
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from markdown import Markdown
from markdown.preprocessors import Preprocessor
from markdown.extensions import Extension


# Any flowchart with at least this many node identifiers gets the .oversized
# class. The corpus's multi-head diagram has ~33 distinct node ids; the next
# largest (encoder/decoder flow) has ~12. The threshold sits comfortably
# between them.
OVERSIZED_NODE_THRESHOLD = 25

# Each rendered figure is tagged with a placeholder string during the
# preprocessor pass and substituted back in after markdown finishes.
PLACEHOLDER_PREFIX = "MERMAID_PLACEHOLDER_"


class MermaidPreprocessor(Preprocessor):
    """Replace ```mermaid blocks with placeholder tokens; render to SVG.

    The HTML substitution happens in two passes because python-markdown
    operates line-by-line in preprocessors but the final HTML assembly
    happens in postprocessors. We do the rendering up-front (since it's
    expensive — each call shells out to Node + Chromium) and stash the
    finished `<figure>` markup keyed by placeholder.

    When ``classdef_lines`` is non-empty, each line is prepended into every
    mermaid block immediately after the diagram-type line (e.g. the
    ``flowchart TD`` header). This lets a themed build inject the four
    universal tag classDefs (ingress / core / transform / bridge) so that
    the :::tag annotations already present in the themed corpus pick up
    the per-theme styling. Diagrams that don't contain ``classDef`` slots
    (e.g. ``sequenceDiagram``) get the lines skipped — they fall back to
    the mermaid-config.json themeVariables instead.
    """

    FENCE_RE = re.compile(
        r"^([ \t]*)```mermaid[ \t]*\n(.*?)\n[ \t]*```[ \t]*$",
        re.MULTILINE | re.DOTALL,
    )

    # Diagram-type tokens that support ``classDef``. Anything outside this
    # set (sequenceDiagram, erDiagram, gantt, pie, etc.) gets no
    # classDef injection — those diagram types are themed via
    # mermaid-config.json's themeVariables.
    CLASSDEF_DIAGRAMS = (
        "flowchart", "graph",
        "stateDiagram-v2", "stateDiagram",
        "classDiagram", "classDiagram-v2",
        "mindmap",
    )

    def __init__(self, md: Markdown, mmdc_path: Path, build_dir: Path,
                 mermaid_config: Path, puppeteer_config: Path,
                 classdef_lines: list[str] | None = None,
                 *, output_format: str = "svg"):
        super().__init__(md)
        if output_format not in ("svg", "png", "dual"):
            raise ValueError(
                f"output_format must be svg|png|dual, got {output_format!r}"
            )
        self.mmdc_path = mmdc_path
        self.build_dir = build_dir
        self.mermaid_config = mermaid_config
        self.puppeteer_config = puppeteer_config
        self.classdef_lines = list(classdef_lines or [])
        # "svg" — inline SVG into <figure> for WeasyPrint (default, PDF path).
        # "png" — emit a placeholder <img class="mermaid-png" src=...> for the
        #         DOCX renderer to extract and embed via python-docx.
        # "dual" — render SVG via mmdc + emit a marker the docx renderer
        #          recognizes; cairosvg-based rasterize-to-PNG lands in Task 3.2.
        self.output_format = output_format
        # populated as fences are found; keyed by placeholder, value is
        # the literal <figure>...</figure> HTML to splice back in (SVG mode)
        self.figures: dict[str, str] = {}
        self.build_dir.mkdir(parents=True, exist_ok=True)
        # Read primary text color from mermaid config for foreignObject fix
        self._primary_text_color = self._read_text_color(mermaid_config)

    def run(self, lines: list[str]) -> list[str]:
        text = "\n".join(lines)

        def replace(match: re.Match[str]) -> str:
            indent = match.group(1)
            source = match.group(2)
            placeholder = self._render_block(source)
            # The placeholder must live on its own line surrounded by blank
            # lines so markdown treats it as a block-level passthrough
            # (raw HTML protected by the post-processor).
            return f"\n\n{indent}{placeholder}\n\n"

        new_text = self.FENCE_RE.sub(replace, text)
        return new_text.split("\n")

    def _render_block(self, source: str) -> str:
        is_oversized = self._count_nodes(source) >= OVERSIZED_NODE_THRESHOLD

        # Oversized top-down flowcharts produce extremely wide layouts
        # (every node at the same level lands in a single row). Rewriting
        # the direction to LR produces a tall layout that fits a portrait
        # page much better. We document this transformation in the README;
        # it's the same source semantically, just laid out differently.
        render_source = source
        if is_oversized:
            render_source = self._maybe_rewrite_direction(source)

        # Inject per-theme classDef lines so the :::tag annotations in the
        # themed corpus actually resolve to the per-theme palette. We do
        # this after direction rewriting (the regex looks at the diagram
        # header, which is what classdef injection also keys off of).
        if self.classdef_lines:
            render_source = self._inject_classdefs(render_source)

        digest = hashlib.sha256(render_source.encode("utf-8")).hexdigest()[:16]
        mmd_path = self.build_dir / f"{digest}.mmd"

        if self.output_format == "png":
            png_path = self.build_dir / f"{digest}.png"
            if not png_path.exists():
                mmd_path.write_text(render_source, encoding="utf-8")
                self._invoke_mmdc(mmd_path, png_path, fmt="png")
            # PNG path is for DOCX consumers. Route through the same
            # placeholder/figures-dict roundtrip as SVG mode so the
            # postprocessor unwraps any <p>...</p> markdown wraps around
            # us — keeps PNG and SVG modes structurally identical from
            # the consumer's perspective (no surprise <p> wrapper to
            # work around in the docx renderer's HTML walker).
            img_html = f"<img class='mermaid-png' src='{png_path.as_uri()}' />"
            placeholder = f"{PLACEHOLDER_PREFIX}{digest}"
            self.figures[placeholder] = img_html
            return placeholder

        if self.output_format == "dual":
            # Render SVG once via mmdc; rasterize to PNG via cairosvg in
            # Task 3.2. For Phase 1 we emit a marker carrying the SVG path;
            # the docx renderer's dual-embed branch reads it and synthesizes
            # the PNG companion when 3.2 lands.
            #
            # Dual-mode HTML is DOCX-only — never feed to a browser/PDF
            # renderer. The empty src='' on the rendered <img> is intentional:
            # makes accidental misuse fail visibly (broken-image icon)
            # rather than silently render nothing.
            svg_path = self.build_dir / f"{digest}.svg"
            if not svg_path.exists():
                mmd_path.write_text(render_source, encoding="utf-8")
                self._invoke_mmdc(mmd_path, svg_path, fmt="svg")
            img_html = (
                f"<img class='mermaid-dual' src='' "
                f"data-svg='{svg_path.as_uri()}' />"
            )
            placeholder = f"{PLACEHOLDER_PREFIX}{digest}"
            self.figures[placeholder] = img_html
            return placeholder

        # output_format == "svg" (default — PDF path).
        svg_path = self.build_dir / f"{digest}.svg"
        if not svg_path.exists():
            mmd_path.write_text(render_source, encoding="utf-8")
            self._invoke_mmdc(mmd_path, svg_path, fmt="svg")

        svg = svg_path.read_text(encoding="utf-8")
        cleaned_svg = self._strip_explicit_dimensions(svg)
        cleaned_svg = self._inject_nodelabel_color(cleaned_svg)
        css_class = "mermaid oversized" if is_oversized else "mermaid"

        figure_html = (
            f'<figure class="{css_class}">'
            f'{cleaned_svg}'
            f'</figure>'
        )

        placeholder = f"{PLACEHOLDER_PREFIX}{digest}"
        self.figures[placeholder] = figure_html
        return placeholder

    def _invoke_mmdc(
        self, mmd_path: Path, out_path: Path, *, fmt: str = "svg",
    ) -> None:
        """Run mmdc to convert .mmd to .svg or .png via `node <mmdc-cli.js>`.

        We invoke node directly against mmdc's source entry point rather
        than going through the .cmd shim. This avoids:
          - shell=True on Windows (which is fragile if any path has a
            space — the shim args don't survive the shell parsing)
          - the .cmd-vs-bash-script split between platforms
          - puppeteer auto-detecting bundled Chromium incorrectly when
            launched through a wrapper

        --configFile passes mermaid theme/layout knobs; --puppeteerConfigFile
        passes Chromium launch args (sandbox enabled by default; see
        runtime.puppeteer_config()).

        mmdc detects output format from `out_path`'s extension. PNG mode
        adds `-s 3` for HiDPI rendering (needed for DOCX print-quality
        embedding); SVG mode does not need it because the output is vector.
        """
        cmd = [
            "node",
            str(self.mmdc_path),
            "-i", str(mmd_path),
            "-o", str(out_path),
            "-b", "transparent",
            "--configFile", str(self.mermaid_config),
            "--puppeteerConfigFile", str(self.puppeteer_config),
        ]
        if fmt == "png":
            cmd += ["-s", "3"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            sys.stderr.write(
                f"mmdc failed for {mmd_path.name}\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}\n"
            )
            raise RuntimeError(f"mmdc returned exit code {result.returncode}")
        if not out_path.exists():
            raise RuntimeError(f"mmdc reported success but {out_path} is missing")

    @staticmethod
    def _strip_explicit_dimensions(svg: str) -> str:
        """Remove width="..." and height="..." from the root <svg>.

        mmdc emits something like:
          <svg ... width="1234" height="567" viewBox="0 0 1234 567">
        With the explicit pixel dimensions present, CSS max-width / max-height
        do nothing — the SVG renders at its intrinsic pixel size and overflows
        the printable area. Stripping them leaves only viewBox, which lets
        the CSS clamps actually scale the figure to fit.
        """
        # Match only the root <svg> tag. The first <svg occurrence is the
        # outer one; child <svg> nodes inside are uncommon for mmdc output
        # but we restrict to the first match to be safe.
        def fix_root_svg(match: re.Match[str]) -> str:
            tag = match.group(0)
            tag = re.sub(r'\s+width="[^"]*"', '', tag, count=1)
            tag = re.sub(r'\s+height="[^"]*"', '', tag, count=1)
            return tag

        return re.sub(r'<svg\b[^>]*>', fix_root_svg, svg, count=1)

    @staticmethod
    def _read_text_color(config_path: Path) -> str | None:
        """Extract primaryTextColor from mermaid-config.json."""
        try:
            import json
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            return cfg.get("themeVariables", {}).get("primaryTextColor")
        except Exception:
            return None

    def _inject_nodelabel_color(self, svg: str) -> str:
        """Inject inline color on foreignObject HTML content.

        Mermaid's ER/class/state diagrams use <foreignObject> with HTML
        <span class="nodeLabel"> for text. WeasyPrint does NOT apply CSS
        rules from SVG <style> blocks to HTML inside foreignObject, so we
        must inject inline style="color:..." on the container divs.
        """
        if "foreignObject" not in svg or not self._primary_text_color:
            return svg
        color = self._primary_text_color

        def _add_color_to_div(match: re.Match[str]) -> str:
            """Prepend color into the existing style attribute of the div."""
            full = match.group(0)
            # If div already has a style attribute, prepend color into it
            if 'style="' in full:
                return re.sub(
                    r'style="',
                    f'style="color:{color}; ',
                    full, count=1,
                )
            # No style attribute — add one
            return full.replace("<div", f'<div style="color:{color}"', 1)

        # Match foreignObject -> div (with any attributes in between)
        svg = re.sub(
            r'<foreignObject[^>]*>\s*<div[^>]*>',
            _add_color_to_div,
            svg,
        )
        return svg

    def _inject_classdefs(self, source: str) -> str:
        """Insert classDef lines after the diagram-type header.

        Mermaid expects classDef declarations inside the diagram body. The
        themed corpus already contains :::ingress / :::core / :::transform
        / :::bridge annotations on nodes; without matching classDef lines
        in the same block, mermaid renders them in its default color. By
        prepending the theme-derived classDef lines right after the
        ``flowchart TD`` (or similar) header, every annotated node picks
        up the theme palette automatically.

        We only touch diagrams whose first non-empty line starts with one
        of CLASSDEF_DIAGRAMS — sequenceDiagram and friends ignore classDef
        and would produce a syntax error if we tried.
        """
        lines = source.split("\n")
        header_idx = None
        for i, raw in enumerate(lines):
            stripped = raw.strip()
            if not stripped:
                continue
            first_token = stripped.split()[0]
            if first_token in self.CLASSDEF_DIAGRAMS:
                header_idx = i
            break

        if header_idx is None:
            return source  # not a flowchart/graph; leave alone

        # Match the indent of the first body line (or fall back to four
        # spaces, which is what the corpus uses) so the injected classDef
        # lines align visually in the cached .mmd file.
        indent = "    "
        for raw in lines[header_idx + 1:]:
            if raw.strip():
                # use the same leading whitespace as the first body line
                indent = raw[: len(raw) - len(raw.lstrip())] or indent
                break

        insert_at = header_idx + 1
        injected = [f"{indent}{line}" for line in self.classdef_lines]
        return "\n".join(lines[:insert_at] + injected + lines[insert_at:])

    @staticmethod
    def _maybe_rewrite_direction(source: str) -> str:
        """Rewrite TD/TB flowcharts to LR for oversized diagrams.

        A top-down flowchart with many parallel branches at one level
        renders as a single very-wide row that wraps badly to a printable
        page. LR (left-right) inverts the axes — many parallel branches
        become a tall column instead, which fits a portrait page after
        the max-height clamp scales it down. The diagram semantics don't
        change; only the layout direction does.
        """
        return re.sub(
            r'^(\s*flowchart\s+)(TD|TB)\b',
            r'\1LR',
            source,
            count=1,
            flags=re.MULTILINE,
        )

    @staticmethod
    def _count_nodes(source: str) -> int:
        """Estimate distinct nodes referenced in a mermaid flowchart.

        We only need to distinguish "small/medium" diagrams from the
        deliberately oversized multi-head figure. The signal we trust is
        the count of distinct node identifiers — the short tokens like
        `A`, `H1Q`, `C` that appear on edges. We deliberately strip out
        anything inside brackets (labels), parentheses, braces, quotes,
        and skip mermaid keywords to avoid counting label words as nodes.
        SequenceDiagrams have very few identifiers so they are always
        under-threshold.
        """
        # Strip out bracketed labels: [text], (text), {text}, "text", and
        # everything after `%%` (mermaid comments). This leaves only the
        # bare identifier graph topology.
        stripped = source
        stripped = re.sub(r'\[[^\]]*\]', '', stripped)
        stripped = re.sub(r'\([^)]*\)', '', stripped)
        stripped = re.sub(r'\{[^}]*\}', '', stripped)
        stripped = re.sub(r'"[^"]*"', '', stripped)
        stripped = re.sub(r'%%[^\n]*', '', stripped)

        skip = {
            "flowchart", "graph", "sequenceDiagram", "TD", "TB", "LR", "RL", "BT",
            "subgraph", "end", "participant", "actor", "Note", "note", "loop",
            "alt", "opt", "par", "and", "rect",
        }
        tokens = re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\b', stripped)
        unique = {t for t in tokens if t not in skip}
        return len(unique)


class MermaidPostprocessor:
    """Splice rendered <figure> HTML back into the final document.

    Markdown's preprocessor produced placeholder tokens that survive the
    parser as plain text. We do the substitution after html generation
    so we don't fight markdown's HTML escaping and inline-handling.
    """

    def __init__(self, figures: dict[str, str]):
        self.figures = figures

    def __call__(self, html: str) -> str:
        for placeholder, figure_html in self.figures.items():
            # The placeholder may have been wrapped in <p>...</p> by markdown
            # because it sat on its own line. Match either form.
            patterns = [
                f"<p>{placeholder}</p>",
                placeholder,
            ]
            for pattern in patterns:
                if pattern in html:
                    html = html.replace(pattern, figure_html, 1)
                    break
        return html


class MermaidExtension(Extension):
    """Plugs the mermaid preprocessor into a python-markdown instance."""

    def __init__(self, mmdc_path: Path, build_dir: Path,
                 mermaid_config: Path, puppeteer_config: Path,
                 classdef_lines: list[str] | None = None,
                 *, output_format: str = "svg"):
        super().__init__()
        self.mmdc_path = mmdc_path
        self.build_dir = build_dir
        self.mermaid_config = mermaid_config
        self.puppeteer_config = puppeteer_config
        self.classdef_lines = list(classdef_lines or [])
        self.output_format = output_format
        self.preprocessor: MermaidPreprocessor | None = None

    def extendMarkdown(self, md: Markdown) -> None:
        self.preprocessor = MermaidPreprocessor(
            md, self.mmdc_path, self.build_dir,
            self.mermaid_config, self.puppeteer_config,
            classdef_lines=self.classdef_lines,
            output_format=self.output_format,
        )
        # Priority 50 puts us before fenced_code (priority 25). Higher
        # priority preprocessors run first.
        md.preprocessors.register(self.preprocessor, "mermaid", 50)


def find_mmdc() -> Path:
    """Locate mermaid-cli's main module (invoked as `node <path>`).

    Returns the path to mermaid-cli's `cli.js` so callers can invoke it
    via `node <cli.js>` — robust across platforms (no .cmd shim parsing,
    no shell=True needed). The plugin bootstrap installs mermaid-cli at
    ~/.md-publisher/runtime/node_modules/@mermaid-js/mermaid-cli/.
    """
    from . import runtime
    cli = runtime.mmdc_entry()
    if cli.exists():
        return cli
    raise FileNotFoundError(
        f"mermaid-cli not found at {cli}. "
        "Run /md-publisher:publish on any markdown file to trigger first-run "
        "bootstrap, or `python runtime/bootstrap.py` directly from the plugin."
    )
