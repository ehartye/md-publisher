"""Regression test for the WeasyPrint `base_url` bug.

`build_pdf` must resolve relative image paths (e.g. `![chart](graphics/x.png)`
in user markdown) against the *source markdown's own directory*, not the
plugin install directory. A prior version passed `base_url=plugin_root()`,
which silently broke every relative image reference in user documents (the
image XObject count in the resulting PDF was 0) while providing no benefit,
since mermaid diagrams use absolute `file://` URIs and theme fonts load via
absolute `https://` @import — neither depends on base_url at all.

This test stubs out `weasyprint.HTML` so it runs without the native
Pango/Cairo/GDK-Pixbuf dependencies WeasyPrint needs, keeping it fast and
portable in CI.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from lib import pipeline
from lib.theme_loader import resolve_selection


class _FakeHTML:
    """Captures constructor args instead of actually rendering a PDF."""
    captured: dict = {}

    def __init__(self, *, string: str, base_url: str):
        _FakeHTML.captured["string"] = string
        _FakeHTML.captured["base_url"] = base_url

    def write_pdf(self, path: str) -> None:
        Path(path).write_bytes(b"%PDF-1.7\n%%EOF")


@pytest.fixture
def fake_weasyprint(monkeypatch):
    _FakeHTML.captured = {}
    fake_module = types.SimpleNamespace(HTML=_FakeHTML, CSS=object())
    monkeypatch.setitem(sys.modules, "weasyprint", fake_module)
    return _FakeHTML


def test_build_pdf_resolves_relative_images_against_source_dir(tmp_path, fake_weasyprint):
    source_dir = tmp_path / "report"
    source_dir.mkdir()
    (source_dir / "graphics").mkdir()
    (source_dir / "graphics" / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    source_md = source_dir / "doc.md"
    source_md.write_text(
        "# Title\n\n![a chart](graphics/chart.png)\n",
        encoding="utf-8",
    )

    output = tmp_path / "out" / "doc.pdf"
    theme_selection = resolve_selection(name="default")

    pipeline.build_pdf(
        source=source_md,
        output=output,
        theme_selection=theme_selection,
    )

    assert fake_weasyprint.captured["base_url"] == str(source_dir)
    assert 'src="graphics/chart.png"' in fake_weasyprint.captured["string"]


def test_build_pdf_constrains_image_width_regardless_of_theme(tmp_path, fake_weasyprint):
    """No bundled theme defines its own `img` rule (verified by this test's
    intent), so build_pdf must supply a baseline `max-width: 100%` itself —
    otherwise a source image wider than the page renders oversized and gets
    clipped by WeasyPrint instead of scaling down."""
    source_dir = tmp_path / "report"
    source_dir.mkdir()
    (source_dir / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    source_md = source_dir / "doc.md"
    source_md.write_text("# Title\n\n![a chart](chart.png)\n", encoding="utf-8")

    pipeline.build_pdf(
        source=source_md,
        output=tmp_path / "out" / "doc.pdf",
        theme_selection=resolve_selection(name="default"),
    )

    html = fake_weasyprint.captured["string"]
    assert "max-width: 100%" in html
    # the base rule must appear before the theme's own print.css block so a
    # theme can still override it if it ever defines its own `img` rule.
    assert html.index("max-width: 100%") < html.index("print.css — themable layout")
