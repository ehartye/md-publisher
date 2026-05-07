"""Theme resolution + spec loading for the md-publisher plugin.

Two levels of theme storage, checked in order:

1. **User themes** at `~/.md-publisher/themes/<slug>/` — created by the
   theme-advisor skill. Each user theme directory contains:
     - style.css           — WeasyPrint stylesheet
     - mermaid-config.json — mmdc theme config
     - spec.json           — single-theme spec block (ingress/core/transform/
                             bridge classDef colors, palette, fonts)
2. **Built-in themes** at `${CLAUDE_PLUGIN_ROOT}/themes/<slug>/` — ship with
   the plugin (atlas-light, atlas-dark, phosphor-light, phosphor-dark,
   arcade-light, arcade-dark, default). The classDef color data for built-in
   themes lives in the aggregate `themes/theme-spec.json`.

Slug convention:
  - Mode-aware themes: f"{name}-{mode}"  e.g. "atlas-light", "phosphor-dark"
  - Mode-less themes:  just "{name}"     e.g. "default"

Resolution order: user > built-in. Same-slug user theme overrides the
shipped one (intentional — lets a user iterate on a built-in's clone
without modifying plugin files).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import runtime


UNIVERSAL_TAGS = ("ingress", "core", "transform", "bridge")
DEFAULT_THEME_SLUG = "default"


@dataclass(frozen=True)
class Palette:
    bg: str          # page background
    paper: str       # secondary background (cards, code blocks if differs from page)
    ink: str         # primary text
    ink_soft: str    # secondary text (captions, meta, muted)
    accent: str      # primary accent (rules, links, code-block stripe)
    accent_alt: str  # secondary accent (arcade gradient end, callout backgrounds)
    rule: str        # horizontal rule color
    code_bg: str     # code block background
    code_text: str   # code block foreground
    table_stripe: str  # alternating-row background


@dataclass(frozen=True)
class Fonts:
    serif: str      # body in atlas; everything in phosphor; body in arcade
    sans: str       # accent type (TOC entries, eyebrows, captions, table headers)
    mono: str       # code blocks + inline code
    display: str    # cover title, h1/h2 in atlas/arcade


def _resolve_palette(family: str, mode_palette: dict) -> Palette:
    """Normalize a mode palette block from theme-spec.json into a uniform Palette.

    Atlas/Phosphor use a single `accent`; arcade exposes `accent1`/`accent2`/
    `accent3`. We pick `accent2` as primary for arcade (matches cover.css's
    hard offset shadow color, the most distinctive arcade signal); `accent1`
    becomes accent_alt.
    """
    p = mode_palette
    if family == "arcade":
        accent = p.get("accent2", p.get("accent1", "#000000"))
        accent_alt = p.get("accent1", accent)
    else:
        accent = p.get("accent", "#000000")
        # accentSoft picked over accentCool: the former is the cross-family
        # convention (atlas + phosphor both ship it); accentCool only exists
        # in phosphor and would force a per-family branch here for one role.
        accent_alt = p.get("accentSoft", accent)
    return Palette(
        bg=p.get("bg", "#FFFFFF"),
        paper=p.get("paper", p.get("bg", "#FFFFFF")),
        ink=p.get("ink", "#000000"),
        ink_soft=p.get("inkSoft", p.get("ink", "#000000")),
        accent=accent,
        accent_alt=accent_alt,
        rule=p.get("rule", accent),
        code_bg=p.get("codeBg", p.get("bg", "#F0F0F0")),
        code_text=p.get("codeText", p.get("ink", "#000000")),
        table_stripe=p.get("tableStripe", p.get("bg", "#FFFFFF")),
    )


def _resolve_fonts(family: str, fonts_block: dict) -> Fonts:
    """Pick serif/sans/mono/display per-family.

    The theme-spec uses different key sets per family (atlas: body/sansAccent/
    mono/display; phosphor: body for everything; arcade: body=Recursive,
    display2=Bungee, code=Recursive-mono-axis). Collapse to one shape.

    Arcade's mono is hard-overridden to JetBrains Mono per DECISION.md
    concession #2: Recursive's MONO axis is not reliably addressable via
    Word's <w:rFonts>.

    Invariant: `family` must be one of the three built-in families. Callers
    outside `resolve_selection()` (which guards on `name != DEFAULT_THEME_SLUG`
    and never invokes this for user themes) should ensure the same — user
    themes use `_fonts_from_user_spec` / `_fonts_from_css` instead.
    """
    if family == "atlas":
        return Fonts(
            serif=fonts_block["body"]["family"],
            sans=fonts_block["sansAccent"]["family"],
            mono=fonts_block["mono"]["family"],
            display=fonts_block["display"]["family"],
        )
    if family == "phosphor":
        plex = fonts_block["body"]["family"]
        return Fonts(serif=plex, sans=plex, mono=plex, display=plex)
    if family == "arcade":
        return Fonts(
            serif=fonts_block["body"]["family"],
            sans=fonts_block["display2"]["family"],
            mono="JetBrains Mono",
            display=fonts_block["display"]["family"],
        )
    raise ValueError(f"unknown theme family: {family}")


# --- User-theme palette/fonts helpers ---
#
# User themes (in ~/.md-publisher/themes/) may declare explicit `palette` and
# `fonts` blocks in their spec.json (v0.2+ scaffold), or pre-date v0.2 and only
# carry CSS custom properties on `:root`. We try the spec block first and fall
# back to scraping `:root { --bg: ...; --font-display: ...; }` from style.css.

_CSS_VAR_RE = re.compile(r"--([a-zA-Z0-9-]+)\s*:\s*([^;]+);")

# Map of CSS custom-property name -> Palette field. Multiple source names may
# map to the same destination; with the dict-comprehension below, **later
# entries win** when a theme declares more than one source for the same field.
# Ordering rationale:
#   - `accent`/`accent-soft` (atlas + phosphor convention) are listed AFTER
#     `accent2`/`accent1` (arcade convention) so a theme with `--accent`
#     wins over `--accent2` if it weirdly carries both. In practice arcade
#     themes only have `accent[123]` and atlas/phosphor only have `accent`,
#     so the choice is non-conflicting either way.
#   - `--accent2` is the "primary" arcade accent (matches `_resolve_palette`'s
#     arcade branch and cover.css's hard-offset shadow).
_PALETTE_VAR_MAP = {
    "bg": "bg", "paper": "paper", "ink": "ink", "ink-soft": "ink_soft",
    "accent1": "accent_alt", "accent2": "accent",   # arcade convention
    "accent": "accent", "accent-soft": "accent_alt",  # atlas/phosphor wins if both present
    "rule": "rule",
    "code-bg": "code_bg", "code-text": "code_text",
    "table-stripe": "table_stripe",
}

# Same last-write-wins semantics. `font-body` is the convention used by every
# bundled theme; `font-serif` is the convention encoded in this map's keys.
# Both alias to the same Fonts.serif slot. `font-display2` is arcade's
# Bungee-for-accents (matches `_resolve_fonts` arcade branch -> Fonts.sans).
# `font-heading` is the default theme's name for the display role.
_FONT_VAR_MAP = {
    "font-display2": "sans",            # arcade
    "font-heading": "display",          # default theme
    "font-body": "serif",               # bundled-theme convention
    "font-display": "display", "font-sans": "sans",
    "font-mono": "mono", "font-serif": "serif",
}


def _palette_from_css(css_path: Path) -> Palette:
    """Best-effort palette extraction from a theme's :root CSS custom properties.

    Used when a user theme's spec.json lacks an explicit `palette` block.
    Missing values get safe defaults; downstream prints a warning.
    """
    text = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    raw: dict[str, str] = {}
    for m in _CSS_VAR_RE.finditer(text):
        raw[m.group(1)] = m.group(2).strip()
    pdict = {dst: raw[src] for src, dst in _PALETTE_VAR_MAP.items() if src in raw}
    return Palette(
        bg=pdict.get("bg", "#FFFFFF"),
        paper=pdict.get("paper", pdict.get("bg", "#FFFFFF")),
        ink=pdict.get("ink", "#000000"),
        ink_soft=pdict.get("ink_soft", pdict.get("ink", "#000000")),
        accent=pdict.get("accent", "#000000"),
        accent_alt=pdict.get("accent_alt", pdict.get("accent", "#000000")),
        rule=pdict.get("rule", pdict.get("accent", "#CCCCCC")),
        code_bg=pdict.get("code_bg", "#F0F0F0"),
        code_text=pdict.get("code_text", "#000000"),
        table_stripe=pdict.get("table_stripe", pdict.get("bg", "#FFFFFF")),
    )


def _fonts_from_css(css_path: Path) -> Fonts:
    """Best-effort font extraction from CSS custom properties.

    Falls back to system stacks for any font role not declared.
    """
    text = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    raw: dict[str, str] = {}
    for m in _CSS_VAR_RE.finditer(text):
        raw[m.group(1)] = m.group(2).strip().split(",")[0].strip().strip("'\"")
    fdict = {dst: raw[src] for src, dst in _FONT_VAR_MAP.items() if src in raw}
    return Fonts(
        serif=fdict.get("serif", "serif"),
        sans=fdict.get("sans", "sans-serif"),
        mono=fdict.get("mono", "monospace"),
        display=fdict.get("display", fdict.get("serif", "serif")),
    )


def _palette_from_user_spec(block: dict) -> Palette:
    """Read a single-theme spec.json's palette block (snake_case keys)."""
    return Palette(
        bg=block.get("bg", "#FFFFFF"),
        paper=block.get("paper", block.get("bg", "#FFFFFF")),
        ink=block.get("ink", "#000000"),
        ink_soft=block.get("ink_soft", block.get("ink", "#000000")),
        accent=block.get("accent", "#000000"),
        accent_alt=block.get("accent_alt", block.get("accent", "#000000")),
        rule=block.get("rule", block.get("accent", "#CCCCCC")),
        code_bg=block.get("code_bg", "#F0F0F0"),
        code_text=block.get("code_text", "#000000"),
        table_stripe=block.get("table_stripe", block.get("bg", "#FFFFFF")),
    )


