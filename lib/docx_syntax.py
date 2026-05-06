"""Pygments-driven syntax highlighting for DOCX code fences.

Strategy
--------
The plugin's WeasyPrint themes share a single ``themes/pygments.css`` file
that maps Pygments token short-classes (``.k``, ``.s``, ``.nf``, ...) to
``color`` / ``font-weight`` / ``font-style`` declarations. We:

1. Parse that CSS once (per mode) into ``{short_class: {color, bold, italic}}``.
2. Lex each fenced code block as a whole (line-by-line lexing breaks
   tokenizers that span lines, e.g. multi-line strings or block comments).
3. Yield ``(text, color, bold, italic)`` tuples to the renderer, with
   ``"\n"`` text used as a sentinel for soft line breaks so the caller
   can emit ``<w:br/>`` between source lines while keeping the entire
   fence in one Word paragraph (one shaded rectangle, one accent rule).

When the fence has no language hint or an unknown lexer, we yield one
plain (uncolored) tuple per line, separated by ``"\n"`` sentinels — the
soft-line-break behavior is preserved so even unhighlighted fences get
the single-paragraph treatment.

Per-mode palette
----------------
``get_pygments_palette(mode)`` returns the right palette for the
selection's mode. ``mode='dark'`` loads ``themes/pygments-dark.css`` if
present; otherwise (or for any other mode) falls back to ``pygments.css``.
Palettes are cached by mode key, so per-process load cost is one parse
per mode.
"""

from __future__ import annotations

import re
from pathlib import Path

from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import STANDARD_TYPES
from pygments.util import ClassNotFound

from . import runtime


PYGMENTS_CSS = runtime.plugin_root() / "themes" / "pygments.css"

# Match either ``.codehilite .X { ... }`` (the file's actual prefix) or a
# plain ``.X { ... }`` rule. We capture the short class name and the body.
_PYG_RULE_RE = re.compile(
    r"\.(?:codehilite\s+)?\.([a-zA-Z0-9_-]+)\s*\{([^}]*)\}"
)
_COLOR_RE = re.compile(r"color:\s*(#[0-9A-Fa-f]{3,8})")
_BOLD_RE = re.compile(r"font-weight:\s*bold")
_ITALIC_RE = re.compile(r"font-style:\s*italic")


def _expand_short_color(hex_color: str) -> str:
    """Expand a 3-char hex (#abc) to 6-char (#aabbcc); leave 6/8-char alone."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return "#" + h[:6].upper()


def _load_pygments_palette(css_path: Path) -> dict[str, dict]:
    """Parse a pygments.css file into ``{short_class: {color, bold, italic}}``.

    Missing CSS file is tolerated (we return ``{}`` and the highlighter
    falls back to plain runs); a missing color simply yields ``None`` so
    the renderer leaves the run uncolored.
    """
    if not css_path.exists():
        return {}
    text = css_path.read_text(encoding="utf-8")
    palette: dict[str, dict] = {}
    for m in _PYG_RULE_RE.finditer(text):
        cls, body = m.group(1), m.group(2)
        c_match = _COLOR_RE.search(body)
        palette[cls] = {
            "color": _expand_short_color(c_match.group(1)) if c_match else None,
            "bold": bool(_BOLD_RE.search(body)),
            "italic": bool(_ITALIC_RE.search(body)),
        }
    return palette


# Module-level cache keyed by mode so we parse each file at most once per process.
_PALETTES: dict[str, dict[str, dict]] = {}


def get_pygments_palette(mode: str | None = None) -> dict[str, dict]:
    """Return ``{short_class: {color, bold, italic}}`` for the given mode.

    ``mode='dark'`` loads ``themes/pygments-dark.css`` if present; otherwise
    (or for any other mode) falls back to ``pygments.css``. Phase 3 ships
    ``pygments-dark.css`` so dark-mode themes get readable code blocks.
    """
    key = mode or "light"
    if key not in _PALETTES:
        if key == "dark":
            dark_path = runtime.plugin_root() / "themes" / "pygments-dark.css"
            css_path = dark_path if dark_path.exists() else PYGMENTS_CSS
        else:
            css_path = PYGMENTS_CSS
        _PALETTES[key] = _load_pygments_palette(css_path)
    return _PALETTES[key]


def _style_for_token(ttype, palette: dict[str, dict]) -> dict:
    """Walk the Pygments token hierarchy until we find a class with style.

    ``STANDARD_TYPES`` returns the short CSS class for a leaf token. If we
    don't find a matching palette entry for the leaf, we walk up the
    token's ancestors (``.parent``) — Pygments tokens form a tree, and
    e.g. ``Name.Function.Magic`` should fall back to ``Name.Function``
    then ``Name`` if the most-specific class isn't styled.
    """
    cur = ttype
    while cur is not None:
        cls = STANDARD_TYPES.get(cur, "")
        if cls and cls in palette:
            return palette[cls]
        cur = cur.parent
    return {}


# Sentinel emitted between source lines — the renderer turns this into a
# Word soft line break (<w:br/>) inside the same paragraph.
LINE_BREAK = "\n"


def iter_highlighted_tokens(content: str, lang: str, mode: str | None = None):
    """Yield ``(text, color, bold, italic)`` tuples for one code fence.

    ``text == LINE_BREAK`` is the line-break sentinel; the caller emits a
    ``<w:br/>`` instead of a text run for those.

    If ``lang`` is empty/unknown, falls back to one plain token per line
    (still with ``LINE_BREAK`` sentinels between them) so the renderer
    keeps emitting a single paragraph regardless of highlighting status.

    ``mode`` selects which Pygments CSS palette to use ('light' / 'dark').
    """
    content = content.rstrip("\n")
    palette = get_pygments_palette(mode)

    lexer = None
    if lang:
        try:
            lexer = get_lexer_by_name(lang.strip())
        except ClassNotFound:
            lexer = None

    if lexer is None:
        # No highlighting — one run per line, with sentinels between.
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if i > 0:
                yield (LINE_BREAK, None, False, False)
            if line:
                yield (line, None, False, False)
        return

    # Lex the WHOLE block so multi-line tokens (docstrings, block
    # comments, heredocs) are tokenized correctly.
    for ttype, text in lex(content, lexer):
        if not text:
            continue
        style = _style_for_token(ttype, palette)
        color = style.get("color")
        bold = style.get("bold", False)
        italic = style.get("italic", False)
        # A single Pygments token may contain newlines — split so each
        # newline becomes a LINE_BREAK sentinel in the stream.
        parts = text.split("\n")
        for j, part in enumerate(parts):
            if j > 0:
                yield (LINE_BREAK, None, False, False)
            if part:
                yield (part, color, bold, italic)
