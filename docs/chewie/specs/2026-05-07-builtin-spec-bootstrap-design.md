# Built-in Theme spec.json Bootstrap — Design

**Status:** Draft pending user review.
**Date:** 2026-05-07.
**Scope:** Hand-write 7 per-theme `spec.json` files for the bundled themes so `/md-publisher:rebuild-themes --apply` can refresh their `mermaid-config.json` + `preview.html` to pick up the new accent-leaning template defaults.

## Problem

Per `docs/chewie/specs/2026-05-07-builtin-theme-spec-bootstrap-followup.md`, none of the built-in theme directories (`atlas-light`, `atlas-dark`, `phosphor-light`, `phosphor-dark`, `arcade-light`, `arcade-dark`, `default`) have a per-theme `spec.json`. Without it, `rebuild-themes.py` correctly skips them, so they never pick up the template-variable bumps and multi-diagram preview shipped in the mermaid-diagnostics work.

## Goal

Add a `spec.json` to each of the 7 built-in theme directories, then run the migrator once to refresh `mermaid-config.json` + `preview.html` for each.

## Non-goals

- Schema changes to `spec.json` (no new font roles, no rich typography metadata)
- Code changes to `rebuild-themes.py` or `scaffold-theme.py`
- A `bootstrap-builtin-specs.py` script (manual conversion is right-sized for 7 files)
- Style.css regeneration (out of scope; rebuild-themes intentionally doesn't touch style.css)
- A dark variant of the `default` theme

## Locked design decisions

(All five locked through brainstorming on 2026-05-07. See follow-up doc for the original questions.)

| Decision | Resolution |
|---|---|
| **Font shape collapse** | Drop the master's rich typography metadata (`weight`, `opsz`, `italic`, `weights[]`). Per-theme spec.json carries bare family names only — matches scaffold-theme's `first_family()` persistence convention and what user themes already use. |
| **Sans-key naming** | Collapse to `sans` (matches existing user-theme convention). Future schema expansion can add roles cleanly. |
| **Source of truth for the palette** | Lift from master `themes.<name>.modes.<mode>.*`. The master has all 10 canonical keys (bg, paper, ink, inkSoft, accent, accentSoft, rule, codeBg, codeText, tableStripe) for atlas + arcade + default. style.css uses inconsistent prefixes across themes (default uses `--color-*`, others use `--bg/--ink/...`) so it's not a uniform source. Non-canonical extras present in some themes (phosphor's `accentCool`, `inkEmphasis`, `inkBright`; arcade's `accent3`) do NOT round-trip through spec.json — they stay in style.css where WeasyPrint reads them. |
| **Source of truth for built-in fonts** | Per-theme hand-mapping using the table in the next subsection. Master role names diverge across themes (atlas: `sansAccent`; phosphor: `code`+`italic`; arcade: `display2`; default: `heading` instead of `display`), so neither master nor style.css gives a uniform extraction rule. Hand-mapping is right-sized for 4 theme families (atlas, phosphor, arcade, default). |
| **Default theme** | Emit `themes/default/spec.json` with NO `mode` field (acknowledging default's mode-less identity; aligns with theme-gallery's mode-less handling per commit `30cf747`). Do not scaffold a `default-dark/`. |
| **`mermaid.fontFamily` and `mermaid.lineColor`** | Lift verbatim from each theme's existing `themes/<theme>/mermaid-config.json["themeVariables"]["fontFamily"]` and `["lineColor"]`. Preserves deployed-render behavior. |

Mechanical mappings (no decision required):

| Source | Destination |
|---|---|
| master `themes.<name>.modes.<mode>.{bg, paper, ink, rule}` | spec `palette.{bg, paper, ink, rule}` (verbatim) |
| master `themes.<name>.modes.<mode>.{inkSoft, accentSoft, codeBg, codeText, tableStripe}` | spec `palette.{ink_soft, accent_alt, code_bg, code_text, table_stripe}` (snake_case rename) |
| master `themes.<name>.modes.<mode>.accent` (atlas, phosphor, default) | spec `palette.accent` (verbatim). Arcade is special-cased — see subsection. |
| master `themes.<name>.{tagline, displayName}` | spec `{tagline, displayName}` (verbatim) |
| master `themes.<name>.mermaid.tagStyling.<mode>.{ingress, core, transform, bridge}` | spec `mermaid.tagStyling.{ingress, core, transform, bridge}` (slice the per-mode block; same dict shape) |

### Phosphor's missing tableStripe

Phosphor's master `modes.<mode>` block doesn't include a `tableStripe` field. For phosphor's spec.json, default `table_stripe` to the value of `paper` (matches the "no alternating row treatment" intent and aligns with scaffold-theme's fallback convention).

### Arcade's 3-accent palette

Arcade has `accent1` (neon green), `accent2` (magenta), `accent3` (cyan) in the master. Spec.json schema only carries `accent` + `accent_alt`. Per the existing convention enforced by `tests/test_theme_loader.py::test_arcade_accent_uses_accent2`:

