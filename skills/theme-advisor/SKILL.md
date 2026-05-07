---
name: theme-advisor
description: This skill should be used when the user asks to "create a custom theme", "design a theme", "make a new theme", "add a brand theme to md-publisher", or invokes /md-publisher:theme-advisor. Walks the user through ~6 focused questions about audience, aesthetic, palette, fonts, and mode, then materializes a working theme directory at ~/.md-publisher/themes/<slug>/ that the publish skill immediately recognizes.
---

# theme-advisor

Interactive theme creation. Ask the user a small number of focused questions, derive a complete theme spec from the answers, materialize the theme files via the scaffold script, and offer a preview before declaring done.

## When to use

Trigger on any of:
- "create a custom theme" / "make a new theme"
- "design a theme for [audience/brand]"
- "I want a theme that looks like [X]"
- explicit invocation: `/md-publisher:theme-advisor`

## Workflow

### Step 1 — Q&A (six focused questions)

Ask **one question per turn**, prefer multiple-choice format when possible. Wait for answers before moving on.

1. **Audience and use case** — "Who's reading this and in what context?" Examples: "internal engineering reference", "customer-facing report", "kid's birthday invite", "game manual style for hobby project". The answer shapes everything.

2. **One aesthetic adjective** — pick one or two from a varied palette: editorial, minimal, brutalist, luxury, retro, terminal, futuristic, organic, playful, industrial, art-deco. If the user proposes their own, use that.

3. **Palette source** — three sub-options:
   - "Tell me 1-3 hex codes" (user gives a brand color or two; derive complementary neutrals)
   - "Describe the vibe" (user says "warm earth tones", "cool clinical", "neon"; pick representative hexes)
   - "Match an existing theme" (start from atlas/phosphor/arcade/default and tweak)

4. **Font feel** — serif body / sans body / mono body / mixed. Pick distinctive Google Fonts or system fonts:
   - Serif: Newsreader, Source Serif 4, Spectral, Charter, Cormorant
   - Sans: Sora, DM Sans, Mona Sans, Inter Display, Manrope, Söhne (paid)
   - Mono: IBM Plex Mono, JetBrains Mono, Berkeley Mono (paid), Iosevka
   - Display: Audiowide, Bungee, Tomorrow, Russo One (only for very specific aesthetics)

5. **Mode** — light only, dark only, or both? If "both", schedule TWO scaffold runs with paired palettes.

6. **One distinguishing detail** — the *one* thing that makes this theme memorable. Examples: "italic page title", "shell-prompt prefix on h2", "neon-glow display", "gold accent", "all-caps headings", "hand-drawn dividers". This goes in the spec's tagline and informs accent choices.

### Step 2 — Build the spec

From the answers, build a JSON spec that conforms to the scaffold script's schema (see `scripts/scaffold-theme.py` docstring).

**Hard-validated by the script** (build will fail without these):
- `slug` (kebab-case, mode-suffixed if mode-aware: e.g. `solarized-light`). The slug IS the resolution key — the publish skill resolves `--theme NAME --mode MODE` by computing slug `NAME-MODE` and looking for that directory under `~/.md-publisher/themes/`.
- `fonts` (display, body, sans, mono — full font stacks with system fallbacks)
- `palette` (bg, paper, ink, inkSoft, accent, accentSoft, rule, codeBg, codeText)
- `mermaid.tagStyling.{ingress,core,transform,bridge}` — the four classDef styles

**Soft-required** (script defaults if missing, but should be set for a polished theme):
- `name` (the user-visible base name; persisted in spec.json for reference)
- `mode` (`"light"` or `"dark"`, or omit for mode-less)
- `displayName` (Title Case; appears in theme-gallery cards)
- `tagline` (one-sentence aesthetic summary; preview.html caption)
- `mermaid.fontFamily` (defaults to `fonts.sans`)
- `mermaid.lineColor` (defaults to `palette.accent` — yields theme-colored relationship lines and arrowheads in ER and class diagrams; override only if accent is too saturated for the aesthetic)

For palette derivation, follow these defaults if the user didn't specify:
- `paper` defaults to `bg`; for cards/figures, `paper` can be a slightly elevated tone
- `inkSoft` is a 60-70% mix of ink toward bg
- `accentSoft` is a ~10-15% mix of accent toward bg (good for table-header backgrounds)
- `rule` is the same family as `inkSoft` but lighter
- `codeBg` is `bg` minus a tiny shift toward `ink` (for warm light themes); for dark themes a touch warmer than bg

For mermaid `tagStyling`, fall back to these patterns unless the aesthetic dictates otherwise:
- `ingress`: `fill: paper, stroke: accent, color: ink, strokeWidth: 2`
- `core`: `fill: paper, stroke: inkSoft, color: ink, strokeWidth: 1`
- `transform`: `fill: codeBg, stroke: inkSoft, color: ink, strokeWidth: 1`
- `bridge`: `fill: transparent, stroke: inkSoft, color: inkSoft, strokeWidth: 1, strokeDasharray: "4 3"`

### Step 3 — Materialize via scaffold

Write the spec to a temp file, then invoke:

```
${CLAUDE_PLUGIN_ROOT}/skills/theme-advisor/scripts/scaffold-theme.py --spec /tmp/<slug>.json
```

The scaffold script writes the theme to `~/.md-publisher/themes/<slug>/`:
- `style.css` — generated from a parameterized template
- `mermaid-config.json` — mmdc config
- `spec.json` — the input spec, persisted
- `preview.html` — single-file static preview

