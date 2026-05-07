# md-publisher

A plugin for Claude Code and GitHub Copilot CLI that turns markdown documents — with embedded mermaid diagrams — into themed, searchable, paged PDFs *and* Microsoft Word DOCX. Built on WeasyPrint and python-docx after two structured toolchain bake-offs (see `~/repos/md-publisher-bakeoff/` for both rounds).

## What you get

Five skills, six bundled themes, and Python pipelines that produce print-ready PDFs and editable DOCX with searchable mermaid text.

| Skill | What it does | Invoke as |
|---|---|---|
| **`publish`** | Turn a markdown file into a themed PDF or DOCX (or both). | `/md-publisher:publish doc.md [--theme atlas --mode light] [--format pdf\|docx\|both] [--all] [--open] [--no-cover]` |
| **`preprocess`** | LLM-tag mermaid diagrams (`:::ingress / :::core / :::transform / :::bridge`) so themes color nodes by role. Rewrites the source in place; original gets backed up. | `/md-publisher:preprocess doc.md [--add-frontmatter]` |
| **`theme-advisor`** | Interactive Q&A flow that produces a custom theme module under `~/.md-publisher/themes/<name>/`. | `/md-publisher:theme-advisor` |
| **`theme-gallery`** | List, preview, and pick from built-in + user-installed themes in one combined view. | `/md-publisher:theme-gallery` |
| **`install-fonts`** | Per-user install of the Google Fonts the bundled themes use (Newsreader, Sora, JetBrains Mono, IBM Plex Mono, Audiowide, Bungee, Recursive). Cross-platform; no admin required. Run once after first DOCX build to fix font substitution. | `/md-publisher:install-fonts [--theme atlas --mode dark] [--dry-run]` |

## DOCX output

`--format docx` (or `--format both`) produces a Microsoft Word `.docx` alongside or instead of the PDF. Same theme system, mostly-same fidelity. The DOCX path uses programmatic OOXML via python-docx for full control over page color, fonts, code-block syntax highlighting, and per-theme cover treatments.

A few documented concessions vs the PDF version (see `~/repos/md-publisher-bakeoff/docx/DECISION.md` for context):
- Mermaid is embedded as PNG (themed, but not searchable like the PDF SVG version).
- Arcade's mono uses JetBrains Mono (Word can't reliably address Recursive's mono variation axis).
- DOCX requires the theme fonts to be installed locally — Word substitutes when fonts are missing. Run `/md-publisher:install-fonts` once after first install.

All themes — including `default` — support both PDF and DOCX output.

## Themes

Six bundled themes (each in light + dark): **atlas** (corporate, customer-facing), **phosphor** (developer, terminal aesthetic), **arcade** (gamer, manual aesthetic), and **default** (clean editorial). Custom themes go to `~/.md-publisher/themes/<slug>/`. The gallery resolver checks user themes first, falls back to built-ins.

## Output convention

`<source-md-dir>/.md-publisher/<YYYYMMDD-HHMMSS>/<slug>.<pdf|docx>` by default. Override with `--output <path>` (extension must match `--format`). With `--format both`, the PDF and DOCX share the same timestamp dir and slug. The same `.md-publisher/<timestamp>/` directory holds the backup of the original markdown when `preprocess` runs.

## First-run setup

