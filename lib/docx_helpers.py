"""Shared helpers used by lib/docx_*.py ports.

Tiny stdlib-only utilities for color conversion and OOXML namespace
shortcuts. Kept here so the docx_* port modules don't each carry their
own copy.
"""

from __future__ import annotations

from docx.oxml.ns import qn


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """'#0F1620' -> (15, 22, 32). Tolerates 3-char form too."""
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def hex_to_ooxml(color: str) -> str:
    """'#0F1620' -> '0F1620' — OOXML wants no leading hash."""
    return color.lstrip("#").upper()


def _w(tag: str) -> str:
    """Shortcut for the WordprocessingML namespace: _w('fldChar') -> '{...}fldChar'."""
    return qn(f"w:{tag}")
