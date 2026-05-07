# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Reference docs (read first)

- **`.github/copilot-instructions.md`** — already developer-facing; covers test commands, two-pipeline architecture, theme resolution, theme-file roles, cover-page HTML structure, mermaid universal tags, runtime bootstrap, and key conventions. Treat it as the canonical short brief.
- **`README.md`** — user-facing reference for skill invocations, output conventions, install paths, and troubleshooting.

The notes below are deltas/emphasis on top of those — not a replacement.

## Tests

```bash
python -m pytest tests/ -q                        # full suite
python -m pytest tests/test_theme_loader.py -q    # one file
python -m pytest tests/test_theme_loader.py -k signal -q   # one test
```

No build, no linter. `tests/conftest.py` puts the plugin root on `sys.path` and sets `CLAUDE_PLUGIN_ROOT`, so `lib.*` imports work without install. Integration tests look for `~/repos/md-publisher-bakeoff/corpus/transformers-explainer-themed.md`; override with `MD_PUBLISHER_TEST_CORPUS=<path>` or let those tests skip.

## Architecture (the parts that span multiple files)

This is a **plugin** — distributed via `.claude-plugin/plugin.json`, invoked through skills, never `pip install`ed. Skill scripts use `lib/` as a sibling, not a package.

**Two pipelines, one theme model.** `ThemeSelection` (palette + fonts + mermaid config, produced by `lib/theme_loader.py::resolve_selection`) feeds both:

- `lib/pipeline.py::build_pdf()` — WeasyPrint, consumes `style.css` + `cover.css` directly.
- `lib/docx_pipeline.py::build_docx()` — python-docx, reads `spec.json` and applies palette/fonts programmatically via `lib/docx_styles.py`. DOCX-only modules follow the `lib/docx_*.py` naming pattern.

**Theme resolution order:** `~/.md-publisher/themes/<slug>/` (user) → `themes/<slug>/` (built-in). Slug is `{name}-{mode}` except `default`. Each theme dir needs `style.css`, `mermaid-config.json`, `spec.json`; PDF themes additionally need `cover.css`.

**Runtime bootstrap.** Heavy deps (WeasyPrint stack, mermaid-cli, Puppeteer Chromium) live at `~/.md-publisher/runtime/`, installed on first skill run by `runtime/bootstrap.py`. `lib/runtime.py` locates them. `CLAUDE_PLUGIN_ROOT` env var resolves the plugin install dir (with a walk-up fallback from `lib/`). On macOS, `lib/gtk_loader.py::_ensure_macos_dyld_path` injects Homebrew's lib dir into `DYLD_LIBRARY_PATH` so WeasyPrint can find Pango/Cairo.

**Mermaid universal tags.** `:::ingress | :::core | :::transform | :::bridge` get expanded into `classDef` lines by `lib/mermaid_processor.py` using each theme's `spec.json::mermaid.tagStyling`. The `preprocess` skill applies these tags via LLM and also auto-promotes misplaced classDiagram tags (priority `ingress > core > transform > bridge`).

## Conventions worth knowing

- **Version is duplicated in three files** that must stay in sync on a release: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `marketplace-entry.json`.
- **Adding a built-in theme:** create `themes/<slug>/` with the four files above, add the `(name, mode)` pair to `tests/conftest.py::all_themes`, and update `lib/font_install.py` if it introduces new Google Fonts. The scaffold templates (`STYLE_CSS_TEMPLATE`, `COVER_CSS_TEMPLATE`, `PREVIEW_HTML_TEMPLATE`) live in `skills/theme-advisor/scripts/scaffold-theme.py` and are also imported by `skills/rebuild-themes/scripts/rebuild-themes.py`.
- **Per-theme `spec.json` is canonical;** `themes/theme-spec.json` is the legacy aggregate, kept only as a fallback for older families.
- **No PyYAML.** Front-matter is parsed with a regex parser inside `lib/pipeline.py`. Do not introduce PyYAML.
- **stdlib-only in `lib/` and `skills/*/scripts/`** unless the dep is in the runtime venv (WeasyPrint, python-docx, Pygments, markdown, cairosvg). Scripts that may run from system Python before bootstrap must be stdlib-only.
- **Files >500 lines are a smell** — prefer splitting (the `docx_*.py` decomposition is the model).
