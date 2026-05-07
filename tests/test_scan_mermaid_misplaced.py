"""Tests for scan-mermaid.py's misplaced_tags detection (classDiagram)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCAN_SCRIPT = PLUGIN_ROOT / "skills" / "preprocess" / "scripts" / "scan-mermaid.py"


def _run_scan(md_path: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCAN_SCRIPT), str(md_path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def test_correctly_placed_class_header_tag_is_not_misplaced(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(
        "```mermaid\n"
        "classDiagram\n"
        "    class Foo:::ingress {\n"
        "        +method1()\n"
        "    }\n"
        "```\n"
    )
    out = _run_scan(md)
    block = out["blocks"][0]
    assert block["diagram_type"] == "classDiagram"
    assert block.get("misplaced_tags", []) == []


def test_tag_inside_method_body_is_misplaced(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(
        "```mermaid\n"
        "classDiagram\n"
        "    class Foo {\n"
        "        +method1():::core\n"
        "    }\n"
        "```\n"
    )
    out = _run_scan(md)
    block = out["blocks"][0]
    misplaced = block["misplaced_tags"]
    assert len(misplaced) == 1
    assert misplaced[0]["class_name"] == "Foo"
    assert misplaced[0]["tag"] == "core"


def test_multiple_misplaced_tags_all_reported(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(
        "```mermaid\n"
        "classDiagram\n"
        "    class Engine {\n"
        "        +check():::ingress\n"
        "        +run():::core\n"
        "        +helper():::transform\n"
        "    }\n"
        "```\n"
    )
    out = _run_scan(md)
    misplaced = out["blocks"][0]["misplaced_tags"]
    assert len(misplaced) == 3
    tags = sorted(m["tag"] for m in misplaced)
    assert tags == ["core", "ingress", "transform"]
    assert all(m["class_name"] == "Engine" for m in misplaced)


def test_orphan_tag_no_class_header(tmp_path):
    """A :::tag attached to an identifier with no `class Foo` header is orphan."""
    md = tmp_path / "doc.md"
    md.write_text(
        "```mermaid\n"
        "classDiagram\n"
        "    Foo --> Bar\n"
        "    Foo:::core\n"
        "```\n"
    )
    out = _run_scan(md)
    misplaced = out["blocks"][0]["misplaced_tags"]
    assert len(misplaced) == 1
    assert misplaced[0]["class_name"] is None
    assert misplaced[0]["tag"] == "core"


def test_non_classdiagram_blocks_have_empty_misplaced(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(
        "```mermaid\n"
        "flowchart TD\n"
        "    A[Start]:::ingress --> B[End]\n"
        "```\n"
    )
    out = _run_scan(md)
    block = out["blocks"][0]
    assert block["diagram_type"] == "flowchart"
    assert block.get("misplaced_tags", []) == []
