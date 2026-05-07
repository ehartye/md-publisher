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
