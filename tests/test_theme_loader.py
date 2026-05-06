"""Tests for lib/theme_loader.py — Palette/Fonts resolution + CSS fallback.

Covers the public surface used by lib.pipeline (PDF) and the
forthcoming lib.docx_pipeline (DOCX):
  - resolve_selection across all 6 bundled (theme, mode) combos.
  - Per-family palette quirks: atlas's editorial-trust palette, dark
    mode flips, arcade's accent1/accent2 swap, arcade's mono override.
  - Phosphor's all-IBM-Plex-Mono shape.
  - CSS-extraction fallback for pre-v0.2 user themes (palette + fonts).
  - Unknown theme name raises FileNotFoundError.
"""
from __future__ import annotations

import pytest

from lib.theme_loader import (
    Fonts,
    Palette,
    ThemeSelection,
    _fonts_from_css,
    _palette_from_css,
    resolve_selection,
)


def test_resolve_selection_for_each_bundled_theme(all_themes):
    """All 6 bundled themes resolve to a ThemeSelection with palette + fonts."""
    name, mode = all_themes
    sel = resolve_selection(name=name, mode=mode)
    assert isinstance(sel, ThemeSelection)
    assert sel.slug == f"{name}-{mode}"
    assert sel.name == name
    assert sel.mode == mode
    assert sel.palette is not None, f"palette missing for {sel.slug}"
    assert sel.fonts is not None, f"fonts missing for {sel.slug}"
    assert isinstance(sel.palette, Palette)
    assert isinstance(sel.fonts, Fonts)


def test_atlas_light_palette_values():
    """Atlas-light palette pulls from themes/theme-spec.json."""
    sel = resolve_selection(name="atlas", mode="light")
    assert sel.palette.bg == "#F8F1E7"
    assert sel.palette.accent == "#B22234"
    assert sel.palette.code_bg == "#F4ECDB"


def test_atlas_dark_palette_differs_from_light():
    """Dark mode flips bg + ink; both must materially differ from light."""
    light = resolve_selection(name="atlas", mode="light")
    dark = resolve_selection(name="atlas", mode="dark")
    assert light.palette.bg != dark.palette.bg
    assert light.palette.ink != dark.palette.ink


def test_phosphor_fonts_are_all_plex_mono():
    """Phosphor uses IBM Plex Mono for everything per the theme spec."""
    sel = resolve_selection(name="phosphor", mode="light")
    assert sel.fonts.serif == "IBM Plex Mono"
    assert sel.fonts.sans == "IBM Plex Mono"
    assert sel.fonts.mono == "IBM Plex Mono"
    assert sel.fonts.display == "IBM Plex Mono"


def test_arcade_mono_overridden_to_jetbrains_mono():
    """Arcade's mono is hard-overridden per DECISION.md concession #2.

    Recursive's MONO axis is not reliably addressable via Word's
    <w:rFonts>, so the resolver substitutes JetBrains Mono. Body still
    Recursive (variable axes work fine for plain text).
    """
    sel = resolve_selection(name="arcade", mode="light")
    assert sel.fonts.mono == "JetBrains Mono"
    assert sel.fonts.serif == "Recursive"


def test_arcade_accent_uses_accent2():
    """Arcade picks accent2 as primary accent; accent1 becomes accent_alt.

    accent2 is the cover hard-shadow color (most distinctive arcade
    signal). The two must materially differ — if they're equal the
    per-family swap silently degraded to no-op.
    """
    sel = resolve_selection(name="arcade", mode="light")
    assert sel.palette.accent != sel.palette.accent_alt


def test_palette_from_css_extracts_custom_properties(tmp_path):
    """CSS fallback parses :root { --bg: ...; ... } from style.css."""
    css_path = tmp_path / "style.css"
    css_path.write_text(""":root {
    --bg: #112233;
    --ink: #EEDDCC;
    --accent: #FF0000;
    --code-bg: #001122;
    --code-text: #FFFFFF;
}""", encoding="utf-8")
    pal = _palette_from_css(css_path)
    assert pal.bg == "#112233"
    assert pal.ink == "#EEDDCC"
    assert pal.accent == "#FF0000"
    assert pal.code_bg == "#001122"
    assert pal.code_text == "#FFFFFF"


def test_palette_from_css_defaults_missing_keys(tmp_path):
    """Missing CSS variables default to safe values."""
    css_path = tmp_path / "style.css"
    css_path.write_text(":root { --bg: #112233; }", encoding="utf-8")
    pal = _palette_from_css(css_path)
    assert pal.bg == "#112233"
    assert pal.ink == "#000000"
    assert pal.accent == "#000000"


def test_fonts_from_css_extracts_first_font_in_stack(tmp_path):
    """CSS font-family stacks like 'Newsreader, Georgia, serif' yield 'Newsreader'."""
    css_path = tmp_path / "style.css"
    css_path.write_text(""":root {
    --font-display: 'Newsreader', Georgia, serif;
    --font-mono: 'JetBrains Mono', Consolas, monospace;
}""", encoding="utf-8")
    fonts = _fonts_from_css(css_path)
    assert fonts.display == "Newsreader"
    assert fonts.mono == "JetBrains Mono"


def test_fonts_from_css_falls_back_to_system_stacks(tmp_path):
    """Missing font CSS variables fall back to generic stacks."""
    css_path = tmp_path / "style.css"
    css_path.write_text("", encoding="utf-8")
    fonts = _fonts_from_css(css_path)
    assert fonts.serif == "serif"
    assert fonts.sans == "sans-serif"
    assert fonts.mono == "monospace"


def test_unknown_theme_raises():
    """Resolver raises FileNotFoundError for a theme with no directory."""
    with pytest.raises(FileNotFoundError):
        resolve_selection(name="nonexistent-theme-xyz123", mode="light")
