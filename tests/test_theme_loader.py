"""Tests for lib/theme_loader.py — Palette/Fonts resolution + CSS fallback.

Covers the public surface used by lib.pipeline (PDF) and the
forthcoming lib.docx_pipeline (DOCX):
  - resolve_selection across all 8 bundled (theme, mode) combos.
  - Per-family palette quirks: atlas's editorial-trust palette, dark
    mode flips, arcade's accent1/accent2 swap, arcade's mono override.
  - Meridian's flat per-directory spec shape for built-ins.
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
    build_classdefs,
    resolve_selection,
)


def test_resolve_selection_for_each_bundled_theme(all_themes):
    """All 8 bundled themes resolve to a ThemeSelection with palette + fonts."""
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


def test_meridian_uses_flat_builtin_spec_and_art_deco_fonts():
    """Meridian resolves palette/fonts from its per-directory spec.json."""
    light = resolve_selection(name="meridian", mode="light")
    dark = resolve_selection(name="meridian", mode="dark")

    assert light.palette.bg == "#FAF7F0"
    assert light.palette.accent == "#B8860B"
    assert light.palette.code_bg == "#F3EFE5"
    assert light.fonts.serif == "Playfair Display"
    assert light.fonts.sans == "DM Sans"
    assert light.fonts.mono == "Fira Code"
    assert light.fonts.display == "Playfair Display"

    assert dark.palette.bg == "#141414"
    assert dark.palette.ink == "#F0EBE0"
    assert dark.palette.accent == "#D4A843"


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


def test_bloom_light_uses_botanical_palette_and_fonts():
    """BLOOM light should resolve its cream paper, forest ink, and Fraunces stack."""
    sel = resolve_selection(name="bloom", mode="light")
    assert sel.palette.bg == "#FDF8F0"
    assert sel.palette.accent == "#9E2A2B"
    assert sel.palette.code_bg == "#F5F0E5"
    assert sel.fonts.display == "Fraunces"
    assert sel.fonts.serif == "Fraunces"
    assert sel.fonts.sans == "Libre Franklin"
    assert sel.fonts.mono == "Victor Mono"


def test_bloom_dark_flips_to_warm_cream_ink():
    """BLOOM dark should invert to forest paper with warm cream ink and rose accent."""
    sel = resolve_selection(name="bloom", mode="dark")
    assert sel.palette.bg == "#121F12"
    assert sel.palette.ink == "#F5F0E1"
    assert sel.palette.accent == "#E07B6C"
    assert sel.palette.table_stripe == "#141F14"


def test_bloom_uses_builtin_spec_for_classdefs():
    """BLOOM should expose its per-tag mermaid styling through build_classdefs."""
    sel = resolve_selection(name="bloom", mode="light")
    lines = build_classdefs(sel)
    assert any("classDef ingress" in line and "stroke:#9E2A2B" in line for line in lines)
    assert any("classDef core" in line and "stroke:#1A3A1A" in line for line in lines)


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


def test_tundra_uses_declared_fonts_and_palette():
    """Tundra resolves its Nordic minimal font stack and teal palette."""
    light = resolve_selection(name="tundra", mode="light")
    dark = resolve_selection(name="tundra", mode="dark")
    assert light.fonts.display == "Cormorant Garamond"
    assert light.fonts.serif == "Cormorant Garamond"
    assert light.fonts.sans == "Karla"
    assert light.fonts.mono == "Source Code Pro"
    assert light.palette.accent == "#0A8270"
    assert light.palette.rule == "#D1D8DC"
    assert dark.palette.bg == "#1E272E"
    assert dark.palette.accent == "#12B886"


def test_signal_uses_declared_fonts_and_manual_palette():
    """Signal resolves its condensed field-manual typography and orange palette."""
    light = resolve_selection(name="signal", mode="light")
    dark = resolve_selection(name="signal", mode="dark")
    assert light.fonts.display == "Barlow Condensed"
    assert light.fonts.serif == "Barlow"
    assert light.fonts.sans == "Barlow"
    assert light.fonts.mono == "Inconsolata"
    assert light.palette.bg == "#F0EDE8"
    assert light.palette.accent == "#E8590C"
    assert light.palette.table_stripe == "#F0EDE8"
    assert dark.palette.bg == "#141517"
    assert dark.palette.accent == "#FF6B35"
    assert dark.palette.code_bg == "#1A1C1F"


def test_unknown_theme_raises():
    """Resolver raises FileNotFoundError for a theme with no directory."""
    with pytest.raises(FileNotFoundError):
        resolve_selection(name="nonexistent-theme-xyz123", mode="light")
