#!/usr/bin/env python
"""Apply tag decisions to a markdown file's mermaid blocks (in place).

Reads a JSON decisions document from --decisions <file> with the schema:

    {
      "source": "/abs/path/to/doc.md",
      "tags": [
        {"index": 0, "node_id": "A", "tag": "ingress"},
        {"index": 0, "node_id": "B", "tag": "core"},
        ...
      ],
      "frontmatter": {                          # optional
        "title":    "Document Title",
        "subtitle": "Optional subtitle",
        "author":   "Optional author",
        "date":     "2026-05-03"
      }
    }

Behavior:
  1. Backs up the original file to
     <source-dir>/.md-publisher/<YYYYMMDD-HHMMSS>/original.md
  2. Rewrites the source IN PLACE with each `<node-id>[label]` augmented
     to `<node-id>[label]:::<tag>` on the FIRST occurrence within the
     specified mermaid block.
  3. If `frontmatter` is present, prepends a YAML --- ... --- block to
     the file (after the backup is taken).

Each invocation produces a fresh timestamp directory; previous backups
remain. Decisions for already-tagged nodes are skipped silently (idempotent).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# Reuse helpers from scan-mermaid via direct import (sibling script)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib.util
_scan_spec = importlib.util.spec_from_file_location(
    "_scan", Path(__file__).resolve().parent / "scan-mermaid.py"
)
_scan = importlib.util.module_from_spec(_scan_spec)
_scan_spec.loader.exec_module(_scan)

# Plugin lib (output_paths) needs CLAUDE_PLUGIN_ROOT or fallback
import os
PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or
                   Path(__file__).resolve().parent.parent.parent.parent)
sys.path.insert(0, str(PLUGIN_ROOT))
from lib import output_paths  # noqa: E402


# Match a node DEFINITION (id immediately followed by [label] or ([rounded])
# or {{hex}} or {rhombus}). Captures the id and the closing bracket position.
NODE_DEF_RE = re.compile(
    r"\b([A-Za-z][\w_-]*)\s*"           # node id
    r"([\[\(\{][^\]\)\}]*[\]\)\}])"     # label-bracket
)


def append_tag_in_block(
    block_src: str, node_id: str, tag: str, *, already_tagged: set[str]
) -> str:
    """Append `:::tag` to the FIRST definition of `node_id` in block_src.

    No-ops if node_id is already in already_tagged (caller's prior pass) or
    no matching node-definition exists.
    """
    if node_id in already_tagged:
        return block_src
    appended = {"done": False}

    def _sub(m: re.Match[str]) -> str:
        if appended["done"]:
            return m.group(0)
        if m.group(1) != node_id:
            return m.group(0)
        appended["done"] = True
        return f"{m.group(0)}:::{tag}"

    return NODE_DEF_RE.sub(_sub, block_src, count=0)


def apply_tags_to_text(text: str, decisions_by_index: dict[int, list[dict]]) -> str:
    """Walk mermaid fences in `text` and apply tag decisions per block index."""
    blocks_processed = [0]

    def replace_block(m: re.Match[str]) -> str:
        idx = blocks_processed[0]
        blocks_processed[0] += 1
        block_src = m.group(2)
        decisions = decisions_by_index.get(idx, [])
        if not decisions:
            return m.group(0)
        # Honor existing tags so we don't double-tag if invoked twice
        _, already = _scan.collect_nodes(block_src)
        for d in decisions:
            block_src = append_tag_in_block(
                block_src, d["node_id"], d["tag"], already_tagged=already
            )
            already.add(d["node_id"])
        return f"```mermaid\n{block_src}\n```"

    return _scan.FENCE_RE.sub(replace_block, text)


def render_frontmatter(meta: dict) -> str:
    """Render a minimal YAML front matter block from a flat dict of strings."""
    lines = ["---"]
    for k in ("title", "subtitle", "author", "date"):
        v = meta.get(k)
        if v:
            # Quote values containing ':' or starting with special chars
            escaped = str(v).replace('"', '\\"')
            lines.append(f'{k}: "{escaped}"')
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--decisions", type=Path, required=True,
                   help="JSON file with the decisions document")
    p.add_argument("--no-backup", action="store_true",
                   help="(dangerous) skip the backup step")
    args = p.parse_args()

    if not args.decisions.exists():
        sys.stderr.write(f"[apply-tags] decisions not found: {args.decisions}\n")
        return 2

    doc = json.loads(args.decisions.read_text(encoding="utf-8"))
    source = Path(doc["source"]).resolve()
    if not source.exists():
        sys.stderr.write(f"[apply-tags] source not found: {source}\n")
        return 2

    decisions_by_index: dict[int, list[dict]] = {}
    for d in doc.get("tags", []):
        decisions_by_index.setdefault(d["index"], []).append(d)

    original_text = source.read_text(encoding="utf-8")

    # Back up the original BEFORE any rewrite
    if not args.no_backup:
        ts = output_paths.timestamp()
        backup = output_paths.derive_backup_path(source, ts=ts)
        output_paths.ensure_parent(backup)
        backup.write_text(original_text, encoding="utf-8")
        print(f"[apply-tags] backed up original to: {backup}")

    new_text = apply_tags_to_text(original_text, decisions_by_index)

    # Optional front matter prepend (only if not already present)
    fm = doc.get("frontmatter")
    if fm and not new_text.lstrip().startswith("---"):
        new_text = render_frontmatter(fm) + new_text

    if new_text == original_text:
        print("[apply-tags] no changes (decisions already applied)")
        return 0

    source.write_text(new_text, encoding="utf-8")
    n_tags = sum(len(v) for v in decisions_by_index.values())
    print(f"[apply-tags] rewrote {source} ({n_tags} tag(s) applied)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
