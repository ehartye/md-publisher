#!/usr/bin/env python
"""List all md-publisher themes (built-in + user-installed) as JSON.

Used by the theme-gallery skill. Output schema:

    {
      "user_themes_dir": "/home/.../.md-publisher/themes",
      "plugin_themes_dir": "/path/to/plugin/themes",
      "themes": [
        {
          "slug": "atlas-light",
          "name": "atlas",
          "mode": "light",
          "source": "builtin",
          "path": "/path/to/plugin/themes/atlas-light",
          "has_preview": false,
          "spec_summary": {
            "displayName": "ATLAS",
            "tagline": "Editorial-trust serif on warm bone, red as punctuation only.",
            "audience": "corporate / customer-facing",
            "palette": { "bg": "#F8F1E7", "ink": "#0F1620", ... },
            "fonts":   { "serif": "Newsreader", "sans": "Sora", ... }
          }
        },
        ...
      ]
    }
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or
                   Path(__file__).resolve().parent.parent.parent.parent)
USER_THEMES_DIR = Path.home() / ".md-publisher" / "themes"
PLUGIN_THEMES_DIR = PLUGIN_ROOT / "themes"


def name_from_slug(slug: str) -> tuple[str, str | None]:
    if slug.endswith("-light"):
        return slug[:-len("-light")], "light"
    if slug.endswith("-dark"):
        return slug[:-len("-dark")], "dark"
    return slug, None


def _resolve_builtin_palette(family: str, mode: str | None, block: dict) -> dict:
    """Normalize the per-mode palette block to the snake_case Palette schema.

    Atlas/phosphor expose `accent`; arcade exposes `accent1`/`accent2`. Mirror
    lib.theme_loader._resolve_palette so the gallery shows the same colors
    the DOCX/PDF pipelines actually paint.
    """
    if mode is None:
        # Mode-less theme (e.g. default): try "light" as the single mode fallback
        p = block.get("modes", {}).get("light", {})
        if not p:
            return {}
    else:
        p = block.get("modes", {}).get(mode, {})
        if not p:
            return {}
    if family == "arcade":
        accent = p.get("accent2", p.get("accent1", "#000000"))
        accent_alt = p.get("accent1", accent)
    else:
        accent = p.get("accent", "#000000")
        accent_alt = p.get("accentSoft", accent)
    return {
        "bg":           p.get("bg", "#FFFFFF"),
        "paper":        p.get("paper", p.get("bg", "#FFFFFF")),
        "ink":          p.get("ink", "#000000"),
        "ink_soft":     p.get("inkSoft", p.get("ink", "#000000")),
        "accent":       accent,
        "accent_alt":   accent_alt,
        "rule":         p.get("rule", accent),
        "code_bg":      p.get("codeBg", p.get("bg", "#F0F0F0")),
        "code_text":    p.get("codeText", p.get("ink", "#000000")),
        "table_stripe": p.get("tableStripe", p.get("bg", "#FFFFFF")),
    }


def _resolve_builtin_fonts(family: str, fonts: dict) -> dict:
    """Mirror lib.theme_loader._resolve_fonts so the gallery shows the same
    typography roles the pipelines apply.
    """
    if family == "atlas":
        return {
            "serif":   fonts["body"]["family"],
            "sans":    fonts["sansAccent"]["family"],
            "mono":    fonts["mono"]["family"],
            "display": fonts["display"]["family"],
        }
    if family == "phosphor":
        plex = fonts["body"]["family"]
        return {"serif": plex, "sans": plex, "mono": plex, "display": plex}
    if family == "arcade":
        return {
            "serif":   fonts["body"]["family"],
            "sans":    fonts["display2"]["family"],
            "mono":    "JetBrains Mono",  # see lib.theme_loader for rationale
            "display": fonts["display"]["family"],
        }
    # Generic fallback (e.g. default theme)
    result = {}
    if "body" in fonts:
        result["serif"] = fonts["body"]["family"]
    if "display" in fonts:
        result["display"] = fonts["display"]["family"]
    if "mono" in fonts:
        result["mono"] = fonts["mono"]["family"]
    if "sansAccent" in fonts:
        result["sans"] = fonts["sansAccent"]["family"]
    return result


def builtin_summary_for(name: str, mode: str | None, theme_dir: Path) -> dict:
    """Pull displayName/tagline/palette/fonts for a built-in theme."""
    direct_spec = theme_dir / "spec.json"
    if direct_spec.exists():
        spec = json.loads(direct_spec.read_text(encoding="utf-8"))
        return {
            "displayName": spec.get("displayName", name),
            "tagline":     spec.get("tagline", ""),
            "audience":    spec.get("audience", ""),
            "palette":     spec.get("palette", {}),
            "fonts":       _unwrap_user_fonts(spec.get("fonts", {})),
        }

    aggregate = PLUGIN_THEMES_DIR / "theme-spec.json"
    if not aggregate.exists():
        return {}
    spec = json.loads(aggregate.read_text(encoding="utf-8"))
    block = spec.get("themes", {}).get(name, {})
    if not block:
        return {"displayName": name}
    out = {
        "displayName": block.get("displayName", name),
        "tagline":     block.get("tagline", ""),
        "audience":    block.get("audience", ""),
        "palette":     _resolve_builtin_palette(name, mode, block),
        "fonts":       _resolve_builtin_fonts(name, block.get("fonts", {})),
    }
    return out


def _unwrap_user_fonts(raw: dict) -> dict:
    """Coerce a user-theme `fonts` block to flat {role: family-string}.

    v0.2+ themes use {"serif": "Newsreader"} (flat strings). Pre-v0.2
    themes (and bake-off-era spec dumps) use {"serif": {"family": "X"}}
    (nested dicts mirroring the bundled theme-spec.json shape). The
    gallery wants the flat string form for inline-style rendering, so
    unwrap defensively. Mirrors the safety net in font_install._families_from_themes.
    """
    out: dict[str, str] = {}
    for role, val in raw.items():
        if isinstance(val, str):
            out[role] = val
        elif isinstance(val, dict) and isinstance(val.get("family"), str):
            out[role] = val["family"]
        # else: skip silently — gallery handles missing roles via fallbacks
    return out


def user_summary_for(theme_dir: Path) -> dict:
    """Read the per-theme spec.json for a user-installed theme.

    User themes from v0.2 forward have explicit `palette` and `fonts` blocks
    at spec.json top level (snake_case keys). Pre-v0.2 user themes that
    used the nested {family: "X"} shape get unwrapped; themes with neither
    block fall back to a neutral chip set in the gallery card.
    """
    sp = theme_dir / "spec.json"
    if not sp.exists():
        return {}
    spec = json.loads(sp.read_text(encoding="utf-8"))
    return {
        "displayName": spec.get("displayName", theme_dir.name),
        "tagline":     spec.get("tagline", ""),
        "audience":    spec.get("audience", ""),
        "palette":     spec.get("palette", {}),
        "fonts":       _unwrap_user_fonts(spec.get("fonts", {})),
    }


def collect(theme_dir: Path, source_label: str) -> list[dict]:
    if not theme_dir.exists():
        return []
    out = []
    for entry in sorted(theme_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "style.css").exists():
            continue
        slug = entry.name
        name, mode = name_from_slug(slug)
        summary = (
            user_summary_for(entry) if source_label == "user"
            else builtin_summary_for(name, mode, entry)
        )
        out.append({
            "slug": slug,
            "name": name,
            "mode": mode,
            "source": source_label,
            "path": str(entry),
            "has_preview": (entry / "preview.html").exists(),
            "spec_summary": summary,
        })
    return out


def main() -> int:
    themes = collect(PLUGIN_THEMES_DIR, "builtin")
    # Skip the "default" theme's "default" mode-less entry from the spec aggregate
    # — its spec block doesn't exist, so summary is {}. That's fine.
    user_themes = collect(USER_THEMES_DIR, "user")

    # User themes win on slug collision (intentional override)
    builtin_by_slug = {t["slug"]: t for t in themes}
    for ut in user_themes:
        builtin_by_slug[ut["slug"]] = ut
    merged = sorted(builtin_by_slug.values(), key=lambda t: (
        t["source"],
        1 if t["slug"] == "default" else 0,
        t["slug"],
    ))

    payload = {
        "user_themes_dir": str(USER_THEMES_DIR),
        "plugin_themes_dir": str(PLUGIN_THEMES_DIR),
        "themes": merged,
    }
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
