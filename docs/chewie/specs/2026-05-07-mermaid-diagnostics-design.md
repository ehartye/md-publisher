# Mermaid Diagnostics — Design

**Status:** Draft pending user review
**Date:** 2026-05-07
**Scope:** Plugin enhancement across `preprocess` and `theme-advisor` skills

## Problem

Two classes of bug currently slip through to the rendered PDF without warning:

1. **Misplaced classDiagram tags.** A user writing `+method(args):::core` inside a class body looks plausible but mermaid silently ignores the marker — `:::class` syntax must attach to the class header, not a method line. The class renders unstyled and the user only finds out by eyeballing the PDF.
2. **Subtle non-flowchart diagram styling.** ER diagrams (and to a lesser extent class/state) can't use `:::tag` classDef wiring at all — they're themed only via `mermaid-config.json` `themeVariables`. The current scaffold template maps the relevant variables (e.g. `attributeBackgroundColorOdd`) to neutral palette tokens (`code_bg`), producing a near-white grey that reads as "not themed" against a white page. Theme authors don't see this until they publish a real document with an ER diagram.

Compounding both: `preview.html` shows zero diagrams, so theme authors validate typography but not diagram styling.

## Goals

- Catch misplaced classDiagram tags at preprocess time and auto-fix them.
- Make newly-generated themes look distinctly branded across non-flowchart diagrams by default.
- Make diagram styling self-evident at theme-creation time via a multi-diagram preview.
- Refresh existing themes to pick up the template improvements.

## Non-Goals

- Automatic ERD/state-diagram tag injection (mermaid syntax doesn't support per-node classes for those types — they're themed at the diagram level).
- Visual regression testing of preview.html (no headless-browser snapshots in scope).
- A separate `theme-doctor` or `lint` skill (diagnostics live inside the existing workflow checkpoints — preprocess catches authoring bugs; theme-advisor catches theming gaps).

## Architecture

Three tracks, each landing in its existing skill home plus a small shared helper for the migrator.

```
Track 1 — Preprocess auto-fix
    skills/preprocess/scripts/scan-mermaid.py    (+ misplaced_tags field)
    skills/preprocess/scripts/apply-tags.py      (+ promotion pass)
    skills/preprocess/SKILL.md                   (+ Step 2.5)

Track 2 — Scaffold template enrichment
    skills/theme-advisor/scripts/scaffold-theme.py
        - Bump 4 mermaid template variables to accent-leaning tokens
        - Multi-diagram preview.html (3 figures: flowchart, ER, class)
    skills/theme-advisor/SKILL.md

Track 3 — Theme migrator (new skill: rebuild-themes)
    skills/rebuild-themes/SKILL.md                   (new)
    skills/rebuild-themes/scripts/rebuild-themes.py  (new)
    marketplace-entry.json                           (+ rebuild-themes in skills array)
    skills/theme-advisor/SKILL.md                    (+ maintenance pointer to rebuild-themes)

Cross-cutting
    tests/test_scan_mermaid_misplaced.py
    tests/test_apply_tags_promotion.py
    tests/test_scaffold_theme_template.py
    tests/test_rebuild_themes.py
    README.md                                        (+ what's new)
```

## Track 1 — Preprocess auto-fix

### Detection (`scan-mermaid.py`)

Per `classDiagram` / `classDiagram-v2` block, the scanner emits a new field:

```json
{
  "misplaced_tags": [
    {"class_name": "WorkflowEngine", "tag": "core", "line_in_block": 7},
    {"class_name": null, "tag": "ingress", "line_in_block": 12}
  ]
}
```

Detection rule:

1. Find every `class Foo { ... }` block (the explicit-header form).
2. Inside each class body (between `{` and `}`), find any `:::tag` occurrences. These are necessarily misplaced — class headers are outside the body.
3. Find `:::tag` markers attached to identifiers that aren't class headers and aren't already on a class line. Map each to its enclosing `class Foo` if one exists; otherwise emit `class_name: null` (the orphan-class case).

Existing scanner output keys (`tagged`, `untagged_node_ids`, etc.) are unchanged. The `tagged` boolean continues to count any `:::tag` in the block — including misplaced ones — so callers reading the existing field shape don't get a behavior change. The new field is additive.

### Auto-fix (`apply-tags.py`)

A new top-level decision section in the decisions JSON:

```json
{
  "source": "/abs/path/to/doc.md",
  "tags": [...],
  "class_tag_promotions": [
    {"index": 2, "class_name": "WorkflowEngine", "winning_tag": "core"}
  ]
}
```

