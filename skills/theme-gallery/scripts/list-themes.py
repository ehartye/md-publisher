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
            "audience": "corporate / customer-facing"
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


def builtin_summary_for(name: str) -> dict:
    """Pull displayName/tagline/audience for a built-in theme from the aggregate spec."""
    aggregate = PLUGIN_THEMES_DIR / "theme-spec.json"
    if not aggregate.exists():
        return {}
    spec = json.loads(aggregate.read_text(encoding="utf-8"))
    block = spec.get("themes", {}).get(name, {})
    return {
        "displayName": block.get("displayName", name),
        "tagline":     block.get("tagline", ""),
        "audience":    block.get("audience", ""),
    }


def user_summary_for(theme_dir: Path) -> dict:
    """Read the per-theme spec.json (if present) for a user-installed theme."""
    sp = theme_dir / "spec.json"
    if not sp.exists():
        return {}
    spec = json.loads(sp.read_text(encoding="utf-8"))
    return {
        "displayName": spec.get("displayName", theme_dir.name),
        "tagline":     spec.get("tagline", ""),
        "audience":    spec.get("audience", ""),
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
            else builtin_summary_for(name)
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
    merged = sorted(builtin_by_slug.values(), key=lambda t: (t["source"], t["slug"]))

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
