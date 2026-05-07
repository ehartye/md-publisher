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


# Priority for promoting misplaced classDiagram tags to the class header.
# A class with even one ingress method becomes ingress; ties otherwise
# resolve in this fixed order so the rewrite is deterministic.
TAG_PRIORITY = ("ingress", "core", "transform", "bridge")


def _strip_body_tags(body: str) -> str:
    """Remove any `:::tag` markers from a class body (idempotent)."""
    return re.sub(r"\s*:::\s*[A-Za-z][\w-]*", "", body)


def apply_class_tag_promotions(text: str, promotions: list[dict]) -> tuple[str, list[str]]:
    """Apply class_tag_promotions to a markdown text. Returns (new_text, warnings).

    For each promotion entry `{index, class_name, winning_tag}`:
      - If class_name is None: append a warning, leave source alone.
      - If the class has an explicit `class Foo { ... }` header: rewrite header
        to add the winning tag (or keep an existing header tag if present),
        then strip all `:::tag` markers from the body.
      - If the class is declared bare (`class Foo` with no body): rewrite the
        header to `class Foo:::winning_tag`. (No body to strip.)
    """
    warnings: list[str] = []

    # Index promotions per (block_index, class_name) — last-wins on duplicate.
    by_block: dict[int, list[dict]] = {}
    for p in promotions:
        by_block.setdefault(p["index"], []).append(p)

    blocks_processed = [0]

    def replace_block(m: re.Match[str]) -> str:
        idx = blocks_processed[0]
        blocks_processed[0] += 1
        block_promotions = by_block.get(idx, [])
        if not block_promotions:
            return m.group(0)
        block_src = m.group(2)
        for promo in block_promotions:
            cls = promo["class_name"]
            tag = promo["winning_tag"]
            if cls is None:
                warnings.append(
                    f"orphan class promotion (block {idx}, tag {tag!r}): "
                    "no `class Foo` header found; skipping"
                )
                continue
            block_src = _promote_one_class(block_src, cls, tag)
        return f"```mermaid\n{block_src}\n```"

    new_text = _scan.FENCE_RE.sub(replace_block, text)
    return new_text, warnings


def _promote_one_class(block_src: str, class_name: str, winning_tag: str) -> str:
    """Rewrite a single class within a block's mermaid source.

    Order of operations:
      1. Find `class <name> [:::existing] [{ body }]`.
      2. If the class has a body, strip body :::tag markers regardless.
      3. If the header already has a tag, KEEP it (do not overwrite).
         Otherwise insert `:::winning_tag` after the class name.
      4. If the class has no body (bare `class Foo`), only step 3 applies.
    """
    # With-body form. Note: the trailing `\s*` from the original plan was
    # moved out of `head_prefix` and into `gap` so that re-emission keeps the
    # ` ` between header and `{` (avoids `class Foo :::core{` mis-spacing).
    body_pattern = re.compile(
        rf"(class\s+{re.escape(class_name)})(:::[A-Za-z][\w-]*)?(\s*)\{{(.*?)\}}",
        re.DOTALL,
    )

    def _sub_with_body(m: re.Match[str]) -> str:
        head_prefix, existing_tag, gap, body = m.group(1), m.group(2), m.group(3), m.group(4)
        stripped_body = _strip_body_tags(body)
        if existing_tag:
            tag_part = existing_tag  # keep existing
        else:
            tag_part = f":::{winning_tag}"
        # Ensure at least one space between header (incl. tag) and `{`
        gap_out = gap if gap else " "
        return f"{head_prefix}{tag_part}{gap_out}{{{stripped_body}}}"

    new_block, n_subs = body_pattern.subn(_sub_with_body, block_src, count=1)
    if n_subs:
        return new_block

    # Bare-form fallback: `class Foo` on its own line, no body
    bare_pattern = re.compile(
        rf"(^\s*class\s+{re.escape(class_name)}\s*)(:::[A-Za-z][\w-]*)?(\s*$)",
        re.MULTILINE,
    )

    def _sub_bare(m: re.Match[str]) -> str:
        head_prefix, existing_tag, trailing = m.group(1), m.group(2), m.group(3)
        if existing_tag:
            return m.group(0)  # already tagged, no change
        return f"{head_prefix}:::{winning_tag}{trailing}"

    new_block, _ = bare_pattern.subn(_sub_bare, block_src, count=1)
    return new_block


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

    # Compute the rewritten text first; if nothing actually changes, exit
    # without taking a backup. This keeps idempotent re-runs from piling
    # up empty timestamped backup directories.
    new_text = apply_tags_to_text(original_text, decisions_by_index)

    # Apply class_tag_promotions (Task 2 of mermaid-diagnostics design).
    # Promotions are independent of `tags`; they only touch classDiagram
    # blocks identified by index in the decisions doc.
    promotions = doc.get("class_tag_promotions", [])
    promotion_warnings: list[str] = []
    if promotions:
        new_text, promotion_warnings = apply_class_tag_promotions(new_text, promotions)
    for w in promotion_warnings:
        sys.stderr.write(f"[apply-tags] warning: {w}\n")

    # Optional front matter prepend (only if not already present)
    fm = doc.get("frontmatter")
    if fm and not new_text.lstrip().startswith("---"):
        new_text = render_frontmatter(fm) + new_text

    if new_text == original_text:
        print("[apply-tags] no changes (decisions already applied); no backup written")
        return 0

    # Now that we know there's a real change to write, take the backup first
    if not args.no_backup:
        ts = output_paths.timestamp()
        backup = output_paths.derive_backup_path(source, ts=ts)
        output_paths.ensure_parent(backup)
        backup.write_text(original_text, encoding="utf-8")
        print(f"[apply-tags] backed up original to: {backup}")

    source.write_text(new_text, encoding="utf-8")
    n_tags = sum(len(v) for v in decisions_by_index.values())
    n_promos = len(promotions) - len(promotion_warnings)
    summary = f"{n_tags} tag(s) applied"
    if promotions:
        summary += f", {n_promos} class promotion(s)"
        if promotion_warnings:
            summary += f", {len(promotion_warnings)} orphan-class warning(s)"
    print(f"[apply-tags] rewrote {source} ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