Algorithm:

1. Group misplaced tags by `(block_index, class_name)`.
2. For each group, pick the winning tag using **priority-wins precedence**: `ingress > core > transform > bridge`. If a class already has a header tag, keep it (don't overwrite); still strip any misplaced body tags below.
3. **If the class has an explicit `class Foo { ... }` header line:** rewrite header to `class Foo:::winning_tag {` (preserve the brace + body verbatim). Strip ALL `:::tag` markers from body lines for that class.
4. **If the class has NO explicit header line** (orphan): leave the misplaced tags untouched. Emit a warning to stderr naming the class. Apply-tags does NOT fabricate `class Foo` declarations.

### Workflow (preprocess SKILL.md)

A new "Step 2.5 — Resolve misplaced tags" between current Step 2 and Step 3:

> For each block where `misplaced_tags` is non-empty: group by class, apply the priority-wins rule (`ingress > core > transform > bridge`), and emit one `class_tag_promotions` entry per class with an explicit header. Orphan classes (no explicit `class Foo` header) get reported to the user as "you'll need to add a `class Foo` declaration to enable styling" — apply-tags will skip them with a warning.

### Idempotence

Re-running scan on a class whose tag has already been promoted yields zero `misplaced_tags` — the body is now clean and the header tag is correctly placed. No promotion decision is generated. Backup is not taken when nothing changes (existing behavior of apply-tags' "computed text matches original" check covers this).

## Track 2 — Scaffold template enrichment

### Mermaid variable bumps (`scaffold-theme.py`)

Surgical changes to `MERMAID_CONFIG_TEMPLATE`:

| Variable | Current | New |
|---|---|---|
| `attributeBackgroundColorOdd` | `{{code_bg}}` | `{{accent_soft}}` |
| `clusterBkg` | `{{code_bg}}` | `{{accent_soft}}` |
| `altBackground` | `{{code_bg}}` | `{{accent_soft}}` |
| `sectionBkgColor` | `{{code_bg}}` | `{{accent_soft}}` |

These are the variables that drive **fill area** of visible diagram elements (ER alternating row, flowchart subgraph background, state diagram alt regions, gantt section bars). Border/text variables already use accent or ink and stay unchanged.

### Multi-diagram preview.html

`PREVIEW_HTML_TEMPLATE` gains three `<figure class="mermaid">` blocks below the existing typography sample:

1. **Flowchart** — 5-node graph: 1 `:::ingress`, 1 `:::core`, 1 `:::transform`, 1 `:::bridge`, 1 untagged. Caption: "Flowchart with classDef tags".
2. **erDiagram** — 2-entity ER with attributes. Caption: "ER diagram (themed via mermaid-config)".
3. **classDiagram** — 3-class diagram with one class tagged `:::ingress` on its header. Caption: "Class diagram with `:::ingress` header tag".

### Preview rendering

Scaffold script invokes mmdc inline (same path as the publish pipeline: `~/.md-publisher/runtime/node_modules/@mermaid-js/mermaid-cli/src/cli.js`) at scaffold time, captures each SVG, and inlines it into preview.html. No new runtime dependency.

### mmdc-missing fallback

If the runtime hasn't been bootstrapped yet (theme created before first publish), scaffold falls back to embedding the raw mermaid source inside `<pre class="mermaid-source">` blocks with a small "(install runtime then re-scaffold to see live diagrams)" note. The theme files themselves are still written successfully — preview is non-blocking.

## Track 3 — Theme migrator (new `rebuild-themes` skill)

### Skill: `skills/rebuild-themes/SKILL.md`

A new top-level skill so the slash command `/md-publisher:rebuild-themes` is discoverable on its own (this plugin's slash commands derive from skill names — there is no separate command registration in marketplace-entry.json or .claude-plugin/plugin.json). The SKILL.md is short: it describes when to invoke (after a plugin upgrade, after editing a theme's `spec.json` by hand) and delegates to the script.

### Script: `skills/rebuild-themes/scripts/rebuild-themes.py`

Walks installed themes and regenerates `mermaid-config.json` and `preview.html` from each theme's `spec.json`. Imports template constants (`MERMAID_CONFIG_TEMPLATE`, `PREVIEW_HTML_TEMPLATE`) and substitution helpers (`substitute`, `first_family`) from `scaffold-theme.py` via `importlib.util.spec_from_file_location` — the same cross-script import pattern `apply-tags.py` already uses to import from `scan-mermaid.py`. No refactor of scaffold-theme.py needed; the constants are already module-level.

### Discovery

Two roots walked, in order:

1. **Built-in:** `<plugin-root>/themes/*/`
2. **User-installed:** `~/.md-publisher/themes/*/`

A theme is migration-eligible if its directory contains `spec.json`. Directories without `spec.json` are skipped with a warning naming the directory.

### Files rewritten per theme

- `mermaid-config.json` — picks up the new accent-leaning template variables
- `preview.html` — picks up the multi-diagram showcase

NOT rewritten:

- `style.css` (out of scope; no CSS template changes)
- `cover.css` (out of scope)
- `spec.json` (input, not output)

### Backup

Each theme's pre-migration `mermaid-config.json` and `preview.html` are copied to `<theme-dir>/.backup-<YYYYMMDD-HHMMSS>/` before being overwritten. Allows recovery if regenerated output looks wrong.

### Invocation

| Args | Behavior |
|---|---|
| `rebuild-themes.py` | dry-run; lists which themes would be migrated, no writes |
| `rebuild-themes.py --apply` | actually rewrite all eligible themes |
| `rebuild-themes.py --theme <slug>` | migrate only that theme (still requires `--apply`) |
| `rebuild-themes.py --apply --no-backup` | escape hatch (do not document or recommend) |

### Slash command

`/md-publisher:rebuild-themes` invokes the `rebuild-themes` skill, which calls `rebuild-themes.py --apply` (with prior dry-run output shown to the user for confirmation when invoked interactively). `theme-advisor/SKILL.md` references this skill in its maintenance section as the supported way to refresh themes after a plugin upgrade.

### Idempotence

Running twice produces identical output (same spec → same template → same files). Backup directories accumulate per timestamp.

## Testing

Pytest unit tests under `tests/`, matching the conventions of the existing `test_output_paths.py` and `test_theme_loader.py`. Each test creates a temp directory with a minimal valid input, runs the script via `subprocess.run`, and asserts on stdout/stderr/produced files.

| File | Coverage |
|---|---|
| `tests/test_scan_mermaid_misplaced.py` | correctly-placed tags → empty `misplaced_tags`; misplaced tags → detected; orphan-class case → `class_name: null` |
| `tests/test_apply_tags_promotion.py` | priority-wins precedence; orphan-class skip-and-warn; header rewrite preserves body; idempotent re-run |
| `tests/test_scaffold_theme_template.py` | generated mermaid-config has the bumped accent-leaning variables; preview.html contains 3 mermaid figures; mmdc-missing fallback emits source blocks |
| `tests/test_rebuild_themes.py` | dry-run lists without writing; `--apply` rewrites and creates backup; missing-spec themes skipped with warning; `--theme <slug>` filters |

Out of scope: mmdc rendering correctness (third-party); preview.html visual regression; end-to-end PDF rebuild of qmsnext design.md (manual smoke test only).

## Documentation updates

- `skills/preprocess/SKILL.md`: new Step 2.5; new row in "What gets tagged"; new "Failure modes" entry; updated example in Step 5.
- `skills/theme-advisor/SKILL.md`: Step 4 mentions multi-diagram preview; new "Maintenance — refreshing existing themes" section pointing to the rebuild-themes skill.
- `skills/rebuild-themes/SKILL.md`: NEW. Short skill doc explaining when to invoke (after plugin upgrades, after manual `spec.json` edits) and what it rewrites.
- `README.md`: 3 bullets in "What's new" — preprocess auto-fix; richer theme generation; `/md-publisher:rebuild-themes` for upgrades.
- `marketplace-entry.json`: add `rebuild-themes` to the `skills` array (this is how the marketplace surfaces the skill; slash commands derive from skill names automatically — there is no separate command-registration field).

## Migration / rollout

1. Land Track 1 (preprocess) — backwards-compatible (new field is additive; new decision section is opt-in).
2. Land Track 2 (scaffold template) — new themes generated after this point use the new template. Existing themes are unchanged until migrated.
3. Land Track 3 (migrator) — ship the script + slash command. README mentions running it after upgrade.
4. Run `/md-publisher:rebuild-themes` once locally to refresh both built-in (committed to repo) and user-installed (uncommitted) themes.

## Open follow-ups (out of scope for this design)

- Backfilling style.css/cover.css from spec — separate design when CSS template changes warrant it.
- Visual regression test for preview.html.
- Generalizing the misplaced-tag detection to stateDiagram (does mermaid's state syntax have an analogous trap?).
