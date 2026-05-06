"""Tests for lib/output_paths.py — format-aware path derivation.

The kwarg is `output_format=` (not `format=`) per output_paths.py's
docstring rationale: it avoids shadowing the `format()` builtin in
callers and matches MermaidPreprocessor's `output_format=` so callers
threading both don't have to remember which sister API uses which name.
"""
from __future__ import annotations

import pytest

from lib.output_paths import (
    default_output_dir,
    derive_output_path,
    derive_output_pdf,
)


def test_derive_output_path_pdf_default(tmp_path):
    src = tmp_path / "doc.md"
    src.touch()
    out = derive_output_path(
        src, output_format="pdf", theme_label="atlas-light", ts="20260506-120000"
    )
    assert out.suffix == ".pdf"
    assert "atlas-light" in out.stem
    assert ".md-publisher" in str(out)
    assert "20260506-120000" in str(out)


def test_derive_output_path_docx(tmp_path):
    src = tmp_path / "doc.md"
    src.touch()
    out = derive_output_path(
        src, output_format="docx", theme_label="phosphor-dark", ts="20260506-120000"
    )
    assert out.suffix == ".docx"
    assert "phosphor-dark" in out.stem


def test_derive_output_path_explicit_output_honored(tmp_path):
    """--output is verbatim — caller's responsibility, no extension override."""
    src = tmp_path / "doc.md"
    explicit = tmp_path / "custom.docx"
    out = derive_output_path(src, output_format="docx", explicit_output=explicit)
    assert out == explicit


def test_derive_output_path_invalid_format_raises(tmp_path):
    src = tmp_path / "doc.md"
    src.touch()
    with pytest.raises(ValueError, match="unsupported output_format"):
        derive_output_path(src, output_format="rtf")


def test_derive_output_pdf_alias_matches_output_format_pdf(tmp_path):
    """The backwards-compat derive_output_pdf alias produces identical
    output to derive_output_path(output_format='pdf').
    """
    src = tmp_path / "doc.md"
    src.touch()
    a = derive_output_pdf(src, theme_label="atlas-light", ts="20260506-120000")
    b = derive_output_path(
        src, output_format="pdf", theme_label="atlas-light", ts="20260506-120000"
    )
    assert a == b


def test_default_output_dir_uses_md_publisher_subdir(tmp_path):
    src = tmp_path / "doc.md"
    out_dir = default_output_dir(src, ts="20260506-120000")
    assert out_dir == tmp_path / ".md-publisher" / "20260506-120000"


def test_pdf_and_docx_share_timestamp_dir_when_same_ts(tmp_path):
    """`--format both` produces sibling files in the same timestamp dir."""
    src = tmp_path / "doc.md"
    src.touch()
    pdf_out = derive_output_path(
        src, output_format="pdf", theme_label="atlas-light", ts="20260506-120000"
    )
    docx_out = derive_output_path(
        src, output_format="docx", theme_label="atlas-light", ts="20260506-120000"
    )
    assert pdf_out.parent == docx_out.parent
    assert pdf_out.stem == docx_out.stem