def _fonts_from_user_spec(block: dict) -> Fonts:
    """Read a single-theme spec.json's fonts block.

    Tolerates two shapes per role:
      - bare string:  `"serif": "Newsreader"`                  (v0.2+ scaffold)
      - dict wrapper: `"serif": {"family": "Newsreader", ...}` (pre-v0.2
                     bake-off shape; theme-spec.json's per-family fonts
                     blocks use this with optional weight/opsz siblings)
    Anything else falls back to a system-stack default rather than
    propagating a non-string into ThemeSelection.fonts.X (which would
    crash callers like python-docx's Font.name setter or .lower() casts).
    """
    return Fonts(
        serif=_read_font(block.get("serif"), default="serif"),
        sans=_read_font(block.get("sans"), default="sans-serif"),
        mono=_read_font(block.get("mono"), default="monospace"),
        display=_read_font(
            block.get("display", block.get("serif")), default="serif"
        ),
    )


def _read_font(value, *, default: str) -> str:
    """Unwrap legacy {family: 'X'} font specs to bare 'X'; pass strings through.

    Returns `default` for anything else (None, bool, list, dict-without-family).
    Used by `_fonts_from_user_spec` to absorb the schema drift between v0.2's
    bare-string convention and the bake-off's dict wrapper.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        family = value.get("family")
        return family if isinstance(family, str) else default
    return default


@dataclass(frozen=True)
class ThemeSelection:
    """A resolved theme + the asset paths it implies."""
    slug: str
    name: str            # "atlas", "phosphor", "arcade", or custom
    mode: str | None     # "light", "dark", or None for mode-less themes
    css_path: Path
    mermaid_config_path: Path
    is_user_theme: bool  # True if from ~/.md-publisher/themes/, False if built-in
    spec_block: dict | None  # single-theme spec block (None for "default")
    # New (DOCX-only consumers — existing PDF code ignores):
    palette: Palette | None = None
    fonts: Fonts | None = None


def _slug(name: str, mode: str | None) -> str:
    return f"{name}-{mode}" if mode else name


def _theme_dir_for(slug: str) -> tuple[Path, bool]:
    """Find a theme directory by slug. Returns (path, is_user_theme)."""
    user_dir = runtime.USER_THEMES_DIR / slug
    if user_dir.exists():
        return user_dir, True
    builtin_dir = runtime.plugin_root() / "themes" / slug
    if builtin_dir.exists():
        return builtin_dir, False
    raise FileNotFoundError(
        f"theme {slug!r} not found in user dir ({runtime.USER_THEMES_DIR}) "
        f"or built-in ({runtime.plugin_root() / 'themes'})"
    )


def _load_builtin_spec_block(name: str, mode: str | None) -> dict | None:
    """Pull a single-theme spec block from the aggregate built-in spec.

    Returns None for mode-less / no-spec-data themes (e.g. "default" — which
    uses neutral mermaid styling and doesn't need classDef injection).
    """
    aggregate = runtime.plugin_root() / "themes" / "theme-spec.json"
    if not aggregate.exists():
        return None
    spec = json.loads(aggregate.read_text(encoding="utf-8"))
    return spec.get("themes", {}).get(name)


def resolve_selection(
    *,
    name: str = DEFAULT_THEME_SLUG,
    mode: str | None = None,
) -> ThemeSelection:
    """Resolve (name, mode) to a ThemeSelection, raising if absent."""
    slug = _slug(name, mode) if name != DEFAULT_THEME_SLUG else DEFAULT_THEME_SLUG
    theme_dir, is_user = _theme_dir_for(slug)
    css_path = theme_dir / "style.css"
    mermaid_path = theme_dir / "mermaid-config.json"
    if not css_path.exists():
        raise FileNotFoundError(f"missing style.css in theme dir: {theme_dir}")
    if not mermaid_path.exists():
        raise FileNotFoundError(
            f"missing mermaid-config.json in theme dir: {theme_dir}"
        )

    if is_user:
        spec_path = theme_dir / "spec.json"
        spec_block = (
            json.loads(spec_path.read_text(encoding="utf-8"))
            if spec_path.exists() else None
        )
    else:
        spec_block = _load_builtin_spec_block(name, mode)

    palette: Palette | None = None
    fonts: Fonts | None = None
    if is_user:
        # User theme: spec.json may have top-level palette + fonts blocks.
        if spec_block and "palette" in spec_block:
            palette = _palette_from_user_spec(spec_block["palette"])
        else:
            palette = _palette_from_css(css_path)
        if spec_block and "fonts" in spec_block:
            fonts = _fonts_from_user_spec(spec_block["fonts"])
        else:
            fonts = _fonts_from_css(css_path)
    else:
        # Built-in: use the per-family resolver against the aggregate spec.
        if spec_block:
            if spec_block.get("fonts"):
                try:
                    fonts = _resolve_fonts(name, spec_block["fonts"])
                except (ValueError, KeyError):
                    # Fallback for families not explicitly handled (e.g. default)
                    fonts = _fonts_from_css(css_path)
            else:
                fonts = _fonts_from_css(css_path)
            # Resolve palette: use explicit mode, or fall back to "light" for
            # mode-less themes (e.g. default) that still declare modes.light.
            effective_mode = mode or "light"
            if "modes" in spec_block and effective_mode in spec_block["modes"]:
                palette = _resolve_palette(name, spec_block["modes"][effective_mode])
            else:
                palette = _palette_from_css(css_path)

    return ThemeSelection(
        slug=slug,
        name=name,
        mode=mode,
        css_path=css_path,
        mermaid_config_path=mermaid_path,
        is_user_theme=is_user,
        spec_block=spec_block,
        palette=palette,
        fonts=fonts,
    )


def pygments_css_path(selection: ThemeSelection) -> Path:
    """Return the right Pygments stylesheet for this selection's mode.

    Both pipelines (PDF via lib.pipeline, DOCX via lib.docx_syntax) need
    the same mode -> file mapping; centralizing it here means renaming
    the dark file or adding a third style only updates one place.

    Falls back to the light file when the dark file isn't installed —
    forward-compat for users mid-upgrade.
    """
    themes_dir = runtime.plugin_root() / "themes"
    if selection.mode == "dark":
        dark = themes_dir / "pygments-dark.css"
        if dark.exists():
            return dark
    return themes_dir / "pygments.css"


def build_classdefs(selection: ThemeSelection) -> list[str]:
    """Build mermaid `classDef <tag> <props>` lines for the four universal tags.

    Returns [] when the selection has no spec block (e.g. the "default"
    theme — no per-tag styling, mermaid renders with its built-in palette).
    """
    if selection.spec_block is None:
        return []
    mode = selection.mode
    if mode is None:
        # Mode-less theme — try to find a single 'tagStyling' block; otherwise []
        tag_styling = (
            selection.spec_block.get("mermaid", {}).get("tagStyling", {})
        )
    else:
        raw_tag_styling = (
            selection.spec_block.get("mermaid", {}).get("tagStyling", {})
        )
        # Built-in themes nest by mode: tagStyling.{mode}.{tag}
        # User themes (per-mode dirs) are flat: tagStyling.{tag}
        tag_styling = raw_tag_styling.get(mode, raw_tag_styling)
        # If we got the mode sub-dict, use it; otherwise the flat dict
        # itself is the tag styling (check by seeing if a universal tag
        # key exists at the top level).
        if isinstance(tag_styling, dict) and not any(
            k in tag_styling for k in UNIVERSAL_TAGS
        ):
            tag_styling = {}
    if not tag_styling:
        return []
    lines: list[str] = []
    for tag in UNIVERSAL_TAGS:
        style = tag_styling.get(tag)
        if not style:
            continue
        parts = [
            f"fill:{style['fill']}",
            f"stroke:{style['stroke']}",
            f"color:{style['color']}",
            f"stroke-width:{style['strokeWidth']}px",
        ]
        if "strokeDasharray" in style:
            parts.append(f"stroke-dasharray:{style['strokeDasharray']}")
        lines.append(f"classDef {tag} {','.join(parts)}")
    return lines


def list_available_themes() -> list[dict]:
    """Enumerate every theme available to the plugin (built-in + user).

    Returned dicts have keys: slug, name, mode (str|None), source ("user"|
    "builtin"), path. Used by the theme-gallery skill to render its
    combined view.
    """
    seen: dict[str, dict] = {}
    # Built-in first so user themes can override by slug
    for entry in (runtime.plugin_root() / "themes").iterdir() if (runtime.plugin_root() / "themes").exists() else []:
        if entry.is_dir() and (entry / "style.css").exists():
            seen[entry.name] = {
                "slug": entry.name,
                "name": _name_from_slug(entry.name)[0],
                "mode": _name_from_slug(entry.name)[1],
                "source": "builtin",
                "path": entry,
            }
    if runtime.USER_THEMES_DIR.exists():
        for entry in runtime.USER_THEMES_DIR.iterdir():
            if entry.is_dir() and (entry / "style.css").exists():
                seen[entry.name] = {
                    "slug": entry.name,
                    "name": _name_from_slug(entry.name)[0],
                    "mode": _name_from_slug(entry.name)[1],
                    "source": "user",
                    "path": entry,
                }
    return sorted(seen.values(), key=lambda t: (t["source"], t["slug"]))


def _name_from_slug(slug: str) -> tuple[str, str | None]:
    """Split 'atlas-light' -> ('atlas', 'light'); 'default' -> ('default', None)."""
    if slug.endswith("-light"):
        return slug[:-len("-light")], "light"
    if slug.endswith("-dark"):
        return slug[:-len("-dark")], "dark"
    return slug, None
