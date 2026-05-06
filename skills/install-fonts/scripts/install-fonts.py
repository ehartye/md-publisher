#!/usr/bin/env python
"""Skill entry point for /md-publisher:install-fonts.

Resolves the requested theme(s), enumerates their font requirements,
downloads + installs missing fonts via lib.font_install. Per-user
install on every supported platform — no admin/sudo required.

Invoke directly with the bootstrapped venv's python:
    ~/.md-publisher/runtime/.venv/Scripts/python.exe \\
        skills/install-fonts/scripts/install-fonts.py [flags]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or
                   Path(__file__).resolve().parent.parent.parent.parent)
sys.path.insert(0, str(PLUGIN_ROOT))

from lib.font_install import detect_missing_fonts, install_all_for_themes
from lib.theme_loader import list_available_themes, resolve_selection


def _collect_selections(theme: str | None, mode: str | None) -> list:
    """Resolve the (theme, mode) requests into a list of ThemeSelection.

    --theme alone means both modes (when applicable); --mode alone is
    rejected by argparse (requires --theme). No flags means all
    discoverable themes.
    """
    selections: list = []
    if theme:
        modes = [mode] if mode else ["light", "dark", None]
        for m in modes:
            try:
                selections.append(resolve_selection(name=theme, mode=m))
            except FileNotFoundError:
                continue
    else:
        for theme_meta in list_available_themes():
            try:
                selections.append(resolve_selection(
                    name=theme_meta["name"], mode=theme_meta["mode"]
                ))
            except FileNotFoundError:
                continue
    return selections


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--theme", default=None,
                   help="theme name (default: all installed themes)")
    p.add_argument("--mode", default=None, choices=["light", "dark"],
                   help="mode within --theme (default: both)")
    p.add_argument("--all", action="store_true",
                   help="install fonts for every installed theme "
                        "(explicit form of no --theme)")
    p.add_argument("--dry-run", action="store_true",
                   help="list what would be installed without downloading")
    args = p.parse_args()

    if args.mode and not args.theme:
        p.error("--mode requires --theme")
    if args.all and args.theme:
        p.error("--all and --theme are mutually exclusive")

    selections = _collect_selections(args.theme, args.mode)
    if not selections:
        sys.stderr.write(
            "[install-fonts] no themes resolved — nothing to install\n"
        )
        return 1

    print(f"[install-fonts] target: {len(selections)} theme(s); "
          f"platform={sys.platform}")
    missing_before = detect_missing_fonts(selections)
    if not missing_before and not args.dry_run:
        print("[install-fonts] all needed fonts already installed.")
        return 0

    n = install_all_for_themes(selections, dry_run=args.dry_run)
    if args.dry_run:
        print(f"[install-fonts] dry run — "
              f"{len(missing_before)} font famil(y/ies) would be installed.")
    else:
        print(f"[install-fonts] {n} font file(s) installed "
              "(already-present skipped).")
        print("[install-fonts] Open apps need to re-launch to pick up "
              "newly-registered fonts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
