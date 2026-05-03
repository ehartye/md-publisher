---
name: preprocess
description: This skill should be used when the user asks to "preprocess doc.md", "tag mermaid for theming", "add classDef tags to my mermaid", "annotate diagrams", "prepare doc for theming", or invokes /md-publisher:preprocess. Scans a markdown document for mermaid flowcharts, decides a universal-tag class (ingress/core/transform/bridge) for each untagged node, rewrites the source in place, and backs up the original. Optionally adds YAML front matter for richer cover-page metadata.
---

# preprocess

Add the four universal tag classes (`:::ingress`, `:::core`, `:::transform`, `:::bridge`) to mermaid flowchart nodes so themes can color them by semantic role. The annotated source becomes the new authoring baseline; the original is backed up to `<source-dir>/.md-publisher/<timestamp>/original.md` before the rewrite.

## When to use

Trigger on any of:
- "preprocess `doc.md`" / "tag the mermaid in `doc.md`"
- "annotate the diagrams" / "add classDef tags"
- "prepare `doc.md` for theming"
- explicit invocation: `/md-publisher:preprocess <markdown-file>`
- right before publishing if the doc has untagged mermaid and the user wants per-node coloring

## What gets tagged

The four universal classes correspond to a node's role in the diagram's data flow:

| Tag | Meaning |
|---|---|
| `:::ingress` | Entry / exit / source / sink — terminals in the data flow |
| `:::core` | Primary processing — the "main thing" the diagram is about |
| `:::transform` | Auxiliary processing — projections, conversions, preprocessing |
| `:::bridge` | Connector / aggregator / intermediate — joins, splits, glue |

These map per-theme to specific colors via each theme's `classDef` rules (e.g., ATLAS makes ingress nodes red-bordered, ARCADE makes them neon-green). No coloring is applied at preprocess time — only the class assignments. Themes do the visual work at publish time.

Mermaid blocks that are NOT flowchart/graph (sequenceDiagram, classDiagram, etc.) are left alone — `classDef` only applies to flowcharts. They still get themed at publish time, just at the diagram-level palette rather than per-node.

## Workflow

Two-script pipeline. The skill orchestrates between them and supplies the per-node tag decisions.

### Step 1 — Scan

Invoke `${CLAUDE_PLUGIN_ROOT}/skills/preprocess/scripts/scan-mermaid.py <doc.md>`. It emits JSON describing every mermaid block:

```json
{
  "source": "/abs/path/to/doc.md",
  "blocks": [
    {
      "index": 0,
      "line_start": 38,
      "line_end": 46,
      "diagram_type": "flowchart",
      "supports_classdef": true,
      "tagged": false,
      "node_count": 12,
      "node_ids": ["A", "B", ...],
      "untagged_node_ids": ["A", "B", ...],
      "snippet": "flowchart TD\\n    A[Input tokens] --> ..."
    }
  ]
}
```

`line_start` and `line_end` are 1-based line numbers in the source — useful when you need to read prose around a diagram for context. `snippet` is the first 12 lines of the block (truncated for brevity in the scan output; the full source is at the line range above).

### Step 2 — Decide tags

For each block where `supports_classdef: true` and there are `untagged_node_ids`:

1. Read the `snippet` to understand what the diagram represents (the file body around `line_start` may also help — read context if the snippet is ambiguous).
2. For each untagged node, decide which of the four classes fits:
   - **`:::ingress`** for entry/exit nodes — typically the source of input or destination of output. Look for words like "input", "output", "request", "response", "start", "end", "source", "sink", "result", "next-token".
   - **`:::core`** for the diagram's main subject — the steps that *are* the thing being explained. Encoder/decoder blocks, attention heads, processing stages, business logic.
   - **`:::transform`** for auxiliary steps — projections, conversions, preprocessing, formatting. Things that prepare input for or convert output from the core.
   - **`:::bridge`** for connectors and intermediates — concatenations, joins, splits, things that route or combine but aren't the primary work.

3. When in doubt, prefer `:::core` — it is the "neutral default" and themes color it as the most prominent role.

4. If a node's role genuinely doesn't fit any of the four, leave it untagged. Themes color untagged nodes with the diagram-level palette, which is a fine fallback.

### Step 3 — Optional front matter

If the user passes `--add-frontmatter` (or asks for richer cover-page metadata), prompt for:
- title (suggest first h1 of the doc)
- subtitle (optional)
- author (suggest from git config user.name if available)
- date (suggest today's date)

Skip this step by default. YAML front matter changes how OTHER markdown tools (not in this plugin) parse the file — it is opt-in for that reason.

### Step 4 — Apply

Write the decisions to a temp JSON file, then invoke:
```
${CLAUDE_PLUGIN_ROOT}/skills/preprocess/scripts/apply-tags.py --decisions <temp.json>
```

The apply script:
1. Computes the rewritten text first; if no actual change would land, exits without writing a backup or touching the source.
2. Otherwise, backs up the original file to `<source-dir>/.md-publisher/<YYYYMMDD-HHMMSS>/original.md`.
3. Rewrites the source in place — appends `:::<tag>` to the FIRST occurrence of each tagged node ID's definition.
4. Optionally prepends YAML front matter.
5. Reports the number of tags applied.

**Do NOT pass `--no-backup`.** The flag exists in apply-tags.py as an escape hatch but using it removes the only safety net against an accidental destructive rewrite. The default backup behavior is the load-bearing safety property of this skill.

### Step 5 — Report

Tell the user:
- How many blocks were scanned
- How many tags were applied (and per-block breakdown)
- Where the backup landed
- Suggest the next step: `/md-publisher:publish <doc.md>` for theme-aware coloring

## Decisions JSON schema

```json
{
  "source": "/abs/path/to/doc.md",
  "tags": [
    {"index": 0, "node_id": "A", "tag": "ingress"},
    {"index": 0, "node_id": "B", "tag": "core"},
    {"index": 1, "node_id": "X", "tag": "ingress"}
  ],
  "frontmatter": {
    "title": "Document Title",
    "subtitle": "...",
    "author": "...",
    "date": "2026-05-03"
  }
}
```

`frontmatter` is optional. Tags with `index` referencing a non-existent block are silently dropped.

## Idempotence

Re-running preprocess on an already-tagged document is safe:
- scan-mermaid reports no `untagged_node_ids` for already-fully-tagged blocks
- apply-tags skips append for already-tagged nodes (it walks existing `:::class` patterns and treats them as already-done)

A document may be preprocessed multiple times — each run produces a new timestamped backup but the source converges.

## Examples

```bash
# Step 1: scan
${CLAUDE_PLUGIN_ROOT}/skills/preprocess/scripts/scan-mermaid.py docs/intro.md > /tmp/scan.json

# (skill reads /tmp/scan.json, decides tags, writes /tmp/decisions.json)

# Step 2: apply
${CLAUDE_PLUGIN_ROOT}/skills/preprocess/scripts/apply-tags.py --decisions /tmp/decisions.json
```

## Failure modes

- **Source not found** — surface a clear path error.
- **No mermaid blocks in the source** — report `0 blocks scanned, nothing to tag` and exit cleanly.
- **All blocks fully tagged already** — report and exit cleanly. No backup is taken (no rewrite happened).
- **User declines to choose tags for some nodes** — leave those untagged. Themes will color via the diagram-level palette.

## Reference files

- `scripts/scan-mermaid.py` — the scan helper
- `scripts/apply-tags.py` — the rewriter + backup
- `${CLAUDE_PLUGIN_ROOT}/lib/output_paths.py` — backup-path conventions
