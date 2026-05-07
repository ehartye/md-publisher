#!/usr/bin/env python
"""Refresh installed themes by re-deriving mermaid-config.json + preview.html
from each theme's spec.json. Imports the templates and helpers from
scaffold-theme.py so the two scripts stay in lockstep.

Usage:
    rebuild-themes.py                   # dry run; lists themes that would migrate
    rebuild-themes.py --apply           # rewrite all eligible themes (with backup)
    rebuild-themes.py --theme <slug>    # restrict to one theme
    rebuild-themes.py --apply --no-backup
                                        # skip backup (escape hatch; not recommended)

Discovery roots (in order):
    1. <plugin-root>/themes/*/         (built-in themes shipped with the plugin)
    2. ~/.md-publisher/themes/*/       (user-installed themes)

A theme is eligible if its directory contains spec.json. Directories
lacking spec.json are skipped with a warning naming the directory.

Backup convention: per theme, the pre-migration mermaid-config.json and
preview.html are copied to <theme-dir>/.backup-<YYYYMMDD-HHMMSS>/ before
being overwritten.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Plugin root: env var if set (matches scaffold-theme convention), else
# walk up from this script.
PLUGIN_ROOT = Path(
    os.environ.get("CLAUDE_PLUGIN_ROOT")
    or Path(__file__).resolve().parent.parent.parent.parent
)
USER_THEMES_DIR = Path.home() / ".md-publisher" / "themes"
BUILTIN_THEMES_DIR = PLUGIN_ROOT / "themes"

# Cross-script import: pull MERMAID_CONFIG_TEMPLATE, PREVIEW_HTML_TEMPLATE,
# substitute, first_family, _render_preview_figure, PREVIEW_FLOWCHART/_ER/_CLASS,
# _classdef_props from scaffold-theme.py. Same importlib pattern apply-tags
# uses to import scan-mermaid.
SCAFFOLD_PATH = (
    PLUGIN_ROOT / "skills" / "theme-advisor" / "scripts" / "scaffold-theme.py"
)
_spec = importlib.util.spec_from_file_location("_scaffold", SCAFFOLD_PATH)
_scaffold = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scaffold)


def discover_themes(only: str | None = None) -> list[Path]:
    """Return theme directories (with spec.json) under built-in + user roots."""
    found: list[Path] = []
    for root in (BUILTIN_THEMES_DIR, USER_THEMES_DIR):
        if not root.exists():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if only and entry.name != only:
                continue
            found.append(entry)
    return found


def is_eligible(theme_dir: Path) -> bool:
    return (theme_dir / "spec.json").exists()


def _spec_to_substitution_dict(spec: dict) -> dict:
    """Map a spec.json (with snake_case palette keys) into the {{var}} dict
    that scaffold-theme's templates expect."""
    palette = spec["palette"]
    fonts = spec.get("fonts", {})
    # spec.json uses snake_case after normalization; map back to template keys.
    return {
        "display_name": spec.get("displayName", spec.get("slug", "Theme")),
        "font_display": fonts.get("display", "serif"),
        "font_body":    fonts.get("serif",   fonts.get("body", "serif")),
        "font_sans":    fonts.get("sans",    "sans-serif"),
        "font_mono":    fonts.get("mono",    "monospace"),
        "bg":           palette["bg"],
        "paper":        palette.get("paper", palette["bg"]),
        "ink":          palette["ink"],
        "ink_soft":     palette.get("ink_soft", palette["ink"]),
        "accent":       palette["accent"],
        "accent_soft":  palette.get("accent_alt", palette["accent"]),
        "rule":         palette.get("rule", palette.get("ink_soft", "#cccccc")),
        "code_bg":      palette.get("code_bg", palette.get("paper", palette["bg"])),
        "code_text":    palette.get("code_text", palette["ink"]),
    }


def regenerate_mermaid_config(theme_dir: Path, spec: dict) -> str:
    """Re-derive mermaid-config.json from a theme's spec.json."""
    subs = _spec_to_substitution_dict(spec)
    line_color = spec.get("mermaid", {}).get(
        "lineColor", spec["palette"].get("accent", "#555555")
    )
    mermaid_font = spec.get("mermaid", {}).get(
        "fontFamily", subs["font_sans"]
    )
    mermaid_subs = {**subs, "line_color": line_color, "mermaid_font": mermaid_font}
    cfg_str = _scaffold.substitute(
        json.dumps(_scaffold.MERMAID_CONFIG_TEMPLATE, indent=2),
        mermaid_subs,
    )
    json.loads(cfg_str)  # validate
    return cfg_str


