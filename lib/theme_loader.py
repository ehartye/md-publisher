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


# --- User-theme palette/fonts helpers — implementations land in Task 1.2 ---
#
# These stubs exist so that `resolve_selection()` for a user theme raises a
# clear, actionable error today rather than NameError. Task 1.2 replaces each
# body with a real implementation (CSS extraction + spec.json reader); the
# signatures and call sites stay identical.
_USER_THEME_HELPER_TODO = (
    "user-theme palette/fonts arrives in Task 1.2 of the v0.2 productization "
    "plan (docs/superpowers/plans/2026-05-06-docx-productization-implementation.md). "
    "Until then, only built-in themes (atlas/phosphor/arcade) populate "
    "ThemeSelection.palette and .fonts."
)


def _palette_from_user_spec(block: dict) -> Palette:
    raise NotImplementedError(_USER_THEME_HELPER_TODO)


def _palette_from_css(css_path: Path) -> Palette:
    raise NotImplementedError(_USER_THEME_HELPER_TODO)


def _fonts_from_user_spec(block: dict) -> Fonts:
    raise NotImplementedError(_USER_THEME_HELPER_TODO)


def _fonts_from_css(css_path: Path) -> Fonts:
    raise NotImplementedError(_USER_THEME_HELPER_TODO)


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
    if name != DEFAULT_THEME_SLUG:
        if is_user:
            # User theme: spec.json may have top-level palette + fonts blocks.
            if spec_block and "palette" in spec_block:
                palette = _palette_from_user_spec(spec_block["palette"])
            else:
                palette = _palette_from_css(css_path)  # CSS fallback (Task 1.2)
            if spec_block and "fonts" in spec_block:
                fonts = _fonts_from_user_spec(spec_block["fonts"])
            else:
                fonts = _fonts_from_css(css_path)
        else:
            # Built-in: use the per-family resolver against the aggregate spec.
            if spec_block:
                fonts = _resolve_fonts(name, spec_block["fonts"])
                if mode and "modes" in spec_block:
                    palette = _resolve_palette(name, spec_block["modes"][mode])

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
        tag_styling = (
            selection.spec_block.get("mermaid", {}).get("tagStyling", {}).get(mode, {})
        )
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
