"""Tests for apply-tags.py's class_tag_promotions rewrite pass.

Promotions move misplaced :::tag markers from inside a class body up to
the class header, picking a winner via the priority precedence
ingress > core > transform > bridge. Orphan classes (no `class Foo`
header) are skipped with a stderr warning.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
APPLY_SCRIPT = PLUGIN_ROOT / "skills" / "preprocess" / "scripts" / "apply-tags.py"


def _run_apply(decisions_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(APPLY_SCRIPT), "--decisions", str(decisions_path)],
        capture_output=True, text=True,
    )


def _write_decisions(tmp_path: Path, source_md: Path, promotions: list[dict]) -> Path:
    decisions = {
        "source": str(source_md),
        "tags": [],
        "class_tag_promotions": promotions,
    }
    p = tmp_path / "decisions.json"
    p.write_text(json.dumps(decisions))
    return p


def test_promotes_misplaced_tag_to_header(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(
        "```mermaid\n"
        "classDiagram\n"
        "    class Foo {\n"
        "        +method1():::core\n"
        "    }\n"
        "```\n"
    )
    decisions = _write_decisions(tmp_path, md, [
        {"index": 0, "class_name": "Foo", "winning_tag": "core"},
    ])
    result = _run_apply(decisions)
    assert result.returncode == 0, result.stderr
    new_text = md.read_text()
    assert "class Foo:::core {" in new_text
    assert ":::core" not in new_text.split("class Foo:::core {", 1)[1].split("}", 1)[0]


def test_priority_wins_ingress_over_core(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(
        "```mermaid\n"
        "classDiagram\n"
        "    class Engine {\n"
        "        +check():::ingress\n"
        "        +run():::core\n"
        "    }\n"
        "```\n"
    )
    decisions = _write_decisions(tmp_path, md, [
        {"index": 0, "class_name": "Engine", "winning_tag": "ingress"},
    ])
    result = _run_apply(decisions)
    assert result.returncode == 0, result.stderr
    new_text = md.read_text()
    assert "class Engine:::ingress {" in new_text
    body = new_text.split("class Engine:::ingress {", 1)[1].split("}", 1)[0]
    assert ":::core" not in body
    assert ":::ingress" not in body


def test_orphan_class_skipped_with_warning(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(
        "```mermaid\n"
        "classDiagram\n"
        "    Foo --> Bar\n"
        "    Foo:::core\n"
        "```\n"
    )
    original = md.read_text()
    decisions = _write_decisions(tmp_path, md, [
        {"index": 0, "class_name": None, "winning_tag": "core"},
    ])
    result = _run_apply(decisions)
    assert result.returncode == 0, result.stderr
    assert md.read_text() == original  # source untouched
    assert "orphan" in result.stderr.lower() or "no class" in result.stderr.lower()


def test_existing_header_tag_preserved_body_stripped(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(
        "```mermaid\n"
        "classDiagram\n"
        "    class Foo:::transform {\n"
        "        +method1():::core\n"
        "    }\n"
        "```\n"
    )
    decisions = _write_decisions(tmp_path, md, [
        {"index": 0, "class_name": "Foo", "winning_tag": "core"},
    ])
    result = _run_apply(decisions)
    assert result.returncode == 0, result.stderr
    new_text = md.read_text()
    assert "class Foo:::transform {" in new_text  # existing kept
    body = new_text.split("class Foo:::transform {", 1)[1].split("}", 1)[0]
    assert ":::core" not in body  # body stripped


def test_idempotent_rerun_no_changes(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(
        "```mermaid\n"
        "classDiagram\n"
        "    class Foo:::core {\n"
        "        +method1()\n"
        "    }\n"
        "```\n"
    )
    original = md.read_text()
    decisions = _write_decisions(tmp_path, md, [
        {"index": 0, "class_name": "Foo", "winning_tag": "core"},
    ])
    result = _run_apply(decisions)
    assert result.returncode == 0, result.stderr
    assert md.read_text() == original  # already correct, no change
