---
name: theme-gallery
description: This skill should be used when the user asks to "show me available themes", "list themes", "what themes do I have installed", "browse themes", "show theme gallery", "preview themes side by side", or invokes /md-publisher:theme-gallery. Lists all built-in and user-installed themes in one combined view; can produce a single HTML gallery page with live previews of each theme card.
---

# theme-gallery

List every theme available to md-publisher (built-in + user-installed) and optionally render a single HTML gallery page that shows them side by side.

## When to use

Trigger on any of:
- "show me the themes" / "list available themes"
- "what themes do I have"
- "browse themes" / "theme gallery"
- "preview all themes" / "show themes side by side"
- explicit invocation: `/md-publisher:theme-gallery`
- right after `/md-publisher:theme-advisor` finishes (offer to show it in the gallery)

## Workflow

### Default flow (concise list)

For a quick text answer, invoke:

```
${CLAUDE_PLUGIN_ROOT}/skills/theme-gallery/scripts/list-themes.py
```

This emits a JSON document. Render a tight summary table back to the user:

| Slug | Source | Display name | Mode | Tagline |
|---|---|---|---|---|
| atlas-light | builtin | ATLAS | light | Editorial-trust serif on warm bone, red as punctuation only. |
| ... | ... | ... | ... | ... |

Group by source: user themes first, then built-ins.

### Gallery flow (visual preview)

If the user asked specifically to "browse" / "preview" / "see them side by side", run the gallery builder:

```
${CLAUDE_PLUGIN_ROOT}/skills/theme-gallery/scripts/build-gallery.py --open
```

This:
1. Calls `list-themes.py` for the full set
2. Renders `~/.md-publisher/gallery.html` — one card per theme, each with an embedded live `preview.html` iframe
3. Opens the gallery in the OS default browser (if `--open` is passed)

Tell the user the gallery file path so they can re-open it later.

## Resolution semantics

User themes (`~/.md-publisher/themes/<slug>/`) override built-ins of the same slug. The gallery and list both reflect this — if a user theme has the same slug as a built-in, only the user version appears.

This is intentional: it lets a user clone a built-in theme to their themes dir, tweak it, and have their version take effect everywhere without touching plugin files.

## Output examples

### list-themes.py JSON (abbreviated):

```json
{
  "user_themes_dir": "C:\\Users\\you\\.md-publisher\\themes",
  "plugin_themes_dir": "C:\\Users\\you\\...\\md-publisher\\themes",
  "themes": [
    {
      "slug": "atlas-light",
      "name": "atlas",
      "mode": "light",
      "source": "builtin",
      "path": "C:\\...\\themes\\atlas-light",
      "has_preview": false,
      "spec_summary": {
        "displayName": "ATLAS",
        "tagline": "Editorial-trust serif on warm bone, red as punctuation only.",
        "audience": "corporate / customer-facing / non-technical"
      }
    },
    ...
  ]
}
```

### Render to user

For the basic list flow, format as a markdown table grouped by source. Keep the tagline column wrap-aware (truncate at ~60 chars with an ellipsis if needed).

For the gallery flow, just confirm the output path:

```
Wrote gallery to ~/.md-publisher/gallery.html (8 themes)
Opened in default browser.
```

## Failure modes

- **No themes found** — reports zero themes; still emits valid JSON / empty gallery. Offer `/md-publisher:theme-advisor` to create the first custom theme.
- **User themes dir missing** — that's fine; only built-ins appear. Don't auto-create the dir; it gets created on first theme-advisor run.
- **Theme dir present but missing style.css** — silently skipped (the dir doesn't qualify as a complete theme).

## Reference files

- `scripts/list-themes.py` — JSON list generator
- `scripts/build-gallery.py` — single-page HTML gallery builder
- `${CLAUDE_PLUGIN_ROOT}/lib/theme_loader.py` — `list_available_themes()` (the same logic, exposed as a Python API)