def regenerate_preview(theme_dir: Path, spec: dict, mermaid_config_path: Path) -> str:
    """Re-derive preview.html from a theme's spec.json — including 3 mermaid figures."""
    subs = _spec_to_substitution_dict(spec)
    style_css = (theme_dir / "style.css").read_text(encoding="utf-8") if (theme_dir / "style.css").exists() else ""

    # Render the 3 figures via mmdc (with fallback when missing)
    mmdc_path = (
        Path.home() / ".md-publisher" / "runtime" / "node_modules"
        / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js"
    )
    build_dir = theme_dir / ".preview-build"
    build_dir.mkdir(exist_ok=True)
    try:
        flowchart_src = _scaffold.substitute(_scaffold.PREVIEW_FLOWCHART, {
            "ingress_classdef":   _scaffold._classdef_props(spec["mermaid"]["tagStyling"]["ingress"]),
            "core_classdef":      _scaffold._classdef_props(spec["mermaid"]["tagStyling"]["core"]),
            "transform_classdef": _scaffold._classdef_props(spec["mermaid"]["tagStyling"]["transform"]),
            "bridge_classdef":    _scaffold._classdef_props(spec["mermaid"]["tagStyling"]["bridge"]),
        })
        flowchart_figure = _scaffold._render_preview_figure(flowchart_src, mermaid_config_path, build_dir, mmdc_path)
        er_figure = _scaffold._render_preview_figure(_scaffold.PREVIEW_ER, mermaid_config_path, build_dir, mmdc_path)
        class_figure = _scaffold._render_preview_figure(_scaffold.PREVIEW_CLASS, mermaid_config_path, build_dir, mmdc_path)
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

    body = spec.get("fonts", {}).get("body", spec.get("fonts", {}).get("serif", "serif"))
    preview_subs = {
        **subs,
        "tagline": spec.get("tagline", "Custom md-publisher theme."),
        "font_body_first": _scaffold.first_family(body),
        "style_css_inline": style_css,
        "flowchart_figure": flowchart_figure,
        "er_figure":        er_figure,
        "class_figure":     class_figure,
    }
    return _scaffold.substitute(_scaffold.PREVIEW_HTML_TEMPLATE, preview_subs)


def backup_existing(theme_dir: Path, ts: str) -> Path:
    """Copy current mermaid-config.json, preview.html, cover.css to .backup-<ts>/. Returns dir."""
    backup_dir = theme_dir / f".backup-{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("mermaid-config.json", "preview.html", "cover.css"):
        src = theme_dir / fname
        if src.exists():
            shutil.copy2(src, backup_dir / fname)
    return backup_dir


def migrate_theme(theme_dir: Path, *, apply: bool, backup: bool) -> dict:
    """Migrate one theme. Returns a small status dict for reporting."""
    spec = json.loads((theme_dir / "spec.json").read_text(encoding="utf-8"))
    if not apply:
        return {"theme": theme_dir.name, "status": "would-migrate"}

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir: Path | None = None
    if backup:
        backup_dir = backup_existing(theme_dir, ts)

    # Regenerate. mermaid-config.json must exist before preview rendering
    # (mmdc reads the config to apply theme variables).
    new_cfg = regenerate_mermaid_config(theme_dir, spec)
    (theme_dir / "mermaid-config.json").write_text(new_cfg, encoding="utf-8")

    # Regenerate cover.css from the current template (picks up decorative
    # improvements like triple-rule, ornament, etc.).
    subs = _spec_to_substitution_dict(spec)
    new_cover = _scaffold.substitute(_scaffold.COVER_CSS_TEMPLATE, subs)
    (theme_dir / "cover.css").write_text(new_cover, encoding="utf-8")

    new_preview = regenerate_preview(theme_dir, spec, theme_dir / "mermaid-config.json")
    (theme_dir / "preview.html").write_text(new_preview, encoding="utf-8")

    return {
        "theme": theme_dir.name,
        "status": "migrated",
        "backup": str(backup_dir) if backup_dir else None,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true",
                   help="actually rewrite files (default is dry-run)")
    p.add_argument("--theme", type=str, default=None,
                   help="restrict migration to one theme by slug")
    p.add_argument("--no-backup", action="store_true",
                   help="(escape hatch) do not back up pre-migration files")
    args = p.parse_args()

    themes = discover_themes(only=args.theme)
    if not themes:
        msg = f"no themes found"
        if args.theme:
            msg += f" matching --theme {args.theme!r}"
        sys.stderr.write(f"[rebuild-themes] {msg}\n")
        return 0

    eligible = []
    for theme in themes:
        if is_eligible(theme):
            eligible.append(theme)
        else:
            sys.stderr.write(
                f"[rebuild-themes] skip {theme.name}: missing spec.json\n"
            )

    if not eligible:
        sys.stderr.write("[rebuild-themes] no eligible themes\n")
        return 0

    if not args.apply:
        print(f"[rebuild-themes] dry-run; {len(eligible)} theme(s) would migrate:")
        for theme in eligible:
            print(f"  would migrate: {theme}")
        print("Re-run with --apply to actually rewrite.")
        return 0

    for theme in eligible:
        status = migrate_theme(theme, apply=True, backup=not args.no_backup)
        suffix = ""
        if status.get("backup"):
            suffix = f"  (backed up to {status['backup']})"
        print(f"[rebuild-themes] migrated: {theme}{suffix}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
