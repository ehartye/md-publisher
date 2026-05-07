#!/usr/bin/env python
"""Scan a markdown file for mermaid blocks and report which need tagging.

Emits structured JSON to stdout that the preprocess skill consumes to
decide tags. Output schema:

    {
      "source": "/abs/path/to/doc.md",
      "blocks": [
        {
          "index": 0,                    # 0-based block index
          "line_start": 38,              # 1-based line of opening ```mermaid
          "line_end": 46,                # 1-based line of closing ```
          "diagram_type": "flowchart",   # "flowchart" | "graph" | "sequenceDiagram" | "classDiagram" | ...
          "supports_classdef": true,     # only flowchart/graph
          "tagged": false,               # any node has :::class on it?
          "node_count": 12,
          "node_ids": ["A", "B", ...],   # only for flowchart/graph
          "untagged_node_ids": [...],    # IDs that need tags assigned
          "snippet": "flowchart LR\\n    A[...] --> B[...]\\n..."  # first 12 source lines
        },
        ...
      ]
    }

The preprocess skill reads this output, decides a `:::class` for each
untagged node based on diagram semantics, then invokes apply-tags.py
with the decisions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# Match a mermaid fenced code block. Captures the inner source.
FENCE_RE = re.compile(
    r"^([ \t]*)```mermaid[ \t]*\n(.*?)\n[ \t]*```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)

# A node id followed by a label-bracket. We use the same heuristic as the
# bake-off's mermaid_processor / chromium / typst — it's the convention.
NODE_ID_RE = re.compile(r"\b([A-Za-z][\w_-]*)\s*[\[\(\{]")

# A node followed by ":::classname" — used to detect already-tagged nodes
TAGGED_NODE_RE = re.compile(r"\b([A-Za-z][\w_-]*)\s*[\[\(\{][^\]\)\}]*[\]\)\}]\s*:::\s*([A-Za-z][\w-]*)")

# Bare-style tag (e.g. `A:::tag` referenced after first definition)
BARE_TAGGED_RE = re.compile(r"\b([A-Za-z][\w_-]*)\s*:::\s*([A-Za-z][\w-]*)\b")

# First-line diagram-type detection
DIAGRAM_TYPES = (
    "flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram",
    "stateDiagram-v2", "erDiagram", "journey", "gantt", "pie", "quadrantChart",
    "requirementDiagram", "gitGraph", "mindmap", "timeline", "sankey-beta",
    "block-beta", "architecture-beta",
)


def detect_diagram_type(source: str) -> str:
    first = next((line.strip() for line in source.splitlines() if line.strip()), "")
    for t in DIAGRAM_TYPES:
        if first.startswith(t):
            return t
    return "unknown"


def collect_nodes(source: str) -> tuple[list[str], set[str]]:
    """Return (ordered unique node IDs found via label-bracket pattern, set of tagged IDs)."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in NODE_ID_RE.finditer(source):
        node = m.group(1)
        if node not in seen_set:
            seen_set.add(node)
            seen.append(node)
    tagged: set[str] = set()
    for m in TAGGED_NODE_RE.finditer(source):
        tagged.add(m.group(1))
    for m in BARE_TAGGED_RE.finditer(source):
        tagged.add(m.group(1))
    return seen, tagged


# classDiagram: only top-level class declarations are styleable nodes.
# Methods/attributes inside the braces are not separate renderable targets.
CLASS_DECL_RE = re.compile(r"^\s*class\s+([A-Za-z][\w_-]*)", re.MULTILINE)
CLASS_RELATION_RE = re.compile(r"^\s*([A-Za-z][\w_-]*)\s+(?:<\||--|\*--|o--|\.\.)", re.MULTILINE)
CLASS_RELATION_RHS_RE = re.compile(r"(?:<\||--|\*--|o--|\.\.)[>|*o.]*\s+([A-Za-z][\w_-]*)", re.MULTILINE)


