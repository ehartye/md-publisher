"""Tests for skills/rebuild-themes/scripts/rebuild-themes.py.

Discovery walks <plugin-root>/themes/ and ~/.md-publisher/themes/ for
directories containing spec.json. Dry-run lists themes without writing;
--apply rewrites mermaid-config.json and preview.html and creates a
.backup-<ts>/ subdirectory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
REBUILD_SCRIPT = PLUGIN_ROOT / "skills" / "rebuild-themes" / "scripts" / "rebuild-themes.py"


def _seed_user_theme(home: Path, slug: str = "test-theme-light") -> Path:
    """Create a minimal theme directory with spec.json + stale mermaid-config.json."""
    theme_dir = home / ".md-publisher" / "themes" / slug
    theme_dir.mkdir(parents=True, exist_ok=True)
    spec = {
        "slug": slug,
        "name": "test-theme",
        "mode": "light",
        "displayName": "Test Theme",
        "tagline": "A theme for tests.",
        "fonts": {
            "display": "Charter, Georgia, serif",
            "body":    "Charter, Georgia, serif",
            "sans":    "Inter, system-ui, sans-serif",
            "mono":    "JetBrains Mono, Consolas, monospace",
        },
        "palette": {
            "bg":         "#FFFFFF",
            "paper":      "#FFFFFF",
            "ink":        "#111111",
            "ink_soft":   "#555555",
            "accent":     "#005FCC",
            "accent_alt": "#E0EBF8",
            "rule":       "#DDDDDD",
            "code_bg":    "#F5F5F5",
            "code_text":  "#111111",
        },
        "mermaid": {
            "tagStyling": {
                "ingress":   {"fill": "#FFFFFF", "stroke": "#005FCC", "color": "#111111", "strokeWidth": 2},
                "core":      {"fill": "#FFFFFF", "stroke": "#555555", "color": "#111111", "strokeWidth": 1},
                "transform": {"fill": "#F5F5F5", "stroke": "#555555", "color": "#111111", "strokeWidth": 1},
                "bridge":    {"fill": "transparent", "stroke": "#555555", "color": "#555555", "strokeWidth": 1, "strokeDasharray": "4 3"},
            },
            "fontFamily": "Inter, sans-serif",
            "lineColor": "#555555",
        },
    }
    (theme_dir / "spec.json").write_text(json.dumps(spec))
    # Seed a stale mermaid-config.json so we can detect the rewrite
    (theme_dir / "mermaid-config.json").write_text('{"stale": true}')
    (theme_dir / "preview.html").write_text("<html>stale</html>")
    return theme_dir


def _run_rebuild(home: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    cmd = [sys.executable, str(REBUILD_SCRIPT), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def test_dry_run_lists_themes_without_writing(tmp_path):
    theme = _seed_user_theme(tmp_path)
    original = (theme / "mermaid-config.json").read_text()
    result = _run_rebuild(tmp_path)  # no args = dry run
    assert result.returncode == 0, result.stderr
    assert "test-theme-light" in result.stdout
    assert "dry-run" in result.stdout.lower() or "would" in result.stdout.lower()
    assert (theme / "mermaid-config.json").read_text() == original


def test_apply_rewrites_files_and_creates_backup(tmp_path):
    theme = _seed_user_theme(tmp_path)
    result = _run_rebuild(tmp_path, "--apply")
    assert result.returncode == 0, result.stderr
    new_cfg = json.loads((theme / "mermaid-config.json").read_text())
    # Bumped variable should be accent_soft (#E0EBF8 in our seed spec)
    assert new_cfg["themeVariables"]["attributeBackgroundColorOdd"] == "#E0EBF8"
    # Backup directory created with the original stale content
    backup_dirs = list(theme.glob(".backup-*"))
    assert len(backup_dirs) == 1
    backup = backup_dirs[0]
    assert (backup / "mermaid-config.json").read_text() == '{"stale": true}'


def test_missing_spec_skipped_with_warning(tmp_path):
    # Theme directory with NO spec.json
    theme_dir = tmp_path / ".md-publisher" / "themes" / "no-spec-theme"
    theme_dir.mkdir(parents=True)
    (theme_dir / "mermaid-config.json").write_text('{"some": "config"}')
    result = _run_rebuild(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "no-spec-theme" in result.stderr
    assert "spec.json" in result.stderr.lower()


def test_theme_filter_applies_only_to_named_theme(tmp_path):
    theme_a = _seed_user_theme(tmp_path, "theme-a-light")
    theme_b = _seed_user_theme(tmp_path, "theme-b-light")
    result = _run_rebuild(tmp_path, "--apply", "--theme", "theme-a-light")
    assert result.returncode == 0, result.stderr
    cfg_a = json.loads((theme_a / "mermaid-config.json").read_text())
    assert "themeVariables" in cfg_a  # rewritten
    cfg_b = json.loads((theme_b / "mermaid-config.json").read_text())
    assert cfg_b == {"stale": True}  # untouched