The first time any skill runs, the plugin bootstraps a Python venv and Node toolchain at `~/.md-publisher/runtime/`. This takes ~2 minutes (downloads ~250 MB total: WeasyPrint, python-docx, Pygments, cairosvg, mermaid-cli, and Puppeteer's Chromium). Subsequent runs reuse the cache.

If anything goes wrong with native dep loading (especially WeasyPrint/Cairo on macOS Homebrew installs), run `python <plugin-root>/runtime/bootstrap.py --doctor` for a platform-specific diagnostic with actionable install/fix instructions.

**System dependency** that the bootstrap CANNOT install for you: a GTK 3 runtime (Pango / Cairo / GDK-Pixbuf), needed by WeasyPrint for native typography. On Windows the easiest source is Inkscape or GIMP (their bundled GTK works); on Linux `apt install libpango-1.0-0 libcairo2 libgdk-pixbuf2.0-0`; on macOS `brew install pango cairo gdk-pixbuf libffi`. The bootstrap probes for it and prints a clear error with install instructions if missing.

## Layout

```
md-publisher/
├── .claude-plugin/plugin.json       plugin manifest (used by both Claude Code and Copilot CLI)
├── .github/
│   ├── plugin/marketplace.json      Copilot CLI marketplace entry
│   └── copilot-instructions.md      plugin context for Copilot CLI
├── README.md
├── skills/
│   ├── publish/        SKILL.md + scripts/  (--format pdf|docx|both)
│   ├── preprocess/     SKILL.md + scripts/
│   ├── theme-advisor/  SKILL.md + scripts/
│   ├── theme-gallery/  SKILL.md + scripts/
│   └── install-fonts/  SKILL.md + scripts/  (cross-platform Google Fonts installer)
├── lib/                shared Python pipelines
│   ├── pipeline.py         WeasyPrint PDF pipeline
│   ├── docx_pipeline.py    python-docx DOCX pipeline (orchestration entry)
│   ├── docx_*.py           DOCX components (styles, renderer, cover, syntax, toc)
│   ├── theme_loader.py     theme resolution + Palette/Fonts dataclasses
│   ├── font_install.py     cross-platform per-user font install
│   └── ...
├── themes/             six built-in themes + theme-spec.json + pygments{,-dark}.css
└── runtime/            bootstrap (with --doctor mode) + dep manifests
```

User-data lives at `~/.md-publisher/`:

```
~/.md-publisher/
├── runtime/.venv/      Python deps installed once at bootstrap
├── runtime/node_modules/    mermaid-cli + Chromium
└── themes/<slug>/      user-created themes (theme-advisor output)
```

## Installation

### Claude Code

```bash
claude plugin install ehartye/md-publisher
```

### GitHub Copilot CLI

```bash
copilot plugin install ehartye/md-publisher
```

Both paths use the same skills and runtime — no difference in capability.

## Verifying the install

After installing the plugin, run the bootstrap probe in your terminal:

```bash
python <plugin-root>/runtime/bootstrap.py --status
```

A healthy install prints all three checks as `OK`:

```
md-publisher bootstrap status
  user runtime:    /home/.../.md-publisher/runtime
  venv:            OK  (.../.venv/Scripts/python.exe)
  mmdc:            OK  (.../node_modules/@mermaid-js/mermaid-cli/src/cli.js)
  gtk:             OK
                   GTK runtime found at: ...
```

If any line says `MISSING`, see Troubleshooting below.

To smoke-test the whole pipeline end-to-end without involving Claude Code, render any markdown file directly:

```bash
python <plugin-root>/skills/publish/scripts/publish.py <some-doc.md>
```

A successful run writes `<some-doc-dir>/.md-publisher/<timestamp>/<slug>.pdf` and prints the path. If you don't have a test markdown handy, the bake-off corpus at `~/repos/md-publisher-bakeoff/corpus/transformers-explainer.md` (if present from the project's research history) is a known-good 6-page input with mermaid diagrams.

## Troubleshooting

For native-dep loading problems (Pango / Cairo / GDK-Pixbuf, mmdc, font dir), run:

```
python <plugin-root>/runtime/bootstrap.py --doctor
```

`--doctor` actually exercises each native dep (full PDF write, cairosvg rasterize, mmdc --version, font-dir write probe) instead of just checking that the file exists, then prints platform-specific recovery instructions. Common cases:

### macOS — WeasyPrint can't find Pango/Cairo despite `brew install pango cairo`

The dynamic loader doesn't search Homebrew's prefix by default on Apple Silicon. The plugin auto-detects `brew --prefix` and prepends `<prefix>/lib` to `DYLD_LIBRARY_PATH` at runtime (see `lib/gtk_loader.py::_ensure_macos_dyld_path`). If you still see `OSError: cannot load library 'libpango-1.0-0.dylib'`, run `--doctor` to confirm brew-prefix detection worked, and add to your shell rc as a permanent fix:

```bash
# Apple Silicon (M-series)
export DYLD_LIBRARY_PATH="/opt/homebrew/lib:$DYLD_LIBRARY_PATH"
# Intel macOS
export DYLD_LIBRARY_PATH="/usr/local/lib:$DYLD_LIBRARY_PATH"
```

### Linux — Pango/Cairo missing entirely

```bash
sudo apt install libpango-1.0-0 libcairo2 libgdk-pixbuf2.0-0 libffi-dev   # Debian/Ubuntu
sudo dnf install pango cairo gdk-pixbuf2 libffi                            # Fedora/RHEL
sudo pacman -S pango cairo gdk-pixbuf2 libffi                              # Arch
```

### Windows — GTK 3 runtime not found