def collect_class_nodes(source: str) -> tuple[list[str], set[str]]:
    """For classDiagram: only class-level identifiers, not methods."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for regex in (CLASS_DECL_RE, CLASS_RELATION_RE, CLASS_RELATION_RHS_RE):
        for m in regex.finditer(source):
            node = m.group(1)
            if node not in seen_set:
                seen_set.add(node)
                seen.append(node)
    tagged: set[str] = set()
    for m in BARE_TAGGED_RE.finditer(source):
        tagged.add(m.group(1))
    return seen, tagged


# stateDiagram: state names from transitions and state declarations.
STATE_TRANS_RE = re.compile(r"^\s*([A-Za-z][\w_-]*)\s*-->", re.MULTILINE)
STATE_TRANS_RHS_RE = re.compile(r"-->\s*([A-Za-z][\w_-]*)", re.MULTILINE)
STATE_DECL_RE = re.compile(r"^\s*state\s+\"[^\"]*\"\s+as\s+([A-Za-z][\w_-]*)", re.MULTILINE)


def collect_state_nodes(source: str) -> tuple[list[str], set[str]]:
    """For stateDiagram: state identifiers only."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for regex in (STATE_DECL_RE, STATE_TRANS_RE, STATE_TRANS_RHS_RE):
        for m in regex.finditer(source):
            node = m.group(1)
            if node not in seen_set:
                seen_set.add(node)
                seen.append(node)
    tagged: set[str] = set()
    for m in BARE_TAGGED_RE.finditer(source):
        tagged.add(m.group(1))
    return seen, tagged


# mindmap: root and branch nodes. Indentation-based; identifiers in
# double-parens ((root)), brackets [Branch], or plain text.
MINDMAP_NODE_RE = re.compile(r"^\s+([A-Za-z][\w_-]*)\s*[\[\(\{]", re.MULTILINE)
MINDMAP_ROOT_RE = re.compile(r"^\s+root\(\(([^)]+)\)\)", re.MULTILINE)


def collect_mindmap_nodes(source: str) -> tuple[list[str], set[str]]:
    """For mindmap: branch identifiers."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in MINDMAP_NODE_RE.finditer(source):
        node = m.group(1)
        if node not in seen_set:
            seen_set.add(node)
            seen.append(node)
    tagged: set[str] = set()
    for m in BARE_TAGGED_RE.finditer(source):
        tagged.add(m.group(1))
    return seen, tagged


def line_of_offset(text: str, offset: int) -> int:
    """1-based line number for a character offset in `text`."""
    return text.count("\n", 0, offset) + 1


def scan(source_path: Path) -> dict:
    text = source_path.read_text(encoding="utf-8")
    blocks: list[dict] = []
    for idx, m in enumerate(FENCE_RE.finditer(text)):
        block_src = m.group(2)
        diagram_type = detect_diagram_type(block_src)
        supports_classdef = diagram_type in (
            "flowchart", "graph",
            "stateDiagram-v2", "stateDiagram",
            "classDiagram", "classDiagram-v2",
            "mindmap",
        )
        if supports_classdef:
            if diagram_type in ("classDiagram", "classDiagram-v2"):
                node_ids, tagged = collect_class_nodes(block_src)
            elif diagram_type in ("stateDiagram-v2", "stateDiagram"):
                node_ids, tagged = collect_state_nodes(block_src)
            elif diagram_type == "mindmap":
                node_ids, tagged = collect_mindmap_nodes(block_src)
            else:
                node_ids, tagged = collect_nodes(block_src)
            untagged = [n for n in node_ids if n not in tagged]
        else:
            node_ids, tagged, untagged = [], set(), []
        snippet_lines = block_src.splitlines()[:12]
        blocks.append({
            "index": idx,
            "line_start": line_of_offset(text, m.start()),
            "line_end": line_of_offset(text, m.end()),
            "diagram_type": diagram_type,
            "supports_classdef": supports_classdef,
            "tagged": bool(tagged) and not untagged,
            "node_count": len(node_ids),
            "node_ids": node_ids,
            "untagged_node_ids": untagged,
            "snippet": "\n".join(snippet_lines),
        })
    return {"source": str(source_path.resolve()), "blocks": blocks}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("source", type=Path, help="markdown file to scan")
    args = p.parse_args()
    if not args.source.exists():
        sys.stderr.write(f"[scan-mermaid] not found: {args.source}\n")
        return 2
    result = scan(args.source)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
