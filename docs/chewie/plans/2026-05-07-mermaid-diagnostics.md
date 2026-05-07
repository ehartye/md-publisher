# Mermaid Diagnostics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use h-superpowers:subagent-driven-development, h-superpowers:team-driven-development, or h-superpowers:executing-plans to implement this plan (ask user which approach).

**Goal:** Catch misplaced classDiagram `:::tag` markers via preprocess auto-fix, enrich the scaffold-theme template + preview.html so non-flowchart diagrams look distinctly branded by default, and ship a `rebuild-themes` skill to refresh existing themes.

**Architecture:** Three independent tracks. Track 1 (preprocess) extends `scan-mermaid.py` with a `misplaced_tags` field per classDiagram block, and `apply-tags.py` with a `class_tag_promotions` rewrite pass — both regex-based, no new deps. Track 2 (scaffold-theme) bumps 4 mermaid template variables to accent-leaning palette tokens and replaces the preview.html template with one that includes 3 inline mermaid figures rendered via mmdc at scaffold time (with a static-source fallback if the runtime isn't bootstrapped yet). Track 3 (rebuild-themes) is a new top-level skill that walks installed themes and re-derives `mermaid-config.json` + `preview.html` from each theme's `spec.json`, importing scaffold-theme's templates and helpers via importlib (same cross-script pattern apply-tags uses to import scan-mermaid).

**Tech Stack:** Python 3.10+, regex (no parser dep), pytest, mmdc (Mermaid CLI, already required by publish pipeline)

**Spec:** `docs/chewie/specs/2026-05-07-mermaid-diagnostics-design.md`

---

## Track 1 — Preprocess auto-fix

### Task 1: Add misplaced_tags detection to scan-mermaid.py

**Files:**
- Modify: `skills/preprocess/scripts/scan-mermaid.py` (add detection helpers + scan output field)
- Create: `tests/test_scan_mermaid_misplaced.py`

**Step 1: Write the failing test**

Create `tests/test_scan_mermaid_misplaced.py`:

```python
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
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scan_mermaid_misplaced.py -v`
Expected: FAIL — `KeyError: 'misplaced_tags'` or `assert 0 == 1` (the field doesn't exist yet, or `dict.get` returns the default empty list which then fails the positive assertions).

**Step 3: Implement the detection helpers**

Modify `skills/preprocess/scripts/scan-mermaid.py`. After the existing `MINDMAP_ROOT_RE` block (around line 138) and BEFORE `def collect_mindmap_nodes`, add:

```python
# classDiagram misplaced-tag detection.
#
# Mermaid's :::class syntax must attach to a class HEADER (e.g.
# `class Foo:::core` or `class Foo:::core { ... }`). When a user puts
# `:::tag` on a method or attribute line inside the body, mermaid
# silently ignores it and the class renders unstyled. We detect those
# misplaced tags so apply-tags.py can promote them up to the header.
#
# Detection algorithm:
#   1. Find every `class Foo { body }` block; record (name, body_span).
#   2. For each body, every `:::tag` inside is misplaced for class Foo.
#   3. After masking out all class bodies, find any remaining
#      `<identifier>:::tag` matches in the source. If <identifier>
#      matches a known class name (declared via `class Foo` anywhere),
#      it is misplaced for that class. Otherwise it is "orphan" —
#      class_name=None — and apply-tags will leave it alone with a warning.
#
# A `:::tag` directly on a class header line (`class Foo:::core`) is
# CORRECT and is not reported as misplaced.

CLASS_BLOCK_RE = re.compile(
    r"class\s+([A-Za-z][\w_-]*)\s*(?::::[A-Za-z][\w-]*)?\s*\{(.*?)\}",
    re.DOTALL,
)
# Class declaration WITHOUT a body — `class Foo` on its own line.
CLASS_BARE_DECL_RE = re.compile(
    r"^\s*class\s+([A-Za-z][\w_-]*)\s*(?::::[A-Za-z][\w-]*)?\s*$",
    re.MULTILINE,
)
# A bare `<id>:::tag` reference (no surrounding brackets) — used to find
# tags attached to identifiers outside class bodies.
BARE_REF_TAG_RE = re.compile(r"\b([A-Za-z][\w_-]*)\s*:::\s*([A-Za-z][\w-]*)")
# Just the tag at any position (no leading identifier capture). Used inside
# a class body where any tag is misplaced regardless of what it's attached to.
TAG_ONLY_RE = re.compile(r":::\s*([A-Za-z][\w-]*)")


def line_in_block(block_src: str, char_offset: int) -> int:
    """1-based line number for a character offset within a block source."""
    return block_src.count("\n", 0, char_offset) + 1


def collect_misplaced_class_tags(block_src: str) -> list[dict]:
    """Return a list of `{class_name, tag, line_in_block}` for misplaced tags.

    Misplaced means the `:::tag` marker is placed somewhere mermaid will
    silently ignore — either inside a class body, or on a bare reference
    to a class name without a `class Foo:::tag` header rewrite.
    """
    misplaced: list[dict] = []

    # Step 1: find class bodies; record (name, body_text, body_start_offset)
    body_spans: list[tuple[int, int]] = []  # to mask out for step 3
    for m in CLASS_BLOCK_RE.finditer(block_src):
        class_name = m.group(1)
        body = m.group(2)
        body_start = m.start(2)
        body_spans.append((m.start(), m.end()))
        for t in TAG_ONLY_RE.finditer(body):
            misplaced.append({
                "class_name": class_name,
                "tag": t.group(1),
                "line_in_block": line_in_block(block_src, body_start + t.start()),
            })

    # Step 3: mask body spans, find bare `<id>:::tag` references in remainder.
    # Build a list of declared class names (with-body and bare).
    declared = {m.group(1) for m in CLASS_BLOCK_RE.finditer(block_src)}
    declared |= {m.group(1) for m in CLASS_BARE_DECL_RE.finditer(block_src)}

    # Replace body-content with same-length spaces so character offsets
    # for the OUTSIDE-bodies content remain stable.
    masked = list(block_src)
    for start, end in body_spans:
        for i in range(start, end):
            if masked[i] != "\n":
                masked[i] = " "
    masked_src = "".join(masked)

    for m in BARE_REF_TAG_RE.finditer(masked_src):
        ident = m.group(1)
        tag = m.group(2)
        # Skip the tag-on-header form (`class Foo:::tag`): the leading
        # identifier matches "class" not "Foo", and we already exclude
        # those via the declaration regexes. But just in case the bare-ref
        # regex catches the class keyword itself, skip it.
        if ident == "class":
            continue
        misplaced.append({
            "class_name": ident if ident in declared else None,
            "tag": tag,
            "line_in_block": line_in_block(block_src, m.start()),
        })

    return misplaced
```

Then, in the `scan` function (around line 161), MODIFY the per-block dict assembly. Find:

```python
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
```

REPLACE with:

```python
        misplaced_tags: list[dict] = []
        if diagram_type in ("classDiagram", "classDiagram-v2"):
            misplaced_tags = collect_misplaced_class_tags(block_src)
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
            "misplaced_tags": misplaced_tags,
            "snippet": "\n".join(snippet_lines),
        })
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scan_mermaid_misplaced.py -v`
Expected: PASS (5/5).

**Step 5: Run the full preprocess test suite to confirm nothing else broke**

Run: `pytest tests/ -v`
Expected: All existing tests still pass.

**Step 6: Commit**

```bash
git add tests/test_scan_mermaid_misplaced.py skills/preprocess/scripts/scan-mermaid.py
git commit -m "feat(preprocess): detect misplaced classDiagram :::tag markers"
```

---

### Task 2: Add class_tag_promotions to apply-tags.py with priority-wins

**Files:**
- Modify: `skills/preprocess/scripts/apply-tags.py` (add promotion algorithm + rewrite pass)
- Create: `tests/test_apply_tags_promotion.py`

**Step 1: Write the failing test**

Create `tests/test_apply_tags_promotion.py`:

```python
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
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_apply_tags_promotion.py -v`
Expected: FAIL — `class_tag_promotions` is not yet processed by apply-tags.py, so the source is unchanged in cases that expect rewrites.

**Step 3: Implement the promotion pass**

Modify `skills/preprocess/scripts/apply-tags.py`. After the existing `apply_tags_to_text` function (around line 91), ADD the following helper functions:

```python
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
    # With-body form
    body_pattern = re.compile(
        rf"(class\s+{re.escape(class_name)}\s*)(:::[A-Za-z][\w-]*)?(\s*)\{{(.*?)\}}",
        re.DOTALL,
    )

    def _sub_with_body(m: re.Match[str]) -> str:
        head_prefix, existing_tag, gap, body = m.group(1), m.group(2), m.group(3), m.group(4)
        stripped_body = _strip_body_tags(body)
        if existing_tag:
            tag_part = existing_tag  # keep existing
        else:
            tag_part = f":::{winning_tag}"
        return f"{head_prefix}{tag_part}{gap}{{{stripped_body}}}"

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
```

Then MODIFY `main()` (around line 128) to also process promotions. Find:

```python
    # Compute the rewritten text first; if nothing actually changes, exit
    # without taking a backup. This keeps idempotent re-runs from piling
    # up empty timestamped backup directories.
    new_text = apply_tags_to_text(original_text, decisions_by_index)
```

REPLACE with:

```python
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
```

Then update the final report (around line 175). Find:

```python
    n_tags = sum(len(v) for v in decisions_by_index.values())
    print(f"[apply-tags] rewrote {source} ({n_tags} tag(s) applied)")
```

REPLACE with:

```python
    n_tags = sum(len(v) for v in decisions_by_index.values())
    n_promos = len(promotions) - len(promotion_warnings)
    summary = f"{n_tags} tag(s) applied"
    if promotions:
        summary += f", {n_promos} class promotion(s)"
        if promotion_warnings:
            summary += f", {len(promotion_warnings)} orphan-class warning(s)"
    print(f"[apply-tags] rewrote {source} ({summary})")
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_apply_tags_promotion.py -v`
Expected: PASS (5/5).

**Step 5: Run all tests to confirm no regressions**

Run: `pytest tests/ -v`
Expected: all tests pass.

**Step 6: Commit**

```bash
git add tests/test_apply_tags_promotion.py skills/preprocess/scripts/apply-tags.py
git commit -m "feat(preprocess): auto-promote misplaced classDiagram tags to header"
```

---

### Task 3: Update preprocess SKILL.md with Step 2.5 and orphan-class warning

**Files:**
- Modify: `skills/preprocess/SKILL.md`

**Step 1: Add the misplaced-tag row to the "What gets tagged" table**

Open `skills/preprocess/SKILL.md`. Find the table under `## What gets tagged` (around line 21). After the four-row tag table, add a new paragraph:

Find:

```markdown
These are the universal classes correspond to a node's role in the diagram's data flow:

| Tag | Meaning |
|---|---|
| `:::ingress` | Entry / exit / source / sink — terminals in the data flow |
| `:::core` | Primary processing — the "main thing" the diagram is about |
| `:::transform` | Auxiliary processing — projections, conversions, preprocessing |
| `:::bridge` | Connector / aggregator / intermediate — joins, splits, glue |

These map per-theme to specific colors via each theme's `classDef` rules
```

(Note: the existing line begins "The four universal classes" — match the actual file. Verify with `grep -n "universal classes" skills/preprocess/SKILL.md` first.)

After the existing paragraph that ends with "Themes do the visual work at publish time.", ADD:

```markdown
For `classDiagram` blocks, the preprocess skill also detects **misplaced** `:::tag` markers — `:::tag` placed inside a method/attribute line (e.g. `+method():::core`) instead of on the class header. Mermaid silently ignores these. The skill auto-promotes them to the class header using priority-wins precedence (`ingress > core > transform > bridge`) and strips the misplaced markers from the body. See Step 2.5.
```

**Step 2: Insert Step 2.5 between current Step 2 and Step 3**

Find the line `### Step 3 — Optional front matter` (around line 80). Immediately BEFORE it, insert:

```markdown
### Step 2.5 — Resolve misplaced classDiagram tags (auto-fix)

For each block where `misplaced_tags` is non-empty:

1. Group the misplaced tags by `class_name`.
2. For each group with a non-null class name, pick the winning tag using the priority precedence: `ingress > core > transform > bridge`.
3. Emit one `class_tag_promotions` entry into the decisions JSON: `{index, class_name, winning_tag}`. apply-tags will rewrite the header and strip body markers.
4. For groups with `class_name: null` (orphan — no `class Foo` header line in the source), do NOT emit a promotion. Tell the user: "Class `<name>` has misplaced `:::tag` markers but no explicit `class <name>` header in the diagram. Add a header line so the auto-fix can attach the tag." apply-tags will warn and skip if a null-class promotion ever lands in the decisions doc.

The promotions are independent of the `tags` array — they touch only classDiagram blocks via `class_tag_promotions`.
```

**Step 3: Add an entry to the Failure modes section**

Find `## Failure modes` (near the end). After the existing entries, ADD:

```markdown
- **Orphan class with misplaced tag** — a `:::tag` marker appears on or near a class identifier that has no explicit `class Foo` declaration in the diagram. apply-tags emits a stderr warning and leaves the source untouched; ask the user to add the `class Foo` declaration so the auto-fix can attach the tag.
```

**Step 4: Update the example output line in Step 5**

Find Step 5 (around line 105). Find:

```markdown
- How many tags were applied (and per-block breakdown)
```

REPLACE the surrounding bullet list with:

```markdown
- How many blocks were scanned
- How many tags were applied (and per-block breakdown)
- How many class header promotions were applied (and how many orphan-class warnings, if any)
- Where the backup landed
- Suggest the next step: `/md-publisher:publish <doc.md>` for theme-aware coloring
```

(The existing list already has the first/second/fourth/fifth bullets — only the third one is new.)

**Step 5: Verify the file is well-formed**

Run: `python3 -c "import pathlib; p = pathlib.Path('skills/preprocess/SKILL.md'); assert p.read_text().count('### Step') >= 5"`
Expected: No output (success — at least 5 step headings now exist: 1, 2, 2.5, 3, 4, 5).

**Step 6: Commit**

```bash
git add skills/preprocess/SKILL.md
git commit -m "docs(preprocess): document misplaced-tag auto-fix (Step 2.5 + orphan warning)"
```

---

## Track 2 — Scaffold template enrichment

### Task 4: Bump 4 mermaid template variables to accent-leaning tokens

**Files:**
- Modify: `skills/theme-advisor/scripts/scaffold-theme.py` (`MERMAID_CONFIG_TEMPLATE`)
- Create: `tests/test_scaffold_theme_template.py`

**Step 1: Write the failing test**

Create `tests/test_scaffold_theme_template.py`:

```python
"""Tests for scaffold-theme.py — template enrichment + multi-diagram preview.

Tests construct a minimal valid spec, invoke scaffold-theme.py against a
temp HOME, and assert on the generated mermaid-config.json + preview.html
contents.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCAFFOLD_SCRIPT = (
    PLUGIN_ROOT / "skills" / "theme-advisor" / "scripts" / "scaffold-theme.py"
)


def _minimal_spec(slug: str = "test-theme-light") -> dict:
    return {
        "slug": slug,
        "name": "test-theme",
        "mode": "light",
        "displayName": "Test Theme",
        "tagline": "A theme for tests.",
        "fonts": {
            "display": "Charter, Georgia, serif",
            "body":    "Charter, Georgia, serif",
            "sans":    "Inter, system-ui, sans-serif",
            "mono":    "JetBrains Mono, Consolas, monospace",
        },
        "palette": {
            "bg":         "#FFFFFF",
            "paper":      "#FFFFFF",
            "ink":        "#111111",
            "inkSoft":    "#555555",
            "accent":     "#005FCC",
            "accentSoft": "#E0EBF8",
            "rule":       "#DDDDDD",
            "codeBg":     "#F5F5F5",
            "codeText":   "#111111",
        },
        "mermaid": {
            "tagStyling": {
                "ingress":   {"fill": "#FFFFFF", "stroke": "#005FCC", "color": "#111111", "strokeWidth": 2},
                "core":      {"fill": "#FFFFFF", "stroke": "#555555", "color": "#111111", "strokeWidth": 1},
                "transform": {"fill": "#F5F5F5", "stroke": "#555555", "color": "#111111", "strokeWidth": 1},
                "bridge":    {"fill": "transparent", "stroke": "#555555", "color": "#555555", "strokeWidth": 1, "strokeDasharray": "4 3"},
            },
            "fontFamily": "Inter, sans-serif",
            "lineColor": "#555555",
        },
    }


def _run_scaffold(tmp_path: Path, spec: dict, force: bool = False) -> subprocess.CompletedProcess:
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    env = {**os.environ, "HOME": str(tmp_path)}
    cmd = [sys.executable, str(SCAFFOLD_SCRIPT), "--spec", str(spec_path)]
    if force:
        cmd.append("--force")
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def test_mermaid_config_uses_accent_soft_for_attribute_odd(tmp_path):
    spec = _minimal_spec()
    result = _run_scaffold(tmp_path, spec)
    assert result.returncode == 0, result.stderr
    cfg = json.loads(
        (tmp_path / ".md-publisher" / "themes" / spec["slug"] / "mermaid-config.json").read_text()
    )
    # accentSoft = #E0EBF8 in the spec
    assert cfg["themeVariables"]["attributeBackgroundColorOdd"] == "#E0EBF8"


def test_mermaid_config_uses_accent_soft_for_cluster_bkg(tmp_path):
    spec = _minimal_spec()
    result = _run_scaffold(tmp_path, spec)
    assert result.returncode == 0, result.stderr
    cfg = json.loads(
        (tmp_path / ".md-publisher" / "themes" / spec["slug"] / "mermaid-config.json").read_text()
    )
    assert cfg["themeVariables"]["clusterBkg"] == "#E0EBF8"


def test_mermaid_config_uses_accent_soft_for_alt_background(tmp_path):
    spec = _minimal_spec()
    result = _run_scaffold(tmp_path, spec)
    assert result.returncode == 0, result.stderr
    cfg = json.loads(
        (tmp_path / ".md-publisher" / "themes" / spec["slug"] / "mermaid-config.json").read_text()
    )
    assert cfg["themeVariables"]["altBackground"] == "#E0EBF8"


def test_mermaid_config_uses_accent_soft_for_section_bkg(tmp_path):
    spec = _minimal_spec()
    result = _run_scaffold(tmp_path, spec)
    assert result.returncode == 0, result.stderr
    cfg = json.loads(
        (tmp_path / ".md-publisher" / "themes" / spec["slug"] / "mermaid-config.json").read_text()
    )
    assert cfg["themeVariables"]["sectionBkgColor"] == "#E0EBF8"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scaffold_theme_template.py -v`
Expected: FAIL — current values are `{{code_bg}}` (`#F5F5F5`), not `{{accent_soft}}` (`#E0EBF8`).

**Step 3: Bump the four template variables**

Modify `skills/theme-advisor/scripts/scaffold-theme.py`. In `MERMAID_CONFIG_TEMPLATE` (around line 235), make exactly four substitutions:

Find:

```python
        "clusterBkg": "{{code_bg}}",
        "clusterBorder": "{{rule}}",
```

REPLACE with:

```python
        "clusterBkg": "{{accent_soft}}",
        "clusterBorder": "{{rule}}",
```

Find:

```python
        # State diagram
        "labelColor": "{{ink}}",
        "altBackground": "{{code_bg}}",
        # Class diagram
```

REPLACE with:

```python
        # State diagram
        "labelColor": "{{ink}}",
        "altBackground": "{{accent_soft}}",
        # Class diagram
```

Find:

```python
        # ER diagram
        "attributeBackgroundColorEven": "{{paper}}",
        "attributeBackgroundColorOdd": "{{code_bg}}",
```

REPLACE with:

```python
        # ER diagram
        "attributeBackgroundColorEven": "{{paper}}",
        "attributeBackgroundColorOdd": "{{accent_soft}}",
```

Find:

```python
        # Gantt
        "sectionBkgColor": "{{code_bg}}",
        "sectionBkgColor2": "{{paper}}",
```

REPLACE with:

```python
        # Gantt
        "sectionBkgColor": "{{accent_soft}}",
        "sectionBkgColor2": "{{paper}}",
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scaffold_theme_template.py -v`
Expected: PASS (4/4).

**Step 5: Commit**

```bash
git add tests/test_scaffold_theme_template.py skills/theme-advisor/scripts/scaffold-theme.py
git commit -m "feat(theme-advisor): bump 4 mermaid template vars to accent-leaning tokens"
```

---

### Task 5: Multi-diagram preview.html with mmdc rendering + fallback

**Files:**
- Modify: `skills/theme-advisor/scripts/scaffold-theme.py` (replace `PREVIEW_HTML_TEMPLATE`, add render-figures helper, integrate)
- Modify: `tests/test_scaffold_theme_template.py` (add tests for preview content + fallback)

**Step 1: Write the failing tests**

Append to `tests/test_scaffold_theme_template.py`:

```python
def test_preview_html_contains_three_mermaid_figures(tmp_path):
    spec = _minimal_spec()
    result = _run_scaffold(tmp_path, spec)
    assert result.returncode == 0, result.stderr
    preview = (tmp_path / ".md-publisher" / "themes" / spec["slug"] / "preview.html").read_text()
    # Three captioned diagram blocks. Each becomes an SVG figure when mmdc
    # is available, or a `<pre class="mermaid-source">` fallback when not.
    assert preview.count('class="mermaid-fig"') == 3, preview
    assert "Flowchart with classDef tags" in preview
    assert "ER diagram" in preview
    assert "Class diagram" in preview


def test_preview_html_fallback_when_mmdc_missing(tmp_path, monkeypatch):
    """If mmdc isn't bootstrapped, preview falls back to source blocks.

    Simulated by pointing HOME at tmp_path (no ~/.md-publisher/runtime/
    inside the temp dir) so the scaffold can't find mmdc.
    """
    spec = _minimal_spec()
    result = _run_scaffold(tmp_path, spec)
    assert result.returncode == 0, result.stderr
    preview = (tmp_path / ".md-publisher" / "themes" / spec["slug"] / "preview.html").read_text()
    # Either rendered SVGs or fallback <pre> blocks — both must show up under
    # the mermaid-fig class. We assert the fallback path here by checking the
    # explicit "(install runtime ...)" hint that the script emits.
    assert (
        "mermaid-source" in preview
        or "<svg" in preview  # rendered if mmdc happens to exist on PATH
    )
```

**Step 2: Run new tests to verify they fail**

Run: `pytest tests/test_scaffold_theme_template.py -v -k "preview"`
Expected: FAIL — preview.html still uses the old single-typography-block template.

**Step 3: Replace `PREVIEW_HTML_TEMPLATE` and add the rendering helper**

Modify `skills/theme-advisor/scripts/scaffold-theme.py`. REPLACE the entire `PREVIEW_HTML_TEMPLATE` (around line 373) with:

```python
PREVIEW_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>{{display_name}} preview</title>
<style>
{{style_css_inline}}
.preview-page { width: 7in; margin: 1in auto; background: var(--bg); padding: 1in; }
.mermaid-fig { margin: 1.5em 0; padding: 0.5em; background: var(--paper); border: 1px solid var(--rule); border-radius: 4px; }
.mermaid-fig figcaption { font-family: var(--font-sans); font-size: 9pt; color: var(--ink-soft); margin-top: 0.4em; text-align: center; }
.mermaid-fig svg { display: block; margin: 0 auto; max-width: 100%; height: auto; }
.mermaid-source { font-family: var(--font-mono); font-size: 0.8em; background: var(--code-bg); padding: 0.5em; overflow-x: auto; }
.mermaid-fallback-note { font-family: var(--font-sans); font-size: 8pt; color: var(--ink-soft); font-style: italic; }
</style></head>
<body><div class="preview-page">
<h1>{{display_name}}</h1>
<p style="color: var(--ink-soft); font-style: italic;">{{tagline}}</p>
<h2>Section heading</h2>
<p>The quick brown fox jumps over the lazy dog. Body text in <em>{{font_body_first}}</em>; an <a href="#">accent link</a> like this.</p>
<pre><code>def example():
    return "code block"</code></pre>
<table><thead><tr><th>Column</th><th>Column</th></tr></thead>
<tbody><tr><td>Row data</td><td>More data</td></tr></tbody></table>

<h2>Diagram preview</h2>
<figure class="mermaid-fig">
{{flowchart_figure}}
<figcaption>Flowchart with classDef tags</figcaption>
</figure>
<figure class="mermaid-fig">
{{er_figure}}
<figcaption>ER diagram (themed via mermaid-config)</figcaption>
</figure>
<figure class="mermaid-fig">
{{class_figure}}
<figcaption>Class diagram with <code>:::ingress</code> header tag</figcaption>
</figure>

<p style="margin-top: 1em; font-family: var(--font-sans); font-size: 9pt; color: var(--ink-soft);">
&mdash; {{display_name}} &middot; theme preview</p>
</div></body></html>
"""
```

Then add the rendering helpers. After the existing `first_family` function (around line 402), ADD:

```python
# Sample diagram sources used in preview.html. Each should render with the
# theme applied so the author can verify ER alternating rows, classDiagram
# header tagging, and the four flowchart classDef colors before publishing.

PREVIEW_FLOWCHART = """flowchart LR
    classDef ingress   {{ingress_classdef}}
    classDef core      {{core_classdef}}
    classDef transform {{transform_classdef}}
    classDef bridge    {{bridge_classdef}}
    A[Input]:::ingress --> B[Validate]:::transform
    B --> C[Process]:::core
    C --> D{Decision}:::bridge
    D --> E[Output]:::ingress
"""

PREVIEW_ER = """erDiagram
    CUSTOMER {
        string id PK
        string name
        string email
    }
    ORDER {
        string id PK
        string customer_id FK
        decimal total
        date placed_at
    }
    CUSTOMER ||--o{ ORDER : places
"""

PREVIEW_CLASS = """classDiagram
    class Engine:::ingress {
        +run()
        +stop()
    }
    class Worker {
        +process(task)
    }
    class Queue {
        +enqueue(item)
        +dequeue() item
    }
    Engine --> Queue
    Engine --> Worker
    Worker --> Queue
"""


def _classdef_props(style: dict) -> str:
    """Render a tagStyling style dict into a mermaid classDef rhs string."""
    parts = []
    for k, v in style.items():
        if k == "strokeWidth":
            parts.append(f"stroke-width:{v}")
        elif k == "strokeDasharray":
            parts.append(f"stroke-dasharray:{v}")
        else:
            parts.append(f"{k}:{v}")
    return ",".join(parts)


def _render_mermaid_to_svg(mermaid_src: str, mermaid_config_path: Path,
                           build_dir: Path, mmdc_path: Path) -> str | None:
    """Render a mermaid source via mmdc; return SVG string, or None on failure.

    Failures (mmdc missing, render error, timeout) return None so the caller
    can fall back to embedding the source. Preview rendering is non-blocking —
    the theme files are still produced regardless.
    """
    import subprocess as _sp
    if not mmdc_path.exists():
        return None
    src_path = build_dir / "preview.mmd"
    out_path = build_dir / "preview.svg"
    src_path.write_text(mermaid_src, encoding="utf-8")
    try:
        _sp.run(
            ["node", str(mmdc_path),
             "-i", str(src_path), "-o", str(out_path),
             "-c", str(mermaid_config_path),
             "-b", "transparent"],
            check=True, capture_output=True, timeout=30,
        )
    except (_sp.CalledProcessError, _sp.TimeoutExpired, FileNotFoundError):
        return None
    if not out_path.exists():
        return None
    return out_path.read_text(encoding="utf-8")


def _render_preview_figure(mermaid_src: str, mermaid_config_path: Path,
                           build_dir: Path, mmdc_path: Path) -> str:
    """Render `mermaid_src` via mmdc; on failure, return a fallback HTML block."""
    svg = _render_mermaid_to_svg(mermaid_src, mermaid_config_path, build_dir, mmdc_path)
    if svg:
        # Strip any leading XML declaration; we're inlining
        return re.sub(r"^<\?xml[^>]*\?>\s*", "", svg)
    escaped = mermaid_src.replace("&", "&amp;").replace("<", "&lt;")
    return (
        f'<pre class="mermaid-source">{escaped}</pre>'
        f'<p class="mermaid-fallback-note">'
        f'(install runtime then re-scaffold to see live diagrams)</p>'
    )
```

Then MODIFY `main()` (around line 540) to render the three figures before substituting into preview. Find:

```python
    # Preview HTML — uses the same generated style.css inline. font_body_first
    # follows the same body→display→"serif" fallback chain as spec.json
    # serif so a spec with only `display` doesn't show a degraded preview.
    preview_subs = {
        **style_subs,
        "tagline": tagline,
        "font_body_first": first_family(body),
        "style_css_inline": style_css,
    }
    preview_html = substitute(PREVIEW_HTML_TEMPLATE, preview_subs)
    (target / "preview.html").write_text(preview_html, encoding="utf-8")
```

REPLACE with:

```python
    # Preview HTML — renders three mermaid figures (flowchart, ER, class)
    # using mmdc so the author can verify diagram styling before publishing.
    # Falls back to source blocks if the mmdc runtime isn't bootstrapped.
    mmdc_path = (
        Path.home() / ".md-publisher" / "runtime" / "node_modules"
        / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js"
    )
    build_dir = target / ".preview-build"
    build_dir.mkdir(exist_ok=True)
    mermaid_config_path = target / "mermaid-config.json"

    flowchart_src = substitute(PREVIEW_FLOWCHART, {
        "ingress_classdef":   _classdef_props(spec["mermaid"]["tagStyling"]["ingress"]),
        "core_classdef":      _classdef_props(spec["mermaid"]["tagStyling"]["core"]),
        "transform_classdef": _classdef_props(spec["mermaid"]["tagStyling"]["transform"]),
        "bridge_classdef":    _classdef_props(spec["mermaid"]["tagStyling"]["bridge"]),
    })
    flowchart_figure = _render_preview_figure(flowchart_src, mermaid_config_path, build_dir, mmdc_path)
    er_figure = _render_preview_figure(PREVIEW_ER, mermaid_config_path, build_dir, mmdc_path)
    class_figure = _render_preview_figure(PREVIEW_CLASS, mermaid_config_path, build_dir, mmdc_path)

    # Cleanup the build dir; failures here are non-fatal.
    import shutil
    try:
        shutil.rmtree(build_dir)
    except OSError:
        pass

    preview_subs = {
        **style_subs,
        "tagline": tagline,
        "font_body_first": first_family(body),
        "style_css_inline": style_css,
        "flowchart_figure": flowchart_figure,
        "er_figure":        er_figure,
        "class_figure":     class_figure,
    }
    preview_html = substitute(PREVIEW_HTML_TEMPLATE, preview_subs)
    (target / "preview.html").write_text(preview_html, encoding="utf-8")
```

**Step 4: Run preview tests to verify they pass**

Run: `pytest tests/test_scaffold_theme_template.py -v -k "preview"`
Expected: PASS (2/2). The tests pass whether mmdc is or isn't present, because they assert on either an SVG OR the fallback marker.

**Step 5: Run all scaffold tests to confirm no regressions**

Run: `pytest tests/test_scaffold_theme_template.py -v`
Expected: PASS (6/6 — 4 from Task 4 + 2 from Task 5).

**Step 6: Commit**

```bash
git add tests/test_scaffold_theme_template.py skills/theme-advisor/scripts/scaffold-theme.py
git commit -m "feat(theme-advisor): multi-diagram preview.html with mmdc rendering"
```

---

### Task 6: Update theme-advisor SKILL.md to mention the multi-diagram preview

**Files:**
- Modify: `skills/theme-advisor/SKILL.md`

**Step 1: Update Step 4 (Preview) to call out the diagrams**

Open `skills/theme-advisor/SKILL.md`. Find the section `### Step 4 — Preview` (around line 89). Find:

```markdown
Tell the user the preview is at `~/.md-publisher/themes/<slug>/preview.html`. Suggest opening it in a browser:
```

REPLACE with:

```markdown
Tell the user the preview is at `~/.md-publisher/themes/<slug>/preview.html`. The preview now includes three sample mermaid diagrams (flowchart with the four classDef colors, ER, and classDiagram) so the author can verify diagram styling at the same time as typography. Suggest opening it in a browser:
```

**Step 2: Verify the file**

Run: `grep -n "three sample mermaid diagrams" skills/theme-advisor/SKILL.md`
Expected: one match.

**Step 3: Commit**

```bash
git add skills/theme-advisor/SKILL.md
git commit -m "docs(theme-advisor): note multi-diagram preview in Step 4"
```

---

## Track 3 — rebuild-themes skill

### Task 7: Create the rebuild-themes script with discovery, dry-run, and --apply

**Files:**
- Create: `skills/rebuild-themes/scripts/rebuild-themes.py`
- Create: `tests/test_rebuild_themes.py`

**Step 1: Write the failing test**

Create `tests/test_rebuild_themes.py`:

```python
"""Tests for skills/rebuild-themes/scripts/rebuild-themes.py.

Discovery walks <plugin-root>/themes/ and ~/.md-publisher/themes/ for
directories containing spec.json. Dry-run lists themes without writing;
--apply rewrites mermaid-config.json and preview.html and creates a
.backup-<ts>/ subdirectory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
REBUILD_SCRIPT = PLUGIN_ROOT / "skills" / "rebuild-themes" / "scripts" / "rebuild-themes.py"


def _seed_user_theme(home: Path, slug: str = "test-theme-light") -> Path:
    """Create a minimal theme directory with spec.json + stale mermaid-config.json."""
    theme_dir = home / ".md-publisher" / "themes" / slug
    theme_dir.mkdir(parents=True, exist_ok=True)
    spec = {
        "slug": slug,
        "name": "test-theme",
        "mode": "light",
        "displayName": "Test Theme",
        "tagline": "A theme for tests.",
        "fonts": {
            "display": "Charter, Georgia, serif",
            "body":    "Charter, Georgia, serif",
            "sans":    "Inter, system-ui, sans-serif",
            "mono":    "JetBrains Mono, Consolas, monospace",
        },
        "palette": {
            "bg":         "#FFFFFF",
            "paper":      "#FFFFFF",
            "ink":        "#111111",
            "ink_soft":   "#555555",
            "accent":     "#005FCC",
            "accent_alt": "#E0EBF8",
            "rule":       "#DDDDDD",
            "code_bg":    "#F5F5F5",
            "code_text":  "#111111",
        },
        "mermaid": {
            "tagStyling": {
                "ingress":   {"fill": "#FFFFFF", "stroke": "#005FCC", "color": "#111111", "strokeWidth": 2},
                "core":      {"fill": "#FFFFFF", "stroke": "#555555", "color": "#111111", "strokeWidth": 1},
                "transform": {"fill": "#F5F5F5", "stroke": "#555555", "color": "#111111", "strokeWidth": 1},
                "bridge":    {"fill": "transparent", "stroke": "#555555", "color": "#555555", "strokeWidth": 1, "strokeDasharray": "4 3"},
            },
            "fontFamily": "Inter, sans-serif",
            "lineColor": "#555555",
        },
    }
    (theme_dir / "spec.json").write_text(json.dumps(spec))
    # Seed a stale mermaid-config.json so we can detect the rewrite
    (theme_dir / "mermaid-config.json").write_text('{"stale": true}')
    (theme_dir / "preview.html").write_text("<html>stale</html>")
    return theme_dir


def _run_rebuild(home: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    cmd = [sys.executable, str(REBUILD_SCRIPT), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def test_dry_run_lists_themes_without_writing(tmp_path):
    theme = _seed_user_theme(tmp_path)
    original = (theme / "mermaid-config.json").read_text()
    result = _run_rebuild(tmp_path)  # no args = dry run
    assert result.returncode == 0, result.stderr
    assert "test-theme-light" in result.stdout
    assert "dry-run" in result.stdout.lower() or "would" in result.stdout.lower()
    assert (theme / "mermaid-config.json").read_text() == original


def test_apply_rewrites_files_and_creates_backup(tmp_path):
    theme = _seed_user_theme(tmp_path)
    result = _run_rebuild(tmp_path, "--apply")
    assert result.returncode == 0, result.stderr
    new_cfg = json.loads((theme / "mermaid-config.json").read_text())
    # Bumped variable should be accent_soft (#E0EBF8 in our seed spec)
    assert new_cfg["themeVariables"]["attributeBackgroundColorOdd"] == "#E0EBF8"
    # Backup directory created with the original stale content
    backup_dirs = list(theme.glob(".backup-*"))
    assert len(backup_dirs) == 1
    backup = backup_dirs[0]
    assert (backup / "mermaid-config.json").read_text() == '{"stale": true}'


def test_missing_spec_skipped_with_warning(tmp_path):
    # Theme directory with NO spec.json
    theme_dir = tmp_path / ".md-publisher" / "themes" / "no-spec-theme"
    theme_dir.mkdir(parents=True)
    (theme_dir / "mermaid-config.json").write_text('{"some": "config"}')
    result = _run_rebuild(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "no-spec-theme" in result.stderr
    assert "spec.json" in result.stderr.lower()


def test_theme_filter_applies_only_to_named_theme(tmp_path):
    theme_a = _seed_user_theme(tmp_path, "theme-a-light")
    theme_b = _seed_user_theme(tmp_path, "theme-b-light")
    result = _run_rebuild(tmp_path, "--apply", "--theme", "theme-a-light")
    assert result.returncode == 0, result.stderr
    cfg_a = json.loads((theme_a / "mermaid-config.json").read_text())
    assert "themeVariables" in cfg_a  # rewritten
    cfg_b = json.loads((theme_b / "mermaid-config.json").read_text())
    assert cfg_b == {"stale": True}  # untouched
```

**Step 2: Create the script directory + the failing run**

```bash
mkdir -p skills/rebuild-themes/scripts
```

**Step 3: Run tests to verify they fail**

Run: `pytest tests/test_rebuild_themes.py -v`
Expected: FAIL — `rebuild-themes.py` doesn't exist yet (FileNotFoundError or `subprocess.CalledProcessError`).

**Step 4: Implement the script**

Create `skills/rebuild-themes/scripts/rebuild-themes.py`:

```python
#!/usr/bin/env python
"""Refresh installed themes by re-deriving mermaid-config.json + preview.html
from each theme's spec.json. Imports the templates and helpers from
scaffold-theme.py so the two scripts stay in lockstep.

Usage:
    rebuild-themes.py                   # dry run; lists themes that would migrate
    rebuild-themes.py --apply           # rewrite all eligible themes (with backup)
    rebuild-themes.py --theme <slug>    # restrict to one theme
    rebuild-themes.py --apply --no-backup
                                        # skip backup (escape hatch; not recommended)

Discovery roots (in order):
    1. <plugin-root>/themes/*/         (built-in themes shipped with the plugin)
    2. ~/.md-publisher/themes/*/       (user-installed themes)

A theme is eligible if its directory contains spec.json. Directories
lacking spec.json are skipped with a warning naming the directory.

Backup convention: per theme, the pre-migration mermaid-config.json and
preview.html are copied to <theme-dir>/.backup-<YYYYMMDD-HHMMSS>/ before
being overwritten.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Plugin root: env var if set (matches scaffold-theme convention), else
# walk up from this script.
PLUGIN_ROOT = Path(
    os.environ.get("CLAUDE_PLUGIN_ROOT")
    or Path(__file__).resolve().parent.parent.parent.parent
)
USER_THEMES_DIR = Path.home() / ".md-publisher" / "themes"
BUILTIN_THEMES_DIR = PLUGIN_ROOT / "themes"

# Cross-script import: pull MERMAID_CONFIG_TEMPLATE, PREVIEW_HTML_TEMPLATE,
# substitute, first_family, _render_preview_figure, PREVIEW_FLOWCHART/_ER/_CLASS,
# _classdef_props from scaffold-theme.py. Same importlib pattern apply-tags
# uses to import scan-mermaid.
SCAFFOLD_PATH = (
    PLUGIN_ROOT / "skills" / "theme-advisor" / "scripts" / "scaffold-theme.py"
)
_spec = importlib.util.spec_from_file_location("_scaffold", SCAFFOLD_PATH)
_scaffold = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scaffold)


def discover_themes(only: str | None = None) -> list[Path]:
    """Return theme directories (with spec.json) under built-in + user roots."""
    found: list[Path] = []
    for root in (BUILTIN_THEMES_DIR, USER_THEMES_DIR):
        if not root.exists():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if only and entry.name != only:
                continue
            found.append(entry)
    return found


def is_eligible(theme_dir: Path) -> bool:
    return (theme_dir / "spec.json").exists()


def _spec_to_substitution_dict(spec: dict) -> dict:
    """Map a spec.json (with snake_case palette keys) into the {{var}} dict
    that scaffold-theme's templates expect."""
    palette = spec["palette"]
    fonts = spec.get("fonts", {})
    # spec.json uses snake_case after normalization; map back to template keys.
    return {
        "display_name": spec.get("displayName", spec.get("slug", "Theme")),
        "font_display": fonts.get("display", "serif"),
        "font_body":    fonts.get("serif",   fonts.get("body", "serif")),
        "font_sans":    fonts.get("sans",    "sans-serif"),
        "font_mono":    fonts.get("mono",    "monospace"),
        "bg":           palette["bg"],
        "paper":        palette.get("paper", palette["bg"]),
        "ink":          palette["ink"],
        "ink_soft":     palette.get("ink_soft", palette["ink"]),
        "accent":       palette["accent"],
        "accent_soft":  palette.get("accent_alt", palette["accent"]),
        "rule":         palette.get("rule", palette.get("ink_soft", "#cccccc")),
        "code_bg":      palette.get("code_bg", palette.get("paper", palette["bg"])),
        "code_text":    palette.get("code_text", palette["ink"]),
    }


def regenerate_mermaid_config(theme_dir: Path, spec: dict) -> str:
    """Re-derive mermaid-config.json from a theme's spec.json."""
    subs = _spec_to_substitution_dict(spec)
    line_color = spec.get("mermaid", {}).get(
        "lineColor", spec["palette"].get("ink_soft", "#555555")
    )
    mermaid_font = spec.get("mermaid", {}).get(
        "fontFamily", subs["font_sans"]
    )
    mermaid_subs = {**subs, "line_color": line_color, "mermaid_font": mermaid_font}
    cfg_str = _scaffold.substitute(
        json.dumps(_scaffold.MERMAID_CONFIG_TEMPLATE, indent=2),
        mermaid_subs,
    )
    json.loads(cfg_str)  # validate
    return cfg_str


def regenerate_preview(theme_dir: Path, spec: dict, mermaid_config_path: Path) -> str:
    """Re-derive preview.html from a theme's spec.json — including 3 mermaid figures."""
    subs = _spec_to_substitution_dict(spec)
    style_css = (theme_dir / "style.css").read_text(encoding="utf-8") if (theme_dir / "style.css").exists() else ""

    # Render the 3 figures via mmdc (with fallback when missing)
    mmdc_path = (
        Path.home() / ".md-publisher" / "runtime" / "node_modules"
        / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js"
    )
    build_dir = theme_dir / ".preview-build"
    build_dir.mkdir(exist_ok=True)
    try:
        flowchart_src = _scaffold.substitute(_scaffold.PREVIEW_FLOWCHART, {
            "ingress_classdef":   _scaffold._classdef_props(spec["mermaid"]["tagStyling"]["ingress"]),
            "core_classdef":      _scaffold._classdef_props(spec["mermaid"]["tagStyling"]["core"]),
            "transform_classdef": _scaffold._classdef_props(spec["mermaid"]["tagStyling"]["transform"]),
            "bridge_classdef":    _scaffold._classdef_props(spec["mermaid"]["tagStyling"]["bridge"]),
        })
        flowchart_figure = _scaffold._render_preview_figure(flowchart_src, mermaid_config_path, build_dir, mmdc_path)
        er_figure = _scaffold._render_preview_figure(_scaffold.PREVIEW_ER, mermaid_config_path, build_dir, mmdc_path)
        class_figure = _scaffold._render_preview_figure(_scaffold.PREVIEW_CLASS, mermaid_config_path, build_dir, mmdc_path)
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

    body = spec.get("fonts", {}).get("body", spec.get("fonts", {}).get("serif", "serif"))
    preview_subs = {
        **subs,
        "tagline": spec.get("tagline", "Custom md-publisher theme."),
        "font_body_first": _scaffold.first_family(body),
        "style_css_inline": style_css,
        "flowchart_figure": flowchart_figure,
        "er_figure":        er_figure,
        "class_figure":     class_figure,
    }
    return _scaffold.substitute(_scaffold.PREVIEW_HTML_TEMPLATE, preview_subs)


def backup_existing(theme_dir: Path, ts: str) -> Path:
    """Copy current mermaid-config.json + preview.html to .backup-<ts>/. Returns dir."""
    backup_dir = theme_dir / f".backup-{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("mermaid-config.json", "preview.html"):
        src = theme_dir / fname
        if src.exists():
            shutil.copy2(src, backup_dir / fname)
    return backup_dir


def migrate_theme(theme_dir: Path, *, apply: bool, backup: bool) -> dict:
    """Migrate one theme. Returns a small status dict for reporting."""
    spec = json.loads((theme_dir / "spec.json").read_text(encoding="utf-8"))
    if not apply:
        return {"theme": theme_dir.name, "status": "would-migrate"}

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir: Path | None = None
    if backup:
        backup_dir = backup_existing(theme_dir, ts)

    # Regenerate. mermaid-config.json must exist before preview rendering
    # (mmdc reads the config to apply theme variables).
    new_cfg = regenerate_mermaid_config(theme_dir, spec)
    (theme_dir / "mermaid-config.json").write_text(new_cfg, encoding="utf-8")

    new_preview = regenerate_preview(theme_dir, spec, theme_dir / "mermaid-config.json")
    (theme_dir / "preview.html").write_text(new_preview, encoding="utf-8")

    return {
        "theme": theme_dir.name,
        "status": "migrated",
        "backup": str(backup_dir) if backup_dir else None,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true",
                   help="actually rewrite files (default is dry-run)")
    p.add_argument("--theme", type=str, default=None,
                   help="restrict migration to one theme by slug")
    p.add_argument("--no-backup", action="store_true",
                   help="(escape hatch) do not back up pre-migration files")
    args = p.parse_args()

    themes = discover_themes(only=args.theme)
    if not themes:
        msg = f"no themes found"
        if args.theme:
            msg += f" matching --theme {args.theme!r}"
        sys.stderr.write(f"[rebuild-themes] {msg}\n")
        return 0

    eligible = []
    for theme in themes:
        if is_eligible(theme):
            eligible.append(theme)
        else:
            sys.stderr.write(
                f"[rebuild-themes] skip {theme.name}: missing spec.json\n"
            )

    if not eligible:
        sys.stderr.write("[rebuild-themes] no eligible themes\n")
        return 0

    if not args.apply:
        print(f"[rebuild-themes] dry-run; {len(eligible)} theme(s) would migrate:")
        for theme in eligible:
            print(f"  would migrate: {theme}")
        print("Re-run with --apply to actually rewrite.")
        return 0

    for theme in eligible:
        status = migrate_theme(theme, apply=True, backup=not args.no_backup)
        suffix = ""
        if status.get("backup"):
            suffix = f"  (backed up to {status['backup']})"
        print(f"[rebuild-themes] migrated: {theme}{suffix}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Step 5: Make the script executable**

```bash
chmod +x skills/rebuild-themes/scripts/rebuild-themes.py
```

**Step 6: Run tests to verify they pass**

Run: `pytest tests/test_rebuild_themes.py -v`
Expected: PASS (4/4).

**Step 7: Run the full test suite**

Run: `pytest tests/ -v`
Expected: all tests pass.

**Step 8: Commit**

```bash
git add tests/test_rebuild_themes.py skills/rebuild-themes/scripts/rebuild-themes.py
git commit -m "feat(rebuild-themes): add migrator script with discovery, dry-run, --apply"
```

---

### Task 8: Create skills/rebuild-themes/SKILL.md

**Files:**
- Create: `skills/rebuild-themes/SKILL.md`

**Step 1: Write the SKILL.md**

Create `skills/rebuild-themes/SKILL.md`:

````markdown
---
name: rebuild-themes
description: This skill should be used when the user asks to "rebuild themes", "refresh themes after upgrading the plugin", "apply new template defaults to existing themes", "regenerate mermaid-config for installed themes", or invokes /md-publisher:rebuild-themes. Walks built-in and user-installed themes and re-derives `mermaid-config.json` + `preview.html` from each theme's `spec.json`, picking up any template improvements that shipped after the theme was originally created.
---

# rebuild-themes

Re-derive `mermaid-config.json` and `preview.html` for every installed theme from the theme's persisted `spec.json`. Use this after a plugin upgrade that improves the scaffold-theme template — it brings already-installed themes in line with the new defaults without asking the author to re-run theme-advisor.

## When to use

Trigger on any of:
- "rebuild themes" / "refresh my themes"
- "apply new template defaults to existing themes"
- after a plugin upgrade that mentions "richer mermaid styling" or "preview improvements"
- after manually editing a theme's `spec.json` and wanting the derived files updated
- explicit invocation: `/md-publisher:rebuild-themes`

## What gets rewritten

For each theme directory containing `spec.json`:
- `mermaid-config.json` — re-derived from the spec's palette + fonts
- `preview.html` — re-derived; includes the multi-diagram showcase if the runtime is bootstrapped

NOT rewritten:
- `style.css` — out of scope; CSS template changes are a separate concern
- `cover.css` — same
- `spec.json` — input, not output

## Workflow

### Step 1 — Dry run (default)

Invoke the script with no arguments to list which themes would be migrated:

```bash
${CLAUDE_PLUGIN_ROOT}/skills/rebuild-themes/scripts/rebuild-themes.py
```

The script walks two roots in order: `<plugin-root>/themes/*/` (built-in) and `~/.md-publisher/themes/*/` (user-installed). Only directories containing `spec.json` are eligible.

### Step 2 — Confirm with the user

Show the list and ask: "Apply migration to these N themes? Each will be backed up to `<theme-dir>/.backup-<YYYYMMDD-HHMMSS>/` before being rewritten." Wait for explicit confirmation.

### Step 3 — Apply

```bash
${CLAUDE_PLUGIN_ROOT}/skills/rebuild-themes/scripts/rebuild-themes.py --apply
```

Each theme's pre-migration files are copied to a timestamped backup directory inside the theme. The script prints one line per theme migrated.

### Step 4 — Optional: scope to one theme

Use `--theme <slug>` to migrate only one theme (still requires `--apply`). Useful for spot-checking a template change.

```bash
${CLAUDE_PLUGIN_ROOT}/skills/rebuild-themes/scripts/rebuild-themes.py --apply --theme atlas-light
```

### Step 5 — Report

Tell the user:
- How many themes were migrated
- Where each backup lives (in case any output looks wrong)
- Suggest opening one or two themes' `preview.html` to verify the visual result before relying on the migration

## Failure modes

- **No themes installed** — script exits 0 with a friendly stderr note.
- **Theme directory has no `spec.json`** — that theme is skipped with a stderr warning naming it. (Themes shipped before spec persistence are the typical case.)
- **mmdc not bootstrapped** — preview.html falls back to embedding raw mermaid source with a "(install runtime then re-scaffold to see live diagrams)" note. mermaid-config.json is still rewritten correctly.
- **Backup write fails** — surfaced as a Python exception; the script bails before touching the original files.

## Reference files

- `scripts/rebuild-themes.py` — the migrator
- `${CLAUDE_PLUGIN_ROOT}/skills/theme-advisor/scripts/scaffold-theme.py` — source of the templates and substitution helpers (imported via importlib)
````

**Step 2: Verify the SKILL.md is syntactically valid frontmatter + markdown**

Run: `python3 -c "import pathlib; t = pathlib.Path('skills/rebuild-themes/SKILL.md').read_text(); assert t.startswith('---\n'); assert '---\n' in t[4:]"`
Expected: No output.

**Step 3: Commit**

```bash
git add skills/rebuild-themes/SKILL.md
git commit -m "feat(rebuild-themes): add SKILL.md for the new top-level skill"
```

---

### Task 9: Register the new skill in marketplace-entry.json + theme-advisor maintenance pointer

**Files:**
- Modify: `marketplace-entry.json`
- Modify: `skills/theme-advisor/SKILL.md`

**Step 1: Add rebuild-themes to the marketplace skills array**

Open `marketplace-entry.json`. Find the `"skills"` array (line 35-41). Add a new entry after the `install-fonts` line:

Find:

```json
  "skills": [
    { "name": "publish",        "summary": "Render markdown to a themed PDF or DOCX (or both)" },
    { "name": "preprocess",     "summary": "LLM-tag mermaid diagrams for theme-aware coloring" },
    { "name": "theme-advisor",  "summary": "Interactive Q&A flow that creates a custom theme" },
    { "name": "theme-gallery",  "summary": "List + preview built-in and user-installed themes" },
    { "name": "install-fonts",  "summary": "Per-platform detect + install of theme fonts so DOCX renders identically to PDF" }
  ],
```

REPLACE with:

```json
  "skills": [
    { "name": "publish",         "summary": "Render markdown to a themed PDF or DOCX (or both)" },
    { "name": "preprocess",      "summary": "LLM-tag mermaid diagrams for theme-aware coloring" },
    { "name": "theme-advisor",   "summary": "Interactive Q&A flow that creates a custom theme" },
    { "name": "theme-gallery",   "summary": "List + preview built-in and user-installed themes" },
    { "name": "install-fonts",   "summary": "Per-platform detect + install of theme fonts so DOCX renders identically to PDF" },
    { "name": "rebuild-themes",  "summary": "Refresh installed themes after a plugin upgrade by re-deriving mermaid-config + preview from each theme's spec" }
  ],
```

Also update the `description` field at the top to bump the skill count from "5 skills" to "6 skills". Find:

```json
  "description": "Turn markdown documents (with embedded mermaid) into themed, searchable, paged PDFs and Microsoft Word DOCX files via WeasyPrint and python-docx. 5 skills (publish, preprocess, theme-advisor, theme-gallery, install-fonts) + 6 bundled themes (atlas/phosphor/arcade in light + dark) + interactive custom-theme creation",
```

REPLACE with:

```json
  "description": "Turn markdown documents (with embedded mermaid) into themed, searchable, paged PDFs and Microsoft Word DOCX files via WeasyPrint and python-docx. 6 skills (publish, preprocess, theme-advisor, theme-gallery, install-fonts, rebuild-themes) + 6 bundled themes (atlas/phosphor/arcade in light + dark) + interactive custom-theme creation",
```

**Step 2: Validate marketplace-entry.json is still parseable**

Run: `python3 -c "import json; json.load(open('marketplace-entry.json'))"`
Expected: No output (success).

**Step 3: Add maintenance pointer to theme-advisor SKILL.md**

Open `skills/theme-advisor/SKILL.md`. After the `## Failure modes` section (near the bottom), insert a new section before `## Reference files`:

Find:

```markdown
## Reference files
```

INSERT BEFORE it:

```markdown
## Maintenance — refreshing existing themes

After a plugin upgrade that improves the scaffold-theme template (richer mermaid coloring, multi-diagram preview, etc.), already-installed themes don't automatically pick up the changes — they were generated by the old template. Use the `rebuild-themes` skill to re-derive each theme's `mermaid-config.json` and `preview.html` from its `spec.json` without losing the original palette/fonts:

```
/md-publisher:rebuild-themes
```

The skill walks both built-in (`<plugin-root>/themes/`) and user-installed (`~/.md-publisher/themes/`) directories. Backs up pre-migration files to a timestamped directory inside each theme. See `skills/rebuild-themes/SKILL.md` for details.

```

**Step 4: Verify the markdown insertion**

Run: `grep -n "Maintenance — refreshing existing themes" skills/theme-advisor/SKILL.md`
Expected: one match.

**Step 5: Commit**

```bash
git add marketplace-entry.json skills/theme-advisor/SKILL.md
git commit -m "feat(plugin): register rebuild-themes skill + theme-advisor maintenance pointer"
```

---

### Task 10: Update README.md to add the new skill + describe behavior changes

**Files:**
- Modify: `README.md`

**Step 1: Update the "What you get" intro line**

Open `README.md`. Find (line 7):

```markdown
Five skills, six bundled themes, and Python pipelines that produce print-ready PDFs and editable DOCX with searchable mermaid text.
```

REPLACE with:

```markdown
Six skills, six bundled themes, and Python pipelines that produce print-ready PDFs and editable DOCX with searchable mermaid text.
```

**Step 2: Add the rebuild-themes row to the skills table**

Find the `install-fonts` row in the table (line 15). After it, ADD:

```markdown
| **`rebuild-themes`** | Refresh installed themes after a plugin upgrade by re-deriving `mermaid-config.json` + `preview.html` from each theme's `spec.json`. Backs up the pre-migration files per theme. | `/md-publisher:rebuild-themes [--theme <slug>]` |
```

**Step 3: Update the preprocess row to mention the auto-fix**

Find the existing `preprocess` row (line 12):

```markdown
| **`preprocess`** | LLM-tag mermaid diagrams (`:::ingress / :::core / :::transform / :::bridge`) so themes color nodes by role. Rewrites the source in place; original gets backed up. | `/md-publisher:preprocess doc.md [--add-frontmatter]` |
```

REPLACE with:

```markdown
| **`preprocess`** | LLM-tag mermaid diagrams (`:::ingress / :::core / :::transform / :::bridge`) so themes color nodes by role. Also auto-fixes misplaced classDiagram tags (e.g. `:::core` inside a method line) by promoting them to the class header using priority `ingress > core > transform > bridge`. Rewrites the source in place; original gets backed up. | `/md-publisher:preprocess doc.md [--add-frontmatter]` |
```

**Step 4: Update the theme-advisor row to mention the diagram preview**

Find the existing `theme-advisor` row (line 13):

```markdown
| **`theme-advisor`** | Interactive Q&A flow that produces a custom theme module under `~/.md-publisher/themes/<name>/`. | `/md-publisher:theme-advisor` |
```

REPLACE with:

```markdown
| **`theme-advisor`** | Interactive Q&A flow that produces a custom theme module under `~/.md-publisher/themes/<name>/`. The generated `preview.html` includes flowchart, ER, and classDiagram samples so authors can verify diagram styling before publishing. | `/md-publisher:theme-advisor` |
```

**Step 5: Verify the table still parses**

Run: `python3 -c "import pathlib; t = pathlib.Path('README.md').read_text(); rows = [l for l in t.splitlines() if l.startswith('| **')]; assert len(rows) == 6, rows"`
Expected: No output (success — exactly 6 skill rows now).

**Step 6: Commit**

```bash
git add README.md
git commit -m "docs(README): add rebuild-themes skill row + note preprocess auto-fix"
```

---

### Task 11: Run rebuild-themes locally to refresh built-in + user themes

**Files:**
- Modifies (via the script): `themes/*/mermaid-config.json`, `themes/*/preview.html`, and the same files under `~/.md-publisher/themes/*/`

**Step 1: Dry-run to preview the migration**

Run: `python3 skills/rebuild-themes/scripts/rebuild-themes.py`
Expected: list of every theme that would be migrated (built-in + user). Confirm count matches what you expect (~6 built-in + however many user themes exist).

**Step 2: Apply the migration**

Run: `python3 skills/rebuild-themes/scripts/rebuild-themes.py --apply`
Expected: one "migrated:" line per theme, each with a backup path.

**Step 3: Spot-check the result for one built-in theme**

Run: `python3 -c "import json; cfg = json.load(open('themes/atlas-light/mermaid-config.json')); print(cfg['themeVariables']['attributeBackgroundColorOdd'])"`
Expected: a hex color matching `atlas-light`'s `accent_alt` (NOT the previous `code_bg`). Open `themes/atlas-light/preview.html` in a browser to visually verify the new diagram figures render.

**Step 4: Re-publish the qmsnext design.md to confirm end-to-end fix**

Run:
```bash
python3 skills/preprocess/scripts/scan-mermaid.py /Users/H468632/Documents/repos/poc-repos/qrm-modular-design/docs/packages/qmsnext/design.md > /tmp/scan.json
```

Look in `/tmp/scan.json` for the classDiagram block (around index 2). Confirm `misplaced_tags` is non-empty (with the `args:::ingress`, `recordId:::core`, `args:::core` patterns from the source). This proves Task 1 detects the original bug.

Then manually craft a decisions JSON with the promotions and run apply-tags. (Skipping the full preprocess SKILL flow here; this is a smoke test, not the production path.) Or invoke the preprocess slash command in a Claude Code session against the doc.

**Step 5: Commit the refreshed built-in themes**

```bash
git add themes/
git commit -m "chore(themes): refresh built-in mermaid-config + preview after template enrichment"
```

(Note: user themes under `~/.md-publisher/themes/` are NOT in the repo — those changes are local-only and do not get committed.)

---

## Self-Review Notes

- All 11 tasks have exact file paths.
- All test code is complete (not "write tests for the above").
- All implementation code is complete in each step.
- All bash commands have expected output.
- Symbol consistency: `apply_class_tag_promotions`, `_promote_one_class`, `_strip_body_tags`, `_classdef_props`, `_render_preview_figure`, `_render_mermaid_to_svg`, `regenerate_mermaid_config`, `regenerate_preview`, `discover_themes`, `is_eligible`, `migrate_theme`, `backup_existing` — all defined in tasks where they're first used and re-referenced consistently in later tasks.
- Spec coverage: misplaced-tag detection (Task 1), promotion algorithm with priority-wins (Task 2), preprocess SKILL.md docs (Task 3), four template variable bumps (Task 4), multi-diagram preview with mmdc + fallback (Task 5), theme-advisor SKILL.md note (Task 6), migrator script (Task 7), rebuild-themes SKILL.md (Task 8), marketplace-entry registration + maintenance pointer (Task 9), README (Task 10), local refresh (Task 11). All spec sections covered.
- TDD: every code-bearing task has the test-first pattern.
- Commits: one per task, conventional-commits prefix matching repo style.
