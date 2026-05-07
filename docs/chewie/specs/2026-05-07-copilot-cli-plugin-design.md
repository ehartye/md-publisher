# Copilot CLI Plugin Support

**Date:** 2026-05-07  
**Status:** Approved  
**Goal:** Make md-publisher installable as a GitHub Copilot CLI plugin without disrupting the existing Claude Code plugin.

## Problem

md-publisher is currently installable only as a Claude Code plugin (via `.claude-plugin/plugin.json`). Users of GitHub Copilot CLI cannot discover or install it through the Copilot plugin system.

## Approach

Leverage Copilot CLI's fallback manifest discovery — it already checks `.claude-plugin/plugin.json` as the last location in its search order. The existing manifest and `skills/` directory structure already conform to Copilot's expected format. We only need to add:

1. A marketplace manifest so the repo is self-contained and discoverable
2. A copilot-instructions.md for plugin context
3. A README update documenting both install paths

No existing files are modified. No sync scripts, no duplicated skills, no separate plugin directory.

## Deliverables

### 1. `.github/plugin/marketplace.json`

Creates a self-contained marketplace so users can:
```bash
copilot plugin marketplace add ehartye/md-publisher
copilot plugin install md-publisher@md-publisher
# or directly:
copilot plugin install ehartye/md-publisher
```

Contents:
```json
{
  "name": "md-publisher",
  "owner": {
    "name": "Eric Hartye",
    "email": "eric@hartye.com"
  },
  "metadata": {
    "description": "Markdown to themed PDF/DOCX publishing pipeline with mermaid support"
  },
  "plugins": [
    {
      "name": "md-publisher",
      "source": "./",
      "description": "Turn markdown documents (with embedded mermaid) into themed, searchable, paged PDFs and DOCX files via WeasyPrint and python-docx"
    }
  ]
}
```

### 2. `.github/copilot-instructions.md`

Provides Copilot CLI with project context (~80 lines). Covers:

- **What md-publisher is** — WeasyPrint + python-docx pipeline for markdown → PDF/DOCX
- **Available skills** — publish, preprocess, theme-advisor, theme-gallery, install-fonts
- **Runtime architecture** — bootstrap creates `~/.md-publisher/runtime/` (Python venv + Node mermaid-cli); first run ~2 min, ~250 MB
- **Key paths** — `lib/` (core), `themes/` (bundled), `runtime/` (bootstrap), `skills/` (skill definitions)
- **Usage patterns** — common workflows, skill invocation
- **Error handling** — `--doctor` mode for native dep issues

### 3. README update

Add an "Installation" section documenting both install paths:
```markdown
## Installation

### Claude Code
claude plugin install ehartye/md-publisher

### GitHub Copilot CLI
copilot plugin install ehartye/md-publisher
```

## What stays untouched

| Path | Reason |
|------|--------|
| `.claude-plugin/plugin.json` | Serves both Claude and Copilot (fallback discovery) |
| `skills/` | Copilot defaults to `skills/` — already correct |
| `marketplace-entry.json` | Separate concern (Claude marketplace draft) |
| `runtime/`, `lib/`, `themes/`, `tests/` | No plugin-system changes needed |

## Discovery Mechanics

Copilot CLI checks plugin manifests in this order:
1. `.plugin/plugin.json`
2. `plugin.json` (root)
3. `.github/plugin/plugin.json`
4. `.claude-plugin/plugin.json` ← **found here**

Copilot CLI checks marketplace manifests in this order:
1. `marketplace.json` (root)
2. `.plugin/marketplace.json`
3. `.github/plugin/marketplace.json` ← **found here**
4. `.claude-plugin/marketplace.json`

Skills default to `skills/` when no explicit path is set in the manifest — which matches our layout exactly.

## Validation

After implementation:
- `copilot plugin install ./` from the repo root should succeed
- All 5 skills should appear in `copilot plugin list` output
- Existing `claude plugin install` path remains unaffected
- Existing tests (`pytest`) continue to pass
