---
name: publish
description: This skill should be used when the user asks to "publish doc.md to PDF", "render markdown as themed PDF", "build a themed PDF", "make a PDF with the atlas/phosphor/arcade theme", or invokes /md-publisher:publish. Renders any markdown document (with embedded mermaid) to a paged, searchable, themed PDF via WeasyPrint, with optional multi-theme rendering and post-build open.
---

# publish

Render a markdown document with embedded mermaid diagrams to a paged, searchable PDF using one of the bundled themes (or a user-installed custom theme). The pipeline is deterministic; the same input always produces the same output for a given (theme, mode).

## When to use

Trigger this skill when the user wants any of:
- "publish `doc.md`" / "build PDF from `doc.md`"
- "render `doc.md` with the atlas theme" / "with phosphor dark"
- "build all six themed variants of `doc.md`"
- "open the PDF after building"
- explicit invocation: `/md-publisher:publish <markdown-file>`

## Workflow

1. **Resolve the input.** The argument is a path to a markdown file. Resolve relative to the user's current working directory. Verify the file exists; surface a clear error if not.

2. **Run the build script.** Invoke `${CLAUDE_PLUGIN_ROOT}/skills/publish/scripts/publish.py` with the parsed flags. The script:
   - Checks first-run bootstrap; runs `${CLAUDE_PLUGIN_ROOT}/runtime/bootstrap.py` automatically if `~/.md-publisher/runtime/` is missing pieces (~2 min, downloads ~250 MB on first call only).
   - Resolves the theme via `lib.theme_loader.resolve_selection(name, mode)`. User themes at `~/.md-publisher/themes/<slug>/` win over built-ins at `${CLAUDE_PLUGIN_ROOT}/themes/<slug>/`.
   - Calls `lib.pipeline.build_pdf(...)` for each requested (theme, mode).
   - Writes outputs to `<source-dir>/.md-publisher/<YYYYMMDD-HHMMSS>/<stem>[-<theme>].pdf` by default, or to `--output` if explicit.
   - Optionally opens each rendered PDF (`start` on Windows, `open` on macOS, `xdg-open` on Linux).

3. **Report the result.** Print the rendered PDF paths and sizes. On `--all`, print one line per variant.

## Flags

The publish script accepts:

| Flag | Default | Meaning |
|---|---|---|
| `<input.md>` | required | Path to source markdown |
| `--theme <name>` | `default` | Theme name: `default`, `atlas`, `phosphor`, `arcade`, or any user-installed custom |
| `--mode <light\|dark>` | `light` | Mode (only meaningful for themes that have modes — `default` ignores it) |
| `--output <path>` | derived | Override the output PDF path. When set, `.md-publisher/<ts>/` convention is bypassed. |
| `--all` | off | Render every (theme × mode) combo for built-in themes (6 PDFs). Ignores `--theme`/`--mode`. |
| `--no-cover` | off | Suppress the cover page (some downstream uses prefer none) |
| `--open` | off | Open the produced PDF(s) with the OS default viewer after build |

## Mermaid behavior

If the input contains mermaid blocks WITHOUT `:::ingress / :::core / :::transform / :::bridge` annotations, the publish script proceeds with default per-node coloring (no warning unless `--strict` — which is not currently a flag). To get theme-aware per-node coloring, pre-process the document first via `/md-publisher:preprocess` — that skill rewrites the source with universal tags applied.

## Examples

```bash
# Basic build, default theme
${CLAUDE_PLUGIN_ROOT}/skills/publish/scripts/publish.py docs/intro.md

# Themed build
${CLAUDE_PLUGIN_ROOT}/skills/publish/scripts/publish.py docs/intro.md --theme atlas --mode dark

# All six built-in variants in one shot
${CLAUDE_PLUGIN_ROOT}/skills/publish/scripts/publish.py docs/intro.md --all

# Custom output path + open
${CLAUDE_PLUGIN_ROOT}/skills/publish/scripts/publish.py docs/intro.md --output /tmp/x.pdf --open
```

## Output reporting

After a successful build, report the produced files in a small table:

```
docs/.md-publisher/20260503-143015/intro-atlas-light.pdf  (192 KB)
docs/.md-publisher/20260503-143015/intro-atlas-dark.pdf   (193 KB)
```

For single-PDF builds, just one line. For `--all`, six lines.

## Failure modes

- **Bootstrap not yet run** — the script handles this automatically; surface the bootstrap output to the user (it takes ~2 min on first call).
- **GTK runtime missing on Windows** — clear error from the bootstrap probe with install hints. Plugin cannot proceed without GTK; document and point at install methods (Inkscape bundles it, or the dedicated GTK3 Runtime installer).
- **Unknown theme** — `theme_loader.resolve_selection` raises with the searched paths. Tell the user the theme isn't installed and suggest `/md-publisher:theme-gallery` to see what is available.
- **Mermaid render failure** — mmdc errors get propagated; usually a syntax problem in the diagram source. Quote the failing block back to the user.

## Reference files

For implementation details (script internals, error envelope, exact path resolution rules), see:
- `scripts/publish.py` — the build entry script
- `${CLAUDE_PLUGIN_ROOT}/lib/pipeline.py` — the WeasyPrint pipeline
- `${CLAUDE_PLUGIN_ROOT}/lib/theme_loader.py` — theme resolution
- `${CLAUDE_PLUGIN_ROOT}/lib/output_paths.py` — output path conventions
