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
class ThemeSelection:
    """A resolved theme + the asset paths it implies."""
    slug: str
    name: str            # "atlas", "phosphor", "arcade", or custom
    mode: str | None     # "light", "dark", or None for mode-less themes
    css_path: Path
    mermaid_config_path: Path
    is_user_theme: bool  # True if from ~/.md-publisher/themes/, False if built-in
    spec_block: dict | None  # single-theme spec block (None for "default")


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

    return ThemeSelection(
        slug=slug,
        name=name,
        mode=mode,
        css_path=css_path,
        mermaid_config_path=mermaid_path,
        is_user_theme=is_user,
        spec_block=spec_block,
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
