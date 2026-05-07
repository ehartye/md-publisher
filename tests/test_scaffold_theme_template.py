"""Tests for scaffold-theme.py — template enrichment + multi-diagram preview.

Tests construct a minimal valid spec, invoke scaffold-theme.py against a
temp HOME, and assert on the generated mermaid-config.json + preview.html
contents.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCAFFOLD_SCRIPT = (
    PLUGIN_ROOT / "skills" / "theme-advisor" / "scripts" / "scaffold-theme.py"
)


def _minimal_spec(slug: str = "test-theme-light") -> dict:
    return {
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
            "inkSoft":    "#555555",
            "accent":     "#005FCC",
            "accentSoft": "#E0EBF8",
            "rule":       "#DDDDDD",
            "codeBg":     "#F5F5F5",
            "codeText":   "#111111",
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


def _run_scaffold(tmp_path: Path, spec: dict, force: bool = False) -> subprocess.CompletedProcess:
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    env = {**os.environ, "HOME": str(tmp_path)}
    cmd = [sys.executable, str(SCAFFOLD_SCRIPT), "--spec", str(spec_path)]
    if force:
        cmd.append("--force")
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def test_mermaid_config_uses_accent_soft_for_attribute_odd(tmp_path):
    spec = _minimal_spec()
    result = _run_scaffold(tmp_path, spec)
    assert result.returncode == 0, result.stderr
    cfg = json.loads(
        (tmp_path / ".md-publisher" / "themes" / spec["slug"] / "mermaid-config.json").read_text()
    )
    # accentSoft = #E0EBF8 in the spec
    assert cfg["themeVariables"]["attributeBackgroundColorOdd"] == "#E0EBF8"


def test_mermaid_config_uses_accent_soft_for_cluster_bkg(tmp_path):
    spec = _minimal_spec()
    result = _run_scaffold(tmp_path, spec)
    assert result.returncode == 0, result.stderr
    cfg = json.loads(
        (tmp_path / ".md-publisher" / "themes" / spec["slug"] / "mermaid-config.json").read_text()
    )
    assert cfg["themeVariables"]["clusterBkg"] == "#E0EBF8"


def test_mermaid_config_uses_accent_soft_for_alt_background(tmp_path):
    spec = _minimal_spec()
    result = _run_scaffold(tmp_path, spec)
    assert result.returncode == 0, result.stderr
    cfg = json.loads(
        (tmp_path / ".md-publisher" / "themes" / spec["slug"] / "mermaid-config.json").read_text()
    )
    assert cfg["themeVariables"]["altBackground"] == "#E0EBF8"


def test_mermaid_config_uses_accent_soft_for_section_bkg(tmp_path):
    spec = _minimal_spec()
    result = _run_scaffold(tmp_path, spec)
    assert result.returncode == 0, result.stderr
    cfg = json.loads(
        (tmp_path / ".md-publisher" / "themes" / spec["slug"] / "mermaid-config.json").read_text()
    )
    assert cfg["themeVariables"]["sectionBkgColor"] == "#E0EBF8"
