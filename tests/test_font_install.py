"""Tests for lib/font_install.py font family coverage for bundled themes."""
from __future__ import annotations

from lib.font_install import FONT_SLUGS


def test_font_slug_map_covers_meridian_google_fonts():
    """Meridian's Google Fonts must be installable for DOCX fidelity."""
    assert FONT_SLUGS["Playfair Display"] == "playfairdisplay"
    assert FONT_SLUGS["DM Sans"] == "dmsans"
    assert FONT_SLUGS["Fira Code"] == "firacode"


def test_font_slug_map_covers_signal_google_fonts():
    """Signal's Google Fonts must be installable for DOCX fidelity."""
    assert FONT_SLUGS["Barlow Condensed"] == "barlowcondensed"
    assert FONT_SLUGS["Barlow"] == "barlow"
    assert FONT_SLUGS["Inconsolata"] == "inconsolata"


def test_font_slug_map_covers_tundra_google_fonts():
    """Tundra's Google Fonts must be installable for DOCX fidelity."""
    assert FONT_SLUGS["Cormorant Garamond"] == "cormorantgaramond"
    assert FONT_SLUGS["Karla"] == "karla"
    assert FONT_SLUGS["Source Code Pro"] == "sourcecodepro"