| Spec.json key | Arcade source | Why |
|---|---|---|
| `accent` | master `accent2` (magenta) | The cover hard-shadow color — the most distinctive arcade signal |
| `accent_alt` | master `accent1` (neon green) | The secondary accent (gradient start) |

Arcade's `accent3` (cyan) does NOT round-trip through spec.json. It lives only in `style.css` as `--accent3` and continues to be consumed there by WeasyPrint. This is acceptable: spec.json is the consumer-bridge layer, not the typography/color-system master.

### Per-theme font role mapping (the wrinkle)

Each theme's `style.css` exposes a different set of `--font-*` custom properties. Mapping each into the canonical 4-role spec.json schema (`serif, sans, mono, display`):

| Theme | serif | sans | mono | display | Notes |
|---|---|---|---|---|---|
| atlas-{light,dark} | Newsreader | Sora | JetBrains Mono | Newsreader | Direct 4-role mapping; `serif` = `--font-body`, `display` = `--font-display`. |
| phosphor-{light,dark} | IBM Plex Mono | IBM Plex Mono | IBM Plex Mono | IBM Plex Mono | Mono-only theme; all 4 roles fold to one family. style.css only has `--font-mono`. |
| arcade-{light,dark} | Recursive | Recursive | Recursive | Audiowide | `body` and `mono` both use Recursive (different variable axes via style.css); `sans` = body family. `--font-display2` (Bungee) is a 5th role not expressible in spec.json — preserved in style.css. |
| default | Source Serif Pro | Inter | JetBrains Mono | Inter | style.css uses `--font-heading` instead of `--font-display`; both `sans` and `display` collapse to Inter. |

## Files written

Seven `spec.json` files at `themes/<theme>/spec.json`:

1. `themes/atlas-light/spec.json`
2. `themes/atlas-dark/spec.json`
3. `themes/phosphor-light/spec.json`
4. `themes/phosphor-dark/spec.json`
5. `themes/arcade-light/spec.json`
6. `themes/arcade-dark/spec.json`
7. `themes/default/spec.json` — no `mode` field

## Per-file content shape

Concrete example (atlas-light):

```json
{
  "slug": "atlas-light",
  "name": "atlas",
  "mode": "light",
  "displayName": "ATLAS",
  "tagline": "Editorial-trust serif on warm bone, red as punctuation only.",
  "fonts": {
    "serif":   "Newsreader",
    "sans":    "Sora",
    "mono":    "JetBrains Mono",
    "display": "Newsreader"
  },
  "palette": {
    "bg":           "#F8F1E7",
    "paper":        "#FFFFFF",
    "ink":          "#0F1620",
    "ink_soft":     "#4A5568",
    "accent":       "#B22234",
    "accent_alt":   "#DDD3C1",
    "rule":         "#DDD3C1",
    "code_bg":      "#F4ECDB",
    "code_text":    "#0F1620",
    "table_stripe": "#F8F1E7"
  },
  "mermaid": {
    "tagStyling": {
      "ingress":   { /* lifted from master.themes.atlas.mermaid.tagStyling.light.ingress */ },
      "core":      { /* lifted */ },
      "transform": { /* lifted */ },
      "bridge":    { /* lifted */ }
    },
    "fontFamily": "/* lifted from existing themes/atlas-light/mermaid-config.json themeVariables.fontFamily */",
    "lineColor":  "/* lifted from existing themes/atlas-light/mermaid-config.json themeVariables.lineColor */"
  }
}
```

`themes/default/spec.json` is identical in shape but without the `mode` field.

## Verification

After writing the 7 files:

1. `python3 skills/rebuild-themes/scripts/rebuild-themes.py` — dry run should now list all 7 built-ins as eligible (no more `[rebuild-themes] skip <name>: missing spec.json` warnings).
2. `python3 skills/rebuild-themes/scripts/rebuild-themes.py --apply` — refreshes `mermaid-config.json` + `preview.html` for all 7. Each gets a `.backup-<timestamp>/` directory with the prior content.
3. Spot-check at least one theme per family:
   - `themes/atlas-light/mermaid-config.json["themeVariables"]["attributeBackgroundColorOdd"]` should now equal `"#DDD3C1"` (atlas-light's accent_alt), not the previous `code_bg` value.
   - Open `themes/atlas-light/preview.html` in a browser to confirm the multi-diagram showcase renders (flowchart + ER + classDiagram).
4. `pytest tests/` — must remain 43/43 (this work touches only theme assets, not code).

## Commits

Two:

1. `feat(themes): add per-theme spec.json for built-in themes` — the 7 hand-written `spec.json` files.
2. `chore(themes): refresh built-in mermaid-config + preview from new specs` — the rebuild output (the regenerated `mermaid-config.json` + `preview.html` per theme, plus the timestamped `.backup-*/` directories created by the migrator).

## Open follow-ups (out of scope for this design)

- The `MD_PUBLISHER_BUILTIN_THEMES_ROOT` env-var override for `rebuild-themes.py` (quality-sentinel's prophylactic suggestion from Task 7's review). Not blocking; defer to a small future cleanup commit.
- Schema migration to support a 5th+ font role (e.g. distinct sans-eyebrow vs sans-structural) — only worth doing when an actual creative use-case demands it.