If the slug already exists, the scaffold script refuses (returns exit code 1) unless `--force` is passed. Per Phase 3 decision, **abort and ask the user for a different slug** rather than overwriting silently.

### Step 4 — Preview

Tell the user the preview is at `~/.md-publisher/themes/<slug>/preview.html`. The preview now includes three sample mermaid diagrams (flowchart with the four classDef colors, ER, and classDiagram) so the author can verify diagram styling at the same time as typography. Suggest opening it in a browser:
- Windows: `start <path>`
- macOS: `open <path>`
- Linux: `xdg-open <path>`

Ask: "Want to tweak anything before this becomes a 'real' theme?" Common tweaks:
- One color (most common — accent darker, ink less harsh)
- Font swap (one family didn't render as expected)
- An additional aesthetic detail not captured

If the user wants tweaks: update the spec JSON, re-run scaffold with `--force`, regenerate preview. Iterate until the user says "ship it".

### Step 5 — Both modes (if the user picked "both" in step 5)

Repeat steps 2-4 for the dark variant. Slug becomes `<base>-dark` (vs `<base>-light`). Palette inverts:
- `bg` from light → dark surface
- `ink` from dark → light bone/cream
- `accent` may need a brighter cousin (red `#B22234` light → `#E84855` dark, etc.)

The two specs share the same base in their slugs (e.g., `solarized-light` and `solarized-dark`). The publish skill resolves `--theme solarized --mode light` to slug `solarized-light` (and similarly for dark) — so the slug naming convention IS the resolution mechanism. The `name` field in spec.json is metadata for the gallery and theme-advisor; it does not affect resolution.

### Step 6 — Confirm and report

After all variants are scaffolded:
- List the new theme(s) with full path
- Suggest the next step: try them out via `/md-publisher:publish <doc.md> --theme <name> --mode <mode>`
- Mention that `/md-publisher:theme-gallery` will show them alongside the built-ins

## Spec template (concrete example)

A user who answered: "internal engineering reference, terminal aesthetic, hex `#33FF66` accent, mono body, dark mode, distinguishing detail = 'green phosphor on near-black'":

```json
{
  "slug": "green-phosphor-dark",
  "name": "green-phosphor",
  "mode": "dark",
  "displayName": "Green Phosphor",
  "tagline": "VT100 green CRT printed onto warm paper.",
  "fonts": {
    "display": "JetBrains Mono, Consolas, monospace",
    "body":    "JetBrains Mono, Consolas, monospace",
    "sans":    "JetBrains Mono, Consolas, monospace",
    "mono":    "JetBrains Mono, Consolas, monospace"
  },
  "palette": {
    "bg":         "#000000",
    "paper":      "#0A0A0F",
    "ink":        "#E0F0E0",
    "inkSoft":    "#5A8A5A",
    "accent":     "#33FF66",
    "accentSoft": "#0A2010",
    "rule":       "#1A2A1A",
    "codeBg":     "#0F1A0F",
    "codeText":   "#80FF80"
  },
  "mermaid": {
    "tagStyling": {
      "ingress":   {"fill": "transparent", "stroke": "#80FF80", "color": "#80FF80", "strokeWidth": 2},
      "core":      {"fill": "transparent", "stroke": "#33FF66", "color": "#33FF66", "strokeWidth": 2},
      "transform": {"fill": "transparent", "stroke": "#5A8A5A", "color": "#E0F0E0", "strokeWidth": 1},
      "bridge":    {"fill": "transparent", "stroke": "#5A8A5A", "color": "#5A8A5A", "strokeWidth": 1, "strokeDasharray": "4 3"}
    },
    "fontFamily": "JetBrains Mono, monospace",
    "lineColor": "#5A8A5A"
  }
}
```

## Failure modes

- **User can't decide on palette** — pick reasonable defaults from the aesthetic adjective and ship the preview. They can iterate.
- **Slug collision** — abort, ask for new name. Don't suggest `<slug>-2` automatically — the user might want a different name entirely.
- **Font not available locally** — themes use font-stack fallbacks; the first family may degrade gracefully. Document in the preview.html if a primary font is unlikely to be present.
- **Scaffold script fails** — surface the error verbatim. Almost always either a malformed spec (missing required field) or a write-permission problem on `~/.md-publisher/themes/`.

## Maintenance — refreshing existing themes

After a plugin upgrade that improves the scaffold-theme template (richer mermaid coloring, multi-diagram preview, etc.), already-installed themes don't automatically pick up the changes — they were generated by the old template. Use the `rebuild-themes` skill to re-derive each theme's `mermaid-config.json` and `preview.html` from its `spec.json` without losing the original palette/fonts:

```
/md-publisher:rebuild-themes
```

The skill walks both built-in (`<plugin-root>/themes/`) and user-installed (`~/.md-publisher/themes/`) directories. Backs up pre-migration files to a timestamped directory inside each theme. See `skills/rebuild-themes/SKILL.md` for details.

## Reference files

- `scripts/scaffold-theme.py` — generates the theme assets from a spec JSON. Read its docstring for the full spec schema.
- `${CLAUDE_PLUGIN_ROOT}/themes/theme-spec.json` — examples of well-formed theme spec blocks (atlas, phosphor, arcade)
- `${CLAUDE_PLUGIN_ROOT}/themes/<theme-mode>/style.css` — examples of finished theme stylesheets
