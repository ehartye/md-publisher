---
name: publish
description: This skill should be used when the user asks to "publish doc.md to PDF", "publish doc.md to DOCX", "render markdown as themed PDF", "render markdown as DOCX", "convert markdown to PDF or Word", "build a themed PDF", "make a PDF with the atlas/phosphor/arcade theme", "render every theme", "build PDF and DOCX of doc.md", "build and open the PDF", or invokes /md-publisher:publish. Renders any markdown document (with embedded mermaid) to a paged, searchable, themed PDF via WeasyPrint and/or a Microsoft Word DOCX via python-docx, with optional multi-theme rendering and post-build open.
---

# publish

Render a markdown document with embedded mermaid diagrams to a paged, searchable PDF using one of the bundled themes (or a user-installed custom theme). The pipeline is deterministic; the same input always produces the same output for a given (theme, mode).

## When to use

Trigger this skill when the user wants any of:
- "publish `doc.md`" / "build PDF from `doc.md`"
- "publish `doc.md` to DOCX" / "build a Word doc from `doc.md`"
- "build PDF and DOCX of `doc.md`" (use `--format both`)
- "render `doc.md` with the atlas theme" / "with phosphor dark"
- "build all six themed variants of `doc.md`"
- "open the PDF after building"
- explicit invocation: `/md-publisher:publish <markdown-file> [--format pdf|docx|both]`

**DOCX consumer note:** the DOCX path needs the theme's fonts installed locally (Word substitutes when fonts are missing). On first DOCX build, run `/md-publisher:install-fonts` once to install the bundled themes' Google Fonts per-user.

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
| `--mode <light\|dark>` | `light` | Mode for theme variants. **Silently ignored when `--theme default`** (default has no mode variants). |
| `--output <path>` | derived | Override the output path. Extension must match `--format` (`.pdf` for `--format pdf`, `.docx` for `--format docx`). Ignored when `--all` is set or `--format both`. |
| `--all` | off | Render every (theme × mode) combo for built-in themes (6 outputs per format; 12 with `--format both`). Ignores `--theme`, `--mode`, and `--output`. |
| `--no-cover` | off | Suppress the cover page. Useful when concatenating with other docs as an appendix, or embedding into a larger document. |
| `--open` | off | Open the produced file(s) with the OS default viewer after build. |
| `--format pdf\|docx\|both` | `pdf` | Output format. `pdf` (default) preserves existing behavior. `docx` builds a Microsoft Word document via python-docx. `both` produces sibling PDF + DOCX in the same `.md-publisher/<ts>/` dir. The `default` theme is PDF-only (no per-mode palette/fonts data); use atlas/phosphor/arcade or a custom theme for DOCX. |

## Mermaid behavior

If the input contains mermaid blocks WITHOUT `:::ingress / :::core / :::transform / :::bridge` annotations, the publish script proceeds with default per-node coloring (no warning is emitted). To get theme-aware per-node coloring, pre-process the document first via `/md-publisher:preprocess` — that skill rewrites the source with universal tags applied.

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
- **Native deps missing (WeasyPrint Pango/Cairo)** — first triage: tell user to run `python <plugin-root>/runtime/bootstrap.py --doctor`. The doctor mode prints a platform-specific fix:
  - **Windows**: install Inkscape (https://inkscape.org), GIMP, or the [GTK3 Runtime](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer); plugin auto-detects.
  - **macOS**: `brew install pango cairo gdk-pixbuf libffi`. Plugin auto-prepends `<brew --prefix>/lib` to `DYLD_LIBRARY_PATH` so the dynamic loader finds the libs. SIP-protected Python (e.g. `/usr/bin/python3`) ignores `DYLD_LIBRARY_PATH` — recommend brew/pyenv Python in that case.
  - **Linux**: `apt install libpango-1.0-0 libcairo2 libgdk-pixbuf2.0-0 libffi-dev` (Debian/Ubuntu) or distro equivalent.
- **Unknown theme** — `theme_loader.resolve_selection` raises with the searched paths. Tell the user the theme isn't installed and suggest `/md-publisher:theme-gallery` to see what is available.
- **Missing fonts (DOCX path)** — publish-docx prints a non-blocking warning when the resolved theme's fonts aren't installed locally. Suggest `/md-publisher:install-fonts --theme <name> --mode <mode>` to fix; the build proceeds with Word's font substitutes in the meantime.
- **Mermaid render failure** — mmdc errors get propagated; usually a syntax problem in the diagram source. Quote the failing block back to the user.

## Reference files

For implementation details (script internals, error envelope, exact path resolution rules), see:
- `scripts/publish.py` — the build entry script
- `${CLAUDE_PLUGIN_ROOT}/lib/pipeline.py` — the WeasyPrint pipeline
- `${CLAUDE_PLUGIN_ROOT}/lib/theme_loader.py` — theme resolution
- `${CLAUDE_PLUGIN_ROOT}/lib/output_paths.py` — output path conventions