Install Inkscape (https://inkscape.org) or GIMP — they bundle a complete GTK3 stack the plugin auto-detects. Or install [GTK3 Runtime](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer) directly. If GTK lives somewhere unusual, set `WEASYPRINT_DLL_DIR=<gtk-bin-dir>`.

### Other symptoms

| Symptom | Cause | Fix |
|---|---|---|
| `--status` reports `gtk: MISSING` (Windows) | Pango / Cairo / GDK-Pixbuf DLLs not on the search path | Install Inkscape (https://inkscape.org) or GIMP — they bundle a complete GTK3 stack that the plugin auto-detects. Alternatively install [GTK3 Runtime](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer). If GTK lives somewhere unusual, set `WEASYPRINT_DLL_DIR=<gtk-bin-dir>`. |
| `--status` reports `gtk: MISSING` (Linux) | System packages missing | `sudo apt install libpango-1.0-0 libcairo2 libgdk-pixbuf2.0-0` (Debian/Ubuntu); equivalent for other distros |
| `--status` reports `gtk: MISSING` (macOS) | Homebrew packages missing | `brew install pango cairo gdk-pixbuf libffi` |
| `[bootstrap] ERROR: npm not on PATH` | Node.js not installed | Install Node 18+ from https://nodejs.org |
| `mmdc failed for ...` with `Failed to launch the browser process` (Linux/CI/WSL) | Chromium's user-namespace sandbox can't initialize in the current environment | Set `MD_PUBLISHER_DISABLE_SANDBOX=1` and re-run. **Do NOT set this on a multi-user system** — it disables a primary defense layer. |
| `pdftotext` extraction returns no mermaid labels | Diagrams rasterized somewhere in the pipeline | This shouldn't happen with the bundled toolchain; if it does, check that mermaid-config.json has `htmlLabels: false`. File a bug with the input that triggered it. |
| `WeasyPrint` import error referencing `cffi` | venv corrupted from an interrupted install | `python <plugin-root>/runtime/bootstrap.py --force` rebuilds both the venv and node_modules |
| Themed build renders but looks wrong (wrong font / wrong color) | Theme CSS or mermaid-config typo, or the theme's font isn't installed locally | Open `~/.md-publisher/themes/<slug>/preview.html` in a browser to see how the theme is supposed to look in isolation. If the preview looks right but the PDF doesn't, file a bug with the input. |
| Cover page missing | Cover skipped via `--no-cover`, or no `cover.css` in the user theme | User themes scaffolded by theme-advisor get a default `cover.css`; user themes hand-built without one render with the universal base layout only (no per-theme decoration). Add a `cover.css` to enable theme-specific cover styling. |

## Contributing

### Adding a new built-in theme

1. Add a `themes/<name-mode>/` directory with `style.css` (full WeasyPrint stylesheet) and `mermaid-config.json` (mmdc theme config). Optionally include `cover.css` for theme-specific cover-page treatment.
2. Add a per-theme block to `themes/theme-spec.json` under `themes.<name>` with the palette + fonts + per-tag mermaid styling. Mirror the structure of an existing theme block.
3. Test: `python skills/publish/scripts/publish.py <some-doc.md> --theme <name> --mode <mode>`.

If your theme is mode-aware (light + dark), create both directories and both spec entries — the resolver computes the slug as `<name>-<mode>`.

### Adding a new skill

1. `mkdir -p skills/<skill-name>/scripts`
2. Write `skills/<skill-name>/SKILL.md` with third-person frontmatter description, specific trigger phrases, and an imperative-form body. Reference any helper scripts under `${CLAUDE_PLUGIN_ROOT}/skills/<skill-name>/scripts/...`.
3. Helper scripts in `scripts/` should accept JSON on stdin or argparse args — never interpolate user-supplied values into source strings.
4. Skill auto-discovery is automatic; restart Claude Code to pick up the new skill.

### Filing bugs and feature requests

Bugs: include the markdown that triggered the issue, the full command line, the exit code, and any output from `python <plugin-root>/runtime/bootstrap.py --status`.

Theme-quality bugs: also include screenshots of the resulting PDF and the theme's `preview.html` so it's clear whether the issue is in the theme definition or the renderer.

### Code style

- Python: standard library only in lib/ and skills/scripts/ unless the venv has the dep (only WeasyPrint, Pygments, markdown allowed). No third-party deps in scripts that run from the system Python.
- Skills: follow the patterns of the existing four; keep SKILL.md under ~2000 words and push detail into scripts/ or references/.
- Files over 500 lines are a smell; refactor.

## Project history

This plugin is the productized output of a four-toolchain bake-off (Chromium + Playwright, WeasyPrint, Typst, Pandoc + XeLaTeX) that produced 24 themed PDFs from the same corpus. WeasyPrint won on the right combination of theming flexibility, install footprint, and aesthetic-fidelity ceiling for the audience-targeted themes. The full comparison + the four implementations live at `~/repos/md-publisher-bakeoff/` if you need to verify or reproduce the decision.

## License

MIT — see [LICENSE](LICENSE).
